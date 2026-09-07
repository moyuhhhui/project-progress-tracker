<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Actor, History, Intent, Milestone, Project, User } from '../types'
import { api } from '../api'
import { dateTime, errorText, fieldLabels, intentLabels } from '../domain'
import StateBadge from './StateBadge.vue'
import FieldValue from './FieldValue.vue'
import OwnerRoles from './OwnerRoles.vue'

const props = defineProps<{ project: Project; actor: Actor; users: User[] }>()
defineEmits<{ close: []; edit: [intent: Intent, node?: Milestone] }>()
const manager = computed(() => props.actor.internal_shared || props.actor.role === 'admin' || props.project.owner_id === props.actor.id)
const ended = computed(() => ['completed', 'cancelled'].includes(props.project.status))
const history = ref<History | null>(null), loading = ref(false), error = ref('')
function canReport(node: Milestone) { return !ended.value && props.project.status !== 'paused' &&
  !['completed', 'cancelled', 'paused'].includes(node.status) && (manager.value || node.owner_id === props.actor.id) }
function canChangeStatus(node: Milestone) { return !ended.value && (manager.value ||
  node.owner_id === props.actor.id && !['completed', 'cancelled'].includes(node.status)) }
async function loadHistory() {
  loading.value = true; error.value = ''
  try { history.value = await api<History>(`/api/projects/${props.project.id}/history`) }
  catch (e) { error.value = errorText(e) }
  finally { loading.value = false }
}
watch(() => [props.project.id, props.project.version], loadHistory, { immediate: true })
</script>
<template>
  <el-drawer :model-value="true" :title="`${project.code} · 项目详情`" size="min(1000px, 98vw)" @close="$emit('close')">
    <div class="detail-heading"><h1>{{ project.name }}</h1><StateBadge :state="project.status" :flags="project.flags" :reason="project.pause_reason" /><p class="field-value muted">{{ project.description || '暂无说明' }}</p></div>
    <h2>项目信息</h2>
    <div class="detail-facts"><div><span>负责人 / 分工</span><strong><OwnerRoles :project="project" :users="users" /></strong></div><div><span>开始日期</span><strong>{{ project.start_date }}</strong></div><div><span>当前截止</span><strong>{{ project.due_date }}</strong></div><div><span>原始截止</span><strong>{{ project.original_due_date }}</strong></div><div><span>总体进度</span><strong>{{ project.progress == null ? '—' : `${project.progress}%` }}</strong></div><div><span>公开展示</span><strong>{{ project.display_visible ? '大屏可见' : '关闭' }}</strong></div></div>
    <p class="muted">项目成员：<FieldValue :value="project.member_ids" field="member_ids" :users="users" /></p>
    <el-descriptions title="对接信息" :column="1" border class="section-gap"><el-descriptions-item label="对接单位">{{ project.contact_company || '未填写' }}</el-descriptions-item><el-descriptions-item label="对接人">{{ project.contact_name || '未填写' }}</el-descriptions-item><el-descriptions-item label="联系方式"><span class="field-value">{{ project.contact_info || '未填写' }}</span></el-descriptions-item></el-descriptions>
    <div v-if="manager" class="detail-actions"><el-button :disabled="ended" @click="$emit('edit', 'edit_project')">编辑项目信息</el-button><el-button :disabled="ended" @click="$emit('edit', 'add_milestone')">添加项目事项</el-button><el-button @click="$emit('edit', 'project_status')">变更项目状态</el-button></div>
    <el-tabs>
      <el-tab-pane :label="`项目事项（${project.milestones.length}）`">
        <article v-for="(node, index) in project.milestones" :key="node.id" class="milestone-card">
          <div class="section-heading"><h3><span class="node-number">{{ String(index + 1).padStart(2, '0') }}</span>{{ node.name }}</h3><StateBadge :state="node.status" :flags="node.flags" :reason="node.pause_reason" /></div>
          <p class="criterion"><strong>完成标准</strong> {{ node.criterion }}</p>
          <el-progress :percentage="node.progress" :stroke-width="8" />
          <div class="node-dates"><span>{{ node.owner_name }}</span><span>{{ node.start_date }} → {{ node.due_date }}</span><span>原计划 {{ node.original_due_date }}</span><span>每 {{ node.update_interval }} 个工作日更新</span></div>
          <dl class="node-detail"><div><dt>最新进展</dt><dd>{{ node.summary || '尚未汇报' }}</dd></div><div><dt>当前阻碍</dt><dd :class="{ 'warning-text': node.blocker }">{{ node.blocker || '暂无已记录阻碍' }}</dd></div><div><dt>下一步</dt><dd>{{ node.next_step || '暂无安排' }}</dd></div><div><dt>预计完成</dt><dd>{{ node.expected_date || '未填写' }}</dd></div></dl>
          <div class="milestone-footer"><small class="muted">最后有效汇报 {{ dateTime(node.last_report_at) }}</small><div class="inline"><el-button v-if="manager && !ended" size="small" @click="$emit('edit', 'edit_milestone', node)">调整计划</el-button><el-button v-if="canChangeStatus(node)" size="small" @click="$emit('edit', 'milestone_status', node)">{{ manager ? '变更状态' : '确认完成' }}</el-button><el-button v-if="canReport(node)" type="primary" size="small" @click="$emit('edit', 'report_progress', node)">汇报进度</el-button></div></div>
        </article>
      </el-tab-pane>
      <el-tab-pane label="进展与操作历史">
        <div class="section-heading"><p class="muted">最近 100 条进展与 100 条操作；历史记录保留原始值。</p><el-button text :loading="loading" @click="loadHistory">刷新历史</el-button></div>
        <el-alert v-if="error" :title="error" type="error" :closable="false" />
        <el-empty v-if="history && !history.reports.length && !history.audit.length" description="暂无历史记录" />
        <h3 v-if="history?.reports.length">进展汇报</h3>
        <article v-for="report in history?.reports" :key="report.id" class="history-card"><div class="section-heading"><strong>{{ project.milestones.find(n => n.id === report.milestone_id)?.name || '项目事项' }}</strong><small>{{ dateTime(report.at) }} · {{ report.actor_name }}</small></div><span v-if="report.data.historical" class="badge">历史补录 {{ report.data.event_date }}</span><p class="field-value">{{ report.data.summary }}</p><dl class="node-detail"><div v-for="(value, field) in report.data" v-show="!['summary', 'historical', 'event_date'].includes(String(field))" :key="field"><dt>{{ fieldLabels[field] || field }}</dt><dd><FieldValue :value="value" :field="String(field)" :users="users" /></dd></div></dl></article>
        <h3 v-if="history?.audit.length">操作审计</h3>
        <el-collapse><el-collapse-item v-for="entry in history?.audit" :key="entry.id" :title="`${intentLabels[entry.intent]} · ${entry.actor_name} · ${dateTime(entry.at)}`"><p class="muted">操作前后完整快照</p><div class="audit-grid"><div><strong>操作前</strong><pre>{{ JSON.stringify(entry.before_data, null, 2) }}</pre></div><div><strong>操作后</strong><pre>{{ JSON.stringify(entry.after_data, null, 2) }}</pre></div></div></el-collapse-item></el-collapse>
      </el-tab-pane>
    </el-tabs>
  </el-drawer>
</template>
