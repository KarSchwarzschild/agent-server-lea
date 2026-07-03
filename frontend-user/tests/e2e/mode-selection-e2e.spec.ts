/**
 * 模式选择端到端测试
 */

import { test, expect } from '@playwright/test'

test('模式选择完整流程', async ({ page }) => {
  await page.goto('http://localhost:9002/login')
  await page.fill('input[placeholder*="用户名"]', 'wj2929')
  await page.fill('input[placeholder*="密码"]', 'wj2929')
  await page.click('button:has-text("登录")')
  await page.waitForTimeout(2000)

  await page.goto('http://localhost:9002/qa/super-agent')
  await page.waitForTimeout(3000)

  // 选择多专家辩论技能
  await page.click('button:has-text("选择技能")')
  await page.waitForTimeout(500)
  await page.click('text=多专家辩论')
  await page.waitForTimeout(500)

  // 输入话题
  await page.fill('textarea', '高校应该优先发展线上教学还是线下教学？')
  await page.waitForTimeout(500)

  // 点击发送
  await page.click('button:has-text("发送")')
  console.log('✅ 已点击发送')

  // 等待模式选择卡片
  await page.waitForSelector('.mode-selection-card', { timeout: 30000 })
  console.log('✅ 模式选择卡片已显示')

  // 验证卡片
  await expect(page.locator('.mode-item:has-text("辩论")')).toBeVisible()
  await expect(page.locator('.mode-item:has-text("研讨")')).toBeVisible()

  // 选择研讨
  await page.click('.mode-item:has-text("研讨")')
  await page.waitForTimeout(300)

  // 点击确认
  await page.click('.confirm-btn')
  console.log('✅ 已点击确认')

  // 验证卡片消失
  await expect(page.locator('.mode-selection-card')).not.toBeVisible({ timeout: 5000 })
  console.log('✅ 测试通过')
})
