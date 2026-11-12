#!/usr/bin/env python3
"""Trusted command bridge: Autoloop events enter the existing workbench once.

Only requests already saved by the local owner proxy may create work. Provider
selection, project boundaries, review and PROJECT archival remain in server.py.
This command never reads credentials or directly invokes a model.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from config import ROOT, DATA_DIR, PORT, INSTANCE_ID
DATA = DATA_DIR / 'autoloop'
ROLES = {role: 'codex' for role in ('planner', 'researcher', 'executor', 'reviewer')}
WAITING = {'waiting_input', 'paused', 'failed', 'review_failed', 'interrupted'}
ACTIVE = {'queued', 'planning', 'running', 'reviewing'}
RUN_ID = re.compile(r'[a-f0-9]{16}')
ABOUT = re.compile(r'workbench-run:([a-f0-9]{16})')


class BridgeError(RuntimeError):
    """A bounded, public-safe explanation; never raw model or HTTP output."""


class NetworkError(BridgeError):
    pass


class BridgeCancelled(BridgeError):
    pass


def request_key(source, event_id):
    return 'autoloop:' + hashlib.sha256((source + '\n' + event_id).encode()).hexdigest()


def request_filename(data, event_id):
    return Path(data) / 'requests' / (hashlib.sha256(event_id.encode()).hexdigest() + '.json')


def request_fingerprint(record):
    value = {key: record[key] for key in ('project_key', 'title', 'brief')}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def read_json(path, limit=2 * 1024 * 1024):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise BridgeError('本机请求或映射文件无效')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise BridgeError('本机请求或映射文件无法读取') from exc
    if not isinstance(value, dict):
        raise BridgeError('本机请求或映射必须是 JSON 对象')
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def receipt(text, **fields):
    if not fields:
        return text
    return text + '\n\n```agent-autoloop\n' + json.dumps(fields, ensure_ascii=False) + '\n```'


class PlatformClient:
    def __init__(self, url=None, timeout=4):
        url = url or ('http://127.0.0.1:' + str(PORT))
        parsed = urllib.parse.urlparse(url)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or
                parsed.username or parsed.password or parsed.path not in {'', '/'} or
                parsed.query or parsed.fragment or not parsed.port):
            raise BridgeError('工作台地址必须是本机回环 HTTP 地址')
        self.url, self.timeout = url.rstrip('/'), timeout
        self.verified = False
        # Ignore inherited proxies: the destination is this machine only.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self, method, path, payload=None):
        if not self.verified:
            try:
                with self.opener.open(self.url + '/api/health', timeout=self.timeout) as response:
                    health = json.load(response)
                if not isinstance(health, dict) or health.get('instance_id') != INSTANCE_ID:
                    raise BridgeError('目标工作台不属于此 Orbit 实例；未提交任务')
                self.verified = True
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                raise NetworkError('无法核验此 Orbit 实例；未提交任务') from exc
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request(self.url + path, data=data, method=method,
            headers={'Content-Type': 'application/json', 'Origin': self.url})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise BridgeError('工作台响应超过桥接读取上限')
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise BridgeError('工作台响应格式无效')
            return value
        except urllib.error.HTTPError as exc:
            raise NetworkError('工作台请求失败（HTTP %d）；保留原任务，稍后核验' % exc.code) from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise NetworkError('暂时无法连接本机工作台；保留原任务，稍后核验') from exc


class Bridge:
    def __init__(self, project_key, official_run_id, data=DATA, client=None,
                 poll_interval=2, stop_event=None):
        if not isinstance(project_key, str) or not project_key or len(project_key) > 100:
            raise BridgeError('项目标识无效')
        if not re.fullmatch(r'[1-9][0-9]{0,18}', str(official_run_id)):
            raise BridgeError('缺少有效的官方运行编号')
        self.project_key, self.official_run_id = project_key, str(official_run_id)
        self.data = Path(data)
        self.client = client or PlatformClient()
        self.poll_interval = poll_interval
        self.stop_event = stop_event or threading.Event()
        self.record = None
        self.platform_run_id = None

    @property
    def mapping_file(self):
        return self.data / 'bridge' / (self.official_run_id + '.json')

    def save_mapping(self, status):
        if not self.record or not self.platform_run_id:
            return
        write_json(self.mapping_file, {
            'autoloop_run_id': int(self.official_run_id),
            'event_id': self.record['event_id'], 'source': self.record['source'],
            'project_key': self.project_key, 'platform_run_id': self.platform_run_id,
            'title': self.record['title'], 'status': status,
            'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        })

    def load_request(self, event_id):
        if not isinstance(event_id, str) or not event_id or len(event_id) > 200:
            raise BridgeError('事件编号无效')
        record = read_json(request_filename(self.data, event_id))
        if (record.get('event_id') != event_id or record.get('source') != 'local' or
                record.get('project_key') != self.project_key or
                record.get('event_type') != 'workbench.' + self.project_key or
                record.get('idempotency_key') != request_key('local', event_id)):
            raise BridgeError('事件与本机已授权请求不匹配')
        for key, maximum in [('title', 120), ('brief', 12000)]:
            value = record.get(key)
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise BridgeError('本机请求的任务标题或要求无效')
        if record.get('action') not in {'create', 'attach_existing'}:
            raise BridgeError('本机请求的动作无效')
        return record

    def validate_event(self, event):
        if not isinstance(event, dict):
            raise BridgeError('事件格式无效')
        record = self.load_request(event.get('id'))
        if event.get('source') != record['source'] or event.get('type') != record['event_type']:
            raise BridgeError('事件来源或类型与本机请求不匹配')
        try:
            detail = json.loads(event.get('detail', ''))
        except (TypeError, ValueError) as exc:
            raise BridgeError('事件详情必须是已登记的 JSON 请求') from exc
        compact = {'request_id': record['event_id'], 'request_sha256': request_fingerprint(record)}
        if detail != compact:
            raise BridgeError('事件详情与本机请求不匹配；不接受执行器或目录覆盖')
        return record

    def validate_run(self, run):
        if (not isinstance(run, dict) or not RUN_ID.fullmatch(str(run.get('id', ''))) or
                run.get('project_key') != self.project_key or
                (self.platform_run_id and run['id'] != self.platform_run_id)):
            raise BridgeError('工作台运行与本次项目映射不匹配')
        self.platform_run_id = run['id']
        return run

    def resume_answer(self, context):
        answers = context['answers']
        targets = set()
        for answer in answers:
            match = ABOUT.fullmatch(str(answer.get('about', ''))) if isinstance(answer, dict) else None
            if not match:
                raise BridgeError('卡片回答缺少有效的工作台映射')
            targets.add(match.group(1))
        if len(targets) != 1:
            raise BridgeError('一轮回答只能核验同一个工作台任务')
        ident = targets.pop()
        matches = []
        for path in (self.data / 'bridge').glob('[0-9]*.json'):
            mapping = read_json(path)
            if mapping.get('platform_run_id') == ident and mapping.get('project_key') == self.project_key:
                matches.append(mapping)
        # A task can deliberately be attached again to recover a discarded
        # receipt. The answer event keeps the originating lane as subject;
        # use that to select the right trusted request rather than conflating
        # the two attachments or dispatching anything new.
        subjects = {e.get('subject') for e in context.get('events', [])
                    if isinstance(e, dict) and isinstance(e.get('subject'), str) and e.get('subject')}
        if subjects:
            if len(subjects) != 1:
                raise BridgeError('回答事件来自不同任务事项，拒绝合并核验')
            matches = [m for m in matches if m.get('event_id') in subjects]
        if not matches:
            raise BridgeError('卡片引用的任务不属于本机已登记映射')
        event_ids = {m.get('event_id') for m in matches}
        if len(event_ids) != 1:
            raise BridgeError('工作台映射存在冲突，保留任务等待检查')
        self.record = self.load_request(event_ids.pop())
        self.platform_run_id = ident
        return self.validate_run(self.client('GET', '/api/runs/' + ident))

    def cancel_if_requested(self):
        if not self.stop_event.is_set():
            return
        if self.platform_run_id:
            try:
                result = self.client('POST', '/api/runs/' + self.platform_run_id + '/cancel', {})
                self.save_mapping(result.get('status', 'cancel_requested'))
            except BridgeError:
                self.save_mapping('cancel_pending')
                raise BridgeCancelled('已收到取消，但工作台连接中断；请在工作台核验取消状态')
        raise BridgeCancelled('已请求取消工作台任务')

    def ask(self, run, context):
        status = run['status']
        descriptions = {
            'waiting_input': ('等待人工交接', '请在工作台打开对应任务，将客户端成果回填后再继续核验。'),
            'paused': ('工作台任务已暂停', '请在工作台继续任务后，再回到此处核验。'),
            'failed': ('工作台任务执行失败', '请在工作台查看原因；需要重试时由你在工作台操作。此卡片只重新核验。'),
            'review_failed': ('工作台审查未通过', '请在工作台查看修改意见并决定重试。此卡片不会自动重做任务。'),
            'interrupted': ('工作台任务已中断', '请在工作台检查恢复条件并决定重试，再回来核验。'),
            'archive_failed': ('成果归档待处理', '任务产物已保留，但项目归档未完成。请在工作台检查归档状态。'),
        }
        title, text = descriptions.get(status, ('任务状态需要检查', '请在工作台检查当前任务，再回来核验。'))
        key = 'workbench-' + self.platform_run_id
        # A retry that sees its own existing open card must not generate another one.
        marker = '工作台任务 ' + self.platform_run_id
        existing = [c for c in context.get('open', []) if marker in str(c.get('text', ''))]
        if existing:
            return '待你处理：' + title + '；已有待办卡片，任务尚未完成。'
        return receipt('待你处理：' + title + '；任务尚未完成。', ask=[{
            'title': title, 'text': text + '\n' + marker,
            'options': ['已在工作台处理，重新核验', '仍需在工作台处理'], 'recommend': 0,
            'key': key, 'about': 'workbench-run:' + self.platform_run_id,
        }])

    def completed(self, run, context):
        paths = [a.get('path') for a in run.get('artifacts', []) if isinstance(a, dict) and isinstance(a.get('path'), str)]
        safe_paths = [p.replace('\n', ' ')[:160] for p in paths[:8]]
        first = '工作台任务已完成并通过独立审查；任务 ' + run['id'] + '，产物 %d 项。' % len(paths)
        if safe_paths:
            first += '\n产物：' + '、'.join(safe_paths)
        marker = '工作台任务 ' + self.platform_run_id
        resolved = [{'card': c['card'], 'reason': '已核验同一工作台任务完成并保存产物'}
                    for c in context.get('open', []) if isinstance(c, dict) and isinstance(c.get('card'), int)
                    and marker in str(c.get('text', ''))]
        return receipt(first, **({'resolve': resolved} if resolved else {}))

    def run(self, context):
        if not isinstance(context, dict):
            raise BridgeError('官方上下文必须是 JSON 对象')
        events, answers = context.get('events', []), context.get('answers', [])
        if not isinstance(events, list) or not isinstance(answers, list):
            raise BridgeError('官方事件或回答格式无效')
        self.cancel_if_requested()
        if answers:
            if any(not isinstance(e, dict) or e.get('type') != 'card.answered' for e in events):
                raise BridgeError('回答与新任务发生合并，拒绝遗漏事件；请分开提交')
            run = self.resume_answer(dict(context, answers=answers))
        else:
            if len(events) != 1:
                raise BridgeError('每轮必须只有一个业务事件；合并事件未执行，请使用独立 subject')
            self.record = self.validate_event(events[0])
            if self.mapping_file.exists():
                mapping = read_json(self.mapping_file)
                if (mapping.get('event_id') != self.record['event_id'] or
                        mapping.get('project_key') != self.project_key or
                        not RUN_ID.fullmatch(str(mapping.get('platform_run_id', '')))):
                    raise BridgeError('官方运行编号已有另一份映射')
                self.platform_run_id = mapping['platform_run_id']
                run = self.validate_run(self.client('GET', '/api/runs/' + self.platform_run_id))
            elif self.record['action'] == 'attach_existing':
                ident = self.record.get('platform_run_id')
                if not isinstance(ident, str) or not RUN_ID.fullmatch(ident):
                    raise BridgeError('既有任务编号无效')
                run = self.validate_run(self.client('GET', '/api/runs/' + ident))
                if run.get('title') != self.record['title']:
                    raise BridgeError('既有任务标题与核验请求不匹配')
            else:
                payload = {k: self.record[k] for k in ('title', 'brief', 'project_key', 'idempotency_key')}
                payload.update(executor_id='codex', role_executors=dict(ROLES))
                run = self.validate_run(self.client('POST', '/api/runs', payload))
        while True:
            self.save_mapping(run.get('status', 'unknown'))
            self.cancel_if_requested()
            status = run.get('status')
            if status == 'completed':
                if run.get('archive', {}).get('status') == 'failed':
                    self.save_mapping('archive_failed')
                    return self.ask(dict(run, status='archive_failed'), context)
                return self.completed(run, context)
            if status in WAITING:
                return self.ask(run, context)
            if status == 'cancelled':
                return 'NOTHING: 工作台任务已取消，未计为完成；任务 ' + run['id']
            if status not in ACTIVE:
                raise BridgeError('工作台返回未知状态；保留原任务等待核验')
            self.stop_event.wait(self.poll_interval)
            self.cancel_if_requested()
            run = self.validate_run(self.client('GET', '/api/runs/' + self.platform_run_id))


def reconcile_cancel_pending(data=DATA, client=None):
    """Retry only a recorded cancellation after the local service returns."""
    client = client or PlatformClient()
    counts = {'cancelled': 0, 'already_terminal': 0, 'skipped': 0, 'pending': 0}
    terminal = {'completed', 'cancelled', 'failed', 'review_failed', 'interrupted'}
    for path in (Path(data) / 'bridge').glob('[0-9]*.json'):
        try:
            mapping = read_json(path)
            if mapping.get('status') != 'cancel_pending':
                continue
            bridge = Bridge(mapping.get('project_key'), path.stem, data=data, client=client)
            bridge.record = bridge.load_request(mapping.get('event_id'))
            ident = mapping.get('platform_run_id')
            if not isinstance(ident, str) or not RUN_ID.fullmatch(ident):
                raise BridgeError('取消映射无效')
            bridge.platform_run_id = ident
            run = bridge.validate_run(client('GET', '/api/runs/' + ident))
            if run.get('status') in terminal:
                bridge.save_mapping(run['status'])
                counts['already_terminal'] += 1
            else:
                result = bridge.validate_run(client('POST', '/api/runs/' + ident + '/cancel', {}))
                if result.get('status') != 'cancelled':
                    counts['pending'] += 1
                    continue
                bridge.save_mapping(result['status'])
                counts['cancelled'] += 1
        except NetworkError:
            counts['pending'] += 1
        except (BridgeError, OSError, TypeError, ValueError):
            counts['skipped'] += 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--context', required=True)
    args = parser.parse_args(argv)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        bridge = Bridge(args.project, os.environ.get('AGENT_AUTOLOOP_RUN', ''), stop_event=stop)
        print(bridge.run(read_json(args.context)), flush=True)
        return 0
    except BridgeCancelled as exc:
        print('NOTHING: ' + str(exc), flush=True)
        return 0
    except BridgeError as exc:
        print('FAILED: ' + str(exc), flush=True)
        return 1
    except Exception:
        # Never print a traceback containing user context or arbitrary HTTP data.
        print('FAILED: 本机事件桥接异常；保留原任务和映射，请检查本机配置', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
