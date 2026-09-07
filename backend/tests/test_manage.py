import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend.app.store import Store


class ManageTests(unittest.TestCase):
    def test_first_admin_and_non_overwriting_consistent_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.sqlite3'
            backup = Path(directory) / 'backup.sqlite3'
            env = {**os.environ, 'TRACKER_DB': str(source), 'PYTHONIOENCODING': 'utf-8'}
            root = Path(__file__).resolve().parents[2]
            def run(*args):
                return subprocess.run([sys.executable, '-m', 'backend.manage', *args],
                                      cwd=root, env=env, capture_output=True, text=True, encoding='utf-8')
            created = run('init-admin', '--name', '测试管理员')
            self.assertEqual(created.returncode, 0)
            key = created.stdout.strip().splitlines()[-1]
            user = Store(source).authenticate(key)
            self.assertEqual(user['name'], '测试管理员')
            self.assertEqual(user['role'], 'admin')
            self.assertNotEqual(run('init-admin').returncode, 0)
            self.assertEqual(run('backup', str(backup)).returncode, 0)
            self.assertEqual(Store(backup).authenticate(key)['id'], user['id'])
            previous = backup.read_bytes()
            self.assertNotEqual(run('backup', str(backup)).returncode, 0)
            self.assertEqual(backup.read_bytes(), previous)


if __name__ == '__main__':
    unittest.main()
