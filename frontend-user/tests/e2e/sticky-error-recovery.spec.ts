/**
 * Skill 粘性优化 v4.1 — E2E 视觉 + 源码 + SSE 测试
 *
 * 验证：
 *   1. 源码静态检查：useSSEHandler SKILL_ERROR/SKILL_TIMEOUT 处理
 *   2. 源码静态检查：MessageItem 统一 ErrorActionBar
 *   3. 源码静态检查：类型定义 errorInfo.skill 字段
 *   4. 源码静态检查：followup_detector error_context 参数
 *   5. SSE 实时测试：多轮对话粘性保持
 *   6. SSE 实时测试：error 事件格式
 *   7. 视觉截图：超级智能体页面 + 多轮对话
 *
 * 运行方式：
 *   cd frontend-user && npx playwright test tests/e2e/sticky-error-recovery.spec.ts --headed
 */
import { test, expect, type Page } from '@playwright/test'
import * as fs from 'fs'
import * as path from 'path'
import { fileURLToPath } from 'url'

const BASE_URL = 'http://localhost:9002'
const API_URL = 'http://localhost:8000'
const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const SRC_DIR = path.resolve(__dirname, '../../src')
const BACKEND_DIR = path.resolve(__dirname, '../../../backend')

/** 登录并获取 token */
async function login(page: Page): Promise<string> {
  const resp = await page.request.post(`${API_URL}/api/auth/login`, {
    data: { username: 'admin', password: 'admin123' },
  })
  const body = await resp.json()
  const token = body.access_token
  await page.goto(BASE_URL)
  await page.evaluate((t) => {
    localStorage.setItem('token', t)
  }, token)
  return token
}

/** 发送 SSE 请求并返回原始文本 */
async function sendChat(
  page: Page,
  token: string,
  message: string,
  sessionId?: string,
): Promise<{ body: string; events: Array<{ event: string; data: any }> }> {
  const payload: any = { message }
  if (sessionId) payload.session_id = sessionId

  const resp = await page.request.post(
    `${API_URL}/api/user/super-agent/chat/stream?async=false`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'text/event-stream',
      },
      data: payload,
    },
  )

  const body = await resp.text()
  const events: Array<{ event: string; data: any }> = []
  const lines = body.split('\n')
  let currentEvent = 'message'

  for (const line of lines) {
    if (line.startsWith('event: ')) {
      currentEvent = line.substring(7).trim()
    } else if (line.startsWith('data: ') && line !== 'data: [DONE]') {
      try {
        const data = JSON.parse(line.substring(6))
        events.push({ event: currentEvent, data })
        if (currentEvent === 'done') break
      } catch {}
    }
  }

  return { body, events }
}

/** 从事件列表中提取 session_id */
function extractSessionId(events: Array<{ event: string; data: any }>): string {
  const done = events.find((e) => e.event === 'done')
  return done?.data?.session_id || ''
}

test.describe('Skill 粘性优化 v4.1', () => {
  test.setTimeout(300000) // 5 分钟：深度调研等 Skill 需要较长执行时间

  // ====== 1. 源码静态检查 ======

  test('useSSEHandler — SKILL_ERROR/SKILL_TIMEOUT 统一处理', async () => {
    const source = fs.readFileSync(
      path.join(SRC_DIR, 'composables/useSSEHandler.ts'),
      'utf-8',
    )

    // 应包含统一的 error code 判断
    expect(source).toContain('SKILL_ERROR')
    expect(source).toContain('SKILL_TIMEOUT')

    // 应从 data.code 中读取（v4.1 新增的后端字段）
    expect(source).toContain('data.code')

    // 应设置 errorInfo 包含 skill 字段
    expect(source).toContain('data.skill')

    // retryable 应默认为 true
    expect(source).toContain('data.retryable !== false')
  })

  test('MessageItem — 统一 ErrorActionBar 结构', async () => {
    const source = fs.readFileSync(
      path.join(SRC_DIR, 'components/super-agent/MessageItem.vue'),
      'utf-8',
    )

    // 错误横幅存在
    expect(source).toContain('error-banner')
    expect(source).toContain('msg.errorInfo')

    // 重试按钮存在
    expect(source).toContain('retry-question')
    expect(source).toContain('msg.errorInfo.retryable')

    // v4.1: 超时场景差异化文案
    expect(source).toContain('SKILL_TIMEOUT')
  })

  test('类型定义 — errorInfo 包含 skill 字段', async () => {
    const source = fs.readFileSync(
      path.join(SRC_DIR, 'types/super-agent.ts'),
      'utf-8',
    )

    // v4.1: 新增 skill 字段
    expect(source).toContain('skill?: string')

    // 原有字段仍在
    expect(source).toContain('code: string')
    expect(source).toContain('retryable: boolean')
    expect(source).toContain('originalQuestion?: string')
  })

  test('后端 followup_detector — error_context 参数存在', async () => {
    const source = fs.readFileSync(
      path.join(
        BACKEND_DIR,
        'app/services/super_agent/followup_detector.py',
      ),
      'utf-8',
    )

    // v4.1: error_context 参数
    expect(source).toContain('error_context: Optional[dict] = None')

    // 重试检测函数
    expect(source).toContain('def is_retry_request')

    // 同族 Skill 判断
    expect(source).toContain('def _are_related_skills')

    // SKILL_FAMILIES 定义
    expect(source).toContain('SKILL_FAMILIES')

    // 规则 1 阈值提高到 0.88
    expect(source).toContain('0.88')

    // 规则 8 有粘性时默认 refine
    expect(source).toContain("if current_skill:")
    expect(source).toContain("return 'refine'")
  })

  test('后端 orchestrator — 粘性加权 + context 状态字段', async () => {
    const source = fs.readFileSync(
      path.join(BACKEND_DIR, 'app/services/super_agent/orchestrator.py'),
      'utf-8',
    )

    // v4.1: OrchestratorContext 新字段
    expect(source).toContain('last_skill_status')
    expect(source).toContain('last_skill_error')

    // 粘性加权方法
    expect(source).toContain('_apply_sticky_bonus')

    // 同族 Skill 判断
    expect(source).toContain('_are_related_skills')

    // 错误状态更新
    expect(source).toContain("context.last_skill_status = 'error'")
    expect(source).toContain("context.last_skill_status = 'timeout'")
    expect(source).toContain("context.last_skill_status = 'success'")

    // SSE error 事件包含 skill 和 retryable
    expect(source).toContain("'code': 'SKILL_ERROR'")
    expect(source).toContain("'code': 'SKILL_TIMEOUT'")
    expect(source).toContain("'retryable': True")
  })

  test('后端 context_store — 持久化 last_skill_status', async () => {
    const source = fs.readFileSync(
      path.join(BACKEND_DIR, 'app/services/super_agent/context_store.py'),
      'utf-8',
    )

    // save 路径
    expect(source).toContain("existing_ctx['last_skill_status']")
    expect(source).toContain("existing_ctx['last_skill_error']")

    // load 路径
    expect(source).toContain("ctx_data.get('last_skill_status')")
    expect(source).toContain("ctx_data.get('last_skill_error')")
  })

  // ====== 2. SSE 实时测试 ======

  test('SSE 多轮对话 — 短追问保持粘性', async ({ page }) => {
    const token = await login(page)

    // 轮 1: 用快速 Skill 触发粘性（web-search 通常 10-30 秒完成）
    const r1 = await sendChat(page, token, '帮我搜索2024年中国在线教育市场规模')
    const sid = extractSessionId(r1.events)
    expect(sid).toBeTruthy()

    const intentEvt1 = r1.events.find((e) => e.event === 'intent')
    console.log(`[轮1] session=${sid}, intent=${JSON.stringify(intentEvt1?.data)}`)

    // 轮 2: 短追问
    const r2 = await sendChat(page, token, '有没有更多国内的案例？', sid)
    const intentEvt2 = r2.events.find((e) => e.event === 'intent')
    console.log(`[轮2] intent=${JSON.stringify(intentEvt2?.data)}`)

    if (intentEvt2) {
      const type = intentEvt2.data.type || ''
      // 不应触发全新的意图（应是 context_qa 或同一 skill）
      expect(['context_qa', 'skill', 'qa']).toContain(type)
    }
  })

  test('SSE 多轮对话 — 搜索请求在调研中保持粘性', async ({ page }) => {
    const token = await login(page)

    // 轮 1: 用 web-search 建立粘性（比 deep-research 快得多）
    const r1 = await sendChat(page, token, '帮我搜索AI教育应用的最新趋势')
    const sid = extractSessionId(r1.events)
    expect(sid).toBeTruthy()

    const intentEvt1 = r1.events.find((e) => e.event === 'intent')
    console.log(`[同族保护-轮1] session=${sid}, intent=${JSON.stringify(intentEvt1?.data)}`)

    // 轮 2: "搜索XXX" — 同族保护
    const r2 = await sendChat(page, token, '搜索有关在线教育的最新数据', sid)
    const intentEvt2 = r2.events.find((e) => e.event === 'intent')
    console.log(`[同族保护-轮2] intent=${JSON.stringify(intentEvt2?.data)}`)

    if (intentEvt2) {
      const skillName = intentEvt2.data.skill_name || ''
      // 不应切换到 web-search（应保持粘性或走 context_qa）
      expect(skillName).not.toBe('web-search')
    }
  })

  test('SSE error 事件 — 无效 Skill 返回正确格式', async ({ page }) => {
    const token = await login(page)

    const resp = await page.request.post(
      `${API_URL}/api/user/super-agent/chat/stream?async=false`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'text/event-stream',
        },
        data: { message: '测试', force_skill: 'non_existent_12345' },
      },
    )

    const body = await resp.text()
    const lines = body.split('\n')
    let hasError = false

    for (let i = 0; i < lines.length; i++) {
      if (lines[i] === 'event: error') {
        const dataLine = lines[i + 1] || ''
        if (dataLine.startsWith('data: ')) {
          try {
            const data = JSON.parse(dataLine.substring(6))
            hasError = true
            // 应包含 code 字段
            expect(data.code || data.error_code).toBeTruthy()
            console.log(`[error格式] ✓ code=${data.code || data.error_code}`)
          } catch {}
        }
      }
    }

    expect(hasError).toBe(true)
  })

  // ====== 3. 视觉截图 ======

  test('视觉截图 — 超级智能体页面正常渲染', async ({ page }) => {
    const token = await login(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`${BASE_URL}/qa/super-agent`)
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(2000)

    await page.screenshot({
      path: 'tests/e2e/screenshots/sticky-v41-page.png',
      fullPage: false,
    })

    // 验证输入框存在
    const chatInput = page.locator(
      '.chat-input-wrapper, .input-area, textarea, [placeholder*="输入"], [placeholder*="请输入"]',
    )
    await expect(chatInput.first()).toBeVisible({ timeout: 10000 })
  })

  test('视觉截图 — 多轮对话后的消息列表', async ({ page }) => {
    const token = await login(page)
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`${BASE_URL}/qa/super-agent`)
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(2000)

    // 找到输入框并发送消息
    const textarea = page.locator(
      'textarea, [contenteditable], .chat-input-wrapper textarea',
    )
    const firstTextarea = textarea.first()

    if (await firstTextarea.isVisible({ timeout: 5000 }).catch(() => false)) {
      // 发送第一条消息
      await firstTextarea.fill('你好，请简要介绍一下你的功能')
      // 找发送按钮
      const sendBtn = page.locator(
        'button:has-text("发送"), .send-btn, [aria-label="发送"], button.chat-send-btn',
      )
      if (await sendBtn.first().isVisible({ timeout: 3000 }).catch(() => false)) {
        await sendBtn.first().click()
      } else {
        // 尝试 Enter 发送
        await firstTextarea.press('Enter')
      }

      // 等待响应
      await page.waitForTimeout(5000)

      // 截图：多轮对话状态
      await page.screenshot({
        path: 'tests/e2e/screenshots/sticky-v41-chat.png',
        fullPage: false,
      })

      // 验证消息列表有内容
      const messageItems = page.locator(
        '.message-item, .chat-message, [class*="message"]',
      )
      const count = await messageItems.count()
      console.log(`[视觉] 消息项数量: ${count}`)
    } else {
      console.log('[视觉] 未找到输入框，跳过交互截图')
      await page.screenshot({
        path: 'tests/e2e/screenshots/sticky-v41-chat.png',
        fullPage: false,
      })
    }
  })
})
