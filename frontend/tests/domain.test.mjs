import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { changedFields, reportData, displaySlides, isSnapshotStale } from '../src/domain.ts'
import * as domain from '../src/domain.ts'

test('安排状态区分进行中、已完成和暂停类型', () => {
  assert.deepEqual(domain.arrangementPresentation({ status: 'active', paused: false }),
    { label: '进行中', tone: 'active' })
  assert.deepEqual(domain.arrangementPresentation({ status: 'completed', paused: false }),
    { label: '已完成', tone: 'completed' })
  assert.deepEqual(domain.arrangementPresentation({ status: 'active', paused: true }),
    { label: '暂停中', tone: 'paused' })
})

test('负责人展示优先采用后端结构化分工而不是旧的整句文本', () => {
  const project = {
    owner_name: '小柯为A角和主要负责人，小朱为B角',
    owner_roles: {},
    owner_assignments: [
      { name: '小柯', role: 'A角', primary: true },
      { name: '小朱', role: 'B角', primary: false },
    ],
  }
  assert.deepEqual(domain.ownerPresentation(project, []), [
    { name: '小柯', role: 'A角', primary: true },
    { name: '小朱', role: 'B角', primary: false },
  ])
})

test('项目情绪概览按状态和风险计数并给出鼓励文案', () => {
  const summary = domain.projectMoodSummary([
    { status: 'not_started', flags: [] },
    { status: 'not_started', flags: ['overdue'] },
    { status: 'active', flags: ['blocked', 'due_soon'] },
    { status: 'completed', flags: ['overdue'] },
    { status: 'cancelled', flags: ['overdue'] },
  ])
  assert.deepEqual(summary, {
    total: 5,
    cards: [
      { tone: 'preparation', label: '前期准备', count: 2, message: '正在蓄力' },
      { tone: 'active', label: '进行中', count: 1, message: '保持节奏' },
      { tone: 'completed', label: '已完成', count: 1, message: 'Congratulations！' },
      { tone: 'attention', label: '需要关注', count: 2, message: '及时处理' },
    ],
  })
})

test('项目颜色按编号稳定分配七种指定色并循环复用', () => {
  assert.equal(domain.projectColorTone('1'), 'orange')
  assert.equal(domain.projectColorTone('2'), 'lemon')
  assert.equal(domain.projectColorTone('3'), 'grass')
  assert.equal(domain.projectColorTone('4'), 'lavender')
  assert.equal(domain.projectColorTone('5'), 'brown')
  assert.equal(domain.projectColorTone('6'), 'slate')
  assert.equal(domain.projectColorTone('7'), 'charcoal')
  assert.equal(domain.projectColorTone('8'), 'orange')
  assert.equal(domain.projectColorTone('P0002'), 'lemon')
  assert.equal(domain.projectColorTone('project-alpha'), domain.projectColorTone('project-alpha'))
})

test('顶部进行中概览与日历进行中使用同一组天蓝状态色', () => {
  const page = readFileSync(new URL('../src/views/ProjectsPage.vue', import.meta.url), 'utf8')
  assert.match(page, /\.mood-card\.tone-active::before\{background:#5ca8dd\}/)
  assert.match(page, /\.mood-card\.tone-active\{background:#e9f6ff\}/)
})

test('工作台时间轴将十天内节点突出、十天外节点弱化、过期节点标为预警', () => {
  assert.equal(domain.timelineEmphasis('2026-09-06', '2026-09-07'), 'warning')
  assert.equal(domain.timelineEmphasis('2026-09-07', '2026-09-07'), 'near')
  assert.equal(domain.timelineEmphasis('2026-09-17', '2026-09-07'), 'near')
  assert.equal(domain.timelineEmphasis('2026-09-18', '2026-09-07'), 'far')
})

test('跨天调研每天展示但只计一项，窗口中间日期不遗漏，不修改原记录', () => {
  const projects = [{ id: 'p', status: 'active', milestones: [
    { id: 'survey', name: '工厂实地调研', status: 'active', start_date: '2026-09-08', due_date: '2026-09-09' },
  ] }]
  const before = JSON.stringify(projects)
  const result = domain.planningOverview(projects, '2026-09-06')
  assert.deepEqual(result.calendarDays.filter(day => day.items.length).map(day =>
    [day.date, day.items[0].id, day.items[0].action]), [
    ['2026-09-08', 'p-survey', '开始'], ['2026-09-09', 'p-survey', '截止'],
  ])
  assert.equal(result.calendarActiveCount, 1)
  assert.equal(JSON.stringify(projects), before)
  projects[0].milestones[0].start_date = '2026-08-30'
  projects[0].milestones[0].due_date = '2026-09-30'
  const spanning = domain.planningOverview(projects, '2026-09-06')
  assert.equal(spanning.calendarDays.filter(day => day.items.length === 1).length, 10)
  assert.equal(spanning.calendarDays[0].items[0].action, '安排中')
  assert.equal(spanning.calendarActiveCount, 1)
})

test('完成事项仍保留在日历，进行中计数不含完成事项，未定日期保留原话和状态', () => {
  const projects = [{ id: 'p', name: '工厂项目', status: 'active', milestones: [
    { id: 'research', name: '调研工厂', status: 'active', due_date: '2026-09-09' },
    { id: 'undated', name: '准备资料', status: 'completed', time_text: '下周' },
  ] }]
  let result = domain.planningOverview(projects, '2026-09-06')
  assert.equal(result.calendarDays[3].items[0].status, 'active')
  projects[0].milestones[0].status = 'completed'
  result = domain.planningOverview(projects, '2026-09-06')
  assert.equal(result.calendarDays[3].items[0].status, 'completed')
  assert.equal(result.calendarDays[3].items[0].id, 'p-research')
  assert.equal(result.upcoming.length, 0)
  assert.equal(result.undated[0].timeText, '下周')
  assert.equal(result.undated[0].status, 'completed')
  projects[0].status = 'completed'
  assert.equal(domain.planningOverview(projects, '2026-09-06').calendarDays[3].items.length, 1)
})

test('计划概览按节点日期分组，交付统计按项目去重，不包含已结束项目和节点', () => {
  assert.equal(typeof domain.planningOverview, 'function')
  const project = (id, due_date, milestones, status = 'active') => ({ id, code: `P${id}`, name: id, status, due_date, milestones })
  const node = (id, due_date, status = 'not_started') => ({ id, name: id, due_date, status })
  const result = domain.planningOverview([
    project('1', '2026-09-11', [node('今天1', '2026-09-04'), node('今天2', '2026-09-04'), node('下周', '2026-09-11')]),
    project('2', '2026-09-12', [node('远期', '2026-09-12'), node('旧节点', '2026-09-03')]),
    project('3', '', []),
    project('4', '2026-09-04', [node('完成节点', '2026-09-04')], 'completed'),
    project('5', '2026-09-04', [node('取消节点', '2026-09-04')], 'cancelled'),
  ], '2026-09-04')
  assert.deepEqual(result.counts, { today: 2, week: 1, flex: 1, total: 3 })
  assert.deepEqual(result.today.map(x => x.target), ['今天1', '今天2'])
  assert.deepEqual(result.week.map(x => x.target), ['下周'])
  assert.equal(result.overdue[0].target, '旧节点')
  assert.equal(result.later[0].target, '远期')
  assert.equal(result.flex[0].projectId, '3')
})

test('十天事项包含今天与第十天，跨月排序，排除暂停和结束事项，同一节点不重复', () => {
  assert.equal(typeof domain.planningOverview, 'function')
  const node = (id, start_date, due_date, status = 'active') => ({ id, name: id, start_date, due_date, status })
  const milestones = [
    node('第十天', '2026-09-01', '2026-10-03'),
    node('今天', '2026-09-24', '2026-09-24'),
    node('将开始', '2026-09-25', '2026-10-10'),
    node('第十一天', '2026-09-01', '2026-10-04'),
    node('逾期', '2026-09-01', '2026-09-23'),
    node('暂停', '2026-09-24', '2026-09-25', 'paused'),
    node('完成', '2026-09-24', '2026-09-25', 'completed'),
    node('取消', '2026-09-24', '2026-09-25', 'cancelled'),
  ]
  const projects = [{ id: 'p1', status: 'active', milestones },
    { id: 'p2', status: 'paused', milestones: [node('暂停项目', '2026-09-24', '2026-09-25')] }]
  const before = JSON.stringify(projects)
  const result = domain.planningOverview(projects, '2026-09-24')
  assert.equal(result.endDate, '2026-10-03')
  assert.deepEqual(result.upcoming.map(x => [x.target, x.actionDate, x.action]), [
    ['今天', '2026-09-24', '截止'], ['将开始', '2026-09-25', '开始'], ['第十天', '2026-10-03', '截止'],
  ])
  assert.equal(JSON.stringify(projects), before)
  assert.deepEqual(domain.planningOverview([], '2026-09-24').groups, [])
})

test('时间轴仅按十天内的行动日期分组，同一天合并，跨年后窗口滚动', () => {
  const projects = [{ id: 'p', status: 'active', milestones: [
    { id: 'a', name: '启动', status: 'active', start_date: '2026-12-31', due_date: '2027-02-01' },
    { id: 'b', name: '交付', status: 'active', due_date: '2026-12-31' },
    { id: 'c', name: '边界', status: 'active', due_date: '2027-01-09' },
    { id: 'd', name: '超范围', status: 'active', due_date: '2027-01-10' },
  ] }]
  const result = domain.planningOverview(projects, '2026-12-31')
  assert.deepEqual(result.upcomingGroups?.map(group => [group.date, group.items.map(item => item.target)]),
    [['2026-12-31', ['交付', '启动']], ['2027-01-09', ['边界']]])
  assert.deepEqual(domain.planningOverview(projects, '2027-01-01').upcomingGroups?.map(group => group.date),
    ['2027-01-09', '2027-01-10'])
  assert.deepEqual(domain.planningOverview([], '2026-12-31').upcomingGroups, [])
})

test('日历固定显示连续十天并保留空日期，时间轴保留十天以外的节点', () => {
  const result = domain.planningOverview([{ id: 'p', status: 'active', milestones: [
    { id: 'a', name: '跨年交付', status: 'active', due_date: '2027-01-01' },
    { id: 'b', name: '远期交付', status: 'active', due_date: '2027-02-01' },
  ] }], '2026-12-31')
  assert.equal(result.calendarDays?.length, 10)
  assert.equal(result.calendarDays[0].date, '2026-12-31')
  assert.deepEqual(result.calendarDays[0].items, [])
  assert.equal(result.calendarDays[1].items[0].target, '跨年交付')
  assert.equal(result.calendarDays[9].date, '2027-01-09')
  assert.deepEqual(result.groups.map(group => group.date), ['2027-01-01', '2027-02-01'])
  assert.equal(domain.planningOverview([], '2026-12-31').calendarDays.length, 10)
})

test('本周视图按周一至周日同时限制日历和时间轴范围', () => {
  const result = domain.planningOverview([{ id: 'p', status: 'active', milestones: [
    { id: 'monday', name: '周一事项', status: 'active', due_date: '2026-09-07' },
    { id: 'sunday', name: '周日事项', status: 'active', due_date: '2026-09-13' },
    { id: 'next', name: '下周事项', status: 'active', due_date: '2026-09-14' },
  ] }], '2026-09-09', 'week')
  assert.equal(result.rangeStart, '2026-09-07')
  assert.equal(result.endDate, '2026-09-13')
  assert.equal(result.calendarDays.length, 7)
  assert.deepEqual(result.groups.map(group => group.date), ['2026-09-07', '2026-09-13'])
})

test('本月视图展示当月全部日期并同时限制时间轴范围', () => {
  const result = domain.planningOverview([{ id: 'p', status: 'active', milestones: [
    { id: 'first', name: '月初事项', status: 'active', due_date: '2028-02-01' },
    { id: 'last', name: '月底事项', status: 'active', due_date: '2028-02-29' },
    { id: 'next', name: '下月事项', status: 'active', due_date: '2028-03-01' },
  ] }], '2028-02-12', 'month')
  assert.equal(result.rangeStart, '2028-02-01')
  assert.equal(result.endDate, '2028-02-29')
  assert.equal(result.calendarDays.length, 29)
  assert.equal(result.calendarDays[0].column, 2)
  assert.deepEqual(result.groups.map(group => group.date), ['2028-02-01', '2028-02-29'])
})

test('日历事项携带项目结构化A角B角负责人', () => {
  const result = domain.planningOverview([{ id: 'p', status: 'active', owner_name: '旧负责人文本',
    owner_assignments: [
      { name: '小柯', role: 'A角', primary: true },
      { name: '小朱', role: 'B角', primary: false },
    ],
    milestones: [{ id: 'item', name: '现场调研', status: 'active', due_date: '2026-09-09' }],
  }], '2026-09-09', 'week')
  assert.deepEqual(result.calendarDays[2].items[0].owners, [
    { name: '小柯', role: 'A角', primary: true },
    { name: '小朱', role: 'B角', primary: false },
  ])
})

test('大屏月历按整周事项密度压缩空行并放大有事项行', () => {
  assert.equal(typeof domain.calendarRowTemplate, 'function')
  const days = Array.from({ length: 30 }, (_, index) => ({
    date: `2026-09-${String(index + 1).padStart(2, '0')}`,
    column: index === 0 ? 2 : 1,
    items: [],
  }))
  days[7].items = [{}]
  days[14].items = [{}, {}]
  assert.equal(domain.calendarRowTemplate(days), '.55fr 1.2fr 1.5fr .55fr .55fr')
})

test('编辑只提交发生变化的字段，保留 false 和空描述的明确修改', () => {
  assert.deepEqual(changedFields({ name: '项目', description: '旧', display_visible: true },
    { name: '项目', description: '', display_visible: false }, ['name', 'description', 'display_visible']),
    { description: '', display_visible: false })
})

test('未启用进度更新或未填写可选内容，不覆盖当前字段', () => {
  assert.deepEqual(reportData({ summary: '等待供应商', progress_enabled: false, progress: 0,
    blocker: '', next_step: '', expected_date: '', clear_fields: [], historical: false, event_date: '' }),
    { summary: '等待供应商', historical: false })
})

test('明确清空与赋值不同时提交；历史补录保留事件日期', () => {
  assert.deepEqual(reportData({ summary: '已核对', progress_enabled: true, progress: 0,
    blocker: '旧阻碍', next_step: '复核', expected_date: '2026-09-10',
    clear_fields: ['blocker', 'expected_date'], historical: true, event_date: '2026-09-01' }),
    { summary: '已核对', historical: true, progress: 0, next_step: '复核',
      clear_fields: ['blocker', 'expected_date'], event_date: '2026-09-01' })
})

test('大屏按后端风险顺序遍历项目全部节点分页，不遗漏长项目', () => {
  const projects = [{ id: '1', milestones: Array.from({ length: 7 }, (_, i) => ({ id: String(i) })) },
    { id: '2', milestones: [] }]
  assert.deepEqual(displaySlides(projects, 4), [
    { projectId: '1', nodePage: 0 }, { projectId: '1', nodePage: 1 }, { projectId: '2', nodePage: 0 },
  ])
})

test('最后成功快照超过或达到60秒标记过期，首次没有快照同样过期', () => {
  assert.equal(isSnapshotStale(null, 1000), true)
  assert.equal(isSnapshotStale(1000, 60999), false)
  assert.equal(isSnapshotStale(1000, 61000), true)
})

test('清除预览展开实际字段和原始内容，不把清除列表当成业务字段', () => {
  assert.equal(typeof domain.previewRows, 'function')
  assert.deepEqual(domain.previewRows({ summary: '已解决', clear_fields: ['blocker', 'expected_date'] },
    { summary: '等待', blocker: '审批尚未完成', expected_date: '2026-09-20' }), [
    { key: 'summary', value: '已解决', before: '等待', cleared: false },
    { key: 'blocker', value: null, before: '审批尚未完成', cleared: true },
    { key: 'expected_date', value: null, before: '2026-09-20', cleared: true },
  ])
})
