<script setup lang="ts">
import { computed } from 'vue'
import type { User, State } from '../types'
import { fieldLabels, stateLabels } from '../domain'
const props = defineProps<{ value: unknown; field?: string; users?: User[] }>()
const text = computed(() => {
  const v = props.value
  if (v === undefined || v === null || v === '') return '未设置'
  if (props.field === 'owner_id') return props.users?.find(u => u.id === v)?.name || String(v)
  if (props.field === 'owner_roles' && typeof v === 'object' && !Array.isArray(v)) return Object.entries(v).map(([id, role]) => `${role} · ${props.users?.find(u => u.id === id)?.name || '成员不可用'}`).join('、') || '未指定'
  if (props.field === 'status') return stateLabels[v as State] || String(v)
  if (typeof v === 'boolean') return v ? '是' : '否'
  if (Array.isArray(v) && props.field === 'member_ids') return v.map(id => props.users?.find(u => u.id === id)?.name || id).join('、') || '无'
  if (Array.isArray(v) && props.field === 'clear_fields') return v.map(k => fieldLabels[k] || k).join('、')
  if (typeof v === 'object') return JSON.stringify(v, null, 2)
  return String(v)
})
</script>
<template><span class="field-value">{{ text }}</span></template>
