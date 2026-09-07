<script setup lang="ts">
import type { State, Flag } from '../types'
import { stateLabels, flagLabels } from '../domain'
defineProps<{ state?: State; flags?: Flag[]; reason?: string }>()
</script>
<template>
  <span class="badges">
    <span v-if="state" class="badge" :class="`state-${state}`">{{ stateLabels[state] }}</span>
    <span v-if="state === 'paused'" class="pause-reason">暂停原因：{{ reason || '未记录' }}</span>
    <span v-for="flag in flags || []" :key="flag" class="badge" :class="`risk-${flag}`">{{ flagLabels[flag] }}</span>
  </span>
</template>
<style scoped>
.pause-reason{flex-basis:100%;white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.6;color:#946200}
</style>
