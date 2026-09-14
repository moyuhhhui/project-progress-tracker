import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from backend.app.models import Action, MessageInput
from backend.app.ai import parse_message
from backend.app.service import Service, TZ, BusinessError
from backend.app.store import Store
from backend.app.wecom import BotHandler, BotRuntime, read_status, validate_web_url


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

    def test_query_reply_contains_project_and_milestone_status(self):
        reply = self.handler.format_query_reply([{
            'name': '仓储系统升级', 'code': 'P0001', 'status': 'active', 'owner_name': '柯金成',
            'progress': 40, 'due_date': '2026-10-30', 'flags': ['blocked'],
            'milestones': [{'name': '方案设计', 'status': 'paused', 'progress': 20,
                            'owner_name': '朱浩', 'due_date': '2026-09-30',
                            'blocker': '等待确认', 'next_step': '补充方案'}],
        }])
        self.assertIn('项目状态：仓储系统升级', reply)
        self.assertIn('整体状态：进行中', reply)
        self.assertIn('当前风险：有阻碍', reply)
        self.assertIn('方案设计：已暂停，进度 20%', reply)
        self.assertIn('阻碍：等待确认', reply)

    def legacy_group_draft(self):
        from backend.app.ai import save_incomplete
        draft = save_incomplete(self.service, self.admin, Action(intent='create_project'),
                                '创建项目', {'missing_fields': ['name']})
        with self.store.connect(write=True) as db:
            db.execute('INSERT INTO wecom_drafts VALUES(?,?,?,?)',
                       (draft['id'], 'bot-1', 'group-1', self.admin['id']))
        return draft['id']

    def test_complete_message_saves_without_confirmation_and_returns_detail_link(self):
        reply = self.handle()
        draft = self.drafts()[0]
        self.assertEqual(len(self.service.projects(self.admin, display=True)), 1)
        self.assertEqual(draft['status'], 'confirmed')
        self.assertIn('https://tracker.example/#projects', reply)
        self.assertNotIn('草稿', reply)
        self.assertNotIn('保密项目', reply)
        self.assertNotIn(self.admin['access_key'], reply)
        self.assertIn('已自动保存', reply)
        self.assertNotIn('登录', reply)
        self.service.confirm(self.admin, draft['id'])
        self.assertEqual(len(self.service.projects(self.admin)), 1)

    def test_group_reply_summarizes_batch_results(self):
        calls = [
            {'name': 'create_project', 'arguments': {'data': {'name': '批量项目甲'}}},
            {'name': 'create_project', 'arguments': {'data': {'name': '批量项目乙'}}},
        ]

        reply = self.handle(parser=lambda _: calls)

        self.assertIn('已处理 2 个项目', reply)
        self.assertIn('成功保存 2 项', reply)
        self.assertIn('https://tracker.example/#projects', reply)
        self.assertNotIn('批量项目甲', reply)

    def test_redelivery_after_restart_deduplicates_and_tracks_current_status(self):
        self.handle()
        did = self.drafts()[0]['id']
        self.service.confirm(self.admin, did)
        self.handler = BotHandler(Service(Store(self.store.path), lambda: self.at), 'bot-1', 'https://tracker.example')
        reply = self.handle()
        self.assertEqual(self.calls, 1)
        self.assertEqual(len(self.drafts()), 1)
        self.assertIn('已自动保存', reply)

    def test_missing_project_and_fields_do_not_create_fallback_project(self):
        raw = '下周安排调研，负责人之后再定。' * 180
        parser = lambda _: json.dumps({'intent': 'record_item', 'data': {},
                                      'missing_fields': ['text'], 'ambiguities': ['所属项目不明确']})
        reply = self.handle(self.frame(text=raw), parser)
        self.assertIn('无法处理', reply)
        self.handle(self.frame(text=raw), parser)
        self.assertEqual(self.service.projects(self.admin, display=True), [])
        self.assertEqual(self.drafts(), [])

    def test_rephrased_create_prefix_does_not_duplicate_group_arrangement(self):
        parser = lambda _: json.dumps({'intent': 'record_item', 'data': {
            'project_name': '锐瀚科技', 'text': '去工厂调研', 'due_date': '2026-09-08'}})
        self.handle(self.frame(text='锐瀚科技，下周二去工厂调研@1'), parser)
        self.handle(self.frame(text='新建项目，锐瀚科技，下周二去工厂调研@1', msgid='second-message'), parser)
        self.assertEqual(len(self.service.projects(self.admin)[0]['milestones']), 1)

    def test_wrong_candidate_id_does_not_discard_parsed_dates(self):
        self.handle()
        wrong = self.service.projects(self.admin)[0]
        self.handle(self.frame(text='锐瀚科技，下周二周三调研', msgid='new-company'), lambda _: json.dumps({
            'intent': 'record_item', 'project_id': wrong['id'], 'data': {
                'project_name': '锐瀚科技', 'text': '下周二周三调研', 'items': [
                    {'text': '周二调研', 'due_date': '2026-09-08'},
                    {'text': '周三调研', 'due_date': '2026-09-09'}]}}))
        project = next(p for p in self.service.projects(self.admin) if p['name'] == '锐瀚科技')
        self.assertEqual([n['due_date'] for n in project['milestones']], ['2026-09-08', '2026-09-09'])

    def test_shared_owner_names_and_roles_remain_readable_without_accounts(self):
        from backend.app.ai import save_group_message
        self.service.internal_shared = True
        draft = save_group_message(self.service, self.admin, Action(intent='create_project', data={
            'name': '锐瀚科技', 'owner_roles': {'张毅': 'A1', '朱浩': 'A2'}}), '锐瀚科技负责人张毅a1朱浩a2', {})
        self.assertEqual(draft['status'], 'confirmed')
        self.assertEqual(self.service.projects(self.admin)[0]['owner_name'], '张毅（A1）、朱浩（A2）')

    def test_ambiguous_update_keeps_original_state_without_fallback_item(self):
        self.handle()
        project = self.service.projects(self.admin)[0]
        raw = '有个节点完成了，先记录下来'
        reply = self.handle(self.frame(text=raw, msgid='ambiguous'), lambda _: json.dumps({
            'intent': 'milestone_status', 'project_id': project['id'],
            'data': {'status': 'completed'}, 'ambiguities': ['不知道哪个节点']}))
        self.assertIn('无法处理', reply)
        saved = self.service.projects(self.admin, display=True)[0]
        self.assertEqual(saved['milestones'][0]['status'], project['milestones'][0]['status'])
        self.assertEqual(len(saved['milestones']), len(project['milestones']))

    def test_group_record_keeps_multiple_items_and_uncertain_time_without_draft(self):
        reply = self.handle(parser=lambda _: json.dumps({'intent': 'record_item', 'data': {
            'project_name': '调研项目', 'text': '下周调研，随后出方案', 'items': [
                {'text': '调研', 'time_text': '下周'}, {'text': '出方案', 'time_text': '随后'}]},
            'ambiguities': ['具体日期不明确']}))
        self.assertIn('已自动保存', reply)
        nodes = self.service.projects(self.admin, display=True)[0]['milestones']
        self.assertEqual([n['time_text'] for n in nodes], ['下周', '随后'])
        self.assertEqual([n['due_date'] for n in nodes], [None, None])

    def test_message_for_closed_project_is_rejected_without_fallback_project(self):
        self.handle()
        project = self.service.projects(self.admin)[0]
        self.service.create_draft(self.admin, Action(intent='project_status', project_id=project['id'],
            data={'status': 'completed', 'reason': '完成'}), auto_save=True)
        reply = self.handle(self.frame(text='再安排一次交流', msgid='closed-project'), lambda _: json.dumps({
            'intent': 'record_item', 'project_id': project['id'], 'data': {'text': '再安排一次交流'}}))
        self.assertIn('无法处理', reply)
        projects = self.service.projects(self.admin, display=True)
        self.assertEqual(len(projects), 1)
        self.assertEqual(next(p for p in projects if p['id'] == project['id'])['status'], 'completed')
        self.assertTrue(all(d['status'] == 'confirmed' for d in self.drafts()))

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

    def test_followup_is_bound_to_sender_and_original_group(self):
        did = self.legacy_group_draft()
        for changes in ({'chatid': 'another-group'}, {'userid': 'employee.2'}):
            reply = self.handle(self.frame(text=f'补充 {did} 日期完整', msgid='follow-denied', **changes))
            self.assertIn('无法', reply)
        self.assertEqual(self.calls, 0)
        self.handle(self.frame(text=f'补充 {did} 日期完整', msgid='follow-allowed'))
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.service.draft(self.admin, did)['status'], 'cancelled')
        self.assertEqual(len(self.drafts()), 2)

    def test_web_draft_cannot_be_used_as_group_followup(self):
        result = asyncio.run(parse_message(self.service, self.admin,
            MessageInput(text='创建', client_message_id='web-message'), self.parser))
        self.handle(self.frame(text='补充 ' + result['draft']['id'] + ' 日期', msgid='follow-web'))
        self.assertEqual(self.calls, 1)

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
        self.service.confirm(self.admin, self.drafts()[0]['id'])
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
        self.service.confirm(self.admin, self.drafts()[0]['id'])
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
        asyncio.run(parse_message(self.service, self.admin, request, self.parser))
        with self.assertRaises(BusinessError):
            asyncio.run(parse_message(self.service, self.admin, request, self.parser, channel='wecom'))
        with self.store.connect() as db:
            ids = {row['id'] for row in db.execute('SELECT id FROM messages')}
        self.assertEqual(ids, {
            'web:' + self.admin['id'] + ':same-message-key',
            'wecom:' + self.admin['id'] + ':same-message-key',
        })
        self.assertEqual(len(self.drafts()), 1)

    def test_pairing_command_is_retired_without_invoking_model(self):
        reply = self.handle(self.frame(text='绑定 dead-code', userid='employee.1'))
        self.assertIn('无需绑定', reply)
        self.assertEqual(self.calls, 0)
        self.assertEqual(self.drafts(), [])

    def test_mentioned_confirmation_does_not_invoke_model(self):
        reply = self.handle(self.frame(text='@项目机器人\u2005确认'))
        self.assertEqual(self.calls, 0)
        self.assertIn('无需确认', reply)

    def test_mentioned_followup_uses_existing_draft(self):
        did = self.legacy_group_draft()
        self.handle(self.frame(text=f'@项目机器人\u2005补充 {did} 日期完整', msgid='mentioned-followup'))
        self.assertEqual(self.service.draft(self.admin, did)['status'], 'cancelled')

    def test_group_reply_reports_recognized_intent_without_project_details(self):
        reply = self.handle()
        self.assertIn('已自动保存', reply)
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
