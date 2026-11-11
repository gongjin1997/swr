import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建产物输出到 Python 包内，由 FastAPI 托管（见 swr_builder/web/spa.py）
export default defineConfig({
  plugins: [vue()],
  base: '/',
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  build: {
    outDir: fileURLToPath(new URL('../swr_builder/webui', import.meta.url)),
    emptyOutDir: true,
    assetsDir: 'assets',
    chunkSizeWarningLimit: 1500,
  },
  server: {
    port: 5173,
    proxy: {
      // 开发时把 API 转发到本机运行的平台：python -m swr_builder -c config.yaml
      '/api': { target: 'http://127.0.0.1:8080', changeOrigin: false },
    },
  },
})
