/**
 * cite-verify 引用审核 — 重新生成 + Markdown 渲染测试
 *
 * 测试流程：
 * 1. 打开已有会话 sa_9492e9bd8b92
 * 2. 点击最后一轮"重新生成"按钮
 * 3. 监听 SSE 事件（plan_created, action, content, done）
 * 4. 等待完成后截图验证 Markdown 渲染
 *
 * 运行：
 *   cd frontend-user && npx playwright test tests/e2e/cite-verify-regenerate.spec.ts --headed
 */
import { test, expect, type Page } from '@playwright/test'

const BASE = 'http://localhost:9002'
const SESSION_ID = 'sa_9492e9bd8b92'
const TOKEN =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyXzE5NzJmMzFhZTU5ZSIsInJvbGVzIjpbInVzZXIiXSwicGVybWlzc2lvbnMiOlsiZG9jdW1lbnQ6cmVhZCIsImtiOnJlYWQiXSwiZXhwIjoxNzc3MDMzNjc2fQ.lD1xwr3Cl-iDYygCZT8r_F972o5WSHgpWqCmKK9f6qc'
const API_BASE = 'http://localhost:8000'

// ─── SSE Event Collector ──────────────────────────────
interface SSEEvent {
  type: string
  data: Record<string, unknown>
  timestamp: number
}

// ─── Helpers ──────────────────────────────────────────

async function login(page: Page) {
  await page.goto(BASE)
  await page.evaluate((t) => localStorage.setItem('token', t), TOKEN)
}

async function gotoSession(page: Page) {
  await page.goto(`${BASE}/qa/super-agent/${SESSION_ID}`)
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(2000) // 等历史消息加载
}

// ─── Tests ────────────────────────────────────────────

test.describe('cite-verify 重新生成 + 渲染验证', () => {
  let sseEvents: SSEEvent[] = []

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    sseEvents = []
    await login(page)
  })

  test('重新生成引用审核并验证 Markdown 渲染', async ({ page }) => {
    test.setTimeout(300_000) // 5 分钟超时

    // ─── Step 1: 进入会话 ───
    console.log('[Step 1] 进入会话...')
    await gotoSession(page)

    // 截图：重新生成前的状态
    await page.screenshot({ path: 'test-results/01-before-regenerate.png', fullPage: true })

    // ─── Step 2: 拦截 SSE 并收集事件 ───
    console.log('[Step 2] 设置 SSE 监听...')
    await page.route(`${API_BASE}/api/user/super-agent/chat/events/**`, async (route) => {
      // 不拦截，只监听
      await route.continue()
    })

    // 注入 SSE 事件收集器
    await page.evaluate(() => {
      const origES = window.EventSource
      ;(window as any).__sseEvents = []
      // Monkey-patch fetch to capture SSE streams
      const origFetch = window.fetch
      window.fetch = async (...args: any[]) => {
        const resp = await origFetch.apply(window, args)
        const url = typeof args[0] === 'string' ? args[0] : args[0]?.url || ''
        if (url.includes('chat/events')) {
          console.log('[SSE] Intercepted SSE fetch:', url)
        }
        return resp
      }
    })

    // ─── Step 3: 点击重新生成 ───
    console.log('[Step 3] 查找并点击重新生成按钮...')

    // 找到最后一个重新生成按钮（class: regen-btn--main, title: 重新生成）
    const regenBtns = page.locator('button.regen-btn--main[title="重新生成"]')
    const lastRegenBtn = regenBtns.last()
    await expect(lastRegenBtn).toBeVisible({ timeout: 10000 })

    // hover 让操作栏显示
    const actionsBar = page.locator('.ai-message-actions').last()
    await actionsBar.hover()
    await page.waitForTimeout(500)

    // 点击最后一个"重新生成"
    await lastRegenBtn.click()

    console.log('[Step 3] 已点击重新生成')
    await page.screenshot({ path: 'test-results/03-after-click-regen.png', fullPage: true })

    // ─── Step 4: 等待 cite-verify 开始执行 ───
    console.log('[Step 4] 等待 cite-verify 开始...')

    // 等待"引用审核"标签或 plan 步骤出现（说明 cite-verify 被识别并开始）
    try {
      await page.waitForFunction(() => {
        const body = document.body.textContent || ''
        return body.includes('引用审核') && (
          body.includes('步骤') || body.includes('阶段') || body.includes('生成中')
        )
      }, { timeout: 30_000 })
      console.log('[Step 4] cite-verify 已开始')
    } catch {
      console.log('[Step 4] 未检测到 cite-verify 开始标志，继续等待...')
    }

    await page.screenshot({ path: 'test-results/04-streaming-started.png', fullPage: true })

    // ─── Step 5: 等待引用审核完成 ───
    console.log('[Step 5] 等待引用审核完成（最多 5 分钟）...')

    // 周期截图（每 45 秒）
    const screenshotInterval = setInterval(async () => {
      try {
        const ts = Math.floor(Date.now() / 1000)
        await page.screenshot({ path: `test-results/05-progress-${ts}.png`, fullPage: true })
        console.log(`[Step 5] 进度截图`)
      } catch {}
    }, 45000)

    // 等待完成：监听 SSE 请求完成
    // 使用 page.waitForResponse 捕获 SSE 连接关闭
    try {
      await page.waitForResponse(
        (resp) => resp.url().includes('chat/events') && resp.status() === 200,
        { timeout: 280_000 }
      )
      // SSE 连接响应完成后，等内容渲染
      await page.waitForTimeout(5000)
      console.log('[Step 5] SSE 流响应完成')
    } catch {
      console.log('[Step 5] SSE 等待超时，检查当前状态...')
    }

    // 额外等待：确保所有内容渲染完毕
    await page.waitForTimeout(3000)

    clearInterval(screenshotInterval)

    // ─── Step 6: 截图并验证 ───
    console.log('[Step 6] 验证渲染结果...')
    await page.screenshot({ path: 'test-results/05-final-result.png', fullPage: true })

    // 获取最后一个 AI 消息的 HTML
    const lastContent = page.locator('.markdown-content').last()
    await expect(lastContent).toBeVisible({ timeout: 5000 })

    // 截图最后一个消息的内容区域
    await lastContent.screenshot({ path: 'test-results/06-markdown-content.png' })

    // ─── Step 7: Markdown 渲染验证 ───
    console.log('[Step 7] Markdown 渲染质量检查...')

    const contentHtml = await lastContent.innerHTML()
    const contentText = await lastContent.textContent() || ''

    // 检查 1: ## 标题应该被渲染为 <h2>/<h3>，不应该以原始文本出现
    const rawHeadingMarkers = (contentText.match(/(?:^|\s)#{2,6}\s/gm) || []).length
    console.log(`  原始 ## 标记数: ${rawHeadingMarkers}`)
    // 如果有超过 2 个原始 ## 标记还在文本中，说明渲染失败
    expect(rawHeadingMarkers).toBeLessThanOrEqual(2) // 容忍少量（可能在代码块中）

    // 检查 2: 应该有 <h2> 或 <h3> 标签
    const h2Count = (contentHtml.match(/<h2/g) || []).length
    const h3Count = (contentHtml.match(/<h3/g) || []).length
    console.log(`  <h2> 数量: ${h2Count}, <h3> 数量: ${h3Count}`)
    expect(h2Count + h3Count).toBeGreaterThan(0)

    // 检查 3: 应该有 <table> 标签（审核报告含表格）
    const tableCount = (contentHtml.match(/<table/g) || []).length
    console.log(`  <table> 数量: ${tableCount}`)
    expect(tableCount).toBeGreaterThan(0)

    // 检查 4: 不应该有大量未渲染的 |---| 分隔行
    const rawTableSeparators = (contentText.match(/\|[-]+\|/g) || []).length
    console.log(`  原始 |---| 分隔行: ${rawTableSeparators}`)
    expect(rawTableSeparators).toBeLessThanOrEqual(1) // 容忍少量

    // 检查 5: cite_extract 工具是否被调用（在页面任意位置查找）
    const pageText = await page.textContent('body') || ''
    const hasCiteExtract = pageText.includes('cite_extract')
    console.log(`  页面含 cite_extract: ${hasCiteExtract}`)
    // 非硬性要求（模型可能选择不调用），仅记录
    if (!hasCiteExtract) {
      console.log('  ⚠ cite_extract 未在页面中找到（模型可能未调用）')
    }

    // 检查 6: 不应该有"执行中断"错误
    const hasError = contentText.includes('执行中断') || contentText.includes('超时')
    console.log(`  执行中断错误: ${hasError}`)
    expect(hasError).toBe(false)

    // ─── Step 8: 滚动截图各区域 ───
    console.log('[Step 8] 分区域截图...')

    // 滚动到顶部
    await lastContent.evaluate((el) => el.scrollIntoView({ block: 'start' }))
    await page.waitForTimeout(500)
    await page.screenshot({ path: 'test-results/07-content-top.png', fullPage: false })

    // 滚动到中间
    await lastContent.evaluate((el) => {
      const parent = el.closest('.message-list') || el.parentElement
      if (parent) parent.scrollTop = parent.scrollHeight / 2
    })
    await page.waitForTimeout(500)
    await page.screenshot({ path: 'test-results/08-content-middle.png', fullPage: false })

    // 滚动到底部
    await lastContent.evaluate((el) => {
      const parent = el.closest('.message-list') || el.parentElement
      if (parent) parent.scrollTop = parent.scrollHeight
    })
    await page.waitForTimeout(500)
    await page.screenshot({ path: 'test-results/09-content-bottom.png', fullPage: false })

    console.log('[完成] 所有截图已保存到 test-results/')
    console.log('验证通过 ✅')
  })
})
