import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.release_audit import audit, check_content, check_path


class ReleaseAuditTests(unittest.TestCase):
    def test_private_paths_and_runtime_state_are_rejected(self):
        for name in ('data/run.json', 'work/note.md', '.env', '.env.local',
                     'orbit.json', 'PROJECT.md', 'AGENTS.md', 'archive/PROJECT.md', 'dump.sqlite',
                     '../README.md', '/tmp/file.md'):
            with self.subTest(name=name):
                self.assertTrue(check_path(name))
        for name in ('README.md', '.env.example', 'examples/project/PROJECT.md',
                     '.github/workflows/test.yml', 'LICENSE'):
            with self.subTest(name=name):
                self.assertEqual(check_path(name), [])

    def test_secrets_report_only_locations(self):
        token = 's' + 'k-' + 'Ab3dE5fG7hJ9kL2mN4pQ6rS8tU0vW2xY'
        findings = check_content('example.py', ('key = "' + token + '"\n').encode())
        self.assertTrue(findings)
        self.assertEqual(findings[0].line, 1)
        self.assertNotIn(token, repr(findings))
        self.assertTrue(check_content('.env.example', ('API_' + 'KEY=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789\n').encode()))
        personal_path = '/' + 'Users/' + 'private-person/project/'
        self.assertTrue(check_content('README.md', personal_path.encode()))
        self.assertEqual(check_content('provenance.json', ('"sha256":"' + 'a1b2c3d4' * 8 + '"').encode()), [])

    def test_symlink_is_never_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'outside.md').write_text('not for release')
            (root / 'link.md').symlink_to(root / 'outside.md')
            self.assertEqual(audit(root, ['link.md'])[0].reason, 'symbolic link')

    def test_staged_audit_reads_staged_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
            token = 'g' + 'hp_' + 'Ab3dE5fG7hJ9kL2mN4pQ6rS8tU0vW2xY'
            (root / 'file.txt').write_text(token)
            subprocess.run(['git', 'add', 'file.txt'], cwd=root, check=True)
            (root / 'file.txt').write_text('safe working tree')
            self.assertTrue(audit(root, ['file.txt'], staged=True))
            self.assertEqual(audit(root, ['file.txt']), [])


if __name__ == '__main__':
    unittest.main()
