/**
 * 模式选择卡片视觉回归测试
 */

import { test, expect } from '@playwright/test'

test.describe('模式选择卡片视觉测试', () => {
  test('模式选择卡片样式正确', async ({ page }) => {
    await page.goto('http://localhost:9002/test-mode-selection.html')
    await page.waitForSelector('.mode-selection-card')
    await expect(page.locator('.mode-selection-card')).toHaveScreenshot('mode-selection-card.png')
  })

  test('选中状态样式正确', async ({ page }) => {
    await page.goto('http://localhost:9002/test-mode-selection.html')
    await page.waitForSelector('.mode-selection-card')
    await page.click('.mode-item[data-mode="seminar"]')
    await page.waitForTimeout(300)
    await expect(page.locator('.mode-selection-card')).toHaveScreenshot('mode-selected-seminar.png')
  })

  test('倒计时显示正确', async ({ page }) => {
    await page.goto('http://localhost:9002/test-mode-selection.html')
    const countdown = page.locator('#countdown')
    await expect(countdown).toBeVisible()
    const text = await countdown.textContent()
    expect(text).toMatch(/\d+s/)
  })
})
