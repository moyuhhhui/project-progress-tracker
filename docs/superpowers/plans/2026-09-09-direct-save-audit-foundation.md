# Direct Save and Audit Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the persisted confirmation workflow while preserving every project, report, and audit relationship through a new operation record and direct-save API.

**Architecture:** Introduce `operations` as the durable provenance and idempotency boundary, first alongside the legacy columns and then as their replacement. Extract the existing proposal-and-save logic into a direct executor used by the web API, AI messages, WeCom, automatic completion, and reminders. Only after all readers and writers use `operation_id` may the old endpoints, UI, models, and tables be removed.

**Tech Stack:** Python 3.13, FastAPI, Pydantic 2, SQLite/WAL, Vue 3, TypeScript 5.7, Element Plus, Node test runner, `unittest`.

**Spec:** `docs/blueprints/2026-09-09-wecom-ai-reliability-blueprint.md`

## Global Constraints

- Do not implement project-name fuzzy matching, unified project-content editing, or `not_started` removal in this plan; those belong to later plans.
- Do not drop `drafts`, `wecom_drafts`, or legacy columns until the migration backup, row-count checks, foreign-key checks, and rollback tests pass.
- Preserve project storage schema version 2 and all existing project IDs, versions, milestone IDs, timestamps, reports, and audit snapshots.
- Every completed write must have exactly one non-empty `operation_id`; browser retries and repeated message callbacks must return the cached operation result without writing again.
- Frontend direct writes continue to enforce the current authentication and project-management rules.
- Incomplete AI input writes no project, report, audit, or intermediate business record.
- Tests must use temporary SQLite databases and mocked AI/WeCom senders; no test may call a paid model or send an external message.
- Before each commit, inspect `git status --short` and stage only files listed by that task.

---

### Task 1: Add the operation ledger with an additive migration

**Files:**
- Modify: `backend/app/store.py`
- Create: `backend/tests/test_operation_migration.py`

**Interfaces:**
- Produces table `operations(id, source, source_message_id, actor_ref, request_hash, status, created_at, result, diagnostics)`.
- Produces `Store.create_operation(db, *, operation_id, source, source_message_id, actor_ref, request_hash, created_at, diagnostics)`, `Store.operation_by_source(db, source, source_message_id)`, and `Store.finish_operation(db, operation_id, status, result)`.
- Temporarily adds nullable `operation_id` to `audit` and `reports`; legacy `draft_id` columns remain until Task 7.

- [ ] **Step 1: Write failing fresh-schema and legacy-migration tests**

```python
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.app.store import Store


def make_legacy_database(path):
    with sqlite3.connect(path) as db:
        db.executescript('''
        CREATE TABLE audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
          user_id TEXT NOT NULL, at TEXT NOT NULL, intent TEXT NOT NULL,
          before_data TEXT, after_data TEXT NOT NULL, draft_id TEXT NOT NULL
        );
        CREATE TABLE reports (
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
          milestone_id TEXT NOT NULL, user_id TEXT NOT NULL, at TEXT NOT NULL,
          data TEXT NOT NULL, draft_id TEXT NOT NULL
        );
        INSERT INTO audit(project_id,user_id,at,intent,before_data,after_data,draft_id)
          VALUES(1,'u1','2026-09-07T10:00:00+08:00','create_project',NULL,'{}','legacy-a');
        INSERT INTO audit(project_id,user_id,at,intent,before_data,after_data,draft_id)
          VALUES(1,'u1','2026-09-07T11:00:00+08:00','report_progress','{}','{}','legacy-b');
        INSERT INTO reports(project_id,milestone_id,user_id,at,data,draft_id)
          VALUES(1,'m1','u1','2026-09-07T11:00:00+08:00','{}','legacy-b');
        ''')

class OperationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / 'tracker.sqlite3'

    def test_fresh_store_has_operation_ledger_and_dual_reference_columns(self):
        store = Store(self.db_path)
        with store.connect() as db:
            operation_columns = {row['name'] for row in db.execute('PRAGMA table_info(operations)')}
            audit_columns = {row['name'] for row in db.execute('PRAGMA table_info(audit)')}
            report_columns = {row['name'] for row in db.execute('PRAGMA table_info(reports)')}
        self.assertEqual(operation_columns, {
            'id', 'source', 'source_message_id', 'actor_ref', 'request_hash',
            'status', 'created_at', 'result', 'diagnostics'})
        self.assertIn('operation_id', audit_columns)
        self.assertIn('operation_id', report_columns)

    def test_legacy_links_are_backfilled_without_changing_history_counts(self):
        make_legacy_database(self.db_path)
        Store(self.db_path)
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT count(*) FROM reports').fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT count(*) FROM operations WHERE source='legacy'"
            ).fetchone()[0], 2)
            self.assertEqual(db.execute(
                'SELECT count(*) FROM audit WHERE operation_id IS NULL'
            ).fetchone()[0], 0)
        backups = list(Path(self.temp.name).glob(
            'tracker.sqlite3.before-operations-*.bak'))
        self.assertEqual(len(backups), 1)
```

`make_legacy_database` must create the exact pre-migration tables used by the current `Store`, insert two legacy IDs into `audit`, and reuse one of them in `reports`.

- [ ] **Step 2: Run the migration tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_operation_migration -v`

Expected: FAIL because `operations` and `operation_id` do not exist.

- [ ] **Step 3: Implement the additive schema and migration**

Add this fresh-install table and index inside `Store.__init__`:

```sql
CREATE TABLE IF NOT EXISTS operations (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_message_id TEXT,
  actor_ref TEXT NOT NULL DEFAULT '',
  request_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  result TEXT,
  diagnostics TEXT NOT NULL DEFAULT '{}'
);
CREATE UNIQUE INDEX IF NOT EXISTS operation_source_message_unique
  ON operations(source, source_message_id)
  WHERE source_message_id IS NOT NULL;
```

After the initial schema script, call `_migrate_operations()`. That method must:

1. Use `PRAGMA table_info` to add nullable `operation_id` columns only when absent.
2. Read distinct non-empty `draft_id` values from `audit` and `reports`.
3. Insert one `source='legacy'`, `status='done'` operation per distinct value using the old ID as the operation ID and the earliest linked audit/report `user_id` as `actor_ref`.
4. Backfill both tables with `operation_id=draft_id`.
5. Verify zero NULL operation links and unchanged audit/report row counts before commit.
6. Create a sibling backup named `tracker.sqlite3.before-operations-<hex>.bak` before the first schema mutation.

Add helpers with these exact signatures:

```python
def create_operation(self, db, *, operation_id, source, source_message_id,
                     actor_ref, request_hash, created_at, diagnostics=None):
    db.execute(
        'INSERT INTO operations VALUES(?,?,?,?,?,?,?,NULL,?)',
        (operation_id, source, source_message_id, actor_ref, request_hash,
         'processing', created_at, encode(diagnostics or {})))

def operation_by_source(self, db, source, source_message_id):
    if source_message_id is None:
        return None
    return db.execute(
        'SELECT * FROM operations WHERE source=? AND source_message_id=?',
        (source, source_message_id)).fetchone()

def finish_operation(self, db, operation_id, status, result):
    db.execute('UPDATE operations SET status=?,result=? WHERE id=?',
               (status, encode(result), operation_id))
```

- [ ] **Step 4: Run Task 1 tests and the storage regression tests**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_operation_migration backend.tests.test_project_storage -v`

Expected: all tests pass and each migration test directory contains exactly one pre-migration backup.

- [ ] **Step 5: Commit Task 1**

```powershell
git add backend/app/store.py backend/tests/test_operation_migration.py
git commit -m "feat: add operation ledger migration"
```

---

### Task 2: Extract a direct, idempotent action executor

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/service.py`
- Modify: `backend/tests/test_service.py`

**Interfaces:**
- Produces `ExecuteActionRequest(action, client_operation_id, expected_version)`.
- Produces `Service._resolve_action(db, actor, action) -> tuple[Action, dict | None]`.
- Produces `Service.execute_action(user, request, *, source, actor_ref='', diagnostics=None) -> dict`.
- Keeps `Service.create_draft` working temporarily by delegating its confirmed save to the same internal persistence function.

- [ ] **Step 1: Write failing direct-execution tests**

```python
def create_request(key, name):
    return ExecuteActionRequest(client_operation_id=key, expected_version=None,
        action=Action(intent='create_project', data={'name': name}))

def test_execute_action_saves_once_and_returns_cached_result(self):
    request = ExecuteActionRequest(
        client_operation_id='browser-create-001',
        expected_version=None,
        action=Action(intent='create_project', data={'name': '直接保存项目'}))
    first = self.service.execute_action(self.admin, request, source='web')
    second = self.service.execute_action(self.admin, request, source='web')
    self.assertEqual(second, first)
    with self.store.connect() as db:
        self.assertEqual(db.execute('SELECT count(*) FROM projects').fetchone()[0], 1)
        self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0], 1)
        self.assertEqual(db.execute('SELECT operation_id FROM audit').fetchone()[0],
                         first['operation_id'])

def test_execute_action_rejects_reused_key_with_different_body(self):
    self.service.execute_action(self.admin, create_request('same-key-001', '甲'), source='web')
    with self.assertRaisesRegex(BusinessError, '操作编号不能用于不同内容'):
        self.service.execute_action(self.admin, create_request('same-key-001', '乙'), source='web')

def test_execute_action_checks_expected_project_version(self):
    saved = self.service.execute_action(self.admin, create_request('create-versioned', '版本项目'), source='web')
    request = ExecuteActionRequest(client_operation_id='edit-versioned', expected_version=99,
        action=Action(intent='edit_project', project_id=saved['project_id'],
                      data={'description': '不应保存', 'reason': '并发测试'}))
    with self.assertRaisesRegex(BusinessError, '项目已被修改'):
        self.service.execute_action(self.admin, request, source='web')
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_service.ServiceTests.test_execute_action_saves_once_and_returns_cached_result -v`

Expected: FAIL because `ExecuteActionRequest` and `execute_action` do not exist.

- [ ] **Step 3: Add the request contract and executor**

Add to `models.py`:

```python
class ExecuteActionRequest(Contract):
    action: Action
    client_operation_id: Annotated[str, Field(min_length=8, max_length=100)]
    expected_version: Annotated[int, Field(strict=True, ge=1)] | None = None
```

In `Service`, extract the body that currently applies `propose`, writes `projects`, `audit`, and optional `reports` into this implementation:

```python
def _persist_action(self, db, actor, action, current, operation_id):
    project = self.propose(db, actor, action, current)
    if current:
        pid, version = int(current['id']), current['version'] + 1
        for field in ('id', 'code', 'version'):
            project.pop(field, None)
        db.execute('UPDATE projects SET version=?,data=? WHERE id=?',
                   (version, encode_project(project), pid))
    else:
        require(action.intent != 'record_item' or not any(
            self.store.project(row)['name'] == project['name']
            for row in db.execute('SELECT * FROM projects')),
            '项目名称冲突，请核对后重试')
        version = 1
        pid = db.execute('INSERT INTO projects(version,data) VALUES(1,?)',
                         (encode_project(project),)).lastrowid
    at = self.clock().isoformat()
    db.execute(
        'INSERT INTO audit(project_id,user_id,at,intent,before_data,after_data,draft_id,operation_id) '
        'VALUES(?,?,?,?,?,?,?,?)',
        (pid, actor['id'], at, action.intent, encode(current) if current else None,
         encode(project), operation_id, operation_id))
    if action.intent == 'report_progress':
        report_data = {**action.data,
            'before_progress': self.find_node(current, action.milestone_id)['progress'],
            'after_progress': self.find_node(project, action.milestone_id)['progress']}
        db.execute(
            'INSERT INTO reports(project_id,milestone_id,user_id,at,data,draft_id,operation_id) '
            'VALUES(?,?,?,?,?,?,?)',
            (pid, action.milestone_id, actor['id'], at, encode(report_data),
             operation_id, operation_id))
    return {'project_id': str(pid), 'code': f'P{pid:04d}', 'version': version,
            'message': '已保存'}
```

During the additive period, fill both `draft_id` and `operation_id` with the operation ID. Task 7 removes the former column and shortens both insert statements.

Import `hashlib` and `hmac`, then implement `execute_action` so the concrete behavior is:

```python
def _resolve_action(self, db, actor, action):
    if action.intent == 'record_item' and not action.project_id:
        name = action.data.get('project_name', '').strip()
        matches = [project for row in db.execute('SELECT * FROM projects')
                   if (project := self.store.project(row))['name'] == name]
        require(all(allowed(actor, project) for project in matches),
                '无法安全关联项目', 403)
        require(len(matches) <= 1, '项目名称重复，请补充项目编号')
        if matches:
            action = action.model_copy(update={'project_id': matches[0]['id']})
    current = (None if action.intent == 'create_project'
               or (action.intent == 'record_item' and not action.project_id)
               else self.get_project(db, action.project_id, actor))
    return action, current

def execute_action(self, user, request, *, source, actor_ref='', diagnostics=None):
    request_body = encode({'action': request.action.model_dump(mode='json'),
                           'expected_version': request.expected_version})
    request_hash = hashlib.sha256(request_body.encode()).hexdigest()
    with self.store.connect(write=True) as db:
        actor = self.fresh_user(db, user)
        resolved_actor_ref = actor_ref or actor['id']
        existing = self.store.operation_by_source(db, source, request.client_operation_id)
        if existing:
            require(hmac.compare_digest(existing['actor_ref'], resolved_actor_ref),
                    '操作记录不存在', 404)
            require(existing['request_hash'] == request_hash,
                    '同一操作编号不能用于不同内容', 409)
            require(existing['status'] == 'done' and existing['result'],
                    '操作正在处理或上次处理中断，请检查项目列表', 409)
            return json.loads(existing['result'])
        operation_id = secrets.token_hex(16)
        self.store.create_operation(db, operation_id=operation_id, source=source,
            source_message_id=request.client_operation_id, actor_ref=resolved_actor_ref,
            request_hash=request_hash, created_at=self.clock().isoformat(),
            diagnostics=diagnostics)
        action, current = self._resolve_action(db, actor, request.action)
        require(current is None or request.expected_version == current['version'],
                '项目已被修改，请刷新后重试', 409)
        result = self._persist_action(db, actor, action, current, operation_id)
        result['operation_id'] = operation_id
        self.store.finish_operation(db, operation_id, 'done', result)
        return result
```

Use the existing compact `encode` helper so the request hash is deterministic and includes `expected_version`.

- [ ] **Step 4: Make the legacy confirmed path delegate to `_persist_action`**

When `_save` confirms a legacy record, ensure an operation exists with `id=draft_id`, `source='legacy'`, then call `_persist_action(db, actor, action, current, draft_id)`. Continue updating the legacy result only for compatibility until Task 7.

- [ ] **Step 5: Run service tests**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_service backend.tests.test_internal_shared -v`

Expected: all tests pass; old confirmed writes and new direct writes both populate `audit.operation_id`.

- [ ] **Step 6: Commit Task 2**

```powershell
git add backend/app/models.py backend/app/service.py backend/tests/test_service.py
git commit -m "feat: execute project actions directly"
```

---

### Task 3: Expose the direct-save API

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_api.py`

**Interfaces:**
- Produces `POST /api/actions` accepting `ExecuteActionRequest` and returning `{operation_id, project_id, code, version, message}`.
- Retains the legacy routes until Task 7 so this task is independently deployable.

- [ ] **Step 1: Write failing API tests**

```python
def test_direct_action_endpoint_saves_and_is_idempotent(self):
    body = {'client_operation_id': 'api-create-001', 'expected_version': None,
            'action': {'intent': 'create_project', 'data': {'name': 'API 直接保存'}}}
    first = self.client.post('/api/actions', json=body, headers=self.headers())
    second = self.client.post('/api/actions', json=body, headers=self.headers())
    self.assertEqual(first.status_code, 200, first.text)
    self.assertEqual(second.json(), first.json())
    self.assertEqual(len(self.client.get('/api/projects', headers=self.headers()).json()['projects']), 1)

def test_display_account_cannot_write_direct_action(self):
    response = self.client.post('/api/actions', json={
        'client_operation_id': 'display-write-001',
        'action': {'intent': 'create_project', 'data': {'name': '禁止'}}
    }, headers=self.headers(self.display))
    self.assertEqual(response.status_code, 403)
```

- [ ] **Step 2: Run the endpoint test and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_api.ApiTests.test_direct_action_endpoint_saves_and_is_idempotent -v`

Expected: FAIL with HTTP 404.

- [ ] **Step 3: Add the route**

```python
@app.post('/api/actions')
def execute_action(body: ExecuteActionRequest, actor=Depends(editor)):
    return service.execute_action(actor, body, source='web')
```

Import `ExecuteActionRequest`; keep request-size middleware and existing authentication unchanged.

- [ ] **Step 4: Run API and service regressions**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_api backend.tests.test_service -v`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add backend/app/main.py backend/tests/test_api.py
git commit -m "feat: add direct action API"
```

---

### Task 4: Make AI messages save directly and retain no incomplete object

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/ai.py`
- Modify: `backend/tests/test_ai.py`
- Modify: `backend/tests/test_information.py`

**Interfaces:**
- `MessageInput` contains only `text` and `client_message_id`.
- Produces `expected_version_for(action: Action, candidates: list[dict]) -> int | None`.
- A complete single action returns `{'kind': 'saved', 'result': {'operation_id': str, 'project_id': str, 'code': str, 'version': int, 'message': str}}`.
- An incomplete or ambiguous parse returns `{'kind': 'needs_input', 'missing_fields': list[str], 'ambiguities': list[str], 'message': str}` and writes no business rows.
- Batch response contains final action results and business failures, never legacy object IDs.

- [ ] **Step 1: Replace persistence-follow-up tests with failing direct-result tests**

```python
def test_incomplete_message_returns_fields_without_persisting_business_record(self):
    parser = lambda _: ParsedMessage(intent='record_item', data={'text': '明天处理'},
                                     missing_fields=['project_name'])
    result = asyncio.run(parse_message(self.service, self.admin,
        MessageInput(text='明天处理', client_message_id='missing-project-001'), parser))
    self.assertEqual(result['kind'], 'needs_input')
    self.assertEqual(result['missing_fields'], ['project_name'])
    with self.store.connect() as db:
        self.assertEqual(db.execute('SELECT count(*) FROM projects').fetchone()[0], 0)
        self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0], 0)

def test_complete_message_returns_saved_result_without_legacy_record(self):
    parser = lambda _: ParsedMessage(intent='create_project', data={'name': 'AI 直接项目'})
    result = asyncio.run(parse_message(self.service, self.admin,
        MessageInput(text='新建 AI 直接项目', client_message_id='ai-create-001'), parser))
    self.assertEqual(result['kind'], 'saved')
    self.assertIn('operation_id', result['result'])
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_ai -v`

Expected: failures reference the old `kind='draft'` and persisted incomplete behavior.

- [ ] **Step 3: Remove follow-up state from the message contract**

Delete `MessageInput.previous_draft_id`. Remove `save_incomplete`, `replace_previous`, and every branch that loads or appends previous source text. Each new message is independently parsed.

- [ ] **Step 4: Route complete actions through `execute_action`**

Include project `version` in every `prompt_context` candidate and add:

```python
def expected_version_for(action, candidates):
    if action.project_id:
        project = next((item for item in candidates
                        if item['id'] == action.project_id), None)
        require(project is not None, '项目不在本次可操作范围', 403)
        return project['version']
    elif action.intent == 'record_item' and action.data.get('project_name'):
        matches = [item for item in candidates
                   if item['name'] == action.data['project_name'].strip()]
        require(len(matches) <= 1, '项目名称重复，请补充项目编号')
        return matches[0]['version'] if matches else None
    return None
```

For a single action use:

```python
saved_results, failures = [], []
for index, action in enumerate(actions):
    try:
        operation_key = hashlib.sha256(
            f'{request.client_message_id}:{index}'.encode()).hexdigest()
        operation_request = ExecuteActionRequest(
            action=action,
            client_operation_id=operation_key,
            expected_version=expected_version_for(action, candidates))
        saved_results.append(service.execute_action(
            user, operation_request, source=channel,
            diagnostics={'model_retries': model_retries, 'batch_index': index}))
    except BusinessError as exc:
        failures.append({'project_name': _action_label(action, index),
                         'message': exc.message})
result = ({'kind': 'saved', 'result': saved_results[0]}
          if len(saved_results) == 1 and not failures
          else {'kind': 'batch', 'results': saved_results, 'failures': failures,
                'recognized_actions': len(actions),
                'saved_actions': len(saved_results),
                'business_failures': len(failures)})
```

For this foundation plan, enumerate `actions` so each batch action gets a stable `:<index>` suffix, preserve the current batch iteration behavior, and return only final saved results and business failures. The later AI-reliability plan will replace iteration with per-project atomic groups and final-plan de-duplication.

- [ ] **Step 5: Make duplicate message reads return cached direct results**

Update the `messages.response` cache branches for `saved`, `needs_input`, `batch`, `query`, and `ignored`. A repeated client message ID with different text remains HTTP 409.

- [ ] **Step 6: Run AI tests**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_ai backend.tests.test_information backend.tests.test_ai_tools backend.tests.test_deepseek -v`

Expected: all tests pass with zero inserts into the legacy tables from AI paths.

- [ ] **Step 7: Commit Task 4**

```powershell
git add backend/app/models.py backend/app/ai.py backend/tests/test_ai.py backend/tests/test_information.py
git commit -m "feat: save AI actions without intermediate records"
```

---

### Task 5: Remove the WeCom follow-up and legacy-link workflow

**Files:**
- Modify: `backend/app/wecom.py`
- Modify: `backend/tests/test_wecom.py`
- Modify: `backend/tests/test_internal_shared.py`

**Interfaces:**
- Consumes AI result kinds `saved`, `needs_input`, `batch`, `query`, and `ignored`.
- Produces no `wecom_drafts` row and no URL fragment pointing to a removed page.

- [ ] **Step 1: Write failing WeCom reply tests**

```python
def group_frame(msgid, text):
    return {'cmd': 'aibot_msg_callback', 'body': {
        'msgid': msgid, 'aibotid': 'test-bot', 'chatid': 'test-group',
        'chattype': 'group', 'from': {'userid': 'employee-one'},
        'msgtype': 'text', 'text': {'content': text}}}

def test_complete_group_message_returns_saved_summary_without_legacy_link(self):
    result = {'kind': 'saved', 'result': {'project_id': '1', 'code': 'P0001',
              'operation_id': 'op-1', 'version': 1, 'message': '已保存'}}
    with patch('backend.app.wecom.ai.parse_message', return_value=result):
        reply = asyncio.run(self.handler.handle(group_frame('wecom-direct-001', '新建项目')))
    self.assertIn('成功保存 1 项', reply)
    self.assertNotIn('#draft=', reply)
    self.assertNotIn('补充 ', reply)

def test_incomplete_group_message_lists_missing_fields_without_writing_link(self):
    result = {'kind': 'needs_input', 'missing_fields': ['project_name'],
              'ambiguities': [], 'message': '请补充所属项目名称'}
    with patch('backend.app.wecom.ai.parse_message', return_value=result):
        reply = asyncio.run(self.handler.handle(group_frame('wecom-missing-001', '明天调研')))
    self.assertIn('请补充所属项目名称', reply)
    self.assertNotIn('#draft=', reply)
```

- [ ] **Step 2: Run WeCom tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_wecom backend.tests.test_internal_shared -v`

Expected: old reply-kind assumptions fail.

- [ ] **Step 3: Simplify the handler**

Delete parsing of `补充 <id>`, all `wecom_drafts` queries/inserts, and all removed-page URLs. Keep `msgid`-based message idempotency, internal shared actor behavior, text limits, and existing error sanitization.

Use these reply semantics:

```python
if result['kind'] == 'saved':
    return f"已识别 1 项安排，成功保存 1 项，失败 0 项。工作台：{self.web_url}/#projects"
if result['kind'] == 'needs_input':
    return result['message'] + '；未保存任何项目或事项。请补充后重新 @机器人发送。'
```

Batch wording must count actions, never projects or model attempts.

- [ ] **Step 4: Run WeCom regressions**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_wecom backend.tests.test_wecom_logging backend.tests.test_internal_shared -v`

Expected: all tests pass and no test queries `wecom_drafts`.

- [ ] **Step 5: Commit Task 5**

```powershell
git add backend/app/wecom.py backend/tests/test_wecom.py backend/tests/test_internal_shared.py
git commit -m "feat: simplify WeCom direct-save replies"
```

---

### Task 6: Switch the web UI to direct writes and remove the obsolete page

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/ActionEditor.vue`
- Delete: `frontend/src/components/DraftPreview.vue`
- Delete: `frontend/src/views/DraftsPage.vue`
- Modify: `frontend/src/views/ProjectsPage.vue`
- Modify: `frontend/src/views/Workspace.vue`
- Modify: `frontend/tests/workspace-refresh.test.mjs`

**Interfaces:**
- Produces TypeScript `SavedAction` and `ExecuteActionRequest` types.
- `ActionEditor` emits `saved: [result: SavedAction]`.
- `Workspace` no longer fetches `/api/drafts` or recognizes `#drafts`/`#draft=<id>`.

- [ ] **Step 1: Add failing source-level UI tests**

Extend `workspace-refresh.test.mjs`:

```javascript
test('workspace contains no obsolete page route or API call', () => {
  const source = readFileSync(new URL('../src/views/Workspace.vue', import.meta.url), 'utf8')
  assert.doesNotMatch(source, /DraftsPage|DraftPreview|\/api\/drafts|#draft/)
})

test('action editor posts a direct operation and emits saved', () => {
  const source = readFileSync(new URL('../src/components/ActionEditor.vue', import.meta.url), 'utf8')
  assert.match(source, /\/api\/actions/)
  assert.match(source, /emit\('saved'/)
  assert.doesNotMatch(source, /emit\('draft'/)
})
```

- [ ] **Step 2: Run the frontend tests and verify RED**

Run: `npm test`

Working directory: `frontend`

Expected: both new source assertions fail.

- [ ] **Step 3: Add direct-save types and update `ActionEditor`**

Add:

```typescript
export interface SavedAction {
  operation_id: string
  project_id: string
  code: string
  version: number
  message: string
}

export interface ExecuteActionRequest {
  client_operation_id: string
  expected_version: number | null
  action: Action
}
```

Replace the old request with:

```typescript
const clientOperationId = ref(crypto.randomUUID())

const request: ExecuteActionRequest = {
  client_operation_id: clientOperationId.value,
  expected_version: props.project?.version ?? null,
  action,
}
const saved = await api<SavedAction>('/api/actions', 'POST', request)
emit('saved', saved)
emit('close')
```

Create the UUID once when the dialog component is mounted and reuse it if the HTTP call is retried while the dialog remains open.

- [ ] **Step 4: Remove page state and refresh after direct saves**

Delete the two obsolete Vue files. Remove their imports, state, navigation item, count, hash parsing, dialog, API fetch, and cancel handlers from `Workspace`. Change `ProjectsPage` and `Workspace` event wiring from `draft` to `saved`; after `saved`, call `refresh()` and keep the project section active.

- [ ] **Step 5: Run frontend tests and build**

Run: `npm test`

Run: `npm run build`

Working directory: `frontend`

Expected: tests pass; Vue/TypeScript compilation succeeds; generated bundle contains no route label or `/api/drafts` string.

- [ ] **Step 6: Commit Task 6**

```powershell
git add frontend/src/types.ts frontend/src/components/ActionEditor.vue frontend/src/components/DraftPreview.vue frontend/src/views/DraftsPage.vue frontend/src/views/ProjectsPage.vue frontend/src/views/Workspace.vue frontend/tests/workspace-refresh.test.mjs
git commit -m "feat: remove obsolete page and save actions directly"
```

---

### Task 7: Move automatic system writes to operation IDs

**Files:**
- Modify: `backend/app/reminders.py`
- Modify: `backend/app/service.py`
- Modify: `backend/tests/test_reminders.py`

**Interfaces:**
- Produces `Service.record_system_operation(db, *, source, action, project_id, before, after) -> str`.
- Automatic completion writes an operation before its audit row.

- [ ] **Step 1: Extend the existing automatic-completion test with a failing provenance assertion**

In `test_worker_auto_completes_untouched_item_at_due_hour_and_audits_system_source`, extend the existing database query and assertions:

```python
with self.store.connect() as db:
    audit = db.execute(
        'SELECT user_id,intent,before_data,after_data,operation_id '
        'FROM audit ORDER BY id DESC LIMIT 1').fetchone()
    operation = db.execute(
        'SELECT source,status FROM operations WHERE id=?',
        (audit['operation_id'],)).fetchone()
self.assertEqual(tuple(operation), ('system:auto-complete', 'done'))
self.assertEqual((audit['user_id'], audit['intent']),
                 ('system:auto-complete', 'milestone_status'))
```

- [ ] **Step 2: Run the focused test and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_reminders -v`

Expected: the new assertion fails because automatic completion does not create an operation.

- [ ] **Step 3: Implement the system-operation helper**

```python
def record_system_operation(self, db, *, source, action, project_id, before, after):
    operation_id = secrets.token_hex(16)
    request_hash = hashlib.sha256(encode({
        'source': source, 'action': action, 'project_id': str(project_id),
        'before': before, 'after': after}).encode()).hexdigest()
    self.store.create_operation(db, operation_id=operation_id, source=source,
        source_message_id=None, actor_ref=source, request_hash=request_hash,
        created_at=self.clock().isoformat(), diagnostics={})
    self.store.finish_operation(db, operation_id, 'done', {
        'project_id': str(project_id), 'intent': action})
    return operation_id
```

Call it from automatic completion with `source='system:auto-complete'` and write the returned ID into both additive columns.

- [ ] **Step 4: Run reminder and service tests**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_reminders backend.tests.test_service -v`

Expected: all tests pass.

- [ ] **Step 5: Commit Task 7**

```powershell
git add backend/app/reminders.py backend/app/service.py backend/tests/test_reminders.py
git commit -m "feat: audit automatic writes with operations"
```

---

### Task 8: Remove the legacy backend workflow and finalize the schema

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/service.py`
- Modify: `backend/app/store.py`
- Modify: `backend/tests/browser_fixture.py`
- Modify: `backend/tests/test_ai.py`
- Modify: `backend/tests/test_api.py`
- Modify: `backend/tests/test_information.py`
- Modify: `backend/tests/test_internal_shared.py`
- Modify: `backend/tests/test_operation_migration.py`
- Modify: `backend/tests/test_service.py`
- Modify: `backend/tests/test_wecom.py`

**Interfaces:**
- Final `audit` and `reports` contain non-null `operation_id` foreign keys and no `draft_id`.
- Final schema contains no `drafts` or `wecom_drafts` table.
- Runtime code exposes only `/api/actions` and `/api/messages` for writes.

- [ ] **Step 1: Add the failing final-schema test**

```python
def test_final_schema_has_no_legacy_tables_or_columns(self):
    Store(self.db_path)
    with sqlite3.connect(self.db_path) as db:
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        audit_columns = {row[1] for row in db.execute('PRAGMA table_info(audit)')}
        report_columns = {row[1] for row in db.execute('PRAGMA table_info(reports)')}
        self.assertNotIn('drafts', tables)
        self.assertNotIn('wecom_drafts', tables)
        self.assertNotIn('draft_id', audit_columns)
        self.assertNotIn('draft_id', report_columns)
        self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
```

- [ ] **Step 2: Run the final-schema test and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_operation_migration.OperationMigrationTests.test_final_schema_has_no_legacy_tables_or_columns -v`

Expected: FAIL because the additive schema still contains legacy tables and columns.

- [ ] **Step 3: Remove backend routes and service contracts**

Delete all `/api/drafts` routes, `create_draft`, `draft`, `confirm`, `cancel`, `_save`, `replace_previous`, `MessageInput.previous_draft_id`, and legacy response branches. Shorten `_persist_action` so its audit and report inserts contain only `operation_id`.

- [ ] **Step 4: Port each affected test fixture to direct execution**

Use `ExecuteActionRequest` plus `Service.execute_action` in service/AI tests and `POST /api/actions` in API/browser fixtures. Preserve assertions for authorization, idempotency, project history, schema rejection, internal shared access, and member assignment. Delete only assertions whose sole subject was listing, confirming, cancelling, expiring, or supplementing the removed intermediate object.

- [ ] **Step 5: Rebuild final SQLite tables safely**

In one `BEGIN IMMEDIATE` migration after creating a backup:

```sql
CREATE TABLE audit_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  user_id TEXT NOT NULL,
  at TEXT NOT NULL,
  intent TEXT NOT NULL,
  before_data TEXT,
  after_data TEXT NOT NULL,
  operation_id TEXT NOT NULL REFERENCES operations(id)
);
INSERT INTO audit_new SELECT id,project_id,user_id,at,intent,before_data,after_data,operation_id FROM audit;

CREATE TABLE reports_new (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL,
  milestone_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  at TEXT NOT NULL,
  data TEXT NOT NULL,
  operation_id TEXT NOT NULL REFERENCES operations(id)
);
INSERT INTO reports_new SELECT id,project_id,milestone_id,user_id,at,data,operation_id FROM reports;
```

Capture audit/report counts before the copy and compare them with the new tables. After both counts match, execute:

```sql
DROP TABLE audit;
ALTER TABLE audit_new RENAME TO audit;
DROP TABLE reports;
ALTER TABLE reports_new RENAME TO reports;
DROP TABLE IF EXISTS wecom_drafts;
DROP TABLE IF EXISTS drafts;
```

Run `PRAGMA foreign_key_check` before commit. A count or foreign-key failure must raise and roll back the transaction.

- [ ] **Step 6: Prove no runtime references remain**

Run: `rg -n "draft|Draft|previous_draft" backend/app frontend/src -g "!store.py"`

Expected: no results. `store.py` remains excluded because deployed databases still need the one-time legacy table migration.

- [ ] **Step 7: Run all backend and frontend tests**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v`

Run: `npm test`

Run: `npm run build`

Working directory for the last two commands: `frontend`

Expected: all commands exit 0.

- [ ] **Step 8: Commit Task 8**

```powershell
git add backend/app/main.py backend/app/models.py backend/app/service.py backend/app/store.py backend/tests/browser_fixture.py backend/tests/test_ai.py backend/tests/test_api.py backend/tests/test_information.py backend/tests/test_internal_shared.py backend/tests/test_operation_migration.py backend/tests/test_service.py backend/tests/test_wecom.py
git commit -m "refactor: remove legacy confirmation backend"
```

---

### Task 9: Document and verify the deployable foundation

**Files:**
- Modify: `README.md`
- Modify: `docs/blueprints/2026-09-09-wecom-ai-reliability-blueprint.md` only to mark Plan 1 acceptance evidence, without changing approved requirements.

**Interfaces:**
- Documents `POST /api/actions`, operation idempotency, backup naming, direct AI behavior, and removed URLs.

- [ ] **Step 1: Update operator documentation**

Replace old usage text with concrete behavior:

```text
网页新增或修改会直接保存正式项目数据。每次提交携带客户端操作编号；网络重试复用同一编号，不会重复写入。
企业微信信息完整时直接保存；信息不足时只回复需要补充的字段，不保留待确认记录。
旧的待确认页面、深链接和接口已经移除。
```

Document the automatic `.before-operations-<hex>.bak` backup and state that deployment must retain it until row-count and history checks complete.

- [ ] **Step 2: Run the complete verification matrix**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v`

Run: `npm test`

Run: `npm run build`

Working directory for the last two commands: `frontend`

Run: `rg -n "draft|Draft|previous_draft|#draft|/api/drafts" backend/app frontend/src README.md -g "!store.py"`

Expected: backend tests pass, frontend tests pass, build succeeds, and the final search returns no runtime/documentation matches outside the required one-time migration in `store.py`.

- [ ] **Step 3: Inspect migration evidence**

Run a migration test database and verify:

```powershell
.\.venv\Scripts\python.exe -m unittest backend.tests.test_operation_migration -v
```

Expected: audit/report counts are unchanged, every link resolves to `operations`, foreign-key check is empty, rollback test passes, and a backup file exists.

- [ ] **Step 4: Commit Task 9**

```powershell
git add README.md docs/blueprints/2026-09-09-wecom-ai-reliability-blueprint.md
git commit -m "docs: document direct-save operation workflow"
```

- [ ] **Step 5: Record the checkpoint before Plan 2**

Run: `git status --short`

Expected: clean working tree. Record the backend test count, frontend test count, build result, and migration backup path in the implementation handoff. Do not begin the unified project-content editor until these results are present.
