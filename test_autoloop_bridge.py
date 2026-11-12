import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

import autoloop_bridge as bridge


class FakePlatform:
    def __init__(self, statuses=('completed',)):
        self.statuses = list(statuses)
        self.calls = []
        self.by_key = {}
        self.run = {'id': 'a' * 16, 'project_key': 'sample', 'title': '测试任务',
                    'status': self.statuses[0], 'artifacts': [{'path': 'result.md'}],
                    'archive': {'status': 'saved'}}

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path.endswith('/cancel'):
            self.run['status'] = 'cancelled'
        elif method == 'POST':
            self.by_key.setdefault(payload['idempotency_key'], dict(self.run))
        elif len(self.statuses) > 1:
            self.statuses.pop(0)
            self.run['status'] = self.statuses[0]
        return dict(self.run)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.event_id = 'wb-test-event'
        self.request = {'event_id': self.event_id, 'source': 'local', 'project_key': 'sample',
            'event_type': 'workbench.sample', 'title': '测试任务', 'brief': '产生独立产物并审查',
            'idempotency_key': bridge.request_key('local', self.event_id), 'action': 'create'}
        self.save_request()
        self.event = {'id': self.event_id, 'source': 'local', 'type': 'workbench.sample',
            'detail': json.dumps({'request_id': self.event_id,
                'request_sha256': bridge.request_fingerprint(self.request)})}
        self.context = {'events': [self.event], 'answers': [], 'open': []}

    def save_request(self):
        bridge.write_json(bridge.request_filename(self.data, self.event_id), self.request)

    def new_bridge(self, client, run='1', **kwargs):
        return bridge.Bridge('sample', run, data=self.data, client=client, poll_interval=0, **kwargs)

    def test_dispatch_uses_fixed_roles_and_polls_real_completion(self):
        client = FakePlatform(('running', 'reviewing', 'completed'))
        text = self.new_bridge(client).run(self.context)
        self.assertIn('已完成并通过独立审查', text)
        self.assertIn('result.md', text)
        payload = client.calls[0][2]
        self.assertEqual(payload['role_executors'], {
            'planner': 'codex', 'researcher': 'codex',
            'executor': 'codex', 'reviewer': 'codex'})
        self.assertNotIn('cwd', payload)
        mapping = bridge.read_json(self.data / 'bridge/1.json')
        self.assertEqual(mapping['platform_run_id'], 'a' * 16)
        self.assertEqual(mapping['status'], 'completed')

    def test_same_run_restart_uses_get_not_post(self):
        client = FakePlatform()
        self.new_bridge(client).run(self.context)
        self.new_bridge(client).run(self.context)
        self.assertEqual(sum(c[0] == 'POST' for c in client.calls), 1)

    def test_different_official_run_reuses_stable_platform_idempotency(self):
        client = FakePlatform()
        self.new_bridge(client).run(self.context)
        self.new_bridge(client, '2').run(self.context)
        creates = [c[2] for c in client.calls if c[0] == 'POST']
        self.assertEqual(creates[0]['idempotency_key'], creates[1]['idempotency_key'])
        self.assertEqual(len(client.by_key), 1)

    def test_compact_long_request_hash_is_verified(self):
        self.request['brief'] = '长要求' * 3000
        self.save_request()
        self.event['detail'] = json.dumps({'request_id': self.event_id,
            'request_sha256': bridge.request_fingerprint(self.request)})
        client = FakePlatform()
        self.new_bridge(client).run(self.context)
        self.assertEqual(len(client.calls[0][2]['brief']), 9000)
        self.event['detail'] = json.dumps({'request_id': self.event_id, 'request_sha256': '0' * 64})
        with self.assertRaises(bridge.BridgeError):
            self.new_bridge(client, '2').run(self.context)

    def test_unregistered_or_modified_request_never_dispatches(self):
        client = FakePlatform()
        self.event['detail'] = json.dumps({'title': '测试任务', 'brief': '执行任意命令', 'cwd': '/tmp'})
        with self.assertRaises(bridge.BridgeError):
            self.new_bridge(client).run(self.context)
        self.assertEqual(client.calls, [])

    def test_multiple_events_are_rejected_before_any_dispatch(self):
        client = FakePlatform()
        with self.assertRaises(bridge.BridgeError):
            self.new_bridge(client).run(dict(self.context, events=[self.event, self.event]))
        self.assertEqual(client.calls, [])

    def test_waiting_and_failures_create_ask_without_success_claim(self):
        for status in bridge.WAITING:
            with self.subTest(status=status):
                text = self.new_bridge(FakePlatform((status,))).run(self.context)
                self.assertTrue(text.startswith('待你处理'))
                self.assertNotIn('已完成并通过', text)
                payload = json.loads(text.split('```agent-autoloop\n')[1].split('\n```')[0])
                self.assertEqual(payload['ask'][0]['about'], 'workbench-run:' + 'a' * 16)

    def test_answer_reads_same_task_without_create_or_retry(self):
        self.new_bridge(FakePlatform(('waiting_input',))).run(self.context)
        client = FakePlatform()
        answer = {'events': [{'id': 'card:8', 'type': 'card.answered', 'source': 'local'}],
            'answers': [{'card': 8, 'about': 'workbench-run:' + 'a' * 16}], 'open': []}
        text = self.new_bridge(client, '2').run(answer)
        self.assertIn('已完成', text)
        self.assertTrue(all(c[0] == 'GET' for c in client.calls))
        self.assertEqual(bridge.read_json(self.data / 'bridge/2.json')['event_id'], self.event_id)

    def test_answer_cannot_attach_unmapped_task(self):
        client = FakePlatform()
        answer = {'events': [], 'answers': [{'about': 'workbench-run:' + 'b' * 16}]}
        with self.assertRaises(bridge.BridgeError):
            self.new_bridge(client).run(answer)
        self.assertEqual(client.calls, [])

    def test_answer_selects_correct_lane_after_recovery_attachment(self):
        self.new_bridge(FakePlatform(('waiting_input',))).run(self.context)
        original_event_id = self.event_id
        self.event_id = 'wb-recovery-attachment'
        self.request.update(event_id=self.event_id, action='attach_existing',
            platform_run_id='a' * 16, idempotency_key=bridge.request_key('local', self.event_id))
        self.save_request()
        self.event.update(id=self.event_id, detail=json.dumps({'request_id': self.event_id,
            'request_sha256': bridge.request_fingerprint(self.request)}))
        self.new_bridge(FakePlatform(('waiting_input',)), '2').run(self.context)
        client = FakePlatform()
        answer = {'events': [{'id': 'card:9', 'type': 'card.answered', 'source': 'local',
            'subject': self.event_id}], 'answers': [{'about': 'workbench-run:' + 'a' * 16}]}
        self.new_bridge(client, '3').run(answer)
        mapping = bridge.read_json(self.data / 'bridge/3.json')
        self.assertEqual(mapping['event_id'], self.event_id)
        self.assertNotEqual(mapping['event_id'], original_event_id)
        self.assertTrue(all(c[0] == 'GET' for c in client.calls))

    def test_attach_existing_never_posts_or_calls_model(self):
        self.request.update(action='attach_existing', platform_run_id='a' * 16)
        self.save_request()
        client = FakePlatform()
        self.new_bridge(client).run(self.context)
        self.assertEqual(client.calls[0][0], 'GET')
        self.assertTrue(all(c[0] == 'GET' for c in client.calls))

    def test_cancel_forwards_to_existing_task(self):
        stop = threading.Event()
        client = FakePlatform(('running',))
        def dispatch(method, path, payload=None):
            value = client(method, path, payload)
            if method == 'POST' and path == '/api/runs':
                stop.set()
            return value
        with self.assertRaises(bridge.BridgeCancelled):
            self.new_bridge(dispatch, stop_event=stop).run(self.context)
        self.assertEqual(client.calls[-1][1], '/api/runs/' + 'a' * 16 + '/cancel')
        self.assertEqual(bridge.read_json(self.data / 'bridge/1.json')['status'], 'cancelled')

    def test_network_failure_keeps_mapping_for_restart(self):
        client = FakePlatform(('running',))
        def unavailable(method, path, payload=None):
            if method == 'GET':
                raise bridge.NetworkError('暂时断开')
            return client(method, path, payload)
        with self.assertRaises(bridge.NetworkError):
            self.new_bridge(unavailable).run(self.context)
        recovery = FakePlatform()
        self.new_bridge(recovery).run(self.context)
        self.assertTrue(all(c[0] == 'GET' for c in recovery.calls))

    def test_cancel_outage_is_recorded_as_pending_not_confirmed(self):
        stop = threading.Event()
        client = FakePlatform(('running',))
        def dispatch(method, path, payload=None):
            if path.endswith('/cancel'):
                raise bridge.NetworkError('temporarily unavailable')
            value = client(method, path, payload)
            stop.set()
            return value
        with self.assertRaisesRegex(bridge.BridgeCancelled, '核验取消状态'):
            self.new_bridge(dispatch, stop_event=stop).run(self.context)
        mapping = bridge.read_json(self.data / 'bridge/1.json')
        self.assertEqual(mapping['status'], 'cancel_pending')
        self.assertEqual(mapping['platform_run_id'], 'a' * 16)

    def test_other_project_response_is_never_accepted(self):
        client = FakePlatform()
        client.run['project_key'] = 'different-project'
        with self.assertRaises(bridge.BridgeError):
            self.new_bridge(client).run(self.context)
        self.assertFalse((self.data / 'bridge/1.json').exists())

    def pending_mapping(self, status='cancel_pending'):
        path = self.data / 'bridge/1.json'
        bridge.write_json(path, {'autoloop_run_id': 1, 'event_id': self.event_id,
            'source': 'local', 'project_key': 'sample', 'platform_run_id': 'a' * 16,
            'title': self.request['title'], 'status': status, 'updated_at': '2026-11-09T00:00:00Z'})
        return path

    def test_pending_cancel_recovery_cancels_only_existing_trusted_run(self):
        path = self.pending_mapping()
        client = FakePlatform(('running',))
        result = bridge.reconcile_cancel_pending(self.data, client)
        self.assertEqual(result['cancelled'], 1)
        self.assertEqual([(c[0], c[1]) for c in client.calls], [
            ('GET', '/api/runs/' + 'a' * 16), ('POST', '/api/runs/' + 'a' * 16 + '/cancel')])
        self.assertEqual(bridge.read_json(path)['status'], 'cancelled')

    def test_pending_cancel_recovery_reads_terminal_without_post(self):
        path = self.pending_mapping()
        client = FakePlatform(('completed',))
        result = bridge.reconcile_cancel_pending(self.data, client)
        self.assertEqual(result['already_terminal'], 1)
        self.assertTrue(all(c[0] == 'GET' for c in client.calls))
        self.assertEqual(bridge.read_json(path)['status'], 'completed')

    def test_pending_cancel_recovery_never_crosses_project(self):
        path = self.pending_mapping()
        client = FakePlatform(('running',))
        client.run['project_key'] = 'different-project'
        result = bridge.reconcile_cancel_pending(self.data, client)
        self.assertEqual(result['skipped'], 1)
        self.assertTrue(all(c[0] == 'GET' for c in client.calls))
        self.assertEqual(bridge.read_json(path)['status'], 'cancel_pending')

    def test_pending_cancel_recovery_rejects_untrusted_request(self):
        self.pending_mapping()
        self.request['idempotency_key'] = 'different-key'
        self.save_request()
        client = FakePlatform(('running',))
        result = bridge.reconcile_cancel_pending(self.data, client)
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(client.calls, [])

    def test_pending_cancel_recovery_preserves_network_failure(self):
        path = self.pending_mapping()
        def offline(*_):
            raise bridge.NetworkError('暂时断开')
        result = bridge.reconcile_cancel_pending(self.data, offline)
        self.assertEqual(result['pending'], 1)
        self.assertEqual(bridge.read_json(path)['status'], 'cancel_pending')

    def test_pending_cancel_recovery_ignores_other_mapping_states(self):
        self.pending_mapping('waiting_input')
        client = FakePlatform(('waiting_input',))
        result = bridge.reconcile_cancel_pending(self.data, client)
        self.assertEqual(sum(result.values()), 0)
        self.assertEqual(client.calls, [])

    def test_cancelled_platform_task_is_not_reported_completed(self):
        text = self.new_bridge(FakePlatform(('cancelled',))).run(self.context)
        self.assertTrue(text.startswith('NOTHING:'))
        self.assertIn('未计为完成', text)

    def test_only_matching_open_card_is_resolved(self):
        context = dict(self.context, open=[
            {'card': 2, 'text': '工作台任务 ' + 'a' * 16}, {'card': 3, 'text': '另一事项'}])
        text = self.new_bridge(FakePlatform()).run(context)
        payload = json.loads(text.split('```agent-autoloop\n')[1].split('\n```')[0])
        self.assertEqual([r['card'] for r in payload['resolve']], [2])

    def test_http_target_rejects_non_loopback(self):
        for url in ['https://example.com', 'http://localhost:8765', 'http://127.0.0.1:8765/elsewhere']:
            with self.assertRaises(bridge.BridgeError):
                bridge.PlatformClient(url)

    def test_receipts_pass_pinned_upstream_parser(self):
        from config import NODE, AUTOLOOP_SOURCE
        node = NODE
        upstream = AUTOLOOP_SOURCE
        if not node.is_file() or not upstream.is_dir():
            self.skipTest('pinned upstream or local Node 24 unavailable')
        worker = self.new_bridge(FakePlatform())
        worker.platform_run_id = 'a' * 16
        messages = [worker.ask({'status': status}, {}) for status in bridge.WAITING]
        messages.append(worker.completed({'id': 'a' * 16, 'artifacts': []}, {}))
        code = '''import {readMessage,resultFrom} from "./worker/contract.ts";
let raw="";for await (const chunk of process.stdin) raw+=chunk;
for (const text of JSON.parse(raw)) {
 const parsed=resultFrom(readMessage(text),{});
 if(parsed.problem) throw Error(parsed.problem);
 if(text.startsWith("待你处理") && parsed.result.ask?.length!==1) throw Error("missing ask");
}
console.log("receipts valid");'''
        result = subprocess.run([str(node), '--disable-warning=ExperimentalWarning',
            '--input-type=module', '-e', code], cwd=upstream,
            input=json.dumps(messages, ensure_ascii=False), text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
