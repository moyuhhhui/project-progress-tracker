<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { Actor, Intent, Milestone, Project, User, Meeting } from '../types'
import { arrangementPresentation, editableStates, ownerRoleLabel, ownerRoleTone, planningOverview, projectColorTone, projectMoodSummary, stateLabels, timelineEmphasis, timelineStatusLabel, today } from '../domain'
import StateBadge from '../components/StateBadge.vue'
import OwnerRoles from '../components/OwnerRoles.vue'
import ActionEditor from '../components/ActionEditor.vue'
import ProjectDetail from '../components/ProjectDetail.vue'

const props = defineProps<{ actor: Actor; projects: Project[]; users: User[]; meetings: Meeting[]; loading: boolean }>()
const search = ref(''), stateFilter = ref(''), riskOnly = ref(false)
const page = ref(1), selectedId = ref('')
const currentDate = ref(today())
const overview = computed(() => planningOverview(props.projects, currentDate.value, undefined, props.meetings, props.users))
const timelineRange = ref<'week' | 'month'>('week')
const timelineOverview = computed(() => planningOverview(props.projects, currentDate.value, timelineRange.value, props.meetings, props.users))
const mood = computed(() => projectMoodSummary(props.projects))
const calendarStart = ref(''), calendarRange = ref<'week' | 'month'>('week')
const calendarOverview = computed(() => planningOverview(props.projects, calendarStart.value || currentDate.value, calendarRange.value, props.meetings, props.users))
const undatedGroups = computed(() => props.projects.map(project => ({
  project, items: overview.value.undated.filter(item => item.projectId === project.id),
})).filter(group => group.items.length))
const timelineDate = (date: string) => new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric', weekday: 'short',
}).format(new Date(`${date}T00:00:00+08:00`))
let dateTimer: ReturnType<typeof setInterval> | undefined
onMounted(() => { dateTimer = setInterval(() => { currentDate.value = today() }, 1_000) })
onBeforeUnmount(() => clearInterval(dateTimer))
const editor = ref<{ intent: Intent; project?: Project; node?: Milestone } | null>(null)
const filtered = computed(() => props.projects.filter(p =>
  `${p.code} ${p.name} ${p.owner_name}`.toLowerCase().includes(search.value.toLowerCase()) &&
  (!stateFilter.value || p.status === stateFilter.value || (stateFilter.value === 'active' && p.status === 'not_started')) && (!riskOnly.value || !!p.flags?.length)))
const visibleProjects = computed(() => filtered.value.slice((page.value - 1) * 15, page.value * 15))
const selected = computed(() => props.projects.find(p => p.id === selectedId.value))
function edit(intent: Intent, project?: Project, node?: Milestone) { editor.value = { intent, project, node } }
function projectRowClass({ row }: { row: Project }) {
  const mood = !['completed', 'cancelled'].includes(row.status) && row.flags?.length
    ? 'mood-row-attention'
    : `mood-row-${row.status === 'not_started' ? 'active' : row.status}`
  return `${mood} project-tone-${projectColorTone(row.id)}`
}
</script>
<template>
  <div class="page-heading progress-heading"><div><h1>项目进度</h1><p class="muted">把每一步推进，都变成看得见的成就。</p></div><div class="heading-actions"><p class="progress-cheer">✦ 今天也在稳稳向前 · {{ mood.total }} 个项目</p><el-button type="primary" @click="edit('create_project')">＋ 新建项目</el-button></div></div>
  <section class="mood-summary" aria-label="项目状态概览">
    <article v-for="card in mood.cards.filter(card => card.tone !== 'preparation')" :key="card.tone" class="mood-card" :class="`tone-${card.tone}`"><span>{{ card.label }}</span><strong>{{ card.count }} 项</strong><small>{{ card.message }}</small></article>
  </section>
  <section v-loading="loading" class="planning-timeline" aria-label="关键时间轴">
    <div class="timeline-heading"><h2>关键时间轴</h2><div class="range-actions"><span class="timeline-range">{{ timelineOverview.rangeStart }} — {{ timelineOverview.endDate }}</span><el-button-group><el-button :type="timelineRange === 'week' ? 'primary' : 'default'" @click="timelineRange = 'week'">本周</el-button><el-button :type="timelineRange === 'month' ? 'primary' : 'default'" @click="timelineRange = 'month'">本月</el-button></el-button-group></div></div>
    <p class="timeline-hint">按计划截止日期排列 · 点击查看项目</p>
    <div v-if="timelineOverview.groups.length" class="timeline">
      <div v-for="group in timelineOverview.groups" :key="group.date" class="timeline-group" :class="[`timeline-${timelineEmphasis(group.date, currentDate)}`, { 'timeline-warning': group.items.some(item => timelineStatusLabel(item) === '逾期'), 'is-today': group.date === currentDate }]">
        <div class="timeline-day"><time :datetime="group.date">{{ group.date === currentDate ? '今天 · ' : '' }}{{ timelineDate(group.date) }}</time><span>{{ group.date === currentDate ? '今日安排' : `${group.items.length} 项安排` }}</span></div>
        <div class="timeline-items"><button v-for="item in group.items" :key="item.id" class="timeline-item" :class="[`status-${arrangementPresentation(item).tone}`, `project-tone-${projectColorTone(item.projectId)}`, { 'is-overdue': timelineStatusLabel(item) === '逾期', 'is-paused': item.paused }]" @click="selectedId = item.projectId"><span class="timeline-project-block"><span class="timeline-project">{{ item.code }} {{ item.projectName }}</span><span v-if="item.owners.some(person => person.name !== '待明确')" class="timeline-owners"><span v-for="person in item.owners" :key="`${person.role}-${person.name}`"><b :class="`role-${ownerRoleTone(person.role)}`">{{ ownerRoleLabel(person.role, person.primary) }}</b>{{ person.name }}</span></span></span><span class="timeline-arrow">→</span><strong>{{ item.target }}</strong><span v-if="item.meeting" class="timeline-action">{{ item.timeText }}</span><span v-if="item.paused" class="timeline-action">暂停中</span><span v-else-if="item.status === 'completed'" class="timeline-action is-completed">已完成</span><span v-else-if="timelineStatusLabel(item) === '逾期'" class="timeline-action">逾期</span></button></div>
      </div>
    </div>
    <p v-else class="timeline-empty">{{ loading ? '正在读取事项…' : '暂无已定日期的项目事项' }}</p>
  </section>
  <section v-loading="loading" class="planning-timeline" aria-label="安排日历">
    <div class="timeline-heading"><h2>安排日历</h2><div class="range-actions"><span class="timeline-range">{{ calendarOverview.rangeStart }} — {{ calendarOverview.endDate }}</span><el-button-group><el-button :type="calendarRange === 'week' ? 'primary' : 'default'" @click="calendarRange = 'week'">本周</el-button><el-button :type="calendarRange === 'month' ? 'primary' : 'default'" @click="calendarRange = 'month'">本月</el-button></el-button-group></div></div>
    <p class="timeline-hint">{{ calendarOverview.calendarActiveCount }} 项进行中 · 跨天事项每天展示，数量不重复计算 · 点击查看项目</p>
    <el-date-picker v-model="calendarStart" type="date" value-format="YYYY-MM-DD" placeholder="选择参考日期" aria-label="日历参考日期" clearable />
    <div class="calendar-grid" :class="{ 'is-month': calendarRange === 'month' }">
      <section v-for="day in calendarOverview.calendarDays" :key="day.date" class="calendar-day" :class="{ 'is-today': day.date === currentDate }" :style="day.date === calendarOverview.rangeStart ? { gridColumnStart: day.column } : undefined" :aria-label="timelineDate(day.date)">
        <header class="calendar-date"><time :datetime="day.date">{{ timelineDate(day.date) }}</time><span v-if="day.date === currentDate">今天</span></header>
        <div class="calendar-events"><button v-for="item in day.items" :key="item.id" class="calendar-event" :class="[`status-${arrangementPresentation(item).tone}`, `project-tone-${projectColorTone(item.projectId)}`]" @click="selectedId = item.projectId"><span class="calendar-project">{{ item.code }} {{ item.projectName }}</span><strong>{{ item.target }}</strong><span v-if="item.meeting" class="calendar-event-meta">{{ item.timeText }}</span><span class="calendar-owners"><span v-for="person in item.owners" :key="`${person.role}-${person.name}`"><b :class="`role-${ownerRoleTone(person.role)}`">{{ ownerRoleLabel(person.role, person.primary) }}</b>{{ person.name }}</span></span><span class="calendar-event-meta"><span>{{ item.action }}</span><strong>{{ arrangementPresentation(item).label }}</strong></span></button><p v-if="!day.items.length" class="calendar-empty">暂无安排</p></div>
      </section>
    </div>
  </section>
  <section v-if="undatedGroups.length" class="planning-timeline" aria-label="日期待定事项">
    <div class="timeline-heading"><h2>日期待定</h2><span class="timeline-range">{{ undatedGroups.length }} 个项目 · {{ overview.undated.length }} 项事项</span></div>
    <p class="timeline-hint">按项目分组 · 点击事项查看详情</p>
    <div class="undated-grid">
      <article v-for="group in undatedGroups" :key="group.project.id" class="undated-project" :class="`project-tone-${projectColorTone(group.project.id)}`">
        <h3><span>{{ group.project.code }}</span>{{ group.project.name }}<small>{{ group.items.length }} 项</small></h3>
        <ul><li v-for="item in group.items" :key="item.id"><button @click="selectedId = item.projectId">{{ item.target }} · {{ arrangementPresentation(item).label }}<span v-if="item.timeText"> · {{ item.timeText }}</span></button></li></ul>
      </article>
    </div>
  </section>
  <section class="progress-sheet" aria-label="全部项目进度表">
    <div class="filters"><el-input v-model="search" placeholder="搜索项目、编号、负责人" clearable aria-label="搜索项目" @input="page = 1" /><el-select v-model="stateFilter" placeholder="全部状态" clearable aria-label="筛选项目状态" @change="page = 1"><el-option v-for="value in editableStates" :key="value" :label="stateLabels[value]" :value="value" /></el-select><el-checkbox v-model="riskOnly" @change="page = 1">只看风险</el-checkbox></div>
    <el-table v-loading="loading" :data="visibleProjects" row-key="id" class="progress-table" :row-class-name="projectRowClass" :empty-text="projects.length ? '没有符合筛选条件的项目' : '暂无项目，点击右上角新建项目'" @row-click="(project: Project) => selectedId = project.id">
      <el-table-column label="项目" min-width="140"><template #default="{ row }"><div class="project-identity" :class="`project-tone-${projectColorTone(row.id)}`"><button class="project-name" @click.stop="selectedId = row.id">{{ row.name }}</button><div class="table-note">{{ row.code }}</div></div></template></el-table-column>
      <el-table-column label="负责人 / 分工" min-width="155"><template #default="{ row }"><OwnerRoles :project="row" :users="users" /></template></el-table-column>
      <el-table-column prop="due_date" label="截止日期" min-width="100" />
      <el-table-column label="状态" min-width="80"><template #default="{ row }"><StateBadge :state="row.status" :flags="row.flags" :reason="row.pause_reason" /></template></el-table-column>
      <el-table-column label="操作" width="110" fixed="right"><template #default="{ row }"><el-button link type="primary" @click.stop="selectedId = row.id">查看</el-button><el-button link :disabled="['completed', 'cancelled'].includes(row.status)" @click.stop="edit('edit_project', row)">编辑</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-if="filtered.length > 15" v-model:current-page="page" :page-size="15" :total="filtered.length" layout="prev, pager, next, total" class="pagination" />
  </section>
  <ProjectDetail v-if="selected" :project="selected" :actor="actor" :users="users" @close="selectedId = ''" @edit="(intent, node) => edit(intent, selected, node)" />
  <ActionEditor v-if="editor" :key="`${editor.intent}-${editor.project?.id}-${editor.node?.id}`" v-bind="editor" :actor="actor" :users="users" @close="editor = null" />
</template>

<style scoped>
.heading-actions{display:flex;align-items:center;justify-content:flex-end;gap:12px;flex-wrap:wrap}.progress-cheer{margin:0;padding:8px 12px;border-radius:10px;background:#f2f4f7;color:#526176;font-size:12px}.mood-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-bottom:22px}.mood-card{position:relative;overflow:hidden;display:grid;grid-template-columns:1fr auto;gap:7px 12px;padding:17px 18px;background:#fff;border:1px solid #e1e7f0;border-radius:12px;box-shadow:0 7px 22px #1e3f700a}.mood-card::before{content:'';position:absolute;inset:0 auto 0 0;width:4px;background:#8294ad}.mood-card>span{font-size:12px;color:#718098}.mood-card>strong{grid-row:1 / span 2;grid-column:2;font-size:21px;font-weight:650;color:#273750}.mood-card>small{font-size:11px;color:#8290a5}.mood-card.tone-active::before{background:#5ca8dd}.mood-card.tone-completed::before{background:#43a47a}.mood-card.tone-attention::before{background:#d9535f}.mood-card.tone-active{background:#e9f6ff}.mood-card.tone-completed{background:#f8fdfb}.mood-card.tone-attention{background:#fffafb}
.project-tone-orange{--project-tone:#e46f18;--project-soft:#fff1e7;--project-border:#f0ae7c}.project-tone-lemon{--project-tone:#ae9200;--project-soft:#fff9d7;--project-border:#e3d36d}.project-tone-grass{--project-tone:#78a842;--project-soft:#f1f8e8;--project-border:#b8d58e}.project-tone-lavender{--project-tone:#8a70b5;--project-soft:#f4f0fa;--project-border:#c7b8dd}.project-tone-brown{--project-tone:#754c35;--project-soft:#f5eee9;--project-border:#b99e8e}.project-tone-slate{--project-tone:#687386;--project-soft:#f2f4f7;--project-border:#b8c0cc}.project-tone-charcoal{--project-tone:#3f4856;--project-soft:#eef0f3;--project-border:#a8afb9}
.calendar-event.status-active{background:#e9f6ef;border-left-color:#43a47a;color:#28775a}.calendar-event.status-completed{background:#e9f6ff;border-left-color:#5ca8dd;color:#265f89}.calendar-event.status-paused{background:#f2f3f6;border-left-color:#8a93a2;color:#606b7b}.calendar-grid{margin-top:14px}.range-actions{display:flex;align-items:center;justify-content:flex-end;gap:10px;flex-wrap:wrap}.range-actions :deep(.el-button){padding:6px 12px}.calendar-owners,.timeline-owners{display:flex;flex-wrap:wrap;gap:4px 7px;font-size:10px}.calendar-owners>span,.timeline-owners>span{display:inline-flex;align-items:center;gap:3px}.calendar-owners b,.timeline-owners b{font-size:9px;font-weight:600;border-radius:3px;padding:0 4px}.calendar-owners b.role-primary,.timeline-owners b.role-primary{color:#3068da;background:#eef3ff}.calendar-owners b.role-secondary,.timeline-owners b.role-secondary{color:#7a54a6;background:#f3edfa}.calendar-owners b.role-neutral,.timeline-owners b.role-neutral{color:#526176;background:#ffffffb8}
.undated-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.undated-project{min-width:0;border:1px solid #e5e9f0;border-left:4px solid var(--project-tone,#8294ad);border-radius:8px;padding:14px 16px;background:#fafbfe}.undated-project h3{display:flex;align-items:baseline;flex-wrap:wrap;gap:8px;margin:0 0 9px;font-size:14px;color:#26354a}.undated-project h3>span{color:var(--project-tone,#7c889a);background:var(--project-soft,#f2f4f7);border:1px solid var(--project-border,#e1e7f0);border-radius:4px;padding:1px 5px}.undated-project h3>span,.undated-project h3>small{font-size:11px;font-weight:400}.undated-project h3>small{margin-left:auto;color:#7c889a}.undated-project ul{list-style:none;padding:0;margin:0}.undated-project li+li{border-top:1px solid #e9edf4}.undated-project button{display:block;width:100%;border:0;background:none;text-align:left;font:inherit;font-size:13px;line-height:1.6;color:#3068da;padding:7px 0;cursor:pointer;overflow-wrap:anywhere}.undated-project button:hover{text-decoration:underline}.undated-project button:focus-visible{outline:2px solid #3068da;outline-offset:2px}
@media(max-width:700px){.undated-grid{grid-template-columns:1fr}.timeline .timeline-group{grid-template-columns:1fr;gap:7px}}
.progress-heading h1{font-size:24px;margin-bottom:6px}
.calendar-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));border-top:1px solid #e5e9f0;border-left:1px solid #e5e9f0;border-radius:8px;overflow:hidden}.calendar-grid.is-month .calendar-day{min-height:138px}
.calendar-day{min-width:0;min-height:170px;border-right:1px solid #e5e9f0;border-bottom:1px solid #e5e9f0;background:#fff}.calendar-day.is-today{background:#f2faff;box-shadow:inset 0 3px #5ca8dd}.calendar-day:has(.calendar-empty):not(.is-today){background:#fafbfc}
.calendar-date{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:4px;padding:12px 10px;font-size:12px;font-weight:600;color:#526176;border-bottom:1px solid #edf0f4}.calendar-date>span{font-size:10px;color:#24658f;background:#dff2ff;border-radius:4px;padding:2px 5px}
.calendar-events{padding:9px;display:flex;flex-direction:column;gap:8px}.calendar-event{width:100%;display:flex;flex-direction:column;gap:5px;text-align:left;font:inherit;font-size:12px;line-height:1.5;border:0;border-left:3px solid #5ca8dd;border-radius:5px;background:#e9f6ff;padding:8px;color:#265f89;cursor:pointer;overflow-wrap:anywhere}.calendar-event:hover{filter:brightness(.97)}.calendar-event:focus-visible{outline:2px solid #3d8fc8;outline-offset:2px}.calendar-project{align-self:flex-start;font-size:10px;color:var(--project-tone,#526176);background:var(--project-soft,#f2f4f7);border:1px solid var(--project-border,#e1e7f0);border-radius:4px;padding:1px 5px}.calendar-event strong{font-weight:600}.calendar-event-meta{display:flex;justify-content:space-between;gap:5px;font-size:10px}.calendar-empty{font-size:12px;color:#b3bac5;margin:16px 0;text-align:center}
@media(max-width:600px){.calendar-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.calendar-day{grid-column-start:auto!important}.range-actions{justify-content:flex-start}}
.planning-timeline{background:#fff;border:1px solid #e1e7f0;border-radius:14px;padding:22px 24px;margin-bottom:20px;box-shadow:0 7px 22px #1e3f7008}
.timeline-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
.timeline-heading h2{display:flex;align-items:center;gap:10px;margin:0;font-size:17px;color:#26354a}
.timeline-heading h2::before{content:'';width:4px;height:18px;border-radius:2px;background:#526176}
.timeline-heading h2 span{font-size:12px;font-weight:500;color:#3068da;background:#eef3ff;border-radius:20px;padding:3px 10px}
.timeline-range,.timeline-hint{font-size:12px;color:#7c889a}.timeline-hint{margin:10px 0 22px;line-height:1.7}
.timeline{position:relative;padding-left:28px}.timeline::before{content:'';position:absolute;left:7px;top:8px;bottom:10px;width:2px;background:#c9def3}
.timeline-group{position:relative;margin-bottom:12px;display:grid;grid-template-columns:175px minmax(0,1fr);gap:12px;align-items:start}.timeline-group:last-child{margin-bottom:0}
.timeline-group::before{content:'';position:absolute;left:-28px;top:8px;width:16px;height:16px;box-sizing:border-box;border:4px solid #4f8fda;border-radius:50%;background:#fff;box-shadow:0 0 0 3px #fff}.timeline-group.timeline-warning::before{border-color:#d9535f}.timeline-group.is-today::before{border-color:#43a47a;box-shadow:0 0 0 4px #43a47a25}
.timeline-day{display:flex;align-items:center;gap:5px 8px;flex-wrap:wrap;margin-bottom:0;padding-top:5px;font-size:14px;font-weight:600;color:#26354a}.timeline-day>span{font-size:11px;font-weight:500;color:#2c69a8;background:#e7f2fd;border-radius:20px;padding:2px 9px}.timeline-warning .timeline-day>span{color:#b43e48;background:#fdebed}.is-today .timeline-day>span{background:#dff2e8;color:#287052}
.timeline-items{display:flex;flex-wrap:wrap;gap:8px}.timeline-item{--timeline-tone:var(--project-tone,#60728d);display:flex;align-items:center;flex-wrap:wrap;gap:7px;max-width:100%;text-align:left;font:inherit;font-size:13px;line-height:1.6;background:var(--project-soft,#f5f7fb);border:1px solid var(--project-border,#e1e7f0);border-left:3px solid var(--timeline-tone);border-radius:9px;padding:7px 11px;cursor:pointer;box-shadow:0 3px 9px #1e3f700a;overflow-wrap:anywhere}.timeline-item:hover{border-color:var(--timeline-tone);filter:brightness(.985)}.timeline-item.is-paused{--timeline-tone:#8a93a2;border-color:#e2e5ea;background:#f7f8fa}.timeline-item:focus-visible{outline:2px solid var(--timeline-tone);outline-offset:3px}.timeline-group.timeline-near .timeline-item{padding:10px 14px;font-size:14px;box-shadow:0 6px 16px #1e3f7010}.timeline-group.timeline-near .timeline-item strong{font-size:14px}.timeline-group.timeline-far{opacity:.5}.timeline-group.timeline-far .timeline-item{padding:5px 9px;font-size:12px;box-shadow:none}
.timeline-project-block{display:flex;flex-direction:column;align-items:flex-start;gap:4px}.timeline-project{color:var(--project-tone,#526176);background:var(--project-soft,#f2f4f7);border:1px solid var(--project-border,#e1e7f0);border-radius:5px;padding:1px 6px}.timeline-arrow{color:#99a5b5}.timeline-item strong{color:var(--project-tone,#526176);font-weight:600}.timeline-item.is-paused strong{color:#606b7b}.timeline-action{font-size:10px;color:#b43e48;background:#fdebed;border-radius:4px;padding:1px 6px}.timeline-action.start{color:#327d5d;background:#eaf6f0}.timeline-owner{font-size:11px;color:#8a95a6}.timeline-empty{font-size:13px;color:#8a95a6;padding:14px 0 4px;margin:0}
@media(max-width:600px){.planning-timeline{padding:18px 16px}.timeline-item{width:100%}.timeline-range{font-size:11px}}
.progress-sheet{background:#fff;border:1px solid #e1e7f0;border-radius:14px;overflow:hidden;box-shadow:0 7px 22px #1e3f7008}
.progress-sheet .filters{padding:18px 20px;margin:0;border-bottom:1px solid #edf0f4}
.progress-table{--el-table-header-bg-color:#fafbfc;--el-table-header-text-color:#68788f;--el-table-row-hover-bg-color:#f7faff;--el-table-border-color:#edf0f4;color:#26354a}
.progress-table :deep(.el-table__cell){padding:15px 0}
.progress-table :deep(.cell){padding:0 10px}
.progress-table :deep(.el-table__body tr>td:first-child){box-shadow:inset 4px 0 #9aa4b2}.progress-table :deep(.el-table__body tr.project-tone-orange>td:first-child){box-shadow:inset 4px 0 #e46f18}.progress-table :deep(.el-table__body tr.project-tone-lemon>td:first-child){box-shadow:inset 4px 0 #ae9200}.progress-table :deep(.el-table__body tr.project-tone-grass>td:first-child){box-shadow:inset 4px 0 #78a842}.progress-table :deep(.el-table__body tr.project-tone-lavender>td:first-child){box-shadow:inset 4px 0 #8a70b5}.progress-table :deep(.el-table__body tr.project-tone-brown>td:first-child){box-shadow:inset 4px 0 #754c35}.progress-table :deep(.el-table__body tr.project-tone-slate>td:first-child){box-shadow:inset 4px 0 #687386}.progress-table :deep(.el-table__body tr.project-tone-charcoal>td:first-child){box-shadow:inset 4px 0 #3f4856}
.project-identity{position:relative}
.project-name{padding:0;border:0;background:none;color:#26354a;font-weight:500;text-align:left;line-height:1.6;overflow-wrap:anywhere}
.project-name:hover{color:#3068da}
.table-note{font-size:12px;color:var(--project-tone,#8a95a6);margin-top:3px}
.progress-sheet .pagination{margin:0;padding:18px}
@media(max-width:900px){.mood-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.heading-actions{justify-content:flex-start}}
@media(max-width:600px){.mood-summary{gap:9px}.mood-card{padding:14px}.mood-card>strong{font-size:17px}.progress-cheer{width:100%}}
.calendar-event.status-completed{background:#e9f6ff;border-left-color:#5ca8dd;color:#265f89}.timeline-item.status-completed{border-color:#9cc9ef;background:#e9f6ff;color:#265f89}.timeline-item.status-completed strong{color:#265f89}.timeline-action.is-completed{color:#265f89;background:#e9f6ff}
.timeline-item.status-active{background:#e9f6ef;border-color:#8ed0b0;color:#28775a}.timeline-item.status-active strong{color:#28775a}
.calendar-grid{grid-template-columns:repeat(4,minmax(0,1fr))}.calendar-grid.is-month{grid-template-columns:repeat(7,minmax(0,1fr))}
</style>

