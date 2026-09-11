"""智能机器人群消息适配；解析后直接保存并展示。"""
import hashlib
import ipaddress
import re
import secrets
from datetime import timedelta
from urllib.parse import urlsplit

from . import ai
from .models import MessageInput
from .service import BusinessError, require
from .store import encode


def validate_web_url(value):
    try:
        url = urlsplit(value)
        hostname = (url.hostname or '').rstrip('.').lower()
        is_loopback = hostname == 'localhost'
        if not is_loopback:
            try:
                is_loopback = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                pass
        valid = (url.scheme in ('http', 'https') and url.hostname and url.port != 0
                 and not is_loopback
                 and not url.username and not url.password and not url.query and not url.fragment
                 and url.path in ('', '/') and not any(c.isspace() for c in value)
                 and not any(c in value for c in '[]()<>\\'))
    except ValueError:
        valid = False
    require(valid, 'TRACKER_WEB_URL 须为员工可访问的 http(s) 根地址，不含账号、查询参数或片段')
    return value.rstrip('/')


class BotHandler:
    def __init__(self, service, bot_id, web_url):
        self.service, self.bot_id = service, bot_id
        self.web_url = validate_web_url(web_url)

    def actor(self, userid):
        if self.service.internal_shared:
            shared_id = self.service.store.ensure_shared_identity()
            with self.service.store.connect() as db:
                return self.service.fresh_user(db, self.service.store.user(db, shared_id))
        with self.service.store.connect() as db:
            row = db.execute("SELECT id FROM users WHERE wecom_user_id=? AND active=1 AND role!='display'",
                             (userid,)).fetchone()
            require(row, '请管理员在成员管理中配置你的企业微信成员 ID，并启用普通成员账号。', 403)
            return self.service.fresh_user(db, self.service.store.user(db, row['id']))

    async def handle(self, frame, parser=None):
        body = frame.get('body') if isinstance(frame, dict) else None
        if not isinstance(body, dict) or frame.get('cmd') != 'aibot_msg_callback':
            return None
        sender = body.get('from')
        if not isinstance(sender, dict):
            return None
        msgid, chatid, userid = body.get('msgid'), body.get('chatid'), sender.get('userid')
        if (body.get('aibotid') != self.bot_id or body.get('chattype') != 'group'
                or not all(isinstance(v, str) and 0 < len(v) <= 300 for v in (msgid, chatid, userid))):
            return None
        text_body = body.get('text')
        text = text_body.get('content') if isinstance(text_body, dict) else None
        if isinstance(text, str):
            # 群回调可能保留开头的 @昵称及企业微信插入的 Unicode 空格。
            text = re.sub(r'^@\S+\s+', '', text.strip(), count=1).strip()
        if body.get('msgtype') == 'text' and isinstance(text, str) and re.match(r'^绑定(?:\s|$)', text):
            return '当前工作台无需绑定，直接 @机器人发送项目安排即可。'
        try:
            actor = self.actor(userid)
        except BusinessError as exc:
            return exc.message
        if body.get('msgtype') != 'text':
            return '目前支持 @机器人发送纯文本，请把项目操作写成文字。'
        if not isinstance(text, str) or not 0 < len(text.strip()) <= 6000:
            return '请发送 1 至 6000 字的项目说明。'
        text = text.strip()
        if re.match(r'^(确认|取消|帮助)(?:\s|$)', text):
            return f'无需确认。项目消息解析后直接保存并上大屏，需要修改请打开工作台：{self.web_url}/#projects'
        previous = None
        if text.startswith('补充'):
            match = re.fullmatch(r'补充\s+([0-9a-f]{24})\s+(.+)', text, re.DOTALL)
            if not match:
                return '补充格式：@机器人 补充 草稿完整编号 补充说明。编号可从草稿链接中复制。'
            previous, text = match.groups()
        key = hashlib.sha256(encode([self.bot_id, chatid, userid, msgid]).encode()).hexdigest()
        try:
            with self.service.store.connect() as db:
                if previous and not self.service.internal_shared:
                    bound = db.execute('SELECT 1 FROM wecom_drafts WHERE draft_id=? AND bot_id=? AND chat_id=? AND user_id=?',
                                       (previous, self.bot_id, chatid, actor['id'])).fetchone()
                    require(bound, '无法补充此草稿；请使用本人在当前群发起的草稿。', 404)
                duplicate = db.execute('SELECT 1 FROM messages WHERE id=?', ('wecom:' + actor['id'] + ':' + key,)).fetchone()
                count = db.execute('SELECT count(*) FROM messages WHERE user_id=? AND created_at>?',
                                   (actor['id'], (self.service.clock() - timedelta(minutes=1)).isoformat())).fetchone()[0]
                require(duplicate or count < 10, '提交过于频繁，请一分钟后再试。', 429)
            result = await ai.parse_message(self.service, actor,
                MessageInput(text=text, client_message_id=key, previous_draft_id=previous), parser, channel='wecom')
            # 模型调用期间可能停用或重新绑定成员；不得向旧身份返回草稿入口。
            if not self.service.internal_shared:
                require(self.actor(userid)['id'] == actor['id'], '账号绑定已变化', 403)
            if result['kind'] == 'draft':
                draft = self.service.draft(actor, result['draft']['id'])
                if draft['status'] == 'confirmed':
                    return f'已自动保存，可在大屏查看；需要调整请在前端修改：{self.web_url}/#projects'
                with self.service.store.connect(write=True) as db:
                    db.execute('INSERT OR IGNORE INTO wecom_drafts VALUES(?,?,?,?)',
                               (draft['id'], self.bot_id, chatid, actor['id']))
                labels = {'pending': '草稿待处理，请补充或重新提交', 'needs_input': '草稿需要补充信息，暂不上大屏',
                          'confirmed': '信息齐全，已自动保存', 'cancelled': '草稿已取消', 'expired': '草稿已过期'}
                intents = {'record_item': '记录项目事项', 'create_project': '创建项目', 'edit_project': '修改项目', 'add_milestone': '新增目标节点',
                           'edit_milestone': '修改目标节点', 'report_progress': '汇报进度',
                           'project_status': '更新项目状态', 'milestone_status': '更新目标状态'}
                reply = (f"已识别：{intents[draft['action']['intent']]}。{labels[draft['status']]}。查看详情：\n"
                         f"{self.web_url}/#draft={draft['id']}")
                if draft['status'] in ('pending', 'needs_input'):
                    reply += '\n在本群 @机器人发送：补充 草稿完整编号 补充说明。'
                return reply
            if result['kind'] == 'batch':
                total, succeeded, failed = result['total'], result['succeeded'], result['failed']
                if succeeded and not failed:
                    return (f'已处理 {total} 个项目，成功保存 {succeeded} 项。'
                            f'可在工作台查看：{self.web_url}/#projects')
                if succeeded:
                    reply = f'已处理 {total} 个项目：成功 {succeeded} 项，失败 {failed} 项。'
                    reply += f'已保存内容可在工作台查看：{self.web_url}/#projects'
                else:
                    reply = f'已处理 {total} 个项目：成功 0 项，失败 {failed} 项；未保存任何项目。'
                if result.get('failures'):
                    reply += '\n' + '\n'.join(
                        f"- {item['project_name']}：{item['message']}"
                        for item in result['failures'])
                return reply
            if result['kind'] == 'query':
                return f'请打开工作台查看项目：{self.web_url}/#projects'
            return '未识别到明确的项目操作，未修改数据。'
        except BusinessError as exc:
            if exc.status == 429:
                return '提交过于频繁，请一分钟后再试。'
            if exc.status == 503:
                return 'AI 尚未配置，请管理员配置后再试；当前可使用网页手动录入。'
            if exc.status == 502:
                return '解析失败或超时，未保存项目；请重新发送或在前端录入。'
            if self.service.internal_shared:
                return f'处理未完成：{exc.message}。查看工作台：{self.web_url}/#projects'
            # 校验异常中可能含字段内容或其他项目名称，不在群里公开。
            return f'无法处理此消息，请本人在前端检查项目及账号权限：{self.web_url}/#projects'


class BotRuntime:
    """单机进程租约和状态心跳；与提醒 worker 的租约分开。"""
    def __init__(self, service, bot_id):
        self.service, self.owner = service, bot_id + ':' + secrets.token_hex(12)

    def acquire(self):
        at = self.service.clock()
        with self.service.store.connect(write=True) as db:
            row = db.execute('SELECT * FROM wecom_runtime WHERE id=1').fetchone()
            require(not row or row['expires_at'] <= at.isoformat(), '已有企业微信接收进程在运行', 409)
            db.execute('INSERT OR REPLACE INTO wecom_runtime VALUES(1,?,?,?)',
                       (self.owner, 'connecting', (at + timedelta(seconds=90)).isoformat()))

    def update(self, phase):
        with self.service.store.connect(write=True) as db:
            count = db.execute('UPDATE wecom_runtime SET phase=?,expires_at=? WHERE id=1 AND owner=?',
                               (phase, (self.service.clock() + timedelta(seconds=90)).isoformat(), self.owner)).rowcount
            require(count == 1, '企业微信接收进程租约已失效', 409)

    def close(self):
        with self.service.store.connect(write=True) as db:
            db.execute("UPDATE wecom_runtime SET phase='stopped',expires_at=? WHERE id=1 AND owner=?",
                       (self.service.clock().isoformat(), self.owner))


def read_status(service):
    with service.store.connect() as db:
        row = db.execute('SELECT phase,expires_at FROM wecom_runtime WHERE id=1').fetchone()
    phase = row['phase'] if row else 'not_started'
    if row and phase != 'stopped' and row['expires_at'] <= service.clock().isoformat():
        phase = 'offline'
    labels = {'not_started': '企业微信接收进程尚未启动', 'connecting': '正在连接企业微信',
              'authenticated': '企业微信长连接已认证，等待群内 @消息；真实业务闭环仍需验收',
              'disconnected': '企业微信连接已断开，正在重连', 'error': '企业微信连接或回复异常，请检查接收进程',
              'offline': '企业微信接收进程心跳已过期', 'stopped': '企业微信接收进程已停止'}
    return {'wecom_inbound': phase, 'message': labels.get(phase, '企业微信接收状态待检查')}
