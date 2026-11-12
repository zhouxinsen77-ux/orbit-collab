#!/usr/bin/env python3
"""Set up Orbit without touching an existing user's projects or credentials."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
AUTOLOOP_COMMIT = 'fade2cee76bd91ed38e889b9a91276fd78e90625'


def initialize():
    target = ROOT / 'orbit.json'
    # Read and validate the template before creating a configuration file.
    template = (ROOT / 'orbit.example.json').read_text(encoding='utf-8')
    if not isinstance(json.loads(template), dict):
        raise SystemExit('orbit.example.json must contain a JSON object.')
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(template)
        print('Created orbit.json with the included example project and knowledge base.')
    except FileExistsError:
        print('Keeping existing orbit.json.')
    print('Start: python3 launcher.py start --open')
    print('Agent setup: see README.md; no model is invoked by initialization.')


def doctor():
    import config
    print('Orbit 0.1.0 / Python ' + sys.version.split()[0])
    checks = {'platform': sys.platform in {'darwin', 'linux'},
              'python': sys.version_info >= (3, 9),
              'project_index': config.PROJECT_INDEX.is_file(),
              'vault': config.VAULT.is_dir()}
    for key, good in checks.items():
        print(('OK   ' if good else 'FAIL ') + key)
    for name, binary in [('Codex', config.CODEX), ('Node', config.NODE)]:
        try:
            result = subprocess.run([str(binary), '--version'], capture_output=True, text=True, timeout=8)
            print(name + ': ' + (result.stdout.strip()[:120] if result.returncode == 0 else 'not ready'))
        except (OSError, subprocess.TimeoutExpired):
            print(name + ': not installed (optional for viewing the workspace)')
    try:
        result = subprocess.run([str(config.CODEX), 'exec', '--help'], capture_output=True, text=True, timeout=8)
        required = ('--ignore-user-config', '--ignore-rules', '--output-schema', '--ephemeral')
        print('Codex automation flags: ' + ('OK' if all(x in result.stdout for x in required) else 'upgrade Codex before running tasks'))
    except (OSError, subprocess.TimeoutExpired):
        pass
    print('No credentials are printed. Missing agents do not prevent local browsing.')
    return 0 if all(checks.values()) else 1


def install_optional(component):
    if component not in {'harness', 'workbuddy', 'autoloop'}:
        raise ValueError('Unknown optional component')
    import config
    try:
        version = subprocess.check_output([str(config.NODE), '--version'], text=True).strip()
        if int(version.lstrip('v').split('.')[0]) < 24:
            raise ValueError('Node 24 or newer required')
    except (OSError, ValueError, subprocess.CalledProcessError):
        raise SystemExit('Install Node.js 24+ first, then rerun this command.')
    env = os.environ.copy()
    env['PATH'] = str(Path(config.NODE).parent) + os.pathsep + env.get('PATH', '')
    npm = shutil.which('npm', path=env['PATH'])
    if component in {'harness', 'workbuddy'}:
        if not npm:
            raise SystemExit('npm is missing; install the standard Node.js distribution.')
        package, folder = {'harness': ('@deepseek-ai/dsh@0.2.2-rc.1', 'harness-runtime'),
                           'workbuddy': ('@tencent-ai/codebuddy-code@2.181.0', 'workbuddy')}[component]
        target = ROOT / 'tools' / folder
        target.mkdir(parents=True, exist_ok=True)
        subprocess.run([npm, 'install', '--prefix', str(target), '--save-exact', '--no-audit', '--no-fund', package], env=env, check=True)
        entry = target / ('node_modules/@deepseek-ai/dsh/lib/bin.js' if component == 'harness' else 'node_modules/.bin/codebuddy')
        if not entry.is_file():
            raise SystemExit('Installation completed without the expected executable; inspect npm output before continuing.')
        print('Installed optional provider. Configure your own login/key as described in README.md.')
    else:
        target = config.AUTOLOOP_SOURCE
        if target.exists():
            try:
                actual = subprocess.check_output(['git', '-C', str(target), 'rev-parse', 'HEAD'], text=True, stderr=subprocess.DEVNULL).strip()
                dirty = subprocess.check_output(['git', '-C', str(target), 'status', '--porcelain', '--untracked-files=normal'], text=True).strip()
            except (OSError, subprocess.CalledProcessError):
                raise SystemExit('Existing Autoloop directory is not a complete Git checkout; keeping it unchanged. Move it aside before reinstalling.')
            if actual != AUTOLOOP_COMMIT or dirty or not (target / 'bin/agent-autoloop.ts').is_file():
                raise SystemExit('Existing Autoloop directory differs from the clean pinned commit; keeping it unchanged.')
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['git', 'clone', '--no-checkout', 'https://github.com/KKKKhazix/agent-autoloop.git', str(target)], check=True)
            subprocess.run(['git', '-C', str(target), 'checkout', '--detach', AUTOLOOP_COMMIT], check=True)
        print('Optional event engine installed. Enable: python3 autoloop_service.py start')


def main():
    parser = argparse.ArgumentParser(description='Orbit local setup (macOS / Linux)')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('init')
    sub.add_parser('doctor')
    optional = sub.add_parser('install')
    optional.add_argument('component', choices=['harness', 'workbuddy', 'autoloop'])
    args = parser.parse_args()
    if sys.version_info < (3, 9) or sys.platform not in {'darwin', 'linux'}:
        raise SystemExit('Orbit requires Python 3.9+ on macOS or Linux. Windows users can try WSL2; it is not yet verified.')
    if args.action == 'init':
        initialize()
    elif args.action == 'doctor':
        return doctor()
    else:
        install_optional(args.component)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
