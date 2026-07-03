/**
 * Phase 1 真实 LLM 视觉测试 — /qa/super-agent 页面 + @skill 强制路由
 *
 * 运行：cd frontend-user && npx playwright test tests/e2e/phase1-live.spec.ts --timeout=180000
 */
import { test, expect, type Page } from '@playwright/test'

const BASE = 'http://localhost:9002'
const TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbl8wMDEiLCJyb2xlcyI6WyJzdXBlcl9hZG1pbiJdLCJwZXJtaXNzaW9ucyI6WyJkb2N1bWVudDp3cml0ZSIsImtiOmRlbGV0ZSIsInJvbGU6cmVhZCIsInN0YXRzOnJlYWQiLCJjb25maWc6d3JpdGUiLCJ1c2VyOndyaXRlIiwicm9sZTp3cml0ZSIsInVzZXI6ZGVsZXRlIiwiZG9jdW1lbnQ6cmVhZCIsImtiOnJlYWQiLCJkb2N1bWVudDpkZWxldGUiLCJ1c2VyOnJlYWQiLCJyb2xlOmRlbGV0ZSIsImtiOndyaXRlIiwiY29uZmlnOnJlYWQiLCJsb2c6cmVhZCJdLCJleHAiOjE3NzY5NTg0MDR9.G7ff5kJ17E6fw4u3KRpL21d-S-if0rU7iwfovZPgpHQ'

async function login(page: Page) {
  await page.goto(BASE)
  await page.evaluate((t) => localStorage.setItem('token', t), TOKEN)
}

/** 进入 Super Agent 页面，关闭弹窗，切换到专业模式 */
async function gotoSuperAgent(page: Page) {
  await page.goto(`${BASE}/qa/super-agent`)
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(1000)

  // 关闭「选择 AI 助手」弹窗（如果出现）
  const closeBtn = page.locator('button:has-text("×"), .n-card-header__close, [class*="close"]').first()
  if (await closeBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
    await closeBtn.click()
    await page.waitForTimeout(500)
  }
  await page.keyboard.press('Escape')
  await page.waitForTimeout(500)

  // 切换到「专业」模式
  const proBtn = page.locator('button:has-text("专业"), [class*="tab"]:has-text("专业")').first()
  if (await proBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
    await proBtn.click()
    await page.waitForTimeout(500)
  }
}

/** 新建对话 */
async function newChat(page: Page) {
  const btn = page.locator('button:has-text("新建对话")').first()
  if (await btn.isVisible().catch(() => false)) {
    await btn.click()
    await page.waitForTimeout(1000)
    // 可能再次弹出助手选择
    await page.keyboard.press('Escape')
    await page.waitForTimeout(500)
  }
}

/** 发送消息（Super Agent 页面用 Enter 或点发送按钮） */
async function sendMessage(page: Page, message: string) {
  const input = page.locator('textarea').first()
  await expect(input).toBeVisible({ timeout: 10000 })
  await input.fill(message)
  await page.waitForTimeout(300)

  // 尝试点发送按钮
  const sendBtn = page.locator('button:has-text("发送"), button[class*="send"]').first()
  if (await sendBtn.isVisible().catch(() => false)) {
    await sendBtn.click()
  } else {
    // Enter 发送
    await input.press('Enter')
  }
}

/** 通过 @ mention 选择 Skill 并发送 */
async function sendWithSkill(page: Page, skillName: string, message: string) {
  const input = page.locator('textarea').first()
  await expect(input).toBeVisible({ timeout: 10000 })

  // 输入 @ 触发 mention 下拉
  await input.fill('@')
  await page.waitForTimeout(500)
  await input.pressSequentially(skillName, { delay: 50 })
  await page.waitForTimeout(800)

  // 点选下拉项
  const dropdownItem = page.locator(`[class*="mention"] >> text=${skillName}`).first()
  if (await dropdownItem.isVisible({ timeout: 2000 }).catch(() => false)) {
    await dropdownItem.click()
    await page.waitForTimeout(500)
  }

  // 输入实际消息
  await input.fill(message)
  await page.waitForTimeout(300)

  // 发送
  const sendBtn = page.locator('button:has-text("发送"), button[class*="send"]').first()
  if (await sendBtn.isVisible().catch(() => false)) {
    await sendBtn.click()
  } else {
    await input.press('Enter')
  }
}

// ─── Tests ──────────────────────────────────────────

test.describe('Phase 1 Live', () => {

test('1. 单 Skill 回归 — QA 正常输出', async ({ page }) => {

  await login(page)
  await gotoSuperAgent(page)
  await newChat(page)

  await sendMessage(page, '搜一下2024年终身教育政策')

  // 等待内容出现
  await page.waitForTimeout(25000)
  await page.screenshot({ path: 'tests/e2e/screenshots/phase1-live-single-skill.png', fullPage: true })
})

// ─── 2. QuizBlock — @quiz-generator ────────────────

test('2. QuizBlock — @quiz-generator 出题', async ({ page }) => {
  test.setTimeout(120000)
  await login(page)
  await gotoSuperAgent(page)
  await newChat(page)

  await sendWithSkill(page, 'quiz-generator', '根据远程教育理论出3道选择题')

  await page.waitForTimeout(50000)
  await page.screenshot({ path: 'tests/e2e/screenshots/phase1-live-quizblock.png', fullPage: true })

  const quizBlocks = page.locator('.quiz-block, [class*="quiz-block"], .quiz-container')
  console.log(`QuizBlock 数量: ${await quizBlocks.count()}`)
})

// ─── 3. 深度调研 — @deep-research ──────────────────

test('3. 深度调研 — @deep-research plan 面板', async ({ page }) => {
  test.setTimeout(180000)
  await login(page)
  await gotoSuperAgent(page)
  await newChat(page)

  await sendWithSkill(page, 'deep-research', '帮我调研远程教育质量评估方法')

  // 等 plan 面板
  await page.waitForTimeout(15000)
  await page.screenshot({ path: 'tests/e2e/screenshots/phase1-live-research-plan.png', fullPage: true })

  const planPanel = page.locator('.plan-panel, .execution-plan, [class*="plan"]')
  console.log(`Plan 面板: ${await planPanel.count()}`)

  // 继续等 outline
  let outlineFound = false
  for (let i = 0; i < 8; i++) {
    await page.waitForTimeout(15000)
    const editor = page.locator('.outline-editor')
    if (await editor.isVisible().catch(() => false)) {
      outlineFound = true
      await page.screenshot({ path: 'tests/e2e/screenshots/phase1-live-outline-editor.png', fullPage: true })
      console.log(`OutlineEditor 出现！（${(i + 1) * 15 + 15}s 后）`)
      break
    }
    if (i === 2) {
      await page.screenshot({ path: 'tests/e2e/screenshots/phase1-live-research-mid.png', fullPage: true })
    }
  }
  await page.screenshot({ path: 'tests/e2e/screenshots/phase1-live-research-final.png', fullPage: true })
  console.log(`OutlineEditor 出现: ${outlineFound}`)
})

// ─── 4. ActionSequence ─────────────────────────────

test('4. ActionSequence — 搜文献写综述', async ({ page }) => {
  test.setTimeout(180000)
  await login(page)
  await gotoSuperAgent(page)
  await newChat(page)

  await sendMessage(page, '帮我搜集远程教育相关文献然后写一份综述')

  await page.waitForTimeout(15000)
  await page.screenshot({ path: 'tests/e2e/screenshots/phase1-live-sequence-step1.png', fullPage: true })

  await page.waitForTimeout(45000)
  await page.screenshot({ path: 'tests/e2e/screenshots/phase1-live-sequence-step2.png', fullPage: true })

  const steps = page.locator('.plan-step, [class*="plan-step"], [class*="step-item"]')
  console.log(`Plan step 元素: ${await steps.count()}`)
})

})
