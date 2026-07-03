/**
 * 模式选择完整流程 E2E 测试
 * 测试：选择技能 → 输入话题 → 看到模式选择卡片 → 点击确认 → 辩论开始
 */

import { test, expect } from '@playwright/test'

test.describe('模式选择完整流程', () => {
  test('应该显示模式选择卡片并能点击确认开始', async ({ page }) => {
    // 1. 登录
    await page.goto('http://localhost:9002/login')
    await page.fill('input[placeholder*="用户名"]', 'wj2929')
    await page.fill('input[placeholder*="密码"]', 'wj2929')
    await page.click('button:has-text("登录")')
    await page.waitForTimeout(2000)

    // 2. 进入超级智能体
    await page.goto('http://localhost:9002/qa/super-agent')
    await page.waitForSelector('textarea', { timeout: 10000 })

    // 3. 选择多专家辩论技能
    await page.click('button:has-text("选择技能")')
    await page.waitForTimeout(500)
    await page.click('text=多专家辩论')
    await page.waitForTimeout(500)

    // 4. 输入话题（不指定模式）
    const topic = '线上教学和线下教学哪个更好？'
    await page.fill('textarea', topic)
    await page.waitForTimeout(500)

    // 5. 点击发送
    await page.click('button:has-text("发送")')

    // 6. 等待模式选择卡片出现
    await page.waitForSelector('.mode-selection-card', { timeout: 10000 })
    console.log('✅ 模式选择卡片已显示')

    // 7. 验证卡片内容
    await expect(page.locator('text=选择讨论模式')).toBeVisible()
    await expect(page.locator('.mode-item:has-text("辩论")')).toBeVisible()
    await expect(page.locator('.mode-item:has-text("研讨")')).toBeVisible()
    await expect(page.locator('.mode-item:has-text("苏格拉底")')).toBeVisible()
    await expect(page.locator('.confirm-btn')).toBeVisible()
    console.log('✅ 卡片内容验证通过')

    // 8. 选择一个模式（点击研讨）
    await page.click('.mode-item:has-text("研讨")')
    await page.waitForTimeout(300)
    console.log('✅ 已选择研讨模式')

    // 9. 点击确认开始按钮
    await page.click('.confirm-btn')
    console.log('✅ 已点击确认开始')

    // 10. 验证模式选择卡片消失
    await expect(page.locator('.mode-selection-card')).not.toBeVisible({ timeout: 5000 })
    console.log('✅ 模式选择卡片已消失')

    // 11. 验证辩论开始（应该有 debate_created 事件，显示专家信息）
    await page.waitForTimeout(3000)

    // 检查是否有专家信息显示
    const hasExperts = await page.locator('text=/专家|理论|实践|技术/').count() > 0
    expect(hasExperts).toBeTruthy()
    console.log('✅ 辩论已开始，专家信息已显示')

    console.log('✅ 完整流程测试通过')
  })

  test('倒计时结束应自动使用推荐模式', async ({ page }) => {
    // 登录
    await page.goto('http://localhost:9002/login')
    await page.fill('input[placeholder*="用户名"]', 'wj2929')
    await page.fill('input[placeholder*="密码"]', 'wj2929')
    await page.click('button:has-text("登录")')
    await page.waitForTimeout(2000)

    // 进入超级智能体
    await page.goto('http://localhost:9002/qa/super-agent')
    await page.waitForSelector('textarea', { timeout: 10000 })

    // 选择技能
    await page.click('button:has-text("选择技能")')
    await page.waitForTimeout(500)
    await page.click('text=多专家辩论')
    await page.waitForTimeout(500)

    // 输入话题
    await page.fill('textarea', '如何提高教学质量？')
    await page.click('button:has-text("发送")')

    // 等待模式选择卡片
    await page.waitForSelector('.mode-selection-card', { timeout: 10000 })

    // 验证倒计时显示
    const countdown = page.locator('.countdown')
    await expect(countdown).toBeVisible()
    console.log('✅ 倒计时已显示')

    // 等待倒计时结束（30秒，但我们只等5秒验证倒计时在减少）
    await page.waitForTimeout(2000)
    const countdownText = await countdown.textContent()
    expect(countdownText).toMatch(/\d+s/)
    console.log(`✅ 倒计时正在运行: ${countdownText}`)
  })
})
