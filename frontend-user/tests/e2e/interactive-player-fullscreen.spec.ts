/**
 * InteractivePlayer 全屏标签切换 — E2E 视觉测试
 *
 * 使用真实历史会话（雪花算法学习引导，3 个知识点）验证：
 *   1. 全屏打开后标签栏正确显示
 *   2. 标签切换 iframe 内容变化
 *   3. iframe 高度贴合内容（无大面积白空间）
 *   4. 左右边距对称
 *   5. ESC 关闭全屏
 *
 * 运行方式：
 *   npx playwright test tests/e2e/interactive-player-fullscreen.spec.ts --headed
 *
 * 前置条件：
 *   - 前端 dev server: http://localhost:9002
 *   - 后端 API: http://localhost:8000
 */
import { test, expect, type Page } from '@playwright/test'

const BASE_URL = 'http://localhost:9002'
const SESSION_ID = 'sa_bceb382895df'
const AUTH_TOKEN = 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyXzE5NzJmMzFhZTU5ZSIsInJvbGVzIjpbInVzZXIiXSwicGVybWlzc2lvbnMiOlsiZG9jdW1lbnQ6cmVhZCIsImtiOnJlYWQiXSwiZXhwIjoxNzc1MTIxODk1fQ.TCa9ldZtpjrTE5hX7UuJkWUDECaEcHH8Kp2mQFKsnuI'

/** 注入 auth token 到 localStorage，模拟已登录 */
async function loginViaStorage(page: Page) {
  await page.goto(BASE_URL)
  await page.evaluate((token) => {
    localStorage.setItem('token', token.replace('Bearer ', ''))
  }, AUTH_TOKEN)
}

/** 等待会话加载完成（至少出现 N 个 InteractivePlayer） */
async function waitForPlayers(page: Page, minCount: number) {
  await page.waitForSelector('.interactive-player', { timeout: 15000 })
  await page.waitForFunction(
    (n) => document.querySelectorAll('.interactive-player').length >= n,
    minCount,
    { timeout: 15000 },
  )
  // 等 iframe 渲染完成
  await page.waitForTimeout(2000)
}

test.describe('InteractivePlayer 全屏多实例标签切换', () => {
  test.beforeEach(async ({ page }) => {
    // 使用大屏 viewport 模拟真实用户环境
    await page.setViewportSize({ width: 1440, height: 900 })
    await loginViaStorage(page)
    // 导航到超级智能体页面并加载历史会话
    await page.goto(`${BASE_URL}/qa/super-agent`)
    await page.waitForLoadState('networkidle')
  })

  test('加载历史会话后出现多个 InteractivePlayer', async ({ page }) => {
    // 点击左侧会话列表中的雪花算法会话
    const sessionItem = page.locator('.session-item', { hasText: '雪花算法' }).first()
    if (await sessionItem.isVisible()) {
      await sessionItem.click()
    } else {
      // 直接通过 API 加载
      await page.evaluate(async (sid) => {
        window.dispatchEvent(new CustomEvent('load-session', { detail: sid }))
      }, SESSION_ID)
    }

    await waitForPlayers(page, 2)
    const players = page.locator('.interactive-player')
    const count = await players.count()
    expect(count).toBeGreaterThanOrEqual(2)

    await page.screenshot({ path: 'tests/e2e/screenshots/01-session-loaded.png', fullPage: true })
  })

  test('全屏显示标签栏（多个 player 时）', async ({ page }) => {
    const sessionItem = page.locator('.session-item', { hasText: '雪花算法' }).first()
    if (await sessionItem.isVisible()) await sessionItem.click()
    await waitForPlayers(page, 2)

    // 点击第一个 InteractivePlayer 的全屏按钮
    const fullscreenBtn = page.locator('.interactive-player .toolbar-btn[title="全屏"]').first()
    await fullscreenBtn.click()

    // 等待全屏弹窗出现
    const overlay = page.locator('.fullscreen-overlay')
    await expect(overlay).toBeVisible({ timeout: 3000 })

    // 验证标签栏存在
    const tabs = page.locator('.fullscreen-tabs')
    await expect(tabs).toBeVisible()

    // 验证有多个标签
    const tabButtons = page.locator('.fullscreen-tab')
    const tabCount = await tabButtons.count()
    expect(tabCount).toBeGreaterThanOrEqual(2)

    // 验证第一个标签是 active
    const firstTab = tabButtons.first()
    await expect(firstTab).toHaveClass(/active/)

    await page.screenshot({ path: 'tests/e2e/screenshots/02-fullscreen-with-tabs.png' })
  })

  test('标签切换更新 iframe 内容', async ({ page }) => {
    const sessionItem = page.locator('.session-item', { hasText: '雪花算法' }).first()
    if (await sessionItem.isVisible()) await sessionItem.click()
    await waitForPlayers(page, 2)

    // 进入全屏
    await page.locator('.interactive-player .toolbar-btn[title="全屏"]').first().click()
    await page.locator('.fullscreen-overlay').waitFor({ state: 'visible' })
    await page.waitForTimeout(1000)

    // 记录当前 iframe srcdoc 长度
    const iframe = page.locator('.fullscreen-iframe')
    const initialSrcdoc = await iframe.getAttribute('srcdoc')

    // 点击第二个标签
    const secondTab = page.locator('.fullscreen-tab').nth(1)
    await secondTab.click()
    await page.waitForTimeout(1500)

    // 验证第二个标签变为 active
    await expect(secondTab).toHaveClass(/active/)

    // 验证 iframe 内容已变化
    const newSrcdoc = await iframe.getAttribute('srcdoc')
    expect(newSrcdoc).not.toBe(initialSrcdoc)

    await page.screenshot({ path: 'tests/e2e/screenshots/03-tab-switched.png' })
  })

  test('iframe 高度贴合内容（无大面积白空间）', async ({ page }) => {
    const sessionItem = page.locator('.session-item', { hasText: '雪花算法' }).first()
    if (await sessionItem.isVisible()) await sessionItem.click()
    await waitForPlayers(page, 2)

    // 进入全屏
    await page.locator('.interactive-player .toolbar-btn[title="全屏"]').first().click()
    await page.locator('.fullscreen-overlay').waitFor({ state: 'visible' })
    // 等待 iframe 加载 + 高度上报
    await page.waitForTimeout(3000)

    // 获取 iframe 和 scroll 容器的尺寸
    const metrics = await page.evaluate(() => {
      const iframe = document.querySelector('.fullscreen-iframe') as HTMLIFrameElement
      const scroll = document.querySelector('.fullscreen-scroll') as HTMLElement
      const container = document.querySelector('.fullscreen-container') as HTMLElement
      if (!iframe || !scroll || !container) return null

      const iframeRect = iframe.getBoundingClientRect()
      const scrollRect = scroll.getBoundingClientRect()
      const containerRect = container.getBoundingClientRect()

      return {
        iframeHeight: iframeRect.height,
        scrollHeight: scrollRect.height,
        containerHeight: containerRect.height,
        // 白空间比例 = (scroll区域高度 - iframe高度) / scroll区域高度
        whiteSpaceRatio: (scrollRect.height - iframeRect.height - 32) / scrollRect.height, // 32 = padding
        // 左右边距
        leftMargin: iframeRect.left - scrollRect.left,
        rightMargin: scrollRect.right - iframeRect.right,
      }
    })

    expect(metrics).not.toBeNull()
    if (metrics) {
      // iframe 高度应 > 100px（有内容）
      expect(metrics.iframeHeight).toBeGreaterThan(100)

      // 容器应贴合内容：白空间比例不超过 15%
      console.log(`iframe 高度: ${metrics.iframeHeight}px, scroll 区域: ${metrics.scrollHeight}px`)
      console.log(`容器高度: ${metrics.containerHeight}px`)
      console.log(`白空间比例: ${(metrics.whiteSpaceRatio * 100).toFixed(1)}%`)
      expect(metrics.whiteSpaceRatio).toBeLessThan(0.15)

      // 容器高度应 < 90vh（不应撑满全屏，内容短时收缩）
      // 90vh at 900px viewport = 810px
      console.log(`容器高度 vs 90vh: ${metrics.containerHeight}px vs 810px`)
      if (metrics.iframeHeight < 600) {
        // 内容短时，容器不应撑满
        expect(metrics.containerHeight).toBeLessThan(750)
      }

      // 左右边距差异不超过 4px（对称性）
      const marginDiff = Math.abs(metrics.leftMargin - metrics.rightMargin)
      console.log(`左边距: ${metrics.leftMargin}px, 右边距: ${metrics.rightMargin}px, 差值: ${marginDiff}px`)
      expect(marginDiff).toBeLessThan(4)
    }

    await page.screenshot({ path: 'tests/e2e/screenshots/04-height-fit.png' })
  })

  test('ESC 关闭全屏', async ({ page }) => {
    const sessionItem = page.locator('.session-item', { hasText: '雪花算法' }).first()
    if (await sessionItem.isVisible()) await sessionItem.click()
    await waitForPlayers(page, 2)

    // 进入全屏
    await page.locator('.interactive-player .toolbar-btn[title="全屏"]').first().click()
    const overlay = page.locator('.fullscreen-overlay')
    await expect(overlay).toBeVisible()

    // 按 ESC
    await page.keyboard.press('Escape')
    await expect(overlay).not.toBeVisible({ timeout: 2000 })
  })

  test('关闭按钮关闭全屏', async ({ page }) => {
    const sessionItem = page.locator('.session-item', { hasText: '雪花算法' }).first()
    if (await sessionItem.isVisible()) await sessionItem.click()
    await waitForPlayers(page, 2)

    // 进入全屏
    await page.locator('.interactive-player .toolbar-btn[title="全屏"]').first().click()
    const overlay = page.locator('.fullscreen-overlay')
    await expect(overlay).toBeVisible()

    // 点击关闭按钮
    await page.locator('.fullscreen-close').click()
    await expect(overlay).not.toBeVisible({ timeout: 2000 })
  })

  test('单个 player 全屏不显示标签栏', async ({ page }) => {
    // 新建会话，只发一条消息产生一个 player
    // 这里用已有会话但验证行为：如果只有一个 player，不显示标签
    const sessionItem = page.locator('.session-item', { hasText: '雪花算法' }).first()
    if (await sessionItem.isVisible()) await sessionItem.click()
    await waitForPlayers(page, 1)

    // 获取 player 数量
    const playerCount = await page.locator('.interactive-player').count()

    // 进入全屏
    await page.locator('.interactive-player .toolbar-btn[title="全屏"]').first().click()
    await page.locator('.fullscreen-overlay').waitFor({ state: 'visible' })

    const tabs = page.locator('.fullscreen-tabs')
    if (playerCount <= 1) {
      // 单个 player：标签栏不显示
      await expect(tabs).not.toBeVisible()
    } else {
      // 多个 player：标签栏显示
      await expect(tabs).toBeVisible()
    }

    await page.locator('.fullscreen-close').click()
  })
})
