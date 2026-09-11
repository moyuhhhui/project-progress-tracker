import hashlib
import json
import os
import re
import secrets
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def encode_project(project):
    return encode({'schema_version': 2,
                   'project_info': {key: value for key, value in project.items() if key != 'milestones'},
                   'project_items': project['milestones']})


def decode_project(value):
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError('项目存储格式无效')
    if 'schema_version' in data:
        if (data['schema_version'] != 2 or not isinstance(data.get('project_info'), dict)
                or not isinstance(data.get('project_items'), list)):
            raise ValueError('不支持的项目存储版本或格式')
        return {**data['project_info'], 'milestones': data['project_items']}
    if not isinstance(data.get('milestones'), list):
        raise ValueError('旧项目缺少事项列表，迁移已停止')
    return data


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def owner_summary(assignments):
    return '、'.join(f"{item['name']}（{item['role']}）" for item in assignments)


def legacy_owner_assignments(value):
    """只迁移能完整识别的旧负责人句式，无法确认的文本保持原样。"""
    if not isinstance(value, str) or not value.strip():
        return []
    parts = [part.strip() for part in re.split(r'[、，,]', value.strip()) if part.strip()]
    assignments = []
    for part in parts:
        parenthesized = re.fullmatch(r'(.+?)[（(](.+?)[）)]', part)
        if parenthesized:
            name, role_text = (item.strip() for item in parenthesized.groups())
            primary = '主负责人' in role_text
            role = re.sub(r'\s*[·和]?\s*主负责人\s*', '', role_text).strip()
        elif '为' in part:
            name, role_text = (item.strip() for item in part.split('为', 1))
            primary = '主要负责人' in role_text or '主负责人' in role_text
            role = re.sub(r'(?:和)?主要?负责人$', '', role_text).strip()
        else:
            return []
        if not name or not role or len(name) > 100 or len(role) > 30:
            return []
        assignments.append({'name': name, 'role': role, 'primary': primary})
    return assignments


class Store:
    def __init__(self, path=None):
        self.path = str(path or os.getenv('TRACKER_DB', 'data/tracker.sqlite3'))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
              token_hash TEXT UNIQUE NOT NULL, active INTEGER NOT NULL DEFAULT 1,
              wecom_user_id TEXT NOT NULL DEFAULT ''
            );
            CREATE UNIQUE INDEX IF NOT EXISTS wecom_unique ON users(wecom_user_id)
              WHERE wecom_user_id != '';
            CREATE TABLE IF NOT EXISTS projects (
              id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL, data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS drafts (
              id TEXT PRIMARY KEY, user_id TEXT NOT NULL, status TEXT NOT NULL,
              created_at TEXT NOT NULL, expires_at TEXT NOT NULL, expected_version INTEGER,
              action TEXT NOT NULL, preview TEXT, source_text TEXT NOT NULL DEFAULT '',
              diagnostics TEXT NOT NULL DEFAULT '{}', result TEXT,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY, user_id TEXT NOT NULL, text_hash TEXT NOT NULL,
              status TEXT NOT NULL, created_at TEXT NOT NULL, response TEXT,
              FOREIGN KEY(user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
              user_id TEXT NOT NULL, at TEXT NOT NULL, intent TEXT NOT NULL,
              before_data TEXT, after_data TEXT NOT NULL, draft_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reports (
              id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
              milestone_id TEXT NOT NULL, user_id TEXT NOT NULL, at TEXT NOT NULL,
              data TEXT NOT NULL, draft_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reminders (
              id TEXT PRIMARY KEY, project_id INTEGER NOT NULL, milestone_id TEXT NOT NULL,
              owner_id TEXT NOT NULL, local_day TEXT NOT NULL, reasons TEXT NOT NULL,
              status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              updated_at TEXT NOT NULL, next_attempt TEXT, detail TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS worker_lease (
              id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL, expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS wecom_drafts (
              draft_id TEXT PRIMARY KEY REFERENCES drafts(id), bot_id TEXT NOT NULL,
              chat_id TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS wecom_runtime (
              id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL,
              phase TEXT NOT NULL, expires_at TEXT NOT NULL
            );
            ''')
            # 一次性绑定码已停用；企业微信身份由共享模式或成员管理配置。
            db.execute('DROP TABLE IF EXISTS wecom_pairings')
        self._migrate_projects()
        self._migrate_owner_assignments()

    def _migrate_projects(self):
        with self.connect(write=True) as db:
            updates = []
            for row in db.execute('SELECT id,data FROM projects'):
                project = decode_project(row['data'])
                if 'schema_version' not in json.loads(row['data']):
                    updates.append((encode_project(project), row['id']))
            if not updates:
                return
            backup_path = f'{self.path}.before-project-items-{secrets.token_hex(6)}.bak'
            # 锁住写入后用独立读连接备份，避免对当前写事务调用 backup。
            with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(backup_path)) as backup:
                source.backup(backup)
            db.executemany('UPDATE projects SET data=? WHERE id=?', updates)

    def _migrate_owner_assignments(self):
        with self.connect(write=True) as db:
            updates = []
            for row in db.execute('SELECT id,data FROM projects'):
                project = decode_project(row['data'])
                assignments = project.get('owner_assignments') or legacy_owner_assignments(project.get('owner_name'))
                assignments = [
                    {**item, 'role': {'A': 'A角', 'B': 'B角'}.get(item['role'].upper(), item['role'].upper())}
                    for item in assignments
                ]
                roles = {user_id: {'A': 'A角', 'B': 'B角'}.get(role.upper(), role.upper())
                         for user_id, role in project.get('owner_roles', {}).items()}
                if assignments:
                    summary = owner_summary(assignments)
                    if (project.get('owner_assignments') != assignments or project.get('owner_name') != summary
                            or project.get('owner_roles', {}) != roles):
                        project['owner_assignments'] = assignments
                        project['owner_name'] = summary
                        project['owner_roles'] = roles
                        updates.append((encode_project(project), row['id']))
                elif project.get('owner_roles', {}) != roles:
                    project['owner_roles'] = roles
                    updates.append((encode_project(project), row['id']))
            if not updates:
                return
            backup_path = f'{self.path}.before-owner-assignments-{secrets.token_hex(6)}.bak'
            with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(backup_path)) as backup:
                source.backup(backup)
            db.executemany('UPDATE projects SET data=? WHERE id=?', updates)

    @contextmanager
    def connect(self, write=False):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def add_user(self, name, role='member', wecom_user_id=''):
        token = secrets.token_urlsafe(32)
        user_id = secrets.token_hex(8)
        with self.connect(write=True) as db:
            db.execute('INSERT INTO users(id,name,role,token_hash,wecom_user_id) VALUES(?,?,?,?,?)',
                       (user_id, name, role, token_hash(token), wecom_user_id))
        return {'id': user_id, 'name': name, 'role': role, 'active': True,
                'wecom_user_id': wecom_user_id, 'access_key': token}

    def ensure_shared_identity(self):
        with self.connect(write=True) as db:
            db.execute("INSERT OR IGNORE INTO users(id,name,role,token_hash,active) VALUES(?,?,?,?,1)",
                       ('internal-workspace', '公司共享工作台', 'member', token_hash(secrets.token_urlsafe(32))))
        return 'internal-workspace'

    def authenticate(self, token):
        with self.connect() as db:
            row = db.execute('SELECT id,name,role,active,wecom_user_id FROM users '
                             'WHERE token_hash=? AND active=1', (token_hash(token),)).fetchone()
            return dict(row) if row else None

    @staticmethod
    def project(row):
        if row is None:
            return None
        return {**decode_project(row['data']), 'id': str(row['id']),
                'code': f"P{row['id']:04d}", 'version': row['version']}

    @staticmethod
    def user(db, user_id):
        row = db.execute('SELECT id,name,role,active,wecom_user_id FROM users WHERE id=?',
                         (user_id,)).fetchone()
        return dict(row) if row else None

    def settings(self):
        from .models import ReminderSettings
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key='reminders'").fetchone()
        return ReminderSettings.model_validate_json(row['value']) if row else ReminderSettings()
