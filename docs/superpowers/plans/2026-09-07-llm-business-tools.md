# LLM Business Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-Action DeepSeek response with controlled multi-tool calls so one message can safely create or update several independent projects.

**Architecture:** A new tool catalog derives JSON schemas from the existing Pydantic business models. DeepSeek may emit multiple registered tool calls; the backend converts each call to an `Action`, validates and saves it through the existing `Service`, and returns either the existing single-draft response or a batch summary. The legacy injected JSON parser remains available for tests and web compatibility while group-message fallback project creation is removed.

**Tech Stack:** Python 3, FastAPI, Pydantic 2, LangChain DeepSeek, SQLite, `unittest`.

**Spec:** `docs/superpowers/specs/2026-09-07-llm-business-tools-design.md`

## Global Constraints

- LLM tools never receive a database connection and never execute SQL, files, network requests, or arbitrary code.
- Every write is represented as the existing `Action` type and saved through `Service.create_draft(..., auto_save=True)`.
- One message permits at most 20 tool calls and 8 model/tool rounds.
- Missing values are omitted; `null` is rejected.
- Different projects are separate tool calls; multiple tasks for the same project use `RecordItem.items`.
- `A角` and main owner are one role; `owner_assignments[].primary` carries primary ownership.
- Parse or validation failure must not create a `待整理：...` project.
- Existing `P0005` is not deleted or rewritten by this implementation.
- This directory has no Git metadata, so commit steps cannot run; each task ends with a passing test checkpoint instead.

---

### Task 1: Business tool catalog and Action conversion

**Files:**
- Create: `backend/app/ai_tools.py`
- Modify: `backend/app/ai.py`
- Test: `backend/tests/test_ai_tools.py`

**Interfaces:**
- Consumes: `Action`, `RecordItem`, `ProjectCreate`, `ProjectPatch`, `MilestoneCreate`, `MilestonePatch`, `ProgressReport`, `StatusChange` from `backend.app.models`.
- Produces: `business_tool_schemas() -> list[dict]`, `action_from_tool_call(name: str, arguments: dict, allowed_project_ids: set[str]) -> Action`, `MAX_TOOL_CALLS = 20`, `MAX_TOOL_ROUNDS = 8`.

- [ ] **Step 1: Write failing schema and conversion tests**

```python
class AIToolTests(unittest.TestCase):
    def test_record_tool_uses_real_record_item_schema(self):
        tool = next(t for t in business_tool_schemas()
                    if t['function']['name'] == 'record_project_item')
        data = tool['function']['parameters']['properties']['data']
        self.assertIn('owner_assignments', data['properties'])
        self.assertIn('items', data['properties'])
        self.assertFalse(data['additionalProperties'])

    def test_tool_call_becomes_existing_action_and_rejects_unknown_ids(self):
        action = action_from_tool_call('record_project_item', {
            'data': {'project_name': '美国宠物医院', 'text': '9月11日交付'}
        }, {'4'})
        self.assertEqual(action.intent, 'record_item')
        self.assertEqual(action.data['project_name'], '美国宠物医院')
        with self.assertRaises(BusinessError):
            action_from_tool_call('edit_project', {
                'project_id': '999', 'data': {'name': '越权修改', 'reason': '测试'}
            }, {'4'})
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_ai_tools -v`

Expected: import failure for missing `backend.app.ai_tools`.

- [ ] **Step 3: Implement the minimal catalog**

```python
TOOL_INTENTS = {
    'record_project_item': ('record_item', RecordItem),
    'create_project': ('create_project', ProjectCreate),
    'edit_project': ('edit_project', ProjectPatch),
    'add_milestone': ('add_milestone', MilestoneCreate),
    'edit_milestone': ('edit_milestone', MilestonePatch),
    'report_progress': ('report_progress', ProgressReport),
    'change_project_status': ('project_status', StatusChange),
    'change_milestone_status': ('milestone_status', StatusChange),
}

def action_from_tool_call(name, arguments, allowed_project_ids):
    require(name in TOOL_INTENTS, '模型调用了未知业务工具')
    intent, model = TOOL_INTENTS[name]
    require(arguments.get('project_id') in allowed_project_ids
            if arguments.get('project_id') else True,
            '模型引用了不可用的项目')
    require(no_null(arguments), '模型工具参数不能包含 null')
    data = model.model_validate(arguments.get('data', {}), strict=True).model_dump(
        mode='json', exclude_unset=True)
    return Action(intent=intent, project_id=arguments.get('project_id'),
                  milestone_id=arguments.get('milestone_id'), data=data)
```

Generate each OpenAI-style function schema from `data_schema(model)`, with `additionalProperties: false` at the wrapper and data levels. Keep query as a zero-write tool with optional `project_id`.

- [ ] **Step 4: Run Task 1 tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_ai_tools backend.tests.test_information -v`

Expected: all tests pass.

---

### Task 2: DeepSeek multi-tool planning loop

**Files:**
- Modify: `backend/app/ai.py`
- Test: `backend/tests/test_deepseek.py`

**Interfaces:**
- Consumes: `business_tool_schemas`, `MAX_TOOL_CALLS`, `MAX_TOOL_ROUNDS`.
- Produces: `invoke_deepseek_tools(prompt: str, accept: Callable[[str, dict], dict]) -> list[dict]` where each returned item contains `name`, normalized `arguments`, and the callback result.

- [ ] **Step 1: Replace the old SDK expectation with a failing multi-call behavior test**

Use `httpx.MockTransport` to return one assistant response with two `tool_calls` followed by a final assistant response without tool calls. Assert the real request body contains the registered `tools`, does not contain `response_format`, and both calls reach the acceptance callback in order.

```python
accepted = []
result = self.invoke_tools([
    tool_call('call-1', 'record_project_item', {'data': {
        'project_name': '北斗创新中心', 'text': '9月15日验收'}}),
    tool_call('call-2', 'record_project_item', {'data': {
        'project_name': '美国宠物医院', 'text': '9月11日交付'}}),
], lambda name, args: accepted.append((name, args)) or {'accepted': True})
self.assertEqual([args['data']['project_name'] for _, args in accepted],
                 ['北斗创新中心', '美国宠物医院'])
```

- [ ] **Step 2: Run the test and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_deepseek.DeepSeekTests.test_native_sdk_emits_multiple_business_tool_calls -v`

Expected: failure because `invoke_deepseek_tools` and tool registration do not exist.

- [ ] **Step 3: Implement the controlled loop**

Build `ChatDeepSeek` with the current model, key, base URL, timeout, retry and disabled-thinking settings, then call `bind_tools(business_tool_schemas())`. For each `AIMessage.tool_calls`, validate the call count before invoking `accept`, append a LangChain `ToolMessage` containing only the sanitized callback result, and stop when the assistant returns no calls. Reject malformed arguments, unknown tool names, more than 20 calls, more than 8 rounds, empty output, and non-`stop`/tool-call finish conditions with `BusinessError`.

- [ ] **Step 4: Add failing boundary tests, then make them pass one at a time**

Cover literal behaviors:

```python
with self.assertRaisesRegex(BusinessError, '20'):
    self.invoke_tools(twenty_one_tool_calls(), accept)
with self.assertRaisesRegex(BusinessError, '未知'):
    self.invoke_tools([tool_call('x', 'drop_database', {})], accept)
```

Also verify secrets and raw SDK exceptions never appear in returned errors.

- [ ] **Step 5: Run Task 2 tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_deepseek -v`

Expected: all tests pass.

---

### Task 3: Parse, save, cache, and authorize batch actions

**Files:**
- Modify: `backend/app/ai.py`
- Modify: `backend/app/service.py`
- Test: `backend/tests/test_ai.py`
- Test: `backend/tests/test_service.py`

**Interfaces:**
- Consumes: normalized tool calls from Task 2 and `Action` conversion from Task 1.
- Produces: `save_action_batch(service, user, actions, source, diagnostics) -> dict` and cached response shape:

```json
{
  "kind": "batch",
  "drafts": [{"id": "...", "status": "confirmed", "result": {"code": "P0006"}}],
  "failures": [{"project_name": "...", "message": "..."}],
  "total": 6,
  "succeeded": 5,
  "failed": 1
}
```

- [ ] **Step 1: Write a failing six-project regression test**

Inject six normalized tool calls into `parse_message`. Use six literal names from the reported message and assert six distinct saved projects, structured A/B ownership, and no project name beginning with `待整理：`.

```python
self.assertEqual(result['kind'], 'batch')
self.assertEqual(result['succeeded'], 6)
self.assertEqual({p['name'] for p in self.service.projects(self.admin)}, {
    '北斗创新中心', '美国宠物医院语音预约', '锐翰科技工厂AI提效',
    '博思智能体', 'WMS仓储管理系统', '公司内部智能体'})
self.assertFalse(any(p['name'].startswith('待整理：')
                     for p in self.service.projects(self.admin)))
```

- [ ] **Step 2: Run the regression and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_ai.AITests.test_one_message_saves_six_independent_projects -v`

Expected: failure because `parse_message` accepts only one serialized Action.

- [ ] **Step 3: Implement batch saving and compatibility behavior**

Allow the parser boundary to return either the legacy JSON string or normalized tool calls. Validate all calls first. Save each valid Action through `Service.create_draft(..., auto_save=True)`, capture public `BusinessError` messages as failures, return the old `kind='draft'` shape for exactly one successful write with no failures, and return `kind='batch'` otherwise.

Update cached-response handling so a repeated message rebuilds each draft through `service.draft(user, id)`; inaccessible drafts are omitted rather than leaking data. Keep the current `messages.id` hash guard and reuse the stored batch response, so repeated callbacks do not call the model or save twice.

- [ ] **Step 4: Write failing partial-failure and replay tests**

```python
self.assertEqual(result['succeeded'], 1)
self.assertEqual(result['failed'], 1)
self.assertEqual(len(self.service.projects(self.admin)), baseline + 1)
replayed = self.replay(request, self.admin)
self.assertEqual(replayed['succeeded'], 1)
self.assertEqual(len(self.service.projects(self.admin)), baseline + 1)
```

Use one valid project and one duplicate/invalid project. Assert the failure is explicit, the valid project remains saved once, and replay does not invoke the planner.

- [ ] **Step 5: Remove automatic `待整理` fallback with a failing group test first**

Change the existing ambiguous/unassigned group-message expectation to assert no project was created and a business error/failure result is returned. Then remove the fallback block in `save_group_message` that constructs `project_name = '待整理：' + source[:95]`.

- [ ] **Step 6: Run Task 3 tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_ai backend.tests.test_service backend.tests.test_information -v`

Expected: all tests pass.

---

### Task 4: Enterprise WeChat batch reply and full regression

**Files:**
- Modify: `backend/app/wecom.py`
- Test: `backend/tests/test_wecom.py`
- Test: `backend/tests/test_api.py`

**Interfaces:**
- Consumes: the batch response from Task 3.
- Produces: a privacy-safe group reply for full success, partial success, and total failure.

- [ ] **Step 1: Write failing WeCom batch reply tests**

```python
reply = self.handle_with_tool_calls(six_project_calls())
self.assertIn('已处理 6 个项目', reply)
self.assertIn('成功保存 6 项', reply)
self.assertNotIn('负责人', reply)

reply = self.handle_with_tool_calls([valid_call(), invalid_call()])
self.assertIn('成功 1 项', reply)
self.assertIn('失败 1 项', reply)
```

Assert that project descriptions, contact details, other project names, stack traces, and credentials do not appear in group replies.

- [ ] **Step 2: Run the WeCom tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m unittest backend.tests.test_wecom.WeComTests.test_group_reply_summarizes_batch_results -v`

Expected: failure because `BotHandler.handle` has no `kind='batch'` branch.

- [ ] **Step 3: Implement the batch reply branch**

Format counts from `total`, `succeeded`, and `failed`. For failures, include only the sanitized tool label/project label already present in the batch result and the public business message. Always include the existing `/#projects` link when at least one operation succeeds; for total failure, state that nothing was saved.

- [ ] **Step 4: Run backend and frontend regression suites**

Run: `.\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v`

Run: `npm test`

Run: `npm run build`

Expected: all Python and frontend tests pass; Vite build completes with only the pre-existing chunk-size warning.

- [ ] **Step 5: Restart and perform a live smoke test**

Restart the backend and WeCom worker using the existing project commands. Send a new, non-destructive test message containing two uniquely named test projects, verify two independent projects and one batch reply, then remove those test records only with explicit user approval. Do not replay the six-project production message and do not modify `P0005` automatically.

