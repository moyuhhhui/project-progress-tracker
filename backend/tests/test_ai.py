import asyncio
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from backend.app.ai import parse_message, prompt_context
from backend.app.models import Action, MessageInput
from backend.app.service import BusinessError, Service, TZ
from backend.app.store import Store


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

    def test_complete_followup_publishes_once_and_incomplete_draft_stays_off_display(self):
        before = len(self.service.projects(self.admin, display=True))
        request = MessageInput(text='创建项目', client_message_id='auto-create-01')
        incomplete = asyncio.run(parse_message(self.service, self.admin, request,
            lambda _: json.dumps({'intent': 'create_project', 'data': {}})))['draft']
        self.assertEqual(incomplete['status'], 'needs_input')
        self.assertEqual(len(self.service.projects(self.admin, display=True)), before)
        complete = MessageInput(text='补齐信息', client_message_id='auto-create-02', previous_draft_id=incomplete['id'])
        raw = json.dumps({'intent': 'create_project', 'data': {
            'name': '新立项', 'owner_id': self.admin['id'], 'start_date': '2026-09-01', 'due_date': '2026-09-30',
            'milestones': [{'name': '交付', 'criterion': '验收通过', 'owner_id': self.admin['id'],
                            'start_date': '2026-09-01', 'due_date': '2026-09-30'}]}})
        result = asyncio.run(parse_message(self.service, self.admin, complete, lambda _: raw))
        self.assertEqual(result['draft']['status'], 'confirmed')
        self.assertEqual(len(self.service.projects(self.admin, display=True)), before + 1)
        self.assertEqual(self.replay(complete, self.admin)['draft']['id'], result['draft']['id'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM audit WHERE draft_id=?', (result['draft']['id'],)).fetchone()[0], 1)

    def test_concurrent_followups_save_only_one_successor(self):
        from backend.app.ai import save_incomplete
        previous = save_incomplete(self.service, self.admin, Action(intent='create_project'), '创建项目', {})
        raw = json.dumps({'intent': 'create_project', 'data': {
            'name': '并发立项', 'owner_id': self.admin['id'], 'start_date': '2026-09-01', 'due_date': '2026-09-30',
            'milestones': [{'name': '节点', 'criterion': '验收', 'owner_id': self.admin['id'],
                            'start_date': '2026-09-01', 'due_date': '2026-09-20'}]}})
        barrier = threading.Barrier(2)

        def parser(_):
            barrier.wait(timeout=5)
            return raw

        async def concurrent():
            return await asyncio.gather(*(parse_message(self.service, self.admin,
                MessageInput(text='补齐信息', client_message_id=f'concurrent-{i}', previous_draft_id=previous['id']), parser)
                for i in range(2)), return_exceptions=True)

        results = asyncio.run(concurrent())
        successes = [r for r in results if isinstance(r, dict)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(sum(isinstance(r, BusinessError) for r in results), 1)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM drafts WHERE status='pending'").fetchone()[0], 0)
        self.service.confirm(self.admin, successes[0]['draft']['id'])
        self.assertEqual(len(self.service.projects(self.admin)), 2)

    def test_incomplete_followup_after_restart_saves_once_after_seven_days(self):
        from backend.app.ai import save_incomplete
        previous = save_incomplete(self.service, self.admin, Action(intent='create_project'), '创建项目', {})
        self.at += timedelta(days=7)
        self.service = Service(Store(self.store.path), lambda: self.at)
        request = MessageInput(text='补齐信息', client_message_id='restart-followup-01', previous_draft_id=previous['id'])
        raw = json.dumps({'intent': 'create_project', 'data': {
            'name': '重开页面后补齐', 'owner_id': self.admin['id'], 'start_date': '2026-09-01', 'due_date': '2026-09-30',
            'milestones': [{'name': '交付', 'criterion': '验收通过', 'owner_id': self.admin['id'],
                            'start_date': '2026-09-01', 'due_date': '2026-09-30'}]}})
        result = asyncio.run(parse_message(self.service, self.admin, request, lambda _: raw))
        self.assertEqual(result['draft']['status'], 'confirmed')
        self.assertEqual(self.service.draft(self.admin, previous['id'])['status'], 'cancelled')
        self.assertEqual(self.replay(request, self.admin)['draft']['id'], result['draft']['id'])
        self.assertEqual(len(self.service.projects(self.admin)), 2)

    def test_followup_rechecks_previous_status_after_model_returns(self):
        for command in ('cancel', 'confirm'):
            previous = self.service.create_draft(self.admin, Action(intent='edit_project', project_id=self.pid,
                data={'name': '新项目名称', 'reason': '修正'}))

            def parser(_):
                getattr(self.service, command)(self.admin, previous['id'])
                return json.dumps({'intent': 'edit_project', 'project_id': self.pid,
                                   'data': {'name': '不应生成的新草稿', 'reason': '补充'}})

            with self.assertRaises(BusinessError):
                asyncio.run(parse_message(self.service, self.admin,
                    MessageInput(text='补充', client_message_id='while-' + command, previous_draft_id=previous['id']), parser))
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM drafts WHERE status='pending'").fetchone()[0], 0)

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

    def test_cached_incomplete_preview_is_denied_after_revocation(self):
        request = MessageInput(text='调整项目', client_message_id='draft-00001')
        raw = json.dumps({'intent': 'edit_project', 'project_id': self.pid, 'missing_fields': ['reason']})
        result = asyncio.run(parse_message(self.service, self.member, request, lambda _: raw))
        self.assertEqual(result['draft']['status'], 'needs_input')
        self.revoke_membership()
        with self.assertRaises(BusinessError) as caught:
            self.replay(request)
        self.assertEqual(caught.exception.status, 404)

    def test_cached_preview_tracks_cancelled_status(self):
        request, result = self.request('edit_project', self.admin, data={})
        self.service.cancel(self.admin, result['draft']['id'])
        self.assertEqual(self.replay(request, self.admin)['draft']['status'], 'cancelled')

    def test_cached_incomplete_preview_remains_available(self):
        request, _ = self.request('edit_project', self.admin, data={})
        self.at += timedelta(minutes=31)
        self.assertEqual(self.replay(request, self.admin)['draft']['status'], 'needs_input')

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
        self.assertEqual(result['draft']['status'], 'confirmed')
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
        self.assertEqual(result['succeeded'], 6)
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
        calls = [
            {'name': 'create_project', 'arguments': {'data': {'name': '批量新增项目'}}},
            {'name': 'create_project', 'arguments': {'data': {'name': '保密展厅'}}},
        ]
        request = MessageInput(text='同时新增两个项目', client_message_id='partial-batch-message')

        result = asyncio.run(parse_message(self.service, self.admin, request, lambda _: calls))

        self.assertEqual(result['kind'], 'batch')
        self.assertEqual((result['succeeded'], result['failed']), (1, 1))
        self.assertIn('名称冲突', result['failures'][0]['message'])
        self.assertEqual(len(self.service.projects(self.admin)), 2)

        replayed = self.replay(request, self.admin)
        self.assertEqual((replayed['succeeded'], replayed['failed']), (1, 1))
        self.assertEqual(len(self.service.projects(self.admin)), 2)

    def test_group_message_without_project_never_creates_fallback_project(self):
        from backend.app.ai import save_group_message
        before = len(self.service.projects(self.admin))

        with self.assertRaises(BusinessError):
            save_group_message(self.service, self.admin,
                               Action(intent='record_item', data={'text': '先记录下来'}),
                               '先记录下来', {})

        self.assertEqual(len(self.service.projects(self.admin)), before)
        self.assertFalse(any(project['name'].startswith('待整理：')
                             for project in self.service.projects(self.admin)))

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
        for intent in ['query', 'edit_project']:
            with self.subTest(intent=intent), self.assertRaises(BusinessError) as caught:
                self.request(intent, self.outsider, key=f'outsider-{intent}', data={'name': '越权', 'reason': '我是管理员'})
            self.assertEqual(caught.exception.status, 404)

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
        candidate_names = {project['name'] for project in json.loads(prompt[len(SYSTEM):])['projects']}

        self.assertTrue(set(names).issubset(candidate_names))

    def test_prompt_matches_project_short_name_and_one_character_typo(self):
        for name, milestones in (
            ('锐翰科技工厂AI提效', [{'name': '开展工厂现场调研'}]),
            ('博思智能体', [{'name': '出具方案与预算'}]),
        ):
            draft = self.service.create_draft(self.admin, Action(
                intent='create_project', data={'name': name, 'milestones': milestones}))
            self.service.confirm(self.admin, draft['id'])

        prompt = prompt_context(
            self.service, self.admin,
            '锐翰工厂 朱浩a2，张毅a1，博斯智能体柯金成a角，朱浩b角')
        from backend.app.ai import SYSTEM
        candidates = json.loads(prompt[len(SYSTEM):])['projects']

        self.assertTrue({'锐翰科技工厂AI提效', '博思智能体'} <=
                        {project['name'] for project in candidates})

    def test_prompt_matches_warehouse_project_name_variants(self):
        for name, milestones in (
            ('WMS仓储管理系统', [{'name': '完成库存盘点'}]),
            ('仓库机器人', [{'name': '仓储管理系统调研'}]),
        ):
            draft = self.service.create_draft(self.admin, Action(
                intent='create_project', data={'name': name, 'milestones': milestones}))
            self.service.confirm(self.admin, draft['id'])

        from backend.app.ai import SYSTEM
        for index, text in enumerate(('仓储管理系统', 'WSM仓储管理系统', 'wms 仓储管理系统')):
            with self.subTest(text=text):
                prompt = prompt_context(self.service, self.admin, text)
                candidates = json.loads(prompt[len(SYSTEM):])['projects']
                self.assertIn('WMS仓储管理系统',
                              {project['name'] for project in candidates})

    def test_model_create_for_unique_alias_updates_existing_project(self):
        draft = self.service.create_draft(self.admin, Action(intent='create_project', data={
            'name': 'WMS仓储管理系统',
            'owner_assignments': [{'name': '小柯', 'role': 'B角'}],
            'milestones': [{'name': '完成库存盘点'}],
        }))
        project_id = self.service.confirm(self.admin, draft['id'])['project_id']
        before = next(project for project in self.service.projects(self.admin)
                      if project['id'] == project_id)
        request = MessageInput(text='仓储管理系统a角张毅b角柯金成',
                               client_message_id='warehouse-alias-update')
        calls = [{'name': 'create_project', 'arguments': {'data': {
            'name': '仓储管理系统',
            'owner_assignments': [
                {'name': '张毅', 'role': 'A角', 'primary': True},
                {'name': '柯金成', 'role': 'B角', 'primary': False},
            ],
        }}}]

        result = asyncio.run(parse_message(
            self.service, self.admin, request, lambda _: calls, channel='wecom'))

        projects = self.service.projects(self.admin)
        updated = next(project for project in projects if project['id'] == project_id)
        self.assertEqual(result['draft']['action']['intent'], 'edit_project')
        self.assertEqual(len(projects), 2)
        self.assertEqual(updated['owner_assignments'], [
            {'name': '张毅', 'role': 'A角', 'primary': True},
            {'name': '柯金成', 'role': 'B角', 'primary': False},
        ])
        self.assertEqual(updated['milestones'], before['milestones'])

    def test_ambiguous_project_alias_stops_before_saving(self):
        for name in ('WMS仓储管理系统', 'ERP仓储管理系统'):
            draft = self.service.create_draft(
                self.admin, Action(intent='create_project', data={'name': name}))
            self.service.confirm(self.admin, draft['id'])
        before = {project['id']: project['version']
                  for project in self.service.projects(self.admin)}
        request = MessageInput(text='仓储管理系统a角张毅',
                               client_message_id='ambiguous-warehouse-alias')
        calls = [{'name': 'create_project', 'arguments': {'data': {
            'name': '仓储管理系统',
            'owner_assignments': [{'name': '张毅', 'role': 'A角', 'primary': True}],
        }}}]

        result = asyncio.run(parse_message(
            self.service, self.admin, request, lambda _: calls, channel='wecom'))

        self.assertEqual((result['succeeded'], result['failed']), (0, 1))
        self.assertIn('多个项目', result['failures'][0]['message'])
        self.assertEqual(before, {project['id']: project['version']
                                  for project in self.service.projects(self.admin)})

    def test_explicit_create_rejects_existing_project_alias(self):
        draft = self.service.create_draft(
            self.admin, Action(intent='create_project', data={'name': 'WMS仓储管理系统'}))
        self.service.confirm(self.admin, draft['id'])
        before = len(self.service.projects(self.admin))
        request = MessageInput(text='新建项目仓储管理系统',
                               client_message_id='explicit-duplicate-alias')
        calls = [{'name': 'create_project', 'arguments': {
            'data': {'name': '仓储管理系统'}}}]

        result = asyncio.run(parse_message(
            self.service, self.admin, request, lambda _: calls, channel='wecom'))

        self.assertEqual((result['succeeded'], result['failed']), (0, 1))
        self.assertIn('名称冲突', result['failures'][0]['message'])
        self.assertEqual(len(self.service.projects(self.admin)), before)

    def test_model_create_with_unmatched_name_still_creates_project(self):
        request = MessageInput(text='新建项目供应链驾驶舱',
                               client_message_id='unmatched-project-create')
        calls = [{'name': 'create_project', 'arguments': {
            'data': {'name': '供应链驾驶舱'}}}]

        result = asyncio.run(parse_message(
            self.service, self.admin, request, lambda _: calls, channel='wecom'))

        self.assertEqual(result['draft']['status'], 'confirmed')
        self.assertIn('供应链驾驶舱',
                      {project['name'] for project in self.service.projects(self.admin)})

    def test_shared_group_creates_distinct_warehouse_project_with_named_node_owners(self):
        existing = self.service.create_draft(self.admin, Action(intent='create_project', data={
            'name': 'WMS仓储管理系统', 'owner_name': '张毅', 'due_date': '2026-09-30',
        }))
        self.service.confirm(self.admin, existing['id'])
        self.service.internal_shared = True
        request = MessageInput(text='新建项目：华东仓储系统升级',
                               client_message_id='shared-warehouse-create')
        calls = [{'name': 'create_project', 'arguments': {'data': {
            'name': '华东仓储系统升级',
            'owner_assignments': [
                {'name': '柯金成', 'role': 'A角', 'primary': True},
                {'name': '朱浩', 'role': 'B角', 'primary': False},
            ],
            'start_date': '2026-09-16', 'due_date': '2026-10-30',
            'milestones': [
                {'name': '完成现状调研', 'criterion': '输出调研报告', 'owner_id': '柯金成',
                 'start_date': '2026-09-16', 'due_date': '2026-09-20'},
                {'name': '完成系统方案设计', 'criterion': '提交系统方案', 'owner_id': '朱浩',
                 'start_date': '2026-09-21', 'due_date': '2026-09-30'},
            ],
        }}}]

        result = asyncio.run(parse_message(
            self.service, self.admin, request, lambda _: calls, channel='wecom'))

        self.assertEqual(result['draft']['status'], 'confirmed')
        project = next(project for project in self.service.projects(self.admin)
                       if project['name'] == '华东仓储系统升级')
        self.assertEqual([node['owner_name'] for node in project['milestones']], ['柯金成', '朱浩'])

    def test_multiple_project_owner_message_rejects_single_project_action(self):
        projects = []
        for name in ('锐翰科技工厂AI提效', '博思智能体'):
            draft = self.service.create_draft(self.admin, Action(
                intent='create_project', data={'name': name}))
            project_id = self.service.confirm(self.admin, draft['id'])['project_id']
            projects.append(self.service.projects(self.admin)[-1])
        before = {project['id']: project['version'] for project in self.service.projects(self.admin)}
        request = MessageInput(
            text='锐翰工厂 朱浩a2，张毅a1，博斯智能体柯金成a角，朱浩b角',
            client_message_id='mixed-project-owners')
        calls = [{'name': 'edit_project', 'arguments': {
            'project_id': projects[0]['id'],
            'data': {'owner_assignments': [
                {'name': '朱浩', 'role': 'a2'},
                {'name': '张毅', 'role': 'a1'},
                {'name': '柯金成', 'role': 'a角'},
            ], 'reason': '更新负责人分工'},
        }}]

        with self.assertRaises(BusinessError) as caught:
            asyncio.run(parse_message(
                self.service, self.admin, request, lambda _: calls, channel='wecom'))

        self.assertIn('多个项目', caught.exception.message)
        self.assertEqual(before, {project['id']: project['version']
                                  for project in self.service.projects(self.admin)})

    def test_disable_account_during_model_query_denies_response(self):
        request = MessageInput(text='查询', client_message_id='disable-001')
        def parser(_):
            with self.store.connect(write=True) as db:
                db.execute('UPDATE users SET active=0 WHERE id=?', (self.member['id'],))
            return json.dumps({'intent': 'query'})
        with self.assertRaises(BusinessError) as caught:
            asyncio.run(parse_message(self.service, self.member, request, parser))
        self.assertEqual(caught.exception.status, 403)

    def test_followup_persists_and_saves_automatically(self):
        first = MessageInput(text='把项目改名', client_message_id='followup-01')
        raw = json.dumps({'intent': 'edit_project', 'project_id': self.pid, 'missing_fields': ['name', 'reason']})
        result = asyncio.run(parse_message(self.service, self.admin, first, lambda _: raw))
        did = result['draft']['id']
        second = MessageInput(text='新名称，更正', client_message_id='followup-02', previous_draft_id=did)
        raw = json.dumps({'intent': 'edit_project', 'project_id': self.pid, 'data': {'name': '新名称', 'reason': '更正'}})
        result = asyncio.run(parse_message(Service(Store(self.store.path), lambda: self.at), self.admin, second, lambda _: raw))
        self.assertEqual(result['draft']['status'], 'confirmed')
        self.assertEqual(self.service.draft(self.admin, did)['status'], 'cancelled')
        self.assertEqual(result['draft']['before']['name'], '保密展厅')
        self.assertEqual(self.service.projects(self.admin)[0]['name'], '新名称')


if __name__ == '__main__':
    unittest.main()
