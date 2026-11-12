import concurrent.futures
import hashlib
import json
import multiprocessing
from pathlib import Path
import tempfile
import unittest

from knowledge_loop import KnowledgeLoop, KnowledgeError


def concurrent_candidate(args):
    vault, store, evidence_root, number = args
    engine = KnowledgeLoop(vault, store, evidence_root)
    evidence = engine.snapshot_evidence('run-1/checks.md')
    return engine.create_candidate('alpha', '复核', '并发经验 %d' % number, [evidence])['id']


class KnowledgeLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = self.root / 'vault'
        self.pages = self.vault / '知识/概念'
        self.pages.mkdir(parents=True)
        self.runs = self.root / 'runs'
        (self.runs / 'run-1').mkdir(parents=True)
        self.proof = self.runs / 'run-1/checks.md'
        self.proof.write_text('# 验证结果\n知识检索返回正确来源。\n重复操作没有重复创建。\n')
        self.store = self.root / 'data/memories.json'
        self.engine = KnowledgeLoop(self.vault, self.store, self.runs)

    def tearDown(self):
        self.temp.cleanup()

    def test_manual_document_read_enforces_same_privacy_and_symlink_boundary(self):
        page = self.pages / 'method.md'
        page.write_text('# Public synthetic method\nKeep source evidence.\n')
        self.assertIn('synthetic', self.engine.read_document('知识/概念/method.md'))
        page.write_text('---\nprivacy: private\n---\nSynthetic excluded content.\n')
        with self.assertRaises(KnowledgeError):
            self.engine.read_document('知识/概念/method.md')
        page.unlink()
        page.symlink_to(self.proof)
        with self.assertRaises(KnowledgeError):
            self.engine.read_document('知识/概念/method.md')
        with self.assertRaises(KnowledgeError):
            self.engine.read_document('../outside.md')

    def candidate(self, project='alpha', rule='检索时保留知识来源和证据'):
        return self.engine.create_candidate(project, '知识检索', rule,
                                            [self.engine.snapshot_evidence('run-1/checks.md')],
                                            origin_run_id='run-1', conditions='来源仍与当前版本一致')

    def activate(self, record):
        return self.engine.review(record['id'], 'activate',
                                  {'passed': True, 'summary': '核对落盘证据与适用条件', 'reviewer': 'independent-reviewer'},
                                  expected_revision=record['revision'])

    def test_candidate_hidden_active_scoped_and_persisted(self):
        row = self.candidate()
        self.assertEqual([], self.engine.search('知识检索', 'alpha')['results'])
        active = self.activate(row)
        result = self.engine.search('知识检索', 'alpha')['results']
        self.assertEqual(row['id'], result[0]['memory_id'])
        self.assertEqual([], self.engine.search('知识检索', 'beta')['results'])
        reloaded = KnowledgeLoop(self.vault, self.store, self.runs)
        self.assertEqual(active['revision'], reloaded.list_memories('alpha')[0]['revision'])

    def test_deduplication_scope_and_conditions(self):
        first = self.candidate()
        self.assertEqual(first['id'], self.candidate()['id'])
        self.assertNotEqual(first['id'], self.candidate('beta')['id'])
        other = self.engine.create_candidate('alpha', first['title'], first['rule'], first['evidence'], conditions='新条件')
        self.assertNotEqual(first['id'], other['id'])
        self.assertEqual(3, len(self.engine.list_memories()))

    def test_requires_review_and_revision(self):
        row = self.candidate()
        with self.assertRaises(KnowledgeError):
            self.engine.review(row['id'], 'activate', {'passed': False, 'summary': '没有通过'})
        with self.assertRaises(KnowledgeError):
            self.engine.review(row['id'], 'activate', {'passed': True, 'summary': '已检查'}, expected_revision=9)
        with self.assertRaises(KnowledgeError):
            self.engine.review(row['id'], 'activate', {'passed': True})
        self.assertEqual('candidate', self.engine.list_memories()[0]['status'])

    def test_evidence_change_invalidates_and_refresh_does_not_reactivate(self):
        row = self.activate(self.candidate())
        old_evidence = row['evidence']
        self.proof.write_text('# 更新后的核验\n知识检索发生了改动。\n')
        self.assertEqual([], self.engine.search('知识检索', 'alpha')['results'])
        invalid = self.engine.list_memories()[0]
        self.assertEqual('candidate', invalid['status'])
        self.assertTrue(invalid['stale'])
        self.assertGreater(invalid['revision'], row['revision'])
        with self.assertRaises(KnowledgeError):
            self.engine.create_candidate('alpha', '旧快照', '不要使用旧快照', old_evidence)
        refreshed = self.candidate()
        self.assertEqual(row['id'], refreshed['id'])
        self.assertFalse(refreshed['stale'])
        self.assertEqual('candidate', refreshed['status'])
        self.assertEqual([], self.engine.search('知识检索', 'alpha')['results'])
        self.activate(refreshed)
        self.assertEqual(1, len(self.engine.search('知识检索', 'alpha')['results']))

    def test_changed_source_cannot_activate_before_refresh(self):
        row = self.candidate()
        self.proof.write_text('changed\n')
        with self.assertRaises(KnowledgeError):
            self.activate(row)
        self.assertEqual('candidate', self.engine.list_memories()[0]['status'])

    def test_edited_rule_invalidates_old_review(self):
        self.activate(self.candidate())
        state = json.loads(self.store.read_text())
        state['memories'][0]['rule'] = '知识检索被编辑为未经核对的规则'
        self.store.write_text(json.dumps(state))
        self.assertEqual([], self.engine.search('知识检索', 'alpha')['results'])
        self.assertTrue(self.engine.list_memories()[0]['stale'])

    def test_rejection_and_retirement_do_not_reenter_search(self):
        first = self.candidate()
        self.engine.review(first['id'], 'reject', {'summary': '缺乏实际依据'})
        self.assertEqual('rejected', self.candidate()['status'])
        second = self.activate(self.candidate(rule='知识检索必须带证据行号'))
        self.engine.review(second['id'], 'retire', {'summary': '项目条件已经变更'}, expected_revision=second['revision'])
        self.assertEqual([], self.engine.search('知识检索', 'alpha')['results'])
        self.assertEqual({'active': 0, 'candidate': 0, 'rejected': 1, 'retired': 1}, self.engine.status()['counts'])

    def test_selected_knowledge_only_with_evidence_and_project_fields(self):
        page = self.pages / '知识检索.md'
        page.write_text('---\ntitle: 知识检索\nproject_key: alpha\n---\n# 知识检索\n检索需要来源和行号。\n')
        (self.pages / '子目录').mkdir()
        (self.pages / '子目录/私人.md').write_text('知识检索 私人内容')
        (self.vault / 'Codex 对话').mkdir()
        (self.vault / 'Codex 对话/私人.md').write_text('知识检索 私人内容')
        (self.vault / '原始资料').mkdir()
        (self.vault / '原始资料/资料.md').write_text('知识检索 原件')
        rows = self.engine.search('知识检索', 'alpha')['results']
        self.assertEqual(1, len(rows))
        item = rows[0]
        self.assertEqual(hashlib.sha256(page.read_bytes()).hexdigest(), item['sha256'])
        self.assertEqual(5, item['start_line'])
        self.assertEqual(6, item['end_line'])
        self.assertEqual('\n'.join(page.read_text().splitlines()[4:6]), item['excerpt'])
        self.assertEqual([], self.engine.search('知识检索', 'beta')['results'])
        # Retrieval evidence can be used without inventing a location or hash.
        self.engine.create_candidate('alpha', '知识页面依据', '先看检索的原文', [item])

    def test_long_lines_have_exact_ranges_and_multiline_project_scope(self):
        page = self.pages / '精确检索.md'
        page.write_text('---\nprojects:\n  - alpha\n  - gamma\n---\n# 精确检索\n' + '来源证据' * 1000 + '\n第二行检索依据。\n')
        rows = self.engine.search('精确检索', 'alpha')['results']
        self.assertTrue(rows)
        for item in rows:
            lines = page.read_text().splitlines()
            self.assertEqual('\n'.join(lines[item['start_line'] - 1:item['end_line']]), item['excerpt'])
            self.engine.create_candidate('alpha', '精确片段', '用准确的行号定位证据', [item])
        self.assertEqual([], self.engine.search('精确检索', 'beta')['results'])

    def test_long_query_does_not_return_two_generic_bigram_matches(self):
        (self.pages / '数据库维护.md').write_text('# 数据库维护\n备份时检查一致性。\n')
        result = self.engine.search('高尔夫挥杆一致性', 'comics')
        self.assertEqual([], result['results'])
        self.assertTrue(result['warnings'])
        self.assertEqual(3, result['scope']['minimum_score'])
        (self.pages / '高尔夫挥杆.md').write_text('# 高尔夫挥杆\n先检查动作是否保持一致。\n')
        self.assertEqual('高尔夫挥杆', self.engine.search('高尔夫挥杆一致性', 'comics')['results'][0]['title'])

    def test_memory_search_retains_evidence_for_display(self):
        self.activate(self.candidate())
        item = self.engine.search('知识检索', 'alpha')['results'][0]
        self.assertTrue(item['evidence'])
        evidence = item['evidence'][0]
        self.assertEqual('run-1/checks.md', evidence['path'])
        self.assertEqual(1, evidence['start_line'])
        self.assertEqual(hashlib.sha256(self.proof.read_bytes()).hexdigest(), evidence['sha256'])
        self.assertIn('知识检索', evidence['excerpt'])

    def test_private_derivative_inside_selected_directory_is_not_retrieved(self):
        pages = {
            '聊天来源.md': '# 知识检索\n来源：[[Codex 对话/某次私聊]]\n知识检索应引用这个页面。',
            '编码聊天来源.md': '# 知识检索\n来源：https://local/Codex%20%E5%AF%B9%E8%AF%9D%2Fchat.md',
            '私密标记.md': '---\nprivacy: private\n---\n# 知识检索\n保密内容。',
            '私人范围.md': '---\nscope: "personal"\n---\n# 知识检索',
            '未发布.md': '---\nshareable: false\n---\n# 知识检索',
            '原话表.md': '# 知识检索\n| 用户原话 | 来源 |\n| 私人内容 | 当前聊天 |',
            '原话标签.md': '# 知识检索\n## 私人聊天原文\n私人内容',
        }
        for name, content in pages.items():
            (self.pages / name).write_text(content)
        result = self.engine.search('知识检索', 'alpha')
        self.assertEqual([], result['results'])
        self.assertTrue(any('非全库' in w for w in result['warnings']))
        for name in pages:
            with self.subTest(name=name), self.assertRaises(KnowledgeError):
                self.engine.snapshot_evidence('知识/概念/' + name, source='vault')
        self.assertFalse(any(name in str(result) for name in pages))

    def test_generic_privacy_caution_is_not_itself_excluded(self):
        (self.pages / '知识检索规则.md').write_text('# 知识检索规则\n不要泄露私人聊天原文。知识检索应附上证据。\n')
        result = self.engine.search('知识检索', 'alpha')
        self.assertEqual('知识检索规则', result['results'][0]['title'])

    def test_active_vault_memory_is_invalidated_when_source_becomes_private(self):
        path = self.pages / '知识检索规则.md'
        path.write_text('# 知识检索规则\n先核验证据。\n')
        evidence = self.engine.snapshot_evidence('知识/概念/知识检索规则.md', source='vault')
        row = self.engine.create_candidate('alpha', '知识检索', '知识检索时核验证据', [evidence])
        self.activate(row)
        path.write_text('---\nprivacy: private\n---\n# 知识检索规则\n先核验证据。\n')
        self.assertEqual([], self.engine.search('知识检索', 'alpha')['results'])
        self.assertTrue(self.engine.list_memories()[0]['stale'])

    def test_source_traversal_private_files_and_symlinks_rejected(self):
        for path in ('../secret.md', '/tmp/secret.md', 'run-1/../checks.md', 'run-1/config.json', 'run-1/.env', 'run-1/database.db'):
            with self.subTest(path=path), self.assertRaises(KnowledgeError):
                self.engine.snapshot_evidence(path)
        (self.runs / 'run-1/link.md').symlink_to(self.proof)
        with self.assertRaises(KnowledgeError):
            self.engine.snapshot_evidence('run-1/link.md')
        (self.runs / 'linked').symlink_to(self.runs / 'run-1', target_is_directory=True)
        with self.assertRaises(KnowledgeError):
            self.engine.snapshot_evidence('linked/checks.md')
        (self.pages / 'linked.md').symlink_to(self.proof)
        self.assertEqual([], self.engine.search('知识检索', 'alpha')['results'])

    def test_fabricated_sha_and_quote_rejected(self):
        evidence = self.engine.snapshot_evidence('run-1/checks.md')
        for mutation in ({'sha256': '0' * 64}, {'excerpt': '伪造内容'}):
            with self.subTest(mutation=mutation), self.assertRaises(KnowledgeError):
                self.engine.create_candidate('alpha', '检索', '知识检索', [{**evidence, **mutation}])

    def test_corrupt_store_is_not_silently_overwritten(self):
        self.store.write_text('{broken')
        with self.assertRaises(KnowledgeError):
            self.candidate()
        self.assertEqual('{broken', self.store.read_text())

    def test_cross_process_updates_preserve_all_candidates(self):
        # Separate instances/processes must coordinate on the same lock file.
        ctx = multiprocessing.get_context('spawn')
        tasks = [(str(self.vault), str(self.store), str(self.runs), i) for i in range(12)]
        with concurrent.futures.ProcessPoolExecutor(max_workers=3, mp_context=ctx) as pool:
            ids = list(pool.map(concurrent_candidate, tasks))
        self.assertEqual(12, len(set(ids)))
        self.assertEqual(12, len(self.engine.list_memories()))

    def test_threaded_duplicate_creation_and_single_revision_winner(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            rows = list(pool.map(lambda _: self.candidate(), range(10)))
        self.assertEqual(1, len({row['id'] for row in rows}))
        row = rows[0]
        def attempt(_):
            try:
                self.activate(row)
                return True
            except KnowledgeError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(attempt, range(5)))
        self.assertEqual(1, sum(results))


if __name__ == '__main__':
    unittest.main()
