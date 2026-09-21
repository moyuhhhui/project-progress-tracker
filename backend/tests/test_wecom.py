import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from backend.app.models import Action, MessageInput
from backend.app.ai import parse_message
from backend.app.service import Service, TZ, BusinessError
from backend.app.store import Store
from backend.app.wecom import BotHandler, BotRuntime, read_status, validate_web_url


def group_frame(msgid, text):
    return {'cmd': 'aibot_msg_callback', 'body': {
        'msgid': msgid, 'aibotid': 'test-bot', 'chatid': 'test-group',
        'chattype': 'group', 'from': {'userid': 'employee.1'},
        'msgtype': 'text', 'text': {'content': text}}}


class WeComTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'wecom.sqlite3')
        self.at = datetime(2026, 9, 4, 10, tzinfo=TZ)
        self.service = Service(self.store, lambda: self.at)
        self.admin = self.store.add_user('负责人', 'admin', 'employee.1')
        self.other = self.store.add_user('另一成员', 'member', 'employee.2')
        self.handler = BotHandler(self.service, 'bot-1', 'https://tracker.example')
        self.calls = 0

    def frame(self, text='创建内部项目', msgid='msg-1', chatid='group-1', userid='employee.1', **changes):
        body = {'msgid': msgid, 'aibotid': 'bot-1', 'chatid': chatid,
                'chattype': 'group', 'from': {'userid': userid}, 'msgtype': 'text',
                'text': {'content': text}, **changes}
        return {'cmd': 'aibot_msg_callback', 'headers': {'req_id': 'req-' + msgid}, 'body': body}

    def parser(self, prompt):
        self.calls += 1
        return json.dumps({'intent': 'create_project', 'data': {
            'name': '不应出现在群里的保密项目', 'owner_id': self.admin['id'],
            'start_date': '2026-09-01', 'due_date': '2026-09-30',
            'milestones': [{'name': '内部节点', 'criterion': '验收', 'owner_id': self.admin['id'],
                            'start_date': '2026-09-01', 'due_date': '2026-09-20'}]}})

    def handle(self, frame=None, parser=None):
        return asyncio.run(self.handler.handle(frame or self.frame(), parser or self.parser))

    def drafts(self):
        with self.store.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM drafts')]

    def test_complete_group_message_returns_saved_summary_without_legacy_link(self):
        result = {'kind': 'saved', 'result': {'project_id': '1', 'code': 'P0001',
                  'operation_id': 'op-1', 'version': 1, 'message': '已保存'}}
        handler = BotHandler(self.service, 'test-bot', 'https://tracker.example')
        with patch('backend.app.wecom.ai.parse_message', return_value=result):
            reply = asyncio.run(handler.handle(group_frame('wecom-direct-001', '新建项目')))
        self.assertIn('成功保存 1 项', reply)
        self.assertNotIn('#draft=', reply)
        self.assertNotIn('补充 ', reply)

    def test_incomplete_group_message_lists_missing_fields_without_writing_link(self):
        result = {'kind': 'needs_input', 'missing_fields': ['project_name'],
                  'ambiguities': [], 'message': '请补充所属项目名称'}
        handler = BotHandler(self.service, 'test-bot', 'https://tracker.example')
        with patch('backend.app.wecom.ai.parse_message', return_value=result):
            reply = asyncio.run(handler.handle(group_frame('wecom-missing-001', '明天调研')))
        self.assertIn('请补充所属项目名称', reply)
        self.assertNotIn('#draft=', reply)

    def test_batch_reply_counts_actions_not_projects_or_model_attempts(self):
        result = {'kind': 'batch', 'results': [{}, {}],
                  'failures': [{'project_name': '第三项', 'message': '保存失败'}],
                  'recognized_actions': 3, 'saved_actions': 2, 'business_failures': 1,
                  'model_retries': 99}
        handler = BotHandler(self.service, 'test-bot', 'https://tracker.example')
        with patch('backend.app.wecom.ai.parse_message', return_value=result):
            reply = asyncio.run(handler.handle(group_frame('wecom-batch-001', '批量安排')))
        self.assertIn('已识别 3 项安排，成功保存 2 项，失败 1 项。', reply)
        self.assertNotIn('个项目', reply)
        self.assertNotIn('99', reply)

    def test_ignored_message_returns_ai_result_message(self):
        result = {'kind': 'ignored', 'message': '这不是项目安排，未修改数据。'}
        handler = BotHandler(self.service, 'test-bot', 'https://tracker.example')
        with patch('backend.app.wecom.ai.parse_message', return_value=result):
            reply = asyncio.run(handler.handle(group_frame('wecom-ignored-001', '天气不错')))
        self.assertEqual(reply, result['message'])

    def test_complete_message_saves_and_returns_direct_summary(self):
        reply = self.handle()
        self.assertEqual(len(self.service.projects(self.admin, display=True)), 1)
        self.assertEqual(self.drafts(), [])
        self.assertIn('https://tracker.example/#projects', reply)
        self.assertNotIn('草稿', reply)
        self.assertNotIn('保密项目', reply)
        self.assertNotIn(self.admin['access_key'], reply)
        self.assertIn('成功保存 1 项', reply)
        self.assertNotIn('登录', reply)

    def test_group_reply_summarizes_batch_results(self):
        calls = [
            {'name': 'create_project', 'arguments': {'data': {'name': '批量项目甲'}}},
            {'name': 'create_project', 'arguments': {'data': {'name': '批量项目乙'}}},
        ]

        reply = self.handle(parser=lambda _: calls)

        self.assertIn('已识别 2 项安排', reply)
        self.assertIn('成功保存 2 项', reply)
        self.assertIn('失败 0 项', reply)
        self.assertIn('https://tracker.example/#projects', reply)
        self.assertNotIn('个项目', reply)
        self.assertNotIn('批量项目甲', reply)

    def test_redelivery_after_restart_deduplicates_and_tracks_current_status(self):
        first_reply = self.handle()
        self.handler = BotHandler(Service(Store(self.store.path), lambda: self.at), 'bot-1', 'https://tracker.example')
        reply = self.handle()
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.drafts(), [])
        self.assertEqual(len(self.service.projects(self.admin)), 1)
        self.assertEqual(reply, first_reply)
        self.assertIn('成功保存 1 项', reply)

    def test_missing_project_and_fields_do_not_create_fallback_project(self):
        raw = '下周安排调研，负责人之后再定。' * 180
        parser = lambda _: json.dumps({'intent': 'record_item', 'data': {},
                                      'missing_fields': ['text'], 'ambiguities': ['所属项目不明确']})
        reply = self.handle(self.frame(text=raw), parser)
        self.assertIn('请补充', reply)
        self.assertIn('未保存任何项目或事项', reply)
        self.handle(self.frame(text=raw), parser)
        self.assertEqual(self.service.projects(self.admin, display=True), [])
        self.assertEqual(self.drafts(), [])

    def test_group_record_keeps_multiple_parsed_dates(self):
        self.handle()
        self.handle(self.frame(text='锐瀚科技，下周二周三调研', msgid='new-company'), lambda _: json.dumps({
            'intent': 'record_item', 'data': {
                'project_name': '锐瀚科技', 'text': '下周二周三调研', 'items': [
                    {'text': '周二调研', 'due_date': '2026-09-08'},
                    {'text': '周三调研', 'due_date': '2026-09-09'}]}}))
        project = next(p for p in self.service.projects(self.admin) if p['name'] == '锐瀚科技')
        self.assertEqual([n['due_date'] for n in project['milestones']], ['2026-09-08', '2026-09-09'])

    def test_shared_owner_names_and_roles_remain_readable_without_accounts(self):
        self.service.internal_shared = True
        reply = self.handle(parser=lambda _: json.dumps({'intent': 'create_project', 'data': {
            'name': '锐瀚科技', 'owner_assignments': [
                {'name': '张毅', 'role': 'A1', 'primary': True},
                {'name': '朱浩', 'role': 'A2', 'primary': False}]}}))
        self.assertIn('成功保存 1 项', reply)
        self.assertEqual(self.service.projects(self.admin)[0]['owner_name'], '张毅（A1）、朱浩（A2）')

    def test_ambiguous_update_keeps_original_state_without_fallback_item(self):
        self.handle()
        project = self.service.projects(self.admin)[0]
        raw = '有个节点完成了，先记录下来'
        reply = self.handle(self.frame(text=raw, msgid='ambiguous'), lambda _: json.dumps({
            'intent': 'milestone_status', 'project_id': project['id'],
            'data': {'status': 'completed'}, 'ambiguities': ['不知道哪个节点']}))
        self.assertIn('请明确', reply)
        self.assertIn('未保存任何项目或事项', reply)
        saved = self.service.projects(self.admin, display=True)[0]
        self.assertEqual(saved['milestones'][0]['status'], project['milestones'][0]['status'])
        self.assertEqual(len(saved['milestones']), len(project['milestones']))

    def test_group_record_with_ambiguous_time_requests_a_new_complete_message(self):
        reply = self.handle(parser=lambda _: json.dumps({'intent': 'record_item', 'data': {
            'project_name': '调研项目', 'text': '下周调研，随后出方案', 'items': [
                {'text': '调研', 'time_text': '下周'}, {'text': '出方案', 'time_text': '随后'}]},
            'ambiguities': ['具体日期不明确']}))
        self.assertIn('请明确', reply)
        self.assertIn('未保存任何项目或事项', reply)
        self.assertEqual(self.drafts(), [])
        self.assertEqual(self.service.projects(self.admin, display=True), [])

    def test_message_for_closed_project_is_rejected_without_fallback_project(self):
        self.handle()
        project = self.service.projects(self.admin)[0]
        self.service.create_draft(self.admin, Action(intent='project_status', project_id=project['id'],
            data={'status': 'completed', 'reason': '完成'}), auto_save=True)
        draft_count = len(self.drafts())
        reply = self.handle(self.frame(text='再安排一次交流', msgid='closed-project'), lambda _: json.dumps({
            'intent': 'record_item', 'project_id': project['id'], 'data': {'text': '再安排一次交流'}}))
        self.assertIn('失败 1 项', reply)
        self.assertIn('未保存任何项目或事项', reply)
        projects = self.service.projects(self.admin, display=True)
        self.assertEqual(len(projects), 1)
        self.assertEqual(next(p for p in projects if p['id'] == project['id'])['status'], 'completed')
        self.assertEqual(len(self.drafts()), draft_count)

    def test_group_contact_fields_save_as_text_without_exposing_them_in_reply(self):
        contacts = {'contact_company': '合作公司', 'contact_name': '外部联系人', 'contact_info': '微信：partner-test'}
        def parser(prompt):
            self.assertIn('contact_info', prompt)
            result = json.loads(self.parser(prompt))
            result['data'].update(contacts)
            return json.dumps(result)
        reply = self.handle(parser=parser)
        project = self.service.projects(self.admin)[0]
        for field, value in contacts.items():
            self.assertEqual(project[field], value)
            self.assertNotIn(value, reply)

    def test_unbound_disabled_and_display_users_cannot_parse(self):
        self.store.add_user('大屏', 'display', 'screen')
        for userid in ('unbound', 'screen'):
            self.assertIn('配置', self.handle(self.frame(userid=userid)))
        with self.store.connect(write=True) as db:
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.admin['id'],))
        self.handle()
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.drafts(), [])

    def test_foreign_bot_or_incomplete_group_identity_is_ignored(self):
        for frame in (self.frame(aibotid='other-bot'), self.frame(chatid=''), self.frame(msgid=''),
                      self.frame(chattype='single'), {'body': []}, self.frame(**{'from': []})):
            self.assertIsNone(self.handle(frame))
        self.assertEqual(self.calls, 0)

    def test_nontext_and_confirmation_words_do_not_invoke_model(self):
        for frame in (self.frame(msgtype='image'), self.frame(text='确认'),
                      self.frame(text='确认 abcdef123456'), self.frame(text='取消 abcdef123456')):
            self.handle(frame)
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.drafts(), [])

    def test_followup_shaped_text_is_parsed_as_a_new_message(self):
        seen = []
        def parser(prompt):
            seen.append(prompt)
            return json.dumps({'intent': 'ignore'})
        reply = self.handle(self.frame(text='补充 abcdefabcdefabcdefabcdef 日期完整', msgid='new-message'), parser)
        self.assertIn('补充 abcdefabcdefabcdefabcdef 日期完整', seen[0])
        self.assertIn('未识别到明确的项目操作', reply)

    def test_duplicate_does_not_consume_rate_limit_but_new_messages_do(self):
        def incomplete(_):
            self.calls += 1
            return json.dumps({'intent': 'create_project', 'missing_fields': ['name']})
        for i in range(10):
            self.handle(self.frame(msgid=f'msg-{i}'), incomplete)
        self.handle(self.frame(msgid='msg-0'))
        reply = self.handle(self.frame(msgid='over-limit'))
        self.assertEqual(self.calls, 10)
        self.assertIn('频繁', reply)

    def test_model_errors_and_queries_do_not_publish_project_data(self):
        def broken(_):
            raise RuntimeError('secret-key-SENSITIVE')
        reply = self.handle(parser=broken)
        self.assertNotIn('secret-key', reply)
        self.assertIn('失败', reply)
        self.handle(self.frame(msgid='create'))
        reply = self.handle(self.frame(msgid='query'), lambda _: '{"intent":"query"}')
        self.assertIn('https://tracker.example/', reply)
        self.assertNotIn('保密项目', reply)

    def test_disabled_or_rebound_sender_during_model_call_receives_no_link(self):
        def rebind(prompt):
            with self.store.connect(write=True) as db:
                db.execute("UPDATE users SET wecom_user_id='new-employee' WHERE id=?", (self.admin['id'],))
            return self.parser(prompt)
        reply = self.handle(parser=rebind)
        self.assertNotIn('#draft=', reply)

    def test_revoked_project_access_rejects_cached_preview(self):
        self.handle()
        project = self.service.projects(self.admin)[0]
        with self.store.connect(write=True) as db:
            db.execute("UPDATE users SET role='member' WHERE id=?", (self.admin['id'],))
        action = {'intent': 'edit_project', 'project_id': project['id'], 'data': {'name': '修改', 'reason': '修正'}}
        self.handle(self.frame(msgid='edit'), lambda _: json.dumps(action))
        with self.store.connect(write=True) as db:
            value = json.loads(db.execute('SELECT data FROM projects').fetchone()[0])
            value['project_info']['member_ids'] = []
            db.execute('UPDATE projects SET data=?', (json.dumps(value),))
        reply = self.handle(self.frame(msgid='edit'))
        self.assertNotIn('#draft=', reply)

    def test_runtime_lease_and_stale_status_are_persistent(self):
        runtime = BotRuntime(self.service, 'bot-1')
        runtime.acquire()
        runtime.update('authenticated')
        self.assertEqual(read_status(self.service)['wecom_inbound'], 'authenticated')
        other = BotRuntime(self.service, 'bot-1')
        with self.assertRaises(BusinessError):
            other.acquire()
        self.at += timedelta(seconds=91)
        self.assertEqual(read_status(self.service)['wecom_inbound'], 'offline')
        other.acquire()
        other.update('connecting')
        runtime.close()
        self.assertEqual(read_status(self.service)['wecom_inbound'], 'connecting')
        other.close()
        self.assertEqual(read_status(self.service)['wecom_inbound'], 'stopped')

    def test_public_url_must_not_contain_credentials_or_query(self):
        for url in ('', 'javascript:alert(1)', 'https://user:password@example.com',
                    'https://example.com/?token=secret', 'https://example.com/#display',
                    'https://example.com/subpath', 'https://example.com/\n'):
            with self.assertRaises(BusinessError):
                validate_web_url(url)
        self.assertEqual(validate_web_url('https://tracker.example/'), 'https://tracker.example')

    def test_web_and_wecom_message_ids_do_not_collide(self):
        request = MessageInput(text='创建', client_message_id='same-message-key')
        web = asyncio.run(parse_message(self.service, self.admin, request, self.parser))
        wecom = asyncio.run(parse_message(self.service, self.admin, request, self.parser, channel='wecom'))
        self.assertEqual(web['kind'], 'saved')
        self.assertEqual(wecom['kind'], 'saved')
        with self.store.connect() as db:
            ids = {row['id'] for row in db.execute('SELECT id FROM messages')}
        self.assertEqual(ids, {
            'web:' + self.admin['id'] + ':same-message-key',
            'wecom:' + self.admin['id'] + ':same-message-key',
        })
        self.assertEqual(self.drafts(), [])

    def test_pairing_command_is_retired_without_invoking_model(self):
        reply = self.handle(self.frame(text='绑定 dead-code', userid='employee.1'))
        self.assertIn('无需绑定', reply)
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.drafts(), [])

    def test_mentioned_confirmation_does_not_invoke_model(self):
        reply = self.handle(self.frame(text='@项目机器人\u2005确认'))
        self.assertEqual(self.calls, 0)
        self.assertIn('无需确认', reply)

    def test_mentioned_followup_shaped_text_is_parsed_as_a_new_message(self):
        seen = []
        def parser(prompt):
            seen.append(prompt)
            return json.dumps({'intent': 'ignore'})
        self.handle(self.frame(text='@项目机器人\u2005补充 abcdefabcdefabcdefabcdef 日期完整',
                               msgid='mentioned-followup'), parser)
        self.assertIn('补充 abcdefabcdefabcdefabcdef 日期完整', seen[0])

    def test_group_reply_reports_recognized_intent_without_project_details(self):
        reply = self.handle()
        self.assertIn('已识别 1 项安排', reply)
        self.assertIn('成功保存 1 项', reply)
        self.assertNotIn('保密项目', reply)

    def test_sdk_worker_registers_callbacks_replies_and_closes_cleanly(self):
        from backend.wecom_worker import run_bot

        async def scenario():
            stop = asyncio.Event()
            test = self

            class SDKDouble:
                def __init__(self):
                    self.events, self.replies, self.disconnected = {}, [], False

                def on(self, name, callback):
                    self.events[name] = callback

                async def connect(self):
                    self.events['authenticated']()
                    test.assertEqual(read_status(test.service)['wecom_inbound'], 'authenticated')
                    await self.events['message'](test.frame())
                    stop.set()

                async def reply_stream(self, frame, stream_id, content, finish):
                    self.replies.append((frame['headers']['req_id'], stream_id, content, finish))

                def disconnect(self):
                    self.disconnected = True

            client = SDKDouble()
            await run_bot(self.service, 'bot-1', 'https://tracker.example', client, stop, self.parser)
            self.assertTrue(client.disconnected)
            self.assertEqual(len(client.replies), 2)
            self.assertFalse(client.replies[0][3])
            self.assertTrue(client.replies[1][3])
            self.assertEqual(client.replies[0][1], client.replies[1][1])
            self.assertIn('#projects', client.replies[1][2])
            self.assertEqual(read_status(self.service)['wecom_inbound'], 'stopped')

        asyncio.run(scenario())

    def test_sdk_connection_failure_releases_lease(self):
        from backend.wecom_worker import run_bot

        class BrokenSDK:
            disconnected = False

            def on(self, name, callback):
                pass

            async def connect(self):
                raise RuntimeError('secret-key-MUST-NOT-LOG')

            def disconnect(self):
                self.disconnected = True

        client = BrokenSDK()
        with self.assertRaises(RuntimeError):
            asyncio.run(run_bot(self.service, 'bot-1', 'https://tracker.example', client))
        self.assertTrue(client.disconnected)
        self.assertEqual(read_status(self.service)['wecom_inbound'], 'stopped')

