"""Local owner/source proxy for the pinned upstream engine; secrets stay server-side."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from config import ROOT, DATA_DIR, NODE, AUTOLOOP_SOURCE, PROJECT_INDEX, PORT, AUTOLOOP_PORT
DATA = DATA_DIR / 'autoloop'
SOURCE = AUTOLOOP_SOURCE
ENTRY = SOURCE / 'bin/agent-autoloop.ts'
INDEX = PROJECT_INDEX
URL = 'http://127.0.0.1:' + str(AUTOLOOP_PORT)
VERSION = 'v1.7.0'
COMMIT = 'fade2cee76bd91ed38e889b9a91276fd78e90625'
LOCK = threading.RLock()
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(path.name + '.tmp')
    with open(temp, 'w', encoding='utf-8') as stream:
        os.chmod(temp, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
    temp.replace(path)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def projects():
    result = []
    for item in read_json(INDEX, {}).get('projects', []):
        if not isinstance(item, dict) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,50}', item.get('key', '')):
            continue
        path = Path(item.get('root', '')).expanduser()
        if not path.is_absolute():
            path = INDEX.parent / path
        if path.is_dir():
            result.append(dict(item, root=str(path.resolve())))
    return result


def rule_name(key):
    return '项目协作 · ' + key


def environment():
    env = os.environ.copy()
    env['AGENT_AUTOLOOP_HOME'] = str(DATA)
    env['PATH'] = str(NODE.parent) + ':' + env.get('PATH', '')
    return env


def generate_rules(worker):
    agents, rules = {}, []
    for project in projects():
        key = project['key']
        agent_id = 'workbench-' + key.replace('_', '-')
        agents[agent_id] = {'name': project['name'] + '协作队列', 'worker': worker,
            'use': 'command', 'access': 'read', 'timeout': 120,
            'command': [sys.executable, str(ROOT / 'autoloop_bridge.py'), '--project', key, '--context', '{context}']}
        rules.append({'name': rule_name(key), 'when': {'source': 'local', 'types': ['workbench.' + key]},
            'to': agent_id, 'cwd': str(ROOT), 'group': key, 'parallel': 2, 'resume': False, 'tell': 'never',
            'prompt': '仅处理本机协作台已登记的请求。项目与执行权限固定于规则。已有任务只核验，不重复执行。'})
    return {'timezone': 'Asia/Shanghai', 'sources': {'local': {'name': '本机协作台'}},
            'agents': agents, 'channels': {}, 'rules': rules, 'elsewhere': []}


def initialize():
    with LOCK:
        if not ENTRY.is_file() or not NODE.is_file():
            raise RuntimeError('Autoloop 源码或 Node 24 运行环境缺失')
        actual = subprocess.check_output(['git', '-C', str(SOURCE), 'rev-parse', 'HEAD'], text=True).strip()
        if actual != COMMIT:
            raise RuntimeError('Autoloop 版本与已验证版本不一致，请先核验升级')
        DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(DATA, 0o700)
        if not (DATA / 'config.json').exists():
            # Supplying rules before init suppresses the default desktop notification channel.
            write_json(DATA / 'rules.json', generate_rules('workbench-local'))
            result = subprocess.run([str(NODE), '--disable-warning=ExperimentalWarning', str(ENTRY),
                'init', '--command', '/usr/bin/true', '--port', str(AUTOLOOP_PORT), '--no-service'],
                cwd=SOURCE, env=environment(), capture_output=True, timeout=30)
            if result.returncode:
                raise RuntimeError('Autoloop 初始化失败；未注册系统服务')
        config = read_json(DATA / 'config.json', {})
        config['worker']['capacity'] = 2
        config['channels'] = {}
        write_json(DATA / 'config.json', config)
        write_json(DATA / 'rules.json', generate_rules(config['worker']['name']))
        write_json(DATA / 'installation.json', {'version': VERSION, 'commit': COMMIT, 'local_only': True,
            'source_url': 'https://github.com/KKKKhazix/agent-autoloop', 'service': 'manual-launcher',
            'schedules': 0, 'external_channels': 0})


def request(path, payload=None, source=False, timeout=3):
    config = read_json(DATA / 'config.json', {})
    secret = config.get('source', {}).get('key') if source else config.get('owner')
    if not secret:
        raise RuntimeError('事件服务尚未初始化')
    # The origin is fixed; no request-supplied URL or token crosses this boundary.
    req = urllib.request.Request(URL + path, data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode(),
        headers={'Authorization': 'Bearer ' + secret, 'Content-Type': 'application/json'})
    with OPENER.open(req, timeout=timeout) as response:
        return json.load(response)


def state():
    configured = {r.get('name') for r in read_json(DATA / 'rules.json', {}).get('rules', [])}
    base = {'installed': ENTRY.is_file() and (DATA / 'config.json').is_file(), 'running': False,
        'version': VERSION, 'commit': COMMIT, 'url': URL, 'source_url': 'https://github.com/KKKKhazix/agent-autoloop',
        'detail': '可选 Autoloop 事件引擎；固定项目规则派发到 Orbit，等待人工时释放事件执行器。',
        'rules': [{'name': rule_name(p['key']), 'project_key': p['key'], 'project_name': p['name'], 'paused': False} for p in projects() if rule_name(p['key']) in configured],
        'runs': [], 'cards': [], 'worker': {'online': False, 'capacity': 2}, 'counts': {}}
    try:
        upstream = request('/v1/state')
    except Exception:
        base['detail'] = '可选事件服务未启动；Orbit 核心功能可独立使用。安装步骤见 README。'
        return base
    base['running'] = True
    base['worker']['online'] = any(a.get('online') for a in upstream.get('agents', []))
    base['counts'] = {k: upstream.get('totals', {}).get(k, 0) for k in ('running', 'queued', 'attention', 'done')}
    paused = {r['name'] for r in upstream.get('lines', []) if r.get('paused')}
    for rule in base['rules']:
        rule['paused'] = rule['name'] in paused
    for run in upstream.get('runs', []):
        mapping = read_json(DATA / 'bridge' / (str(run['id']) + '.json'), {})
        ident = mapping.get('platform_run_id')
        item = {'id': run['id'], 'status': run.get('state'), 'rule': run.get('rule'),
            'title': mapping.get('title') or run.get('summary') or '事件已进入队列',
            'platform_run_id': ident, 'platform_url': '/#run/' + ident if re.fullmatch(r'[a-f0-9]{16}', ident or '') else None,
            'updated_at': run.get('finishedAt') or run.get('claimedAt') or run.get('createdAt')}
        if item['platform_url']:
            try:
                with OPENER.open('http://127.0.0.1:' + str(PORT) + '/api/runs/' + ident, timeout=2) as response:
                    platform = json.load(response)
                item['status'], item['title'] = platform['status'], platform['title']
                if mapping.get('status') == 'cancel_pending' and platform['status'] not in {'completed', 'cancelled', 'failed', 'review_failed', 'interrupted'}:
                    item['status'] = '取消待核验，请重启平台'
            except Exception:
                item['status'] = mapping.get('status') or 'interrupted'
        elif item['status'] == 'done':
            item['status'] = '已结束事件轮次'
        base['runs'].append(item)
    for card in upstream.get('cards', []):
        match = re.fullmatch(r'workbench-run:([a-f0-9]{16})', card.get('about', ''))
        base['cards'].append({'id': card['id'], 'kind': card.get('kind'), 'title': card.get('title'), 'text': card.get('text'),
            'platform_url': '/#run/' + match.group(1) if match else None, 'options': card.get('options', [])})
    return base


def start():
    with LOCK:
        from autoloop_bridge import reconcile_cancel_pending
        reconcile_cancel_pending(DATA)
        if state()['running']:
            return state()
        initialize()
        with open(DATA / 'service.log', 'ab', buffering=0) as log:
            proc = subprocess.Popen([str(NODE), '--disable-warning=ExperimentalWarning', str(ENTRY), 'up'],
                cwd=SOURCE, env=environment(), stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        time.sleep(.2)
        identity = subprocess.check_output(['/bin/ps', '-p', str(proc.pid), '-o', 'lstart='], text=True).strip() if proc.poll() is None else ''
        write_json(DATA / 'process.json', {'pid': proc.pid, 'started': identity})
        for _ in range(35):
            if proc.poll() is not None:
                raise RuntimeError('事件服务启动失败，详情见 data/autoloop/service.log')
            if state()['running']:
                return state()
            time.sleep(.2)
        raise RuntimeError('事件服务尚未就绪，请查看本机日志')


def stop():
    with LOCK:
        record = read_json(DATA / 'process.json', {})
        pid = record.get('pid')
        if not isinstance(pid, int):
            return
        try:
            command = subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'command='], text=True)
            started = subprocess.check_output(['/bin/ps', '-p', str(pid), '-o', 'lstart='], text=True).strip()
            if str(ENTRY) not in command or started != record.get('started'):
                raise RuntimeError('事件服务进程身份已变化，保留其他进程')
            os.kill(pid, signal.SIGTERM)
            for _ in range(40):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(.2)
        except subprocess.CalledProcessError:
            pass
        (DATA / 'process.json').unlink(missing_ok=True)


def emit(payload, attach_existing=None):
    if not isinstance(payload, dict):
        raise ValueError('事件参数必须为对象')
    rule = next((p for p in projects() if rule_name(p['key']) == payload.get('rule_name')), None)
    title, brief, key = (payload.get(k) for k in ('title', 'brief', 'idempotency_key'))
    if not rule or not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or not isinstance(brief, str) or not 1 <= len(brief.strip()) <= 12000:
        raise ValueError('请选择项目并填写任务名称和目标')
    if rule_name(rule['key']) not in {r.get('name') for r in read_json(DATA / 'rules.json', {}).get('rules', [])}:
        raise ValueError('该项目尚未同步到事件规则，请重启平台后再提交')
    if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', key):
        raise ValueError('事件去重编号无效')
    event_id = 'wb-' + hashlib.sha256(key.encode()).hexdigest()
    record = {'event_id': event_id, 'event_type': 'workbench.' + rule['key'], 'source': 'local', 'project_key': rule['key'], 'title': title.strip(), 'brief': brief.strip(),
        'idempotency_key': 'autoloop:' + hashlib.sha256(('local\n' + event_id).encode()).hexdigest(), 'action': 'create'}
    if attach_existing:
        record.update(action='attach_existing', platform_run_id=attach_existing)
    filename = DATA / 'requests' / (hashlib.sha256(event_id.encode()).hexdigest() + '.json')
    with LOCK:
        old = read_json(filename)
        if old is not None and old != record:
            raise ValueError('同一去重编号已经用于另一份事件内容')
        write_json(filename, record)
        digest = hashlib.sha256(json.dumps({k: record[k] for k in ('project_key', 'title', 'brief')},
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        response = request('/v1/events', {'id': event_id, 'type': 'workbench.' + rule['key'], 'subject': event_id,
            'summary': title.strip(), 'detail': json.dumps({'request_id': event_id, 'request_sha256': digest})}, source=True)
    return {'id': response.get('event'), 'event_id': event_id}


def answer(card_id):
    card = next((c for c in state()['cards'] if c['id'] == card_id), None)
    if not card:
        raise ValueError('待办卡片不存在或已经处理')
    if card.get('kind') == 'notice':
        return request('/v1/cards/%d/resolve' % card_id, {'reason': '本人已在本机工作台核对并标记处理'})
    return request('/v1/cards/%d/answer' % card_id, {'choice': 0})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['init', 'start', 'stop', 'status'])
    args = parser.parse_args()
    if args.action == 'init':
        initialize()
        print('Autoloop 已初始化；没有安装系统服务、定时任务或外部通道。')
    elif args.action == 'stop':
        stop()
        print('事件服务已停止。')
    else:
        print(json.dumps(start() if args.action == 'start' else state(), ensure_ascii=False, indent=2))
