"""Portable install configuration must not discover a previous private setup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent


class ConfigurationTests(unittest.TestCase):
    def probe(self, env):
        clean = {k: v for k, v in os.environ.items() if not k.startswith('ORBIT_')}
        clean.update(env)
        script = "import config,json; print(json.dumps({k:str(getattr(config,k)) for k in ['DATA_DIR','VAULT','PROJECT_INDEX','CODEX','PORT','INSTANCE_ID','DEEPSEEK_ENV']}))"
        return subprocess.run([sys.executable, '-c', script], cwd=ROOT, env=clean,
                              capture_output=True, text=True, timeout=5)

    def test_config_paths_resolve_relative_to_the_file_and_environment_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = base / 'orbit.json'
            config.write_text(json.dumps({'data_dir': 'state', 'vault': 'notes', 'project_index': 'projects.json', 'port': 8891}))
            result = self.probe({'ORBIT_CONFIG': str(config), 'ORBIT_PORT': '8892', 'ORBIT_CODEX_BIN': 'bin/custom-codex'})
            self.assertEqual(result.returncode, 0, result.stderr)
            values = json.loads(result.stdout)
            self.assertEqual(values['DATA_DIR'], str(base / 'state'))
            self.assertEqual(values['VAULT'], str(base / 'notes'))
            self.assertEqual(values['PROJECT_INDEX'], str(base / 'projects.json'))
            self.assertEqual(values['DEEPSEEK_ENV'], str(base / '.env'))
            self.assertEqual(values['CODEX'], str(base / 'bin/custom-codex'))
            self.assertEqual(values['PORT'], '8892')

    def test_clean_config_uses_only_examples_and_local_state(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            config = base / 'orbit.json'
            config.write_text('{}')
            result = self.probe({'ORBIT_CONFIG': str(config), 'PATH': '/usr/bin:/bin'})
            self.assertEqual(result.returncode, 0, result.stderr)
            values = json.loads(result.stdout)
            self.assertEqual(values['VAULT'], str(base / 'examples/vault'))
            self.assertEqual(values['DATA_DIR'], str(base / 'data'))
            self.assertEqual(values['PROJECT_INDEX'], str(base / 'examples/projects.json'))
            self.assertEqual(len(values['INSTANCE_ID']), 24)

    def test_invalid_config_or_port_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'orbit.json'
            for contents in ('[]', '{invalid json', '{"port":0}', '{"port":3350}', '{"port":true}'):
                config.write_text(contents)
                self.assertNotEqual(self.probe({'ORBIT_CONFIG': str(config)}).returncode, 0)
            self.assertNotEqual(self.probe({'ORBIT_CONFIG': str(config.with_name('missing.json'))}).returncode, 0)


if __name__ == '__main__':
    unittest.main()
