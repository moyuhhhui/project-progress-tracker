import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from backend.app.ai import (SYSTEM, TOOL_SYSTEM, expected_version_for,
                            match_project_candidate, parse_message, prompt_context)
from backend.app.models import Action, MessageInput, ParsedMessage
from backend.app.service import BusinessError, Service, TZ
from backend.app.store import Store


class AIDirectSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'direct-save.sqlite3')
        self.service = Service(self.store, lambda: datetime(2026, 9, 4, 10, tzinfo=TZ))
        self.admin = self.store.add_user('管理员', 'admin')

    def test_incomplete_message_returns_fields_without_persisting_business_record(self):
        parser = lambda _: ParsedMessage(
            intent='record_item', data={'text': '明天处理'}, missing_fields=['project_name'])

        result = asyncio.run(parse_message(
            self.service, self.admin,
            MessageInput(text='明天处理', client_message_id='missing-project-001'), parser))

        self.assertEqual(result['kind'], 'needs_input')
        self.assertEqual(result['missing_fields'], ['project_name'])
        with self.store.connect() as db:
            for table in ('projects', 'audit', 'reports', 'drafts', 'operations', 'wecom_drafts'):
                with self.subTest(table=table):
                    self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_complete_message_returns_saved_result_without_legacy_record(self):
        parser = lambda _: ParsedMessage(intent='create_project', data={'name': 'AI 直接项目'})

        result = asyncio.run(parse_message(
            self.service, self.admin,
            MessageInput(text='新建 AI 直接项目', client_message_id='ai-create-001'), parser))

        self.assertEqual(result['kind'], 'saved')
        self.assertIn('operation_id', result['result'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM drafts').fetchone()[0], 0)

    def test_trip_alias_and_optional_people_save_as_one_continuous_item(self):
        self.service.clock = lambda: datetime(2026, 9, 23, 10, tzinfo=TZ)
        draft = self.service.create_draft(self.admin, Action(
            intent='create_project', data={'name': '孝感锐翰科技工厂'}))
        project_id = self.service.confirm(self.admin, draft['id'])['project_id']
        parser = lambda _: ParsedMessage(intent='record_item', data={
            'project_name': '锐翰科技',
            'text': '两人一起出差，下周一到下周四',
            'title': '安排出差',
            'time_text': '下周一到下周四',
            'start_date': '2026-09-28',
            'due_date': '2026-10-01',
        })

        result = asyncio.run(parse_message(
            self.service, self.admin,
            MessageInput(text='锐翰科技，两人一起出差，下周一到下周四',
                         client_message_id='trip-alias-001'), parser))

        self.assertEqual(result['kind'], 'saved')
        projects = self.service.projects(self.admin)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]['id'], project_id)
        self.assertEqual(len(projects[0]['milestones']), 1)
        item = projects[0]['milestones'][0]
        self.assertEqual(item['start_date'], '2026-09-28')
        self.assertEqual(item['due_date'], '2026-10-01')
        self.assertIsNone(item['owner_id'])

    def test_prompts_do_not_require_optional_trip_details(self):
        for system in (SYSTEM, TOOL_SYSTEM):
            with self.subTest(system=system[:20]):
                self.assertIn('负责人姓名', system)
                self.assertIn('出差目的', system)
                self.assertIn('不按天', system)

    def test_production_tool_call_without_project_returns_needs_input_without_business_writes(self):
        def invoke_tools(_, accept):
            accept('record_project_item', {'data': {'text': '明天处理'}})

        request = MessageInput(text='明天处理', client_message_id='tool-missing-project')
        with patch('backend.app.ai.configured', return_value=True), \
             patch('backend.app.ai.invoke_deepseek_tools', side_effect=invoke_tools):
            result = asyncio.run(parse_message(self.service, self.admin, request))

        self.assertEqual(result['kind'], 'needs_input')
        self.assertEqual(result['missing_fields'], ['project_name'])
        self.assertEqual(result['ambiguities'], [])
        with self.store.connect() as db:
            for table in ('projects', 'audit', 'reports', 'drafts', 'operations', 'wecom_drafts'):
                with self.subTest(table=table):
                    self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_production_clarification_tool_returns_ambiguities_without_business_writes(self):
        test = self

        class FakeRootClient:
            @staticmethod
            def close():
                pass

        class FakeModel:
            root_client = FakeRootClient()

            def __init__(self):
                self.round = 0

            def bind_tools(self, tools):
                names = {tool['function']['name'] for tool in tools}
                test.assertIn('request_clarification', names)
                return self

            def invoke(self, _):
                self.round += 1
                if self.round == 1:
                    return type('Response', (), {
                        'response_metadata': {'finish_reason': 'tool_calls'},
                        'tool_calls': [{'id': 'clarify-1', 'name': 'request_clarification', 'args': {
                            'missing_fields': [],
                            'ambiguities': ['项目名称对应多个候选'],
                        }}],
                        'content': '',
                    })()
                return type('Response', (), {
                    'response_metadata': {'finish_reason': 'stop'},
                    'tool_calls': [],
                    'content': 'DONE',
                })()

        request = MessageInput(text='更新这个项目', client_message_id='tool-ambiguous-project')
        with patch('backend.app.ai.configured', return_value=True), \
             patch.dict('os.environ', {'DEEPSEEK_MODEL': 'test-model', 'DEEPSEEK_API_KEY': 'test-key'}), \
             patch('langchain_deepseek.ChatDeepSeek', return_value=FakeModel()):
            result = asyncio.run(parse_message(self.service, self.admin, request))

        self.assertEqual(result['kind'], 'needs_input')
        self.assertEqual(result['missing_fields'], [])
        self.assertEqual(result['ambiguities'], ['项目名称对应多个候选'])
        with self.store.connect() as db:
            for table in ('projects', 'audit', 'reports', 'drafts', 'operations', 'wecom_drafts'):
                with self.subTest(table=table):
                    self.assertEqual(db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_replay_recovers_committed_single_action_after_response_cache_interruption(self):
        request = MessageInput(text='新建中断恢复项目', client_message_id='recover-committed-action')
        raw = json.dumps({'intent': 'create_project', 'data': {'name': '中断恢复项目'}})
        execute_action = self.service.execute_action
        committed = {}

        def commit_then_stop(*args, **kwargs):
            committed['result'] = execute_action(*args, **kwargs)
            raise SystemExit('模拟业务提交后进程退出')

        with patch.object(self.service, 'execute_action', side_effect=commit_then_stop), \
             self.assertRaises(SystemExit):
            asyncio.run(parse_message(self.service, self.admin, request, lambda _: raw))

        restarted = Service(Store(self.store.path), self.service.clock)
        replayed = asyncio.run(parse_message(
            restarted, self.admin, request,
            lambda _: self.fail('重放不应再次调用模型或执行业务写入')))

        self.assertEqual(replayed, {'kind': 'saved', 'result': committed['result']})
        with self.assertRaises(BusinessError) as changed:
            asyncio.run(parse_message(
                restarted, self.admin,
                MessageInput(text='不同内容', client_message_id=request.client_message_id),
                lambda _: self.fail('冲突消息不应调用模型')))
        self.assertEqual(changed.exception.status, 409)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM projects').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0], 1)
            message = db.execute('SELECT status,response FROM messages').fetchone()
            self.assertEqual(message['status'], 'done')
            self.assertIsNotNone(message['response'])

    def test_replay_does_not_misreport_partially_committed_batch_as_saved(self):
        request = MessageInput(text='新建两个项目', client_message_id='recover-partial-batch')
        calls = [
            {'name': 'create_project', 'arguments': {'data': {'name': '批次项目一'}}},
            {'name': 'create_project', 'arguments': {'data': {'name': '批次项目二'}}},
        ]
        execute_action = self.service.execute_action

        def commit_then_stop(*args, **kwargs):
            execute_action(*args, **kwargs)
            raise SystemExit('模拟批次首项提交后进程退出')

        with patch.object(self.service, 'execute_action', side_effect=commit_then_stop), \
             self.assertRaises(SystemExit):
            asyncio.run(parse_message(self.service, self.admin, request, lambda _: calls))

        restarted = Service(Store(self.store.path), self.service.clock)
        with self.assertRaises(BusinessError) as caught:
            asyncio.run(parse_message(
                restarted, self.admin, request,
                lambda _: self.fail('部分批次重放不应再次调用模型或误报完成')))

        self.assertEqual(caught.exception.status, 409)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM projects').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM operations').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0], 1)


class AITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'test.sqlite3')
        self.at = datetime(2026, 9, 4, 10, tzinfo=TZ)
        self.service = Service(self.store, lambda: self.at)
        self.admin = self.store.add_user('管理员', 'admin')
        self.member = self.store.add_user('成员')
        self.outsider = self.store.add_user('外部成员')
        draft = self.service.create_draft(self.admin, Action(intent='create_project', data={
            'name': '保密展厅', 'owner_id': self.admin['id'], 'member_ids': [self.member['id']],
            'start_date': '2026-09-01', 'due_date': '2026-09-30',
            'milestones': [{'name': '采购', 'criterion': '验收入库', 'owner_id': self.admin['id'],
                            'start_date': '2026-09-01', 'due_date': '2026-09-20'}]}))
        self.pid = self.service.confirm(self.admin, draft['id'])['project_id']
        self.nid = self.service.projects(self.admin)[0]['milestones'][0]['id']

    def request(self, intent='query', user=None, key='message-0001', data=None, **fields):
        request = MessageInput(text='查询保密展厅', client_message_id=key, **fields)
        raw = json.dumps({'intent': intent, 'project_id': self.pid, 'data': data or {}})
        return request, asyncio.run(parse_message(self.service, user or self.member, request, lambda _: raw))

    def revoke_membership(self):
        draft = self.service.create_draft(self.admin, Action(intent='edit_project', project_id=self.pid,
                   data={'member_ids': [], 'reason': '项目人员调整'}))
        self.service.confirm(self.admin, draft['id'])

    def replay(self, request, user=None):
        def unexpected(_):
            self.fail('重复消息不应重新调用模型')
        return asyncio.run(parse_message(self.service, user or self.member, request, unexpected))

    def test_message_input_rejects_followup_state(self):
        with self.assertRaises(ValueError):
            MessageInput(text='补齐信息', client_message_id='followup-state-01',
                         previous_draft_id='legacy-draft-id')

    def test_saved_message_replay_after_restart_returns_cached_result_once(self):
        request = MessageInput(text='创建项目', client_message_id='auto-create-01')
        raw = json.dumps({'intent': 'create_project', 'data': {'name': '新立项'}})

        result = asyncio.run(parse_message(self.service, self.admin, request, lambda _: raw))
        restarted = Service(Store(self.store.path), lambda: self.at)
        replayed = asyncio.run(parse_message(restarted, self.admin, request,
            lambda _: self.fail('重复消息不应重新调用模型')))

        self.assertEqual(result['kind'], 'saved')
        self.assertEqual(replayed, result)
        with self.store.connect() as db:
            self.assertEqual(db.execute(
                "SELECT count(*) FROM projects WHERE json_extract(data,'$.project_info.name')='新立项'"
            ).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT count(*) FROM drafts').fetchone()[0], 1)

    def test_concurrent_updates_use_prompt_candidate_version(self):
        raw = json.dumps({'intent': 'edit_project', 'project_id': self.pid,
                          'data': {'name': '并发立项', 'reason': '更正'}})
        barrier = threading.Barrier(2)

        def parser(_):
            barrier.wait(timeout=5)
            return raw

        async def concurrent():
            return await asyncio.gather(*(parse_message(self.service, self.admin,
                MessageInput(text='更正项目名称', client_message_id=f'concurrent-{i}'), parser)
                for i in range(2)))

        results = asyncio.run(concurrent())
        self.assertEqual(sum(result['kind'] == 'saved' for result in results), 1)
        failed = next(result for result in results if result['kind'] == 'batch')
        self.assertEqual(failed['business_failures'], 1)
        self.assertIn('已被修改', failed['failures'][0]['message'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM drafts').fetchone()[0], 1)
        self.assertEqual(self.service.projects(self.admin)[0]['version'], 2)

    def test_cached_query_filters_revoked_projects(self):
        request, result = self.request()
        self.assertEqual(result['projects'][0]['name'], '保密展厅')
        self.revoke_membership()
        self.assertEqual(self.replay(request)['projects'], [])

    def test_cached_query_reads_latest_authorized_data(self):
        request, _ = self.request()
        draft = self.service.create_draft(self.admin, Action(intent='edit_project', project_id=self.pid,
                   data={'name': '更新名称', 'reason': '更正'}))
        self.service.confirm(self.admin, draft['id'])
        self.assertEqual(self.replay(request)['projects'][0]['name'], '更新名称')

    def test_cached_needs_input_result_does_not_depend_on_legacy_record(self):
        request = MessageInput(text='调整项目', client_message_id='draft-00001')
        raw = json.dumps({'intent': 'edit_project', 'project_id': self.pid, 'missing_fields': ['reason']})
        result = asyncio.run(parse_message(self.service, self.member, request, lambda _: raw))
        self.assertEqual(result['kind'], 'needs_input')
        self.revoke_membership()
        self.assertEqual(self.replay(request), result)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM drafts').fetchone()[0], 2)

    def test_same_message_key_different_text_is_conflict(self):
        self.request()
        with self.assertRaises(BusinessError) as caught:
            self.replay(MessageInput(text='不同内容', client_message_id='message-0001'))
        self.assertEqual(caught.exception.status, 409)

    def test_invalid_model_output_never_writes_project_or_leaks_sdk_error(self):
        for i, output in enumerate(['not json', '{"intent":"query","sql":"DROP TABLE users"}']):
            with self.subTest(output=output):
                request = MessageInput(text='输入', client_message_id=f'invalid-{i}')
                with self.assertRaises(BusinessError):
                    asyncio.run(parse_message(self.service, self.member, request, lambda _: output))
        self.assertEqual(self.service.projects(self.admin)[0]['version'], 1)

    def test_invalid_fields_fail_schema_before_creating_draft(self):
        request = MessageInput(text='汇报', client_message_id='invalid-data')
        raw = json.dumps({'intent': 'report_progress', 'project_id': self.pid,
                          'milestone_id': self.nid, 'data': {'summary': '进展', 'progress': 101}})
        with self.assertRaises(BusinessError):
            asyncio.run(parse_message(self.service, self.admin, request, lambda _: raw))
        self.assertEqual(self.service.projects(self.admin)[0]['version'], 1)

    def test_group_project_owners_are_stored_as_structured_assignments(self):
        self.service.internal_shared = True
        request = MessageInput(text='美国宠物医院，小柯为A角和主要负责人，小朱为B角，今天确定开发进度',
                               client_message_id='structured-owners')
        raw = json.dumps({'intent': 'record_item', 'data': {
            'project_name': '美国宠物医院', 'text': '今天确定开发进度',
            'owner_assignments': [
                {'name': '小柯', 'role': 'A角', 'primary': True},
                {'name': '小朱', 'role': 'B角', 'primary': False},
            ]}})
        result = asyncio.run(parse_message(self.service, self.admin, request, lambda _: raw, channel='wecom'))
        self.assertEqual(result['kind'], 'saved')
        project = next(p for p in self.service.projects(self.admin) if p['name'] == '美国宠物医院')
        self.assertEqual(project['owner_assignments'], [
            {'name': '小柯', 'role': 'A角', 'primary': True},
            {'name': '小朱', 'role': 'B角', 'primary': False},
        ])
        self.assertEqual(project['owner_name'], '小柯（A角）、小朱（B角）')
        with self.store.connect() as db:
            stored = self.store.project(db.execute('SELECT * FROM projects WHERE id=?', (project['id'],)).fetchone())
        self.assertEqual(stored['owner_assignments'], project['owner_assignments'])

    def test_one_message_saves_six_independent_projects(self):
        self.service.internal_shared = True
        names = [
            '北斗创新中心', '美国宠物医院语音预约', '锐翰科技工厂AI提效',
            '博思智能体', 'WMS仓储管理系统', '公司内部智能体',
        ]
        calls = [
            {'name': 'record_project_item', 'arguments': {'data': {
                'project_name': names[0], 'text': '9月15日交付验收',
                'owner_name': '张毅', 'time_text': '9月15日', 'due_date': '2026-09-15'}}},
            {'name': 'record_project_item', 'arguments': {'data': {
                'project_name': names[1], 'text': '9月11日交付',
                'owner_assignments': [
                    {'name': '小柯', 'role': 'A角', 'primary': True},
                    {'name': '小朱', 'role': 'B角', 'primary': False}],
                'time_text': '9月11日', 'due_date': '2026-09-11'}}},
            {'name': 'record_project_item', 'arguments': {'data': {
                'project_name': names[2], 'text': '下周安排调研计划', 'time_text': '下周',
                'owner_assignments': [
                    {'name': '张毅', 'role': 'A角', 'primary': True},
                    {'name': '小朱', 'role': 'A角', 'primary': True}]}}},
            {'name': 'record_project_item', 'arguments': {'data': {
                'project_name': names[3], 'text': '10月完成方案与预算', 'time_text': '10月',
                'owner_assignments': [
                    {'name': '小柯', 'role': 'A角', 'primary': True},
                    {'name': '小朱', 'role': 'B角', 'primary': False}]}}},
            {'name': 'record_project_item', 'arguments': {'data': {
                'project_name': names[4], 'text': '9月15日完成开源基座验证',
                'time_text': '9月15日', 'due_date': '2026-09-15',
                'owner_assignments': [
                    {'name': '张毅', 'role': 'A角', 'primary': True},
                    {'name': '小柯', 'role': 'B角', 'primary': False}]}}},
            {'name': 'record_project_item', 'arguments': {'data': {
                'project_name': names[5], 'text': '尽快完成项目管理看板', 'time_text': '尽快',
                'owner_assignments': [
                    {'name': '小朱', 'role': '搭档', 'primary': False},
                    {'name': '小杨', 'role': '搭档', 'primary': False}]}}},
        ]
        request = MessageInput(text='六个项目的完整安排', client_message_id='six-project-message')

        result = asyncio.run(parse_message(
            self.service, self.admin, request, lambda _: calls, channel='wecom'))

        self.assertEqual(result['kind'], 'batch')
        self.assertEqual((result['recognized_actions'], result['saved_actions'], result['business_failures']),
                         (6, 6, 0))
        self.assertEqual(len(result['results']), 6)
        self.assertTrue(all('operation_id' in item for item in result['results']))
        self.assertNotIn('drafts', result)
        projects = self.service.projects(self.admin)
        self.assertEqual(len(projects), 7)
        saved = [project for project in projects if project['name'] in names]
        self.assertEqual({project['name'] for project in saved}, set(names))
        self.assertFalse(any(project['name'].startswith('待整理：') for project in projects))
        owners = next(project for project in saved
                      if project['name'] == '美国宠物医院语音预约')['owner_assignments']
        self.assertEqual(owners, [
            {'name': '小柯', 'role': 'A角', 'primary': True},
            {'name': '小朱', 'role': 'B角', 'primary': False},
        ])

    def test_batch_reports_partial_failure_and_replay_does_not_save_again(self):
        project = self.service.projects(self.admin)[0]
        self.service.create_draft(self.admin, Action(
            intent='project_status', project_id=project['id'],
            data={'status': 'completed', 'reason': '已验收'}), auto_save=True)
        calls = [
            {'name': 'create_project', 'arguments': {'data': {'name': '批量新增项目'}}},
            {'name': 'record_project_item', 'arguments': {
                'project_id': project['id'], 'data': {'text': '再记录一项安排'}}},
        ]
        request = MessageInput(text='同时新增两个项目', client_message_id='partial-batch-message')

        result = asyncio.run(parse_message(self.service, self.admin, request, lambda _: calls))

        self.assertEqual(result['kind'], 'batch')
        self.assertEqual((result['recognized_actions'], result['saved_actions'], result['business_failures']),
                         (2, 1, 1))
        self.assertIn('先恢复项目', result['failures'][0]['message'])
        self.assertEqual(len(self.service.projects(self.admin)), 2)

        replayed = self.replay(request, self.admin)
        self.assertEqual(replayed, result)
        self.assertEqual(len(self.service.projects(self.admin)), 2)

    def test_model_exception_is_sanitized_and_failed_message_cannot_repeat(self):
        request = MessageInput(text='输入', client_message_id='failure-001')
        def broken(_):
            raise RuntimeError('secret-test-key')
        with self.assertRaises(BusinessError) as caught:
            asyncio.run(parse_message(self.service, self.member, request, broken))
        self.assertEqual(caught.exception.status, 502)
        self.assertNotIn('secret-test-key', str(caught.exception))
        with self.assertRaises(BusinessError) as repeat:
            self.replay(request)
        self.assertEqual(repeat.exception.status, 409)

    def test_timeout_is_safe(self):
        request = MessageInput(text='输入', client_message_id='timeout-001')
        async def timeout(awaitable, **_):
            awaitable.close()
            raise TimeoutError()
        with patch('backend.app.ai.asyncio.wait_for', timeout), self.assertRaises(BusinessError) as caught:
            asyncio.run(parse_message(self.service, self.member, request, lambda _: '{}'))
        self.assertEqual(caught.exception.status, 502)
        self.assertEqual(self.service.projects(self.admin)[0]['version'], 1)

    def test_model_cannot_read_or_modify_outside_membership(self):
        with self.assertRaises(BusinessError) as caught:
            self.request('query', self.outsider, key='outsider-query')
        self.assertEqual(caught.exception.status, 404)
        _, result = self.request('edit_project', self.outsider, key='outsider-edit',
                                 data={'name': '越权', 'reason': '我是管理员'})
        self.assertEqual(result['kind'], 'batch')
        self.assertEqual(result['business_failures'], 1)
        self.assertIn('不在本次可操作范围', result['failures'][0]['message'])

    def test_prompt_excludes_unauthorized_projects_and_credentials(self):
        prompt = prompt_context(self.service, self.outsider, '查项目')
        self.assertNotIn('保密展厅', prompt)
        self.assertNotIn(self.admin['access_key'], prompt)
        self.assertNotIn(self.outsider['access_key'], prompt)

    def test_prompt_can_include_more_than_twenty_explicitly_named_project_candidates(self):
        names = []
        for index in range(25):
            name = f'批量候选项目{index + 1}'
            names.append(name)
            draft = self.service.create_draft(self.admin, Action(
                intent='create_project', data={'name': name}))
            self.service.confirm(self.admin, draft['id'])

        prompt = prompt_context(self.service, self.admin, '；'.join(names))
        from backend.app.ai import SYSTEM
        candidates = json.loads(prompt[len(SYSTEM):])['projects']
        candidate_names = {project['name'] for project in candidates}

        self.assertTrue(set(names).issubset(candidate_names))
        self.assertTrue(all(isinstance(project['version'], int) for project in candidates))

    def test_disable_account_during_model_query_denies_response(self):
        request = MessageInput(text='查询', client_message_id='disable-001')
        def parser(_):
            with self.store.connect(write=True) as db:
                db.execute('UPDATE users SET active=0 WHERE id=?', (self.member['id'],))
            return json.dumps({'intent': 'query'})
        with self.assertRaises(BusinessError) as caught:
            asyncio.run(parse_message(self.service, self.member, request, parser))
        self.assertEqual(caught.exception.status, 403)

    def test_each_new_message_is_independent_after_needs_input(self):
        first = MessageInput(text='把项目改名', client_message_id='followup-01')
        raw = json.dumps({'intent': 'edit_project', 'project_id': self.pid, 'missing_fields': ['name', 'reason']})
        result = asyncio.run(parse_message(self.service, self.admin, first, lambda _: raw))
        self.assertEqual(result['kind'], 'needs_input')
        second = MessageInput(text='新名称，更正', client_message_id='followup-02')
        raw = json.dumps({'intent': 'edit_project', 'project_id': self.pid, 'data': {'name': '新名称', 'reason': '更正'}})
        result = asyncio.run(parse_message(Service(Store(self.store.path), lambda: self.at), self.admin, second, lambda _: raw))
        self.assertEqual(result['kind'], 'saved')
        self.assertEqual(self.service.projects(self.admin)[0]['name'], '新名称')

    def test_expected_version_uses_only_exact_candidate_matches(self):
        candidates = [
            {'id': '1', 'name': '北斗', 'version': 3},
            {'id': '2', 'name': '北斗项目', 'version': 7},
        ]

        self.assertEqual(expected_version_for(
            Action(intent='record_item', data={'project_name': ' 北斗 '}), candidates), 3)
        self.assertEqual(expected_version_for(
            Action(intent='edit_project', project_id='2', data={}), candidates), 7)
        self.assertIsNone(expected_version_for(
            Action(intent='record_item', data={'project_name': '北'}), candidates))

    def test_unique_project_name_alias_matches_but_nonunique_alias_is_rejected(self):
        candidates = [
            {'id': '1', 'name': '孝感锐翰科技工厂', 'version': 3},
        ]
        self.assertEqual(match_project_candidate('锐翰科技', candidates), candidates[0])
        self.assertEqual(expected_version_for(
            Action(intent='record_item', data={'project_name': '锐翰科技'}), candidates), 3)
        with self.assertRaises(BusinessError):
            match_project_candidate('锐翰科技', candidates + [
                {'id': '2', 'name': '锐翰科技IT部', 'version': 1},
            ])


if __name__ == '__main__':
    unittest.main()
