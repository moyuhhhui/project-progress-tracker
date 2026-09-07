"""本地管理命令，不在 HTTP 接口暴露首次管理员创建。"""
import argparse
import sqlite3
from pathlib import Path

from .app.store import Store


def main():
    parser = argparse.ArgumentParser(description='项目进度系统本地管理')
    sub = parser.add_subparsers(dest='command',required=True)
    init = sub.add_parser('init-admin',help='仅空用户库可创建首位管理员')
    init.add_argument('--name',default='管理员')
    backup = sub.add_parser('backup',help='使用 SQLite 一致性备份，不覆盖已有文件')
    backup.add_argument('destination')
    args = parser.parse_args()
    store = Store()
    if args.command == 'init-admin':
        with store.connect() as db:
            if db.execute('SELECT count(*) FROM users').fetchone()[0]:
                parser.error('已存在用户，不能重复初始化；请使用管理页面新增成员')
        user = store.add_user(args.name,'admin')
        print('管理员已创建。请妥善保管以下访问密钥（仅此次显示）：')
        print(user['access_key'])
    else:
        target = Path(args.destination).resolve()
        if target.exists() or target == Path(store.path).resolve():
            parser.error('备份目标已存在或与源库相同，拒绝覆盖')
        target.parent.mkdir(parents=True,exist_ok=True)
        with store.connect() as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        print(f'一致性备份已保存：{target}')


if __name__ == '__main__':
    main()
