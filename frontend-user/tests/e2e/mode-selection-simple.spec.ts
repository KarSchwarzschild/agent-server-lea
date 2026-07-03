/**
 * 模式选择核心功能测试（简化版）
 */

import { test, expect } from '@playwright/test'

test('模式选择卡片显示和点击', async ({ page }) => {
  // 直接访问测试页面
  await page.goto('http://localhost:9002/test-mode-selection.html')

  // 验证卡片显示
  await expect(page.locator('.mode-selection-card')).toBeVisible()
  await expect(page.locator('text=选择讨论模式')).toBeVisible()

  // 验证三个模式
  await expect(page.locator('.mode-item[data-mode="debate"]')).toBeVisible()
  await expect(page.locator('.mode-item[data-mode="seminar"]')).toBeVisible()
  await expect(page.locator('.mode-item[data-mode="socratic"]')).toBeVisible()

  // 点击研讨模式
  await page.click('.mode-item[data-mode="seminar"]')
  await page.waitForTimeout(300)

  // 验证选中状态
  await expect(page.locator('.mode-item[data-mode="seminar"]')).toHaveClass(/selected/)

  // 点击确认按钮
  await page.click('#confirmBtn')
  await page.waitForTimeout(500)

  // 验证日志输出
  const logs = await page.evaluate(() => {
    return document.getElementById('log')?.textContent || ''
  })

  expect(logs).toContain('选择模式: seminar')
  expect(logs).toContain('点击确认按钮')
  expect(logs).toContain('✅ 模拟发送请求')

  console.log('✅ 测试通过')
})
