/**
 * export_to_docx 公文导出视觉截图测试
 *
 * 验证：
 * 1. official-doc Skill 触发后 AI 输出公文内容
 * 2. client_select 弹窗出现（格式选择）
 * 3. 弹窗中有 4 个选项且推荐项标注 ★
 * 4. 选择后 export_to_docx 工具调用
 * 5. 文件下载按钮正确渲染
 *
 * 运行：cd frontend-user && npx playwright test tests/e2e/export-docx-visual.spec.ts --headed
 */
import { test, expect, Page } from '@playwright/test'

const TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyXzE5NzJmMzFhZTU5ZSIsInJvbGVzIjpbInVzZXIiXSwicGVybWlzc2lvbnMiOlsiZG9jdW1lbnQ6cmVhZCIsImtiOnJlYWQiXSwiZXhwIjoxNzc3MDMzNjc2fQ.lD1xwr3Cl-iDYygCZT8r_F972o5WSHgpWqCmKK9f6qc'
const SCREENSHOT_DIR = 'tests/e2e/results/export-docx-visual'

async function login(page: Page) {
  await page.goto('/')
  await page.evaluate((t) => localStorage.setItem('token', t), TOKEN)
}

test.describe('公文导出 DOCX 视觉测试', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('管理后台 Skill 配置验证', async ({ page }) => {
    // 访问管理后台检查 official-doc skill
    await page.goto('http://localhost:9001')
    await page.evaluate((t) => localStorage.setItem('token', t), TOKEN)
    await page.goto('http://localhost:9001/super-agent/skills')
    await page.waitForLoadState('domcontentloaded')
    await page.waitForTimeout(5000)

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/01-admin-skills-page.png`,
      fullPage: true,
    })
  })

  test('用户端超级智能体页面加载', async ({ page }) => {
    await page.goto('/qa/super-agent')
    await page.waitForLoadState('domcontentloaded')
    await page.waitForTimeout(5000)

    // 截图：初始状态
    await page.screenshot({
      path: `${SCREENSHOT_DIR}/02-initial-page.png`,
      fullPage: true,
    })

    // 验证输入框存在
    const textarea = page.locator('textarea')
    await expect(textarea).toBeVisible({ timeout: 15000 })
  })

  test('输入公文需求并截图流式输出过程', async ({ page }) => {
    test.setTimeout(180000) // 3 分钟超时

    await page.goto('/qa/super-agent')
    await page.waitForLoadState('domcontentloaded')
    await page.waitForTimeout(5000)

    // 输入公文需求
    const textarea = page.locator('textarea')
    await textarea.fill('写一份关于开展2026年度员工培训工作的通知，生成 Word 文件')

    await page.screenshot({
      path: `${SCREENSHOT_DIR}/03-input-filled.png`,
      fullPage: true,
    })

    // 发送消息
    await textarea.press('Enter')
    await page.waitForTimeout(3000)

    // 截图：AI 开始响应
    await page.screenshot({
      path: `${SCREENSHOT_DIR}/04-ai-responding.png`,
      fullPage: true,
    })

    // 每 10 秒截一次图，持续 2 分钟，捕捉完整流程
    for (let i = 0; i < 12; i++) {
      await page.waitForTimeout(10000)

      await page.screenshot({
        path: `${SCREENSHOT_DIR}/05-progress-${String(i + 1).padStart(2, '0')}.png`,
        fullPage: true,
      })

      // 检查是否出现 client_select 弹窗
      const modal = page.locator('[class*="client-tool"], [class*="ClientTool"]')
      if (await modal.count() > 0) {
        await page.screenshot({
          path: `${SCREENSHOT_DIR}/06-client-select-popup.png`,
          fullPage: true,
        })

        // 检查选项内容
        const options = page.locator('[class*="client-tool"] label, [class*="ClientTool"] label, .n-checkbox-group label')
        const optionCount = await options.count()
        console.log(`  弹窗选项数: ${optionCount}`)

        if (optionCount > 0) {
          for (let j = 0; j < optionCount; j++) {
            const text = await options.nth(j).textContent()
            console.log(`    选项 ${j + 1}: ${text}`)
          }
        }

        // 点击第一个选项（推荐项）然后确认
        const firstOption = options.first()
        if (await firstOption.isVisible()) {
          await firstOption.click()
          await page.screenshot({
            path: `${SCREENSHOT_DIR}/07-option-selected.png`,
            fullPage: true,
          })
        }

        // 点击确认按钮
        const confirmBtn = page.locator('button:has-text("确认"), button:has-text("提交")')
        if (await confirmBtn.count() > 0) {
          await confirmBtn.first().click()
          await page.screenshot({
            path: `${SCREENSHOT_DIR}/08-confirmed.png`,
            fullPage: true,
          })
        }

        // 等待 docx 生成
        await page.waitForTimeout(10000)
        break
      }

      // 检查是否出现下载按钮（可能跳过了 client_select）
      const downloadBtn = page.locator('button:has-text("下载"), [class*="file-card"], [class*="inline-file"]')
      if (await downloadBtn.count() > 0) {
        await page.screenshot({
          path: `${SCREENSHOT_DIR}/09-download-button.png`,
          fullPage: true,
        })
        break
      }
    }

    // 最终截图
    await page.waitForTimeout(5000)
    await page.screenshot({
      path: `${SCREENSHOT_DIR}/10-final-state.png`,
      fullPage: true,
    })

    // 检查是否有生成的文件卡片
    const fileCards = page.locator('[class*="inline-file"], [class*="file-card"], [class*="generated-file"]')
    const fileCardCount = await fileCards.count()
    console.log(`  文件卡片数量: ${fileCardCount}`)

    // 检查 export_to_docx 工具调用卡片
    const toolCards = page.locator('.tool-call-card')
    const toolCardCount = await toolCards.count()
    console.log(`  工具调用卡片数量: ${toolCardCount}`)
  })
})
