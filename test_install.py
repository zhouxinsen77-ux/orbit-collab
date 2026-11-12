"""Installation checks use temporary folders and mocked package/process calls."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import install


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='orbit-installer-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patch = patch.object(install, 'ROOT', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        (self.root / 'orbit.example.json').write_text('{"vault":"examples/vault"}\n')
        self.output = io.StringIO()
        self.capture = contextlib.redirect_stdout(self.output)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def test_init_preserves_existing_configuration_bytes(self):
        target = self.root / 'orbit.json'
        original = b'{"port":19876,"personal_choice":"keep"}\n'
        target.write_bytes(original)
        install.initialize()
        self.assertEqual(target.read_bytes(), original)
        self.assertIn('Keeping existing', self.output.getvalue())

    def test_init_creates_private_configuration_and_runs_no_program(self):
        with patch.object(install.subprocess, 'run') as process:
            install.initialize()
        target = self.root / 'orbit.json'
        self.assertEqual(target.read_bytes(), (self.root / 'orbit.example.json').read_bytes())
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        process.assert_not_called()

    def test_missing_template_does_not_leave_empty_configuration(self):
        (self.root / 'orbit.example.json').unlink()
        with self.assertRaises(FileNotFoundError):
            install.initialize()
        self.assertFalse((self.root / 'orbit.json').exists())

    def test_init_does_not_follow_existing_config_symlink(self):
        private = self.root / 'private.json'
        private.write_text('existing-content')
        (self.root / 'orbit.json').symlink_to(private)
        install.initialize()
        self.assertEqual(private.read_text(), 'existing-content')

    def test_doctor_allows_missing_agents_for_browsing(self):
        vault = self.root / 'vault'
        vault.mkdir()
        index = self.root / 'projects.json'
        index.write_text('{"projects":[]}')
        config = types.SimpleNamespace(PROJECT_INDEX=index, VAULT=vault,
                                       CODEX=self.root / 'missing-codex', NODE=self.root / 'missing-node')
        with patch.dict('sys.modules', {'config': config}), \
                patch.object(install.subprocess, 'run', side_effect=FileNotFoundError), \
                patch.object(install.sys, 'platform', 'linux'):
            self.assertEqual(install.doctor(), 0)
        self.assertIn('optional for viewing', self.output.getvalue())

    def test_optional_install_rejects_old_node_before_mutation(self):
        config = types.SimpleNamespace(NODE=self.root / 'node', AUTOLOOP_SOURCE=self.root / 'tools/autoloop')
        with patch.dict('sys.modules', {'config': config}), \
                patch.object(install.subprocess, 'check_output', return_value='v22.0.0\n'), \
                patch.object(install.subprocess, 'run') as process:
            with self.assertRaisesRegex(SystemExit, 'Node.js 24'):
                install.install_optional('harness')
            process.assert_not_called()
        self.assertFalse((self.root / 'tools').exists())

    def test_optional_package_is_exactly_pinned_and_no_login_is_run(self):
        config = types.SimpleNamespace(NODE=self.root / 'node')
        def npm_install(command, **kwargs):
            expected = '@deepseek-ai/dsh@0.2.2-rc.1'
            self.assertEqual(command[-1], expected)
            self.assertIn('--save-exact', command)
            prefix = Path(command[command.index('--prefix') + 1])
            entry = prefix / 'node_modules/@deepseek-ai/dsh/lib/bin.js'
            entry.parent.mkdir(parents=True)
            entry.write_text('// synthetic npm result')
            return types.SimpleNamespace(returncode=0)
        with patch.dict('sys.modules', {'config': config}), \
                patch.object(install.subprocess, 'check_output', return_value='v24.22.1\n'), \
                patch.object(install.shutil, 'which', return_value='/synthetic/npm'), \
                patch.object(install.subprocess, 'run', side_effect=npm_install) as process:
            install.install_optional('harness')
            self.assertEqual(process.call_count, 1)

    def test_existing_modified_upstream_is_preserved(self):
        upstream = self.root / 'tools/autoloop'
        upstream.mkdir(parents=True)
        marker = upstream / 'user-change.txt'
        marker.write_text('keep this')
        config = types.SimpleNamespace(NODE=self.root / 'node', AUTOLOOP_SOURCE=upstream)
        with patch.dict('sys.modules', {'config': config}), \
                patch.object(install.subprocess, 'check_output', side_effect=['v24.22.1\n', install.AUTOLOOP_COMMIT, ' M file.ts\n']), \
                patch.object(install.subprocess, 'run') as process:
            with self.assertRaisesRegex(SystemExit, 'keeping it unchanged'):
                install.install_optional('autoloop')
            process.assert_not_called()
        self.assertEqual(marker.read_text(), 'keep this')


if __name__ == '__main__':
    unittest.main()
