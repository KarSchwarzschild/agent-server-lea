import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'
import { execFile } from 'node:child_process'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [
      vue(),
      {
        name: 'lea-console-local-session',
        configureServer(server) {
          server.middlewares.use('/__dev/console-session', async (_request, response) => {
            try {
              const { stdout } = await execFileAsync(
                'uv',
                ['run', '--no-sync', 'python', 'scripts/dev_gateway_stub.py', '--mint', '--role', 'platform_admin'],
                { cwd: resolve(__dirname, '..') },
              )
              response.setHeader('Content-Type', 'application/json')
              response.end(JSON.stringify({ token: stdout.trim(), tenant_id: 'default' }))
            } catch (error) {
              response.statusCode = 500
              response.end(JSON.stringify({ error: error instanceof Error ? error.message : '无法生成本地测试身份' }))
            }
          })
        },
      },
    ],
    resolve: { alias: { '@': resolve(__dirname, 'src') } },
    server: {
      port: Number(env.VITE_DEV_PORT || 9400),
      proxy: {
        '/api': {
          target: env.VITE_GATEWAY_TARGET || 'http://127.0.0.1:8080',
          changeOrigin: true,
          timeout: 60000,
        },
      },
    },
  }
})
