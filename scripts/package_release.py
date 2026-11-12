#!/usr/bin/env python3
"""Create an audited source ZIP from a clean, committed Git HEAD.

Only committed source blobs are packaged; untracked/ignored user data is never
read. The archive, source manifest, and SHA256SUMS are written locally to dist/.
This command never uploads files or creates a remote release.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import zipfile

try:
    from .release_audit import check_content, check_path
except ImportError:
    from release_audit import check_content, check_path


class PackageError(RuntimeError):
    pass


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.PIPE)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def package(root: Path, output: Path | None = None) -> dict:
    root = root.resolve()
    output = (output or root / 'dist').absolute()
    commit = git(root, 'rev-parse', '--verify', 'HEAD').decode().strip()
    if git(root, 'status', '--porcelain', '--untracked-files=no'):
        raise PackageError('Tracked files have staged or working changes. Commit the reviewed release first.')
    try:
        version = git(root, 'show', commit + ':VERSION').decode().strip()
    except (subprocess.CalledProcessError, UnicodeDecodeError) as error:
        raise PackageError('Committed VERSION is missing or invalid.') from error
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?', version):
        raise PackageError('VERSION must be a numeric release version, optionally with a prerelease suffix.')
    prefix = 'orbit-collab-' + version + '/'
    records, findings = [], []
    tree = git(root, 'ls-tree', '-r', '-z', commit)
    for entry in tree.split(b'\x00'):
        if not entry:
            continue
        metadata, raw_path = entry.split(b'\t', 1)
        mode, kind, blob = metadata.decode().split(' ')
        relative = raw_path.decode('utf-8')
        if kind != 'blob' or mode not in {'100644', '100755'}:
            raise PackageError('Only ordinary tracked source files may be packaged: ' + relative)
        problems = check_path(relative)
        findings.extend(asdict(item) for item in problems)
        if problems:
            continue
        data = git(root, 'cat-file', 'blob', blob)
        findings.extend(asdict(item) for item in check_content(relative, data))
        records.append({'path': relative, 'bytes': len(data), 'sha256': sha256(data), 'mode': mode})
    if findings:
        # Findings contain locations and categories, never the matching values.
        raise PackageError('Release audit rejected committed source:\n' + json.dumps(findings, ensure_ascii=False, indent=2))
    if not records:
        raise PackageError('No committed source files were found.')
    archive = git(root, 'archive', '--format=zip', '--prefix=' + prefix, commit)
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        entries = [item for item in zipped.infolist() if not item.is_dir()]
        expected = {prefix + record['path']: record for record in records}
        if len(entries) != len(expected) or {item.filename for item in entries} != set(expected):
            raise PackageError('Archive file set differs from committed source (check export-ignore attributes).')
        for entry in entries:
            record = expected[entry.filename]
            if sha256(zipped.read(entry)) != record['sha256']:
                raise PackageError('Archive content differs from audited source: ' + record['path'])
            executable = bool((entry.external_attr >> 16) & 0o111)
            if executable != (record['mode'] == '100755'):
                raise PackageError('Executable permission was not preserved: ' + record['path'])
    archive_name = 'orbit-collab-' + version + '-source.zip'
    manifest_name = 'orbit-collab-' + version + '-SOURCE_MANIFEST.json'
    manifest = {'version': version, 'commit': commit, 'archive_root': prefix.rstrip('/'),
                'file_count': len(records), 'files': records}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    sums = (sha256(archive) + '  ' + archive_name + '\n' +
            sha256(manifest_bytes) + '  ' + manifest_name + '\n').encode()
    if output.is_symlink():
        raise PackageError('Output directory must not be a symbolic link.')
    output.mkdir(parents=True, exist_ok=True)
    payloads = {archive_name: archive, manifest_name: manifest_bytes, 'SHA256SUMS': sums}
    if any((output / name).is_symlink() for name in payloads):
        raise PackageError('Output files must not be symbolic links.')
    # Nothing becomes a user-facing output until all source and archive checks pass.
    with tempfile.TemporaryDirectory(prefix='.orbit-package-', dir=output) as temporary:
        for name, content in payloads.items():
            staging = Path(temporary) / name
            staging.write_bytes(content)
            os.replace(staging, output / name)
    return {'version': version, 'commit': commit, 'files': len(records),
            'archive': str(output / archive_name), 'sha256': sha256(archive),
            'manifest': str(output / manifest_name), 'checksums': str(output / 'SHA256SUMS')}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, help='output directory (default: repository dist/)')
    args = parser.parse_args()
    try:
        result = package(args.root, args.output)
    except (OSError, ValueError, subprocess.CalledProcessError, PackageError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
