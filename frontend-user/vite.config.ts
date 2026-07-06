import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_TARGET || 'http://127.0.0.1:8100'
  const devPort = Number(env.VITE_DEV_PORT || 9102)

  return {
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  test: {
    environment: 'happy-dom',
    globals: true,
    exclude: ['tests/e2e/**', 'node_modules/**'],
  },
  server: {
    port: devPort,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        // SSE 长连接：禁用 proxyTimeout，防止 ERR_INCOMPLETE_CHUNKED_ENCODING
        timeout: 0,
        proxyTimeout: 0,
      },
      '/feed': {
        target: apiTarget,
        changeOrigin: true
      },
      '/kkfileview': {
        target: 'http://127.0.0.1:8012',
        changeOrigin: true
      }
    }
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          // 核心框架
          'vue-vendor': ['vue', 'vue-router', 'pinia'],
          // UI库
          'naive-ui': ['naive-ui'],
          // Markdown渲染相关（marked 被 Review.vue 单独使用，不属于 super-agent 渲染链路）
          'markdown': ['markdown-it', 'highlight.js', 'katex'],
          // 图表库（懒加载，独立分包）
          'mermaid': ['mermaid'],
          'echarts': ['echarts'],
        }
      }
    },
    // 分块大小警告阈值
    chunkSizeWarningLimit: 500
  }
  }
})
