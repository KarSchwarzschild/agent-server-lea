/**
 * policy-review Skill 前端 E2E 测试
 */

import { test, expect } from '@playwright/test'

const ADMIN_URL = 'http://localhost:9001'

async function login(page: any) {
  await page.goto(`${ADMIN_URL}/login`)
  await page.fill('input[placeholder*="用户名"]', 'admin')
  await page.fill('input[type="password"]', 'admin123')
  await page.click('button:has-text("登录")')
  await page.waitForURL('**/dashboard', { timeout: 10000 })
}

test.describe('管理后台改名验证', () => {
  test.beforeEach(async ({ page }) => { await login(page) })

  test('侧边栏显示"公共知识库管理"', async ({ page }) => {
    const sidebar = await page.textContent('.n-layout-sider')
    expect(sidebar).toContain('公共知识库管理')
    expect(sidebar).not.toContain('文献管理')
  })

  test('公共知识库管理页面可访问', async ({ page }) => {
    await page.goto(`${ADMIN_URL}/documents`)
    await page.waitForTimeout(2000)
    const body = await page.textContent('body')
    expect(body).toContain('公共知识库管理')
  })
})

test.describe('Skills 管理 — policy-review', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
    await page.goto(`${ADMIN_URL}/super-agent/skills`)
    await page.waitForTimeout(3000)
  })

  test('Skills 列表包含规章制度审核', async ({ page }) => {
    const body = await page.textContent('body')
    expect(body).toContain('规章制度审核')
  })

  test('规章制度审核卡片显示正确标签', async ({ page }) => {
    // 整个页面滚动到底部确保所有卡片加载
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
    await page.waitForTimeout(500)
    const body = await page.textContent('body')
    // 验证 policy-review 核心信息出现在页面上
    expect(body).toContain('规章制度审核')
    expect(body).toContain('教育辅助')
    expect(body).toContain('审核规章')  // 触发词
  })

  test('点击编辑打开抽屉，包含知识库绑定Tab', async ({ page }) => {
    // 找到规章制度审核卡片的编辑链接
    const editLinks = page.locator('text=编辑')
    const allTexts = await page.locator('.n-card').allTextContents()
    const cardIndex = allTexts.findIndex(t => t.includes('规章制度审核'))

    if (cardIndex >= 0) {
      // 点击对应卡片的编辑
      const cards = page.locator('.n-card')
      const targetCard = cards.nth(cardIndex)
      const editBtn = targetCard.locator('text=编辑')
      await editBtn.click()
      await page.waitForSelector('.n-drawer', { timeout: 8000 })

      const drawerText = await page.textContent('.n-drawer')
      expect(drawerText).toContain('基本信息')
      expect(drawerText).toContain('知识库绑定')
      expect(drawerText).toContain('按等级')
    }
  })
})

test.describe('公共知识库管理 — 精读索引列', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
    await page.goto(`${ADMIN_URL}/documents`)
    await page.waitForTimeout(3000)
  })

  test('表格包含精读索引列', async ({ page }) => {
    const body = await page.textContent('body')
    expect(body).toContain('精读索引')
  })

  test('上传文件按钮存在且文字正确', async ({ page }) => {
    const btn = page.locator('button:has-text("上传文件")')
    await expect(btn).toBeVisible()
    // 不应该显示"上传文献"
    const body = await page.textContent('body')
    expect(body).not.toContain('上传文献')
  })

  test('精读索引状态标签正确渲染', async ({ page }) => {
    const body = await page.textContent('body')
    // 应该包含以下状态之一
    const hasValidStatus = ['可精读', '待索引', '无文件', '索引中', '索引失败'].some(s => body?.includes(s))
    expect(hasValidStatus).toBe(true)
  })
})
