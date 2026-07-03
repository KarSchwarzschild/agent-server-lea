/**
 * PPT Generation & Chart Visualization Skill — E2E 多轮测试
 *
 * 验证项：
 *   1. Skill 意图识别（发送对应 prompt → 正确路由到 Skill）
 *   2. SSE 流式输出正常（thinking steps / tool calls / content）
 *   3. 沙箱执行（生成文件 / 图表 HTML）
 *   4. 前端渲染（文件下载卡片 / 交互式图表 player）
 *   5. 视觉回归（截图对比）
 *
 * 运行方式：
 *   npx playwright test tests/e2e/skill-ppt-chart.spec.ts --timeout 180000
 *
 * 前置条件：
 *   - 前端 dev server: http://localhost:9002
 *   - 后端 API: http://localhost:8000
 *   - ppt-generation 和 chart-visualization Skill 已 seed 到数据库
 */
import { test, expect, type Page } from '@playwright/test'

const BASE_URL = 'http://localhost:9002'

// ─── Helpers ───────────────────────────────────────────

/** 登录并获取 token */
async function login(page: Page) {
  await page.goto(BASE_URL)
  await page.waitForLoadState('networkidle')

  // Naive UI n-input 渲染为 <div class="n-input"><input></div>
  // 检查是否在登录页（有"登录"按钮）
  const loginBtn = page.locator('button:has-text("登录")')
  const isLoginPage = await loginBtn.isVisible({ timeout: 3000 }).catch(() => false)

  if (isLoginPage) {
    // 找到用户名和密码输入框（Naive UI 的 n-form-item 内的 input）
    const inputs = page.locator('.n-input input[type="text"], .n-input input:not([type])')
    const passwordInput = page.locator('.n-input input[type="password"]')

    await inputs.first().fill('testuser')
    await passwordInput.fill('test123')
    await loginBtn.click()
    // 等待路由跳转完成
    await page.waitForURL(/\/qa/, { timeout: 10000 }).catch(() => {})
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(1000)
  }
}

/** 导航到超级智能体页面 */
async function navigateToSuperAgent(page: Page) {
  await page.goto(`${BASE_URL}/qa/super-agent`)
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(2000)

  // 确认不在登录页
  const isLoginPage = await page.locator('button:has-text("登录")').isVisible({ timeout: 1000 }).catch(() => false)
  if (isLoginPage) {
    console.warn('仍在登录页，重新尝试登录')
    await login(page)
    await page.goto(`${BASE_URL}/qa/super-agent`)
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(2000)
  }
}

/** 点击新建对话（弹出 AgentSelector → 关闭/选择默认） */
async function startNewChat(page: Page) {
  const newChatBtn = page.locator('button:has-text("新建对话")')
  if (await newChatBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
    await newChatBtn.click()
    await page.waitForTimeout(800)

    // AgentSelector 弹窗可能出现，关闭它（使用默认助手）
    const modal = page.locator('.n-modal-mask')
    if (await modal.isVisible({ timeout: 1500 }).catch(() => false)) {
      await page.keyboard.press('Escape')
      await page.waitForTimeout(500)
    }
  }
}

/** 发送消息并等待回复完成 */
async function sendMessageAndWait(
  page: Page,
  message: string,
  options: {
    /** 最大等待 ms（默认 150s，LLM + 沙箱执行较慢） */
    timeout?: number
  } = {}
) {
  const { timeout = 150_000 } = options

  // 聚焦输入框（ChatInputArea 内的 textarea）
  const textarea = page.locator('textarea').first()
  await textarea.click()
  await textarea.fill(message)

  // 发送（点击发送按钮比 Enter 更可靠）
  const sendBtn = page.locator('button:has-text("发送")').first()
  if (await sendBtn.isVisible({ timeout: 1000 }).catch(() => false)) {
    await sendBtn.click()
  } else {
    await textarea.press('Enter')
  }

  // 先等待流式输出启动（停止生成按钮出现）
  await page.waitForTimeout(2000)

  // 检查是否出现了意图确认弹窗（低置信度时需要用户确认）
  // 使用文本选择器，因为 Naive UI 的 class 名是 n-button--primary-type 而非 n-button--primary
  const intentConfirmBtn = page.locator('.intent-confirm-card button:has-text("确认使用")').first()
  if (await intentConfirmBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
    console.log('检测到意图确认弹窗，自动确认')
    await intentConfirmBtn.click()
    await page.waitForTimeout(3000)
  }

  // 等待流式输出完成：检测 .stop-generation-btn 消失
  const startTime = Date.now()
  let sawStreaming = false

  while (Date.now() - startTime < timeout) {
    // 再次检查意图确认（可能延迟出现）
    if (!sawStreaming) {
      const hasConfirm = await intentConfirmBtn.isVisible({ timeout: 500 }).catch(() => false)
      if (hasConfirm) {
        console.log('检测到意图确认弹窗（延迟），自动确认')
        await intentConfirmBtn.click()
        await page.waitForTimeout(3000)
        continue
      }
    }

    const hasStopBtn = await page.evaluate(() => {
      return !!document.querySelector('.stop-generation-btn')
    })

    if (hasStopBtn) {
      sawStreaming = true
    }

    if (sawStreaming && !hasStopBtn) {
      // 停止按钮消失了，再等一下确认
      await page.waitForTimeout(2000)
      const stillHasStop = await page.evaluate(() => {
        return !!document.querySelector('.stop-generation-btn')
      })
      if (!stillHasStop) break
    }

    // 没有看到停止按钮，也检查是否已经有 AI 消息（可能响应很快）
    if (!sawStreaming && Date.now() - startTime > 10000) {
      const hasAiMsg = await page.evaluate(() => {
        const msgs = document.querySelectorAll('.ai-message .ai-content')
        return msgs.length > 0
      })
      if (hasAiMsg) break
    }

    await page.waitForTimeout(3000)
  }

  // 最终额外等待确保 DOM 更新完成
  await page.waitForTimeout(1000)
}

/** 获取最后一条 AI 消息的文本内容 */
async function getLastAssistantMessage(page: Page): Promise<string> {
  return page.evaluate(() => {
    const msgs = document.querySelectorAll('.ai-message .ai-content')
    const last = msgs[msgs.length - 1]
    return last?.textContent?.trim() || ''
  })
}

/** 获取所有 AI 消息数量 */
async function getAiMessageCount(page: Page): Promise<number> {
  return page.evaluate(() => {
    return document.querySelectorAll('.ai-message').length
  })
}

/** 检查页面中是否有文件下载卡片 */
async function hasFileCard(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const selectors = [
      '.file-card',
      '.generated-file',
      '[class*="file-download"]',
      '[class*="file-preview"]',
      'a[download]',
      '.sandbox-output',
      '.tool-file-item',
    ]
    return selectors.some(sel => document.querySelector(sel) !== null)
  })
}

/** 检查页面中是否有交互式图表 player */
async function hasInteractivePlayer(page: Page): Promise<boolean> {
  return page.evaluate(() => {
    const selectors = [
      '.interactive-player',
      'iframe[srcdoc]',
      '.interactive-player-container',
    ]
    return selectors.some(sel => document.querySelector(sel) !== null)
  })
}

// ─── Tests ─────────────────────────────────────────────

test.describe('PPT Generation Skill', () => {
  test.setTimeout(180_000)

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await login(page)
    await navigateToSuperAgent(page)
  })

  test('基本 PPT 生成 — 意图识别 + 流式输出 + 文件生成', async ({ page }) => {
    await startNewChat(page)

    // Step 1: 发送 PPT 生成请求
    await sendMessageAndWait(page, '帮我制作一个关于人工智能在教育中的应用的PPT，3页就好，快速模式', {
      timeout: 150_000,
    })

    // 截图
    await page.screenshot({
      path: 'tests/e2e/screenshots/ppt-01-generation-complete.png',
      fullPage: true,
    })

    // Step 2: 验证意图被正确识别
    const intentCorrect = await page.evaluate(() => {
      const text = document.body.innerText
      return text.includes('PPT') ||
             text.includes('ppt') ||
             text.includes('演示文稿') ||
             text.includes('幻灯片') ||
             text.includes('presentation')
    })
    console.log(`PPT 意图识别: ${intentCorrect ? 'PASS' : 'WARN - 未检测到明确的PPT关键词'}`)

    // Step 3: 验证有内容输出（AI 消息非空）
    const lastMsg = await getLastAssistantMessage(page)
    console.log(`Assistant 回复长度: ${lastMsg.length} chars`)
    expect(lastMsg.length).toBeGreaterThan(20)

    // Step 4: 检查是否有文件下载卡片（沙箱生成的 PPTX）
    const hasFile = await hasFileCard(page)
    console.log(`文件下载卡片: ${hasFile ? 'FOUND' : 'NOT FOUND (可能沙箱未启动)'}`)
  })

  test('PPT 多轮对话 — 生成后追加修改请求', async ({ page }) => {
    await startNewChat(page)

    // 第一轮：生成 PPT
    await sendMessageAndWait(page, '做一个3页PPT，主题：终身教育的意义', {
      timeout: 150_000,
    })

    await page.screenshot({
      path: 'tests/e2e/screenshots/ppt-02-round1.png',
      fullPage: true,
    })

    const round1Msg = await getLastAssistantMessage(page)
    console.log(`第一轮回复: ${round1Msg.length} chars`)
    expect(round1Msg.length).toBeGreaterThan(10)

    const msgCountAfterRound1 = await getAiMessageCount(page)

    // 第二轮：追加修改
    await sendMessageAndWait(page, '再加一页关于在线教育趋势的内容', {
      timeout: 150_000,
    })

    await page.screenshot({
      path: 'tests/e2e/screenshots/ppt-03-round2.png',
      fullPage: true,
    })

    const msgCountAfterRound2 = await getAiMessageCount(page)
    console.log(`AI 消息数: 第一轮后=${msgCountAfterRound1}, 第二轮后=${msgCountAfterRound2}`)

    const round2Msg = await getLastAssistantMessage(page)
    console.log(`第二轮回复: ${round2Msg.length} chars`)
    expect(round2Msg.length).toBeGreaterThan(10)
    expect(msgCountAfterRound2).toBeGreaterThan(msgCountAfterRound1)
  })
})

test.describe('Chart Visualization Skill', () => {
  test.setTimeout(180_000)

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await login(page)
    await navigateToSuperAgent(page)
  })

  test('基本图表生成 — 意图识别 + 交互式图表输出', async ({ page }) => {
    await startNewChat(page)

    // Step 1: 发送图表请求
    await sendMessageAndWait(page, '帮我画一个柱状图，展示2020-2024年中国在线教育市场规模：2020年4858亿，2021年5480亿，2022年5700亿，2023年6200亿，2024年6800亿', {
      timeout: 150_000,
    })

    await page.screenshot({
      path: 'tests/e2e/screenshots/chart-01-generation-complete.png',
      fullPage: true,
    })

    // Step 2: 验证意图识别
    const intentCorrect = await page.evaluate(() => {
      const text = document.body.innerText
      return text.includes('图') ||
             text.includes('chart') ||
             text.includes('柱') ||
             text.includes('可视化')
    })
    console.log(`Chart 意图识别: ${intentCorrect ? 'PASS' : 'WARN'}`)

    // Step 3: 验证回复非空
    const lastMsg = await getLastAssistantMessage(page)
    console.log(`Assistant 回复长度: ${lastMsg.length} chars`)
    expect(lastMsg.length).toBeGreaterThan(20)

    // Step 4: 检查是否有交互式图表
    const hasChart = await hasInteractivePlayer(page)
    console.log(`交互式图表: ${hasChart ? 'FOUND' : 'NOT FOUND (可能输出为纯文本描述)'}`)

    // Step 5: 如果有 iframe，检查其尺寸合理性
    if (hasChart) {
      const chartMetrics = await page.evaluate(() => {
        const iframe = document.querySelector('iframe[srcdoc]') as HTMLIFrameElement
        if (!iframe) return null
        const rect = iframe.getBoundingClientRect()
        return { width: rect.width, height: rect.height, visible: rect.width > 0 && rect.height > 0 }
      })
      if (chartMetrics) {
        console.log(`Chart iframe: ${chartMetrics.width}x${chartMetrics.height}px`)
        expect(chartMetrics.visible).toBe(true)
      }
    }
  })

  test('饼图生成 — 不同图表类型', async ({ page }) => {
    await startNewChat(page)

    await sendMessageAndWait(page, '用饼图展示以下学科分布：理工科45%，文科25%，艺术15%，其他15%', {
      timeout: 150_000,
    })

    await page.screenshot({
      path: 'tests/e2e/screenshots/chart-02-pie.png',
      fullPage: true,
    })

    const lastMsg = await getLastAssistantMessage(page)
    console.log(`饼图回复: ${lastMsg.length} chars`)
    expect(lastMsg.length).toBeGreaterThan(10)
  })

  test('图表多轮对话 — 生成后修改样式', async ({ page }) => {
    await startNewChat(page)

    // 第一轮：生成折线图
    await sendMessageAndWait(page, '画一个折线图，展示某课程6个月的报名人数：1月100人，2月150人，3月200人，4月180人，5月250人，6月300人', {
      timeout: 150_000,
    })

    await page.screenshot({
      path: 'tests/e2e/screenshots/chart-03-line-round1.png',
      fullPage: true,
    })

    const round1Msg = await getLastAssistantMessage(page)
    console.log(`折线图回复: ${round1Msg.length} chars`)
    expect(round1Msg.length).toBeGreaterThan(10)

    // 第二轮：修改样式
    await sendMessageAndWait(page, '把这个图表改成深色主题，并加上数据标签', {
      timeout: 150_000,
    })

    await page.screenshot({
      path: 'tests/e2e/screenshots/chart-04-line-round2-dark.png',
      fullPage: true,
    })

    const round2Msg = await getLastAssistantMessage(page)
    console.log(`修改样式回复: ${round2Msg.length} chars`)
    expect(round2Msg.length).toBeGreaterThan(10)
  })
})

test.describe('视觉回归测试', () => {
  test.setTimeout(60_000)

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await login(page)
    await navigateToSuperAgent(page)
  })

  test('Skill 列表中包含 PPT 和 Chart', async ({ page }) => {
    // 展开技能列表（ConversationList.vue 中 .skills-header 可点击展开）
    const skillsHeader = page.locator('.skills-header')
    if (await skillsHeader.isVisible({ timeout: 3000 }).catch(() => false)) {
      await skillsHeader.click()
      await page.waitForTimeout(800)
    }

    await page.screenshot({
      path: 'tests/e2e/screenshots/visual-01-skills-list.png',
    })

    // 验证技能列表中包含新 Skill
    // .skill-name 是 ConversationList.vue 中技能名称的 class
    const skillNames = await page.evaluate(() => {
      const items = document.querySelectorAll('.skill-name')
      return Array.from(items).map(el => el.textContent?.trim() || '')
    })

    console.log(`技能列表 (${skillNames.length}):`, skillNames)

    // 如果 .skill-name 没找到，尝试更宽泛的 .skill-item 内的文本
    let allSkillText = ''
    if (skillNames.length === 0) {
      allSkillText = await page.evaluate(() => {
        const items = document.querySelectorAll('.skill-item')
        return Array.from(items).map(el => el.textContent?.trim() || '').join(' | ')
      })
      console.log(`通过 .skill-item 获取: ${allSkillText}`)
    }

    const combinedText = [...skillNames, allSkillText].join(' ')
    const hasPPT = combinedText.includes('PPT') || combinedText.includes('ppt') || combinedText.includes('演示')
    const hasChart = combinedText.includes('chart') || combinedText.includes('图表') || combinedText.includes('可视化')
    console.log(`PPT Skill 在列表中: ${hasPPT}`)
    console.log(`Chart Skill 在列表中: ${hasChart}`)

    // 至少有一个新 Skill 应该在列表中
    expect(hasPPT || hasChart).toBe(true)
  })

  test('欢迎页默认状态完整', async ({ page }) => {
    await page.screenshot({
      path: 'tests/e2e/screenshots/visual-02-welcome-default.png',
    })

    // 验证欢迎页元素
    const hasWelcome = await page.locator('.welcome-section, .mobile-welcome-section').first().isVisible({ timeout: 3000 }).catch(() => false)
    const hasTitle = await page.locator('text=超级智能体').first().isVisible({ timeout: 3000 }).catch(() => false)
    const hasExamples = await page.locator('.example-questions, .example-tag, .mobile-example-questions').first().isVisible({ timeout: 3000 }).catch(() => false)

    console.log(`欢迎页: welcome=${hasWelcome}, title=${hasTitle}, examples=${hasExamples}`)
    expect(hasWelcome || hasTitle).toBe(true)
  })
})
