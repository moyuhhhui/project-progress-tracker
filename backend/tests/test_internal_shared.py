import importlib
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend.app.models import Action
from backend.app.service import BusinessError
from backend.app.wecom import BotHandler


class InternalSharedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {'TRACKER_INTERNAL_SHARED': 'true',
                                      'TRACKER_DB': str(Path(self.temp.name) / 'import.sqlite3')}):
            create_app = importlib.import_module('backend.app.main').create_app
            self.app = create_app(Path(self.temp.name) / 'shared.sqlite3')
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.service = self.app.state.service
        self.store = self.app.state.store

    def test_empty_database_opens_without_admin_and_can_edit(self):
        actor = self.client.get('/api/me').json()
        self.assertTrue(actor['internal_shared'])
        self.assertEqual(actor['role'], 'member')
        created = self.client.post('/api/drafts', json={'intent': 'record_item',
            'data': {'project_name': '共享项目', 'text': '准备上线'}})
        self.assertEqual(created.status_code, 200)
        pid = created.json()['result']['project_id']
        changed = self.client.post('/api/drafts', json={'intent': 'edit_project', 'project_id': pid,
            'data': {'description': '任何人可以修改', 'reason': '内部协作'}})
        self.assertEqual(changed.status_code, 200)
        for path in ('/api/projects', '/api/users', '/api/settings', '/api/reminders', '/api/display'):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM users WHERE role='admin'").fetchone()[0], 0)

    def test_project_needs_only_name_and_preserves_plain_owner_and_status(self):
        minimal = self.client.post('/api/drafts', json={'intent': 'create_project', 'data': {'name': '只有名称'}})
        self.assertEqual(minimal.status_code, 200, minimal.text)
        response = self.client.post('/api/drafts', json={'intent': 'create_project', 'data': {
            'name': '公司项目进度追踪', 'owner_name': '朱浩', 'status': 'active', 'due_date': '2026-09-09'}})
        self.assertEqual(response.status_code, 200, response.text)
        project = next(p for p in self.client.get('/api/projects').json()['projects'] if p['name'] == '公司项目进度追踪')
        self.assertEqual(project['owner_name'], '朱浩')
        self.assertEqual(project['status'], 'active')
        self.assertEqual(project['due_date'], '2026-09-09')
        self.assertIsNone(project['start_date'])
        self.assertEqual(project['milestones'], [])
        with self.store.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM users WHERE name='朱浩'").fetchone()[0], 0)

    def test_delivery_completes_project_without_any_items(self):
        response = self.client.post('/api/drafts', json={'intent': 'create_project',
            'data': {'name': '无事项项目', 'status': 'active'}})
        pid = response.json()['result']['project_id']
        result = self.client.post('/api/drafts', json={'intent': 'project_status', 'project_id': pid,
            'data': {'status': 'completed', 'reason': '项目已交付'}})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['status'], 'confirmed')
        self.assertEqual(self.client.get('/api/projects').json()['projects'][0]['status'], 'completed')

    def test_wecom_members_share_direct_saved_projects_without_manual_binding(self):
        bot = BotHandler(self.service, 'test-bot', 'http://127.0.0.1:5173')
        first, second = bot.actor('employee-one'), bot.actor('employee-two')

        def frame(msgid, userid, text):
            return {'cmd': 'aibot_msg_callback', 'body': {'msgid': msgid,
                'aibotid': 'test-bot', 'chatid': 'shared-group', 'chattype': 'group',
                'from': {'userid': userid}, 'msgtype': 'text', 'text': {'content': text}}}

        first_reply = asyncio.run(bot.handle(frame('shared-1', 'employee-one', '记录第一条'),
            lambda _: json.dumps({'intent': 'record_item', 'data': {
                'project_name': '共享项目', 'text': '第一条'}})))
        second_reply = asyncio.run(bot.handle(frame('shared-2', 'employee-two', '记录第二条'),
            lambda _: json.dumps({'intent': 'record_item', 'data': {
                'project_name': '共享项目', 'text': '第二条'}})))

        self.assertIn('成功保存 1 项', first_reply)
        self.assertIn('成功保存 1 项', second_reply)
        self.assertEqual(len(self.service.projects(second)), 1)
        self.assertEqual(len(self.service.projects(second)[0]['milestones']), 2)
        self.assertEqual(len(self.client.get('/api/projects').json()['projects']), 1)
        self.assertEqual(self.client.get('/api/drafts').json(), [])
        self.assertEqual(bot.actor('employee-one')['id'], first['id'])

    def test_internal_mode_ignores_account_status_and_binding(self):
        bot = BotHandler(self.service, 'test-bot', 'http://127.0.0.1:5173')
        actor = bot.actor('disabled-person')
        with self.store.connect(write=True) as db:
            db.execute('UPDATE users SET active=0 WHERE id=?', (actor['id'],))
        self.assertTrue(bot.actor('disabled-person')['internal_shared'])
        self.assertEqual(bot.actor('someone-else')['id'], actor['id'])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM users').fetchone()[0], 1)

    def test_internal_mode_does_not_create_one_time_pairing_storage(self):
        with self.store.connect() as db:
            table = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='wecom_pairings'").fetchone()
        self.assertIsNone(table)

    def test_member_creation_does_not_create_admin_or_distribute_keys(self):
        response = self.client.post('/api/users', json={'name': '同事', 'role': 'admin'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['role'], 'member')
        self.assertNotIn('access_key', response.json())

    def test_schema_error_is_not_reported_as_account_permission_error(self):
        bot = BotHandler(self.service, 'test-bot', 'http://127.0.0.1:5173')
        frame = {'cmd': 'aibot_msg_callback', 'body': {'msgid': 'schema-error-message',
            'aibotid': 'test-bot', 'chatid': 'group', 'chattype': 'group',
            'from': {'userid': 'anyone'}, 'msgtype': 'text', 'text': {'content': '添加项目'}}}
        with patch('backend.app.wecom.ai.parse_message', side_effect=BusinessError('模型输出不能用 null 填充缺项')):
            reply = asyncio.run(bot.handle(frame))
        self.assertIn('null', reply)
        self.assertNotIn('账号权限', reply)
        self.assertNotIn('登录', reply)

