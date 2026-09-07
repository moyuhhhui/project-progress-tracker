<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Draft, IntegrationStatus, MessageResult, Project } from '../types'
import { api } from '../api'
import { dateTime, errorText, intentLabels } from '../domain'
import StateBadge from '../components/StateBadge.vue'

const props = defineProps<{ drafts: Draft[]; status: IntegrationStatus | null; busy: boolean }>()
const emit = defineEmits<{ draft: [draft: Draft]; cancel: [draft: Draft]; refresh: [] }>()
const text = ref(''), previous = ref<Draft | null>(null), parsing = ref(false), error = ref(''), response = ref('')
const queryProjects = ref<Project[] | null>(null), filter = ref('open')
const visibleDrafts = computed(() => props.drafts.filter(d => filter.value === 'all' || ['pending', 'needs_input'].includes(d.status)))
const labels: Record<Draft['status'], string> = { pending: '待处理', needs_input: '待补充', confirmed: '已保存', cancelled: '已取消', expired: '已过期' }
function supplement(draft: Draft) { previous.value = draft; text.value = ''; error.value = ''; window.scrollTo({ top: 0, behavior: 'smooth' }) }
async function parse() {
  if (!text.value.trim() || parsing.value) return
  parsing.value = true; error.value = ''; response.value = ''; queryProjects.value = null
  try {
    const result = await api<MessageResult>('/api/messages', 'POST', { text: text.value.trim(), client_message_id: crypto.randomUUID(),
      ...(previous.value ? { previous_draft_id: previous.value.id } : {}) })
    if (result.kind === 'draft') {
      emit('draft', result.draft)
      previous.value = result.draft.status === 'needs_input' ? result.draft : null
      response.value = result.draft.status === 'needs_input' ? '仍需补充以下信息，尚未保存项目。' : result.draft.status === 'confirmed' ? '信息齐全，已自动保存。' : '草稿待处理，请补充说明后重新提交。'
    } else if (result.kind === 'query') { queryProjects.value = result.projects; response.value = '查询完成，未修改任何项目。'; previous.value = null }
    else { response.value = result.message; previous.value = null }
    text.value = ''; emit('refresh')
  } catch (e) { error.value = errorText(e) }
  finally { parsing.value = false }
}
</script>
<template>
  <div class="page-heading"><div><p class="eyebrow">ASSISTANT / 项目助手</p><h1>填写项目与进展，补齐自动保存</h1><p class="muted">支持立项、目标调整、汇报与查询；有歧义时先追问。</p></div></div>
  <div class="assistant-layout">
    <section class="panel">
      <div class="section-heading"><h2>自然语言录入</h2><span class="badge" :class="status?.ai_configured ? 'state-active' : 'state-paused'">{{ status?.ai_configured ? `模型已配置 · ${status.ai_model}` : status ? 'AI 尚未配置' : '正在检查模型状态' }}</span></div>
      <el-alert v-if="status && !status.ai_configured" title="AI 尚未启用，可继续在项目进度中使用手动表单录入。" type="info" :closable="false" class="section-gap" />
      <div v-if="previous" class="followup-box"><div class="section-heading"><strong>补充草稿 {{ previous.id.slice(0, 8) }}</strong><el-button text :disabled="parsing" @click="previous = null">开始新操作</el-button></div><p class="field-value">{{ previous.source_text }}</p><ul><li v-for="question in [...(previous.diagnostics?.missing_fields || []), ...(previous.diagnostics?.ambiguities || [])]" :key="question">{{ question }}</li></ul></div>
      <el-input v-model="text" type="textarea" :rows="6" maxlength="6000" show-word-limit :disabled="parsing" :placeholder="previous ? '补充缺少的信息，或澄清你指的项目和目标…' : '例如：P0001 的方案评审目标完成 60%，已完成初稿，正在等待负责人评审。下一步根据意见修改。'" aria-label="项目助手消息" />
      <div class="assistant-footer"><span class="muted">请一次描述一个操作，写明项目与目标。</span><el-button type="primary" :loading="parsing" :disabled="!text.trim() || !status?.ai_configured" @click="parse">{{ previous ? '提交补充' : '提交信息' }}</el-button></div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="section-gap" />
      <el-alert v-if="response" :title="response" type="success" :closable="false" class="section-gap" />
      <div v-if="queryProjects" class="section-gap"><h3>查询结果（{{ queryProjects.length }}）</h3><el-empty v-if="!queryProjects.length" description="未找到可访问项目" /><div v-for="p in queryProjects" :key="p.id" class="query-result"><strong>{{ p.code }} · {{ p.name }}</strong><StateBadge :state="p.status" :flags="p.flags" /><p>{{ p.owner_name }} · 进度 {{ p.progress == null ? '—' : `${p.progress}%` }} · 截止 {{ p.due_date }}</p><p v-for="n in p.milestones" :key="n.id">{{ n.name }}：{{ n.progress }}% · {{ n.summary || '暂无进展' }}</p></div></div>
    </section>
    <aside class="assistant-note"><h3>需要填清楚什么？</h3><ol><li>是否选对项目和目标</li><li>提取的日期、进度是否准确</li><li>需要修改或清除哪些字段</li></ol><p>未提及的字段会保留。模糊进度不会自动转换成准确百分比。</p><hr><h3>企业微信接入状态</h3><p>{{ status?.message || '连接状态尚未取得' }}</p><p>群内 @机器人提交文字后，缺项保留草稿；补齐后自动保存。完整项目默认进入大屏，可在项目中关闭展示。</p></aside>
  </div>
  <section class="panel section-gap"><div class="section-heading"><h2>团队草稿</h2><el-radio-group v-model="filter"><el-radio-button value="open">待处理</el-radio-button><el-radio-button value="all">全部</el-radio-button></el-radio-group></div><p class="muted">消息已保存到数据库；待补充草稿持续保留，关闭页面后仍可继续补充，完整后自动保存项目。</p>
    <el-empty v-if="!visibleDrafts.length" description="暂无相关草稿" />
    <div v-for="draft in visibleDrafts" :key="draft.id" class="draft-row"><div><strong>{{ intentLabels[draft.action.intent] }} <small class="muted">{{ draft.id.slice(0, 8) }}</small></strong><p>{{ draft.preview?.name || draft.source_text.slice(0, 100) || draft.action.project_id || '待补充' }}</p><small class="muted">创建 {{ dateTime(draft.created_at) }} · {{ draft.status === 'needs_input' ? '已保存草稿，持续保留' : `截止 ${dateTime(draft.expires_at)}` }}</small></div><div class="draft-row-actions"><span class="badge" :class="draft.status === 'pending' ? 'state-active' : ''">{{ labels[draft.status] }}</span><el-button size="small" @click="$emit('draft', draft)">查看详情</el-button><el-button v-if="['needs_input', 'pending'].includes(draft.status)" size="small" :disabled="busy || parsing || (draft.status === 'pending' && Date.parse(draft.expires_at) <= Date.now())" @click="supplement(draft)">补充说明</el-button><el-button v-if="['needs_input', 'pending'].includes(draft.status)" text size="small" :disabled="busy" @click="$emit('cancel', draft)">取消</el-button></div></div>
  </section>
</template>
