import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from backend.app.ai import parse_message
from backend.app.models import MessageInput
from backend.app.models import Action, ParsedMessage
from backend.app.ai import output_schema, validate_output_data, data_schema
from backend.app.reminders import scan
from backend.app.service import BusinessError, Service, TZ
from backend.app.store import Store


class InformationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'information.sqlite3')
        self.user = self.store.add_user('记录人', 'admin')
        self.service = Service(self.store, lambda: datetime(2026, 9, 4, 10, tzinfo=TZ))

    def record(self, key, data, **fields):
        request = MessageInput(text=data['text'], client_message_id=key, **fields)
        return asyncio.run(parse_message(self.service, self.user, request,
            lambda _: json.dumps({'intent': 'record_item', 'data': data})))

    def test_information_creates_project_without_inventing_owner_or_dates(self):
        result = self.record('information-01', {'project_name': '北斗', 'text': '下周去北斗调研', 'time_text': '下周'})
        self.assertEqual(result['draft']['status'], 'confirmed')
        project = Service(Store(self.store.path)).projects(self.user)[0]
        self.assertEqual(project['name'], '北斗')
        self.assertEqual(project['status'], 'active')
        self.assertIsNone(project['owner_id'])
        self.assertIsNone(project['due_date'])
        self.assertEqual(project['milestones'][0]['time_text'], '下周')
        self.assertEqual(project['milestones'][0]['summary'], '下周去北斗调研')
        self.assertFalse(project['milestones'][0]['preparation'])
        self.assertEqual(project['flags'], [])
        self.assertEqual(scan(self.store, self.service.clock()), 0)

    def test_following_information_appends_to_named_project_and_redelivery_deduplicates(self):
        self.record('information-01', {'project_name': '北斗', 'text': '下周去调研'})
        data = {'project_name': '北斗', 'text': '之后再约一次技术交流'}
        result = self.record('information-02', data)
        self.assertEqual(self.record('information-02', data)['draft']['id'], result['draft']['id'])
        projects = self.service.projects(self.user)
        self.assertEqual(len(projects), 1)
        self.assertEqual([n['summary'] for n in projects[0]['milestones']], ['下周去调研', '之后再约一次技术交流'])
        self.assertEqual(projects[0]['version'], 2)

    def test_unclassified_information_is_saved_and_can_be_assigned_later(self):
        previous = self.record('information-01', {'text': '下周去交流'})['draft']
        self.assertEqual(previous['status'], 'needs_input')
        self.assertEqual(self.service.projects(self.user), [])
        result = self.record('information-02', {'project_name': '美国宠物医院', 'text': '下周去交流'},
                             previous_draft_id=previous['id'])
        self.assertEqual(result['draft']['status'], 'confirmed')
        self.assertEqual(self.service.draft(self.user, previous['id'])['status'], 'cancelled')

    def test_date_order_still_validated_for_information(self):
        with self.assertRaises(BusinessError):
            self.record('information-01', {'project_name': '北斗', 'text': '调研',
                'start_date': '2026-09-10', 'due_date': '2026-09-09'})
        self.assertEqual(self.service.projects(self.user), [])

    def test_hidden_existing_project_cannot_be_recreated_by_name(self):
        self.record('original-project', {'project_name': '美国宠物医院', 'text': '既有安排'})
        outsider = self.store.add_user('群成员', 'member')
        self.user = outsider
        result = self.record('outside-message', {'project_name': '美国宠物医院', 'text': '周五做出项目'})
        self.assertEqual(result['draft']['status'], 'needs_input')
        self.assertIsNone(result['draft']['action']['project_id'])
        self.assertEqual(self.service.projects(outsider), [])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM projects').fetchone()[0], 1)

    def test_two_dates_append_two_items_to_existing_project_atomically(self):
        self.record('original-project', {'project_name': '美国宠物医院', 'text': '既有安排'})
        result = self.record('two-date-message', {'project_name': '美国宠物医院',
            'text': '下周五做出项目，周日上线测试', 'items': [
                {'text': '下周五做出项目', 'title': '做出项目', 'due_date': '2026-09-11'},
                {'text': '周日上线测试', 'title': '上线测试', 'due_date': '2026-09-13'},
            ]})
        self.assertEqual(result['draft']['status'], 'confirmed')
        projects = self.service.projects(self.user)
        self.assertEqual(len(projects), 1)
        self.assertEqual([(n['name'], n['due_date']) for n in projects[0]['milestones'][1:]],
                         [('做出项目', '2026-09-11'), ('上线测试', '2026-09-13')])

    def test_invalid_nested_item_does_not_save_any_items(self):
        with self.assertRaises(BusinessError):
            self.record('invalid-multiple', {'project_name': '北斗', 'text': '两条安排', 'items': [
                {'text': '第一条', 'due_date': '2026-09-11'},
                {'text': '第二条', 'due_date': '不是日期'},
            ]})
        self.assertEqual(self.service.projects(self.user), [])

    def test_schema_rejects_unknown_fields_even_for_incomplete_output(self):
        with self.assertRaises(BusinessError):
            validate_output_data(ParsedMessage(intent='create_project', data={'name': '北斗', 'sql': 'anything'},
                                               missing_fields=['owner_id']))
        parsed = ParsedMessage(intent='create_project', data={'name': '北斗'})
        validate_output_data(parsed)
        self.assertEqual(parsed.missing_fields, [])
        with self.assertRaises(BusinessError):
            validate_output_data(ParsedMessage(intent='query', data={'name': '北斗'}))
        schema = output_schema()
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(len(schema['oneOf']), 10)

    def test_new_project_name_is_rechecked_at_commit(self):
        action = Action(intent='record_item', data={'project_name': '北斗', 'text': '安排'})
        first = self.service.create_draft(self.user, action)
        second = self.service.create_draft(self.user, action)
        self.service.confirm(self.user, first['id'])
        with self.assertRaises(BusinessError):
            self.service.confirm(self.user, second['id'])
        self.assertEqual(len(self.service.projects(self.user)), 1)

    def test_schema_does_not_coerce_dates_or_accept_null(self):
        for value in (0, None, '2026-02-30'):
            with self.subTest(value=value), self.assertRaises(BusinessError):
                validate_output_data(ParsedMessage(intent='record_item',
                    data={'text': '安排', 'due_date': value}))

    def test_prompt_schema_does_not_advertise_null_values(self):
        from backend.app.models import RecordItem
        encoded = json.dumps(data_schema(RecordItem))
        self.assertNotIn('"type": "null"', encoded)
        self.assertNotIn('"default": null', encoded)

    def test_arrangement_completed_by_followup_updates_same_item_not_project(self):
        from backend.app.ai import prompt_context, SYSTEM
        created = self.record('factory-plan-01', {'project_name': '工厂项目', 'text': '下周去调研工厂',
            'title': '调研工厂', 'time_text': '下周'})
        pid = created['draft']['result']['project_id']
        project = self.service.projects(self.user)[0]
        node = project['milestones'][0]
        self.assertEqual(node['status'], 'active')
        self.assertIsNone(node['due_date'])
        for i in range(6):
            self.record(f'other-plan-{i}', {'project_name': f'其他{i}', 'text': '其他工作'})
        context = json.loads(prompt_context(self.service, self.user, '工厂调研完成了')[len(SYSTEM):])
        self.assertIn(pid, [p['id'] for p in context['projects']])
        request = MessageInput(text='工厂调研完成了', client_message_id='factory-complete-01')
        raw = json.dumps({'intent': 'milestone_status', 'project_id': pid, 'milestone_id': node['id'],
            'data': {'status': 'completed', 'reason': '工厂调研完成了'}})
        result = asyncio.run(parse_message(self.service, self.user, request, lambda _: raw))
        self.assertEqual(result['draft']['status'], 'confirmed')
        updated = next(p for p in self.service.projects(self.user) if p['id'] == pid)
        self.assertEqual(updated['status'], project['status'])
        self.assertEqual(len(updated['milestones']), 1)
        self.assertEqual(updated['milestones'][0]['id'], node['id'])
        self.assertEqual(updated['milestones'][0]['status'], 'completed')
        self.assertIsNotNone(updated['milestones'][0]['completed_at'])
