"""Protect the local event proxy's fixed routing, idempotence and secret boundary.

All config, request records and mappings live under TemporaryDirectory. Upstream
calls and process actions are mocked; the real Autoloop instance is untouched.
"""
from contextlib import ExitStack
import copy
import hashlib
import io
import json
from pathlib import Path
import signal
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import autoloop_service as service
from server import serve
from test_server import FakeRunner, TestBench


class AutoloopServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='autoloop-service-test-')
        self.root = Path(self.temporary.name)
        self.data = self.root / 'data/autoloop'
        self.source = self.root / 'tools/agent-autoloop'
        self.entry = self.source / 'bin/agent-autoloop.ts'
        self.entry.parent.mkdir(parents=True)
        self.entry.write_text('// never executed\n')
        self.node = self.root / 'fake-node'
        self.node.write_text('never executed\n')
        self.index = self.root / 'projects.json'
        self.project = self.root / 'business-project'
        self.project.mkdir()
        self.index.write_text(json.dumps({'projects': [{'key': 'demo', 'name': '合成项目', 'root': str(self.project), 'summary': '合成测试'}]}))
        self.stack = ExitStack()
        for name, value in [('ROOT', self.root), ('DATA', self.data), ('SOURCE', self.source),
                            ('ENTRY', self.entry), ('NODE', self.node), ('INDEX', self.index)]:
            self.stack.enter_context(patch.object(service, name, value))
        self.addCleanup(self.stack.close)
        self.addCleanup(self.temporary.cleanup)
        self.config = {'owner': 'SYNTHETIC_OWNER_SECRET', 'source': {'name': 'local', 'key': 'SYNTHETIC_SOURCE_SECRET'},
                       'worker': {'name': 'local-worker', 'key': 'SYNTHETIC_WORKER_SECRET'},
                       'hub': {'listen': '127.0.0.1:3350'}, 'channels': {}}
        service.write_json(self.data / 'rules.json', service.generate_rules('local-worker'))

    def payload(self, **changes):
        result = {'rule_name': service.rule_name('demo'), 'title': '合成事件', 'brief': '生成一份可检查的成果', 'idempotency_key': 'synthetic-event-1234'}
        result.update(changes)
        return result

    def transport(self, callback):
        """Cover both the old direct opener and the hardened no-proxy opener."""
        context = ExitStack()
        context.enter_context(patch.object(service.urllib.request, 'urlopen', side_effect=callback))
        if hasattr(service, 'OPENER'):
            context.enter_context(patch.object(service.OPENER, 'open', side_effect=callback))
        return context

    def test_long_brief_is_local_and_upstream_receives_only_hash_pointer(self):
        brief = '仅本机保存的长任务说明。' * 800
        self.assertLessEqual(len(brief), 12000)
        with patch.object(service, 'request', return_value={'event': 42}) as upstream:
            result = service.emit(self.payload(brief=brief, executor_id='untrusted-provider', cwd='/untrusted'))
        path, forwarded = upstream.call_args.args
        self.assertEqual(path, '/v1/events')
        self.assertTrue(upstream.call_args.kwargs['source'])
        detail = json.loads(forwarded['detail'])
        self.assertEqual(set(detail), {'request_id', 'request_sha256'})
        self.assertNotIn(brief, json.dumps(forwarded, ensure_ascii=False))
        self.assertNotIn('executor_id', json.dumps(forwarded))
        files = list((self.data / 'requests').glob('*.json'))
        self.assertEqual(len(files), 1)
        record = json.loads(files[0].read_text())
        self.assertEqual(record['brief'], brief)
        self.assertEqual(record['project_key'], 'demo')
        self.assertEqual(record['event_type'], 'workbench.demo')
        self.assertEqual(detail['request_id'], result['event_id'])
        expected = hashlib.sha256(json.dumps({k: record[k] for k in ('project_key', 'title', 'brief')},
            ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(detail['request_sha256'], expected)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)

    def test_event_replay_keeps_original_content_and_rejects_key_reuse(self):
        payload = self.payload()
        with patch.object(service, 'request', return_value={'event': 42}) as upstream:
            first = service.emit(payload)
            file = next((self.data / 'requests').glob('*.json'))
            original = file.read_bytes()
            replay = service.emit(copy.deepcopy(payload))
            self.assertEqual(first, replay)
            self.assertEqual(file.read_bytes(), original)
            self.assertEqual(upstream.call_args_list[0].args[1], upstream.call_args_list[1].args[1])
            with self.assertRaisesRegex(ValueError, '另一份事件内容'):
                service.emit(self.payload(brief='不同的任务内容'))
            self.assertEqual(upstream.call_count, 2)
            self.assertEqual(file.read_bytes(), original)

    def test_initialize_disables_notifications_before_native_init_and_binds_rules(self):
        observed = []
        def native_init(command, **kwargs):
            before = json.loads((self.data / 'rules.json').read_text())
            observed.append(before)
            self.assertEqual(before['channels'], {})
            self.assertIn('--no-service', command)
            self.assertIn('--port', command)
            self.assertEqual(command[command.index('--port') + 1], '3350')
            service.write_json(self.data / 'config.json', dict(self.config, channels={'desktop': {'key': 'SYNTHETIC_DISABLED_CHANNEL'}}))
            return types.SimpleNamespace(returncode=0)
        with patch.object(service.subprocess, 'check_output', return_value=service.COMMIT + '\n'), \
             patch.object(service.subprocess, 'run', side_effect=native_init):
            service.initialize()
        self.assertEqual(len(observed), 1)
        config = json.loads((self.data / 'config.json').read_text())
        rules = json.loads((self.data / 'rules.json').read_text())
        self.assertEqual(config['channels'], {})
        self.assertEqual(rules['channels'], {})
        self.assertEqual(rules['elsewhere'], [])
        self.assertEqual(config['worker']['capacity'], 2)
        rule = rules['rules'][0]
        self.assertEqual(rule['when'], {'source': 'local', 'types': ['workbench.demo']})
        self.assertEqual(rule['cwd'], str(self.root))
        self.assertEqual(rule['group'], 'demo')
        self.assertEqual(rule['tell'], 'never')
        agent = rules['agents'][rule['to']]
        self.assertEqual(agent['worker'], 'local-worker')
        self.assertEqual(agent['access'], 'read')
        self.assertEqual(agent['command'][1:], [str(self.root / 'autoloop_bridge.py'), '--project', 'demo', '--context', '{context}'])
        installation = json.loads((self.data / 'installation.json').read_text())
        self.assertEqual(installation['external_channels'], 0)
        self.assertEqual(installation['schedules'], 0)

    def test_owner_and_source_secrets_stay_in_server_headers(self):
        service.write_json(self.data / 'config.json', self.config)
        calls = []
        def upstream(request, timeout=3):
            calls.append(request)
            return io.BytesIO(b'{"ok":true}')
        with self.transport(upstream):
            self.assertEqual(service.request('/v1/state'), {'ok': True})
            self.assertEqual(service.request('/v1/events', {'id': 'safe-event'}, source=True), {'ok': True})
        self.assertEqual(calls[0].get_header('Authorization'), 'Bearer SYNTHETIC_OWNER_SECRET')
        self.assertEqual(calls[1].get_header('Authorization'), 'Bearer SYNTHETIC_SOURCE_SECRET')
        self.assertTrue(all(call.full_url.startswith('http://127.0.0.1:3350/') for call in calls))
        self.assertNotIn('SECRET', (calls[1].data or b'').decode())

    def test_public_state_excludes_credentials_and_maps_business_status(self):
        service.write_json(self.data / 'config.json', self.config)
        ident = '0123456789abcdef'
        service.write_json(self.data / 'bridge/1.json', {'platform_run_id': ident, 'title': '真实业务任务', 'status': 'queued', 'owner': 'SYNTHETIC_MAPPING_SECRET'})
        upstream = {'owner': self.config['owner'], 'config': self.config,
                    'agents': [{'online': True, 'key': 'SYNTHETIC_WORKER_SECRET'}],
                    'lines': [{'name': service.rule_name('demo'), 'paused': True, 'sourceKey': 'SYNTHETIC_SOURCE_SECRET'}],
                    'totals': {'running': 0, 'queued': 0, 'attention': 1, 'done': 2, 'owner': 'SYNTHETIC_OWNER_SECRET'},
                    'runs': [{'id': 1, 'state': 'done', 'rule': service.rule_name('demo'), 'summary': '事件轮次完成', 'secret': 'SYNTHETIC_RUN_SECRET'},
                             {'id': 2, 'state': 'done', 'summary': '仅事件轮次', 'secret': 'SYNTHETIC_RUN_SECRET'}],
                    'cards': [{'id': 7, 'kind': 'ask', 'about': 'workbench-run:' + ident, 'title': '需要回填', 'text': '安全卡片文本', 'options': ['重新核验'], 'owner': 'SYNTHETIC_CARD_SECRET'},
                              {'id': 8, 'kind': 'notice', 'about': 'unrelated', 'title': '本机协议通知', 'text': '需要本人核对的安全通知文本', 'owner': 'SYNTHETIC_NOTICE_SECRET'}]}
        def platform(request, timeout=3):
            return io.BytesIO(json.dumps({'status': 'waiting_input', 'title': '实际等待回填'}).encode())
        with patch.object(service, 'request', return_value=upstream), self.transport(platform):
            result = service.state()
        encoded = json.dumps(result, ensure_ascii=False)
        self.assertNotIn('SYNTHETIC_', encoded)
        self.assertTrue(result['running'])
        self.assertTrue(result['rules'][0]['paused'])
        self.assertEqual(result['runs'][0]['status'], 'waiting_input')
        self.assertEqual(result['runs'][0]['platform_run_id'], ident)
        self.assertEqual(result['runs'][1]['status'], '已结束事件轮次')
        self.assertEqual(len(result['cards']), 2)
        self.assertEqual(result['cards'][1]['kind'], 'notice')
        self.assertIsNone(result['cards'][1]['platform_url'])
        self.assertNotIn('owner', result['cards'][0])

    def test_new_project_without_installed_rule_is_not_advertised_or_dispatched(self):
        new_root = self.root / 'new-project'
        new_root.mkdir()
        value = json.loads(self.index.read_text())
        value['projects'].append({'key': 'new', 'name': '尚未同步的项目', 'root': str(new_root)})
        self.index.write_text(json.dumps(value))
        with patch.object(service, 'request', return_value={'agents': [], 'lines': [], 'runs': [], 'cards': []}) as upstream:
            snapshot = service.state()
            self.assertEqual([r['project_key'] for r in snapshot['rules']], ['demo'])
            with self.assertRaisesRegex(ValueError, '尚未同步'):
                service.emit(self.payload(rule_name=service.rule_name('new')))
        self.assertEqual(upstream.call_count, 1)
        self.assertFalse((self.data / 'requests').exists())

    def test_notice_resolves_while_question_answers_without_business_retry(self):
        cards = [{'id': 7, 'kind': 'notice'}, {'id': 8, 'kind': 'ask'}]
        with patch.object(service, 'state', return_value={'cards': cards}), \
             patch.object(service, 'request', return_value={'changed': True}) as upstream:
            service.answer(7)
            service.answer(8)
            self.assertEqual(upstream.call_args_list[0].args[0], '/v1/cards/7/resolve')
            self.assertTrue(upstream.call_args_list[0].args[1]['reason'])
            self.assertEqual(upstream.call_args_list[1].args, ('/v1/cards/8/answer', {'choice': 0}))
            with self.assertRaisesRegex(ValueError, '不存在'):
                service.answer(999)
        self.assertEqual(upstream.call_count, 2)

    def test_stop_refuses_reused_process_identity(self):
        record = {'pid': 12345, 'started': 'Mon Nov 9 10:00:00 2026'}
        service.write_json(self.data / 'process.json', record)
        with patch.object(service.subprocess, 'check_output', side_effect=[str(self.node) + ' ' + str(self.entry) + ' up', 'Mon Nov 9 11:00:00 2026']), \
             patch.object(service.os, 'kill') as kill:
            with self.assertRaisesRegex(RuntimeError, '身份已变化'):
                service.stop()
        kill.assert_not_called()
        self.assertEqual(json.loads((self.data / 'process.json').read_text()), record)

    def test_autoloop_http_routes_keep_same_origin_boundary(self):
        app = TestBench(self.root, FakeRunner(), self.index)
        http = serve(app, 0)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(http.server_port)
        def call(path, payload=None, headers=None):
            headers = dict(headers or {})
            data = None
            if payload is not None:
                headers['Content-Type'] = 'application/json'
                data = json.dumps(payload).encode()
            return urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers), timeout=3)
        try:
            with patch.object(service, 'state', return_value={'running': True}), \
                 patch.object(service, 'start', return_value={'running': True}) as start, \
                 patch.object(service, 'emit', return_value={'event_id': 'safe'}) as emit, \
                 patch.object(service, 'answer', return_value={'changed': True}) as answer:
                self.assertEqual(json.load(call('/api/autoloop')), {'running': True})
                for headers in [{'Origin': 'https://unrelated.example'}, {'Host': 'unrelated.example'}, {'Sec-Fetch-Site': 'cross-site'}]:
                    for path in ['/api/autoloop/start', '/api/autoloop/events', '/api/autoloop/cards/7/answer']:
                        with self.assertRaises(urllib.error.HTTPError) as error:
                            call(path, {}, headers)
                        self.assertEqual(error.exception.code, 403)
                start.assert_not_called()
                emit.assert_not_called()
                answer.assert_not_called()
                self.assertEqual(call('/api/autoloop/events', self.payload(), {'Origin': base}).status, 201)
                emit.assert_called_once()
        finally:
            http.shutdown()
            http.server_close()
            app.shutdown()
            app.conn.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
