<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api'
import { arrangementPresentation, calendarRowTemplate, dateTime, errorText, isSnapshotStale, planningOverview, projectColorTone, today } from '../domain'
import type { Actor, Snapshot } from '../types'
import FullscreenButton from '../components/FullscreenButton.vue'

defineProps<{ actor: Actor }>()
const snapshot = ref<Snapshot | null>(null)
const lastSuccess = ref<number | null>(null)
const now = ref(Date.now()), currentDate = ref(today()), calendarStart = ref('')
const timelineRange = ref<'week' | 'month'>('week'), calendarRange = ref<'week' | 'month'>('week')
const loading = ref(false), error = ref('')
const projects = computed(() => snapshot.value?.projects || [])
const timelineOverview = computed(() => planningOverview(projects.value, currentDate.value, timelineRange.value))
const calendarOverview = computed(() => planningOverview(projects.value, calendarStart.value || currentDate.value, calendarRange.value))
const calendarRows = computed(() => calendarRowTemplate(calendarOverview.value.calendarDays))
const stale = computed(() => isSnapshotStale(lastSuccess.value, now.value))
const time = computed(() => new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false,
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(now.value))
const timelineDate = (date: string) => new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric', weekday: 'short',
}).format(new Date(`${date}T00:00:00+08:00`))

async function refresh() {
  if (loading.value) return
  loading.value = true
  try {
    snapshot.value = await api<Snapshot>('/api/display')
    lastSuccess.value = Date.now(); error.value = ''
  } catch (e) { error.value = errorText(e) }
  finally { loading.value = false }
}

let clockTimer: number, refreshTimer: number
onMounted(() => {
  void refresh()
  clockTimer = window.setInterval(() => { now.value = Date.now(); currentDate.value = today() }, 1000)
  refreshTimer = window.setInterval(() => { void refresh() }, 15_000)
})
onBeforeUnmount(() => { [clockTimer, refreshTimer].forEach(window.clearInterval) })
</script>

<template>
  <main class="display-board">
    <header class="board-header">
      <div><p class="board-kicker">公司项目进度追踪 / 只读展示</p><h1>关键时间与安排</h1></div>
      <div class="board-clock"><time>{{ time }}</time><span>Wuhan · 翼牛科技</span><div class="board-controls"><a href="#projects">返回工作台</a><FullscreenButton /></div></div>
    </header>
    <div v-if="stale && lastSuccess !== null" class="board-warning" role="alert">连接异常 / 数据可能过期 · 已超过 60 秒未成功刷新，以下为最后成功快照。</div>
    <div v-else-if="error" class="board-warning" role="alert">{{ lastSuccess === null ? '暂时无法读取项目：' : '刷新失败，正在保留上次数据：' }}{{ error }}</div>
    <div v-if="!snapshot" class="board-empty">{{ loading ? '正在读取项目快照…' : '暂无成功快照，请检查连接后重试。' }}<button @click="refresh" :disabled="loading">重新读取</button></div>
    <div v-else class="board-planning">
      <section class="planning-panel timeline-panel" aria-label="关键时间轴">
        <div class="panel-heading"><div><p class="board-kicker">TIMELINE</p><h2>关键时间轴</h2></div><div class="range-actions"><span>{{ timelineOverview.rangeStart }} — {{ timelineOverview.endDate }}</span><el-button-group><el-button :type="timelineRange === 'week' ? 'primary' : 'default'" @click="timelineRange = 'week'">本周</el-button><el-button :type="timelineRange === 'month' ? 'primary' : 'default'" @click="timelineRange = 'month'">本月</el-button></el-button-group></div></div>
        <p class="panel-hint">按计划截止日期排列</p>
        <div v-if="timelineOverview.groups.length" class="timeline">
          <div v-for="group in timelineOverview.groups" :key="group.date" class="timeline-group" :class="{ 'is-overdue': group.date < currentDate, 'is-today': group.date === currentDate, 'is-future': group.date > currentDate }">
            <div class="timeline-day"><time :datetime="group.date">{{ group.date === currentDate ? '今天 · ' : '' }}{{ timelineDate(group.date) }}</time><span>{{ group.items.length }} 项</span></div>
            <div class="timeline-items">
              <article v-for="item in group.items" :key="item.id" class="timeline-item" :class="[`status-${arrangementPresentation(item).tone}`, `project-tone-${projectColorTone(item.projectId)}`, { overdue: group.date < currentDate }]">
                <span class="item-project">{{ item.code }} · {{ item.projectName }}</span><strong>{{ item.target }}</strong>
                <div class="item-meta"><span v-if="item.owner !== '待明确'">{{ item.owner }}</span><b>{{ group.date < currentDate ? '逾期' : arrangementPresentation(item).label }}</b></div>
              </article>
            </div>
          </div>
        </div>
        <p v-else class="panel-empty">{{ loading ? '正在读取事项…' : '暂无已定日期的项目事项' }}</p>
      </section>

      <section class="planning-panel calendar-panel" aria-label="安排日历">
        <div class="panel-heading"><div><p class="board-kicker">CALENDAR</p><h2>安排日历</h2></div><div class="range-actions"><span>{{ calendarOverview.rangeStart }} — {{ calendarOverview.endDate }}</span><el-button-group><el-button :type="calendarRange === 'week' ? 'primary' : 'default'" @click="calendarRange = 'week'">本周</el-button><el-button :type="calendarRange === 'month' ? 'primary' : 'default'" @click="calendarRange = 'month'">本月</el-button></el-button-group></div></div>
        <div class="calendar-toolbar"><p class="panel-hint">{{ calendarOverview.calendarActiveCount }} 项进行中 · 跨天事项每天展示</p><el-date-picker v-model="calendarStart" type="date" value-format="YYYY-MM-DD" placeholder="选择参考日期" aria-label="日历参考日期" clearable /></div>
        <div class="calendar-grid" :class="{ 'is-month': calendarRange === 'month' }" :style="{ '--calendar-rows': calendarRows }">
          <section v-for="day in calendarOverview.calendarDays" :key="day.date" class="calendar-day" :class="{ 'is-today': day.date === currentDate }" :style="day.date === calendarOverview.rangeStart ? { gridColumnStart: day.column } : undefined" :aria-label="timelineDate(day.date)">
            <header class="calendar-date"><time :datetime="day.date">{{ timelineDate(day.date) }}</time><span v-if="day.date === currentDate">今天</span></header>
            <div class="calendar-events">
              <article v-for="item in day.items" :key="item.id" class="calendar-event" :class="[`status-${arrangementPresentation(item).tone}`, `project-tone-${projectColorTone(item.projectId)}`]">
                <span class="calendar-project">{{ item.code }} {{ item.projectName }}</span><strong>{{ item.target }}</strong>
                <span class="calendar-owners"><span v-for="person in item.owners" :key="`${person.role}-${person.name}`"><b>{{ person.role || (person.primary ? 'A角' : '负责人') }}</b>{{ person.name }}</span></span>
                <div class="item-meta"><span>{{ item.action }}</span><b>{{ arrangementPresentation(item).label }}</b></div>
              </article>
              <p v-if="!day.items.length" class="calendar-empty">暂无安排</p>
            </div>
          </section>
        </div>
      </section>
    </div>
    <footer class="board-footer"><span :class="{ 'stale-text': stale && lastSuccess !== null }">{{ lastSuccess === null ? '尚未连接' : stale ? '● 数据可能过期' : error ? '● 刷新重试中' : '● 已连接' }} · 快照 {{ dateTime(snapshot?.at) }}</span><button @click="refresh" :disabled="loading">{{ loading ? '读取中' : '刷新' }}</button></footer>
  </main>
</template>

<style scoped>
.display-board{height:100vh;min-height:680px;display:flex;flex-direction:column;overflow:hidden;background:linear-gradient(135deg,#f4f7fc 0%,#f1f8fc 58%,#f6f4fc 100%);color:#24324a;color-scheme:light;padding:22px 30px;font-family:Inter,"Microsoft YaHei",sans-serif;box-sizing:border-box}
.board-header,.board-clock,.board-controls,.panel-heading,.calendar-toolbar,.item-meta,.board-footer{display:flex;align-items:center;justify-content:space-between;gap:16px}.board-header{margin-bottom:16px;flex-shrink:0}.board-kicker{font-size:11px;color:#4d65c4;letter-spacing:2px;margin:0 0 7px}.board-header h1{font-size:27px;margin:0}.board-clock{align-items:flex-end;flex-direction:column;gap:5px}.board-clock time{font-size:21px;font-variant-numeric:tabular-nums}.board-clock>span,.panel-heading>span,.range-actions>span{font-size:11px;color:#6f7c90}.board-controls{gap:12px}.board-controls a{font-size:12px}.display-board button{font:inherit;cursor:pointer;color:inherit;border:1px solid #d4dde8;border-radius:7px;background:#fff;padding:6px 11px}.display-board button:hover{background:#f3f6fa}.display-board button:disabled{opacity:.45;cursor:default}.range-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap}.range-actions :deep(.el-button){padding:5px 9px}.range-actions :deep(.el-button--primary){color:#fff;border-color:#4f8fda;background:#4f8fda}.range-actions :deep(.el-button--primary:hover){background:#417fc8}
.board-warning{padding:11px 16px;border:1px solid #efb7bd;color:#ac3440;background:#fff0f2;border-radius:8px;margin-bottom:14px;flex-shrink:0}.board-empty{flex:1;display:grid;place-items:center;border:1px dashed #c6d1df;color:#58677b;font-size:18px}.board-planning{display:grid;grid-template-columns:minmax(310px,31%) minmax(0,1fr);gap:20px;flex:1;min-height:0}.planning-panel{min-width:0;min-height:0;overflow:auto;background:#ffffffee;border:1px solid #dce5f0;border-radius:12px;padding:20px;box-shadow:0 10px 28px #284d7608}.panel-heading{align-items:flex-start}.panel-heading h2{font-size:19px;margin:0}.panel-hint{font-size:11px;color:#7c889a;margin:9px 0 17px}.panel-empty{font-size:13px;color:#8a95a6;padding:30px 0;text-align:center}
.timeline{position:relative;padding-left:28px}.timeline::before{content:'';position:absolute;left:7px;top:8px;bottom:10px;width:2px;background:#c9def3}.timeline-group{position:relative;margin-bottom:12px;opacity:.72}.timeline-group::before{content:'';position:absolute;left:-28px;top:8px;width:16px;height:16px;box-sizing:border-box;border:4px solid #4f8fda;border-radius:50%;background:#fff;box-shadow:0 0 0 3px #fff}.timeline-group.is-overdue::before{border-color:#d9535f}.timeline-group.is-today{opacity:1;margin:4px 0 24px}.timeline-group.is-today::before{border-color:#43a47a;box-shadow:0 0 0 4px #43a47a25}.timeline-day{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:7px;padding-top:5px;font-size:12px;font-weight:600}.timeline-day>span{font-size:9px;font-weight:500;color:#2c69a8;background:#e7f2fd;border-radius:20px;padding:2px 7px}.timeline-group.is-overdue .timeline-day>span{color:#b43e48;background:#fdebed}.timeline-group.is-today .timeline-day{font-size:17px;margin-bottom:11px}.timeline-group.is-today .timeline-day>span{font-size:11px;color:#287052;background:#dff2e8;padding:4px 10px}.timeline-items{display:flex;flex-wrap:wrap;gap:7px}.timeline-item{display:flex;align-items:center;flex-wrap:wrap;gap:7px;max-width:100%;border:1px solid var(--project-border,#dce5f0);border-left:3px solid var(--project-tone,#60728d);background:var(--project-soft,#f5f7fb);color:var(--project-tone,#526176);border-radius:8px;padding:6px 9px;box-shadow:0 3px 9px #1e3f700a}.timeline-group.is-today .timeline-item{min-width:82%;padding:13px 15px;border-left-width:5px;border-radius:9px;box-shadow:0 9px 22px #24324a16}.timeline-item.status-paused{border-color:#efb7bd;border-left-color:#d9535f;background:#fff0f2;color:#a93641}.timeline-item>strong{font-size:11px;overflow-wrap:anywhere}.timeline-group.is-today .timeline-item>strong{font-size:16px}.calendar-event>strong{display:block;font-size:11px;margin:4px 0;overflow-wrap:anywhere}.item-project{font-size:9px;color:var(--project-tone,#526176);background:#ffffffa8;border:1px solid var(--project-border,#e1e7f0);border-radius:5px;padding:1px 6px}.timeline-group.is-today .item-project,.timeline-group.is-today .item-meta{font-size:10px}.item-meta{font-size:9px;color:inherit;gap:8px}.item-meta b{font-weight:600}.timeline-item.overdue .item-meta b{color:#b43e48;background:#fdebed;border-radius:4px;padding:1px 5px}
.calendar-toolbar{align-items:flex-start;margin:9px 0 14px}.calendar-toolbar .panel-hint{margin:5px 0}.calendar-toolbar :deep(.el-date-editor){width:150px}.calendar-grid{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));border-top:1px solid #e5e9f0;border-left:1px solid #e5e9f0;border-radius:8px;overflow:hidden}.calendar-day{min-width:0;min-height:170px;border-right:1px solid #e5e9f0;border-bottom:1px solid #e5e9f0;background:#fff}.calendar-day.is-today{background:#f2faff;box-shadow:inset 0 3px #5ca8dd}.calendar-date{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:4px;padding:12px 10px;font-size:12px;font-weight:600;color:#526176;border-bottom:1px solid #edf0f4}.calendar-date>span{font-size:10px;color:#24658f;background:#dff2ff;border-radius:4px;padding:2px 5px}.calendar-events{padding:9px;display:flex;flex-direction:column;gap:8px}.calendar-event{width:100%;display:flex;flex-direction:column;gap:5px;box-sizing:border-box;border:0;border-left:3px solid #5ca8dd;border-radius:5px;background:#e9f6ff;padding:8px;color:#265f89;line-height:1.5}.calendar-event.status-active{background:#e9f6ff;border-left-color:#5ca8dd;color:#265f89}.calendar-event.status-completed{background:#e9f6ef;border-left-color:#43a47a;color:#28775a}.calendar-event.status-paused{background:#fff0f2;border-left-color:#d9535f;color:#a93641}.calendar-project{align-self:flex-start;font-size:10px;color:var(--project-tone,#526176);background:var(--project-soft,#f2f4f7);border:1px solid var(--project-border,#e1e7f0);border-radius:4px;padding:1px 5px}.calendar-owners{display:flex;flex-wrap:wrap;gap:3px 6px;font-size:9px}.calendar-owners>span{display:inline-flex;align-items:center;gap:3px}.calendar-owners b{font-size:8px;color:#526176;background:#ffffffb8;border-radius:3px;padding:0 3px}.calendar-empty{font-size:12px;color:#b3bac5;margin:16px 0;text-align:center}
.timeline-panel{display:flex;flex-direction:column}.timeline-panel>.timeline{flex:1;display:block;min-height:0}.timeline-items{display:flex;flex-direction:column;gap:7px;width:100%}.timeline-item{width:100%;min-height:64px;box-sizing:border-box;align-content:center;padding:11px clamp(10px,1vw,14px)}.timeline-group.is-today .timeline-item{min-width:0;min-height:106px;padding:18px clamp(15px,1.3vw,19px)}.timeline-item>strong{font-size:clamp(12px,1.05vw,15px)}.timeline-item .item-project,.timeline-item .item-meta{font-size:clamp(9px,.72vw,11px)}.timeline-group.is-today .timeline-item>strong{font-size:clamp(15px,1.3vw,18px)}.timeline-group.is-today .timeline-item .item-project,.timeline-group.is-today .timeline-item .item-meta{font-size:clamp(10px,.82vw,12px)}.calendar-panel{display:flex;flex-direction:column;overflow:hidden}.calendar-panel .calendar-grid{flex:1;min-height:0;grid-template-rows:minmax(0,1fr)}.calendar-panel .calendar-grid.is-month{grid-template-rows:var(--calendar-rows)}.calendar-panel .calendar-day{display:flex;flex-direction:column;min-height:0;overflow:hidden}.calendar-panel .calendar-date{flex-shrink:0}.calendar-panel .calendar-events{flex:1;min-height:0;overflow:hidden;gap:clamp(4px,.65vh,8px);padding:clamp(5px,.8vh,9px)}.calendar-event{flex:0 1 auto;min-height:0;overflow:hidden;gap:clamp(3px,.55vh,6px);padding:clamp(5px,.75vh,9px) clamp(7px,.7vw,12px)}.calendar-event>strong{display:-webkit-box;overflow:hidden;-webkit-box-orient:vertical;-webkit-line-clamp:4;font-size:clamp(11px,.9vw,13px);line-height:1.35}.calendar-event .item-meta{flex-shrink:0;font-size:clamp(8px,.68vw,10px)}.calendar-project{max-width:100%;box-sizing:border-box;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0}.calendar-grid.is-month .calendar-date{padding:5px 6px;font-size:10px}.calendar-grid.is-month .calendar-events{padding:3px;gap:3px}.calendar-grid.is-month .calendar-event{padding:3px 5px;gap:2px}.calendar-grid.is-month .calendar-event>strong{font-size:10px;-webkit-line-clamp:2}.calendar-grid.is-month .calendar-project{font-size:8px}.calendar-grid.is-month .calendar-owners{font-size:8px}.calendar-grid.is-month .item-meta{display:none}
@media(max-height:900px){.calendar-event .item-meta{display:none}.calendar-event>strong{-webkit-line-clamp:3}.calendar-date{padding:9px 8px}.calendar-panel .calendar-events{gap:4px;padding:5px}.calendar-event{padding:5px 7px}}
.calendar-grid.is-month .calendar-project{display:block;font-size:8px}.calendar-grid.is-month .calendar-owners{font-size:8px;flex-wrap:nowrap}
.project-tone-orange{--project-tone:#e46f18;--project-soft:#fff1e7;--project-border:#f0ae7c}.project-tone-lemon{--project-tone:#ae9200;--project-soft:#fff9d7;--project-border:#e3d36d}.project-tone-grass{--project-tone:#78a842;--project-soft:#f1f8e8;--project-border:#b8d58e}.project-tone-lavender{--project-tone:#8a70b5;--project-soft:#f4f0fa;--project-border:#c7b8dd}.project-tone-brown{--project-tone:#754c35;--project-soft:#f5eee9;--project-border:#b99e8e}.project-tone-slate{--project-tone:#687386;--project-soft:#f2f4f7;--project-border:#b8c0cc}.project-tone-charcoal{--project-tone:#3f4856;--project-soft:#eef0f3;--project-border:#a8afb9}
.board-footer{border-top:1px solid #dfe5ed;padding-top:10px;margin-top:12px;font-size:11px;color:#58677b;flex-shrink:0}.board-footer button{font-size:11px}.stale-text{color:#b43e48}
@media(min-width:1800px){.display-board{padding:30px 42px}.board-planning{grid-template-columns:minmax(360px,29%) minmax(0,1fr)}.calendar-day{min-height:210px}.calendar-event>strong{font-size:14px}.item-project,.item-meta{font-size:11px}}
@media(max-width:1000px){.display-board{height:auto;min-height:100vh;overflow:visible;padding:20px}.board-planning{grid-template-columns:1fr}.planning-panel{overflow:visible}.calendar-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.board-header{align-items:flex-start;flex-direction:column}.board-clock{align-items:flex-start}.calendar-toolbar{flex-wrap:wrap}}
@media(max-width:1000px){.calendar-day{grid-column-start:auto!important}}
</style>

