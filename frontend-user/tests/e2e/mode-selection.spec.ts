/**
 * 模式选择功能 E2E 测试
 *
 * 测试场景：
 * 1. 用户选择辩论技能
 * 2. 输入话题后显示模式选择卡片
 * 3. 验证推荐模式正确
 * 4. 验证倒计时功能
 * 5. 用户选择模式后启动辩论
 */

import { test, expect } from '@playwright/test'

test.describe('模式选择功能测试', () => {
  test.beforeEach(async ({ page }) => {
    // 登录
    await page.goto('http://localhost:9002/login')
    await page.fill('input[type="text"]', 'wj2929')
    await page.fill('input[type="password"]', 'wj2929')
    await page.click('button:has-text("登录")')
    await page.waitForURL('**/qa/**')

    // 进入超级智能体页面
    await page.goto('http://localhost:9002/qa/super-agent')
    await page.waitForSelector('textarea')
  })

  test('应该显示模式选择卡片', async ({ page }) => {
    // 输入 @多专家辩论
    await page.fill('textarea', '@多专家辩论')
    await page.waitForSelector('.mention-list')
    await page.click('.mention-list .mention-item:has-text("多专家辩论")')

    // 输入话题
    await page.fill('textarea', '线上教学和线下教学哪个更好？')
    await page.click('button:has-text("发送")')

    // 验证模式选择卡片显示
    await expect(page.locator('.mode-selection-card')).toBeVisible({ timeout: 5000 })
    await expect(page.locator('text=选择讨论模式')).toBeVisible()
  })

  test('应该推荐正确的模式', async ({ page }) => {
    await page.fill('textarea', '@多专家辩论 线上教学和线下教学哪个更好？')
    await page.click('button:has-text("发送")')

    await page.waitForSelector('.mode-selection-card')

    // 验证"辩论"模式被推荐（有"推荐"标签）
    const debateMode = page.locator('.mode-item:has-text("辩论")')
    await expect(debateMode.locator('.badge:has-text("推荐")')).toBeVisible()
  })

  test('应该显示倒计时', async ({ page }) => {
    await page.fill('textarea', '@多专家辩论 如何提高教学质量？')
    await page.click('button:has-text("发送")')

    await page.waitForSelector('.mode-selection-card')

    // 验证倒计时显示
    const countdown = page.locator('.countdown')
    await expect(countdown).toBeVisible()

    const initialTime = await countdown.textContent()
    expect(initialTime).toMatch(/\d+s/)
  })

  test('应该能手动选择模式', async ({ page }) => {
    await page.fill('textarea', '@多专家辩论 为什么教育如此重要？')
    await page.click('button:has-text("发送")')

    await page.waitForSelector('.mode-selection-card')

    // 选择"苏格拉底"模式
    await page.click('.mode-item:has-text("苏格拉底")')

    // 验证选中状态
    await expect(page.locator('.mode-item:has-text("苏格拉底").selected')).toBeVisible()

    // 点击确认
    await page.click('button:has-text("确认开始")')

    // 验证辩论开始
    await expect(page.locator('text=专家')).toBeVisible({ timeout: 10000 })
  })

  test('倒计时结束应自动开始', async ({ page }) => {
    // 修改倒计时为3秒便于测试
    await page.addInitScript(() => {
      window.TEST_MODE_SELECTION_TIMEOUT = 3
    })

    await page.fill('textarea', '@多专家辩论 测试自动开始')
    await page.click('button:has-text("发送")')

    await page.waitForSelector('.mode-selection-card')

    // 等待倒计时结束（3秒 + 1秒缓冲）
    await page.waitForTimeout(4000)

    // 验证辩论自动开始
    await expect(page.locator('text=专家')).toBeVisible({ timeout: 5000 })
  })
})
