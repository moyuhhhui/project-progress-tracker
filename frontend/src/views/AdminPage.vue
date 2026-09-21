<script setup lang="ts">
import { computed, onMounted, reactive, ref, toRaw } from 'vue'
import { ElMessage } from 'element-plus'
import type { Actor, IntegrationStatus, Role, Settings, User } from '../types'
import { api } from '../api'
import { changedFields, errorText } from '../domain'

defineProps<{ actor: Actor; users: User[]; status: IntegrationStatus | null }>()
const emit = defineEmits<{ refresh: [] }>()
const roleLabels: Record<Role, string> = { admin: '管理员', member: '普通成员', display: '只读大屏' }
const userDialog = ref(false), editing = ref<User | null>(null), error = ref(''), busy = ref(false)
const userForm = reactive({ name: '', role: 'member' as Role, active: true, wecom_user_id: '' })
const settings = ref<Settings | null>(null), originalSettings = ref<Settings | null>(null)
const day = ref(''), workday = ref(false)
interface Review { title: string; path: string; method: string; body?: unknown; rows: { label: string; before: string; after: string }[] }
const review = ref<Review | null>(null)
const calendar = computed(() => Object.entries(settings.value?.workday_overrides || {}).sort(([a], [b]) => a.localeCompare(b)).map(([date, working]) => ({ date, working })))
function editUser(user?: User) {
  editing.value = user || null
  Object.assign(userForm, user ? { name: user.name, role: user.role, active: !!user.active, wecom_user_id: user.wecom_user_id || '' } :
    { name: '', role: 'member', active: true, wecom_user_id: '' })
  error.value = ''; userDialog.value = true
}
function previewUser() {
  if (!userForm.name.trim()) { error.value = '请填写成员名称'; return }
  if (!/^[\w.@-]*$/.test(userForm.wecom_user_id)) { error.value = '企微 UserID 仅支持字母、数字、下划线、点、@ 和短横线'; return }
  const form = { name: userForm.name.trim(), active: userForm.active, wecom_user_id: userForm.wecom_user_id.trim() }
  const original = editing.value ? { name: editing.value.name, active: !!editing.value.active, wecom_user_id: editing.value.wecom_user_id || '' } : {}
  const data = editing.value ? changedFields(original, form, ['name', 'active', 'wecom_user_id']) : { name: form.name, role: userForm.role, wecom_user_id: form.wecom_user_id }
  if (!Object.keys(data).length) { error.value = '没有需要修改的内容'; return }
  const names: Record<string, string> = { name: '姓名', active: '启用账号', role: '角色', wecom_user_id: '企业微信 UserID' }
  const render = (value: unknown, key: string) => key === 'role' ? roleLabels[value as Role] : typeof value === 'boolean' ? value ? '启用' : '停用' : String(value || '未绑定')
  review.value = {
    title: editing.value ? `修改成员：${editing.value.name}` : '创建成员', method: editing.value ? 'PATCH' : 'POST',
    path: editing.value ? `/api/users/${editing.value.id}` : '/api/users', body: data,
    rows: Object.entries(data).map(([key, value]) => ({ label: names[key] || key,
      before: editing.value ? render((original as Record<string, unknown>)[key], key) : '—', after: render(value, key) })),
  }
}
async function loadSettings() {
  try { const value = await api<Settings>('/api/settings'); settings.value = structuredClone(value); originalSettings.value = structuredClone(value) }
  catch (e) { error.value = errorText(e) }
}
function addDay() {
  if (!day.value || !settings.value) return
  settings.value.workday_overrides[day.value] = workday.value; day.value = ''
}
function previewSettings() {
  const value = settings.value, old = originalSettings.value
  if (!value || !old) return
  if (value.end_hour <= value.start_hour) { error.value = '发送结束时间必须晚于开始时间'; return }
  const rows: Review['rows'] = []
  for (const [key, label] of [['start_hour', '发送开始时刻'], ['end_hour', '发送结束时刻'], ['due_hour', '计划截止时刻']] as const) {
    if (value[key] !== old[key]) rows.push({ label, before: `${old[key]}:00`, after: `${value[key]}:00` })
  }
  const dates = new Set([...Object.keys(old.workday_overrides), ...Object.keys(value.workday_overrides)])
  const labelDay = (v: boolean | undefined) => v === undefined ? '按周一至周五默认日历' : v ? '工作日' : '休息日'
  for (const date of dates) if (old.workday_overrides[date] !== value.workday_overrides[date]) rows.push({ label: date,
    before: labelDay(old.workday_overrides[date]), after: labelDay(value.workday_overrides[date]) })
  if (!rows.length) { ElMessage.info('没有需要修改的设置'); return }
  review.value = { title: '更新提醒与工作日历设置', path: '/api/settings', method: 'PUT', body: structuredClone(toRaw(value)), rows }
}
async function confirm() {
  if (!review.value || busy.value) return
  busy.value = true; error.value = ''
  const action = review.value
  try {
    const result = await api<{ message?: string }>(action.path, action.method, action.body)
    review.value = null; userDialog.value = false; ElMessage.success(result.message || '已确认并保存')
    if (action.path === '/api/settings') await loadSettings()
    emit('refresh')
  } catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
onMounted(loadSettings)
</script>
<template>
  <div class="page-heading"><div><p class="eyebrow">ADMIN / 管理</p><h1>成员与提醒设置</h1><p class="muted">维护项目责任人和提醒时间。公司内部共享编辑，无需账号权限或绑定。</p></div><el-button type="primary" @click="editUser()">＋ 新增成员</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false" class="section-gap" />
  <section class="panel"><h2>项目责任人</h2><el-table :data="users" empty-text="暂无成员">
    <el-table-column prop="name" label="姓名" min-width="120" /><el-table-column v-if="!actor.internal_shared" label="角色" width="130"><template #default="{ row }">{{ roleLabels[row.role as Role] }}</template></el-table-column>
    <el-table-column v-if="!actor.internal_shared" label="状态" width="100"><template #default="{ row }"><span class="badge" :class="row.active ? 'state-active' : 'state-cancelled'">{{ row.active ? '启用' : '停用' }}</span></template></el-table-column>
    <el-table-column v-if="!actor.internal_shared" label="企业微信 UserID" min-width="160"><template #default="{ row }">{{ row.wecom_user_id || '未绑定' }}</template></el-table-column>
    <el-table-column label="操作" width="120"><template #default="{ row }"><el-button text type="primary" @click="editUser(row)">编辑</el-button></template></el-table-column>
  </el-table><p class="fine-print">大屏直接打开，展示已开启“大屏可见”的项目。<a href="#display" target="_blank" rel="noopener noreferrer">打开大屏 ↗</a></p></section>
  <section v-if="settings" class="panel section-gap"><h2>提醒与工作日历</h2><p class="muted">时区：Asia/Shanghai。默认周一至周五为工作日；法定节假日和调休需要手工配置。</p>
    <el-form label-position="top"><div class="form-grid three"><el-form-item label="发送开始时刻（整点）"><el-input-number v-model="settings.start_hour" :min="0" :max="22" :precision="0" /></el-form-item><el-form-item label="发送结束时刻（整点）"><el-input-number v-model="settings.end_hour" :min="1" :max="23" :precision="0" /></el-form-item><el-form-item label="计划截止时刻（整点）"><el-input-number v-model="settings.due_hour" :min="0" :max="23" :precision="0" /></el-form-item></div>
    <div class="calendar-controls"><el-date-picker v-model="day" value-format="YYYY-MM-DD" placeholder="选择覆盖日期" aria-label="日历覆盖日期" /><el-switch v-model="workday" active-text="工作日" inactive-text="休息日" /><el-button :disabled="!day" @click="addDay">添加 / 更新日期</el-button></div>
    <el-table :data="calendar" max-height="280" empty-text="暂无覆盖，采用周一至周五默认日历"><el-table-column prop="date" label="日期" /><el-table-column label="配置"><template #default="{ row }">{{ row.working ? '工作日' : '休息日' }}</template></el-table-column><el-table-column label="操作" width="120"><template #default="{ row }"><el-button text @click="delete settings.workday_overrides[row.date]">移除覆盖</el-button></template></el-table-column></el-table>
    <div class="form-footer"><el-button @click="loadSettings">重置未保存修改</el-button><el-button type="primary" @click="previewSettings">预览设置变更</el-button></div></el-form>
  </section>
  <section class="panel section-gap"><h2>集成状态</h2><div class="integration-grid"><div><span class="muted">自然语言模型</span><p>{{ status?.ai_configured ? `已配置：${status.ai_model}` : '尚未配置' }}</p></div><div><span class="muted">企微群消息接收</span><p>{{ status?.message || '正在读取连接状态' }}</p></div><div><span class="muted">企微主动发送开关</span><p>{{ status?.wecom_send_enabled ? '已开启，实际送达请查看记录' : '未开启' }}</p></div></div><p class="muted">在白名单群内直接 @机器人提交文字，无需绑定；信息齐全自动保存，缺项只返回补充提示。</p></section>
  <el-dialog v-model="userDialog" :title="editing ? '编辑成员' : '新增成员'" width="min(520px, 94vw)" :close-on-click-modal="false"><el-form label-position="top"><el-form-item label="姓名" required><el-input v-model="userForm.name" maxlength="100" /></el-form-item><el-form-item v-if="!editing && !actor.internal_shared" label="角色" required><el-select v-model="userForm.role"><el-option v-for="(label, value) in roleLabels" :key="value" :label="label" :value="value" /></el-select></el-form-item><el-form-item v-if="editing && !actor.internal_shared" label="账号状态"><el-switch v-model="userForm.active" active-text="启用" inactive-text="停用" :disabled="editing.id === actor.id" /></el-form-item><el-form-item v-if="!actor.internal_shared" label="企业微信 UserID"><el-input v-model="userForm.wecom_user_id" maxlength="100" placeholder="可暂不绑定；留空保存将解除原绑定" /></el-form-item><el-alert v-if="error" :title="error" type="error" :closable="false" /></el-form><template #footer><el-button @click="userDialog = false">返回</el-button><el-button type="primary" @click="previewUser">预览变更</el-button></template></el-dialog>
  <el-dialog :model-value="!!review" :title="review?.title + ' · 操作预览'" width="min(680px, 94vw)" :close-on-click-modal="false" :show-close="!busy" :close-on-press-escape="!busy" @close="review = null"><el-alert title="核对以下变更，点击确认后立即保存。" type="info" :closable="false" class="section-gap" /><el-table :data="review?.rows" border><el-table-column prop="label" label="字段" /><el-table-column prop="before" label="原值" /><el-table-column prop="after" label="新值" /></el-table><el-alert v-if="error" :title="error" type="error" :closable="false" class="section-gap" /><template #footer><el-button :disabled="busy" @click="review = null">返回修改</el-button><el-button type="primary" :loading="busy" @click="confirm">确认保存</el-button></template></el-dialog>
</template>
