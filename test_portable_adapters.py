"""Portable installation and instance isolation checks; never contact a model."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import adapters
import autoloop_bridge as bridge
import autoloop_service as service
import codebuddy_adapter
import launcher


class PortableAdapterTests(unittest.TestCase):
    def test_deepseek_uses_only_configured_env_file_or_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'local.env'
            source.write_text('DEEPSEEK_API_KEY=test-file\nUNRELATED=ignored\n')
            with patch.object(adapters, 'DEEPSEEK_ENV', source), patch.dict(adapters.os.environ, {}, clear=True):
                self.assertEqual(adapters._harness_credentials()['DEEPSEEK_API_KEY'], 'test-file')
                self.assertNotIn('UNRELATED', adapters._harness_credentials())
            with patch.object(adapters, 'DEEPSEEK_ENV', source), patch.dict(adapters.os.environ, {'DEEPSEEK_API_KEY': 'test-env'}, clear=True):
                self.assertEqual(adapters._harness_credentials()['DEEPSEEK_API_KEY'], 'test-env')

    def test_manual_providers_work_without_desktop_apps(self):
        with patch.object(adapters, '_app_metadata', return_value={'installed': False}), \
                patch.object(adapters, '_harness_credentials', return_value={}):
            providers = {p['id']: p for p in adapters.detect_providers()}
        self.assertTrue(providers['grok']['available'])
        self.assertTrue(providers['doubao']['available'])
        self.assertEqual(providers['grok']['handoff_url'], 'https://grok.com/')
        self.assertFalse(providers['doubao']['authenticated'])

    def test_launcher_rejects_another_orbit_instance(self):
        wrong = {'local_only': True, 'instance_id': 'another-instance', 'codex_available': True}
        with patch.object(launcher.OPENER, 'open', return_value=io.BytesIO(json.dumps(wrong).encode())):
            self.assertIsNone(launcher.health())
        wrong['instance_id'] = launcher.INSTANCE_ID
        with patch.object(launcher.OPENER, 'open', return_value=io.BytesIO(json.dumps(wrong).encode())):
            self.assertEqual(launcher.health(), wrong)

    def test_launcher_refuses_reused_pid(self):
        with tempfile.TemporaryDirectory() as folder:
            record = Path(folder) / 'pid.json'
            record.write_text(json.dumps({'instance_id': launcher.INSTANCE_ID, 'pid': 12345,
                                          'started': 'old', 'command': str(launcher.ROOT / 'server.py')}))
            with patch.object(launcher, 'process_record', return_value={'started': 'new', 'command': str(launcher.ROOT / 'server.py')}), \
                    patch.object(launcher.os, 'kill') as kill:
                with self.assertRaisesRegex(RuntimeError, '身份已变化'):
                    launcher.stop_owned(record)
                kill.assert_not_called()

    def test_bridge_rejects_wrong_instance_before_submitting_private_goal(self):
        client = bridge.PlatformClient('http://127.0.0.1:8876')
        with patch.object(client.opener, 'open', return_value=io.BytesIO(b'{"instance_id":"other"}')) as request:
            with self.assertRaisesRegex(bridge.BridgeError, '不属于此 Orbit'):
                client('POST', '/api/runs', {'brief': 'synthetic-private-goal'})
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[0], 'http://127.0.0.1:8876/api/health')

    def test_event_projects_resolve_against_index_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'project').mkdir()
            index = root / 'projects.json'
            index.write_text(json.dumps({'projects': [{'key': 'demo', 'name': 'Demo', 'root': 'project'}]}))
            with patch.object(service, 'INDEX', index):
                self.assertEqual(service.projects()[0]['root'], str((root / 'project').resolve()))

    def test_no_optional_runtime_does_not_disable_core_imports(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / 'missing'
            with patch.object(service, 'ENTRY', missing), patch.object(service, 'DATA', missing), \
                    patch.object(service, 'request', side_effect=RuntimeError('not configured')):
                state = service.state()
            self.assertFalse(state['installed'])
            self.assertFalse(state['running'])
            self.assertIn('核心功能可独立使用', state['detail'])


if __name__ == '__main__':
    unittest.main()
