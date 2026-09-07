<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import type { Draft, Project, User } from '../types'
import { fieldLabels, intentLabels, dateTime, previewRows } from '../domain'
import FieldValue from './FieldValue.vue'

const props = defineProps<{ draft: Draft; projects: Project[]; users: User[]; busy: boolean }>()
defineEmits<{ close: []; cancel: [] }>()
const now = ref(Date.now())
const timer = window.setInterval(() => { now.value = Date.now() }, 1000)
onBeforeUnmount(() => window.clearInterval(timer))
const current = computed(() => props.projects.find(p => p.id === props.draft.action.project_id || p.code === props.draft.action.project_id))
const before = computed(() => props.draft.before || current.value)
const target = computed(() => props.draft.action.milestone_id ? before.value?.milestones.find(n => n.id === props.draft.action.milestone_id) : before.value)
const expired = computed(() => Date.parse(props.draft.expires_at) <= now.value)
const conflict = computed(() => props.draft.expected_version != null && current.value?.version !== props.draft.expected_version)
const rows = computed(() => previewRows(props.draft.action.data, target.value as unknown as Record<string, unknown> | undefined))
const diagnostics = computed(() => [...(props.draft.diagnostics?.missing_fields || []), ...(props.draft.diagnostics?.ambiguities || [])])
</script>
<template>
  <el-dialog :model-value="true" :title="`${intentLabels[draft.action.intent]} · 录入详情`" width="min(860px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @close="$emit('close')">
    <div class="draft-heading"><div><strong>{{ draft.preview?.name || current?.name || '待补充项目' }}</strong><p class="muted">{{ draft.action.project_id ? current?.code || draft.action.project_id : '新项目' }}<span v-if="draft.action.milestone_id"> / {{ target?.name || draft.action.milestone_id }}</span></p></div><span class="badge">{{ draft.status === 'pending' ? '待处理' : draft.status === 'needs_input' ? '待补充' : draft.status === 'confirmed' ? '已保存' : draft.status === 'cancelled' ? '已取消' : '已过期' }}</span></div>
    <el-alert v-if="expired && draft.status === 'pending'" title="草稿已过期，请重新提交信息。" type="error" :closable="false" />
    <el-alert v-else-if="conflict && draft.status === 'pending'" title="项目版本已变化或当前项目不可访问，请刷新数据并重新生成草稿。" type="warning" :closable="false" />
    <el-alert v-else-if="draft.status === 'pending'" title="旧草稿尚未保存，请在草稿列表补充说明并重新提交；校验完整后自动保存。" type="info" :closable="false" />
    <el-alert v-if="draft.status === 'needs_input'" title="草稿已保存，关闭页面不会丢失；可随时在草稿列表通过补充说明继续录入。" type="info" :closable="false" class="section-gap" />
    <el-alert v-if="diagnostics.length" type="warning" :closable="false" class="section-gap"><ul><li v-for="item in diagnostics" :key="item">{{ item }}</li></ul></el-alert>
    <p class="muted">草稿 {{ draft.id.slice(0, 8) }} · {{ draft.status === 'needs_input' ? '持续保留，待补充' : `截止 ${dateTime(draft.expires_at)}` }}<span v-if="draft.expected_version"> · 基于版本 {{ draft.expected_version }}</span></p>
    <p class="muted">计划截止日期统一按当天 {{ String(draft.due_hour).padStart(2, '0') }}:00（Asia/Shanghai）计算；预计完成日期不改变计划截止。</p>
    <el-table :data="rows" border class="section-gap">
      <el-table-column label="字段" width="145"><template #default="{ row }">{{ fieldLabels[row.key] || row.key }}</template></el-table-column>
      <el-table-column v-if="draft.action.intent !== 'create_project' && draft.action.intent !== 'add_milestone'" label="原值"><template #default="{ row }"><FieldValue :value="row.before" :field="row.key" :users="users" /></template></el-table-column>
      <el-table-column :label="draft.status === 'confirmed' ? '已保存的值' : '已填写的值'"><template #default="{ row }"><span v-if="row.cleared">{{ draft.action.data.historical ? '记录清除请求（不改当前值）' : '明确清除' }}</span><FieldValue v-else :value="row.value" :field="row.key" :users="users" /></template></el-table-column>
    </el-table>
    <div v-if="draft.action.intent === 'create_project' && draft.preview" class="section-gap">
      <h3>项目目标（{{ draft.preview.milestones.length }}）</h3>
      <div v-for="(node, index) in draft.preview.milestones" :key="node.id" class="preview-node"><strong>{{ index + 1 }}. {{ node.name }}</strong><p>{{ node.criterion }}</p><p class="muted"><FieldValue :value="node.owner_id" field="owner_id" :users="users" /> · {{ node.start_date }} 至 {{ node.due_date }} · 每 {{ node.update_interval }} 个工作日汇报</p></div>
    </div>
    <el-alert v-if="draft.action.data.progress === 100" title="100% 进度不等于完成验收；达到完成标准后，请另行确认目标完成。" type="warning" :closable="false" class="section-gap" />
    <el-alert v-if="draft.action.data.historical" title="这是历史补录，只追加历史记录，不改变当前进度或重置待更新时间。" type="info" :closable="false" class="section-gap" />
    <el-collapse v-if="draft.source_text || Object.keys(draft.diagnostics?.evidence || {}).length">
      <el-collapse-item title="查看原始输入与提取依据"><p class="field-value">{{ draft.source_text }}</p><p v-for="(value, field) in draft.diagnostics?.evidence" :key="field"><strong>{{ fieldLabels[field] || field }}：</strong>{{ value }}</p></el-collapse-item>
    </el-collapse>
    <template #footer><el-button :disabled="busy" @click="$emit('close')">关闭</el-button><el-button v-if="['pending', 'needs_input'].includes(draft.status)" :disabled="busy" @click="$emit('cancel')">取消此草稿</el-button></template>
  </el-dialog>
</template>
