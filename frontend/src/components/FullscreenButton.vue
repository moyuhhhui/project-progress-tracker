<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

const fullscreen = ref(false)
function syncFullscreen() { fullscreen.value = !!document.fullscreenElement }
async function toggleFullscreen() {
  try {
    if (document.fullscreenElement) await document.exitFullscreen()
    else {
      if (!document.fullscreenEnabled) throw new Error('Fullscreen unavailable')
      await document.documentElement.requestFullscreen()
    }
  } catch {
    ElMessage.warning('当前浏览器未允许全屏，请在 Chrome 或 Edge 中打开本页后按 F11。')
  }
}
onMounted(() => {
  syncFullscreen()
  document.addEventListener('fullscreenchange', syncFullscreen)
})
onBeforeUnmount(() => document.removeEventListener('fullscreenchange', syncFullscreen))
</script>

<template>
  <el-button :title="fullscreen ? '退出全屏（Esc）' : '全屏展示'" @click="toggleFullscreen">{{ fullscreen ? '退出全屏' : '全屏' }}</el-button>
</template>
