"""Portable, local-only configuration. No personal folders are auto-discovered.

Paths in orbit.json are relative to that file. Environment overrides use the
ORBIT_<FIELD_NAME> convention; secrets belong in the environment or .env.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get('ORBIT_CONFIG', str(ROOT / 'orbit.json'))).expanduser().absolute()
CONFIG_DIR = CONFIG_PATH.parent


def _load():
    if not CONFIG_PATH.exists():
        if 'ORBIT_CONFIG' in os.environ:
            raise ValueError('ORBIT_CONFIG points to a missing configuration file')
        return {}
    try:
        value = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError('Cannot read orbit.json; expected a UTF-8 JSON object') from exc
    if not isinstance(value, dict):
        raise ValueError('orbit.json must be a JSON object')
    return value


SETTINGS = _load()


def setting(name, default):
    return os.environ.get('ORBIT_' + name.upper(), SETTINGS.get(name, default))


def resolve_path(value, base=None):
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError('Configured paths must be non-empty strings')
    path = Path(value).expanduser()
    return (path if path.is_absolute() else (base or CONFIG_DIR) / path).absolute()


def path_setting(name, default):
    return resolve_path(setting(name, default))


def command_setting(name, default):
    value = setting(name, default)
    if not isinstance(value, str) or not value.strip() or '\x00' in value:
        raise ValueError('Configured executables must be non-empty strings')
    if '/' not in value and '\\' not in value:
        return shutil.which(value) or str(CONFIG_DIR / '.missing-executable' / value)
    return str(resolve_path(value))


def port_setting(name, default):
    value = setting(name, default)
    if isinstance(value, bool):
        raise ValueError(name + ' must be an integer from 1024 to 65535')
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(name + ' must be an integer from 1024 to 65535') from exc
    if not 1024 <= port <= 65535 or (isinstance(value, float) and value != port):
        raise ValueError(name + ' must be an integer from 1024 to 65535')
    return port


DATA_DIR = path_setting('data_dir', 'data')
VAULT = path_setting('vault', 'examples/vault')
PROJECT_INDEX = path_setting('project_index', 'examples/projects.json')
CODEX = command_setting('codex_bin', 'codex')
NODE = Path(command_setting('node_bin', 'node'))
DSH = path_setting('harness_entry', 'tools/harness-runtime/node_modules/@deepseek-ai/dsh/lib/bin.js')
DEEPSEEK_ENV = path_setting('deepseek_env_file', '.env')
CODEBUDDY_ENTRY = Path(command_setting('codebuddy_entry', 'tools/workbuddy/node_modules/.bin/codebuddy'))
AUTOLOOP_SOURCE = path_setting('autoloop_dir', 'tools/agent-autoloop')
PORT = port_setting('port', 8765)
AUTOLOOP_PORT = port_setting('autoloop_port', 3350)
if PORT == AUTOLOOP_PORT:
    raise ValueError('port and autoloop_port must be different')
INSTANCE_ID = hashlib.sha256((str(ROOT.resolve()) + '\0' + str(DATA_DIR.resolve())).encode()).hexdigest()[:24]
