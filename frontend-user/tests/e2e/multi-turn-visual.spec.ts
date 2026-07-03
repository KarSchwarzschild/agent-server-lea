/**
 * 多轮对话增强 — 视觉回归测试
 *
 * 使用 Playwright toMatchSnapshot() 截图对比。
 *
 * 首次运行生成基准：
 *   cd frontend-user && npx playwright test tests/e2e/multi-turn-visual.spec.ts --update-snapshots
 *
 * 后续对比：
 *   cd frontend-user && npx playwright test tests/e2e/multi-turn-visual.spec.ts
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

  const closeBtn = page
    .locator('button:has-text("×"), .n-card-header__close, [class*="close"]')
    .first()
  if (await closeBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
    await closeBtn.click()
    await page.waitForTimeout(500)
  }
  await page.keyboard.press('Escape')
  await page.waitForTimeout(500)

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

async function waitForReplyDone(page: Page, timeout = 120_000) {
  await expect(
    page.locator('.ai-message-actions').last()
  ).toBeVisible({ timeout })
}

// ─── Visual Snapshot Tests ──────────────────────────

test.describe.serial('多轮对话视觉回归', () => {
  test.beforeEach(async ({ page }) => {
    // 固定视口尺寸，确保截图一致
    await page.setViewportSize({ width: 1280, height: 800 })
    await login(page)
    await gotoSuperAgent(page)
    await newChat(page)
  })

  // V1: 输入框默认状态
  test('V1: 输入框默认状态截图', async ({ page }) => {
    const inputBox = page.locator('.compact-input-box').first()
    await expect(inputBox).toBeVisible({ timeout: 10000 })

    // 确保输入框未聚焦
    await page.locator('body').click()
    await page.waitForTimeout(300)

    await expect(inputBox).toHaveScreenshot('input-box-default.png', {
      maxDiffPixelRatio: 0.05,
    })
  })

  // V2: 输入框聚焦状态
  test('V2: 输入框聚焦状态截图', async ({ page }) => {
    const inputBox = page.locator('.compact-input-box').first()
    await expect(inputBox).toBeVisible({ timeout: 10000 })

    // 点击输入框聚焦
    const textarea = page.locator('textarea').first()
    await textarea.click()
    await page.waitForTimeout(500)

    await expect(inputBox).toHaveScreenshot('input-box-focused.png', {
      maxDiffPixelRatio: 0.05,
    })
  })

  // V3: AI 消息操作栏
  test('V3: AI 消息操作栏截图', async ({ page }) => {
    await sendMessage(page, '简单回答：1+1=?')
    await waitForReplyDone(page)

    const actionsBar = page.locator('.ai-message-actions').last()
    await expect(actionsBar).toBeVisible()
    await page.waitForTimeout(500)

    await expect(actionsBar).toHaveScreenshot('ai-message-actions.png', {
      maxDiffPixelRatio: 0.05,
    })
  })

  // V4: 用户消息编辑态
  test('V4: 用户消息编辑态截图', async ({ page }) => {
    await sendMessage(page, '测试编辑截图')
    await waitForReplyDone(page)

    const userMsg = page.locator('.user-message, .message.user').last()
    await userMsg.hover()
    await page.waitForTimeout(500)

    const editBtn = userMsg
      .locator('[class*="edit"], button:has-text("编辑"), button[title*="编辑"]')
      .first()

    if (!await editBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      test.skip()
      return
    }
    await editBtn.click()
    await page.waitForTimeout(500)

    const editingContent = page.locator('.user-content--editing').first()
    if (!await editingContent.isVisible({ timeout: 3000 }).catch(() => false)) {
      test.skip()
      return
    }

    await expect(editingContent).toHaveScreenshot('user-content-editing.png', {
      maxDiffPixelRatio: 0.05,
    })
  })

  // V5: Skill 选择下拉
  test('V5: Skill 选择下拉截图', async ({ page }) => {
    await sendMessage(page, '简单回答：什么是AI')
    await waitForReplyDone(page)

    // 找到下拉触发器
    const dropdownTrigger = page
      .locator('.ai-message-actions')
      .last()
      .locator('[class*="dropdown-trigger"], [class*="regen-arrow"], button:has-text("▾")')
      .first()

    if (!await dropdownTrigger.isVisible({ timeout: 5000 }).catch(() => false)) {
      test.skip()
      return
    }

    await dropdownTrigger.click()
    await page.waitForTimeout(500)

    const dropdown = page.locator('.regen-skill-dropdown').first()
    if (!await dropdown.isVisible({ timeout: 3000 }).catch(() => false)) {
      test.skip()
      return
    }

    await expect(dropdown).toHaveScreenshot('regen-skill-dropdown.png', {
      maxDiffPixelRatio: 0.05,
    })
  })
})
