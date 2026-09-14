"""LLM 可见的业务工具契约；工具参数最终仍转换为现有 Action。"""
import json

from pydantic import ValidationError

from .models import (Action, MilestoneCreate, MilestonePatch, ProgressReport,
                     ProjectCreate, ProjectPatch, RecordItem, StatusChange, MeetingCreate)
from .service import BusinessError, require

MAX_TOOL_ROUNDS = 8

TOOL_INTENTS = {
    'record_project_item': ('record_item', RecordItem, '按项目名称、负责人、书面化单句事项和截止时间记录一个项目的一项或多项安排'),
    'create_project': ('create_project', ProjectCreate, '创建没有具体事项的项目'),
    'edit_project': ('edit_project', ProjectPatch, '修改已有项目资料或负责人'),
    'add_milestone': ('add_milestone', MilestoneCreate, '为已有项目新增目标节点'),
    'edit_milestone': ('edit_milestone', MilestonePatch, '修改已有目标节点'),
    'report_progress': ('report_progress', ProgressReport, '汇报已有目标节点的进度'),
    'change_project_status': ('project_status', StatusChange, '修改已有项目状态'),
    'change_milestone_status': ('milestone_status', StatusChange, '修改已有目标节点状态'),
    'create_meeting': ('create_meeting', MeetingCreate, '记录会议；仅开始时间必填，项目、标题、参会人、地点和备注均可选'),
}


def _no_null(value):
    if isinstance(value, dict):
        return all(_no_null(item) for item in value.values())
    if isinstance(value, list):
        return all(_no_null(item) for item in value)
    return value is not None


def data_schema(model):
    """生成适合模型工具参数的 JSON schema，并移除可空分支。"""
    def clean(value):
        if isinstance(value, list):
            return [clean(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: clean(item) for key, item in value.items()
                  if not (key == 'default' and item is None)}
        if 'anyOf' in result:
            result['anyOf'] = [item for item in result['anyOf']
                               if item.get('type') != 'null']
        return result
    return clean(model.model_json_schema())


def business_tool_schemas():
    tools = []
    for name, (_, model, description) in TOOL_INTENTS.items():
        tools.append({'type': 'function', 'function': {
            'name': name,
            'description': description,
            'parameters': {
                'type': 'object',
                'additionalProperties': False,
                'properties': {
                    'project_id': {'type': 'string', 'minLength': 1, 'maxLength': 100},
                    'milestone_id': {'type': 'string', 'minLength': 1, 'maxLength': 100},
                    'data': data_schema(model),
                },
                'required': ['data'],
            },
        }})
    tools.append({'type': 'function', 'function': {
        'name': 'query_projects',
        'description': '查询当前消息可见的候选项目，不写入数据',
        'parameters': {
            'type': 'object',
            'additionalProperties': False,
            'properties': {
                'project_id': {'type': 'string', 'minLength': 1, 'maxLength': 100},
            },
        },
    }})
    return tools


def action_from_tool_call(name, arguments, allowed_project_ids,
                          allowed_milestone_ids=None):
    require(name in TOOL_INTENTS, '模型调用了未知业务工具')
    require(isinstance(arguments, dict) and _no_null(arguments),
            '模型工具参数不能包含 null')
    project_id = arguments.get('project_id')
    milestone_id = arguments.get('milestone_id')
    if project_id:
        require(project_id in allowed_project_ids, '模型引用了不可用的项目')
    if milestone_id and allowed_milestone_ids is not None:
        require(milestone_id in allowed_milestone_ids, '模型引用了不可用的目标节点')
    intent, model, _ = TOOL_INTENTS[name]
    try:
        data = model.model_validate_json(json.dumps(arguments.get('data', {})), strict=True)
    except ValidationError as exc:
        raise BusinessError('模型工具参数不符合业务字段格式') from exc
    return Action(intent=intent, project_id=project_id, milestone_id=milestone_id,
                  data=data.model_dump(mode='json', exclude_unset=True))
