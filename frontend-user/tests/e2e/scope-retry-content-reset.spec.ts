/**
 * Scope Retry Content Reset — E2E 视觉 + 逻辑测试
 *
 * 验证：
 *   1. SSE 事件类型注册完整性（SuperAgent.vue 源码检查）
 *   2. useSSEHandler content_reset 处理逻辑
 *   3. MessageItem 重试徽章
 *   4. 类型定义 retryReason 字段
 *   5. _sid 防串流：done_event 携带 _sid
 *   6. _sid 防串流：普通事件携带 _sid
 *   7. 页面可正常渲染
 *
 * 运行方式：
 *   cd frontend-user && npx playwright test tests/e2e/scope-retry-content-reset.spec.ts --headed
 */
import { test, expect, type Page } from '@playwright/test'
import * as fs from 'fs'
import * as path from 'path'
import { fileURLToPath } from 'url'

const BASE_URL = 'http://localhost:9002'
const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const SRC_DIR = path.resolve(__dirname, '../../src')

/** 登录并获取 token */
async function login(page: Page): Promise<string> {
  const resp = await page.request.post('http://localhost:8000/api/auth/login', {
    data: { username: 'testuser', password: 'test123' },
  })
  const body = await resp.json()
  const token = body.access_token
  await page.goto(BASE_URL)
  await page.evaluate((t) => {
    localStorage.setItem('token', t)
  }, token)
  return token
}

test.describe('Scope Retry Content Reset', () => {
  test.setTimeout(120000)

  // ====== 源码静态检查（直接读文件，不走 Vite 编译） ======

  test('SSE 事件类型注册 — 新增事件在 SSE_EVENT_TYPES 中', async () => {
    const source = fs.readFileSync(path.join(SRC_DIR, 'views/SuperAgent.vue'), 'utf-8')

    const requiredEvents = [
      'parallel_subtask_content_reset',
      'parallel_subtask_scope_retry',
      'parallel_diagnosing',
      'diagnostic_escalation',
      'peer_review',
    ]

    for (const evt of requiredEvents) {
      expect(source).toContain(evt)
    }
  })

  test('useSSEHandler — content_reset handler 正确注册', async () => {
    const source = fs.readFileSync(
      path.join(SRC_DIR, 'composables/useSSEHandler.ts'), 'utf-8'
    )

    expect(source).toContain("case 'parallel_subtask_content_reset'")
    expect(source).toContain('retryReason')
    // 验证清空逻辑（content 被置空）
    expect(source).toContain("resetTask.content = ''")
    // 验证折叠展开
    expect(source).toContain("resetTask.expanded = false")
  })

  test('MessageItem — 重试徽章样式和结构', async () => {
    const source = fs.readFileSync(
      path.join(SRC_DIR, 'components/super-agent/MessageItem.vue'), 'utf-8'
    )

    expect(source).toContain('parallel-retry-badge')
    expect(source).toContain('已重试')
    expect(source).toContain('t.retryReason')
  })

  test('类型定义 — ParallelSubtask 包含 retryReason 字段', async () => {
    const source = fs.readFileSync(
      path.join(SRC_DIR, 'types/super-agent.ts'), 'utf-8'
    )

    expect(source).toContain('retryReason?: string')
  })

  test('API 类型定义 — 包含 content_reset 事件类型', async () => {
    const source = fs.readFileSync(
      path.join(SRC_DIR, 'api/superagent.ts'), 'utf-8'
    )

    expect(source).toContain('parallel_subtask_content_reset')
  })

  // ====== SSE _sid 防串流实时测试 ======

  test('SSE _sid — done_event 包含 _sid', async ({ page }) => {
    const token = await login(page)

    const resp = await page.request.post(
      'http://localhost:8000/api/user/super-agent/chat/stream?async=false',
      {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'text/event-stream',
        },
        data: { message: '你好' },
      }
    )

    const body = await resp.text()
    const lines = body.split('\n')
    let foundDoneWithSid = false
    let nextIsDoneData = false

    for (const line of lines) {
      if (line === 'event: done') {
        nextIsDoneData = true
        continue
      }
      if (nextIsDoneData && line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.substring(6))
          if (data._sid && data.session_id) {
            foundDoneWithSid = true
            expect(data._sid).toBe(data.session_id)
          }
        } catch {}
        nextIsDoneData = false
      }
    }

    expect(foundDoneWithSid).toBe(true)
  })

  test('SSE _sid — 普通事件携带 _sid（覆盖率 >= 50%）', async ({ page }) => {
    const token = await login(page)

    const resp = await page.request.post(
      'http://localhost:8000/api/user/super-agent/chat/stream?async=false',
      {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'text/event-stream',
        },
        data: { message: '简单回答：1+1等于几' },
      }
    )

    const body = await resp.text()
    const lines = body.split('\n')

    let sidCount = 0
    let eventCount = 0

    for (const line of lines) {
      if (line.startsWith('data: ') && line !== 'data: [DONE]') {
        try {
          const data = JSON.parse(line.substring(6))
          eventCount++
          if (data._sid) sidCount++
        } catch {}
      }
    }

    expect(eventCount).toBeGreaterThan(0)
    expect(sidCount).toBeGreaterThan(0)
    // 覆盖率 >= 50%（部分内部事件可能不经过注入点）
    const coverage = sidCount / eventCount
    expect(coverage).toBeGreaterThanOrEqual(0.5)
    console.log(`_sid coverage: ${sidCount}/${eventCount} = ${(coverage * 100).toFixed(1)}%`)
  })

  // ====== 视觉测试 ======

  test('视觉截图 — 超级智能体页面可正常渲染', async ({ page }) => {
    const token = await login(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`${BASE_URL}/qa/super-agent`)
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(2000)

    await page.screenshot({
      path: 'tests/e2e/screenshots/super-agent-scope-retry.png',
      fullPage: false,
    })

    // 验证核心 UI 元素存在
    const chatInput = page.locator(
      '.chat-input-wrapper, .input-area, textarea, [placeholder*="输入"], [placeholder*="请输入"]'
    )
    await expect(chatInput.first()).toBeVisible({ timeout: 10000 })
  })
})
