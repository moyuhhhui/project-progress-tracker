<script setup lang="ts">
import { ref } from 'vue'
import type { IntegrationStatus, MessageResult, Project } from '../types'
import { api } from '../api'
import { errorText } from '../domain'
import StateBadge from '../components/StateBadge.vue'

const props = defineProps<{ status: IntegrationStatus | null }>()
const emit = defineEmits<{ refresh: [] }>()
const text = ref(''), parsing = ref(false), error = ref(''), response = ref('')
const queryProjects = ref<Project[] | null>(null)

async function parse() {
  if (!text.value.trim() || parsing.value) return
  parsing.value = true; error.value = ''; response.value = ''; queryProjects.value = null
  try {
    const result = await api<MessageResult>('/api/messages', 'POST', {
      text: text.value.trim(), client_message_id: crypto.randomUUID(),
    })
    if (result.kind === 'saved') response.value = '已保存，项目数据和审计记录已更新。'
    else if (result.kind === 'batch') response.value = `已识别 ${result.recognized_actions} 项，成功保存 ${result.saved_actions} 项，失败 ${result.business_failures} 项。`
    else if (result.kind === 'needs_input') response.value = result.message
    else if (result.kind === 'query') { queryProjects.value = result.projects; response.value = '查询完成，未修改任何项目。' }
    else response.value = result.message
    text.value = ''; emit('refresh')
  } catch (e) { error.value = errorText(e) }
  finally { parsing.value = false }
}
</script>
<template>
  <div class="page-heading"><div><p class="eyebrow">ASSISTANT / 项目助手</p><h1>填写项目与进展，直接保存</h1><p class="muted">支持立项、目标调整、汇报与查询；有歧义时返回补充提示，完整操作直接写入正式数据。</p></div></div>
  <div class="assistant-layout">
    <section class="panel">
      <div class="section-heading"><h2>自然语言录入</h2><span class="badge" :class="props.status?.ai_configured ? 'state-active' : 'state-paused'">{{ props.status?.ai_configured ? `模型已配置 · ${props.status.ai_model}` : props.status ? 'AI 尚未配置' : '正在检查模型状态' }}</span></div>
      <el-alert v-if="props.status && !props.status.ai_configured" title="AI 尚未启用，可继续在项目进度中使用手动表单录入。" type="info" :closable="false" class="section-gap" />
      <el-input v-model="text" type="textarea" :rows="6" maxlength="6000" show-word-limit :disabled="parsing" placeholder="例如：P0001 的方案评审目标完成 60%，已完成初稿，正在等待负责人评审。下一步根据意见修改。" aria-label="项目助手消息" />
      <div class="assistant-footer"><span class="muted">请一次描述一个操作，写明项目与目标。</span><el-button type="primary" :loading="parsing" :disabled="!text.trim() || !props.status?.ai_configured" @click="parse">提交信息</el-button></div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon class="section-gap" />
      <el-alert v-if="response" :title="response" type="success" :closable="false" class="section-gap" />
      <div v-if="queryProjects" class="section-gap"><h3>查询结果（{{ queryProjects.length }}）</h3><el-empty v-if="!queryProjects.length" description="未找到可访问项目" /><div v-for="p in queryProjects" :key="p.id" class="query-result"><strong>{{ p.code }} · {{ p.name }}</strong><StateBadge :state="p.status" :flags="p.flags" /><p>{{ p.owner_name }} · 进度 {{ p.progress == null ? '—' : `${p.progress}%` }} · 截止 {{ p.due_date }}</p><p v-for="n in p.milestones" :key="n.id">{{ n.name }}：{{ n.progress }}% · {{ n.summary || '暂无进展' }}</p></div></div>
    </section>
    <aside class="assistant-note"><h3>需要填清楚什么？</h3><ol><li>是否选对项目和目标</li><li>提取的日期、进度是否准确</li><li>需要修改或清除哪些字段</li></ol><p>未提及的字段会保留。模糊进度不会自动转换成准确百分比。</p><hr><h3>企业微信接入状态</h3><p>{{ props.status?.message || '连接状态尚未取得' }}</p><p>群内 @机器人提交文字后，信息齐全会直接保存；缺少关键字段时只返回补充提示，不生成业务草稿。</p></aside>
  </div>
</template>
