import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from scripts.package_release import PackageError, package


class PackageReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='orbit-package-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git('init', '-q')
        (self.root / 'VERSION').write_text('0.1.0\n')
        (self.root / 'README.md').write_text('Synthetic package fixture.\n')
        (self.root / 'examples').mkdir()
        (self.root / 'examples' / '知识.md').write_text('原创合成示例。\n', encoding='utf-8')
        (self.root / 'start.command').write_text('#!/bin/sh\necho Orbit\n')
        (self.root / 'start.command').chmod(0o755)
        self.commit()

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE)

    def commit(self):
        self.git('add', '.')
        self.git('-c', 'user.name=Orbit Test', '-c', 'user.email=orbit@example.invalid',
                 'commit', '-qm', 'Synthetic source fixture')

    def test_only_committed_source_is_packaged_with_integrity_and_modes(self):
        (self.root / '.env').write_text('PRIVATE=not-for-distribution\n')
        (self.root / 'data').mkdir()
        (self.root / 'data' / 'note.md').write_text('private runtime material')
        result = package(self.root)
        with zipfile.ZipFile(result['archive']) as archive:
            names = archive.namelist()
            self.assertEqual(set(name for name in names if not name.endswith('/')),
                             {'orbit-collab-0.1.0/README.md', 'orbit-collab-0.1.0/VERSION',
                              'orbit-collab-0.1.0/start.command', 'orbit-collab-0.1.0/examples/知识.md'})
            self.assertEqual(archive.read('orbit-collab-0.1.0/examples/知识.md').decode(), '原创合成示例。\n')
            self.assertTrue((archive.getinfo('orbit-collab-0.1.0/start.command').external_attr >> 16) & 0o111)
        manifest = json.loads(Path(result['manifest']).read_text())
        self.assertEqual(manifest['file_count'], 4)
        for line in Path(result['checksums']).read_text().splitlines():
            expected, name = line.split('  ', 1)
            self.assertEqual(hashlib.sha256((self.root / 'dist' / name).read_bytes()).hexdigest(), expected)
        first_bytes = Path(result['archive']).read_bytes()
        self.assertEqual(Path(package(self.root)['archive']).read_bytes(), first_bytes)

    def test_dirty_tracked_or_staged_source_is_rejected(self):
        (self.root / 'README.md').write_text('Unreviewed change')
        with self.assertRaisesRegex(PackageError, 'Commit'):
            package(self.root)
        self.git('add', 'README.md')
        with self.assertRaisesRegex(PackageError, 'Commit'):
            package(self.root)
        self.assertFalse((self.root / 'dist').exists())

    def test_committed_secret_is_rejected_without_outputs(self):
        token = 's' + 'k-' + 'Ab3dE5fG7hJ9kL2mN4pQ6rS8tU0vW2xY'
        (self.root / 'bad.txt').write_text(token)
        self.commit()
        with self.assertRaises(PackageError) as caught:
            package(self.root)
        self.assertNotIn(token, str(caught.exception))
        self.assertIn('provider token', str(caught.exception))
        self.assertFalse((self.root / 'dist').exists())

    def test_committed_symlink_is_rejected(self):
        (self.root / 'alias.md').symlink_to('README.md')
        self.commit()
        with self.assertRaisesRegex(PackageError, 'ordinary tracked'):
            package(self.root)

    def test_invalid_version_cannot_change_archive_path(self):
        (self.root / 'VERSION').write_text('../outside\n')
        self.commit()
        with self.assertRaisesRegex(PackageError, 'VERSION'):
            package(self.root)


if __name__ == '__main__':
    unittest.main()
