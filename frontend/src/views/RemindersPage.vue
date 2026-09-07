<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { Actor, Project, Reminder } from '../types'
import { api } from '../api'
import { dateTime, errorText } from '../domain'
import StateBadge from '../components/StateBadge.vue'

const props = defineProps<{ actor: Actor; projects: Project[] }>()
const reminders = ref<Reminder[]>([]), loading = ref(false), error = ref(''), statusFilter = ref('')
const labels: Record<string, string> = { queued: '等待发送', blocked: '发送受阻', failed: '发送失败', sending: '发送中', uncertain: '结果不确定', accepted: '平台已接受', cancelled: '已取消' }
const filtered = computed(() => reminders.value.filter(r => !statusFilter.value || r.status === statusFilter.value))
function projectFor(row: Reminder) { return props.projects.find(p => p.id === String(row.project_id)) }
async function load() {
  loading.value = true; error.value = ''
  try { reminders.value = await api<Reminder[]>('/api/reminders') }
  catch (e) { error.value = errorText(e) }
  finally { loading.value = false }
}
async function scan() {
  try { await ElMessageBox.confirm('将按当前项目状态检查并更新提醒队列。本次操作不会发送外部消息。', '检查提醒 · 操作预览', { confirmButtonText: '确认检查', cancelButtonText: '返回' }) }
  catch { return }
  try { const result = await api<{ message: string; count: number }>('/api/reminders/scan', 'POST'); ElMessage.success(`${result.message}（${result.count} 条）`); await load() }
  catch (e) { ElMessage.error(errorText(e)) }
}
async function resolve(row: Reminder) {
  try { await ElMessageBox.confirm(`记录 ${row.id} 的发送结果将由“不确定”改为“平台已接受”。仅在已人工核实平台接收结果后确认；这不代表员工已读。`, '人工核对 · 操作预览', { confirmButtonText: '已核实，确认修改', cancelButtonText: '返回', type: 'warning' }) }
  catch { return }
  try { const result = await api<{ message: string }>(`/api/reminders/${encodeURIComponent(row.id)}/resolve`, 'POST'); ElMessage.success(result.message); await load() }
  catch (e) { ElMessage.error(errorText(e)) }
}
onMounted(load)
</script>
<template>
  <div class="page-heading"><div><p class="eyebrow">REMINDERS / 提醒</p><h1>及时发现需要回应的节点</h1><p class="muted">{{ (actor.internal_shared || actor.role === 'admin') ? '查看提醒队列和异常记录。' : '查看发送给自己的提醒记录。' }} 平台接受不代表员工已读。</p></div><el-button v-if="(actor.internal_shared || actor.role === 'admin')" @click="scan">检查并更新队列</el-button></div>
  <section class="panel"><div class="section-heading"><el-select v-model="statusFilter" placeholder="全部发送状态" clearable aria-label="筛选发送状态"><el-option v-for="(label, value) in labels" :key="value" :label="label" :value="value" /></el-select><el-button :loading="loading" @click="load">刷新记录</el-button></div><el-alert v-if="error" :title="error" type="error" :closable="false" class="section-gap" />
    <el-table :data="filtered" v-loading="loading" class="section-gap" empty-text="暂无提醒记录">
      <el-table-column label="项目 / 目标" min-width="200"><template #default="{ row }"><strong>{{ projectFor(row)?.name || `项目 ${row.project_id}` }}</strong><p class="muted">{{ projectFor(row)?.milestones.find(n => n.id === row.milestone_id)?.name || row.milestone_id }}</p></template></el-table-column>
      <el-table-column prop="owner_name" label="接收人" width="100" />
      <el-table-column label="原因" min-width="170"><template #default="{ row }"><StateBadge :flags="row.reasons" /></template></el-table-column>
      <el-table-column label="发送状态" min-width="140"><template #default="{ row }"><span class="badge" :class="['blocked','failed','uncertain'].includes(row.status) ? 'risk-overdue' : ''">{{ labels[row.status] || row.status }}</span><p class="muted">尝试 {{ row.attempts }} 次</p></template></el-table-column>
      <el-table-column label="最近更新 / 说明" min-width="220"><template #default="{ row }"><span>{{ dateTime(row.updated_at) }}</span><p class="muted">{{ row.detail || '—' }}</p></template></el-table-column>
      <el-table-column v-if="(actor.internal_shared || actor.role === 'admin')" label="处理" width="130"><template #default="{ row }"><el-button v-if="row.status === 'uncertain'" text type="primary" @click="resolve(row)">人工核对</el-button></template></el-table-column>
    </el-table>
  </section>
</template>
