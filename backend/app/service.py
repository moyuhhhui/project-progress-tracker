import copy
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError

from .models import (Action, ProjectCreate, ProjectPatch, MilestoneCreate, MilestonePatch,
                     ProgressReport, StatusChange, RecordItem)
from .store import Store, encode, encode_project, owner_summary

TZ = timezone(timedelta(hours=8))


def now_local():
    return datetime.now(TZ)


class BusinessError(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status
        super().__init__(message)


def require(condition, message, status=400):
    if not condition:
        raise BusinessError(message, status)


def all_access(user):
    return user['role'] == 'admin' or bool(user.get('internal_shared'))


def allowed(user, project):
    return all_access(user) or user['id'] in project['member_ids']


def manager(user, project):
    return all_access(user) or user['id'] == project['owner_id'] or (not project['owner_id'] and user['id'] == project['created_by'])


def validate(model, data):
    try:
        result = model.model_validate(data)
    except ValidationError as exc:
        details = ['.'.join(map(str, e['loc'])) + ': ' + e['msg'] for e in exc.errors()]
        raise BusinessError('字段校验失败：' + '；'.join(details)) from exc
    # 未提及的字段可以为空；显式 null 则拒绝，避免覆盖历史。
    for field in result.model_fields_set:
        require(getattr(result, field) is not None, f'{field} 不能设置为空；清空请用明确的清除操作')
    return result


class Service:
    def __init__(self, store: Store, clock=now_local, *, shared_user_id=''):
        self.store, self.clock = store, clock
        self.shared_user_id = shared_user_id
        self.internal_shared = os.getenv('TRACKER_INTERNAL_SHARED') == 'true'

    def shared_actor(self, actor):
        if self.internal_shared:
            return True
        return bool(actor['active'] and (self.internal_shared or
                    (self.shared_user_id and actor['id'] == self.shared_user_id and actor['role'] == 'admin')))

    def fresh_user(self, db, user):
        fresh = self.store.user(db, user['id'])
        if self.internal_shared:
            require(fresh is not None, '操作记录身份不存在', 400)
            return {**fresh, 'active': True, 'role': 'member', 'internal_shared': True}
        require(fresh and fresh['active'] and fresh['role'] != 'display', '账号无写入或管理权限', 403)
        return fresh

    def get_project(self, db, project_id, user):
        require(project_id is not None, '请选择项目')
        # 只接受系统 ID，不按模糊名称猜测对象。
        raw_id = project_id[1:].lstrip('0') or '0' if project_id.startswith('P') else project_id
        require(raw_id.isdigit(), '项目编号无效')
        project = self.store.project(db.execute('SELECT * FROM projects WHERE id=?', (raw_id,)).fetchone())
        require(project is not None and allowed(user, project), '项目不存在或无权访问', 404)
        return project

    @staticmethod
    def find_node(project, node_id):
        nodes = [n for n in project['milestones'] if n['id'] == node_id]
        require(len(nodes) == 1, '请选择此项目内的有效目标节点')
        return nodes[0]

    def validate_members(self, db, project, current=None):
        members = list(dict.fromkeys(project['member_ids'] + ([project['owner_id']] if project['owner_id'] else [])))
        project['member_ids'] = members
        require(set(project.get('owner_roles', {})) <= set(members), '责任分工只能分配给项目成员')
        previous_members = set(current['member_ids']) if current else set()
        previous_owners = {n['id']: n['owner_id'] for n in current['milestones']} if current else {}
        users = {}
        for uid in members:
            user = self.store.user(db, uid)
            require(user and user['role'] != 'display' and (user['active'] or uid in previous_members),
                    '新增负责人或成员不存在、停用或为大屏账号')
            users[uid] = user
        # 停用人员可以保留在既有记录中待移交，但不能收到新增责任。
        if project['owner_id'] and (not current or project['owner_id'] != current['owner_id']):
            require(users[project['owner_id']]['active'], '新项目负责人必须为启用账号')
        require(not (project['start_date'] and project['due_date']) or project['start_date'] <= project['due_date'], '项目截止日期早于开始日期')
        for node in project['milestones']:
            require(not node['owner_id'] or node['owner_id'] in members, '节点负责人必须属于项目')
            if node['owner_id'] and node['owner_id'] != previous_owners.get(node['id']):
                require(users[node['owner_id']]['active'], '新分派的节点负责人必须为启用账号')
            require((not (node['start_date'] and node['due_date']) or node['start_date'] <= node['due_date'])
                    and (not (project['start_date'] and node['start_date']) or project['start_date'] <= node['start_date'])
                    and (not (project['due_date'] and node['due_date']) or node['due_date'] <= project['due_date']),
                    '节点日期必须在项目计划范围内，且截止不早于开始')

    @staticmethod
    def new_node(data):
        return {**data, 'id': secrets.token_hex(8), 'original_due_date': data['due_date'],
                'status': 'not_started', 'progress': 0, 'summary': '', 'blocker': '',
                'next_step': '', 'expected_date': None, 'last_report_at': None,
                'started_at': None, 'completed_at': None, 'paused_at': None, 'resumed_at': None}

    def propose(self, db, actor, action, current=None):
        require(action.intent not in ('ignore', 'query'), '该操作无需写入')
        at = self.clock().isoformat()
        if action.intent == 'record_item':
            item = validate(RecordItem, action.data).model_dump(mode='json')
            require(not action.milestone_id, '新增信息不能覆盖已有事项')
            if current:
                require(current['status'] not in ('completed', 'cancelled'), '请先恢复项目再记录新安排')
                project = copy.deepcopy(current)
            else:
                require(item['project_name'], '信息已保留，请补充所属项目名称')
                project = dict(name=item['project_name'], description='', contact_company='', contact_name='', contact_info='',
                    owner_id=None, owner_name=None, owner_assignments=[], owner_roles={}, member_ids=[actor['id']],
                    start_date=None, due_date=None, display_visible=True,
                    milestones=[], status='active', original_due_date=None, created_by=actor['id'],
                    created_at=at, updated_at=at, completed_at=None, paused_at=None, resumed_at=None)
            if item['owner_assignments']:
                require(manager(actor, project), '仅项目管理人可修改负责人', 403)
                project['owner_assignments'] = item['owner_assignments']
                project['owner_name'] = owner_summary(item['owner_assignments'])
                project['owner_id'] = None
            elif item['owner_name']:
                require(manager(actor, project), '仅项目管理人可修改负责人', 403)
                project['owner_name'] = item['owner_name']
                project['owner_assignments'] = []
                project['owner_id'] = None
            items = item['items'] or [item]
            require(len(project['milestones']) + len(items) <= 50, '一个项目最多 50 条事项')
            for entry in items:
                owner = entry['owner_id']
                if owner and owner not in project['member_ids']:
                    require(manager(actor, project), '仅项目管理人可添加新的负责人', 403)
                    project['member_ids'].append(owner)
                node = self.new_node(dict(name=entry['title'] or entry['text'][:100], criterion='', owner_id=owner,
                    start_date=entry['start_date'], due_date=entry['due_date'], update_interval=2))
                node.update(summary=entry['text'], time_text=entry['time_text'], recorded_at=at,
                            preparation=project['status'] == 'not_started', status='active', started_at=at)
                project['milestones'].append(node)
            project['updated_at'] = at
        elif action.intent == 'create_project':
            require(not action.project_id and not action.milestone_id, '创建时不接受已有项目或节点编号')
            data = validate(ProjectCreate, action.data).model_dump(mode='json')
            if not all_access(actor):
                require(data['owner_id'] == actor['id'], '只能创建由本人负责的项目', 403)
                require(set(data['member_ids']) <= {actor['id']} and
                        all(n['owner_id'] == actor['id'] for n in data['milestones']),
                        '首次立项不能直接给其他员工分派任务；创建后由项目负责人管理成员', 403)
            nodes = [self.new_node(n) for n in data.pop('milestones')]
            project = {**data, 'milestones': nodes,
                       'original_due_date': data['due_date'], 'created_by': actor['id'],
                       'created_at': at, 'updated_at': at, 'completed_at': None,
                       'paused_at': None, 'resumed_at': None}
            if project['owner_assignments']:
                project['owner_name'] = owner_summary(project['owner_assignments'])
                project['owner_id'] = None
        else:
            require(current is not None, '请选择项目')
            project = copy.deepcopy(current)
            require(action.milestone_id is None or action.intent in
                    ('edit_milestone', 'report_progress', 'milestone_status'), '该操作不接受节点编号')
            if action.intent == 'edit_project':
                require(manager(actor, project), '仅项目负责人可修改项目', 403)
                require(project['status'] not in ('completed', 'cancelled'), '请先恢复项目再修改')
                patch = validate(ProjectPatch, action.data).model_dump(mode='json', exclude_unset=True)
                patch.pop('reason')
                require(bool(patch), '没有需要修改的字段')
                project.update(patch)
                if 'owner_assignments' in patch:
                    project['owner_name'] = owner_summary(patch['owner_assignments']) or None
                    project['owner_id'] = None
                elif 'owner_name' in patch and 'owner_id' not in patch:
                    project['owner_assignments'] = []
                    project['owner_id'] = None
            elif action.intent == 'add_milestone':
                require(manager(actor, project), '仅项目负责人可新增目标', 403)
                require(project['status'] not in ('completed', 'cancelled'), '已结束项目不能新增目标')
                require(len(project['milestones']) < 50, '一个项目最多 50 个目标')
                project['milestones'].append(self.new_node(validate(MilestoneCreate, action.data).model_dump(mode='json')))
            elif action.intent == 'project_status':
                require(manager(actor, project), '仅项目负责人可变更项目状态', 403)
                change = validate(StatusChange, action.data)
                self.change_state(project, change.status, at, change.reason)
            else:
                node = self.find_node(project, action.milestone_id)
                require(project['status'] not in ('completed', 'cancelled'), '项目已结束，请负责人先恢复')
                if action.intent == 'edit_milestone':
                    require(manager(actor, project), '仅项目负责人可调整目标计划', 403)
                    patch = validate(MilestonePatch, action.data).model_dump(mode='json', exclude_unset=True)
                    patch.pop('reason')
                    require(bool(patch), '没有需要修改的字段')
                    node.update(patch)
                elif action.intent == 'milestone_status':
                    change = validate(StatusChange, action.data)
                    if change.status == 'completed' and node['status'] not in ('completed', 'cancelled'):
                        require(manager(actor, project) or node['owner_id'] == actor['id'], '无权完成此节点', 403)
                    else:
                        require(manager(actor, project), '仅项目负责人可暂停、恢复或取消目标', 403)
                    self.change_state(node, change.status, at, change.reason)
                    if change.status == 'completed':
                        node['progress'] = 100
                    if change.status == 'not_started':
                        node['progress'] = 0
                elif action.intent == 'report_progress':
                    require(manager(actor, project) or node['owner_id'] == actor['id'], '无权更新此节点', 403)
                    require(project['status'] != 'paused' and node['status'] not in ('completed', 'cancelled', 'paused'),
                            '目标或项目已暂停/结束，请先恢复')
                    report = validate(ProgressReport, action.data)
                    if report.event_date:
                        require(report.event_date <= self.clock().date(), '汇报发生日期不能晚于今天')
                        require(report.event_date == self.clock().date() or report.historical,
                                '过去日期的补录必须标记为历史记录，不能刷新当前汇报时间')
                    if not report.historical:
                        updates = report.model_dump(mode='json', exclude_unset=True,
                                                    exclude={'clear_fields', 'historical', 'event_date'})
                        for key, value in updates.items():
                            require(value != '' or key == 'summary', '清除阻碍或下一步请使用明确的清除字段')
                            node[key] = value
                        for key in report.clear_fields:
                            node[key] = None if key == 'expected_date' else ''
                        node['last_report_at'] = at
                        if node['status'] == 'not_started':
                            node['status'], node['started_at'] = 'active', at
                        if project['status'] == 'not_started' and not node.get('preparation'):
                            project['status'] = 'active'
                else:
                    raise BusinessError('不支持的操作')
            project['updated_at'] = at
        self.validate_members(db, project, current)
        return project

    @staticmethod
    def change_state(obj, status, at, reason):
        previous = obj['status']
        require(status != previous, '状态没有变化')
        if status == 'not_started':
            require(previous == 'not_started', '已开始的工作不能重置为未开始，请使用恢复进行中')
        obj['status'] = status
        obj['pause_reason'] = reason if status == 'paused' else ''
        if status == 'completed':
            obj['completed_at'] = at
        elif status == 'paused':
            obj['paused_at'] = at
        elif status == 'active':
            obj['completed_at'] = None
            obj['resumed_at'] = at
            if 'started_at' in obj and not obj['started_at']:
                obj['started_at'] = at

    def replace_previous(self, db, actor, previous_draft_id):
        if not previous_draft_id:
            return
        row = db.execute('SELECT * FROM drafts WHERE id=? AND (user_id=? OR ?)',
                         (previous_draft_id, actor['id'], self.shared_actor(actor))).fetchone()
        require(row is not None, '草稿不存在或无权访问', 404)
        action = Action.model_validate_json(row['action'])
        if action.project_id:
            self.get_project(db, action.project_id, actor)
        changed = db.execute("UPDATE drafts SET status='cancelled' WHERE id=? AND (user_id=? OR ?) "
                             "AND (status='needs_input' OR (status='pending' AND expires_at>?))",
                             (previous_draft_id, actor['id'], self.shared_actor(actor), self.clock().isoformat())).rowcount
        require(changed == 1, '原草稿已结束、过期或正在被另一条补充替换，请查看最新草稿', 409)

    def create_draft(self, user, action: Action, source_text='', diagnostics=None, *, previous_draft_id=None, auto_save=False):
        at = self.clock()
        with self.store.connect(write=True) as db:
            actor = self.fresh_user(db, user)
            self.replace_previous(db, actor, previous_draft_id)
            if auto_save and action.intent == 'create_project':
                require(not any(self.store.project(row)['name'] == action.data.get('name')
                                for row in db.execute('SELECT * FROM projects')),
                        '项目名称冲突，请管理员核对项目归属与成员权限；未创建新项目')
            if action.intent == 'record_item' and not action.project_id:
                name = action.data.get('project_name', '').strip()
                matches = [p for row in db.execute('SELECT * FROM projects')
                           if (p := self.store.project(row))['name'] == name]
                require(all(allowed(actor, p) for p in matches), '无法安全关联项目，请管理员核对项目归属与成员权限；未创建新项目')
                require(len(matches) <= 1, '项目名称重复，请补充项目编号')
                if matches:
                    action = action.model_copy(update={'project_id': matches[0]['id']})
            current = (None if action.intent == 'create_project' or (action.intent == 'record_item' and not action.project_id)
                       else self.get_project(db, action.project_id, actor))
            preview = self.propose(db, actor, action, current)
            draft_id = secrets.token_hex(12)
            db.execute('INSERT INTO drafts(id,user_id,status,created_at,expires_at,expected_version,action,preview,source_text,diagnostics) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (draft_id, actor['id'], 'pending', at.isoformat(), (at+timedelta(minutes=30)).isoformat(),
                        current['version'] if current else None, action.model_dump_json(), encode(preview),
                        source_text, encode(diagnostics or {})))
            if auto_save:
                self._save(db, actor, draft_id)
        return self.draft(user, draft_id)

    def draft(self, user, draft_id):
        with self.store.connect() as db:
            actor = self.fresh_user(db,user)
            row = db.execute('SELECT * FROM drafts WHERE id=? AND (user_id=? OR ?)',
                             (draft_id, actor['id'], self.shared_actor(actor))).fetchone()
            require(row is not None, '草稿不存在或无权访问', 404)
            result = dict(row)
            for key in ('action', 'preview', 'diagnostics', 'result'):
                result[key] = json.loads(result[key]) if result[key] else None
            pid = result['action'].get('project_id') or (result['result'] or {}).get('project_id')
            if pid:
                self.get_project(db,pid,actor)
            if result['status'] == 'confirmed':
                audit = db.execute('SELECT before_data FROM audit WHERE draft_id=?', (draft_id,)).fetchone()
                result['before'] = json.loads(audit['before_data']) if audit and audit['before_data'] else None
            if result['status'] == 'pending' and result['expires_at'] <= self.clock().isoformat():
                result['status'] = 'expired'
            result['due_hour'] = self.store.settings().due_hour
            return result

    def confirm(self, user, draft_id):
        with self.store.connect(write=True) as db:
            return self._save(db, user, draft_id)

    def _save(self, db, user, draft_id):
        actor = self.fresh_user(db, user)
        row = db.execute('SELECT * FROM drafts WHERE id=? AND (user_id=? OR ?)',
                         (draft_id, actor['id'], self.shared_actor(actor))).fetchone()
        require(row is not None, '草稿不存在或无权访问', 404)
        action = Action.model_validate_json(row['action'])
        if row['status'] == 'confirmed':
            result = json.loads(row['result'])
            self.get_project(db, result['project_id'], actor)
            return result
        require(row['status'] == 'pending', '此草稿不能确认', 409)
        require(row['expires_at'] > self.clock().isoformat(), '草稿已过期，请重新预览', 409)
        current = (None if action.intent == 'create_project' or (action.intent == 'record_item' and not action.project_id)
                   else self.get_project(db, action.project_id, actor))
        require(current is None or current['version'] == row['expected_version'],
                '项目已被修改，请重新预览，避免覆盖最新数据', 409)
        project = self.propose(db, actor, action, current)
        if current:
            pid, version = int(current['id']), current['version'] + 1
            for field in ('id', 'code', 'version'):
                project.pop(field, None)
            db.execute('UPDATE projects SET version=?, data=? WHERE id=?', (version, encode_project(project), pid))
        else:
            require(action.intent != 'record_item' or not any(self.store.project(row)['name'] == project['name']
                            for row in db.execute('SELECT * FROM projects')),
                    '项目名称冲突，请管理员核对项目归属与成员权限；未创建新项目')
            version = 1
            pid = db.execute('INSERT INTO projects(version,data) VALUES(1,?)', (encode_project(project),)).lastrowid
        at = self.clock().isoformat()
        db.execute('INSERT INTO audit(project_id,user_id,at,intent,before_data,after_data,draft_id) VALUES(?,?,?,?,?,?,?)',
                   (pid, actor['id'], at, action.intent, encode(current) if current else None, encode(project), draft_id))
        if action.intent == 'report_progress':
            report_data = {**action.data, 'before_progress': self.find_node(current, action.milestone_id)['progress'],
                           'after_progress': self.find_node(project, action.milestone_id)['progress']}
            db.execute('INSERT INTO reports(project_id,milestone_id,user_id,at,data,draft_id) VALUES(?,?,?,?,?,?)',
                       (pid, action.milestone_id, actor['id'], at, encode(report_data), draft_id))
        result = {'project_id': str(pid), 'code': f'P{pid:04d}', 'version': version, 'message': '已保存'}
        db.execute("UPDATE drafts SET status='confirmed',result=? WHERE id=?", (encode(result), draft_id))
        return result

    def cancel(self, user, draft_id):
        with self.store.connect(write=True) as db:
            actor = self.fresh_user(db, user)
            count = db.execute("UPDATE drafts SET status='cancelled' WHERE id=? AND (user_id=? OR ?) AND status IN ('pending','needs_input')",
                               (draft_id, actor['id'], self.shared_actor(actor))).rowcount
            require(count == 1, '草稿不存在或已处理', 409)
        return {'message': '已取消，未修改项目'}

    def projects(self, user, display=False):
        from .reminders import decorate
        settings = self.store.settings()
        with self.store.connect() as db:
            users = {r['id']: r['name'] for r in db.execute('SELECT id,name FROM users')}
            projects = [self.store.project(r) for r in db.execute('SELECT * FROM projects ORDER BY id DESC')]
        projects = [p for p in projects if p['display_visible']] if display else [p for p in projects if allowed(user, p)]
        for p in projects:
            p['owner_assignments'] = p.get('owner_assignments', [])
            p['owner_name'] = users.get(p['owner_id']) or p.get('owner_name') or '待明确'
            for n in p['milestones']:
                n['owner_name'] = users.get(n['owner_id'], '待明确')
            decorate(p, self.clock(), settings)
        projects.sort(key=lambda p: (-p['risk_score'], p['due_date'] or '9999-12-31', p['id']))
        if display:
            project_fields = {'id','code','name','description','owner_name','owner_assignments','start_date','due_date','status','progress','pause_reason',
                              'flags','risk_score','updated_at','milestones'}
            node_fields = {'id','name','criterion','owner_name','start_date','due_date','status','progress','summary','pause_reason','time_text','preparation','recorded_at',
                           'blocker','next_step','expected_date','last_report_at','flags'}
            return [{**{k:v for k,v in p.items() if k in project_fields},
                     'milestones': [{k:v for k,v in n.items() if k in node_fields} for n in p['milestones']]} for p in projects]
        return projects
