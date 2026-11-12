"""Real subprocess regression checks for complete stdin/stdout delivery.

The children are local synthetic executables. These tests never call a model,
read real provider credentials, or register production worker processes.
"""
import hashlib
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import adapters
import codebuddy_adapter


class AdapterInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='agent-adapter-input-test-')
        self.root = Path(self.temporary.name)
        self.marker = 'TAIL_PLAN_SCHEMA_DIRECTIVE_7F419C'
        self.prompt = ('合成测试上下文。仅测试输入完整性，不调用模型。\n' * 18000) + self.marker
        self.prompt_bytes = self.prompt.encode('utf-8')
        self.schema = {
            'type': 'object', 'properties': {'schema_sentinel_7f419c': {'type': 'boolean'}},
            'required': ['schema_sentinel_7f419c'], 'additionalProperties': False,
        }
        self.assertGreater(len(self.prompt_bytes), 1024 * 1024)

    def tearDown(self):
        self.temporary.cleanup()

    def child(self, name, provider):
        executable = self.root / name
        code = '''import hashlib,json,sys,time
# Deliberately outwait several adapter polling intervals.
time.sleep(.8)
data = sys.stdin.buffer.read()
text = data.decode('utf-8')
provider = PROVIDER
schema_present = False
if provider == 'workbuddy':
    index = sys.argv.index('--json-schema')
    schema_present = json.loads(sys.argv[index + 1]) == EXPECTED_SCHEMA
else:
    schema_present = 'schema_sentinel_7f419c' in text[PROMPT_CHARS:]
result = {
    'received_bytes': len(data),
    'prompt_sha256': hashlib.sha256(data[:PROMPT_BYTES]).hexdigest(),
    'tail_present': MARKER in text,
    'provider_suffix_present': len(text) > PROMPT_CHARS,
    'schema_present': schema_present,
}
if provider == 'workbuddy':
    print(json.dumps({'type':'result','is_error':False,'structured_output':result}))
else:
    print(json.dumps(result))
'''
        code = code.replace('PROVIDER', repr(provider)).replace('EXPECTED_SCHEMA', repr(self.schema))
        code = code.replace('PROMPT_BYTES', str(len(self.prompt_bytes))).replace('PROMPT_CHARS', str(len(self.prompt)))
        code = code.replace('MARKER', repr(self.marker))
        executable.write_text('#!' + sys.executable + '\n' + code, encoding='utf-8')
        executable.chmod(0o700)
        return executable

    def assert_complete(self, result):
        self.assertGreater(result['received_bytes'], len(self.prompt_bytes))
        self.assertEqual(result['prompt_sha256'], hashlib.sha256(self.prompt_bytes).hexdigest())
        self.assertTrue(result['tail_present'])
        self.assertTrue(result['provider_suffix_present'])
        self.assertTrue(result['schema_present'])

    def test_workbuddy_delayed_child_receives_full_large_prompt_and_schema(self):
        cli = self.child('fake-codebuddy', 'workbuddy')
        with patch.object(codebuddy_adapter, 'CLI', cli), \
             patch.object(codebuddy_adapter, 'environment', return_value={'PATH': '/usr/bin:/bin'}), \
             patch('server.register_child', return_value=None) as register, \
             patch('server.clear_child'):
            result = codebuddy_adapter.run(self.prompt, self.schema, self.root / 'workbuddy-node', threading.Event(), 5)
        self.assert_complete(result)
        register.assert_called_once()

    def test_harness_delayed_child_receives_full_large_prompt_and_schema(self):
        node = self.child('fake-node', 'harness')
        dsh = self.root / 'fake-dsh.js'
        dsh.write_text('// Synthetic placeholder; fake-node implements the test child.\n')
        with patch.object(adapters, 'NODE', node), \
             patch.object(adapters, 'DSH', dsh), \
             patch.object(adapters, '_harness_credentials', return_value={'DEEPSEEK_API_KEY': 'synthetic-test-only'}), \
             patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}, clear=True), \
             patch('server.register_child', return_value=None) as register, \
             patch('server.clear_child'):
            result = adapters.run_api('deepseek_harness', self.prompt, self.schema, self.root / 'harness-node', threading.Event(), 5)
        self.assert_complete(result)
        register.assert_called_once()


class AdapterOutputTests(unittest.TestCase):
    """Real Node stdout writes followed immediately by process.exit(0)."""
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='agent-adapter-output-test-')
        self.root = Path(self.temporary.name)
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is optional; install Node to run the large-output transport checks.')
        self.node = Path(node)
        self.marker = '末尾完整_UTF8_TAIL_7F419C'
        self.payload = ('中文输出完整性核对。' * 50000) + self.marker
        self.schema = {'type': 'object', 'properties': {'body': {'type': 'string'}}, 'required': ['body'], 'additionalProperties': False}
        self.assertGreater(len(self.payload.encode('utf-8')), 128 * 1024)

    def tearDown(self):
        self.temporary.cleanup()

    def child(self, name, provider):
        script = self.root / name
        source = """const fs = require('node:fs');
fs.readFileSync(0, 'utf8');
const body = '中文输出完整性核对。'.repeat(50000) + '末尾完整_UTF8_TAIL_7F419C';
const result = {body};
const output = PROVIDER === 'workbuddy'
  ? {type: 'result', is_error: false, structured_output: result}
  : result;
// A pipe can lose buffered output here; regular file descriptors must preserve it.
process.stdout.write(JSON.stringify(output));
process.stderr.write('合成诊断输出。'.repeat(40000) + '诊断末尾');
process.exit(0);
""".replace('PROVIDER', json.dumps(provider))
        script.write_text('#!' + str(self.node) + '\n' + source, encoding='utf-8')
        script.chmod(0o700)
        return script

    def assert_complete(self, result, workspace):
        self.assertEqual(result['body'], self.payload)
        self.assertEqual(hashlib.sha256(result['body'].encode('utf-8')).hexdigest(), hashlib.sha256(self.payload.encode('utf-8')).hexdigest())
        self.assertTrue(result['body'].endswith(self.marker))
        transport = json.loads((workspace / '_transport.json').read_text())
        self.assertEqual(transport['exit_code'], 0)
        self.assertEqual(transport['transport'], 'file-descriptors')
        self.assertGreater(transport['stdout_bytes'], 128 * 1024)
        self.assertEqual(transport['stderr_bytes'], len(('合成诊断输出。' * 40000 + '诊断末尾').encode('utf-8')))

    def test_workbuddy_node_large_utf8_output_survives_immediate_exit(self):
        cli = self.child('fake-codebuddy-node', 'workbuddy')
        workspace = self.root / 'workbuddy-output'
        with patch.object(codebuddy_adapter, 'CLI', cli), \
             patch.object(codebuddy_adapter, 'environment', return_value={'PATH': '/usr/bin:/bin'}), \
             patch('server.register_child', return_value=None), \
             patch('server.clear_child'):
            result = codebuddy_adapter.run('Only test the local output transport.', self.schema, workspace, threading.Event(), 5)
        self.assert_complete(result, workspace)

    def test_harness_node_large_utf8_output_survives_immediate_exit(self):
        script = self.child('fake-dsh.js', 'harness')
        workspace = self.root / 'harness-output'
        with patch.object(adapters, 'NODE', self.node), \
             patch.object(adapters, 'DSH', script), \
             patch.object(adapters, '_harness_credentials', return_value={'DEEPSEEK_API_KEY': 'synthetic-test-only'}), \
             patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}, clear=True), \
             patch('server.register_child', return_value=None), \
             patch('server.clear_child'):
            result = adapters.run_api('deepseek_harness', 'Only test the local output transport.', self.schema, workspace, threading.Event(), 5)
        self.assert_complete(result, workspace)


if __name__ == '__main__':
    unittest.main(verbosity=2)
