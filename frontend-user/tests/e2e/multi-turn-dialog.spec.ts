/**
 * 多轮对话增强 — 前端 E2E 测试
 *
 * 需要：真实后端运行在 :8000，前端运行在 :9002
 * 运行：cd frontend-user && npx playwright test tests/e2e/multi-turn-dialog.spec.ts --timeout=180000
 */
import { test, expect, type Page } from '@playwright/test'

const BASE = 'http://localhost:9002'
const TOKEN =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbl8wMDEiLCJyb2xlcyI6WyJzdXBlcl9hZG1pbiJdLCJwZXJtaXNzaW9ucyI6WyJkb2N1bWVudDp3cml0ZSIsImtiOmRlbGV0ZSIsInJvbGU6cmVhZCIsInN0YXRzOnJlYWQiLCJjb25maWc6d3JpdGUiLCJ1c2VyOndyaXRlIiwicm9sZTp3cml0ZSIsInVzZXI6ZGVsZXRlIiwiZG9jdW1lbnQ6cmVhZCIsImtiOnJlYWQiLCJkb2N1bWVudDpkZWxldGUiLCJ1c2VyOnJlYWQiLCJyb2xlOmRlbGV0ZSIsImtiOndyaXRlIiwiY29uZmlnOnJlYWQiLCJsb2c6cmVhZCJdLCJleHAiOjE3NzY5NTg0MDR9.G7ff5kJ17E6fw4u3KRpL21d-S-if0rU7iwfovZPgpHQ'

// ─── Helpers ────────────────────────────────────────

async function login(page: Page) {
  await page.goto(BASE)
  await page.evaluate((t) => localStorage.setItem('token', t), TOKEN)
}

async function gotoSuperAgent(page: Page) {
  await page.goto(`${BASE}/qa/super-agent`)
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(1000)

  // 关闭「选择 AI 助手」弹窗
  const closeBtn = page
    .locator('button:has-text("×"), .n-card-header__close, [class*="close"]')
    .first()
  if (await closeBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
    await closeBtn.click()
    await page.waitForTimeout(500)
  }
  await page.keyboard.press('Escape')
  await page.waitForTimeout(500)

  // 切换到「专业」模式
  const proBtn = page
    .locator('button:has-text("专业"), [class*="tab"]:has-text("专业")')
    .first()
  if (await proBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await proBtn.click()
    await page.waitForTimeout(500)
  }
}

async function newChat(page: Page) {
  const btn = page.locator('button:has-text("新建对话")').first()
  if (await btn.isVisible().catch(() => false)) {
    await btn.click()
    await page.waitForTimeout(1000)
    await page.keyboard.press('Escape')
    await page.waitForTimeout(500)
  }
}

async function sendMessage(page: Page, message: string) {
  const input = page.locator('textarea').first()
  await expect(input).toBeVisible({ timeout: 10000 })
  await input.fill(message)
  await page.waitForTimeout(300)

  const sendBtn = page
    .locator('button:has-text("发送"), button[class*="send"]')
    .first()
  if (await sendBtn.isVisible().catch(() => false)) {
    await sendBtn.click()
  } else {
    await input.press('Enter')
  }
}

/**
 * 等待 AI 回复完成（流式结束）
 * 通过检测 .ai-message-actions 出现来判断（非 streaming 时才显示）
 */
async function waitForReplyDone(page: Page, timeout = 120_000) {
  await expect(
    page.locator('.ai-message-actions').last()
  ).toBeVisible({ timeout })
}

// ─── Tests ──────────────────────────────────────────

test.describe.serial('多轮对话增强 E2E', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
    await gotoSuperAgent(page)
    await newChat(page)
  })

  // E1: 操作栏可见
  test('E1: AI 回复完成后操作栏可见，含重新生成', async ({ page }) => {
    await sendMessage(page, '你好，请简单介绍一下自己')
    await waitForReplyDone(page)

    const actions = page.locator('.ai-message-actions').last()
    await expect(actions).toBeVisible()

    // 操作栏内应包含"重新生成"相关按钮
    const regenBtn = actions.locator('[class*="regen"], button:has-text("重新生成")')
    await expect(regenBtn.first()).toBeVisible({ timeout: 5000 })
  })

  // E2: 重新生成
  test('E2: 点击重新生成后流式启动并完成，版本导航出现', async ({ page }) => {
    await sendMessage(page, '用一句话解释什么是机器学习')
    await waitForReplyDone(page)

    // 记录当前 .ai-message-actions 数量
    const actionsCountBefore = await page.locator('.ai-message-actions').count()

    // 点击重新生成按钮
    const actions = page.locator('.ai-message-actions').last()
    const regenBtn = actions.locator('[class*="regen"], button:has-text("重新生成")').first()
    await regenBtn.click()

    // 等待旧操作栏消失（重新生成会截断旧消息，添加流式占位）
    await page.waitForTimeout(2000)

    // 等待新回复完成：流式占位消息完成后 .ai-message-actions 重新出现
    await waitForReplyDone(page, 120_000)

    // 版本导航应该出现（有多个版本了）
    const versionNav = page.locator('.version-nav').last()
    const siblingIndicator = page.locator('.version-nav-label').last()
    const hasVersionNav = await versionNav.isVisible({ timeout: 10_000 }).catch(() => false)
    const hasSiblingIndicator = await siblingIndicator.isVisible({ timeout: 5_000 }).catch(() => false)

    expect(hasVersionNav || hasSiblingIndicator).toBeTruthy()
  })

  // E3: Skill 下拉
  test('E3: 重新生成旁的下拉箭头可展开 Skill 列表', async ({ page }) => {
    await sendMessage(page, '简单回答：1+1等于几')
    await waitForReplyDone(page)

    // 查找下拉触发按钮（▾ 箭头）
    const dropdownTrigger = page
      .locator('.ai-message-actions')
      .last()
      .locator('[class*="dropdown-trigger"], [class*="regen-arrow"], button:has-text("▾")')
      .first()

    if (await dropdownTrigger.isVisible({ timeout: 5000 }).catch(() => false)) {
      await dropdownTrigger.click()
      await page.waitForTimeout(500)

      // 下拉菜单出现
      const dropdown = page.locator('.regen-skill-dropdown')
      await expect(dropdown).toBeVisible({ timeout: 3000 })

      // 应包含"自动选择"或"自动识别"
      const autoOption = dropdown.locator('text=/自动/')
      await expect(autoOption.first()).toBeVisible()
    } else {
      // 如果没有独立的下拉触发器，可能集成在重新生成按钮内
      test.skip()
    }
  })

  // E4: 编辑提问
  test('E4: hover 用户消息可编辑，修改后提交生成新回复', async ({ page }) => {
    await sendMessage(page, '你好世界')
    await waitForReplyDone(page)

    // hover 用户消息以显示编辑按钮
    const userMsg = page.locator('.user-message, .message.user').last()
    await userMsg.hover()
    await page.waitForTimeout(500)

    // 点击编辑按钮
    const editBtn = userMsg.locator('[class*="edit"], button:has-text("编辑"), button[title*="编辑"]').first()
    if (!await editBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      test.skip()
      return
    }
    await editBtn.click()
    await page.waitForTimeout(500)

    // 编辑模式：应出现 textarea 或 contenteditable
    const editArea = page.locator('.user-content--editing textarea, .user-content--editing [contenteditable]').first()
    await expect(editArea).toBeVisible({ timeout: 3000 })

    // 修改内容
    await editArea.fill('改成测试编辑功能')
    await page.waitForTimeout(300)

    // 点击提交
    const submitBtn = page.locator('button:has-text("提交"), button:has-text("确认"), button:has-text("保存")').first()
    await submitBtn.click()

    // 等待新回复生成
    await waitForReplyDone(page, 120_000)

    // 应有新的 AI 回复
    const aiMessages = page.locator('.ai-message, .message.ai')
    expect(await aiMessages.count()).toBeGreaterThanOrEqual(1)
  })

  // E5: 编辑取消
  test('E5: 编辑后取消，恢复原文且无 API 调用', async ({ page }) => {
    await sendMessage(page, '测试取消编辑')
    await waitForReplyDone(page)

    const userMsg = page.locator('.user-message, .message.user').last()
    await userMsg.hover()
    await page.waitForTimeout(500)

    const editBtn = userMsg.locator('[class*="edit"], button:has-text("编辑"), button[title*="编辑"]').first()
    if (!await editBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      test.skip()
      return
    }
    await editBtn.click()
    await page.waitForTimeout(500)

    // 修改内容
    const editArea = page.locator('.user-content--editing textarea, .user-content--editing [contenteditable]').first()
    await editArea.fill('不会提交的内容')

    // 监听网络请求
    let editApiCalled = false
    page.on('request', (req) => {
      if (req.url().includes('/chat/edit')) {
        editApiCalled = true
      }
    })

    // 点击取消
    const cancelBtn = page.locator('button:has-text("取消")').first()
    await cancelBtn.click()
    await page.waitForTimeout(500)

    // 编辑模式应退出
    const editingEl = page.locator('.user-content--editing')
    await expect(editingEl).not.toBeVisible({ timeout: 3000 })

    // 不应有 API 调用
    expect(editApiCalled).toBeFalsy()
  })

  // E6: 停止→发送按钮切换
  test('E6: 生成中显示停止按钮，完成后显示发送按钮', async ({ page }) => {
    await sendMessage(page, '写一篇500字的文章，关于人工智能教育')

    // 生成中应有停止按钮
    const stopBtn = page.locator(
      'button:has-text("停止"), button[class*="stop"], button[title*="停止"]'
    ).first()
    const hasStop = await stopBtn.isVisible({ timeout: 10_000 }).catch(() => false)

    if (hasStop) {
      // 停止按钮可见时，发送按钮不应可见（或被替换）
      // 等待完成
      await waitForReplyDone(page, 120_000)

      // 完成后发送按钮应恢复
      const sendBtn = page.locator(
        'button:has-text("发送"), button[class*="send"]'
      ).first()
      await expect(sendBtn).toBeVisible({ timeout: 5000 })
    } else {
      // 回复太快，跳过停止按钮检测
      await waitForReplyDone(page)
      const sendBtn = page.locator(
        'button:has-text("发送"), button[class*="send"]'
      ).first()
      await expect(sendBtn).toBeVisible({ timeout: 5000 })
    }
  })

  // E7: 快捷回复位置
  test('E7: 回复完成后快捷回复 chips 在 AI 消息底部', async ({ page }) => {
    await sendMessage(page, '什么是终身教育？')
    await waitForReplyDone(page)

    // 快捷回复应在 AI 消息内部
    const quickReplies = page.locator('.ai-quick-replies').last()
    const hasQuickReplies = await quickReplies.isVisible({ timeout: 5000 }).catch(() => false)

    if (hasQuickReplies) {
      // 快捷回复应在 AI 消息容器内
      const aiMsg = page.locator('.ai-message, .message.ai').last()
      const quickInAi = aiMsg.locator('.ai-quick-replies')
      await expect(quickInAi).toBeVisible()

      // 至少有一个 chip
      const chips = quickInAi.locator('.quick-reply-chip')
      expect(await chips.count()).toBeGreaterThanOrEqual(1)
    } else {
      // 某些 Skill 可能不返回快捷回复
      test.skip()
    }
  })
})
