"""Isolated integration tests for retrieval, review, reuse and feedback.

All model calls are explicit fakes; all knowledge, evidence and stores are temporary.
These assertions do not claim real model quality or user/business outcomes.
"""
import json
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

from server import Workbench, WorkflowError, Cancelled, serve


class MemoryRunner:
    binary = '/not-a-real-model'

    def __init__(self, dream_failure=False, memory_verdict='pass', block_dream=False, business_verdict='pass'):
        self.calls = []
        self.lock = threading.Lock()
        self.dream_failure = dream_failure
        self.memory_verdict = memory_verdict
        self.memory_rule = '生成或修改图纸前先核对版本，确认采用最新已验收版本。'
        self.business_verdict = business_verdict
        self.block_dream = block_dream
        self.dream_entered = threading.Event()
        self.release_dream = threading.Event()

    def run(self, role, prompt, schema, workdir, cancel_event, timeout, executor_id='codex'):
        with self.lock:
            self.calls.append({'role': role, 'prompt': prompt, 'workdir': str(workdir), 'executor_id': executor_id})
        if role == 'planner':
            return {'summary': '隔离测试规划', 'tasks': [{'id': 'build', 'title': '图纸版本校验', 'role': 'executor',
                    'instructions': '使用已提供的图纸版本依据，生成可核对说明。', 'depends_on': []}]}
        if role in {'reviewer', 'memory_reviewer'}:
            verdict = self.memory_verdict if role == 'memory_reviewer' else self.business_verdict
            return {'verdict': verdict, 'summary': 'fake 独立复核：' + verdict,
                    'findings': ['证据不足，补充图纸版本对照'] if verdict == 'revise' else []}
        if role == 'dreamer':
            self.dream_entered.set()
            if self.dream_failure:
                raise WorkflowError('隔离测试：复盘执行器失败')
            if self.block_dream:
                while not self.release_dream.wait(.01):
                    if cancel_event.is_set():
                        raise Cancelled('隔离测试：复盘停止')
            evidence = json.loads(prompt.rsplit('\n证据：', 1)[1])
            ref = next((e['ref'] for e in evidence if e['path'].endswith('/deliverable.md')), evidence[0]['ref'])
            return {'summary': 'fake 提炼一条有明确范围的版本经验', 'memories': [{
                    'title': '图纸版本校验',
                    'rule': self.memory_rule,
                    'conditions': '仅用于同项目装修图纸版本校验任务。', 'evidence_refs': [ref]}]}
        ids = list(dict.fromkeys(re.findall(r'^\[K:(k[a-f0-9]{12})\]', prompt, re.M)))
        content = '# 图纸版本校验\n\n生成或修改图纸前先核对版本，确认采用最新已验收版本。\n'
        content += '\n'.join('采用 [K:%s] 核对图纸版本与适用条件。' % source_id for source_id in ids)
        content += '\n伪造引用 [K:k000000000000] 不应登记为采用。'
        return {'summary': 'fake 图纸版本成果', 'deliverable_markdown': content, 'artifact_paths': [],
                'checks': [{'name': '测试内容检查', 'passed': True, 'evidence': 'FakeRunner 隔离链路；非业务验证'}]}


class IsolatedBench(Workbench):
    def providers(self):
        return [{'id': 'codex', 'name': '隔离测试', 'available': True, 'authenticated': True, 'kind': 'cli'}]

    def health(self):
        return {'codex_available': True, 'authenticated': True, 'local_only': True,
                'active_count': sum(t.is_alive() for t in self.threads.values()),
                'dream_active_count': sum(t.is_alive() for t in self.dream_threads.values())}


class KnowledgeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / 'app'
        self.vault = self.base / 'vault'
        self.project = self.base / 'project'
        self.project.mkdir()
        (self.project / 'PROJECT.md').write_text('# 测试项目\n图纸版本应先核对。', encoding='utf-8')
        (self.project / 'AGENTS.md').write_text('源项目只读。', encoding='utf-8')
        (self.root / 'web').mkdir(parents=True)
        self.index = self.base / 'index.json'
        self.index.write_text(json.dumps({'projects': [
            {'key': 'demo', 'name': '图纸测试', 'root': str(self.project)},
            {'key': 'other', 'name': '另一个项目', 'root': str(self.project)},
        ]}), encoding='utf-8')
        self.shared = self.vault / '知识/概念/图纸版本.md'
        self.shared.parent.mkdir(parents=True)
        self.shared.write_text('---\ntitle: 图纸版本\n---\n# 图纸版本\n\n图纸版本校验必须核对当前版本和验收依据。\n', encoding='utf-8')
        for relative, content in [
            ('知识/综合分析/外项目图纸.md', '---\nproject_key: other\n---\n# 图纸版本\nOTHER_SCOPE_ONLY 图纸版本校验'),
            ('原始资料/图纸版本原始.md', 'RAW_PRIVATE_SENTINEL 图纸版本校验'),
            ('Codex 对话/图纸版本聊天.md', 'CHAT_PRIVATE_SENTINEL 图纸版本校验'),
        ]:
            path = self.vault / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        (self.shared.parent / '图纸版本链接.md').symlink_to(self.vault / '原始资料/图纸版本原始.md')
        self.apps = []
        self.http = None

    def tearDown(self):
        if self.http:
            self.http.shutdown()
            self.http.server_close()
        for app in self.apps:
            app.shutdown()
            app.conn.close()
        self.tmp.cleanup()

    def app(self, runner=None, auto_dream=False):
        app = IsolatedBench(root=self.root, runner=runner or MemoryRunner(), project_index=self.index,
                            vault=self.vault, auto_dream=auto_dream, node_timeout=3, run_timeout=10)
        self.apps.append(app)
        return app

    def complete(self, app, title='图纸版本校验测试', **overrides):
        payload = {'title': title, 'brief': '核对图纸版本，保存依据并给出可核对说明。', 'project_key': 'demo'}
        payload.update(overrides)
        run = app.create(payload)
        app.threads[run['id']].join(6)
        self.assertFalse(app.threads[run['id']].is_alive())
        run = app.get(run['id'])
        self.assertEqual(run['status'], 'completed', run.get('error'))
        return run

    def await_dream(self, app, ident):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            with app.lock:
                thread = app.dream_threads.get(ident)
                details = app.knowledge_details(ident)
                if (not thread or not thread.is_alive()) and details['dream']['status'] not in {'queued', 'running'}:
                    return details
            if thread:
                thread.join(.05)
            else:
                time.sleep(.01)
        self.fail('隔离复盘未在测试期限内结束')

    def dream(self, app, ident, expected='completed'):
        app.start_dream(ident)
        details = self.await_dream(app, ident)
        self.assertEqual(details['dream']['status'], expected, details['dream'].get('error'))
        return details

    def test_two_tasks_promote_then_retrieve_and_record_citation_without_claiming_effect(self):
        runner = MemoryRunner()
        app = self.app(runner)
        first = self.complete(app)
        sources = first['knowledge']['retrieval']['results']
        self.assertTrue(sources)
        self.assertTrue(all(s['sha256'] and s['path'] for s in sources))
        self.assertTrue({'planner', 'executor', 'reviewer'} <= {x['role'] for x in first['knowledge']['injections']})
        prompts = '\n'.join(c['prompt'] for c in runner.calls)
        for excluded in ('RAW_PRIVATE_SENTINEL', 'CHAT_PRIVATE_SENTINEL', 'OTHER_SCOPE_ONLY'):
            self.assertNotIn(excluded, prompts)
        detail = self.dream(app, first['id'])
        self.assertEqual(len(detail['memories']), 1)
        memory = detail['memories'][0]
        self.assertEqual(memory['status'], 'active')
        self.assertTrue(memory['verification']['passed'])
        roles = [c['role'] for c in runner.calls]
        self.assertLess(roles.index('dreamer'), roles.index('memory_reviewer'))
        second = self.complete(app, title='第二次图纸版本校验')
        memory_hits = [s for s in second['knowledge']['retrieval']['results'] if s.get('memory_id') == memory['id']]
        self.assertTrue(memory_hits)
        usage = [u for u in second['knowledge']['usage'] if u.get('memory_id') == memory['id']]
        self.assertTrue(usage)
        self.assertTrue(all(u['status'] == 'cited' for u in usage))
        self.assertFalse(any(u['source_id'] == 'k000000000000' for u in second['knowledge']['usage']))
        self.assertEqual(second['knowledge'].get('feedback', []), [])
        self.assertEqual(app.knowledge_loop.search('图纸版本', 'other')['results'][0]['kind'], 'knowledge')
        self.assertFalse(any(s.get('memory_id') == memory['id'] for s in app.knowledge_loop.search('图纸版本', 'other')['results']))
        self.assertTrue(any(a['name'] == 'knowledge.json' for a in second['artifacts']))
        archive = (self.root / 'data' / 'PROJECT.md').read_text()
        self.assertIn('promoted-' + memory['id'], archive)
        self.assertEqual((self.project / 'PROJECT.md').read_text(), '# 测试项目\n图纸版本应先核对。')

    def test_existing_project_rules_reach_both_memory_roles_without_claiming_semantic_dedup(self):
        runner = MemoryRunner()
        app = self.app(runner)
        first = self.complete(app)
        active = self.dream(app, first['id'])['memories'][0]
        candidate = app.knowledge_loop.create_candidate('demo', '候选适用条件',
                    'CANDIDATE_RULE：只有图纸来源与版本一致时，才可使用当前核对结果。',
                    active['evidence'], origin_run_id=first['id'], conditions='候选规则，等待独立复核。')
        other = app.knowledge_loop.create_candidate('other', '外项目规则',
                    'OTHER_PROJECT_RULE：外项目专属规则不得进入本项目复盘上下文。',
                    active['evidence'], origin_run_id=first['id'], conditions='仅限其他项目。')
        second = self.complete(app, title='复述已有经验后再次校验')
        # Force a distinct fake proposal so the reviewer is exercised. The fake
        # cannot assess whether this proposal is semantically new or supported.
        runner.memory_rule = 'FAKE_NEW_PROPOSAL：核对版本后检查交付日期，限定本次适用条件。'
        runner.memory_verdict = 'revise'
        calls_before = len(runner.calls)
        self.dream(app, second['id'])
        memory_calls = [c for c in runner.calls[calls_before:] if c['role'] in {'dreamer', 'memory_reviewer'}]
        self.assertEqual([c['role'] for c in memory_calls], ['dreamer', 'memory_reviewer'])
        for call in memory_calls:
            for existing in (active, candidate):
                self.assertIn(existing['id'], call['prompt'])
                self.assertIn(existing['rule'], call['prompt'])
            self.assertNotIn(other['id'], call['prompt'])
            self.assertNotIn(other['rule'], call['prompt'])

    def test_independent_memory_review_can_keep_candidate_out_of_next_task(self):
        app = self.app(MemoryRunner(memory_verdict='revise'))
        first = self.complete(app)
        detail = self.dream(app, first['id'])
        memory = detail['memories'][0]
        self.assertEqual(memory['status'], 'candidate')
        self.assertIn('证据不足', json.dumps(detail['dream'].get('reviews', []), ensure_ascii=False))
        second = self.complete(app, title='第二次图纸版本校验')
        self.assertFalse(any(s.get('memory_id') == memory['id'] for s in second['knowledge']['retrieval']['results']))

    def test_actual_feedback_is_idempotent_and_harmful_memory_stops_retrieval(self):
        app = self.app()
        first = self.complete(app)
        memory = self.dream(app, first['id'])['memories'][0]
        second = self.complete(app, title='第二次图纸版本校验')
        payload = {'rating': 'harmful', 'note': '隔离测试反馈：该条件下规则不适用。', 'memory_ids': [memory['id']]}
        app.submit_feedback(second['id'], payload)
        detail = app.submit_feedback(second['id'], payload)
        self.assertEqual(len(detail['feedback']), 1)
        self.assertEqual(detail['feedback'][0]['source'], 'user')
        self.assertEqual(app.get(second['id'])['status'], 'completed')
        self.assertEqual(app.knowledge_loop.list_memories('demo')[0]['status'], 'retired')
        self.assertFalse(any(s.get('memory_id') == memory['id'] for s in app.knowledge_search('图纸版本', 'demo')['results']))
        marker = '<!-- knowledge-loop:%s:feedback-%s -->' % (second['id'], detail['feedback'][0]['id'])
        self.assertEqual((self.root / 'data' / 'PROJECT.md').read_text().count(marker), 1)
        with self.assertRaisesRegex(WorkflowError, '本任务'):
            app.submit_feedback(first['id'], payload)

    def test_dream_failure_preserves_completed_task_and_limits_same_evidence_retries(self):
        app = self.app(MemoryRunner(dream_failure=True))
        run = self.complete(app)
        original_review = run['review']
        for _ in range(3):
            self.dream(app, run['id'], expected='failed')
            current = app.get(run['id'])
            self.assertEqual(current['status'], 'completed')
            self.assertEqual(current['review'], original_review)
        with self.assertRaisesRegex(WorkflowError, '三次'):
            app.start_dream(run['id'])
        self.assertEqual(app.knowledge_loop.list_memories(), [])

    def test_duplicate_dream_does_not_add_candidate_or_model_calls(self):
        runner = MemoryRunner()
        app = self.app(runner)
        run = self.complete(app)
        first = self.dream(app, run['id'])
        calls = len(runner.calls)
        self.dream(app, run['id'])
        self.assertEqual(len(runner.calls), calls)
        self.assertEqual(len(app.knowledge_loop.list_memories()), 1)
        self.assertEqual(app.get(run['id'])['knowledge']['dream']['fingerprint'], first['dream']['fingerprint'])

    def test_shutdown_and_restart_interrupt_only_dream_then_allow_retry(self):
        runner = MemoryRunner(block_dream=True)
        app = self.app(runner)
        run = self.complete(app)
        app.start_dream(run['id'])
        self.assertTrue(runner.dream_entered.wait(2))
        app.shutdown()
        self.assertFalse(any(t.is_alive() for t in app.dream_threads.values()))
        self.assertEqual(app.get(run['id'])['status'], 'completed')
        self.assertEqual(app.get(run['id'])['knowledge']['dream']['status'], 'interrupted')
        app.conn.close()
        self.apps.remove(app)
        restored = self.app(MemoryRunner())
        self.assertEqual(restored.get(run['id'])['status'], 'completed')
        self.dream(restored, run['id'])
        self.assertEqual(len(restored.knowledge_loop.list_memories()), 1)

    def test_recovery_handles_abrupt_exit_with_persisted_running_dream(self):
        app = self.app()
        run = self.complete(app)
        app.mutate(run['id'], lambda r: r.setdefault('knowledge', {}).update(dream={'status': 'running', 'fingerprint': 'old', 'attempt': 1}))
        app.shutdown()
        app.conn.close()
        self.apps.remove(app)
        restored = self.app()
        self.assertEqual(restored.get(run['id'])['status'], 'completed')
        self.assertEqual(restored.get(run['id'])['knowledge']['dream']['status'], 'interrupted')

    def test_auto_dream_runs_after_task_and_source_change_disables_reuse(self):
        app = self.app(auto_dream=True)
        run = self.complete(app)
        detail = self.await_dream(app, run['id'])
        self.assertEqual(detail['dream']['status'], 'completed')
        memory = detail['memories'][0]
        self.assertEqual(memory['status'], 'active')
        evidence_path = app.runs_dir / memory['evidence'][0]['path']
        evidence_path.write_text(evidence_path.read_text() + '\n证据已更新。')
        hits = app.knowledge_search('图纸版本', 'demo')['results']
        self.assertFalse(any(s.get('memory_id') == memory['id'] for s in hits))
        stale = app.knowledge_loop.list_memories('demo')[0]
        self.assertEqual(stale['status'], 'candidate')
        self.assertTrue(stale['stale'])

    def test_feedback_during_dream_is_included_in_automatic_followup(self):
        runner = MemoryRunner(block_dream=True)
        app = self.app(runner, auto_dream=True)
        run = self.complete(app)
        self.assertTrue(runner.dream_entered.wait(2))
        fingerprint = app.get(run['id'])['knowledge']['dream']['fingerprint']
        note = 'LATE_FEEDBACK：使用后发现旧版本条件不适用，已核对实际反馈。'
        app.submit_feedback(run['id'], {'rating': 'no_improvement', 'note': note, 'memory_ids': []})
        runner.release_dream.set()
        details = self.await_dream(app, run['id'])
        self.assertEqual(details['dream']['status'], 'completed', details['dream'].get('error'))
        dream_calls = [c for c in runner.calls if c['role'] == 'dreamer']
        self.assertEqual(len(dream_calls), 2)
        self.assertNotIn(note, dream_calls[0]['prompt'])
        self.assertIn(note, dream_calls[1]['prompt'])
        self.assertNotEqual(details['dream']['fingerprint'], fingerprint)
        self.assertEqual(details['feedback'][0]['note'], note)
        self.assertEqual(app.get(run['id'])['status'], 'completed')
        self.assertEqual(len(app.knowledge_loop.list_memories()), 1)

    def test_retry_waits_for_existing_dream_without_mutating_task_state(self):
        runner = MemoryRunner(block_dream=True, business_verdict='revise')
        app = self.app(runner)
        run = app.create({'title': '图纸版本待修订测试', 'brief': '核对图纸版本，独立验收要求修订。', 'project_key': 'demo'})
        app.threads[run['id']].join(6)
        self.assertEqual(app.get(run['id'])['status'], 'review_failed')
        app.start_dream(run['id'])
        self.assertTrue(runner.dream_entered.wait(2))
        previous = app.get(run['id'])
        with self.assertRaisesRegex(WorkflowError, '复盘|记忆|整理'):
            app.action(run['id'], 'retry')
        current = app.get(run['id'])
        self.assertEqual(current['status'], 'review_failed')
        self.assertEqual(current['tasks'], previous['tasks'])
        self.assertEqual(current['knowledge']['dream'], previous['knowledge']['dream'])
        runner.release_dream.set()
        self.await_dream(app, run['id'])
        runner.business_verdict = 'pass'
        app.action(run['id'], 'retry')
        app.threads[run['id']].join(6)
        self.assertEqual(app.get(run['id'])['status'], 'completed')

    def test_changed_evidence_can_be_redreamed_after_stale_memory_is_disabled(self):
        runner = MemoryRunner()
        app = self.app(runner)
        run = self.complete(app)
        first = self.dream(app, run['id'])
        memory = first['memories'][0]
        path = app.runs_dir / memory['evidence'][0]['path']
        path.write_text(path.read_text() + '\n版本依据已修订并重新核对。')
        self.assertFalse(any(s.get('memory_id') == memory['id'] for s in app.knowledge_search('图纸版本', 'demo')['results']))
        second = self.dream(app, run['id'])
        self.assertNotEqual(first['dream']['fingerprint'], second['dream']['fingerprint'])
        self.assertEqual(len([c for c in runner.calls if c['role'] == 'dreamer']), 2)
        self.assertEqual(len(second['memories']), 1)
        self.assertEqual(second['memories'][0]['id'], memory['id'])
        self.assertEqual(second['memories'][0]['status'], 'active')
        self.assertNotEqual(second['memories'][0]['evidence'][0]['sha256'], memory['evidence'][0]['sha256'])

    def test_dream_prompt_excerpt_matches_recorded_evidence_lines(self):
        runner = MemoryRunner()
        app = self.app(runner)
        run = self.complete(app)
        relative = next(p for task in run['tasks'] for p in task['artifact_paths'] if p.endswith('/deliverable.md'))
        path = app.runs_dir / run['id'] / relative
        lines = ['第 %d 行图纸版本核对依据。' % n for n in range(1, 81)]
        lines[40] = 'AFTER_CAPTURED_RANGE 不应混入前40行证据。'
        path.write_text('\n'.join(lines), encoding='utf-8')
        self.dream(app, run['id'])
        call = next(c for c in runner.calls if c['role'] == 'dreamer')
        evidence = json.loads(call['prompt'].rsplit('\n证据：', 1)[1])
        snapshot = next(e for e in evidence if e['path'].endswith('/deliverable.md'))
        self.assertEqual(snapshot['end_line'], 40)
        self.assertEqual(snapshot['excerpt'], '\n'.join(lines[snapshot['start_line'] - 1:snapshot['end_line']]))
        self.assertNotIn('AFTER_CAPTURED_RANGE', call['prompt'])

    def test_cancelled_task_feedback_is_saved_without_starting_dream(self):
        runner = MemoryRunner()
        app = self.app(runner, auto_dream=False)
        run = self.complete(app)
        app.mutate(run['id'], lambda r: r.update(status='cancelled'))
        app.auto_dream = True
        details = app.submit_feedback(run['id'], {'rating': 'unverified', 'note': '取消后未采用，不评价实际效果。', 'memory_ids': []})
        self.assertEqual(len(details['feedback']), 1)
        self.assertEqual(app.get(run['id'])['status'], 'cancelled')
        self.assertFalse(any(c['role'] == 'dreamer' for c in runner.calls))

    def test_feedback_save_survives_background_dream_start_failure(self):
        app = self.app(auto_dream=False)
        run = self.complete(app)
        app.auto_dream = True
        def unavailable(_ident):
            raise WorkflowError('隔离测试：后台复盘暂不可用')
        app.start_dream = unavailable
        details = app.submit_feedback(run['id'], {'rating': 'effective', 'note': '实际反馈应保存，不依赖后台复盘可用。', 'memory_ids': []})
        self.assertEqual(len(details['feedback']), 1)
        self.assertEqual(app.get(run['id'])['status'], 'completed')
        self.assertTrue(any('暂不可用' in event.get('message', '') for event in app.get(run['id'])['events']))

    def test_bounded_evidence_packet_keeps_valid_json_and_latest_feedback(self):
        runner = MemoryRunner()
        app = self.app(runner)
        run = self.complete(app)
        paths = ['checks.md', 'review.md'] + [p for task in run['tasks'] for p in task['artifact_paths'] if p.endswith('/deliverable.md')]
        for relative in paths:
            path = app.runs_dir / run['id'] / relative
            path.write_text('\n'.join(('第 %d 行。' % n) + '图纸版本依据' * 48 for n in range(1, 41)), encoding='utf-8')
        self.assertGreater(sum(len((app.runs_dir / run['id'] / relative).read_text()) for relative in paths), 35000)
        note = 'LATEST_FEEDBACK_MUST_SURVIVE：按最新反馈检查版本适用条件。'
        app.submit_feedback(run['id'], {'rating': 'no_improvement', 'note': note, 'memory_ids': []})
        details = self.dream(app, run['id'])
        call = next(c for c in runner.calls if c['role'] == 'dreamer')
        packet = call['prompt'].rsplit('\n证据：', 1)[1]
        evidence = json.loads(packet)
        self.assertLessEqual(len(packet), 35000)
        self.assertIn(note, packet)
        for snapshot in evidence:
            lines = (app.runs_dir / snapshot['path']).read_text().splitlines()
            self.assertEqual(snapshot['excerpt'], '\n'.join(lines[snapshot['start_line'] - 1:snapshot['end_line']]))
        self.assertEqual(details['dream']['status'], 'completed')

    def test_http_boundaries_invalid_scope_and_feedback(self):
        app = self.app()
        run = self.complete(app)
        self.http = serve(app, 0)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        port = self.http.server_address[1]
        base = 'http://127.0.0.1:' + str(port)

        def request(path, payload=None, headers=None):
            data = None if payload is None else json.dumps(payload).encode()
            values = {'Content-Type': 'application/json'} if payload is not None else {}
            values.update(headers or {})
            req = urllib.request.Request(base + path, data=data, headers=values)
            try:
                with urllib.request.urlopen(req, timeout=3) as res:
                    return res.status, json.load(res)
            except urllib.error.HTTPError as error:
                return error.code, json.load(error)

        search = '/api/knowledge/search?' + urllib.parse.urlencode({'q': '图纸版本', 'project_key': 'demo'})
        status, data = request(search)
        self.assertEqual(status, 200)
        self.assertTrue(data['results'])
        self.assertEqual(request(search, headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(request('/api/knowledge/search?q=test&project_key=missing')[0], 400)
        self.assertEqual(request('/api/runs/0000000000000000/knowledge')[0], 404)
        feedback_path = '/api/runs/%s/feedback' % run['id']
        payload = {'rating': 'effective', 'note': '隔离测试中观察到改善。', 'memory_ids': []}
        self.assertEqual(request(feedback_path, payload, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(request(feedback_path, {'rating': 'not-valid', 'note': '测试'})[0], 400)
        self.assertEqual(request(feedback_path, dict(payload, memory_ids=['unrelated']))[0], 400)
        self.assertEqual(request(feedback_path, payload, {'Origin': base})[0], 200)
        self.assertEqual(request('/api/memories/not-existing/review', {'action': 'activate', 'note': '无效经验', 'expected_revision': 1})[0], 400)
        self.assertEqual(len(app.knowledge_details(run['id'])['feedback']), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
