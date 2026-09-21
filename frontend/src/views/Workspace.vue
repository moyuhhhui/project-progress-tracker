<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../api'
import { dateTime, errorText } from '../domain'
import type { Actor, Project, User, Snapshot, IntegrationStatus } from '../types'
import ProjectsPage from './ProjectsPage.vue'
import DraftsPage from './DraftsPage.vue'
import RemindersPage from './RemindersPage.vue'
import AdminPage from './AdminPage.vue'
import FullscreenButton from '../components/FullscreenButton.vue'

const props = defineProps<{ actor: Actor }>()
const section = ref('projects')
const projects = ref<Project[]>([]), users = ref<User[]>([])
const status = ref<IntegrationStatus | null>(null), snapshotAt = ref('')
const loading = ref(false), error = ref('')
const titles: Record<string, string> = { projects: '项目进度', assistant: '项目助手', reminders: '提醒记录', admin: '成员与设置' }
let refreshing = false
let refreshTimer: ReturnType<typeof setInterval> | undefined
async function refresh(silent = false) {
  if (refreshing) return
  refreshing = true
  loading.value = !silent; error.value = ''
  const results = await Promise.allSettled([
    api<Snapshot>('/api/projects'), api<User[]>('/api/users'), api<IntegrationStatus>('/api/status'),
  ])
  const [p, u, s] = results
  if (p.status === 'fulfilled') { projects.value = p.value.projects; snapshotAt.value = p.value.at }
  if (u.status === 'fulfilled') users.value = u.value
  if (s.status === 'fulfilled') status.value = s.value
  error.value = results.filter(r => r.status === 'rejected').map(r => errorText(r.reason)).join('；')
  loading.value = false
  refreshing = false
}
function refreshVisible() {
  if (document.visibilityState === 'visible') void refresh(true)
}
async function openLinkedDraft() {
  const hash = window.location.hash
  if (hash === '#projects') section.value = 'projects'
  if (hash === '#assistant' || hash === '#drafts') section.value = 'assistant'
}
onMounted(async () => {
  window.addEventListener('hashchange', openLinkedDraft)
  window.addEventListener('focus', refreshVisible)
  document.addEventListener('visibilitychange', refreshVisible)
  refreshTimer = setInterval(refreshVisible, 5_000)
  await refresh()
  await openLinkedDraft()
})
onBeforeUnmount(() => {
  clearInterval(refreshTimer)
  window.removeEventListener('hashchange', openLinkedDraft)
  window.removeEventListener('focus', refreshVisible)
  document.removeEventListener('visibilitychange', refreshVisible)
})
</script>
<template>
  <div class="workspace">
    <aside class="sidebar">
      <div class="brand"><span class="brand-mark">P</span><span>项目进度<br><small>团队工作台</small></span></div>
      <p class="nav-heading">工作空间</p>
      <nav aria-label="工作台导航">
        <button v-for="(label, value) in titles" v-show="value !== 'admin' || actor.internal_shared || actor.role === 'admin'" :key="value"
          :class="{ selected: section === value }" @click="section = value"><span>{{ label }}</span></button>
      </nav>
      <div class="sidebar-bottom"><span class="connection-dot" :class="{ disconnected: error || !snapshotAt }"></span> {{ error ? '连接异常，请刷新重试' : snapshotAt ? '已连接数据服务' : '等待连接' }}<p>保存后立即进入正式项目数据</p></div>
    </aside>
    <main class="workspace-main">
      <header class="topbar"><span>{{ titles[section] }}</span><div class="inline"><span>公司共享工作台</span><a href="#display">打开大屏 ↗</a><FullscreenButton /></div></header>
      <div class="page-content">
        <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="section-gap" />
        <div class="page-toolbar"><span class="muted">{{ snapshotAt ? `最近读取 ${dateTime(snapshotAt)} · 每 5 秒自动同步` : '正在连接数据服务' }}</span><el-button :loading="loading" @click="refresh()">刷新数据</el-button></div>
        <ProjectsPage v-if="section === 'projects'" :actor="actor" :projects="projects" :users="users" :loading="loading" @saved="refresh" />
        <DraftsPage v-if="section === 'assistant'" :status="status" @refresh="refresh" />
        <RemindersPage v-if="section === 'reminders'" :actor="actor" :projects="projects" />
        <AdminPage v-if="section === 'admin' && (actor.internal_shared || actor.role === 'admin')" :actor="actor" :users="users" :status="status" @refresh="refresh" />
      </div>
    </main>
  </div>
</template>
