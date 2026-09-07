import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import ts from 'typescript'

test('工作台定时同步草稿和项目，隐藏时暂停，恢复时同步，卸载时清理', async () => {
  const source = readFileSync(new URL('../src/views/Workspace.vue', import.meta.url), 'utf8')
    .match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  let mounted, unmounted, tick, period, cleared, reads = 0, version = 1
  const listeners = new Map()
  const target = prefix => ({
    addEventListener: (name, fn) => listeners.set(prefix + name, fn),
    removeEventListener: name => listeners.delete(prefix + name),
  })
  const document = { ...target('document:'), visibilityState: 'visible' }
  const context = vm.createContext({
    exports: {}, defineProps: () => ({}), document,
    window: { ...target('window:'), location: { hash: '' } },
    setInterval: (fn, delay) => { tick = fn; period = delay; return 1 },
    clearInterval: id => { cleared = id },
    require: name => {
      if (name === 'vue') return {
        ref: value => ({ value }), computed: getter => ({ get value() { return getter() } }),
        onMounted: fn => { mounted = fn }, onBeforeUnmount: fn => { unmounted = fn },
      }
      if (name === '../api') return { api: async path => {
        reads++
        if (path === '/api/projects') return { projects: [{ version }], at: String(version) }
        if (path === '/api/drafts') return [{ id: 'draft', status: version === 1 ? 'needs_input' : 'confirmed' }]
        return []
      } }
      if (name === '../domain') return { errorText: String }
      return {}
    },
  })
  vm.runInContext(ts.transpileModule(source + '\n globalThis.state = { projects, drafts, selectedDraft, loading };', {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, context)
  await mounted()
  assert.equal(period, 5000)
  context.state.selectedDraft.value = context.state.drafts.value[0]
  version = 2
  tick()
  tick() // 请求未结束时不重叠发起。
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(reads, 8)
  assert.equal(context.state.projects.value[0].version, 2)
  assert.equal(context.state.drafts.value[0].status, 'confirmed')
  assert.equal(context.state.selectedDraft.value.status, 'confirmed')
  assert.equal(context.state.loading.value, false)
  document.visibilityState = 'hidden'
  tick()
  assert.equal(reads, 8)
  document.visibilityState = 'visible'
  listeners.get('document:visibilitychange')()
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(reads, 12)
  unmounted()
  assert.equal(cleared, 1)
  assert.equal(listeners.size, 0)
})
