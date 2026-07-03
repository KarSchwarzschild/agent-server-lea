/**
 * 模式选择卡片点击测试
 */

import { test, expect } from '@playwright/test'

test.describe('模式选择卡片点击功能', () => {
  test('点击确认开始按钮应该启动辩论', async ({ page }) => {
    // 监听控制台日志
    const logs: string[] = []
    page.on('console', msg => logs.push(msg.text()))

    // 访问页面
    await page.goto('http://localhost:9002/login')
    await page.fill('input[placeholder*="用户名"]', 'wj2929')
    await page.fill('input[placeholder*="密码"]', 'wj2929')
    await page.click('button:has-text("登录")')
    await page.waitForTimeout(2000)

    // 进入超级智能体
    await page.goto('http://localhost:9002/qa/super-agent')
    await page.waitForSelector('textarea', { timeout: 10000 })

    // 先选择技能（点击技能选择按钮）
    await page.click('button:has-text("选择技能")')
    await page.waitForTimeout(500)

    // 选择多专家辩论技能
    await page.click('text=多专家辩论')
    await page.waitForTimeout(500)

    // 输入话题
    await page.fill('textarea', '线上教学和线下教学哪个更好？')
    await page.waitForTimeout(500)

    // 点击发送
    await page.click('button:has-text("发送")')

    // 等待模式选择卡片出现
    await page.waitForSelector('.mode-selection-card', { timeout: 10000 })
    console.log('✅ 模式选择卡片已显示')

    // 验证卡片内容
    await expect(page.locator('text=选择讨论模式')).toBeVisible()
    await expect(page.locator('.mode-item:has-text("辩论")')).toBeVisible()
    await expect(page.locator('.mode-item:has-text("研讨")')).toBeVisible()
    await expect(page.locator('.mode-item:has-text("苏格拉底")')).toBeVisible()

    // 点击"确认开始"按钮
    console.log('点击确认开始按钮...')
    await page.click('button:has-text("确认开始")')

    // 等待一下让日志输出
    await page.waitForTimeout(1000)

    // 检查控制台日志
    const hasConfirmLog = logs.some(log => log.includes('[ModeConfirm] 触发'))
    console.log('控制台日志:', logs.filter(l => l.includes('ModeConfirm')))

    if (!hasConfirmLog) {
      console.error('❌ 未检测到 [ModeConfirm] 日志')
      throw new Error('点击事件未触发')
    }

    console.log('✅ 点击事件已触发')

    // 验证模式选择卡片消失
    await expect(page.locator('.mode-selection-card')).not.toBeVisible({ timeout: 5000 })
    console.log('✅ 模式选择卡片已消失')

    // 验证辩论开始（应该有新的消息）
    await page.waitForTimeout(2000)
    const messageCount = await page.locator('.message').count()
    console.log(`消息数量: ${messageCount}`)

    if (messageCount < 2) {
      throw new Error('辩论未启动，消息数量不足')
    }

    console.log('✅ 测试通过')
  })
})
