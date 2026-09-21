<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from 'vue'
import { api } from './api'
import { errorText } from './domain'
import type { Actor } from './types'
import Workspace from './views/Workspace.vue'
import DisplayBoard from './views/DisplayBoard.vue'

const actor = ref<Actor | null>(null)
const loading = ref(true)
const error = ref('')
const displayMode = ref(location.hash === '#display')
function updateMode() { displayMode.value = location.hash === '#display' }
async function connect() {
  loading.value = true; error.value = ''
  try {
    const user = await api<Actor>('/api/me')
    actor.value = user
  } catch (e) { error.value = errorText(e) }
  finally { loading.value = false }
}
onMounted(() => {
  window.addEventListener('hashchange', updateMode)
  void connect()
})
onBeforeUnmount(() => {
  window.removeEventListener('hashchange', updateMode)
})
</script>
<template>
  <main v-if="!actor" class="connection-page">
    <h1>公司项目进度追踪</h1>
    <p v-if="loading">正在连接工作台…</p>
    <template v-else><p role="alert">{{ error }}</p><el-button type="primary" @click="connect">重新连接</el-button></template>
  </main>
  <DisplayBoard v-else-if="displayMode" :actor="actor" />
  <Workspace v-else :actor="actor" />
</template>
<style scoped>
.connection-page{min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;background:#fff;color:#14263f;gap:16px}
</style>

