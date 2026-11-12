"""Bounded local knowledge retrieval and evidence-backed project memories.

Only direct Markdown children of selected knowledge folders are searched. Raw
materials, chat archives, subfolders, links and remote sources are never followed.
Verification records an explicit review; it does not establish business outcomes.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import threading
import uuid
from urllib.parse import unquote


class KnowledgeError(ValueError):
    pass


DEFAULT_DIRS = ('知识/概念', '知识/综合分析', '知识/来源摘要')
STATES = {'candidate', 'active', 'rejected', 'retired'}
MAX_FILE_BYTES = 512_000
MAX_STORE_BYTES = 16_000_000
PRIVATE_PARTS = {'原始资料', 'Codex 对话', '私人导出', 'credentials', 'credential',
                 'secrets', 'secret', 'tokens', 'token', 'auth', 'config', 'private',
                 'password', 'passwords', 'api_key', 'apikey', 'private_key', 'keys'}
LOCKS = {}
LOCKS_GUARD = threading.Lock()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _text(value, label, maximum, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise KnowledgeError('无效的' + label)
    return value.strip()


def _scope(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', value):
        raise KnowledgeError('无效的项目范围')
    return value


def _parts(relative):
    if not isinstance(relative, str) or not relative or '\\' in relative or '\x00' in relative:
        raise KnowledgeError('无效的相对来源路径')
    raw = relative.split('/')
    if any(p in {'', '.', '..'} for p in raw) or PurePosixPath(relative).is_absolute():
        raise KnowledgeError('来源路径不能越界')
    for part in raw:
        if part.startswith(('.', '_')) or Path(part).stem.casefold() in PRIVATE_PARTS:
            raise KnowledgeError('来源路径属于排除范围')
    return raw


def _read(root, relative, maximum=MAX_FILE_BYTES):
    """Open each path component without following symlinks, including races."""
    parts = _parts(relative)
    fd = os.open(str(root), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(file_fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
                raise KnowledgeError('来源不是允许大小的普通文件')
            chunks, remaining = [], maximum + 1
            while remaining:
                chunk = os.read(file_fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b''.join(chunks)
            after = os.fstat(file_fd)
            if len(raw) > maximum or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise KnowledgeError('来源读取期间发生变化或超出大小限制')
            return raw
        finally:
            os.close(file_fd)
    finally:
        os.close(fd)


def _private_knowledge(text):
    """Fail closed for known private provenance/markers, not general cautions.

    This is a conservative publication boundary, not a classifier claiming to
    identify every private fact. There is intentionally no allow/override flag.
    """
    normalized = unquote(text).casefold()
    if re.search(r'codex[\s_-]*对话[ \t]*[/\\]', normalized):
        return True
    # A source table or a content label is different from a method page saying
    # "不要泄露私人聊天原文"; the latter alone is not a privacy marker.
    if re.search(r'\|[^\n|]*(?:用户原话|私人聊天原文|微信聊天原文)[^\n|]*\|', text):
        return True
    if re.search(r'(?m)^[ \t]*(?:(?:#{1,6}|[-*])[ \t]+)?(?:私人聊天原文|微信聊天原文|用户原话|原始聊天记录)(?:[ \t]*[:：]|[ \t]*(?:$|与|及))', text):
        return True
    header = ''
    if text.startswith('---\n'):
        header = text.split('\n---', 1)[0].casefold()
    private_values = r'(?:private|confidential|restricted|internal|sensitive|personal|私密|私有|私人|机密|受限|仅本人)'
    for match in re.finditer(r'(?m)^[ \t]*(privacy|visibility|access|scope|classification|confidentiality|sensitivity)[ \t]*:[ \t]*([^\n]*)', header):
        if re.search(r'(?<![a-z0-9_])' + private_values + r'(?![a-z0-9_])', match.group(2)):
            return True
    if re.search(r'(?m)^[ \t]*(?:private|confidential|sensitive)[ \t]*:[ \t]*["\x27]?(?:true|yes|1)(?:["\x27]?[ \t]*(?:#.*)?$)', header):
        return True
    if re.search(r'(?m)^[ \t]*(?:public|publish|published|shareable)[ \t]*:[ \t]*["\x27]?(?:false|no|0)(?:["\x27]?[ \t]*(?:#.*)?$)', header):
        return True
    return False


def _tokens(text):
    result = set(re.findall(r'[a-z0-9_]{2,}', text.casefold()))
    stop = {'什么', '怎么', '如何', '这个', '那个', '我们', '你们', '可以', '已经', '需要', '进行', '使用', '相关'}
    for run in re.findall(r'[\u3400-\u9fff]+', text):
        if len(run) == 1:
            continue
        result.update(run[i:i + 2] for i in range(len(run) - 1))
    return result - stop


def _score(query, title, body):
    return 3 * len(query & _tokens(title)) + len(query & _tokens(body))


def _fingerprint(record):
    value = {key: record.get(key) for key in ('project_key', 'title', 'rule', 'conditions', 'evidence')}
    return _sha(json.dumps(value, sort_keys=True, ensure_ascii=False).encode())


class KnowledgeLoop:
    def __init__(self, vault, store_path, evidence_root=None, allowed_dirs=DEFAULT_DIRS):
        vault, store_path = Path(vault).absolute(), Path(store_path).absolute()
        if vault.is_symlink() or store_path.is_symlink() or store_path.parent.is_symlink():
            raise KnowledgeError('知识库和存储路径不能是符号链接')
        self.vault = vault.resolve()
        self.store_path = store_path.parent.resolve() / store_path.name
        self.store_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.evidence_root = None
        if evidence_root is not None:
            evidence_root = Path(evidence_root).absolute()
            if evidence_root.is_symlink():
                raise KnowledgeError('证据根目录不能是符号链接')
            self.evidence_root = evidence_root.resolve()
        self.allowed_dirs = tuple(str(PurePosixPath(*_parts(value))) for value in allowed_dirs)
        self.lock_path = self.store_path.with_name(self.store_path.name + '.lock')
        with LOCKS_GUARD:
            self.lock = LOCKS.setdefault(str(self.store_path), threading.RLock())

    def _source(self, path, source):
        parts = _parts(path)
        if source == 'vault':
            if str(PurePosixPath(*parts[:-1])) not in self.allowed_dirs or Path(path).suffix.lower() != '.md':
                raise KnowledgeError('知识来源不在精选目录内')
            return self.vault
        if source == 'run' and self.evidence_root is not None:
            if len(parts) < 2 or Path(path).suffix.lower() not in {'.md', '.txt', '.json'}:
                raise KnowledgeError('只接受任务目录内的文本证据')
            return self.evidence_root
        raise KnowledgeError('证据来源未配置')

    def read_document(self, relative):
        """Serve a selected document under the same boundary as retrieval."""
        root = self._source(relative, 'vault')
        try:
            text = _read(root, relative).decode('utf-8')
        except (OSError, UnicodeError) as exc:
            raise KnowledgeError('知识页不存在、不可读或包含符号链接') from exc
        if _private_knowledge(text):
            raise KnowledgeError('知识页带有私密标记或私人聊天来源，不可公开读取')
        return text[:100000]

    def snapshot_evidence(self, path, start_line=1, end_line=None, source='run'):
        root = self._source(path, source)
        try:
            raw = _read(root, path)
            text = raw.decode('utf-8')
            if source == 'vault' and _private_knowledge(text):
                raise KnowledgeError('知识来源引用私人聊天或带私密标记，不可用于自动召回或经验依据')
            lines = text.splitlines()
        except (OSError, UnicodeError) as exc:
            raise KnowledgeError('证据不可读或包含符号链接') from exc
        if not lines:
            raise KnowledgeError('证据内容为空')
        if end_line is None:
            end_line = min(len(lines), start_line + 39) if type(start_line) is int else 0
        if type(start_line) is not int or type(end_line) is not int or not 1 <= start_line <= end_line <= len(lines) or end_line - start_line >= 100:
            raise KnowledgeError('无效的证据行号，最多100行')
        excerpt = '\n'.join(lines[start_line - 1:end_line])
        if len(excerpt) > 12000:
            raise KnowledgeError('证据片段过长，请缩小行范围')
        return {'source': source, 'path': path, 'start_line': start_line, 'end_line': end_line,
                'sha256': _sha(raw), 'excerpt': excerpt}

    def _checked_evidence(self, evidence):
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 12:
            raise KnowledgeError('候选经验必须有1至12份证据')
        checked, seen = [], set()
        for item in evidence:
            if not isinstance(item, dict) or not re.fullmatch(r'[0-9a-f]{64}', str(item.get('sha256', ''))):
                raise KnowledgeError('证据缺少有效SHA256快照')
            snap = self.snapshot_evidence(item.get('path'), item.get('start_line', 1), item.get('end_line'), item.get('source', 'run'))
            if snap['sha256'] != item['sha256'] or ('excerpt' in item and snap['excerpt'] != item['excerpt']):
                raise KnowledgeError('证据已改版，请重新读取后复核')
            key = (snap['source'], snap['path'], snap['start_line'], snap['end_line'])
            if key not in seen:
                checked.append(snap)
                seen.add(key)
        return checked

    @contextmanager
    def _transaction(self):
        with self.lock:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise KnowledgeError('记忆锁不是普通文件')
                fcntl.flock(fd, fcntl.LOCK_EX)
                if self.store_path.is_symlink():
                    raise KnowledgeError('记忆存储不能是符号链接')
                if self.store_path.exists():
                    try:
                        state = json.loads(_read(self.store_path.parent, self.store_path.name, MAX_STORE_BYTES).decode('utf-8'))
                    except (OSError, UnicodeError, ValueError) as exc:
                        raise KnowledgeError('记忆存储损坏或不可读，未覆盖') from exc
                    if not isinstance(state, dict) or state.get('format') != 'knowledge-loop-v1' or not isinstance(state.get('memories'), list):
                        raise KnowledgeError('记忆存储格式无效，未覆盖')
                    for record in state['memories']:
                        if not isinstance(record, dict) or record.get('status') not in STATES or type(record.get('revision')) is not int:
                            raise KnowledgeError('记忆记录无效，未覆盖')
                else:
                    state = {'format': 'knowledge-loop-v1', 'memories': []}
                before = json.dumps(state, sort_keys=True, ensure_ascii=False)
                yield state
                after = json.dumps(state, sort_keys=True, ensure_ascii=False, indent=2) + '\n'
                if before != json.dumps(state, sort_keys=True, ensure_ascii=False):
                    self._write(after)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _write(self, text):
        raw = text.encode('utf-8')
        if len(raw) > MAX_STORE_BYTES:
            raise KnowledgeError('记忆存储达到上限')
        tmp = self.store_path.with_name(self.store_path.name + '.tmp-' + uuid.uuid4().hex)
        fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.store_path)
            parent = os.open(str(self.store_path.parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
        finally:
            if tmp.exists():
                tmp.unlink()

    def _event(self, record, action, note):
        record['revision'] += 1
        record['updated_at'] = _now()
        record.setdefault('history', []).append({'at': record['updated_at'], 'action': action, 'note': note, 'revision': record['revision']})
        record['history'] = record['history'][-50:]

    def _refresh(self, records):
        for record in records:
            if record['status'] != 'active':
                continue
            try:
                checked = self._checked_evidence(record.get('evidence'))
                verification = record.get('verification') or {}
                if not isinstance(verification, dict):
                    raise KnowledgeError('复核记录无效')
                if verification.get('passed') is not True or not verification.get('summary') or verification.get('content_sha256') != _fingerprint(record):
                    raise KnowledgeError('经验内容已变更或缺少有效复核')
            except (KnowledgeError, TypeError, KeyError):
                record.update(status='candidate', stale=True, stale_reason='证据或经验已变更、不可读，须重新提取并复核')
                self._event(record, 'invalidate', record['stale_reason'])

    def create_candidate(self, project_key, title, rule, evidence, origin_run_id=None, conditions=''):
        project_key = _scope(project_key)
        title, rule = _text(title, '标题', 160), _text(rule, '经验内容', 6000)
        conditions = _text(conditions, '适用条件', 2000, required=False)
        if origin_run_id is not None:
            origin_run_id = _text(origin_run_id, '任务来源', 100)
        checked = self._checked_evidence(evidence)
        norm = re.sub(r'\s+', ' ', rule.casefold()).strip()
        dedupe = _sha((project_key + '\0' + norm + '\0' + conditions).encode())
        with self._transaction() as state:
            self._refresh(state['memories'])
            for record in state['memories']:
                if record.get('dedupe') == dedupe:
                    # A stale candidate can receive fresh snapshots, but never
                    # inherit its previous active/reviewed status.
                    if record['status'] == 'candidate' and record.get('stale'):
                        record.update(title=title, evidence=checked, stale=False, stale_reason='', verification=None)
                        self._event(record, 'refresh_candidate', '重新提取证据，等待复核')
                    result = copy.deepcopy(record)
                    result['deduplicated'] = True
                    return result
            record = {'id': uuid.uuid4().hex[:16], 'project_key': project_key, 'title': title,
                      'rule': rule, 'conditions': conditions, 'evidence': checked,
                      'origin_run_id': origin_run_id, 'status': 'candidate', 'stale': False,
                      'revision': 1, 'dedupe': dedupe, 'verification': None,
                      'created_at': _now(), 'updated_at': _now(), 'history': []}
            state['memories'].append(record)
            return copy.deepcopy(record)

    def review(self, memory_id, action, verification=None, expected_revision=None):
        if action not in {'activate', 'reject', 'retire'}:
            raise KnowledgeError('未知复核操作')
        if not isinstance(verification, dict):
            raise KnowledgeError('必须提供复核记录')
        summary = _text(verification.get('summary', ''), '复核说明', 3000)
        reviewer = _text(verification.get('reviewer', 'human'), '复核者', 100)
        with self._transaction() as state:
            record = next((r for r in state['memories'] if r['id'] == memory_id), None)
            if record is None:
                raise KnowledgeError('经验不存在')
            if expected_revision is not None and (type(expected_revision) is not int or record['revision'] != expected_revision):
                raise KnowledgeError('经验版本已变化，请刷新后重试')
            if action == 'activate':
                if record['status'] != 'candidate' or record.get('stale'):
                    raise KnowledgeError('只有证据有效的候选经验可以晋升')
                if verification.get('passed') is not True:
                    raise KnowledgeError('复核未通过，不能晋升')
                self._checked_evidence(record['evidence'])
                record.update(status='active', stale=False, stale_reason='')
                record['verification'] = {'passed': True, 'summary': summary, 'reviewer': reviewer,
                                          'at': _now(), 'content_sha256': _fingerprint(record)}
            elif action == 'reject':
                if record['status'] != 'candidate':
                    raise KnowledgeError('只有候选经验可以驳回，已复核经验请停用')
                record['status'] = 'rejected'
                record['verification'] = {'passed': False, 'summary': summary, 'reviewer': reviewer, 'at': _now()}
            else:
                if record['status'] != 'active':
                    raise KnowledgeError('只有已复核经验可以停用')
                record['status'] = 'retired'
            self._event(record, action, summary)
            return copy.deepcopy(record)

    def list_memories(self, project_key=None):
        if project_key is not None:
            _scope(project_key)
        with self._transaction() as state:
            self._refresh(state['memories'])
            rows = [r for r in state['memories'] if project_key is None or r['project_key'] == project_key]
            return copy.deepcopy(sorted(rows, key=lambda r: (r['updated_at'], r['id']), reverse=True))

    def _paths(self):
        count = 0
        for relative in self.allowed_dirs:
            fd = None
            try:
                fd = os.open(str(self.vault), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                for part in _parts(relative):
                    nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                    os.close(fd)
                    fd = nxt
                names = sorted(os.listdir(fd))
            except OSError:
                names = []
            finally:
                if fd is not None:
                    os.close(fd)
            for name in names:
                if name.endswith('.md') and not name.startswith(('.', '_')):
                    count += 1
                    if count > 600:
                        return
                    yield relative + '/' + name

    @staticmethod
    def _project_matches(text, project_key):
        # Explicit project fields restrict retrieval; generic topic tags remain
        # shared. This intentionally implements only the simple frontmatter
        # subset used by this module, rather than pretending to parse full YAML.
        if not text.startswith('---\n') or '\n---' not in text[4:]:
            return True
        header = text.split('\n---', 1)[0]
        match = re.search(r'(?m)^project(?:_key|_keys|s)?:[ \t]*([^\n]*)', header)
        if not match:
            return True
        values = match.group(1).strip()
        if values:
            tags = re.findall(r'[a-zA-Z0-9_-]+', values)
        else:
            tail = header[match.end():]
            tags = []
            for line in tail.splitlines():
                if not line.strip():
                    continue
                m = re.fullmatch(r'\s*-\s*[\"\x27]?([a-zA-Z0-9_-]+)[\"\x27]?\s*', line)
                if not m:
                    break
                tags.append(m.group(1))
        return project_key in tags or 'shared' in tags

    def search(self, query, project_key, limit=5):
        query = _text(query, '查询', 8000, required=False)
        project_key = _scope(project_key)
        if type(limit) is not int or not 1 <= limit <= 20:
            raise KnowledgeError('检索数量必须为1至20')
        q = _tokens(query)
        minimum_score = 3 if len(q) >= 3 else 1
        results, warnings = [], []
        memories = self.list_memories(project_key)
        if q:
            for path in self._paths():
                try:
                    raw = _read(self.vault, path)
                    text = raw.decode('utf-8')
                    if _private_knowledge(text):
                        warnings.append('已跳过带聊天归档引用、原话表或私密标记的知识页；当前仅检索受限精选范围，非全库检索')
                        continue
                    if not self._project_matches(text, project_key):
                        continue
                    lines = text.splitlines()
                    title = Path(path).stem
                    start = 0
                    if lines and lines[0] == '---':
                        end = next((i for i in range(1, min(len(lines), 100)) if lines[i] == '---'), None)
                        if end is not None:
                            start = end + 1
                    best = None
                    for pos in range(start, len(lines), 14):
                        segment = []
                        length = 0
                        for line in lines[pos:pos + 20]:
                            if len(line) > 12000 or (segment and length + len(line) + 1 > 2400):
                                break
                            segment.append(line)
                            length += len(line) + 1
                        excerpt = '\n'.join(segment)
                        if not excerpt.strip():
                            continue
                        score = _score(q, title, excerpt)
                        if score >= minimum_score and (best is None or score > best['score']):
                            digest = _sha(raw)
                            source_id = _sha((path + ':' + str(pos + 1) + ':' + digest).encode())[:20]
                            best = {'id': source_id, 'source_id': source_id, 'kind': 'knowledge', 'title': title,
                                    'text': excerpt, 'excerpt': excerpt, 'path': path,
                                    'start_line': pos + 1, 'end_line': pos + len(segment),
                                    'sha256': digest, 'score': score, 'source': 'vault'}
                    if best:
                        results.append(best)
                except (OSError, UnicodeError, KnowledgeError):
                    warnings.append('跳过不可读、超限或被排除的知识页')
            for record in memories:
                if record['status'] != 'active' or record.get('stale'):
                    continue
                excerpt = record['rule'] + ('\n适用条件：' + record['conditions'] if record['conditions'] else '')
                score = _score(q, record['title'], excerpt)
                if score >= minimum_score:
                    results.append({'id': 'memory-' + record['id'], 'source_id': 'memory-' + record['id'],
                                    'kind': 'memory', 'memory_id': record['id'], 'title': record['title'],
                                    'text': excerpt, 'excerpt': excerpt, 'path': str(self.store_path),
                                    'start_line': None, 'end_line': None, 'sha256': _fingerprint(record),
                                    'score': score + 1, 'evidence': record['evidence'],
                                    'revision': record['revision'], 'project_key': project_key,
                                    'verification': record['verification']})
        if q and not results:
            warnings.append('精选知识范围内未找到足够匹配的来源；不代表项目没有相关资料')
        results.sort(key=lambda r: (-r['score'], r['id']))
        return {'results': results[:limit], 'method': 'deterministic_lexical_overlap',
                'scope': {'project_key': project_key, 'knowledge_dirs': list(self.allowed_dirs),
                          'max_pages': 600, 'recursive': False, 'memory_status': 'active',
                          'minimum_score': minimum_score,
                          'excluded': ['原始资料', 'Codex 对话', '含聊天归档引用或原话表的派生页', '显式私密页', '子目录', '符号链接', '外部来源']},
                'warnings': list(dict.fromkeys(warnings))}

    def status(self, project_key=None):
        rows = self.list_memories(project_key)
        return {'counts': {state: sum(r['status'] == state for r in rows) for state in sorted(STATES)},
                'stale': sum(bool(r.get('stale')) for r in rows), 'total': len(rows),
                'method': 'deterministic_lexical_overlap', 'knowledge_dirs': list(self.allowed_dirs),
                'note': '受限精选目录词面检索，非全库；排除聊天归档引用、原话表与显式私密页；候选不召回，已复核不代表业务效果已验证'}
