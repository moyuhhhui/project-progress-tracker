"""提醒只依赖已确认数据；不调用模型，也不把通知成功当作员工已读。"""
import copy
import json
import os
import secrets
from datetime import date, datetime, time, timedelta

import httpx

from .service import TZ, now_local
from .store import encode, encode_project

LABELS = {'overdue': '计划逾期', 'stale': '待更新', 'due_soon': '临近到期', 'blocked': '有阻碍'}


def is_workday(day, settings):
    return settings.workday_overrides.get(day.isoformat(), day.weekday() < 5)


def shift_workdays(value, count, settings):
    step = 1 if count >= 0 else -1
    remaining = abs(count)
    while remaining:
        value += timedelta(days=step)
        if is_workday(value.date(), settings):
            remaining -= 1
    return value


def node_flags(project, node, now, settings):
    flags = []
    if node['blocker'] and node['status'] not in ('completed', 'cancelled'):
        flags.append('blocked')
    if project['status'] in ('paused', 'completed', 'cancelled') or node['status'] in ('paused', 'completed', 'cancelled'):
        return flags
    if node['due_date']:
        deadline = datetime.combine(date.fromisoformat(node['due_date']), time(settings.due_hour), TZ)
        if now > deadline:
            flags.append('overdue')
        elif now >= shift_workdays(deadline, -1, settings):
            flags.append('due_soon')
    if not node['start_date']:
        return flags
    start = datetime.combine(date.fromisoformat(node['start_date']), time(settings.start_hour), TZ)
    anchors = [start]
    for value in (node['last_report_at'], node['started_at'], node['resumed_at'], project.get('resumed_at')):
        if value:
            anchors.append(datetime.fromisoformat(value))
    anchor = max(anchors)
    if now >= start and now >= shift_workdays(anchor, node['update_interval'], settings):
        flags.append('stale')
    order = ['overdue', 'stale', 'due_soon', 'blocked']
    return [f for f in order if f in flags]


def decorate(project, now, settings):
    for node in project['milestones']:
        node['flags'] = node_flags(project, node, now, settings)
    nodes = [n for n in project['milestones'] if n['status'] != 'cancelled']
    project['progress'] = round(sum(n['progress'] for n in nodes) / len(nodes), 1) if nodes else None
    project['flags'] = [flag for flag in ('overdue','stale','due_soon','blocked')
                        if any(flag in n['flags'] for n in nodes)]
    project['risk_score'] = sum({'overdue': 8, 'stale': 4, 'due_soon': 2, 'blocked': 1}[f] for f in project['flags'])


def auto_complete_due(store, now=None):
    now = now or now_local()
    settings = store.settings()
    completed = 0
    with store.connect(write=True) as db:
        for row in db.execute('SELECT * FROM projects').fetchall():
            project = store.project(row)
            before = copy.deepcopy(project)
            changed = 0
            for node in project['milestones']:
                if node['status'] not in ('not_started', 'active') or not node.get('due_date'):
                    continue
                if (node.get('auto_complete_disabled') or node.get('last_report_at') or node.get('paused_at')
                        or node.get('resumed_at') or node.get('blocker')):
                    continue
                deadline = datetime.combine(date.fromisoformat(node['due_date']), time(settings.due_hour), TZ)
                if now < deadline:
                    continue
                node.update(status='completed', progress=100, completed_at=now.isoformat(),
                            completion_source='automatic', pause_reason='')
                changed += 1
            if not changed:
                continue
            project['updated_at'] = now.isoformat()
            pid, version = int(project['id']), project['version'] + 1
            stored = {key: value for key, value in project.items() if key not in ('id', 'code', 'version')}
            db.execute('UPDATE projects SET version=?,data=? WHERE id=?', (version, encode_project(stored), pid))
            after = {**stored, 'id': str(pid), 'code': f'P{pid:04d}', 'version': version}
            db.execute('INSERT INTO audit(project_id,user_id,at,intent,before_data,after_data,draft_id) '
                       'VALUES(?,?,?,?,?,?,?)',
                       (pid, 'system:auto-complete', now.isoformat(), 'milestone_status', encode(before), encode(after),
                        f"auto-complete:{pid}:{now.isoformat()}"))
            completed += changed
    return completed


def scan(store, now=None):
    now = now or now_local()
    settings = store.settings()
    day, at = now.date().isoformat(), now.isoformat()
    generated = 0
    with store.connect(write=True) as db:
        # 未发送的旧日任务不补发；已在途消息保留独立结果，不伪造撤回。
        db.execute("UPDATE reminders SET status='cancelled',detail='旧日期任务已过期',updated_at=? "
                   "WHERE local_day<>? AND status IN ('queued','blocked','failed')", (at, day))
        active_ids = set()
        for row in db.execute('SELECT * FROM projects').fetchall():
            project = store.project(row)
            for node in project['milestones']:
                reasons = [r for r in node_flags(project, node, now, settings) if r != 'blocked']
                if not reasons:
                    continue
                key = f"{node['id']}:{day}"
                active_ids.add(key)
                user = store.user(db, node['owner_id'])
                valid_owner = user and user['active'] and user['wecom_user_id'] and user['role'] != 'display'
                status, detail = ('queued', '') if valid_owner else ('blocked', '负责人未启用或未绑定企微账号')
                old = db.execute('SELECT * FROM reminders WHERE id=?', (key,)).fetchone()
                if old and old['status'] in ('accepted', 'sending', 'uncertain'):
                    continue
                if old and old['status'] == 'failed' and old['owner_id'] == node['owner_id']:
                    status, detail = old['status'], old['detail']
                db.execute('INSERT INTO reminders(id,project_id,milestone_id,owner_id,local_day,reasons,status,updated_at,detail) '
                           'VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET owner_id=excluded.owner_id,'
                           'reasons=excluded.reasons,status=excluded.status,updated_at=excluded.updated_at,detail=excluded.detail',
                           (key, project['id'], node['id'], node['owner_id'], day, encode(reasons), status, at, detail))
                generated += 1
        rows = db.execute("SELECT id FROM reminders WHERE local_day=? AND status IN ('queued','blocked','failed')", (day,)).fetchall()
        for row in rows:
            if row['id'] not in active_ids:
                db.execute("UPDATE reminders SET status='cancelled',detail='提醒条件已消除',updated_at=? WHERE id=?", (at,row['id']))
        # 进程在发送期间异常退出，无法证明外部消息是否已接收，不自动重复发送。
        db.execute("UPDATE reminders SET status='uncertain',detail='发送进程中断，需人工核对' "
                   "WHERE status='sending' AND updated_at<?", ((now-timedelta(minutes=10)).isoformat(),))
    return generated


class WeComSender:
    @property
    def configured(self):
        return os.getenv('TRACKER_WECOM_SEND_ENABLED') == 'true' and all(
            os.getenv(k) for k in ('WECOM_CORP_ID', 'WECOM_APP_SECRET', 'WECOM_AGENT_ID'))

    def send(self, user_id, content):
        if not self.configured:
            return 'blocked', '企微主动发送未启用或凭证不完整'
        try:
            with httpx.Client(timeout=httpx.Timeout(15, connect=5)) as client:
                response = client.get('https://qyapi.weixin.qq.com/cgi-bin/gettoken', params={
                    'corpid': os.environ['WECOM_CORP_ID'], 'corpsecret': os.environ['WECOM_APP_SECRET']})
                response.raise_for_status()
                token = response.json()
                if not isinstance(token, dict):
                    return 'failed', '企微认证响应格式异常（未发送业务消息）'
                if token.get('errcode', 0) != 0 or not token.get('access_token'):
                    return 'failed', f"企微认证错误码：{token.get('errcode', 'unknown')}"
        except (httpx.HTTPError, ValueError):
            return 'failed', '企微认证请求失败（未发送业务消息）'
        try:
            agent_id = int(os.environ['WECOM_AGENT_ID'])
        except ValueError:
            return 'blocked', 'WECOM_AGENT_ID 必须为整数'
        try:
            with httpx.Client(timeout=httpx.Timeout(15, connect=5)) as client:
                response = client.post('https://qyapi.weixin.qq.com/cgi-bin/message/send',
                    params={'access_token': token['access_token']}, json={
                        'touser': user_id, 'msgtype': 'text', 'agentid': agent_id,
                        'text': {'content': content}, 'enable_duplicate_check': 1, 'duplicate_check_interval': 600})
                response.raise_for_status()
                result = response.json()
            if not isinstance(result, dict) or type(result.get('errcode')) is not int:
                return 'uncertain', '企微发送响应格式异常，需人工核对'
            if result.get('errcode') != 0 or result.get('invaliduser') or result.get('unlicenseduser'):
                return 'failed', f"企微拒绝消息，错误码：{result.get('errcode', 'unknown')}，请核对用户与应用权限"
            return 'accepted', '平台已接受，不代表员工已读'
        except (httpx.HTTPError, ValueError):
            return 'uncertain', '发送结果不确定，停止自动重试并请管理员核对'


def dispatch(store, sender=None, now=None):
    sender, now = sender or WeComSender(), now or now_local()
    settings = store.settings()
    if not is_workday(now.date(), settings) or not settings.start_hour <= now.hour < settings.end_hour:
        return 0
    sent, batches = 0, 0
    with store.connect() as db:
        owners = [r['owner_id'] for r in db.execute("SELECT DISTINCT owner_id FROM reminders WHERE local_day=? "
                    "AND status IN ('queued','blocked','failed') AND attempts<3 "
                    "AND (next_attempt IS NULL OR next_attempt<=?)", (now.date().isoformat(),now.isoformat()))]
    for uid in owners:
        if batches >= 20:
            break
        # 发送前重新读取业务数据，取消已完成/换人/暂停的旧任务。
        with store.connect(write=True) as db:
            user = store.user(db, uid)
            rows = db.execute("SELECT * FROM reminders WHERE owner_id=? AND local_day=? "
                              "AND status IN ('queued','blocked','failed') AND attempts<3 "
                              "AND (next_attempt IS NULL OR next_attempt<=?)", (uid, now.date().isoformat(),now.isoformat())).fetchall()
            valid, entries = [], []
            for row in rows:
                project = store.project(db.execute('SELECT * FROM projects WHERE id=?', (row['project_id'],)).fetchone())
                node = next((n for n in project['milestones'] if n['id'] == row['milestone_id']), None) if project else None
                reasons = [f for f in node_flags(project,node,now,settings) if f != 'blocked'] if node else []
                if not node or node['owner_id'] != uid or not reasons:
                    db.execute("UPDATE reminders SET status='cancelled',detail='发送前校验已失效' WHERE id=?", (row['id'],))
                    continue
                if not user or not user['active'] or user['role'] == 'display' or not user['wecom_user_id'] or not sender.configured:
                    db.execute("UPDATE reminders SET status='blocked',detail='负责人未绑定或企微发送未配置' WHERE id=?", (row['id'],))
                    continue
                entry = (f"{project['code']} {project['name']} · {node['name']}\n"
                         f"进度 {node['progress']}%｜截止 {node['due_date']}\n"
                         + '、'.join(LABELS[r] for r in reasons))
                # 企微文本限制按 UTF-8 字节保守分批；未纳入的目标留待下次发送。
                if len(('\n\n'.join(entries+[entry])).encode('utf-8')) > 1700:
                    break
                valid.append(row['id'])
                entries.append(entry)
            for key in valid:
                db.execute("UPDATE reminders SET status='sending',attempts=attempts+1,updated_at=? WHERE id=?", (now.isoformat(),key))
        if not valid:
            continue
        batches += 1
        content = '【项目进度提醒】\n\n' + '\n\n'.join(entries) + '\n\n请更新最新进展、阻碍和下一步安排。'
        try:
            status, detail = sender.send(user['wecom_user_id'], content)
        except Exception:
            status, detail = 'uncertain', '通知通道异常，发送结果需人工核对'
        if status not in ('accepted','failed','blocked','uncertain'):
            status, detail = 'uncertain', '通知通道返回未知状态'
        with store.connect(write=True) as db:
            for key in valid:
                db.execute('UPDATE reminders SET status=?,detail=?,updated_at=?,next_attempt=? WHERE id=?',
                           (status,detail,now.isoformat(),(now+timedelta(minutes=10)).isoformat(),key))
        sent += len(valid) if status == 'accepted' else 0
    return sent


def acquire_lease(store, owner, now=None):
    now = now or now_local()
    with store.connect(write=True) as db:
        row = db.execute('SELECT * FROM worker_lease WHERE id=1').fetchone()
        if row and row['expires_at'] > now.isoformat() and row['owner'] != owner:
            return False
        db.execute('INSERT INTO worker_lease VALUES(1,?,?) ON CONFLICT(id) DO UPDATE '
                   'SET owner=excluded.owner,expires_at=excluded.expires_at',
                   (owner,(now+timedelta(minutes=30)).isoformat()))
    return True


def run_cycle(store, sender=None, now=None):
    owner = secrets.token_hex(16)
    if not acquire_lease(store, owner, now):
        return {'skipped': True}
    try:
        auto_completed = auto_complete_due(store, now)
        return {'auto_completed': auto_completed, 'queued': scan(store, now), 'accepted': dispatch(store, sender, now)}
    finally:
        with store.connect(write=True) as db:
            db.execute('DELETE FROM worker_lease WHERE id=1 AND owner=?', (owner,))
