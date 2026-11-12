#!/usr/bin/env python3
"""Audit an explicit release file list. Reports locations, never secret values.

Use --staged before committing to inspect the actual Git index, or --tracked
to inspect tracked working files. Untracked runtime data is never traversed.
This is a publication guard, not a guarantee that arbitrary prose is private-free.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

DENIED_PARTS = {
    '.git', '.codex', '.codebuddy', '.ssh', '__pycache__', 'node_modules',
    'data', 'work', 'verification', 'outputs', '成果', '设计记录', '原始资料',
    'Codex 对话', 'credentials', 'secrets', 'private',
}
DENIED_NAMES = {'orbit.json', '.env', '.DS_Store', 'auth.json', 'credentials.json'}
ALLOWED_SUFFIXES = {'.py', '.md', '.txt', '.json', '.js', '.css', '.html', '.svg',
                    '.yml', '.yaml', '.toml', '.sh', '.command', '.example'}
ALLOWED_BARE_NAMES = {'LICENSE', 'NOTICE', 'COPYING', '.gitignore', '.gitattributes',
                      'Dockerfile', 'Makefile', 'VERSION'}
PATTERNS = {
    'personal absolute path': re.compile(r'/(?:Users|home)/[^/\s\'"<>]+/'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'provider token': re.compile(r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}'),
    'GitHub token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})'),
    'cloud access key': re.compile(r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    'credential URL': re.compile(r'https?://[^\s/:]+:[^\s/@]+@'),
}
SECRET_ASSIGNMENT = re.compile(
    r'''(?im)["']?\b(?:[A-Z_]*(?:API_KEY|ACCESS_TOKEN|AUTH_TOKEN|TOKEN|PASSWORD|SECRET))["']?\s*[:=]\s*(?:["']([^"'\r\n]{16,})["']|([A-Za-z0-9_+/=.-]{20,})(?:\s|$))''')


@dataclass(frozen=True)
class Finding:
    path: str
    reason: str
    line: int | None = None


def entropy(value: str) -> float:
    return -sum((value.count(char) / len(value)) * math.log2(value.count(char) / len(value))
                for char in set(value)) if value else 0.0


def check_path(relative: str) -> list[Finding]:
    path = PurePosixPath(relative)
    findings = []
    if path.is_absolute() or '..' in path.parts or not path.parts or '\\' in relative:
        return [Finding(relative, 'unsafe manifest path')]
    if any(part in DENIED_PARTS for part in path.parts):
        findings.append(Finding(relative, 'runtime or private directory'))
    if path.name in DENIED_NAMES or (path.name.startswith('.env.') and path.name != '.env.example'):
        findings.append(Finding(relative, 'local credentials or configuration'))
    if path.name in {'PROJECT.md', 'AGENTS.md'} and path.parts[0] != 'examples':
        findings.append(Finding(relative, 'personal project history or instructions'))
    if path.suffix.lower() not in ALLOWED_SUFFIXES and path.name not in ALLOWED_BARE_NAMES:
        findings.append(Finding(relative, 'file type requires explicit release review'))
    return findings


def check_content(relative: str, data: bytes) -> list[Finding]:
    if len(data) > 10 * 1024 * 1024:
        return [Finding(relative, 'file exceeds source release size limit')]
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        return [Finding(relative, 'non-UTF-8 or binary file')]
    if '\x00' in text:
        return [Finding(relative, 'binary NUL content')]
    findings = []
    for reason, pattern in PATTERNS.items():
        for match in pattern.finditer(text):
            findings.append(Finding(relative, reason, text.count('\n', 0, match.start()) + 1))
    # Entropy is restricted to credential-looking assignments, avoiding minified
    # JavaScript and SHA-256 provenance hashes. No matching value is ever printed.
    for match in SECRET_ASSIGNMENT.finditer(text):
        value = match.group(1) or match.group(2)
        if entropy(value) >= 3.5 and re.search(r'[a-zA-Z]', value):
            findings.append(Finding(relative, 'high-entropy credential assignment',
                                    text.count('\n', 0, match.start()) + 1))
    return findings


def audit(root: Path, files: list[str], staged: bool = False) -> list[Finding]:
    root = root.resolve()
    findings = []
    for relative in sorted(set(files)):
        path_findings = check_path(relative)
        findings.extend(path_findings)
        if path_findings:
            continue
        path = root / relative
        if staged:
            info = subprocess.check_output(['git', 'ls-files', '--stage', '--', relative], cwd=root)
            if info.startswith(b'120000 '):
                findings.append(Finding(relative, 'symbolic link'))
                continue
            try:
                data = subprocess.check_output(['git', 'show', ':' + relative], cwd=root, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                findings.append(Finding(relative, 'cannot read staged blob'))
                continue
        else:
            if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root and root in p.parents):
                findings.append(Finding(relative, 'symbolic link'))
                continue
            if root not in path.resolve().parents:
                findings.append(Finding(relative, 'path escapes release root'))
                continue
            try:
                data = path.read_bytes()
            except OSError:
                findings.append(Finding(relative, 'cannot read release file'))
                continue
        findings.extend(check_content(relative, data))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--manifest', type=Path, help='UTF-8 file, one relative source path per line')
    selection.add_argument('--tracked', action='store_true', help='check tracked working files')
    selection.add_argument('--staged', action='store_true', help='check actual Git index blobs')
    args = parser.parse_args()
    if args.manifest:
        files = [line.strip() for line in args.manifest.read_text().splitlines() if line.strip()]
    else:
        files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=args.root).decode().split('\x00')
        files = [path for path in files if path]
    if not files:
        print(json.dumps({'files': 0, 'passed': False, 'error': 'empty release file list'}))
        return 2
    findings = audit(args.root, files, staged=args.staged)
    print(json.dumps({'files': len(set(files)), 'passed': not findings,
                      'findings': [asdict(item) for item in findings]}, ensure_ascii=False, indent=2))
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main())
