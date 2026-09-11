<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { changedFields, editableStates, errorText, intentLabels, reportData, stateLabels, today } from '../domain'
import type { ReportForm } from '../domain'
import type { Action, Actor, Draft, Intent, Milestone, Project, State, User } from '../types'
import NodeFields from './NodeFields.vue'
import type { NodeForm } from './NodeFields.vue'

const props = defineProps<{ intent: Intent; actor: Actor; project?: Project; node?: Milestone; users: User[] }>()
const emit = defineEmits<{ close: []; draft: [draft: Draft] }>()
const busy = ref(false), error = ref('')
const isProject = computed(() => ['create_project', 'edit_project'].includes(props.intent))
const isNode = computed(() => ['add_milestone', 'edit_milestone'].includes(props.intent))
const isStatus = computed(() => ['project_status', 'milestone_status'].includes(props.intent))
const directory = ref<User[]>([])
const availableUsers = computed(() => [...new Map([...props.users, ...directory.value].map(u => [u.id, u])).values()].filter(u => u.active !== false && u.active !== 0 && u.role !== 'display'))
onMounted(async () => {
  if (props.intent !== 'edit_project' || !props.project) return
  try { directory.value = await api<User[]>(`/api/users?project_id=${encodeURIComponent(props.project.id)}`) }
  catch (e) { error.value = `成员目录读取失败：${errorText(e)}` }
})
const projectForm = reactive({
  owner_name: props.project?.owner_name === '待明确' ? '' : props.project?.owner_name || '',
  name: props.project?.name || '', description: props.project?.description || '', owner_id: props.project?.owner_id || (props.actor.internal_shared ? '' : props.actor.id),
  contact_company: props.project?.contact_company || '', contact_name: props.project?.contact_name || '',
  contact_info: props.project?.contact_info || '',
  owner_roles: { ...(props.project?.owner_roles || {}) },
  member_ids: [...(props.project?.member_ids || [props.actor.id])], start_date: props.project?.start_date || '',
  due_date: props.project?.due_date || '', display_visible: props.project?.display_visible ?? true,
})
const originalProject = JSON.parse(JSON.stringify(projectForm)) as Record<string, unknown>
function emptyNode(): NodeForm { return { name: '', criterion: '', owner_id: props.actor.id, start_date: projectForm.start_date,
  due_date: projectForm.due_date, update_interval: 2 } }
const nodeForm = ref<NodeForm>(props.node ? { name: props.node.name, criterion: props.node.criterion, owner_id: props.node.owner_id || '',
  start_date: props.node.start_date, due_date: props.node.due_date, update_interval: props.node.update_interval || 2 } : emptyNode())
const originalNode = JSON.parse(JSON.stringify(nodeForm.value)) as Record<string, unknown>
const nodes = ref<NodeForm[]>([emptyNode()])
const report = reactive<ReportForm>({ summary: '', progress_enabled: false, progress: props.node?.progress || 0,
  blocker: '', next_step: '', expected_date: '', clear_fields: [], historical: false, event_date: '' })
const reason = ref(''), state = ref<State>('active')
const currentState = computed(() => props.node?.status || props.project?.status)
const manager = computed(() => props.actor.internal_shared || props.actor.role === 'admin' || props.project?.owner_id === props.actor.id)
const states = computed(() => editableStates.filter(s => s !== currentState.value &&
  (props.intent === 'project_status' || manager.value || s === 'completed')))
if (states.value.length) state.value = states.value[0]!
const memberOptions = computed(() => props.intent === 'create_project' && !props.actor.internal_shared && props.actor.role !== 'admin' ? availableUsers.value.filter(u => u.id === props.actor.id) : availableUsers.value)
const nodeUsers = computed(() => {
  const ids = isProject.value ? [...projectForm.member_ids, projectForm.owner_id] : props.project?.member_ids || []
  return memberOptions.value.filter(u => ids.includes(u.id))
})
function requireText(value: string, name: string) { if (!value?.trim()) throw new Error(`请填写${name}`) }
function validateNode(node: NodeForm) {
  requireText(node.name, '事项名称')
}
async function preview() {
  if (busy.value) return
  error.value = ''
  try {
    let data: Record<string, unknown>
    if (isProject.value) {
      requireText(projectForm.name, '项目名称')
      projectForm.owner_roles = Object.fromEntries(Object.entries(projectForm.owner_roles).filter(([id, role]) => role && [...projectForm.member_ids, projectForm.owner_id].includes(id)))
      if (props.intent === 'create_project') {
        const filledNodes = nodes.value.filter(node => node.name.trim())
        filledNodes.forEach(validateNode)
        const provided = (value: Record<string, unknown>) => Object.fromEntries(Object.entries(value).filter(([key, v]) =>
          !['owner_id', 'owner_name', 'start_date', 'due_date'].includes(key) || !!v))
        data = provided({ ...projectForm, member_ids: [...new Set([...projectForm.member_ids, projectForm.owner_id].filter(Boolean))],
          milestones: filledNodes.map(node => provided({ ...node })) })
        if (props.actor.internal_shared && projectForm.owner_name.trim()) delete data.owner_id
      } else data = { ...changedFields(originalProject, { ...projectForm }, Object.keys(originalProject)), reason: reason.value.trim() }
    } else if (isNode.value) {
      validateNode(nodeForm.value)
      data = props.intent === 'add_milestone' ? { ...nodeForm.value } :
        { ...changedFields(originalNode, { ...nodeForm.value }, Object.keys(originalNode)), reason: reason.value.trim() }
    } else if (isStatus.value) data = { status: state.value, reason: reason.value.trim() }
    else {
      requireText(report.summary, '进展说明')
      if (report.historical) requireText(report.event_date, '历史发生日期')
      data = reportData(report)
    }
    if (['edit_project', 'edit_milestone', 'project_status', 'milestone_status'].includes(props.intent)) requireText(reason.value, '变更原因')
    const action: Action = { intent: props.intent, data,
      ...(props.project ? { project_id: props.project.id } : {}), ...(props.node ? { milestone_id: props.node.id } : {}) }
    busy.value = true
    const draft = await api<Draft>('/api/drafts', 'POST', action)
    emit('draft', draft); emit('close')
  } catch (e) { error.value = errorText(e) }
  finally { busy.value = false }
}
</script>
<template>
  <el-dialog :model-value="true" :title="intentLabels[intent]" width="min(760px, 94vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @close="$emit('close')">
    <p v-if="project" class="form-context">{{ project.code }} · {{ project.name }}<span v-if="node"> / {{ node.name }}</span></p>
    <el-form label-position="top" @submit.prevent="preview">
      <template v-if="isProject">
        <el-form-item label="项目名称" required><el-input v-model="projectForm.name" maxlength="100" /></el-form-item>
        <el-form-item label="项目说明"><el-input v-model="projectForm.description" type="textarea" :rows="2" maxlength="2000" /></el-form-item>
        <div class="form-grid"><el-form-item label="对接单位（选填）"><el-input v-model="projectForm.contact_company" maxlength="100" placeholder="客户或合作单位" /></el-form-item><el-form-item label="对接人（选填）"><el-input v-model="projectForm.contact_name" maxlength="100" placeholder="姓名或称呼" /></el-form-item></div>
        <el-form-item label="联系方式（选填）"><el-input v-model="projectForm.contact_info" maxlength="200" placeholder="电话、微信或邮箱" /></el-form-item>
        <div class="form-grid"><el-form-item label="项目负责人（选填）"><el-input v-if="actor.internal_shared" v-model="projectForm.owner_name" maxlength="100" placeholder="直接填写姓名，无需账号" /><el-select v-else v-model="projectForm.owner_id" filterable clearable><el-option v-for="u in memberOptions" :key="u.id" :label="u.name" :value="u.id" /></el-select></el-form-item><el-form-item label="项目成员"><el-select v-model="projectForm.member_ids" multiple filterable><el-option v-for="u in memberOptions" :key="u.id" :label="u.name" :value="u.id" /></el-select></el-form-item></div>
        <div class="form-grid"><el-form-item v-for="user in nodeUsers" :key="user.id" :label="`${user.name} · 分工`"><el-select v-model="projectForm.owner_roles[user.id]" placeholder="未指定" clearable><el-option v-for="role in ['A角', 'B角', 'A1', 'A2', '主责', '搭档']" :key="role" :label="role" :value="role" /></el-select></el-form-item></div>
        <div class="form-grid"><el-form-item label="计划开始日期（选填）"><el-date-picker v-model="projectForm.start_date" value-format="YYYY-MM-DD" /></el-form-item><el-form-item label="计划截止日期（选填）"><el-date-picker v-model="projectForm.due_date" value-format="YYYY-MM-DD" /></el-form-item></div>
        <el-form-item label="公开展示"><el-switch v-model="projectForm.display_visible" active-text="在公司大屏展示项目与全部目标详情" /></el-form-item>
        <p v-if="projectForm.display_visible" class="warning-text">完整保存后，大屏会展示项目进展、阻碍与下一步。</p>
        <template v-if="intent === 'create_project'">
          <div class="section-heading"><h3>目标节点</h3><el-button :disabled="nodes.length >= 50" @click="nodes.push(emptyNode())">添加目标</el-button></div>
          <div v-for="(_, index) in nodes" :key="index" class="node-form"><div class="section-heading"><strong>目标 {{ index + 1 }}</strong><el-button text type="danger" :disabled="nodes.length === 1" @click="nodes.splice(index, 1)">移除</el-button></div><NodeFields v-model="nodes[index]!" :users="nodeUsers" /></div>
        </template>
      </template>
      <NodeFields v-if="isNode" v-model="nodeForm" :users="nodeUsers" />
      <template v-if="intent === 'report_progress'">
        <el-alert title="可选字段留空会保留当前值；需要移除信息时，请勾选明确清除。" type="info" :closable="false" class="section-gap" />
        <el-form-item label="本次进展说明" required><el-input v-model="report.summary" type="textarea" :rows="3" maxlength="2000" placeholder="完成了什么，或说明仍在等待的原因" /></el-form-item>
        <el-form-item label="进度"><el-checkbox v-model="report.progress_enabled">本次明确更新百分比</el-checkbox><el-input-number v-if="report.progress_enabled" v-model="report.progress" :min="0" :max="100" :precision="0" /></el-form-item>
        <el-alert v-if="report.progress_enabled && report.progress === 100" title="百分比 100% 不会自动完成节点。达到完成标准后，请另行操作“确认完成”。" type="warning" :closable="false" class="section-gap" />
        <el-form-item label="当前阻碍"><el-input v-model="report.blocker" type="textarea" :disabled="report.clear_fields.includes('blocker')" maxlength="2000" :placeholder="node?.blocker ? `保留当前：${node.blocker}` : '留空表示不修改'" /></el-form-item>
        <el-form-item label="下一步"><el-input v-model="report.next_step" type="textarea" :disabled="report.clear_fields.includes('next_step')" maxlength="2000" placeholder="留空表示不修改" /></el-form-item>
        <el-form-item label="预计完成日期"><el-date-picker v-model="report.expected_date" value-format="YYYY-MM-DD" :disabled="report.clear_fields.includes('expected_date')" placeholder="不会修改计划截止日期" /></el-form-item>
        <el-form-item label="明确清除已有信息"><el-checkbox-group v-model="report.clear_fields"><el-checkbox value="blocker">清除阻碍</el-checkbox><el-checkbox value="next_step">清除下一步</el-checkbox><el-checkbox value="expected_date">清除预计完成日期</el-checkbox></el-checkbox-group></el-form-item>
        <el-form-item><el-switch v-model="report.historical" active-text="补录历史进展（不改变当前进度和汇报计时）" @change="report.event_date = ''" /></el-form-item>
        <el-form-item v-if="report.historical" label="历史发生日期" required><el-date-picker v-model="report.event_date" value-format="YYYY-MM-DD" :disabled-date="(date: Date) => date.getTime() > new Date(`${today()}T23:59:59+08:00`).getTime()" /></el-form-item>
      </template>
      <template v-if="isStatus">
        <el-form-item label="变更后的状态" required><el-select v-model="state"><el-option v-for="s in states" :key="s" :value="s" :label="stateLabels[s]" /></el-select></el-form-item>
        <el-alert v-if="state === 'completed'" :title="node ? `请核对完成标准：${node.criterion}` : '项目完成需要所有未取消的目标均已完成。'" type="warning" :closable="false" class="section-gap" />
        <el-alert v-if="state === 'paused'" title="暂停不会自动顺延计划截止日期。" type="info" :closable="false" class="section-gap" />
      </template>
      <el-form-item v-if="['edit_project', 'edit_milestone', 'project_status', 'milestone_status'].includes(intent)" :label="isStatus && state === 'paused' ? '暂停原因' : '变更原因'" required><el-input v-model="reason" type="textarea" :rows="2" maxlength="1000" placeholder="说明调整或确认的依据" /></el-form-item>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    </el-form>
    <template #footer><el-button :disabled="busy" @click="$emit('close')">返回</el-button><el-button type="primary" :loading="busy" @click="preview">保存</el-button></template>
  </el-dialog>
</template>
