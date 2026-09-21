import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.app.store import Store, encode, encode_project


class ProjectStorageTests(unittest.TestCase):
    def test_migration_structures_recognizable_legacy_owner_descriptions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tracker.sqlite3'
            store = Store(path)
            projects = [
                {'name': '美国宠物医院', 'owner_name': '小柯为A角和主要负责人，小朱为B角', 'milestones': []},
                {'name': '锐瀚科技', 'owner_name': '张毅（A1）、朱浩（A2）', 'milestones': []},
                {'name': '已结构化项目', 'owner_name': '小柯（A角 · 主负责人）、小朱（B角）',
                 'owner_assignments': [
                     {'name': '小柯', 'role': 'A角', 'primary': True},
                     {'name': '小朱', 'role': 'B角', 'primary': False},
                 ], 'milestones': []},
            ]
            with store.connect(write=True) as db:
                db.executemany('INSERT INTO projects(version,data) VALUES(1,?)',
                               [(encode_project(project),) for project in projects])

            migrated = Store(path)
            with migrated.connect() as db:
                saved = [migrated.project(row) for row in db.execute('SELECT * FROM projects ORDER BY id')]
            self.assertEqual(saved[0]['owner_assignments'], [
                {'name': '小柯', 'role': 'A角', 'primary': True},
                {'name': '小朱', 'role': 'B角', 'primary': False},
            ])
            self.assertEqual(saved[0]['owner_name'], '小柯（A角）、小朱（B角）')
            self.assertEqual(saved[1]['owner_assignments'], [
                {'name': '张毅', 'role': 'A1', 'primary': False},
                {'name': '朱浩', 'role': 'A2', 'primary': False},
            ])
            self.assertEqual(saved[2]['owner_name'], '小柯（A角）、小朱（B角）')
            self.assertEqual(len(list(path.parent.glob('tracker.sqlite3.before-owner-assignments-*.bak'))), 1)

    def test_migration_preserves_ids_dates_roles_versions_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tracker.sqlite3'
            store = Store(path)
            original = {'name': '项目', 'due_date': '2026-09-11', 'owner_roles': {'u': 'A1'},
                        'milestones': [{'id': 'item-1', 'name': '待定事项', 'due_date': None,
                                        'time_text': '随后', 'progress': 25}]}
            with store.connect(write=True) as db:
                db.execute('INSERT INTO projects(id,version,data) VALUES(7,3,?)', (encode(original),))
            store = Store(path)
            with store.connect() as db:
                row = db.execute('SELECT * FROM projects WHERE id=7').fetchone()
                raw = json.loads(row['data'])
                self.assertEqual(raw.get('schema_version'), 2)
                self.assertEqual(raw['project_info'], {k: v for k, v in original.items() if k != 'milestones'})
                self.assertEqual(raw['project_items'], original['milestones'])
                self.assertNotIn('milestones', raw)
                self.assertEqual(store.project(row), {**original, 'id': '7', 'code': 'P0007', 'version': 3})
            backups = list(path.parent.glob('tracker.sqlite3.before-project-items-*.bak'))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as db:
                self.assertEqual(json.loads(db.execute('SELECT data FROM projects').fetchone()[0]), original)
            Store(path)
            self.assertEqual(list(path.parent.glob('tracker.sqlite3.before-project-items-*.bak')), backups)

    def test_invalid_legacy_row_does_not_partially_migrate_other_projects(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tracker.sqlite3'
            store = Store(path)
            values = [encode({'name': 'valid', 'milestones': []}), '{invalid']
            with store.connect(write=True) as db:
                db.executemany('INSERT INTO projects(version,data) VALUES(1,?)', [(v,) for v in values])
            with self.assertRaises(ValueError):
                Store(path)
            with closing(sqlite3.connect(path)) as db:
                self.assertEqual([r[0] for r in db.execute('SELECT data FROM projects ORDER BY id')], values)

