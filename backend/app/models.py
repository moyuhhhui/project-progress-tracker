"""固定业务契约；未知字段拒绝，更新仅采用显式提供的字段。"""
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Name = Annotated[str, Field(min_length=1, max_length=100)]
Text = Annotated[str, Field(max_length=2000)]
ID = Annotated[str, Field(min_length=1, max_length=100)]
Percent = Annotated[int, Field(strict=True, ge=0, le=100)]
State = Literal['not_started', 'active', 'paused', 'completed', 'cancelled']
OwnerRole = Literal['A角', 'B角', 'A1', 'A2', '主责', '搭档']


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class OwnerAssignment(Contract):
    name: Name
    role: Annotated[str, Field(min_length=1, max_length=30)]
    primary: bool = False

    @field_validator('role')
    @classmethod
    def uppercase_role(cls, value):
        value = value.upper()
        return {'A': 'A角', 'B': 'B角'}.get(value, value)


class MilestoneCreate(Contract):
    name: Name
    criterion: Text = ''
    owner_id: ID | None = None
    start_date: date | None = None
    due_date: date | None = None
    update_interval: Annotated[int, Field(strict=True, ge=1, le=30)] = 2

    @model_validator(mode='after')
    def dates(self):
        if self.due_date and self.start_date and self.due_date < self.start_date:
            raise ValueError('截止日期不能早于开始日期')
        return self


class ProjectCreate(Contract):
    name: Name
    description: Text = ''
    contact_company: Annotated[str, Field(max_length=100)] = ''
    contact_name: Annotated[str, Field(max_length=100)] = ''
    contact_info: Annotated[str, Field(max_length=200)] = ''
    owner_id: ID | None = None
    owner_name: Name | None = None
    status: Literal['not_started', 'active'] = 'active'
    member_ids: Annotated[list[ID], Field(max_length=200)] = []
    owner_roles: dict[ID, OwnerRole] = Field(default_factory=dict)
    owner_assignments: Annotated[list[OwnerAssignment], Field(max_length=50)] = Field(default_factory=list)
    start_date: date | None = None
    due_date: date | None = None
    display_visible: bool = True
    milestones: Annotated[list[MilestoneCreate], Field(max_length=50)] = Field(default_factory=list)

    @model_validator(mode='after')
    def dates(self):
        if self.due_date and self.start_date and self.due_date < self.start_date:
            raise ValueError('截止日期不能早于开始日期')
        for node in self.milestones:
            if (node.start_date and self.start_date and node.start_date < self.start_date) or (node.due_date and self.due_date and node.due_date > self.due_date):
                raise ValueError('目标节点日期必须在项目计划范围内')
        return self


class ProjectPatch(Contract):
    name: Name | None = None
    owner_name: Name | None = None
    description: Text | None = None
    contact_company: Annotated[str, Field(max_length=100)] | None = None
    contact_name: Annotated[str, Field(max_length=100)] | None = None
    contact_info: Annotated[str, Field(max_length=200)] | None = None
    owner_id: ID | None = None
    member_ids: Annotated[list[ID], Field(max_length=200)] | None = None
    owner_roles: dict[ID, OwnerRole] | None = None
    owner_assignments: Annotated[list[OwnerAssignment], Field(max_length=50)] | None = None
    start_date: date | None = None
    due_date: date | None = None
    display_visible: bool | None = None
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


class MilestonePatch(Contract):
    name: Name | None = None
    criterion: Annotated[str, Field(min_length=1, max_length=2000)] | None = None
    owner_id: ID | None = None
    start_date: date | None = None
    due_date: date | None = None
    update_interval: Annotated[int, Field(strict=True, ge=1, le=30)] | None = None
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


class ProgressReport(Contract):
    summary: Annotated[str, Field(min_length=1, max_length=2000)]
    progress: Percent | None = None
    blocker: Text | None = None
    next_step: Text | None = None
    expected_date: date | None = None
    clear_fields: list[Literal['blocker', 'next_step', 'expected_date']] = []
    historical: bool = False
    event_date: date | None = None

    @model_validator(mode='after')
    def historical_date(self):
        if self.historical and not self.event_date:
            raise ValueError('历史补录必须填写发生日期')
        for key in self.clear_fields:
            if key in self.model_fields_set:
                raise ValueError('同一字段不能同时设置和清空')
        return self


class StatusChange(Contract):
    status: State
    reason: Annotated[str, Field(min_length=1, max_length=1000)]


class RecordedTask(Contract):
    text: Annotated[str, Field(min_length=1, max_length=2000)]
    title: Name | None = None
    time_text: Annotated[str, Field(max_length=100)] = ''
    start_date: date | None = None
    due_date: date | None = None
    owner_id: ID | None = None

    @model_validator(mode='after')
    def dates(self):
        if self.start_date and self.due_date and self.start_date > self.due_date:
            raise ValueError('截止日期不能早于开始日期')
        return self


class RecordItem(RecordedTask):
    project_name: Name | None = None
    owner_name: Name | None = None
    owner_assignments: Annotated[list[OwnerAssignment], Field(max_length=50)] = Field(default_factory=list)
    items: Annotated[list[RecordedTask], Field(min_length=2, max_length=50)] | None = None

    @model_validator(mode='after')
    def separate_dates(self):
        if self.items and any(key in self.model_fields_set for key in ('title', 'time_text', 'start_date', 'due_date', 'owner_id')):
            raise ValueError('多事项的标题、时间和负责人必须分别放在 items 内')
        return self


class MeetingCreate(Contract):
    start_at: datetime
    title: Name | None = None
    project_id: ID | None = None
    attendee_ids: Annotated[list[ID], Field(max_length=200)] = []
    location: Annotated[str, Field(max_length=200)] | None = None
    notes: Text | None = None


Intent = Literal['record_item', 'create_project', 'edit_project', 'add_milestone', 'edit_milestone',
                 'report_progress', 'project_status', 'milestone_status', 'create_meeting', 'query', 'ignore']


class Action(Contract):
    intent: Intent
    project_id: ID | None = None
    milestone_id: ID | None = None
    data: dict = Field(default_factory=dict)


class ParsedMessage(Action):
    schema_version: Literal['1'] = '1'
    missing_fields: Annotated[list[str], Field(max_length=30)] = []
    ambiguities: Annotated[list[str], Field(max_length=30)] = []
    evidence: dict[str, str] = Field(default_factory=dict)


class MessageInput(Contract):
    text: Annotated[str, Field(min_length=1, max_length=6000)]
    client_message_id: Annotated[str, Field(min_length=8, max_length=100)]
    previous_draft_id: ID | None = None


class UserCreate(Contract):
    name: Name
    role: Literal['admin', 'member', 'display'] = 'member'
    wecom_user_id: Annotated[str, Field(max_length=100, pattern=r'^[\w.@-]*$')] = ''


class UserPatch(Contract):
    name: Name | None = None
    active: bool | None = None
    wecom_user_id: Annotated[str, Field(max_length=100, pattern=r'^[\w.@-]*$')] | None = None


class ReminderSettings(Contract):
    workday_overrides: dict[str, bool] = Field(default_factory=dict)
    start_hour: Annotated[int, Field(strict=True, ge=0, le=22)] = 9
    end_hour: Annotated[int, Field(strict=True, ge=1, le=23)] = 18
    due_hour: Annotated[int, Field(strict=True, ge=0, le=23)] = 18
    reminder_hour: Annotated[int, Field(strict=True, ge=0, le=23)] = 12

    @model_validator(mode='after')
    def valid(self):
        if self.end_hour <= self.start_hour:
            raise ValueError('发送结束时间必须晚于开始时间')
        if len(self.workday_overrides) > 2000:
            raise ValueError('日历覆盖项过多')
        for key in self.workday_overrides:
            if date.fromisoformat(key).isoformat() != key:
                raise ValueError('日历日期须为 YYYY-MM-DD')
        return self
