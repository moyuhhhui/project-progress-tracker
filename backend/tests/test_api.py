import importlib
import asyncio
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.dict(os.environ, {'TRACKER_DB': str(Path(self.temp.name) / 'import.sqlite3')}):
            create_app = importlib.import_module('backend.app.main').create_app
        self.app = create_app(Path(self.temp.name) / 'api.sqlite3')
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.store = self.app.state.store
        self.admin = self.store.add_user('管理', 'admin')
        self.member = self.store.add_user('成员')
        self.display = self.store.add_user('大屏', 'display')

    def headers(self, actor=None):
        return {'Authorization': 'Bearer ' + (actor or self.admin)['access_key']}

    def test_direct_action_endpoint_saves_and_is_idempotent(self):
        body = {'client_operation_id': 'api-create-001', 'expected_version': None,
                'action': {'intent': 'create_project', 'data': {'name': 'API 直接保存'}}}
        first = self.client.post('/api/actions', json=body, headers=self.headers())
        second = self.client.post('/api/actions', json=body, headers=self.headers())
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.json(), first.json())
        self.assertEqual(len(self.client.get('/api/projects', headers=self.headers()).json()['projects']), 1)

    def test_direct_meeting_action_is_saved_and_listed(self):
        response = self.client.post('/api/actions', json={
            'client_operation_id': 'api-meeting-001',
            'action': {'intent': 'create_meeting', 'data': {
                'start_at': '2026-09-22T10:00:00+08:00', 'title': '项目评审'}}},
            headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['title'], '项目评审')
        meetings = self.client.get('/api/meetings', headers=self.headers())
        self.assertEqual(meetings.status_code, 200)
        self.assertEqual(meetings.json()['meetings'][0]['title'], '项目评审')

    def test_a1_a2_roles_are_normalized_for_workspace_and_display(self):
        body = {'client_operation_id': 'api-roles-001', 'action': {
            'intent': 'create_project', 'data': {
                'name': 'A1 A2 分工项目', 'owner_id': self.admin['id'],
                'member_ids': [self.admin['id'], self.member['id']],
                'owner_roles': {self.admin['id']: 'A1', self.member['id']: 'A2'}}}}
        response = self.client.post('/api/actions', json=body, headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        project = self.client.get('/api/projects', headers=self.headers()).json()['projects'][0]
        self.assertEqual(project['owner_name'], '管理（A1）、成员（A2）')
        self.assertEqual([(item['name'], item['role'], item['primary']) for item in project['owner_assignments']], [
            ('管理', 'A1', False), ('成员', 'A2', False)])
        display = self.client.get('/api/display', headers=self.headers(self.display)).json()['projects'][0]
        self.assertEqual([(item['name'], item['role']) for item in display['owner_assignments']], [
            ('管理', 'A1'), ('成员', 'A2')])

    def test_display_account_cannot_write_direct_action(self):
        response = self.client.post('/api/actions', json={
            'client_operation_id': 'display-write-001',
            'action': {'intent': 'create_project', 'data': {'name': '禁止'}}
        }, headers=self.headers(self.display))
        self.assertEqual(response.status_code, 403)

    def test_wecom_incomplete_message_never_creates_fallback_after_restart(self):
        from backend.app.wecom import BotHandler
        service = self.app.state.service
        with self.store.connect(write=True) as db:
            db.execute('UPDATE users SET wecom_user_id=? WHERE id=?', ('restart-member', self.admin['id']))
        frame = {'cmd': 'aibot_msg_callback', 'body': {'aibotid': 'test-bot', 'chattype': 'group',
            'msgid': 'restart-message', 'chatid': 'test-group', 'from': {'userid': 'restart-member'},
            'msgtype': 'text', 'text': {'content': '创建项目'}}}
        handler = BotHandler(service, 'test-bot', 'https://tracker.example')
        reply = asyncio.run(handler.handle(frame, lambda _: json.dumps({'intent': 'create_project',
            'data': {}, 'missing_fields': ['name']})))
        self.assertIn('未保存任何项目或事项', reply)
        later = service.clock() + timedelta(days=7)
        with patch.dict(os.environ, {'TRACKER_SHARED_USER_ID': self.admin['id']}):
            reopened = importlib.import_module('backend.app.main').create_app(self.store.path)
        reopened.state.service.clock = lambda: later
        with TestClient(reopened) as client:
            self.assertEqual(client.get('/api/drafts').json(), [])
            self.assertEqual(reopened.state.service.projects(self.admin, display=True), [])

    def shared_client(self):
        with patch.dict(os.environ, {'TRACKER_SHARED_USER_ID': self.admin['id']}):
            app = importlib.import_module('backend.app.main').create_app(self.store.path)
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_shared_workspace_opens_without_key_and_does_not_issue_member_keys(self):
        client = self.shared_client()
        me = client.get('/api/me')
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()['id'], self.admin['id'])
        self.assertNotIn('access_key', me.text)
        for path in ('/api/projects', '/api/display', '/api/users', '/api/settings'):
            self.assertEqual(client.get(path).status_code, 200, path)
        member = client.post('/api/users', json={'name': '新成员', 'role': 'member'})
        self.assertEqual(member.status_code, 200)
        self.assertNotIn('access_key', member.text)
        self.assertEqual(client.post(f"/api/users/{self.member['id']}/rotate-key", json={}).status_code, 404)
        with self.store.connect(write=True) as db:
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.admin['id'],))
        self.assertEqual(client.get('/api/me').status_code, 503)

    def test_shared_workspace_can_review_and_confirm_member_draft_with_shared_audit(self):
        from backend.app.models import Action
        action = Action(intent='create_project', data={'name': '群里提出的项目',
            'owner_id': self.member['id'], 'start_date': '2026-09-01', 'due_date': '2026-09-30',
            'milestones': [{'name': '节点', 'criterion': '验收', 'owner_id': self.member['id'],
                            'start_date': '2026-09-01', 'due_date': '2026-09-30'}]})
        draft = self.app.state.service.create_draft(self.member, action)
        client = self.shared_client()
        listed = client.get('/api/drafts')
        self.assertEqual(listed.status_code, 200)
        self.assertIn(draft['id'], [d['id'] for d in listed.json()])
        self.assertEqual(client.get('/api/drafts/' + draft['id']).status_code, 200)
        self.assertEqual(client.get('/api/projects').json()['projects'], [])
        confirmed = client.post('/api/drafts/' + draft['id'] + '/confirm', json={})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertEqual(client.post('/api/drafts/' + draft['id'] + '/confirm', json={}).json(), confirmed.json())
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT user_id FROM audit').fetchone()[0], self.admin['id'])
            self.assertEqual(db.execute('SELECT user_id FROM drafts').fetchone()[0], self.member['id'])
            self.assertEqual(db.execute('SELECT count(*) FROM projects').fetchone()[0], 1)
        pending = self.app.state.service.create_draft(self.member, action)
        self.assertEqual(client.post('/api/drafts/' + pending['id'] + '/cancel', json={}).status_code, 200)
        from backend.app.ai import save_incomplete
        incomplete = save_incomplete(self.app.state.service, self.member, Action(intent='create_project'), '创建项目', {})
        successor = client.app.state.service.create_draft(self.admin, action, previous_draft_id=incomplete['id'])
        self.assertEqual(client.get('/api/drafts/' + incomplete['id']).json()['status'], 'cancelled')
        self.assertEqual(client.get('/api/drafts/' + successor['id']).json()['status'], 'pending')

    def test_wecom_status_reads_actual_worker_state_without_secrets(self):
        from backend.app.wecom import BotRuntime
        response = self.client.get('/api/status', headers=self.headers())
        self.assertEqual(response.json()['wecom_inbound'], 'not_started')
        runtime = BotRuntime(self.app.state.service, 'bot-private-id')
        runtime.acquire()
        runtime.update('authenticated')
        with patch.dict(os.environ, {'WECOM_BOT_SECRET': 'secret-private-value'}):
            response = self.client.get('/api/status', headers=self.headers())
        self.assertEqual(response.json()['wecom_inbound'], 'authenticated')
        self.assertNotIn('secret-private-value', response.text)
        self.assertNotIn('bot-private-id', response.text)

    def test_status_reports_deepseek_model_without_key(self):
        with patch.dict(os.environ, {'TRACKER_AI_ENABLED': 'true', 'DEEPSEEK_API_KEY': 'secret-ds-value',
                                     'DEEPSEEK_MODEL': 'deepseek-v4-flash'}):
            response = self.client.get('/api/status', headers=self.headers())
        self.assertTrue(response.json()['ai_configured'])
        self.assertEqual(response.json()['ai_model'], 'deepseek-v4-flash')
        self.assertNotIn('secret-ds-value', response.text)

    def make_project(self, visible=False):
        action = {'intent': 'create_project', 'data': {
            'name': '内部项目', 'description': '内部目标', 'owner_id': self.admin['id'],
            'start_date': '2026-09-01', 'due_date': '2026-09-30', 'display_visible': visible,
            'milestones': [{'name': '节点', 'criterion': '验收', 'owner_id': self.admin['id'],
                            'start_date': '2026-09-01', 'due_date': '2026-09-30'}]}}
        draft = self.client.post('/api/drafts', json=action, headers=self.headers())
        self.assertEqual(draft.status_code, 200, draft.text)
        self.assertEqual(draft.json()['status'], 'confirmed')
        self.assertEqual(len(self.client.get('/api/projects', headers=self.headers()).json()['projects']), 1)
        did = draft.json()['id']
        result = self.client.post(f'/api/drafts/{did}/confirm', json={}, headers=self.headers())
        self.assertEqual(result.status_code, 200, result.text)
        return did, result.json()

    def test_authentication_and_revocation(self):
        self.assertEqual(self.client.get('/api/me').status_code, 401)
        self.assertEqual(self.client.get('/api/me', headers={'Authorization': 'Bearer wrong'}).status_code, 401)
        response = self.client.get('/api/me', headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('token_hash', response.text)
        self.assertNotIn('access_key', response.text)
        rotated = self.client.post(f"/api/users/{self.member['id']}/rotate-key", json={}, headers=self.headers())
        self.assertEqual(rotated.status_code, 200)
        self.assertEqual(self.client.get('/api/me', headers=self.headers(self.member)).status_code, 401)
        self.assertEqual(self.client.get('/api/me', headers={'Authorization': 'Bearer ' + rotated.json()['access_key']}).status_code, 200)

    def test_display_is_readonly_and_excludes_private_fields(self):
        self.make_project(True)
        response = self.client.get('/api/display', headers=self.headers(self.display))
        self.assertEqual(response.status_code, 200)
        project = response.json()['projects'][0]
        self.assertEqual(project['name'], '内部项目')
        for forbidden in ('member_ids', 'created_by', 'owner_id', 'source_text', 'wecom_user_id', 'token_hash'):
            self.assertNotIn(forbidden, response.text)
        for path in ('/api/projects', '/api/users', '/api/settings', '/api/drafts', '/api/reminders', '/api/status'):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path, headers=self.headers(self.display)).status_code, 403)
        response = self.client.post('/api/drafts', json={'intent': 'query'}, headers=self.headers(self.display))
        self.assertEqual(response.status_code, 403)

    def test_display_hides_unapproved_projects(self):
        self.make_project(False)
        self.assertEqual(self.client.get('/api/display', headers=self.headers(self.display)).json()['projects'], [])
        self.assertEqual(self.client.get('/api/display', headers=self.headers(self.member)).status_code, 403)

    def test_confirmation_idempotency_and_history(self):
        did, saved = self.make_project()
        repeat = self.client.post(f'/api/drafts/{did}/confirm', json={}, headers=self.headers())
        self.assertEqual(repeat.json(), saved)
        history = self.client.get(f"/api/projects/{saved['project_id']}/history", headers=self.headers()).json()
        self.assertEqual(len(history['audit']), 1)
        self.assertEqual(history['audit'][0]['intent'], 'create_project')
        self.assertEqual(history['reports'], [])
        denied = self.client.get(f"/api/projects/{saved['project_id']}/history", headers=self.headers(self.member))
        self.assertEqual(denied.status_code, 404)

    def test_drafts_cannot_be_read_or_confirmed_by_other_user(self):
        did, _ = self.make_project()
        self.assertEqual(self.client.get(f'/api/drafts/{did}', headers=self.headers(self.member)).status_code, 404)
        self.assertEqual(self.client.post(f'/api/drafts/{did}/confirm', json={}, headers=self.headers(self.member)).status_code, 404)

    def test_admin_routes_reject_member(self):
        for method, path, body in [
            ('post', '/api/users', {'name': '新成员'}),
            ('put', '/api/settings', {}),
            ('post', '/api/reminders/scan', {}),
            ('post', f"/api/users/{self.admin['id']}/rotate-key", {}),
            ('patch', f"/api/users/{self.admin['id']}", {'active': False}),
        ]:
            with self.subTest(path=path):
                self.assertEqual(getattr(self.client, method)(path, json=body, headers=self.headers(self.member)).status_code, 403)

    def test_user_management_validates_and_does_not_expose_keys(self):
        created = self.client.post('/api/users', json={'name': '新成员', 'wecom_user_id': 'employee.1'}, headers=self.headers())
        self.assertEqual(created.status_code, 200)
        self.assertIn('access_key', created.json())
        duplicate = self.client.post('/api/users', json={'name': '重复', 'wecom_user_id': 'employee.1'}, headers=self.headers())
        self.assertEqual(duplicate.status_code, 400)
        listing = self.client.get('/api/users', headers=self.headers())
        self.assertNotIn(created.json()['access_key'], listing.text)
        disabled = self.client.patch(f"/api/users/{created.json()['id']}", json={'active': False}, headers=self.headers())
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(self.client.get('/api/me', headers=self.headers(created.json())).status_code, 401)
        self.assertEqual(self.client.patch(f"/api/users/{self.admin['id']}", json={'active': False}, headers=self.headers()).status_code, 400)

    def test_schema_and_settings_validation(self):
        self.assertEqual(self.client.post('/api/drafts', json={'intent': 'query', 'operator': 'admin'}, headers=self.headers()).status_code, 422)
        self.assertEqual(self.client.put('/api/settings', json={'start_hour': 18, 'end_hour': 9}, headers=self.headers()).status_code, 422)
        self.assertEqual(self.client.put('/api/settings', json={'workday_overrides': {'invalid': True}}, headers=self.headers()).status_code, 422)
        response = self.client.put('/api/settings', json={'workday_overrides': {'2026-09-05': True}}, headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.client.get('/api/settings', headers=self.headers()).json()['workday_overrides']['2026-09-05'])

    def test_disabled_ai_does_not_create_messages_or_projects(self):
        with patch.dict(os.environ, {'TRACKER_AI_ENABLED': 'false'}):
            response = self.client.post('/api/messages', json={'text': '创建项目', 'client_message_id': 'test-disabled'}, headers=self.headers())
        self.assertEqual(response.status_code, 503)
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM messages').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM projects').fetchone()[0], 0)

    def test_status_keeps_inbound_not_started_and_scan_never_sends(self):
        status = self.client.get('/api/status', headers=self.headers()).json()
        self.assertEqual(status['wecom_inbound'], 'not_started')
        with patch('backend.app.reminders.WeComSender.send', side_effect=AssertionError('不应发送外部消息')):
            self.assertEqual(self.client.post('/api/reminders/scan', json={}, headers=self.headers()).status_code, 200)

    def test_sensitive_api_responses_disable_browser_cache(self):
        response = self.client.get('/api/projects', headers=self.headers())
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')

    def test_project_manager_can_select_first_time_collaborators(self):
        from backend.app.models import Action
        outsider = self.store.add_user('首次合作成员', wecom_user_id='private-binding')
        _, saved = self.make_project()
        path = f"/api/users?project_id={saved['project_id']}"
        denied = self.client.get(path, headers=self.headers(self.member))
        self.assertIn(denied.status_code, (403, 404))
        draft = self.app.state.service.create_draft(self.admin, Action(intent='edit_project',
            project_id=saved['project_id'], data={'owner_id': self.member['id'], 'reason': '移交项目'}))
        self.app.state.service.confirm(self.admin, draft['id'])
        response = self.client.get(path, headers=self.headers(self.member))
        self.assertEqual(response.status_code, 200)
        self.assertIn(outsider['id'], [u['id'] for u in response.json()])
        self.assertNotIn('private-binding', response.text)
        self.assertNotIn('wecom_user_id', response.text)
        self.assertNotIn(self.display['id'], [u['id'] for u in response.json()])

    def test_draft_exposes_configured_deadline_hour_for_preview(self):
        did, _ = self.make_project()
        self.client.put('/api/settings', json={'due_hour': 17}, headers=self.headers())
        draft = self.client.get(f'/api/drafts/{did}', headers=self.headers()).json()
        self.assertEqual(draft.get('due_hour'), 17)


if __name__ == '__main__':
    unittest.main()
