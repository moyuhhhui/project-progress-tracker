import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => ({
  plugins: [vue()],
  base: mode === 'production' ? '/project-tracker/' : '/',
  server: { port: 5174, proxy: { '/api': 'http://127.0.0.1:8001' } },
}))
