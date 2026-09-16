import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_TARGET || 'http://127.0.0.1:8080'
  const devPort = Number(env.VITE_DEV_PORT || 9300)

  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src')
      }
    },
    server: {
      port: devPort,
      proxy: {
        '/api/user': {
          target: env.VITE_GATEWAY_TARGET || 'http://127.0.0.1:8080',
          changeOrigin: true,
          timeout: 0,
          proxyTimeout: 0
        },
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          timeout: 0,
          proxyTimeout: 0
        },
        '/health': {
          target: apiTarget,
          changeOrigin: true
        },
        '/chat': {
          target: apiTarget,
          changeOrigin: true,
          timeout: 0,
          proxyTimeout: 0
        },
        '/super-agent': {
          target: apiTarget,
          changeOrigin: true,
          timeout: 0,
          proxyTimeout: 0
        },
        '/knowledge-bases': {
          target: apiTarget,
          changeOrigin: true
        },
        '/kb': {
          target: apiTarget,
          changeOrigin: true
        },
        '/admin': {
          target: apiTarget,
          changeOrigin: true
        },
        '/tools': {
          target: apiTarget,
          changeOrigin: true
        },
        '/parse': {
          target: apiTarget,
          changeOrigin: true
        }
      }
    }
  }
})
