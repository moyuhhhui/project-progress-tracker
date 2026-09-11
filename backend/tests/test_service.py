import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from backend.app.models import Action, ProgressReport
from backend.app.service import BusinessError, Service, TZ
from backend.app.store import Store


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'tracker.sqlite3')
        self.now = datetime(2026, 9, 14, 10, tzinfo=TZ)
        self.service = Service(self.store, clock=lambda: self.now)
        self.admin = self.store.add_user('管理员', 'admin')
        self.owner = self.store.add_user('项目负责人')
        self.member = self.store.add_user('节点负责人')
        self.outsider = self.store.add_user('外部成员')
        self.display = self.store.add_user('大屏', 'display')

    def payload(self):
        return {
            'name': '展厅改造', 'description': '施工项目',
            'owner_id': self.owner['id'],
            'member_ids': [self.owner['id'], self.member['id']],
            'start_date': '2026-09-01', 'due_date': '2026-09-30',
            'milestones': [{
                'name': '采购到位', 'criterion': '清单全部签收',
                'owner_id': self.member['id'],
                'start_date': '2026-09-01', 'due_date': '2026-09-20',
            }, {
                'name': '安装完毕', 'criterion': '验收签字',
                'owner_id': self.owner['id'],
                'start_date': '2026-09-10', 'due_date': '2026-09-30',
            }],
        }

    def create_project(self):
        draft = self.service.create_draft(self.admin, Action(intent='create_project', data=self.payload()))
        result = self.service.confirm(self.admin, draft['id'])
        return self.project(result['project_id'])

    def test_project_contacts_are_optional_and_preserved_until_explicitly_changed(self):
        contacts = {'contact_company': '合作单位', 'contact_name': '陈经理', 'contact_info': '微信：partner-test'}
        draft = self.service.create_draft(self.admin,
            Action(intent='create_project', data={**self.payload(), **contacts}), auto_save=True)
        project = self.project(draft['result']['project_id'])
        for field, value in contacts.items():
            self.assertEqual(project[field], value)
        project = self.apply(project, 'edit_project', {'description': '补充说明', 'reason': '完善资料'})
        for field, value in contacts.items():
            self.assertEqual(project[field], value)
        project = self.apply(project, 'edit_project', {'contact_name': '李经理', 'contact_info': '', 'reason': '对接人变更'})
        self.assertEqual(project['contact_name'], '李经理')
        self.assertEqual(project['contact_info'], '')
        self.assertEqual(project['contact_company'], contacts['contact_company'])
        empty = self.create_project()
        self.assertEqual([empty[field] for field in contacts], ['', '', ''])
        self.assertFalse(set(contacts) & self.service.projects(self.admin, display=True)[0].keys())

    def test_project_writes_keep_information_and_items_separate(self):
        project = self.create_project()
        project = self.apply(project, 'edit_project', {'description': '更新项目信息', 'reason': '测试'})
        project = self.apply(project, 'edit_milestone', {'name': '更新项目事项', 'reason': '测试'}, project['milestones'][0]['id'])
        with self.store.connect() as db:
            raw = json.loads(db.execute('SELECT data FROM projects WHERE id=?', (project['id'],)).fetchone()['data'])
        self.assertEqual(raw.get('schema_version'), 2)
        self.assertEqual(raw['project_info']['description'], '更新项目信息')
        self.assertEqual(raw['project_items'][0]['name'], '更新项目事项')
        self.assertEqual(raw['project_items'][0]['id'], project['milestones'][0]['id'])

    def test_owner_roles_are_saved_and_limited_to_project_members(self):
        project = self.create_project()
        roles = {project['owner_id']: 'A1'}
        project = self.apply(project, 'edit_project', {'owner_roles': roles, 'reason': '明确分工'})
        self.assertEqual(project['owner_roles'], roles)
        with self.assertRaises(BusinessError):
            self.apply(project, 'edit_project', {'owner_roles': {'missing': 'B'}, 'reason': '无效成员'})
        with self.assertRaises(BusinessError):
            self.apply(project, 'edit_project', {'owner_roles': {project['owner_id']: 'C'}, 'reason': '无效角色'})

    def test_record_item_merges_owner_assignments_by_name(self):
        project = self.create_project()
        project = self.apply(project, 'edit_project', {'owner_assignments': [
            {'name': '小柯', 'role': 'A角', 'primary': True},
            {'name': '小朱', 'role': 'B角', 'primary': False},
        ], 'reason': '明确初始分工'}, user=self.admin)

        project = self.apply(project, 'record_item', {
            'text': '补充负责人分工',
            'owner_assignments': [
                {'name': '小朱', 'role': '协助', 'primary': False},
                {'name': '小杨', 'role': 'B角', 'primary': False},
            ],
        }, user=self.admin)

        self.assertEqual(project['owner_assignments'], [
            {'name': '小柯', 'role': 'A角', 'primary': True},
            {'name': '小朱', 'role': '协助', 'primary': False},
            {'name': '小杨', 'role': 'B角', 'primary': False},
        ])
        self.assertEqual(project['owner_name'], '小柯（A角）、小朱（协助）、小杨（B角）')

    def test_owner_assignment_roles_are_normalized_to_uppercase(self):
        project = self.create_project()

        project = self.apply(project, 'edit_project', {'owner_assignments': [
            {'name': '张毅', 'role': 'a1', 'primary': False},
            {'name': '朱浩', 'role': 'b角', 'primary': False},
        ], 'reason': '明确负责人分工'}, user=self.admin)

        self.assertEqual(project['owner_assignments'], [
            {'name': '张毅', 'role': 'A1', 'primary': False},
            {'name': '朱浩', 'role': 'B角', 'primary': False},
        ])

        project = self.apply(project, 'edit_project', {'owner_assignments': [
            {'name': '张毅', 'role': 'A', 'primary': True},
            {'name': '朱浩', 'role': 'B', 'primary': False},
        ], 'reason': '不使用裸角色'}, user=self.admin)
        self.assertEqual([item['role'] for item in project['owner_assignments']], ['A角', 'B角'])

    def project(self, pid):
        with self.store.connect() as db:
            return self.service.get_project(db, pid, self.admin)

    def draft(self, project, intent, data, node=None, user=None):
        return self.service.create_draft(user or self.owner, Action(
            intent=intent, project_id=project['id'], milestone_id=node, data=data))

    def apply(self, project, intent, data, node=None, user=None):
        actor = user or self.owner
        draft = self.draft(project, intent, data, node, actor)
        self.service.confirm(actor, draft['id'])
        return self.project(project['id'])

    def counts(self):
        with self.store.connect() as db:
            return tuple(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                         for table in ('projects', 'audit', 'reports'))

    def test_preview_does_not_create_project_or_audit(self):
        draft = self.service.create_draft(self.admin, Action(intent='create_project', data=self.payload()))
        self.assertEqual(draft['status'], 'pending')
        self.assertEqual(len(draft['preview']['milestones']), 2)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_auto_save_rejects_project_alias_duplicate(self):
        first = self.payload()
        first['name'] = 'WMS仓储管理系统'
        self.service.create_draft(
            self.admin, Action(intent='create_project', data=first), auto_save=True)

        duplicate = self.payload()
        duplicate['name'] = 'wms 仓储管理系统'
        with self.assertRaises(BusinessError) as caught:
            self.service.create_draft(
                self.admin, Action(intent='create_project', data=duplicate), auto_save=True)

        self.assertIn('名称冲突', caught.exception.message)
        self.assertEqual(self.counts(), (1, 1, 0))

    def test_member_cannot_assign_others_during_creation(self):
        with self.assertRaises(BusinessError) as error:
            self.service.create_draft(self.owner, Action(intent='create_project', data=self.payload()))
        self.assertEqual(error.exception.status, 403)
        self.assertEqual(self.counts(), (0, 0, 0))
        data = self.payload()
        data['member_ids'] = []
        for node in data['milestones']:
            node['owner_id'] = self.owner['id']
        draft = self.service.create_draft(self.owner, Action(intent='create_project', data=data))
        self.service.confirm(self.owner, draft['id'])
        self.assertEqual(self.counts(), (1, 1, 0))

    def test_node_owner_cannot_edit_plan_or_another_node(self):
        project = self.create_project()
        for intent, data, node in (
            ('edit_milestone', {'due_date': '2026-09-21', 'reason': '申请延期'}, project['milestones'][0]['id']),
            ('report_progress', {'summary': '更新其他节点'}, project['milestones'][1]['id']),
            ('edit_project', {'member_ids': [], 'reason': '移除成员'}, None),
        ):
            with self.subTest(intent=intent), self.assertRaises(BusinessError) as error:
                self.draft(project, intent, data, node, self.member)
            self.assertEqual(error.exception.status, 403)
        self.assertEqual(self.counts(), (1, 1, 0))

    def test_outsider_and_display_cannot_write(self):
        project = self.create_project()
        for user in (self.outsider, self.display):
            with self.subTest(role=user['role']), self.assertRaises(BusinessError):
                self.draft(project, 'report_progress', {'summary': '越权更新'},
                           project['milestones'][0]['id'], user)
        self.assertEqual(self.counts(), (1, 1, 0))

    def test_confirmation_rechecks_disabled_account(self):
        project = self.create_project()
        draft = self.draft(project, 'report_progress', {'summary': '采购更新'},
                           project['milestones'][0]['id'], self.member)
        with self.store.connect(write=True) as db:
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.member['id'],))
        with self.assertRaises(BusinessError) as error:
            self.service.confirm(self.member, draft['id'])
        self.assertEqual(error.exception.status, 403)
        self.assertEqual(self.project(project['id'])['version'], 1)

    def test_confirmation_rechecks_removed_membership(self):
        project = self.create_project()
        draft = self.draft(project, 'report_progress', {'summary': '采购更新'},
                           project['milestones'][0]['id'], self.member)
        project = self.apply(project, 'edit_milestone', {'owner_id': self.owner['id'], 'reason': '移交'},
                             project['milestones'][0]['id'])
        project = self.apply(project, 'edit_project', {'member_ids': [self.owner['id']], 'reason': '退出项目'})
        with self.assertRaises(BusinessError) as error:
            self.service.confirm(self.member, draft['id'])
        self.assertEqual(error.exception.status, 404)
        self.assertEqual(self.counts(), (1, 3, 0))

    def test_confirmation_is_bound_to_its_author(self):
        draft = self.service.create_draft(self.admin, Action(intent='create_project', data=self.payload()))
        with self.assertRaises(BusinessError) as error:
            self.service.confirm(self.owner, draft['id'])
        self.assertEqual(error.exception.status, 404)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_confirmation_is_persistently_idempotent(self):
        draft = self.service.create_draft(self.admin, Action(intent='create_project', data=self.payload()))
        first = self.service.confirm(self.admin, draft['id'])
        restarted = Service(Store(self.store.path), clock=lambda: self.now)
        self.assertEqual(restarted.confirm(self.admin, draft['id']), first)
        project = self.project(first['project_id'])
        report = self.draft(project, 'report_progress', {'summary': '完成采购六成', 'progress': 60},
                            project['milestones'][0]['id'], self.member)
        result = self.service.confirm(self.member, report['id'])
        self.now += timedelta(hours=2)
        self.assertEqual(restarted.confirm(self.member, report['id']), result)
        self.assertEqual(self.counts(), (1, 2, 1))
        self.assertEqual(self.project(first['project_id'])['milestones'][0]['last_report_at'],
                         '2026-09-14T10:00:00+08:00')

    def test_expired_draft_cannot_be_confirmed_at_expiry_boundary(self):
        draft = self.service.create_draft(self.admin, Action(intent='create_project', data=self.payload()))
        self.now += timedelta(minutes=30)
        self.assertEqual(self.service.draft(self.admin, draft['id'])['status'], 'expired')
        with self.assertRaises(BusinessError) as error:
            self.service.confirm(self.admin, draft['id'])
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_cancelled_draft_cannot_be_confirmed(self):
        draft = self.service.create_draft(self.admin, Action(intent='create_project', data=self.payload()))
        self.service.cancel(self.admin, draft['id'])
        with self.assertRaises(BusinessError) as error:
            self.service.confirm(self.admin, draft['id'])
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_old_draft_does_not_overwrite_newer_version(self):
        project = self.create_project()
        first = self.draft(project, 'edit_project', {'name': '旧名称', 'reason': '调整'})
        project = self.apply(project, 'edit_project', {'name': '新名称', 'reason': '调整'})
        with self.assertRaises(BusinessError) as error:
            self.service.confirm(self.owner, first['id'])
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(self.project(project['id'])['name'], '新名称')
        self.assertEqual(self.counts(), (1, 2, 0))

    def test_unknown_fields_and_invalid_percentages_are_rejected(self):
        project = self.create_project()
        invalid = [
            {'summary': '采购进度', 'progress': value} for value in (-1, 101, True, '60', 60.5)
        ] + [
            {'summary': '采购进度', 'actor_id': self.admin['id']},
            {'summary': '采购进度', 'last_report_at': self.now.isoformat()},
            {'summary': '   '}, {'summary': '采购进度', 'progress': None},
        ]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(BusinessError):
                self.draft(project, 'report_progress', data, project['milestones'][0]['id'], self.member)
        with self.assertRaises(ValidationError):
            Action.model_validate({'intent': 'query', 'admin': True})
        self.assertEqual(self.counts(), (1, 1, 0))

    def test_invalid_dates_and_unbound_node_owners_are_rejected(self):
        for field, value in (('due_date', '2026-08-31'), ('start_date', '2026-10-01'),
                             ('owner_id', self.outsider['id'])):
            data = self.payload()
            data['milestones'][0][field] = value
            with self.subTest(field=field), self.assertRaises(BusinessError):
                self.service.create_draft(self.admin, Action(intent='create_project', data=data))
        project = self.create_project()
        with self.assertRaises(BusinessError):
            self.draft(project, 'edit_project', {'due_date': '2026-09-19', 'reason': '缩短计划'})
        self.assertEqual(self.project(project['id'])['due_date'], '2026-09-30')

    def test_omitted_fields_preserved_and_explicit_clear_only(self):
        project = self.create_project()
        nid = project['milestones'][0]['id']
        project = self.apply(project, 'report_progress', {
            'summary': '采购六成', 'progress': 60, 'blocker': '灯具缺货',
            'next_step': '联系供应商', 'expected_date': '2026-09-22',
        }, nid, self.member)
        project = self.apply(project, 'report_progress', {'summary': '已催供应商'}, nid, self.member)
        node = project['milestones'][0]
        self.assertEqual((node['progress'], node['blocker'], node['next_step'], node['expected_date']),
                         (60, '灯具缺货', '联系供应商', '2026-09-22'))
        for key, value in (('blocker', ''), ('next_step', ' '), ('expected_date', None)):
            with self.subTest(key=key), self.assertRaises(BusinessError):
                self.draft(project, 'report_progress', {'summary': '更新', key: value}, nid, self.member)
        project = self.apply(project, 'report_progress', {
            'summary': '阻碍已解决，撤回预计日期', 'clear_fields': ['blocker', 'expected_date'],
        }, nid, self.member)
        node = project['milestones'][0]
        self.assertEqual((node['blocker'], node['expected_date'], node['next_step']), ('', None, '联系供应商'))

    def test_same_field_cannot_be_set_and_cleared(self):
        with self.assertRaises(ValidationError):
            ProgressReport(summary='更新', blocker='仍有阻碍', clear_fields=['blocker'])

    def test_expected_date_and_plan_changes_preserve_original_dates(self):
        project = self.create_project()
        nid = project['milestones'][0]['id']
        project = self.apply(project, 'report_progress', {'summary': '预计延迟', 'expected_date': '2026-10-02'},
                             nid, self.member)
        self.assertEqual(project['due_date'], '2026-09-30')
        self.assertEqual(project['milestones'][0]['due_date'], '2026-09-20')
        project = self.apply(project, 'edit_project', {'due_date': '2026-10-05', 'reason': '批准调整'})
        project = self.apply(project, 'edit_milestone', {'due_date': '2026-10-03', 'reason': '批准调整'}, nid)
        node = project['milestones'][0]
        self.assertEqual((project['original_due_date'], node['original_due_date']), ('2026-09-30', '2026-09-20'))
        self.assertEqual((project['due_date'], node['due_date'], node['expected_date']),
                         ('2026-10-05', '2026-10-03', '2026-10-02'))

    def test_historical_report_keeps_current_node_and_other_node_unchanged(self):
        project = self.create_project()
        nid = project['milestones'][0]['id']
        untouched = copy.deepcopy(project['milestones'][1])
        project = self.apply(project, 'report_progress', {'summary': '当前六成', 'progress': 60, 'blocker': '缺货'},
                             nid, self.member)
        before = copy.deepcopy(project['milestones'][0])
        self.now += timedelta(days=1)
        project = self.apply(project, 'report_progress', {
            'summary': '补记昨天之前的采购', 'progress': 20, 'historical': True,
            'event_date': '2026-09-10', 'clear_fields': ['blocker'],
        }, nid, self.member)
        self.assertEqual(project['milestones'][0], before)
        self.assertEqual(project['milestones'][1], untouched)
        with self.store.connect() as db:
            data = json.loads(db.execute('SELECT data FROM reports ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertEqual(data['event_date'], '2026-09-10')
        self.assertEqual((data['before_progress'], data['after_progress']), (60, 60))

    def test_history_requires_past_date_and_current_reports_cannot_disguise_history(self):
        project = self.create_project()
        for data in ({'historical': True}, {'historical': True, 'event_date': '2026-09-15'},
                     {'event_date': '2026-09-13'}):
            with self.subTest(data=data), self.assertRaises(BusinessError):
                self.draft(project, 'report_progress', {'summary': '更新', **data},
                           project['milestones'][0]['id'], self.member)
        self.assertEqual(self.counts(), (1, 1, 0))

    def test_hundred_percent_does_not_implicitly_accept_completion(self):
        project = self.create_project()
        nid = project['milestones'][0]['id']
        project = self.apply(project, 'report_progress', {'summary': '采购完成待验收', 'progress': 100}, nid, self.member)
        self.assertEqual(project['milestones'][0]['status'], 'active')
        self.assertIsNone(project['milestones'][0]['completed_at'])
        self.assertNotEqual(project['status'], 'completed')
        project = self.apply(project, 'milestone_status', {'status': 'completed', 'reason': '清单全部签收'},
                             nid, self.member)
        self.assertEqual(project['milestones'][0]['status'], 'completed')
        with self.assertRaises(BusinessError) as error:
            self.draft(project, 'milestone_status', {'status': 'active', 'reason': '重新打开'}, nid, self.member)
        self.assertEqual(error.exception.status, 403)
        project = self.apply(project, 'milestone_status', {'status': 'cancelled', 'reason': '取消安装'},
                             project['milestones'][1]['id'])
        project = self.apply(project, 'project_status', {'status': 'completed', 'reason': '全部有效目标已验收'})
        self.assertEqual(project['status'], 'completed')

    def test_explicit_delivery_allows_completion_with_cancelled_nodes(self):
        project = self.create_project()
        for node in project['milestones']:
            project = self.apply(project, 'milestone_status', {'status': 'cancelled', 'reason': '范围取消'}, node['id'])
        project = self.apply(project, 'project_status', {'status': 'completed', 'reason': '已交付'})
        self.assertEqual(project['status'], 'completed')

    def test_explicit_delivery_does_not_complete_unfinished_items(self):
        project = self.create_project()
        previous_nodes = project['milestones']
        project = self.apply(project, 'project_status', {'status': 'completed', 'reason': '项目已交付'})
        self.assertEqual(project['status'], 'completed')
        self.assertIsNotNone(project['completed_at'])
        self.assertEqual(project['milestones'], previous_nodes)

    def test_cancelled_node_rejects_progress(self):
        project = self.create_project()
        nid = project['milestones'][0]['id']
        project = self.apply(project, 'milestone_status', {'status': 'cancelled', 'reason': '取消采购'}, nid)
        with self.assertRaises(BusinessError):
            self.draft(project, 'report_progress', {'summary': '继续汇报'}, nid, self.member)

    def test_failed_audit_insert_rolls_back_project_creation(self):
        draft = self.service.create_draft(self.admin, Action(intent='create_project', data=self.payload()))
        with self.store.connect(write=True) as db:
            db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.confirm(self.admin, draft['id'])
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.service.draft(self.admin, draft['id'])['status'], 'pending')
        with self.store.connect(write=True) as db:
            db.execute('DROP TRIGGER fail_audit')
        self.service.confirm(self.admin, draft['id'])
        self.assertEqual(self.counts(), (1, 1, 0))

    def test_auto_save_failure_preserves_previous_draft_and_rolls_back_project(self):
        from backend.app.ai import save_incomplete
        previous = save_incomplete(self.service, self.admin, Action(intent='create_project'), '待补充', {})
        with self.store.connect(write=True) as db:
            db.execute("CREATE TRIGGER fail_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.create_draft(self.admin, Action(intent='create_project', data=self.payload()),
                                      previous_draft_id=previous['id'], auto_save=True)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.assertEqual(self.service.draft(self.admin, previous['id'])['status'], 'needs_input')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM drafts').fetchone()[0], 1)

    def test_failed_report_insert_rolls_back_project_and_audit(self):
        project = self.create_project()
        draft = self.draft(project, 'report_progress', {'summary': '采购六成', 'progress': 60},
                           project['milestones'][0]['id'], self.member)
        with self.store.connect(write=True) as db:
            db.execute("CREATE TRIGGER fail_report BEFORE INSERT ON reports BEGIN SELECT RAISE(ABORT, 'report unavailable'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.confirm(self.member, draft['id'])
        self.assertEqual(self.project(project['id']), project)
        self.assertEqual(self.counts(), (1, 1, 0))
        self.assertEqual(self.service.draft(self.member, draft['id'])['status'], 'pending')

    def test_disabled_member_does_not_block_other_reports_and_can_be_transferred(self):
        project = self.create_project()
        with self.store.connect(write=True) as db:
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.member['id'],))
        project = self.apply(project, 'report_progress', {'summary': '安装进行中', 'progress': 30},
                             project['milestones'][1]['id'], self.owner)
        self.assertEqual(project['milestones'][1]['progress'], 30)
        project = self.apply(project, 'edit_milestone', {'owner_id': self.owner['id'], 'reason': '离职移交'},
                             project['milestones'][0]['id'], self.admin)
        project = self.apply(project, 'edit_project', {'member_ids': [self.owner['id']], 'reason': '移除离职成员'},
                             user=self.admin)
        self.assertEqual(project['member_ids'], [self.owner['id']])
        self.assertEqual(project['milestones'][0]['owner_id'], self.owner['id'])
        with self.assertRaises(BusinessError) as error:
            self.draft(project, 'report_progress', {'summary': '停用人员尝试更新'},
                       project['milestones'][0]['id'], self.member)
        self.assertEqual(error.exception.status, 403)
        with self.store.connect() as db:
            self.assertFalse(self.store.user(db, self.member['id'])['active'])

    def test_existing_disabled_member_cannot_receive_new_responsibilities(self):
        project = self.create_project()
        with self.store.connect(write=True) as db:
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.member['id'],))
        new_node = {**self.payload()['milestones'][0], 'name': '新采购任务'}
        for intent, data, nid in (
            ('edit_project', {'owner_id': self.member['id'], 'reason': '更换负责人'}, None),
            ('edit_milestone', {'owner_id': self.member['id'], 'reason': '更换负责人'}, project['milestones'][1]['id']),
            ('add_milestone', new_node, None),
        ):
            with self.subTest(intent=intent), self.assertRaises(BusinessError):
                self.draft(project, intent, data, nid, self.admin)
        self.assertEqual(self.project(project['id'])['version'], 1)

    def test_disabled_outsider_cannot_be_added_to_project(self):
        project = self.create_project()
        with self.store.connect(write=True) as db:
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.outsider['id'],))
        with self.assertRaises(BusinessError):
            self.draft(project, 'edit_project', {
                'member_ids': project['member_ids'] + [self.outsider['id']], 'reason': '邀请成员',
            })
        self.assertNotIn(self.outsider['id'], self.project(project['id'])['member_ids'])

    def test_admin_can_transfer_project_away_from_disabled_owner(self):
        project = self.create_project()
        with self.store.connect(write=True) as db:
            db.execute('UPDATE users SET active=0 WHERE id=?', (self.owner['id'],))
        project = self.apply(project, 'edit_project', {'owner_id': self.member['id'], 'reason': '负责人离职移交'},
                             user=self.admin)
        project = self.apply(project, 'edit_milestone', {'owner_id': self.member['id'], 'reason': '节点移交'},
                             project['milestones'][1]['id'], self.member)
        project = self.apply(project, 'edit_project', {'member_ids': [self.member['id']], 'reason': '移除离职人员'},
                             user=self.member)
        self.assertEqual(project['owner_id'], self.member['id'])
        self.assertEqual(project['member_ids'], [self.member['id']])

    def test_incomplete_draft_survives_restart_and_time_passage(self):
        from backend.app.ai import save_incomplete
        draft = save_incomplete(self.service, self.owner, Action(intent='create_project'),
                                '准备立项', {'missing_fields': ['name']})
        self.now += timedelta(days=7)
        restarted = Service(Store(self.store.path), clock=lambda: self.now)
        restored = restarted.draft(self.owner, draft['id'])
        self.assertEqual(restored['status'], 'needs_input')
        self.assertEqual(restored['source_text'], '准备立项')

    def test_pause_reason_survives_restart_and_clears_when_resumed(self):
        for intent in ('project_status', 'milestone_status'):
            with self.subTest(intent=intent):
                project = self.create_project()
                node_id = project['milestones'][0]['id'] if intent == 'milestone_status' else None
                self.apply(project, intent, {'status': 'paused', 'reason': '等待客户提供资料'}, node_id, self.admin)
                restarted = Service(Store(self.store.path), clock=lambda: self.now)
                saved = next(p for p in restarted.projects(self.admin) if p['id'] == project['id'])
                target = saved['milestones'][0] if node_id else saved
                self.assertEqual(target['pause_reason'], '等待客户提供资料')
                displayed = next(p for p in restarted.projects(self.admin, display=True) if p['id'] == project['id'])
                self.assertEqual((displayed['milestones'][0] if node_id else displayed)['pause_reason'], '等待客户提供资料')
                resumed = self.apply(saved, intent, {'status': 'active', 'reason': '资料已到齐'}, node_id, self.admin)
                self.assertEqual((resumed['milestones'][0] if node_id else resumed)['pause_reason'], '')


if __name__ == '__main__':
    unittest.main()
