import type { State, Flag, Intent, OwnerAssignment, Project, User, Meeting } from './types'

export const stateLabels: Record<State, string> = { not_started: '正常进行', active: '正常进行', paused: '暂停中', completed: '完成 · congratulation！', cancelled: '已取消' }
export const editableStates: State[] = ['active', 'paused', 'completed']
export const flagLabels: Record<Flag, string> = { overdue: '计划逾期', stale: '待更新', due_soon: '临期', blocked: '有阻碍' }
export const intentLabels: Record<Intent, string> = {
  record_item: '记录项目事项',
  create_project: '创建项目', edit_project: '调整项目信息', add_milestone: '新增项目事项', edit_milestone: '调整项目事项',
  report_progress: '汇报进度', project_status: '变更项目状态', milestone_status: '变更目标状态', create_meeting: '会议', query: '查询项目', ignore: '无操作',
}
export const fieldLabels: Record<string, string> = {
  owner_name: '负责人姓名',
  text: '事项原文', title: '事项名称', time_text: '时间原文', items: '分项安排', project_name: '所属项目',
  name: '名称', description: '说明', owner_id: '负责人', member_ids: '项目成员', owner_roles: '责任分工', owner_assignments: '负责人分工', start_date: '计划开始',
  contact_company: '对接单位', contact_name: '对接人', contact_info: '联系方式',
  due_date: '计划截止', display_visible: '大屏可见', milestones: '项目事项', criterion: '完成标准',
  update_interval: '汇报间隔（工作日）', reason: '变更原因', status: '状态', summary: '进展说明', progress: '进度（%）',
  blocker: '当前阻碍', next_step: '下一步', expected_date: '预计完成', clear_fields: '明确清除',
  historical: '历史补录', event_date: '发生日期', before_progress: '原进度（%）', after_progress: '保存后进度（%）',
}

export function changedFields(before: Record<string, unknown>, after: Record<string, unknown>, keys: string[]) {
  return Object.fromEntries(keys.filter(key => JSON.stringify(before[key]) !== JSON.stringify(after[key]))
    .map(key => [key, after[key]]))
}
export function previewRows(data: Record<string, unknown>, before: Record<string, unknown> = {}) {
  const rows = Object.entries(data).filter(([key]) => !['milestones', 'clear_fields'].includes(key))
    .map(([key, value]) => ({ key, value, before: before[key], cleared: false }))
  if (Array.isArray(data.clear_fields)) {
    for (const key of data.clear_fields) {
      if (typeof key === 'string') rows.push({ key, value: null, before: before[key], cleared: true })
    }
  }
  return rows
}
export interface ReportForm {
  summary: string; progress_enabled: boolean; progress: number; blocker: string; next_step: string
  expected_date: string; clear_fields: string[]; historical: boolean; event_date: string
}
export function reportData(form: ReportForm): Record<string, unknown> {
  const data: Record<string, unknown> = { summary: form.summary.trim(), historical: form.historical }
  if (form.progress_enabled) data.progress = form.progress
  for (const key of ['blocker', 'next_step', 'expected_date'] as const) {
    if (!form.clear_fields.includes(key) && form[key]?.trim()) data[key] = form[key].trim()
  }
  if (form.clear_fields.length) data.clear_fields = [...form.clear_fields]
  if (form.event_date) data.event_date = form.event_date
  return data
}
export function displaySlides(projects: { id: string; milestones: unknown[] }[], pageSize = 4) {
  return projects.flatMap(project => Array.from({ length: Math.max(1, Math.ceil(project.milestones.length / pageSize)) },
    (_, nodePage) => ({ projectId: project.id, nodePage })))
}
export function isSnapshotStale(lastSuccess: number | null, now: number) {
  return lastSuccess === null || now - lastSuccess >= 60_000
}
export function dateTime(value?: string | null) {
  if (!value) return '暂无记录'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
}
export function today() {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
}
export function arrangementPresentation(item: { status: string; paused: boolean }) {
  if (item.status === 'completed') return { label: '已完成', tone: 'completed' as const }
  if (item.paused) return { label: '暂停中', tone: 'paused' as const }
  return { label: '进行中', tone: 'active' as const }
}
export function timelineStatusLabel(item: { status: string; paused: boolean; flags?: Flag[] }) {
  const label = arrangementPresentation(item).label
  return label === '进行中' && item.flags?.includes('overdue') ? '逾期' : label
}
export function ownerPresentation(project: Pick<Project, 'owner_assignments' | 'owner_roles' | 'owner_name'>,
  users: User[]): OwnerAssignment[] {
  const assignments = project.owner_assignments?.length
    ? project.owner_assignments.map(item => ({ ...item }))
    : Object.keys(project.owner_roles || {}).length
      ? Object.entries(project.owner_roles || {}).map(([id, role]) => ({
          name: users.find(user => user.id === id)?.name || '成员不可用', role, primary: false,
        }))
      : project.owner_name
        ? project.owner_name.split(/[、,，]/).map(part => {
            const match = part.trim().match(/^(.+?)[（(](A角|B角|A1|A2|B1|B2|主责|搭档)[）)]$/)
            return match ? { name: match[1]!.trim(), role: match[2]!, primary: /^A/.test(match[2]!) } : { name: part.trim(), role: '', primary: false }
          }).filter(item => item.name)
        : [{ name: '待明确', role: '', primary: false }]
  const order = ['A角', 'A1', 'A2', 'B角', 'B1', 'B2']
  return assignments.sort((left, right) => {
    const leftIndex = order.indexOf(ownerRoleLabel(left.role))
    const rightIndex = order.indexOf(ownerRoleLabel(right.role))
    return (leftIndex < 0 ? order.length : leftIndex) - (rightIndex < 0 ? order.length : rightIndex)
  })
}
export function ownerRoleLabel(role: string, primary = false) {
  const value = role.toUpperCase()
  if (value === 'A') return 'A角'
  if (value === 'B') return 'B角'
  return value || (primary ? 'A角' : '负责人')
}
export function ownerRoleMark(role: string, primary = false) {
  const value = ownerRoleLabel(role, primary)
  return value === 'A角' ? 'A' : value === 'B角' ? 'B' : value
}
export function ownerRoleTone(role: string) {
  const value = ownerRoleLabel(role)
  if (/^A(?:角|1|2)$/.test(value)) return 'primary'
  if (/^B(?:角|1|2)$/.test(value)) return 'secondary'
  return 'neutral'
}
export function projectMoodSummary(projects: Pick<Project, 'status' | 'flags'>[]) {
  const attention = projects.filter(project => !['completed', 'cancelled'].includes(project.status) && project.flags?.length).length
  return {
    total: projects.length,
    cards: [
      { tone: 'preparation', label: '前期准备', count: projects.filter(project => project.status === 'not_started').length, message: '正在蓄力' },
      { tone: 'active', label: '进行中', count: projects.filter(project => project.status === 'active').length, message: '保持节奏' },
      { tone: 'completed', label: '已完成', count: projects.filter(project => project.status === 'completed').length, message: 'Congratulations！' },
      { tone: 'attention', label: '需要关注', count: attention, message: attention ? '及时处理' : '一切顺利' },
    ],
  }
}
export type ProjectColorTone = 'orange' | 'lemon' | 'grass' | 'lavender' | 'brown' | 'slate' | 'charcoal'
export function projectColorTone(projectId: string): ProjectColorTone {
  const tones: ProjectColorTone[] = ['orange', 'lemon', 'grass', 'lavender', 'brown', 'slate', 'charcoal']
  const numeric = Number(projectId.match(/\d+$/)?.[0])
  const seed = Number.isFinite(numeric) && numeric > 0
    ? numeric - 1
    : [...projectId].reduce((sum, character) => sum + character.codePointAt(0)!, 0)
  return tones[seed % tones.length]!
}
export type TimelineEmphasis = 'near' | 'far'
export function timelineEmphasis(date: string, currentDate = today()): TimelineEmphasis {
  const nearEnd = new Date(Date.parse(`${currentDate}T00:00:00Z`) + 10 * 86_400_000).toISOString().slice(0, 10)
  return date <= nearEnd ? 'near' : 'far'
}
export type PlanningRange = 'week' | 'month'
export function calendarRowTemplate(days: { column: number; items: unknown[] }[]) {
  if (!days.length) return '1fr'
  const leading = Math.max(0, (days[0]?.column || 1) - 1)
  const rowCount = Math.ceil((leading + days.length) / 7)
  const densities = Array.from({ length: rowCount }, () => 0)
  days.forEach((day, index) => {
    const row = Math.floor((leading + index) / 7)
    densities[row] = Math.max(densities[row]!, day.items.length)
  })
  return densities.map(count => {
    if (count === 0) return '.55fr'
    if (count === 1) return '1.2fr'
    if (count === 2) return '1.5fr'
    return `${Math.min(2.1, 1.5 + (count - 2) * .25)}fr`
  }).join(' ')
}
export function planningOverview(projects: Project[], date = today(), range?: PlanningRange, meetings: Meeting[] = [], users: User[] = []) {
  const offset = (days: number) => new Date(Date.parse(`${date}T00:00:00Z`) + days * 86_400_000).toISOString().slice(0, 10)
  const reference = new Date(`${date}T00:00:00Z`)
  const mondayOffset = -((reference.getUTCDay() + 6) % 7)
  const workWeekStart = offset(mondayOffset)
  const workWeekEnd = offset(mondayOffset + 3)
  const rangeStart = range === 'week'
    ? workWeekStart
    : range === 'month' ? `${date.slice(0, 7)}-01` : date
  const rangeStartDate = new Date(`${rangeStart}T00:00:00Z`)
  const endDate = range === 'week'
    ? workWeekEnd
    : range === 'month'
      ? new Date(Date.UTC(reference.getUTCFullYear(), reference.getUTCMonth() + 1, 0)).toISOString().slice(0, 10)
      : offset(9)
  const dayCount = Math.round((Date.parse(`${endDate}T00:00:00Z`) - rangeStartDate.getTime()) / 86_400_000) + 1
  const weekEnd = workWeekEnd
  const active = projects.filter(p => !['completed', 'cancelled'].includes(p.status))
  const allItems = projects.filter(p => p.status !== 'cancelled').flatMap(project => (project.milestones.length
    ? project.milestones.filter(n => n.status !== 'cancelled')
    : [null]).map(node => ({
      id: `${project.id}-${node?.id || 'project'}`, projectId: project.id, code: project.code,
      projectName: project.name, project, target: node?.name || '项目交付',
      date: (node ? node.due_date : project.due_date) || '',
      startDate: (node ? node.start_date : project.start_date) || '',
      timeText: node?.time_text || '',
      status: node?.status || project.status, paused: project.status === 'paused' || node?.status === 'paused',
      flags: node ? node.flags || [] : project.flags || [],
      reason: node?.pause_reason || project.pause_reason,
      owner: node?.owner_name || project.owner_name || '未分配', nextStep: node?.next_step || '',
      owners: ownerPresentation(project, users), meeting: false,
    }))).concat(meetings.filter(meeting => meeting.status === 'active').map(meeting => {
      const start = new Date(meeting.start_at)
      const day = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(start)
      const project = { id: String(meeting.project_id || meeting.id), code: meeting.project_id ? `P${String(meeting.project_id).padStart(4, '0')}` : '会议', name: meeting.project_id ? '项目会议' : '独立会议', status: 'active', milestones: [] } as unknown as Project
      return { id: meeting.id, projectId: project.id, code: project.code, projectName: project.name, project,
        target: meeting.title || '未命名会议', date: day, startDate: day, timeText: start.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false }),
        status: 'active', paused: false, flags: [], reason: '', owner: '', nextStep: '', owners: [] as OwnerAssignment[], meeting: true }
    })).sort((a, b) => a.date.localeCompare(b.date))
  const items = allItems.filter(item => item.project.status !== 'completed' && item.status !== 'completed')
  const todayItems = items.filter(item => item.date === date)
  const week = items.filter(item => item.date > date && item.date <= weekEnd)
  const flex = items.filter(item => !item.date)
  const groups = Array.from(new Set(allItems.filter(item => item.date && (!range || (item.date >= rangeStart && item.date <= endDate))).map(item => item.date)))
    .map(day => ({ date: day, items: allItems.filter(item => item.date === day) }))
  const inRange = (day: string) => day >= rangeStart && day <= endDate
  const upcoming = items.filter(item => !item.paused && (inRange(item.date) || inRange(item.startDate)))
    .map(item => ({ ...item, actionDate: inRange(item.date) ? item.date : item.startDate,
      action: inRange(item.date) ? '截止' : '开始' }))
    .sort((a, b) => a.actionDate.localeCompare(b.actionDate))
  const upcomingGroups = Array.from(new Set(upcoming.map(item => item.actionDate)))
    .map(day => ({ date: day, items: upcoming.filter(item => item.actionDate === day) }))
  const calendarItems = allItems.filter(item => item.status === 'completed' ||
    (item.project.status !== 'completed' && !item.paused))
  const calendarDays = Array.from({ length: dayCount }, (_, index) => {
    const day = new Date(rangeStartDate.getTime() + index * 86_400_000).toISOString().slice(0, 10)
    return { date: day, column: ((new Date(`${day}T00:00:00Z`).getUTCDay() + 6) % 7) + 1, items: calendarItems.filter(item => {
      const start = item.startDate || item.date, end = item.date || item.startDate
      return start && end && start <= day && day <= end
    }).map(item => ({ ...item, actionDate: day,
      action: day === item.date ? '截止' : day === item.startDate ? '开始' : '安排中' })) }
  })
  const calendarActiveCount = new Set(calendarDays.flatMap(day => day.items)
    .filter(item => item.status !== 'completed').map(item => item.id)).size
  return { groups, upcoming, upcomingGroups, calendarDays, calendarActiveCount, rangeStart, endDate, today: todayItems, week, flex,
    undated: allItems.filter(item => !item.date && !item.startDate),
    overdue: items.filter(item => item.date && item.date < date),
    later: items.filter(item => item.date > weekEnd),
    counts: { today: todayItems.length, week: new Set(week.map(item => item.projectId)).size,
      flex: new Set(flex.map(item => item.projectId)).size, total: active.length },
  }
}
export function errorText(error: unknown) { return error instanceof Error ? error.message : '请求失败，请稍后重试' }
