import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from backend.app.store import Store


def make_legacy_database(path):
    with closing(sqlite3.connect(path)) as db:
        db.executescript('''
        CREATE TABLE audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
          user_id TEXT NOT NULL, at TEXT NOT NULL, intent TEXT NOT NULL,
          before_data TEXT, after_data TEXT NOT NULL, draft_id TEXT NOT NULL
        );
        CREATE TABLE reports (
          id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
          milestone_id TEXT NOT NULL, user_id TEXT NOT NULL, at TEXT NOT NULL,
          data TEXT NOT NULL, draft_id TEXT NOT NULL
        );
        INSERT INTO audit(project_id,user_id,at,intent,before_data,after_data,draft_id)
          VALUES(1,'u1','2026-09-07T10:00:00+08:00','create_project',NULL,'{}','legacy-a');
        INSERT INTO audit(project_id,user_id,at,intent,before_data,after_data,draft_id)
          VALUES(1,'u1','2026-09-07T11:00:00+08:00','report_progress','{}','{}','legacy-b');
        INSERT INTO reports(project_id,milestone_id,user_id,at,data,draft_id)
          VALUES(1,'m1','u1','2026-09-07T11:00:00+08:00','{}','legacy-b');
        ''')


class OperationMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / 'tracker.sqlite3'

    def test_fresh_store_has_operation_ledger_and_dual_reference_columns(self):
        store = Store(self.db_path)
        with store.connect() as db:
            operation_columns = {row['name'] for row in db.execute('PRAGMA table_info(operations)')}
            audit_columns = {row['name'] for row in db.execute('PRAGMA table_info(audit)')}
            report_columns = {row['name'] for row in db.execute('PRAGMA table_info(reports)')}
        self.assertEqual(operation_columns, {
            'id', 'source', 'source_message_id', 'actor_ref', 'request_hash',
            'status', 'created_at', 'result', 'diagnostics'})
        self.assertIn('operation_id', audit_columns)
        self.assertIn('operation_id', report_columns)

    def test_legacy_links_are_backfilled_without_changing_history_counts(self):
        make_legacy_database(self.db_path)
        Store(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM audit').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT count(*) FROM reports').fetchone()[0], 1)
            self.assertEqual(db.execute(
                "SELECT count(*) FROM operations WHERE source='legacy'"
            ).fetchone()[0], 2)
            self.assertEqual(db.execute(
                'SELECT count(*) FROM audit WHERE operation_id IS NULL'
            ).fetchone()[0], 0)
            self.assertEqual(db.execute(
                'SELECT count(*) FROM reports WHERE operation_id IS NULL'
            ).fetchone()[0], 0)
            self.assertEqual(db.execute(
                "SELECT actor_ref FROM operations WHERE id='legacy-a'"
            ).fetchone()[0], 'u1')
        backups = list(Path(self.temp.name).glob(
            'tracker.sqlite3.before-operations-*.bak'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as backup:
            self.assertFalse(backup.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operations'"
            ).fetchone())
            self.assertNotIn('operation_id', {
                row[1] for row in backup.execute('PRAGMA table_info(audit)')
            })

    def test_legacy_operation_uses_earliest_linked_actor(self):
        make_legacy_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute(
                'INSERT INTO audit(project_id,user_id,at,intent,before_data,after_data,draft_id) '
                "VALUES(1,'early-user','2026-09-07T10:00:00+08:00','update',NULL,'{}','legacy-c')"
            )
            db.execute(
                'INSERT INTO reports(project_id,milestone_id,user_id,at,data,draft_id) '
                "VALUES(1,'m2','later-user','2026-09-07T09:30:00+00:00','{}','legacy-c')"
            )
            db.commit()
        Store(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute(
                "SELECT actor_ref FROM operations WHERE id='legacy-c'"
            ).fetchone()[0], 'early-user')

    def test_legacy_operation_preserves_fractional_seconds_for_earliest_actor(self):
        make_legacy_database(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute(
                'INSERT INTO audit(project_id,user_id,at,intent,before_data,after_data,draft_id) '
                "VALUES(1,'later-user','2026-09-07T10:00:00.900+08:00','update',NULL,'{}','legacy-d')"
            )
            db.execute(
                'INSERT INTO reports(project_id,milestone_id,user_id,at,data,draft_id) '
                "VALUES(1,'m3','early-user','2026-09-07T10:00:00.100+08:00','{}','legacy-d')"
            )
            db.commit()
        Store(self.db_path)
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute(
                "SELECT actor_ref FROM operations WHERE id='legacy-d'"
            ).fetchone()[0], 'early-user')

    def test_operation_helpers_create_find_and_finish_the_operation(self):
        store = Store(self.db_path)
        with store.connect(write=True) as db:
            store.create_operation(
                db,
                operation_id='operation-1',
                source='browser',
                source_message_id='message-1',
                actor_ref='user-1',
                request_hash='request-1',
                created_at='2026-09-09T10:00:00+08:00',
                diagnostics={'channel': 'web'},
            )
            operation = store.operation_by_source(db, 'browser', 'message-1')
            self.assertEqual(operation['status'], 'processing')
            self.assertEqual(json.loads(operation['diagnostics']), {'channel': 'web'})
            self.assertIsNone(store.operation_by_source(db, 'browser', None))
            store.finish_operation(db, 'operation-1', 'done', {'project_id': '1'})
            finished = db.execute(
                'SELECT status,result FROM operations WHERE id=?', ('operation-1',)
            ).fetchone()
        self.assertEqual(finished['status'], 'done')
        self.assertEqual(json.loads(finished['result']), {'project_id': '1'})

    def test_legacy_business_states_are_normalized_with_a_backup(self):
        store = Store(self.db_path)
        with store.connect(write=True) as db:
            db.execute('INSERT INTO projects(version,data) VALUES(?,?)', (1, json.dumps({
                'id': 'legacy', 'code': 'P0001', 'name': '旧项目', 'description': '',
                'owner_id': None, 'owner_name': None, 'member_ids': [],
                'start_date': None, 'due_date': None, 'display_visible': True,
                'status': 'not_started', 'milestones': [{
                    'id': 'm1', 'name': '旧事项', 'status': 'not_started', 'preparation': True,
                    'due_date': None,
                }],
            }, ensure_ascii=False)))
        Store(self.db_path)
        with store.connect() as db:
            row = db.execute('SELECT data FROM projects').fetchone()
        project = json.loads(row['data'])
        self.assertEqual(project['project_info']['status'], 'active')
        self.assertEqual(project['project_items'][0]['status'], 'active')
        self.assertNotIn('preparation', project['project_items'][0])
        self.assertEqual(len(list(Path(self.temp.name).glob(
            'tracker.sqlite3.before-business-states-*.bak'))), 1)
