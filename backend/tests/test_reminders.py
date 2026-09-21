import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx

from backend.app.models import Action, ReminderSettings
from backend.app.reminders import (WeComSender, acquire_lease, dispatch, is_workday,
                                   node_flags, run_cycle, scan, shift_workdays)
from backend.app.service import Service, TZ
from backend.app.store import Store, encode


def moment(value):
    return datetime.fromisoformat(value).replace(tzinfo=TZ)


class RecordingSender:
    configured = True

    def __init__(self, result=('accepted', '平台已接受')):
        self.result = result
        self.messages = []

    def send(self, user_id, content):
        self.messages.append((user_id, content))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class ReminderTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name) / 'reminders.sqlite3')
        self.owner = self.store.add_user('节点负责人', wecom_user_id='owner')
        self.other = self.store.add_user('另一负责人', wecom_user_id='other')
        self.now = moment('2026-09-04T10:00:00')
        self.service = Service(self.store, clock=lambda: self.now)
        action = Action(intent='create_project', data={
            'name': '展厅改造', 'owner_id': self.owner['id'],
            'start_date': '2026-08-01', 'due_date': '2026-10-31',
            'milestones': [{'name': '采购到位', 'criterion': '全部材料到货',
                            'owner_id': self.owner['id'], 'start_date': '2026-08-31',
                            'due_date': '2026-09-30', 'update_interval': 2}]})
        draft = self.service.create_draft(self.owner, action)
        self.pid = self.service.confirm(self.owner, draft['id'])['project_id']
        self.nid = self.project()['milestones'][0]['id']

    def project(self):
        with self.store.connect() as db:
            return self.store.project(db.execute('SELECT * FROM projects WHERE id=?', (self.pid,)).fetchone())

    def edit_fixture(self, node=None, project=None):
        current = self.project()
        current.update(project or {})
        current['milestones'][0].update(node or {})
        with self.store.connect(write=True) as db:
            db.execute('UPDATE projects SET data=?,version=version+1 WHERE id=?', (encode(current), self.pid))

    def reminders(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM reminders ORDER BY local_day,id')]

    def flags(self, at=None, settings=None):
        project = self.project()
        return node_flags(project, project['milestones'][0], at or self.now, settings or ReminderSettings())

    def confirm(self, intent, data, node=True):
        action = Action(intent=intent, project_id=self.pid,
                        milestone_id=self.nid if node else None, data=data)
        draft = self.service.create_draft(self.owner, action)
        return self.service.confirm(self.owner, draft['id'])

    def test_workday_holiday_and_makeup_shift_preserve_local_time(self):
        settings = ReminderSettings(workday_overrides={'2026-09-04': False, '2026-09-05': True})
        self.assertFalse(is_workday(moment('2026-09-04T10:00').date(), settings))
        self.assertTrue(is_workday(moment('2026-09-05T10:00').date(), settings))
        self.assertEqual(shift_workdays(moment('2026-09-03T14:30'), 2, settings), moment('2026-09-07T14:30'))
        self.assertEqual(shift_workdays(moment('2026-09-07T14:30'), -2, settings), moment('2026-09-03T14:30'))

    def test_unreported_node_stale_at_interval_but_not_before_start(self):
        cases = [('2026-08-28T10:00', []), ('2026-09-02T08:59:59', []),
                 ('2026-09-02T09:00', ['stale'])]
        for at, expected in cases:
            with self.subTest(at=at):
                self.assertEqual(self.flags(moment(at)), expected)

    def test_due_time_boundary_and_reasons_priority(self):
        self.edit_fixture(node={'due_date': '2026-09-04', 'blocker': '缺货'})
        cases = [('2026-09-03T17:59:59', ['stale', 'blocked']),
                 ('2026-09-03T18:00', ['stale', 'due_soon', 'blocked']),
                 ('2026-09-04T18:00', ['stale', 'due_soon', 'blocked']),
                 ('2026-09-04T18:00:01', ['overdue', 'stale', 'blocked'])]
        for at, expected in cases:
            with self.subTest(at=at):
                self.assertEqual(self.flags(moment(at)), expected)

    def test_worker_auto_completes_untouched_item_at_due_hour_and_audits_system_source(self):
        self.edit_fixture(node={'due_date': '2026-09-04'})
        self.assertEqual(run_cycle(self.store, RecordingSender(), moment('2026-09-04T17:59')).get('auto_completed'), 0)
        self.assertEqual(self.project()['milestones'][0]['status'], 'active')

        result = run_cycle(self.store, RecordingSender(), moment('2026-09-04T18:00'))
        project = self.project()
        node = project['milestones'][0]
        self.assertEqual(result['auto_completed'], 1)
        self.assertEqual((node['status'], node['progress']), ('completed', 100))
        self.assertEqual(node['completed_at'], '2026-09-04T18:00:00+08:00')
        self.assertEqual(node['completion_source'], 'automatic')
        self.assertEqual(project['status'], 'active')
        with self.store.connect() as db:
            audit = db.execute('SELECT user_id,intent,before_data,after_data FROM audit ORDER BY id DESC LIMIT 1').fetchone()
        self.assertEqual((audit['user_id'], audit['intent']), ('system:auto-complete', 'milestone_status'))
        self.assertEqual(json.loads(audit['before_data'])['milestones'][0]['status'], 'active')
        self.assertEqual(json.loads(audit['after_data'])['milestones'][0]['status'], 'completed')

    def test_manual_progress_report_permanently_prevents_automatic_completion(self):
        self.edit_fixture(node={'due_date': '2026-09-04'})
        self.confirm('report_progress', {'summary': '存在现场问题', 'blocker': '设备尚未到货'})

        self.assertEqual(run_cycle(self.store, RecordingSender(), moment('2026-09-04T18:00')).get('auto_completed'), 0)
        node = self.project()['milestones'][0]
        self.assertEqual(node['status'], 'active')
        self.assertTrue(node['auto_complete_disabled'])

    def test_manual_status_change_permanently_prevents_automatic_completion(self):
        self.edit_fixture(node={'due_date': '2026-09-04'})
        self.confirm('milestone_status', {'status': 'paused', 'reason': '等待人工处理'})
        self.confirm('milestone_status', {'status': 'active', 'reason': '继续处理'})

        self.assertEqual(run_cycle(self.store, RecordingSender(), moment('2026-09-04T18:00')).get('auto_completed'), 0)
        node = self.project()['milestones'][0]
        self.assertEqual(node['status'], 'active')
        self.assertTrue(node['auto_complete_disabled'])

    def test_holiday_and_makeup_dates_control_stale_and_due_soon(self):
        self.edit_fixture(node={'start_date': '2026-09-03', 'due_date': '2026-09-07'})
        settings = ReminderSettings(workday_overrides={'2026-09-04': False, '2026-09-05': True})
        self.assertEqual(self.flags(moment('2026-09-05T17:59'), settings), [])
        self.assertEqual(self.flags(moment('2026-09-05T18:00'), settings), ['due_soon'])
        self.assertEqual(self.flags(moment('2026-09-07T09:00'), settings), ['stale', 'due_soon'])

    def test_project_and_node_pause_stop_reminders_resume_restarts_interval(self):
        for node in (True, False):
            with self.subTest(node=node):
                self.confirm('milestone_status' if node else 'project_status',
                             {'status': 'paused', 'reason': '等待审批'}, node=node)
                self.assertEqual(self.flags(), [])
                self.confirm('milestone_status' if node else 'project_status',
                             {'status': 'active', 'reason': '审批完成'}, node=node)
                self.assertEqual(self.flags(moment('2026-09-08T09:59:59')), [])
                self.assertEqual(self.flags(moment('2026-09-08T10:00')), ['stale'])

    def test_historical_report_does_not_clear_stale_or_current_blocker(self):
        self.edit_fixture(node={'blocker': '待审批'})
        self.confirm('report_progress', {'summary': '补录上周汇报', 'historical': True,
                     'event_date': '2026-08-31', 'progress': 80, 'clear_fields': ['blocker']})
        node = self.project()['milestones'][0]
        self.assertEqual(self.flags(), ['stale', 'blocked'])
        self.assertIsNone(node['last_report_at'])
        self.assertEqual(node['progress'], 0)
        self.assertEqual(node['blocker'], '待审批')

    def test_current_report_only_clears_its_node_stale_not_overdue(self):
        project = self.project()
        sibling = copy.deepcopy(project['milestones'][0])
        sibling.update(id='other-node', name='施工完成')
        self.edit_fixture(node={'due_date': '2026-09-03'},
                          project={'milestones': [project['milestones'][0], sibling]})
        self.confirm('report_progress', {'summary': '材料已下单', 'progress': 50})
        project = self.project()
        self.assertEqual(self.flags(), ['overdue'])
        self.assertEqual(node_flags(project, project['milestones'][1], self.now, ReminderSettings()), ['stale'])

    def test_daily_deduplication_survives_reopen_and_combines_reasons(self):
        self.edit_fixture(node={'due_date': '2026-09-03', 'auto_complete_disabled': True})
        sender = RecordingSender()
        self.assertEqual(run_cycle(self.store, sender, self.now)['accepted'], 1)
        self.assertEqual(json.loads(self.reminders()[0]['reasons']), ['overdue', 'stale'])
        reopened = Store(self.store.path)
        self.assertEqual(run_cycle(reopened, sender, self.now + timedelta(minutes=10))['accepted'], 0)
        self.assertEqual(len(sender.messages), 1)
        self.assertEqual(sender.messages[0][0], 'owner')
        self.assertIn('计划逾期、待更新', sender.messages[0][1])
        self.assertEqual(run_cycle(reopened, sender, moment('2026-09-07T10:00'))['accepted'], 1)

    def test_same_owner_nodes_share_one_message(self):
        project = self.project()
        sibling = copy.deepcopy(project['milestones'][0])
        sibling.update(id='second-node', name='施工完成')
        self.edit_fixture(project={'milestones': project['milestones'] + [sibling]})
        sender = RecordingSender()
        self.assertEqual(run_cycle(self.store, sender, self.now)['accepted'], 2)
        self.assertEqual(len(sender.messages), 1)
        self.assertIn('采购到位', sender.messages[0][1])
        self.assertIn('施工完成', sender.messages[0][1])
        self.assertEqual([r['status'] for r in self.reminders()], ['accepted', 'accepted'])

    def test_dispatch_window_is_exclusive_at_end_and_respects_makeup_days(self):
        scan(self.store, self.now)
        sender = RecordingSender()
        for at in ('2026-09-04T08:59', '2026-09-04T18:00', '2026-09-05T10:00'):
            self.assertEqual(dispatch(self.store, sender, moment(at)), 0)
        settings = ReminderSettings(workday_overrides={'2026-09-05': True})
        with self.store.connect(write=True) as db:
            db.execute("INSERT INTO settings VALUES('reminders',?)", (settings.model_dump_json(),))
        self.assertEqual(run_cycle(self.store, sender, moment('2026-09-05T09:00'))['accepted'], 1)
        self.assertEqual(len(sender.messages), 1)

    def test_old_pending_days_expire_instead_of_being_replayed(self):
        scan(self.store, moment('2026-09-02T10:00'))
        scan(self.store, moment('2026-09-03T10:00'))
        sender = RecordingSender()
        result = run_cycle(self.store, sender, self.now)
        self.assertEqual(result['accepted'], 1)
        self.assertEqual([r['status'] for r in self.reminders()], ['cancelled', 'cancelled', 'accepted'])
        self.assertEqual(len(sender.messages), 1)

    def test_dispatch_rechecks_completed_paused_updated_or_reassigned_nodes(self):
        changes = [({'status': 'completed'}, {}), ({'status': 'paused'}, {}),
                   ({}, {'status': 'paused'}), ({'last_report_at': self.now.isoformat()}, {}),
                   ({'owner_id': self.other['id']}, {})]
        original = self.project()
        for node_changes, project_changes in changes:
            with self.subTest(node=node_changes, project=project_changes):
                self.edit_fixture(project=copy.deepcopy(original))
                scan(self.store, self.now)
                self.edit_fixture(node=node_changes, project=project_changes)
                sender = RecordingSender()
                self.assertEqual(dispatch(self.store, sender, self.now), 0)
                self.assertEqual(sender.messages, [])
                self.assertEqual(self.reminders()[0]['status'], 'cancelled')

    def test_reassigned_node_is_requeued_for_current_owner(self):
        scan(self.store, self.now)
        self.edit_fixture(node={'owner_id': self.other['id']})
        sender = RecordingSender()
        self.assertEqual(dispatch(self.store, sender, self.now), 0)
        scan(self.store, self.now)
        self.assertEqual(dispatch(self.store, sender, self.now), 1)
        self.assertEqual(sender.messages[0][0], 'other')

    def test_invalid_owner_cannot_receive_even_if_role_changes_after_scan(self):
        for updates in ({'active': 0}, {'wecom_user_id': ''}, {'role': 'display'}):
            with self.subTest(updates=updates):
                with self.store.connect(write=True) as db:
                    db.execute("UPDATE users SET active=1,wecom_user_id='owner',role='member' WHERE id=?", (self.owner['id'],))
                scan(self.store, self.now)
                key, value = next(iter(updates.items()))
                with self.store.connect(write=True) as db:
                    db.execute(f'UPDATE users SET {key}=? WHERE id=?', (value, self.owner['id']))
                sender = RecordingSender()
                self.assertEqual(dispatch(self.store, sender, self.now), 0)
                self.assertEqual(self.reminders()[0]['status'], 'blocked')
                self.assertEqual(sender.messages, [])

    def test_explicit_failure_retries_with_delay_and_stops_after_three(self):
        sender = RecordingSender(('failed', '平台拒绝'))
        run_cycle(self.store, sender, self.now)
        run_cycle(self.store, sender, self.now + timedelta(minutes=9))
        self.assertEqual(len(sender.messages), 1)
        run_cycle(self.store, sender, self.now + timedelta(minutes=10))
        run_cycle(self.store, sender, self.now + timedelta(minutes=20))
        run_cycle(self.store, sender, self.now + timedelta(minutes=30))
        self.assertEqual(len(sender.messages), 3)
        self.assertEqual(self.reminders()[0]['attempts'], 3)
        self.assertEqual(self.reminders()[0]['status'], 'failed')

    def test_uncertain_exception_and_unknown_results_are_not_retried(self):
        for result in (TimeoutError('unknown delivery'), ('uncertain', '需核对'), ('unexpected', 'bad status')):
            with self.subTest(result=result):
                with self.store.connect(write=True) as db:
                    db.execute('DELETE FROM reminders')
                sender = RecordingSender(result)
                self.assertEqual(run_cycle(self.store, sender, self.now)['accepted'], 0)
                run_cycle(self.store, sender, self.now + timedelta(minutes=20))
                self.assertEqual(self.reminders()[0]['status'], 'uncertain')
                self.assertEqual(len(sender.messages), 1)

    def test_interrupted_send_becomes_uncertain_without_replay(self):
        scan(self.store, self.now)
        with self.store.connect(write=True) as db:
            db.execute("UPDATE reminders SET status='sending',attempts=1")
        sender = RecordingSender()
        run_cycle(self.store, sender, self.now + timedelta(minutes=11))
        self.assertEqual(self.reminders()[0]['status'], 'uncertain')
        self.assertEqual(sender.messages, [])

    def test_worker_lease_prevents_second_worker_until_expiry(self):
        self.assertTrue(acquire_lease(self.store, 'first', self.now))
        self.assertFalse(acquire_lease(self.store, 'second', self.now))
        self.assertEqual(run_cycle(self.store, RecordingSender(), self.now), {'skipped': True})
        self.assertTrue(acquire_lease(self.store, 'second', self.now + timedelta(minutes=30)))

    def test_backoff_owners_do_not_starve_ready_owner(self):
        project = self.project()
        nodes, members = [], [self.owner['id']]
        for index in range(20):
            user = self.store.add_user(f'待重试负责人{index}', wecom_user_id=f'waiting-{index}')
            node = copy.deepcopy(project['milestones'][0])
            node.update(id=f'waiting-{index}', owner_id=user['id'])
            nodes.append(node)
            members.append(user['id'])
        self.edit_fixture(project={'milestones': nodes + project['milestones'], 'member_ids': members})
        scan(self.store, self.now)
        with self.store.connect(write=True) as db:
            db.execute("UPDATE reminders SET status='failed',next_attempt=? WHERE milestone_id LIKE 'waiting-%'",
                       ((self.now + timedelta(minutes=10)).isoformat(),))
        sender = RecordingSender()
        self.assertEqual(dispatch(self.store, sender, self.now), 1)
        self.assertEqual(sender.messages[0][0], 'owner')

    def test_unbound_owners_do_not_starve_ready_owner(self):
        project = self.project()
        nodes, members = [], [self.owner['id']]
        for index in range(20):
            user = self.store.add_user(f'未绑定负责人{index}')
            node = copy.deepcopy(project['milestones'][0])
            node.update(id=f'unbound-{index}', owner_id=user['id'])
            nodes.append(node)
            members.append(user['id'])
        self.edit_fixture(project={'milestones': nodes + project['milestones'], 'member_ids': members})
        sender = RecordingSender()
        self.assertEqual(run_cycle(self.store, sender, self.now)['accepted'], 1)
        self.assertEqual(sender.messages[0][0], 'owner')
        self.assertEqual(sum(r['status'] == 'blocked' for r in self.reminders()), 20)

    def test_batch_limit_leaves_remaining_owner_for_next_dispatch(self):
        project = self.project()
        nodes = project['milestones']
        for index in range(20):
            user = self.store.add_user(f'负责人{index}', wecom_user_id=f'owner-{index}')
            node = copy.deepcopy(nodes[0])
            node.update(id=f'node-{index}', owner_id=user['id'])
            nodes.append(node)
        self.edit_fixture(project={'milestones': nodes})
        sender = RecordingSender()
        scan(self.store, self.now)
        self.assertEqual(dispatch(self.store, sender, self.now), 20)
        self.assertEqual(sum(r['status'] == 'queued' for r in self.reminders()), 1)
        self.assertEqual(dispatch(self.store, sender, self.now), 1)
        self.assertEqual(len(sender.messages), 21)
        self.assertEqual(len({recipient for recipient, _ in sender.messages}), 21)


class WeComSenderTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {'TRACKER_WECOM_SEND_ENABLED': 'true', 'WECOM_CORP_ID': 'test-corp',
                               'WECOM_APP_SECRET': 'test-secret', 'WECOM_AGENT_ID': '1'}).start()
        self.requests = []
        self.client_type = httpx.Client

    def send(self, token=None, message=None, token_error=None, message_error=None):
        def handler(request):
            self.requests.append(request)
            if request.url.path.endswith('gettoken'):
                if token_error:
                    raise token_error
                return httpx.Response(200, json={'access_token': 'test-token', 'errcode': 0} if token is None else token)
            if message_error:
                raise message_error
            return httpx.Response(200, json={'errcode': 0} if message is None else message)
        with patch('backend.app.reminders.httpx.Client',
                   side_effect=lambda **kwargs: self.client_type(transport=httpx.MockTransport(handler), **kwargs)):
            return WeComSender().send('owner', '待更新项目')

    def test_disabled_sender_does_not_attempt_network(self):
        with patch.dict(os.environ, {'TRACKER_WECOM_SEND_ENABLED': 'false'}):
            self.assertEqual(self.send()[0], 'blocked')
        self.assertEqual(self.requests, [])

    def test_authentication_failure_is_safe_to_retry_without_business_send(self):
        self.assertEqual(self.send(token_error=httpx.ConnectTimeout('timed out'))[0], 'failed')
        self.assertEqual([r.method for r in self.requests], ['GET'])

    def test_send_timeout_is_uncertain_and_never_claims_acceptance(self):
        self.assertEqual(self.send(message_error=httpx.ReadTimeout('timed out'))[0], 'uncertain')
        self.assertEqual([r.method for r in self.requests], ['GET', 'POST'])

    def test_acceptance_uses_only_bound_recipient_and_not_read_receipt(self):
        status, detail = self.send()
        self.assertEqual(status, 'accepted')
        self.assertIn('不代表员工已读', detail)
        payload = json.loads(self.requests[-1].content)
        self.assertEqual(payload['touser'], 'owner')
        self.assertEqual(payload['text']['content'], '待更新项目')
        self.assertEqual(payload['agentid'], 1)
        self.assertNotIn('toparty', payload)
        self.assertNotIn('totag', payload)

    def test_rejected_or_invalid_recipient_is_failed(self):
        for message in ({'errcode': 40003}, {'errcode': 0, 'invaliduser': 'owner'},
                        {'errcode': 0, 'unlicenseduser': 'owner'}):
            with self.subTest(message=message):
                self.assertEqual(self.send(message=message)[0], 'failed')

    def test_malformed_token_shape_is_failed_without_sending(self):
        self.assertEqual(self.send(token=['invalid'])[0], 'failed')
        self.assertEqual([r.method for r in self.requests], ['GET'])

    def test_malformed_send_shape_is_uncertain(self):
        self.assertEqual(self.send(message=['invalid'])[0], 'uncertain')

    def test_missing_send_error_code_is_uncertain_not_retryable(self):
        self.assertEqual(self.send(message={})[0], 'uncertain')


if __name__ == '__main__':
    unittest.main()
