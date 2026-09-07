"""仅供本机浏览器验收：临时数据库、公开测试密钥、禁止外部发送。

先构建前端，再在根目录运行 python -m backend.tests.browser_fixture。
管理测试密钥 browser-test-admin；大屏测试密钥 browser-test-display。
只监听 127.0.0.1:8765，停止进程即销毁临时库，不使用正式 TRACKER_DB。
"""
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory(prefix='tracker-browser-test-') as directory:
        os.environ['TRACKER_DB'] = str(Path(directory) / 'browser.sqlite3')
        os.environ['TRACKER_AI_ENABLED'] = 'false'
        os.environ['TRACKER_WECOM_SEND_ENABLED'] = 'false'
        os.environ['TRACKER_WECOM_BOT_ENABLED'] = 'false'
        import uvicorn
        from backend.app.main import create_app
        from backend.app.models import Action
        from backend.app.store import token_hash

        app = create_app()
        store, service = app.state.store, app.state.service
        admin = store.add_user('验收管理员', 'admin')
        display = store.add_user('验收大屏', 'display')
        member = store.add_user('验收成员')
        with store.connect(write=True) as db:
            for user, key in ((admin, 'browser-test-admin'), (display, 'browser-test-display'),
                              (member, 'browser-test-member')):
                db.execute('UPDATE users SET token_hash=? WHERE id=?', (token_hash(key), user['id']))
        today = date.today()
        start, due = (today - timedelta(days=10)).isoformat(), (today + timedelta(days=10)).isoformat()
        for index in range(3):
            action = Action(intent='create_project', data={
                'name': f'验收示例 {index + 1} · 展厅改造', 'description': '临时测试数据，非公司真实项目',
                'owner_id': admin['id'], 'member_ids': [member['id']],
                'start_date': start, 'due_date': due, 'display_visible': index != 2,
                'milestones': [{'name': f'阶段 {n + 1}', 'criterion': '验收清单确认',
                                'owner_id': member['id'], 'start_date': start,
                                'due_date': (today + timedelta(days=n-1)).isoformat()}
                               for n in range(10 if index == 0 else 2)]})
            draft = service.create_draft(admin, action)
            service.confirm(admin, draft['id'])
        linked = service.create_draft(admin, Action(intent='edit_project', project_id='1',
            data={'name': '验收企微链接 · 修改预览', 'reason': '验证登录后直接打开草稿'}))
        print(f"企微草稿链接验收：http://127.0.0.1:8765/#draft={linked['id']}", flush=True)
        uvicorn.run(app, host='127.0.0.1', port=8765)


if __name__ == '__main__':
    main()
