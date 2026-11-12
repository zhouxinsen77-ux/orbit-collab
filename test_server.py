"""Evidence-focused tests; fake execution is explicitly separate from real CLI smoke tests."""
import concurrent.futures
from datetime import datetime
import json
import os
import signal
import subprocess
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

from server import Workbench, WorkflowError, Cancelled, validate_plan, checked_path, serve, register_child, exportable_artifact, acquire_service_lock


def plan(nodes=None):
    return {'summary': '测试任务图', 'tasks': nodes or [
        {'id': 'research', 'title': '资料研究', 'role': 'researcher', 'instructions': '生成资料笔记', 'depends_on': []},
        {'id': 'design', 'title': '设计', 'role': 'executor', 'instructions': '生成设计稿', 'depends_on': []},
        {'id': 'combine', 'title': '整合', 'role': 'executor', 'instructions': '整合前置成果', 'depends_on': ['research', 'design']},
    ]}


class FakeRunner:
    binary = '/does-not-exist'
    def __init__(self, fail_role=None, slow=False, revise=False):
        self.fail_role, self.slow, self.revise = fail_role, slow, revise
        self.calls = []
        self.active, self.max_active = 0, 0
        self.lock = threading.Lock()
    def run(self, role, prompt, schema, workdir, cancel_event, timeout, executor_id='codex'):
        with self.lock:
            self.calls.append((role, str(workdir), executor_id))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if role == 'planner':
                return plan()
            if role == 'reviewer':
                return {'verdict': 'revise' if self.revise else 'pass', 'summary': '已审查测试产物', 'findings': ['需补材料'] if self.revise else []}
            for _ in range(100 if self.slow else 3):
                if cancel_event.wait(.02):
                    raise Cancelled('测试取消')
            if role == self.fail_role:
                raise WorkflowError('测试节点失败')
            Path(workdir).mkdir(parents=True, exist_ok=True)
            return {'summary': '测试成果', 'deliverable_markdown': '# 实际保存的测试成果\n\n这是可核对的测试内容。', 'artifact_paths': [], 'checks': [{'name': '内容检查', 'passed': True, 'evidence': 'fake runner；非真实模型'}]}
        finally:
            with self.lock:
                self.active -= 1


class TestBench(Workbench):
    def providers(self):
        return [{'id': 'codex', 'name': '测试执行器', 'available': True, 'authenticated': True, 'kind': 'cli'},
                {'id': 'manual', 'name': '人工客户端', 'available': True, 'authenticated': True, 'kind': 'manual'},
                {'id': 'second', 'name': '第二执行器', 'available': True, 'authenticated': True, 'kind': 'api'}]
    def health(self):
        return {'codex_available': True, 'authenticated': True, 'version': 'test', 'active_count': 0, 'max_workers': self.max_workers}


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = self.root / 'project'
        self.project.mkdir()
        (self.project / 'PROJECT.md').write_text('# 原项目\n只读内容', encoding='utf-8')
        self.index = self.root / 'index.json'
        self.index.write_text(json.dumps({'projects': [{'key': 'demo', 'name': '测试项目', 'root': str(self.project), 'summary': '测试'}]}))
        self.apps = []
    def tearDown(self):
        for app in self.apps:
            app.shutdown()
            app.conn.close()
        self.tmp.cleanup()
    def app(self, runner=None, **kw):
        app = TestBench(self.root, runner or FakeRunner(), self.index, **kw)
        self.apps.append(app)
        return app
    def create(self, app, **overrides):
        payload = {'title': '测试任务', 'brief': '保存测试产物并独立审查', 'project_key': 'demo'}
        payload.update(overrides)
        return app.create(payload)
    def wait(self, app, ident, predicate=lambda r: r['status'] in {'completed', 'failed', 'cancelled', 'review_failed'}, timeout=6):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = app.get(ident)
            if predicate(run):
                return run
            time.sleep(.02)
        self.fail('等待超时：' + json.dumps(app.get(ident), ensure_ascii=False))

    def test_dag_runs_independent_nodes_in_parallel_and_persists_outputs(self):
        runner = FakeRunner()
        app = self.app(runner)
        run = self.wait(app, self.create(app)['id'])
        self.assertEqual(run['status'], 'completed')
        self.assertGreaterEqual(runner.max_active, 2)
        self.assertEqual([Path(call[1]).parts[-2] for call in runner.calls if call[0] not in {'planner', 'reviewer'}][-1], 'combine')
        self.assertTrue(all(c['passed'] for c in run['checks']))
        self.assertTrue(any(a['name'] == 'deliverable.md' for a in run['artifacts']))
        for artifact in run['artifacts']:
            import hashlib
            data = (app.runs_dir / run['id'] / artifact['path']).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), artifact['sha256'])
        self.assertEqual((self.project / 'PROJECT.md').read_text(), '# 原项目\n只读内容')

    def test_failure_does_not_release_dependent_or_reviewer(self):
        runner = FakeRunner(fail_role='researcher')
        app = self.app(runner)
        run = self.wait(app, self.create(app)['id'])
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(next(t for t in run['tasks'] if t['id'] == 'combine')['status'], 'pending')
        self.assertFalse(any(c[0] == 'reviewer' for c in runner.calls))
        self.assertFalse(any('/combine/' in c[1] for c in runner.calls))

    def test_cancel_stops_running_workers(self):
        runner = FakeRunner(slow=True)
        app = self.app(runner)
        ident = self.create(app)['id']
        self.wait(app, ident, lambda r: any(t['status'] == 'running' for t in r['tasks']))
        app.action(ident, 'cancel')
        app.threads[ident].join(timeout=3)
        self.assertFalse(app.threads[ident].is_alive())
        self.assertEqual(app.get(ident)['status'], 'cancelled')
        self.assertFalse(any(c[0] == 'reviewer' for c in runner.calls))

    def test_pause_preserves_current_work_and_prevents_new_downstream(self):
        class GatedRunner(FakeRunner):
            def __init__(self):
                super().__init__()
                self.entered = {node: threading.Event() for node in ('research', 'design', 'combine')}
                self.release = threading.Event()

            def run(self, role, prompt, schema, workdir, cancel_event, timeout, executor_id='codex'):
                if role not in {'planner', 'reviewer'}:
                    node_id = Path(workdir).parts[-2]
                    self.entered[node_id].set()
                    if node_id in {'research', 'design'}:
                        while not self.release.wait(.02):
                            if cancel_event.is_set():
                                raise Cancelled('测试取消')
                return super().run(role, prompt, schema, workdir, cancel_event, timeout, executor_id)

        class PauseObservedBench(TestBench):
            def __init__(self, *args, **kwargs):
                self.scheduler_paused = threading.Event()
                self.scheduler_returned = threading.Event()
                super().__init__(*args, **kwargs)

            def _wait_ready(self, ident):
                observes_pause = (threading.current_thread().name == 'run-' + ident
                                  and self.get(ident)['status'] == 'paused')
                if observes_pause:
                    self.scheduler_paused.set()
                try:
                    return super()._wait_ready(ident)
                finally:
                    if observes_pause:
                        self.scheduler_returned.set()

        runner = GatedRunner()
        app = PauseObservedBench(self.root, runner, self.index)
        self.apps.append(app)
        ident = self.create(app)['id']
        # A node is marked running before _call's pause check. Wait for actual
        # executor entry, then keep both in flight until the pause is observed.
        self.assertTrue(runner.entered['research'].wait(4), 'research executor did not enter')
        self.assertTrue(runner.entered['design'].wait(4), 'design executor did not enter')
        app.action(ident, 'pause')
        self.assertTrue(app.scheduler_paused.wait(4), 'scheduler did not reach the pause barrier')
        runner.release.set()
        self.wait(app, ident, lambda r: sum(t['status'] == 'completed' for t in r['tasks']) == 2, timeout=4)
        paused = app.get(ident)
        self.assertEqual(paused['status'], 'paused')
        self.assertFalse(app.scheduler_returned.is_set(), 'scheduler passed the pause barrier')
        self.assertEqual(next(t for t in paused['tasks'] if t['id'] == 'combine')['status'], 'pending')
        self.assertFalse(runner.entered['combine'].is_set())
        app.action(ident, 'resume')
        self.assertEqual(self.wait(app, ident)['status'], 'completed')
        self.assertTrue(app.scheduler_returned.is_set())
        self.assertTrue(runner.entered['combine'].is_set())

    def test_review_failure_not_marked_complete_and_retry_limited(self):
        runner = FakeRunner(revise=True)
        app = self.app(runner)
        ident = self.create(app)['id']
        run = self.wait(app, ident)
        self.assertEqual(run['status'], 'review_failed')
        app.threads[ident].join()
        for _ in range(2):
            app.action(ident, 'retry')
            self.wait(app, ident)
            app.threads[ident].join()
        with self.assertRaisesRegex(WorkflowError, '三次'):
            app.action(ident, 'retry')

    def test_restart_marks_work_interrupted_without_losing_plan(self):
        app = self.app()
        run = app.create({'title': '重启任务', 'brief': '保留进度', 'project_key': 'demo'}, start=False)
        app.mutate(run['id'], lambda r: r.update(status='running', tasks=[dict(plan()['tasks'][0], status='running', attempt=1)]))
        app.conn.close()
        self.apps.remove(app)
        new_app = self.app()
        recovered = new_app.get(run['id'])
        self.assertEqual(recovered['status'], 'interrupted')
        self.assertEqual(recovered['tasks'][0]['status'], 'interrupted')

    def test_dedupe_is_atomic(self):
        app = self.app()
        def create_once(_):
            return app.create({'title': '同一事件', 'brief': '幂等测试', 'project_key': 'demo', 'idempotency_key': 'event-1'}, start=False)['id']
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            ids = list(pool.map(create_once, range(10)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(app.all_runs()), 1)

    def test_plan_rejects_cycle_and_unknown_dependency(self):
        value = plan()
        value['tasks'][0]['depends_on'] = ['combine']
        with self.assertRaises(WorkflowError):
            validate_plan(value)
        value = plan()
        value['tasks'][0]['depends_on'] = ['missing']
        with self.assertRaises(WorkflowError):
            validate_plan(value)

    def test_path_rejects_traversal_and_symlink_escape(self):
        root = self.root / 'sandbox'
        root.mkdir()
        (root / 'escape').symlink_to(self.project)
        for relative in ('../index.json', '/etc/passwd', 'escape/PROJECT.md'):
            with self.assertRaises(WorkflowError):
                checked_path(root, relative)

    def test_role_routing_and_manual_handoff(self):
        runner = FakeRunner()
        app = self.app(runner)
        ident = self.create(app, role_executors={'planner': 'codex', 'researcher': 'manual', 'executor': 'second', 'reviewer': 'codex'})['id']
        run = self.wait(app, ident, lambda r: bool(r['handoffs']))
        handoff = run['handoffs'][0]
        self.assertEqual(handoff['provider_id'], 'manual')
        self.assertEqual(run['status'], 'waiting_input')
        app.submit_handoff(ident, {'task_id': 'research', 'content': '# 调研结果\n这是用户在客户端实际得到并粘贴的成果。'})
        run = self.wait(app, ident)
        self.assertEqual(run['status'], 'completed')
        self.assertTrue(any(call[2] == 'second' for call in runner.calls))
        self.assertEqual(run['handoffs'][0]['status'], 'submitted')
        with self.assertRaises(WorkflowError):
            app.submit_handoff(ident, {'task_id': 'research', 'content': '重复提交'})

    def test_plain_handoff_records_submission_without_inventing_model_origin(self):
        runner = FakeRunner()
        app = self.app(runner)
        ident = self.create(app, role_executors={'researcher': 'manual', 'executor': 'second'})['id']
        waiting = self.wait(app, ident, lambda r: bool(r['handoffs']))
        app.action(ident, 'pause')
        self.assertFalse(any(c['name'].startswith('人工交接记录') for c in app._deterministic_checks(waiting)))
        body = '# 合成回填记录\n这是通道测试正文，未调用任何客户端模型。'
        submitted = app.submit_handoff(ident, {'task_id': 'research', 'content': body})
        handoff = submitted['handoffs'][0]
        metadata = json.dumps({k: handoff['result'][k] for k in ('summary', 'checks')}, ensure_ascii=False)
        self.assertNotIn('客户端结果', metadata)
        self.assertNotIn('人工客户端返回', metadata)
        self.assertIn('不据此认定', metadata)
        self.assertEqual(handoff['result']['deliverable_markdown'], body)
        self.assertTrue(handoff.get('submitted_at'))
        self.assertFalse(handoff.get('consumed_at'))
        self.assertFalse(any(c['name'].startswith('人工交接记录') for c in app._deterministic_checks(submitted)))
        app.action(ident, 'resume')
        finished = self.wait(app, ident)
        self.assertEqual(finished['status'], 'completed')
        consumed = finished['handoffs'][0]
        timestamps = [datetime.fromisoformat(consumed[k]) for k in ('created_at', 'submitted_at', 'consumed_at')]
        self.assertTrue(all(t.tzinfo is not None for t in timestamps))
        self.assertLessEqual(timestamps[0], timestamps[1])
        self.assertLessEqual(timestamps[1], timestamps[2])
        evidence = next(c for c in finished['checks'] if c['name'] == '人工交接记录 / research')
        self.assertTrue(evidence['passed'])
        for key in ('created_at', 'submitted_at', 'consumed_at'):
            self.assertIn(consumed[key], evidence['evidence'])
        self.assertIn('平台没有调用 manual 模型', evidence['evidence'])
        self.assertIn('提交者声明', evidence['evidence'])
        self.assertFalse(any(call[2] == 'manual' for call in runner.calls))

    def test_dependency_evidence_matches_actual_node_count_and_executor_routing(self):
        app = self.app()
        run = self.wait(app, self.create(app, role_executors={'researcher': 'second', 'executor': 'codex'})['id'])
        evidence = next(c for c in run['checks'] if c['name'] == '任务图依赖')
        self.assertTrue(evidence['passed'])
        self.assertIn('本次共 %d 个节点' % len(run['tasks']), evidence['evidence'])
        recorded = json.loads(evidence['evidence'].split('节点及分工：', 1)[1])
        expected = [{k: node.get(k) for k in ('id', 'executor_id', 'status', 'depends_on')} for node in run['tasks']]
        self.assertEqual(recorded, expected)
        self.assertEqual(next(n for n in recorded if n['id'] == 'research')['executor_id'], 'second')
        self.assertEqual(next(n for n in recorded if n['id'] == 'combine')['depends_on'], ['research', 'design'])
        self.assertFalse(any(c['name'].startswith('人工交接记录') for c in run['checks']))

    def test_reviewer_receives_current_evidence_before_completion_and_distinguishes_history(self):
        (self.project / 'PROJECT.md').write_text('# 历史项目记录\nHISTORICAL_FAILURE_ONLY：旧任务曾经失败，此条不是本次结果。', encoding='utf-8')
        entered, release = threading.Event(), threading.Event()
        class InspectingReviewer(FakeRunner):
            review_prompt = None
            def run(self, role, prompt, schema, workdir, cancel_event, timeout, executor_id='codex'):
                if role == 'reviewer':
                    self.review_prompt = prompt
                    entered.set()
                    while not release.wait(.02):
                        if cancel_event.is_set():
                            raise Cancelled('测试结束')
                return super().run(role, prompt, schema, workdir, cancel_event, timeout, executor_id)
        runner = InspectingReviewer()
        app = self.app(runner)
        ident = self.create(app)['id']
        try:
            self.assertTrue(entered.wait(timeout=4), 'Reviewer was not reached')
            reviewing = app.get(ident)
            self.assertEqual(reviewing['status'], 'reviewing')
            self.assertIsNone(reviewing['review'])
            self.assertTrue(all(n['status'] == 'completed' for n in reviewing['tasks']))
            self.assertFalse((app.runs_dir / ident / 'review.md').exists())
            self.assertIn('HISTORICAL_FAILURE_ONLY', runner.review_prompt)
            self.assertIn('项目历史记录不代表本次状态', runner.review_prompt)
            self.assertIn('完成状态由你的审查通过后生成', runner.review_prompt)
            self.assertIn('不要循环要求预先存在你的最终验收记录', runner.review_prompt)
            for check in reviewing['checks']:
                self.assertIn(json.dumps(check, ensure_ascii=False), runner.review_prompt)
        finally:
            release.set()
        self.assertEqual(self.wait(app, ident)['status'], 'completed')

    def test_manual_disallowed_for_planner(self):
        app = self.app()
        with self.assertRaisesRegex(WorkflowError, '总控'):
            self.create(app, role_executors={'planner': 'manual'})

    def test_handoff_survives_restart_and_continues_without_repasting(self):
        app = self.app()
        ident = self.create(app, role_executors={'researcher': 'manual'})['id']
        self.wait(app, ident, lambda r: bool(r['handoffs']))
        app.action(ident, 'pause')
        body = '# 唯一人工成果\nPERSISTED_HANDOFF_TEST_BODY'
        app.submit_handoff(ident, {'task_id': 'research', 'content': body})
        app.shutdown()
        app.conn.close()
        self.apps.remove(app)
        restored = self.app()
        run = restored.get(ident)
        self.assertEqual(run['status'], 'interrupted')
        self.assertEqual(run['handoffs'][0]['result']['deliverable_markdown'], body)
        restored.action(ident, 'retry')
        finished = self.wait(restored, ident)
        self.assertEqual(finished['status'], 'completed')
        self.assertEqual(next(t for t in finished['tasks'] if t['id'] == 'research')['output'], body)
        self.assertEqual(len(finished['handoffs']), 1)
        self.assertTrue(finished['handoffs'][0].get('consumed_at'))

    def test_cancel_wins_race_with_completion(self):
        class RaceBench(TestBench):
            def mutate(self, ident, callback):
                if callback.__name__ == 'finish':
                    self.action(ident, 'cancel')
                return super().mutate(ident, callback)
        app = RaceBench(self.root, FakeRunner(), self.index)
        self.apps.append(app)
        ident = self.create(app)['id']
        run = self.wait(app, ident)
        app.threads[ident].join(timeout=3)
        self.assertEqual(app.get(ident)['status'], 'cancelled')
        self.assertNotIn('completed', [e['type'] for e in app.get(ident)['events']])

    def test_internal_logs_and_credentials_cannot_be_exported(self):
        for path in ('_events.jsonl', '_response.json', '.env', '.harness-home/session.json', 'auth.json', 'private.pem'):
            self.assertFalse(exportable_artifact(path), path)
        self.assertTrue(exportable_artifact('source/main.py'))
        class LogRunner(FakeRunner):
            def run(self, role, prompt, schema, workdir, cancel_event, timeout, executor_id='codex'):
                value = super().run(role, prompt, schema, workdir, cancel_event, timeout, executor_id)
                if role not in {'planner', 'reviewer'}:
                    (Path(workdir) / '_events.jsonl').write_text('SYNTHETIC_PRIVATE_LOG')
                    value['artifact_paths'] = ['_events.jsonl']
                return value
        app = self.app(LogRunner())
        run = self.wait(app, self.create(app)['id'])
        self.assertEqual(run['status'], 'failed')
        self.assertFalse(any(a['name'] == '_events.jsonl' for a in run['artifacts']))
        self.assertTrue(any('内部日志' in t['error'] for t in run['tasks']))

    def test_symlink_alias_cannot_export_internal_log(self):
        class AliasRunner(FakeRunner):
            def run(self, role, prompt, schema, workdir, cancel_event, timeout, executor_id='codex'):
                value = super().run(role, prompt, schema, workdir, cancel_event, timeout, executor_id)
                if role not in {'planner', 'reviewer'}:
                    (Path(workdir) / '_events.jsonl').write_text('SYNTHETIC_PRIVATE_LOG')
                    (Path(workdir) / 'public.log').symlink_to('_events.jsonl')
                    value['artifact_paths'] = ['public.log']
                return value
        app = self.app(AliasRunner())
        run = self.wait(app, self.create(app)['id'])
        self.assertEqual(run['status'], 'failed')
        self.assertFalse(any(a['path'].endswith('public.log') for a in run['artifacts']))
        self.assertTrue(any('符号链接' in t['error'] for t in run['tasks']))

    def test_recovery_reaps_only_matching_owned_process(self):
        app = self.app()
        folder = app.runs_dir / 'test-owned' / 'node'
        folder.mkdir(parents=True)
        child = subprocess.Popen(['/bin/sleep', '60'], start_new_session=True)
        untouched = subprocess.Popen(['/bin/sleep', '60'], start_new_session=True)
        try:
            record = register_child(child, folder)
            other = register_child(untouched, folder)
            self.assertIsNotNone(record)
            data = json.loads(other.read_text())
            data['identity'] += 'DOES_NOT_MATCH'
            other.write_text(json.dumps(data))
            self.app()
            child.wait(timeout=4)
            self.assertIsNotNone(child.returncode)
            self.assertIsNone(untouched.poll())
        finally:
            for process in (child, untouched):
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=3)

    def test_manual_wait_does_not_exhaust_execution_budget(self):
        app = self.app(run_timeout=1)
        ident = self.create(app, role_executors={'researcher': 'manual'})['id']
        self.wait(app, ident, lambda r: r['status'] == 'waiting_input' and not any(t['status'] == 'running' for t in r['tasks']))
        time.sleep(1.3)
        self.assertEqual(app.get(ident)['status'], 'waiting_input')
        app.submit_handoff(ident, {'task_id': 'research', 'content': '# 人工成果\n已完成研究。'})
        self.assertEqual(self.wait(app, ident)['status'], 'completed')

    def test_execution_budget_stops_active_work(self):
        app = self.app(FakeRunner(slow=True), run_timeout=.25)
        run = self.wait(app, self.create(app)['id'])
        self.assertEqual(run['status'], 'failed')
        self.assertIn('时限', run['error'])

    def test_duplicate_service_cannot_reap_live_workers(self):
        first = acquire_service_lock(self.root)
        try:
            with self.assertRaisesRegex(WorkflowError, '已在运行'):
                acquire_service_lock(self.root)
        finally:
            first.close()
        second = acquire_service_lock(self.root)
        second.close()

    def test_project_archive_is_idempotent_and_local(self):
        app = self.app()
        ident = self.create(app)['id']
        self.wait(app, ident)
        app.threads[ident].join(timeout=3)
        app._archive_run(ident)
        app._archive_run(ident)
        report = (self.root / 'data' / 'PROJECT.md').read_text()
        self.assertEqual(report.count('<!-- workbench-run:' + ident), 1)
        self.assertEqual(app.get(ident)['archive']['status'], 'saved')
        self.assertNotIn(ident, (self.project / 'PROJECT.md').read_text())
        self.assertFalse((self.root / 'PROJECT.md').exists())

    def test_relative_projects_are_resolved_against_index_and_invalid_rows_skipped(self):
        self.index.write_text(json.dumps({'projects': [
            {'key': 'demo', 'name': 'Portable', 'root': 'project'},
            {'key': 'missing', 'name': 'Missing', 'root': 'absent'},
            {'key': '../bad', 'name': 'Invalid', 'root': 'project'},
            {'key': 'demo', 'name': 'Duplicate', 'root': 'project'},
            {'key': 'badtype', 'name': 'Invalid', 'root': None},
        ]}))
        app = self.app()
        self.assertEqual(app.projects(), [{'key': 'demo', 'name': 'Portable', 'root': str(self.project.resolve()), 'summary': ''}])
        run = self.wait(app, self.create(app)['id'])
        self.assertEqual(run['status'], 'completed')
        self.assertEqual(run['project_root'], str(self.project.resolve()))

    def test_external_data_directory_keeps_runtime_out_of_source_tree(self):
        state = self.root / 'separate-state'
        app = self.app(data_dir=state)
        ident = self.create(app)['id']
        self.wait(app, ident)
        app.threads[ident].join(timeout=3)
        self.assertEqual(app.data, state)
        self.assertTrue((state / 'PROJECT.md').is_file())
        self.assertTrue((state / 'workbench.sqlite3').is_file())
        self.assertFalse((self.root / 'data').exists())
        self.assertFalse((self.root / 'PROJECT.md').exists())

    def test_http_security_and_real_api_persistence(self):
        app = self.app()
        (self.root / 'web').mkdir()
        (self.root / 'web/index.html').write_text('<!doctype html><h1>Test</h1>')
        http = serve(app, 0)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(http.server_port)
        def request(path, payload=None, headers=None):
            request_headers = headers or {}
            if payload is not None:
                request_headers['Content-Type'] = 'application/json'
            req = urllib.request.Request(base + path, data=None if payload is None else json.dumps(payload).encode(), headers=request_headers)
            return urllib.request.urlopen(req, timeout=5)
        try:
            self.assertEqual(request('/').status, 200)
            response = request('/api/runs', {'title': 'API任务', 'brief': 'API持久化', 'project_key': 'demo'})
            self.assertEqual(response.status, 201)
            ident = json.load(response)['id']
            run = self.wait(app, ident)
            self.assertEqual(json.load(request('/api/runs/' + ident))['status'], 'completed')
            artifact = next(a for a in run['artifacts'] if a['name'] == 'deliverable.md')
            self.assertIn('测试成果', request(artifact['url']).read().decode())
            for headers in ({'Origin': 'https://evil.example'}, {'Host': 'evil.example'}, {'Sec-Fetch-Site': 'cross-site'}):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    request('/api/runs', {'title': 'bad', 'brief': 'bad', 'project_key': 'demo'}, headers)
                self.assertEqual(error.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as error:
                request('/api/artifacts/' + ident + '/../index.json')
            self.assertIn(error.exception.code, (400, 404))
        finally:
            http.shutdown()
            http.server_close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
