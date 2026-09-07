<script setup lang="ts">
import type { User } from '../types'
export interface NodeForm { name: string; criterion: string; owner_id: string; start_date: string; due_date: string; update_interval: number }
const model = defineModel<NodeForm>({ required: true })
defineProps<{ users: User[] }>()
</script>
<template>
  <el-form-item label="目标名称" required><el-input v-model="model.name" maxlength="100" placeholder="例如：方案评审通过" /></el-form-item>
  <el-form-item label="完成标准" required><el-input v-model="model.criterion" type="textarea" :rows="2" maxlength="2000" placeholder="写明可以核对的完成结果" /></el-form-item>
  <div class="form-grid"><el-form-item label="节点负责人" required><el-select v-model="model.owner_id" filterable><el-option v-for="u in users" :key="u.id" :label="u.name" :value="u.id" /></el-select></el-form-item><el-form-item label="汇报间隔（工作日）" required><el-input-number v-model="model.update_interval" :min="1" :max="30" :precision="0" /></el-form-item></div>
  <div class="form-grid"><el-form-item label="计划开始日期" required><el-date-picker v-model="model.start_date" value-format="YYYY-MM-DD" placeholder="选择日期" /></el-form-item><el-form-item label="计划截止日期" required><el-date-picker v-model="model.due_date" value-format="YYYY-MM-DD" placeholder="选择日期" /></el-form-item></div>
</template>
