import json
import os
import secrets
import sqlite3
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from . import ai
from .models import Action, MessageInput, UserCreate, UserPatch, ReminderSettings
from .reminders import scan, scan_meetings
from .service import BusinessError, Service, require, manager, all_access
from .store import Store, encode, token_hash
from .wecom import read_status


def create_app(db_path=None):
    app = FastAPI(title='项目进度工作台', docs_url=None, redoc_url=None, openapi_url=None)
    store = Store(db_path)
    shared_user_id = os.getenv('TRACKER_SHARED_USER_ID', '').strip()
    service = Service(store, shared_user_id=shared_user_id)
    if service.internal_shared:
        shared_user_id = store.ensure_shared_identity()
    app.state.store, app.state.service = store, service
    bearer = HTTPBearer(auto_error=False)

    @app.exception_handler(BusinessError)
    async def business_error(request, exc):
        return JSONResponse({'detail':exc.message},status_code=exc.status)

    @app.middleware('http')
    async def limits(request: Request, call_next):
        if request.method in ('POST','PUT','PATCH'):
            length = request.headers.get('content-length', '')
            if not length.isdigit() or int(length) > 100000:
                return JSONResponse({'detail':'请求体缺少长度或超过 100KB'},status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Cache-Control'] = 'no-store'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        return response

    def user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        if service.internal_shared:
            with store.connect() as db:
                return service.fresh_user(db, store.user(db, shared_user_id))
        if credentials is None and shared_user_id:
            with store.connect() as db:
                actor = store.user(db, shared_user_id)
            require(actor and actor['active'] and actor['role'] == 'admin', '共享工作台账号不可用，请检查服务配置', 503)
            return actor
        require(credentials is not None, '请输入访问密钥', 401)
        actor = store.authenticate(credentials.credentials)
        require(actor is not None, '访问密钥无效或已撤销', 401)
        return actor

    def editor(actor=Depends(user)):
        require(actor['role'] != 'display', '大屏账号仅可查看展示数据',403)
        return actor

    def admin(actor=Depends(editor)):
        require(all_access(actor), '需要管理员权限',403)
        return actor

    @app.get('/api/me')
    def me(actor=Depends(user)):
        return actor

    @app.get('/api/status')
    def status(actor=Depends(editor)):
        return {'ai_configured':ai.configured(), 'ai_model':os.getenv('DEEPSEEK_MODEL','') if ai.configured() else '',
                'wecom_send_enabled':os.getenv('TRACKER_WECOM_SEND_ENABLED') == 'true', **read_status(service)}

    @app.get('/api/projects')
    def projects(actor=Depends(editor)):
        return {'projects':service.projects(actor), 'at':service.clock().isoformat()}

    @app.get('/api/meetings')
    def meetings(actor=Depends(editor)):
        return {'meetings': service.meetings(actor), 'at': service.clock().isoformat()}

    @app.get('/api/display')
    def display(actor=Depends(user)):
        # 大屏与工作台共用同一份数据快照，不再单独按管理员/普通账号切换口径。
        return {'projects':service.projects(actor), 'meetings': service.meetings(actor), 'at':service.clock().isoformat()}

    @app.post('/api/drafts')
    def draft(action: Action, actor=Depends(editor)):
        return service.create_draft(actor,action,auto_save=True)

    @app.get('/api/drafts')
    def drafts(actor=Depends(editor)):
        with store.connect() as db:
            ids = [r['id'] for r in db.execute('SELECT id FROM drafts WHERE user_id=? OR ? ORDER BY created_at DESC LIMIT 60',
                   (actor['id'], service.shared_actor(actor)))]
        result = []
        for did in ids:
            try:
                result.append(service.draft(actor,did))
            except BusinessError:
                continue
        return result

    @app.get('/api/drafts/{draft_id}')
    def get_draft(draft_id: str, actor=Depends(editor)):
        return service.draft(actor,draft_id)

    @app.post('/api/drafts/{draft_id}/confirm')
    def confirm(draft_id: str, actor=Depends(editor)):
        return service.confirm(actor,draft_id)

    @app.post('/api/drafts/{draft_id}/cancel')
    def cancel(draft_id: str, actor=Depends(editor)):
        return service.cancel(actor,draft_id)

    @app.post('/api/messages')
    async def messages(body: MessageInput, actor=Depends(editor)):
        with store.connect() as db:
            count = db.execute('SELECT count(*) FROM messages WHERE user_id=? AND created_at>?',
                               (actor['id'],(service.clock()-timedelta(minutes=1)).isoformat())).fetchone()[0]
        require(count < 10, '提交过于频繁，请稍后再试',429)
        return await ai.parse_message(service,actor,body)

    @app.get('/api/projects/{project_id}/history')
    def history(project_id: str, actor=Depends(editor)):
        with store.connect() as db:
            p = service.get_project(db,project_id,actor)
            audit = [dict(r) for r in db.execute("SELECT a.*,CASE WHEN a.user_id='system:auto-complete' THEN '系统自动完成' ELSE u.name END AS actor_name FROM audit a LEFT JOIN users u ON a.user_id=u.id "
                     'WHERE project_id=? ORDER BY a.id DESC LIMIT 100',(p['id'],))]
            reports = [dict(r) for r in db.execute('SELECT r.*,u.name AS actor_name FROM reports r LEFT JOIN users u ON r.user_id=u.id '
                       'WHERE project_id=? ORDER BY r.id DESC LIMIT 100',(p['id'],))]
        for row in audit:
            for key in ('before_data','after_data'):
                row[key] = json.loads(row[key]) if row[key] else None
        for row in reports:
            row['data'] = json.loads(row['data'])
        return {'audit':audit,'reports':reports}

    @app.get('/api/users')
    def users(project_id: str | None = None, actor=Depends(editor)):
        with store.connect() as db:
            if project_id:
                project = service.get_project(db, project_id, actor)
                require(manager(actor, project), '仅项目负责人可查看可分派成员', 403)
                return [dict(r) for r in db.execute("SELECT id,name,active FROM users WHERE active=1 AND role!='display' ORDER BY name")]
            if all_access(actor):
                return [dict(r) for r in db.execute('SELECT id,name,role,active,wecom_user_id FROM users ORDER BY name')]
            visible = {actor['id']}
            for p in service.projects(actor):
                visible.update(p['member_ids'])
            return [{'id':u['id'],'name':u['name'],'active':u['active']} for uid in visible if (u := store.user(db,uid))]

    @app.post('/api/users')
    def add_user(body: UserCreate, actor=Depends(admin)):
        try:
            result = store.add_user(body.name, 'member' if service.internal_shared else body.role, body.wecom_user_id)
            if shared_user_id:
                result.pop('access_key', None)
            return result
        except sqlite3.IntegrityError as exc:
            raise BusinessError('该企业微信账号已绑定') from exc

    @app.patch('/api/users/{user_id}')
    def edit_user(user_id: str, body: UserPatch, actor=Depends(admin)):
        data = body.model_dump(exclude_unset=True)
        require(data and all(v is not None for v in data.values()), '没有有效修改字段')
        require(not (actor['id'] == user_id and data.get('active') is False), '不能停用当前工作台身份')
        with store.connect(write=True) as db:
            require(store.user(db,user_id) is not None,'成员不存在',404)
            try:
                for key,value in data.items():
                    # key 仅来自 extra=forbid 的 UserPatch 字段。
                    db.execute(f'UPDATE users SET {key}=? WHERE id=?', (value,user_id))
            except sqlite3.IntegrityError as exc:
                raise BusinessError('该企业微信账号已绑定') from exc
        return {'message':'成员已更新'}

    @app.post('/api/users/{user_id}/rotate-key')
    def rotate_key(user_id: str, actor=Depends(admin)):
        require(not shared_user_id, '共享工作台不使用访问密钥', 404)
        token = secrets.token_urlsafe(32)
        with store.connect(write=True) as db:
            require(store.user(db,user_id) is not None,'成员不存在',404)
            db.execute('UPDATE users SET token_hash=? WHERE id=?',(token_hash(token),user_id))
        return {'access_key':token,'message':'旧密钥已立即失效，新密钥仅显示一次'}

    @app.get('/api/settings')
    def settings(actor=Depends(editor)):
        return store.settings()

    @app.put('/api/settings')
    def update_settings(body: ReminderSettings, actor=Depends(admin)):
        with store.connect(write=True) as db:
            db.execute("INSERT INTO settings VALUES('reminders',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (body.model_dump_json(),))
        return body

    @app.get('/api/reminders')
    def reminders(actor=Depends(editor)):
        with store.connect() as db:
            if all_access(actor):
                rows = db.execute('SELECT r.*,u.name AS owner_name FROM reminders r LEFT JOIN users u ON r.owner_id=u.id ORDER BY updated_at DESC LIMIT 200').fetchall()
            else:
                rows = db.execute('SELECT r.*,u.name AS owner_name FROM reminders r LEFT JOIN users u ON r.owner_id=u.id WHERE owner_id=? ORDER BY updated_at DESC LIMIT 100', (actor['id'],)).fetchall()
        return [{**dict(r),'reasons':json.loads(r['reasons'])} for r in rows]

    @app.post('/api/reminders/scan')
    def scan_reminders(actor=Depends(admin)):
        return {'count': scan(store) + scan_meetings(store), 'message':'已检查并更新提醒队列；此操作不发送外部消息'}

    @app.post('/api/reminders/{reminder_id}/resolve')
    def resolve_reminder(reminder_id: str, actor=Depends(admin)):
        with store.connect(write=True) as db:
            count = db.execute("UPDATE reminders SET status='accepted',detail='管理员已人工核对平台接收结果' "
                               "WHERE id=? AND status='uncertain'", (reminder_id,)).rowcount
        require(count == 1,'仅可人工确认发送结果不确定的记录',409)
        return {'message':'已记为人工核对，不代表员工已读'}

    dist = Path(__file__).resolve().parents[2] / 'frontend' / 'dist'
    if dist.exists():
        app.mount('/assets', StaticFiles(directory=dist/'assets'), name='assets')

        @app.get('/')
        def frontend():
            return FileResponse(dist/'index.html')
    else:
        @app.get('/')
        def no_frontend():
            return {'message':'后端已运行。前端请执行 npm --prefix frontend install，然后 npm --prefix frontend run dev。'}
    return app


app = create_app()
