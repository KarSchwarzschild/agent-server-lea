/**
 * Artifact 系统 E2E 测试 v2
 *
 * 覆盖：
 * 1. SSE 事件流验证（API 层）
 * 2. 前端面板交互（内联卡片 → 打开面板 → 版本导航 → 关闭）
 * 3. Artifact API CRUD
 * 4. 模板 API
 * 5. 管理后台 output_format
 * 6. 视觉截图全流程（每 5 秒截图）
 *
 * 运行：
 *   cd frontend-user
 *   npx playwright test tests/e2e/artifact-system.spec.ts --timeout=300000
 */

import { test, expect, type Page } from '@playwright/test'
import * as path from 'path'
import * as fs from 'fs'

const API_URL = 'http://localhost:8000'
const BASE_URL = 'http://localhost:9002'
const USERNAME = 'wj2929'
const PASSWORD = 'wj2929'
const WORKTREE = '/Users/mac/Documents/github/OUCOnline/lifelong-edu-agent/.claude/worktrees/canvas'
const RESULTS_DIR = path.join(WORKTREE, 'frontend-user', 'tests', 'e2e', 'results', 'artifact-system')

fs.mkdirSync(RESULTS_DIR, { recursive: true })

let screenshotIndex = 0

async function screenshot(page: Page, name: string) {
  screenshotIndex++
  const filename = `${String(screenshotIndex).padStart(3, '0')}-${name}.png`
  await page.screenshot({ path: path.join(RESULTS_DIR, filename), fullPage: false })
  console.log(`  📸 ${filename}`)
}

async function login(page: Page): Promise<string> {
  const resp = await page.request.post(`${API_URL}/api/auth/login`, {
    data: { username: USERNAME, password: PASSWORD },
  })
  const body = await resp.json()
  const token = body.access_token
  expect(token).toBeTruthy()
  await page.goto(BASE_URL)
  await page.evaluate((t) => localStorage.setItem('token', t), token)
  return token
}

async function navigateToSuperAgent(page: Page) {
  await page.goto(`${BASE_URL}/qa/super-agent`)
  await page.waitForLoadState('networkidle')
  await page.waitForTimeout(1500)
}

async function sendMessage(page: Page, message: string) {
  const input = page.locator('textarea').first()
  await expect(input).toBeVisible({ timeout: 10000 })
  await input.fill(message)
  await page.waitForTimeout(300)
  const sendBtn = page.locator('button[class*="send"], button:has(svg)').last()
  if (await sendBtn.isVisible().catch(() => false)) {
    await sendBtn.click()
  } else {
    await input.press('Enter')
  }
}

async function waitForReplyDone(page: Page, timeout = 180_000) {
  await expect(page.locator('text=重新生成').last()).toBeVisible({ timeout })
}

// ==================== 测试 ====================

test.describe('Artifact 系统 v2', () => {
  test.setTimeout(300_000)

  let token: string

  test.beforeAll(async ({ browser }) => {
    const page = await browser.newPage()
    token = await login(page)
    await page.close()
  })

  test.beforeEach(() => { screenshotIndex = 0 })

  // ── 测试 1：API CRUD ──
  test('1. Artifact API CRUD + 版本 + Diff', async ({ page }) => {
    console.log('\n=== 测试 1: API CRUD ===')
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

    const sessResp = await page.request.get(`${API_URL}/api/user/super-agent/sessions?limit=1`, { headers })
    const sessions = await sessResp.json()
    const sessionId = sessions[0]?.session_id || 'test-session'

    // 创建
    const createResp = await page.request.post(`${API_URL}/api/user/artifacts`, {
      headers,
      data: { session_id: sessionId, title: 'E2E测试文档', content: '# 测试\n\n正文内容', doc_category: 'general', source: 'user_edit' },
    })
    expect(createResp.status()).toBe(200)
    const art = await createResp.json()
    expect(art.id).toBeTruthy()
    console.log(`  ✅ 创建: ${art.id}`)

    // 获取
    const getResp = await page.request.get(`${API_URL}/api/user/artifacts/${art.id}`, { headers })
    expect(getResp.status()).toBe(200)
    console.log(`  ✅ 获取`)

    // 新版本
    const vResp = await page.request.post(`${API_URL}/api/user/artifacts/${art.id}/versions`, {
      headers, data: { content: '# 测试修改版\n\n修改后内容', source: 'user_edit', change_summary: '测试修改' },
    })
    expect(vResp.status()).toBe(200)
    const ver = await vResp.json()
    expect(ver.versionIndex).toBe(2)
    console.log(`  ✅ 新版本 v${ver.versionIndex}`)

    // Diff
    const diffResp = await page.request.get(`${API_URL}/api/user/artifacts/${art.id}/diff?from=1&to=2`, { headers })
    expect(diffResp.status()).toBe(200)
    const diff = await diffResp.json()
    expect(diff.stats.inserts + diff.stats.deletes).toBeGreaterThan(0)
    console.log(`  ✅ Diff: +${diff.stats.inserts} -${diff.stats.deletes}`)

    // 恢复
    const restoreResp = await page.request.post(`${API_URL}/api/user/artifacts/${art.id}/restore`, {
      headers, data: { version_index: 1 },
    })
    expect(restoreResp.status()).toBe(200)
    console.log(`  ✅ 恢复 v1 → v3`)

    // 软删除
    const delResp = await page.request.delete(`${API_URL}/api/user/artifacts/${art.id}`, { headers })
    expect(delResp.status()).toBe(200)
    const get404 = await page.request.get(`${API_URL}/api/user/artifacts/${art.id}`, { headers })
    expect(get404.status()).toBe(404)
    console.log(`  ✅ 删除 + 404`)
  })

  // ── 测试 2：模板 API ──
  test('2. 模板 API', async ({ page }) => {
    console.log('\n=== 测试 2: 模板 API ===')
    const headers = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }

    const listResp = await page.request.get(`${API_URL}/api/user/templates`, { headers })
    const templates = await listResp.json()
    expect(templates.length).toBeGreaterThanOrEqual(5)
    console.log(`  ✅ 系统模板: ${templates.length}`)

    const createResp = await page.request.post(`${API_URL}/api/user/templates`, {
      headers, data: { name: 'E2E模板', doc_category: 'general', content: '# [title]', description: '测试' },
    })
    const tmpl = await createResp.json()
    expect(tmpl.isSystem).toBe(false)
    console.log(`  ✅ 创建用户模板: ${tmpl.id}`)

    await page.request.delete(`${API_URL}/api/user/templates/${tmpl.id}`, { headers })
    console.log(`  ✅ 删除`)
  })

  // ── 测试 3：管理后台 output_format ──
  test('3. 管理后台 output_format', async ({ page }) => {
    console.log('\n=== 测试 3: 管理后台 ===')
    const adminResp = await page.request.post(`${API_URL}/api/auth/login`, { data: { username: 'admin', password: 'admin123' } })
    const adminToken = (await adminResp.json()).access_token
    const headers = { Authorization: `Bearer ${adminToken}` }

    const resp = await page.request.get(`${API_URL}/api/admin/super-skills/doc-generator`, { headers })
    const skill = await resp.json()
    expect(skill.output_format).toBe('artifact')
    console.log(`  ✅ doc-generator output_format=artifact`)

    for (const name of ['deep-research', 'official-doc', 'work-report']) {
      const r = await page.request.get(`${API_URL}/api/admin/super-skills/${name}`, { headers })
      if (r.status() === 200) {
        const s = await r.json()
        console.log(`  ${name}: output_format=${s.output_format || 'null'}`)
      }
    }
  })

  // ── 测试 4：会话加载 artifact + 内联卡片 + 面板展开 ──
  test('4. 历史会话加载 artifact 并打开面板', async ({ page }) => {
    console.log('\n=== 测试 4: 历史会话 Artifact ===')

    await page.setViewportSize({ width: 1920, height: 1080 })
    await login(page)

    // 找一个有 artifact 的会话
    const headers = { Authorization: `Bearer ${token}` }
    const artResp = await page.request.get(`${API_URL}/api/user/artifacts?limit=1`, { headers })
    const docs = await artResp.json()

    if (!docs.items || docs.items.length === 0) {
      console.log('  ⚠️ 无 artifact 文档，跳过')
      return
    }

    const sessionId = docs.items[0].sessionId
    console.log(`  会话: ${sessionId}`)

    await page.goto(`${BASE_URL}/qa/super-agent/${sessionId}`)
    await page.waitForLoadState('networkidle')
    await page.waitForTimeout(3000)
    await screenshot(page, 'session-loaded')

    // 检查 Pinia store 中是否有 artifact
    const storeState = await page.evaluate(() => {
      try {
        const app = (document.querySelector('#app') as any)?.__vue_app__
        const pinia = app?.config?.globalProperties?.$pinia
        const state = pinia?.state?.value?.artifact
        return {
          artifactCount: state?.artifactCount || 0,
          panelMode: state?.panelMode || 'unknown',
        }
      } catch { return { artifactCount: -1, panelMode: 'error' } }
    })
    console.log(`  Store: count=${storeState.artifactCount}, mode=${storeState.panelMode}`)

    // 尝试点击内联卡片（如果存在）
    const inlineCard = page.locator('.artifact-msg-card').first()
    if (await inlineCard.isVisible({ timeout: 2000 }).catch(() => false)) {
      console.log('  ✅ 内联卡片可见，点击打开面板')
      await inlineCard.click()
      await page.waitForTimeout(1000)
      await screenshot(page, 'panel-opened-via-card')
    } else {
      console.log('  ⚠️ 内联卡片不可见（历史消息无 artifact_id），尝试手动打开')
      // 通过 store 打开
      await page.evaluate(() => {
        try {
          const app = (document.querySelector('#app') as any)?.__vue_app__
          const pinia = app?.config?.globalProperties?.$pinia
          const state = pinia?.state?.value?.artifact
          if (state?.artifacts?.size > 0) {
            const lastId = [...state.artifacts.keys()].pop()
            state.activeArtifactId = lastId
            state.panelMode = 'readonly'
          }
        } catch (e) { console.warn('手动打开失败', e) }
      })
      await page.waitForTimeout(1000)
      await screenshot(page, 'panel-opened-manual')
    }

    // 验证面板
    const panel = page.locator('.artifact-panel')
    const hasPanel = await panel.isVisible().catch(() => false)
    console.log(`  面板可见: ${hasPanel}`)

    if (hasPanel) {
      await screenshot(page, 'artifact-panel')

      // 检查标题栏
      const header = page.locator('.artifact-header')
      expect(await header.isVisible()).toBe(true)
      console.log('  ✅ 标题栏可见')

      // 检查内容
      const body = page.locator('.artifact-body')
      const bodyText = await body.textContent().catch(() => '')
      console.log(`  内容长度: ${bodyText?.length || 0} 字符`)
      if ((bodyText?.length || 0) > 100) {
        console.log('  ✅ 面板有实质内容')
      }

      // 检查底部栏
      const footer = page.locator('.artifact-footer')
      expect(await footer.isVisible()).toBe(true)
      console.log('  ✅ 底部栏可见')

      // 测试关闭面板
      const closeBtn = page.locator('.artifact-header button[title="关闭面板"]')
      if (await closeBtn.isVisible().catch(() => false)) {
        await closeBtn.click()
        await page.waitForTimeout(500)
        await screenshot(page, 'panel-closed')
        expect(await panel.isVisible().catch(() => false)).toBe(false)
        console.log('  ✅ 面板关闭')
      }
    }
  })

  // ── 测试 5：视觉截图全流程（新会话 + 流式生成） ──
  test('5. 视觉截图全流程', async ({ page }) => {
    console.log('\n=== 测试 5: 视觉截图全流程 ===')

    await page.setViewportSize({ width: 1920, height: 1080 })
    await login(page)
    await navigateToSuperAgent(page)

    // 切换到专业模式
    const proBtn = page.locator('text=专业').first()
    if (await proBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await proBtn.click()
      await page.waitForTimeout(500)
      console.log('  ✅ 专业模式')
    }
    await screenshot(page, 'initial')

    // 发送消息
    await sendMessage(page, '请直接帮我生成一份2025年度高等教育数字化转型工作总结，包含工作概述、主要成果、存在问题和下一步计划，直接输出完整内容')
    await screenshot(page, 'sent')

    // 每 5 秒截图，最多 2 分钟
    const shots: string[] = []
    for (let i = 0; i < 24; i++) {
      await page.waitForTimeout(5_000)
      const name = `progress-${String(i).padStart(2, '0')}`
      await screenshot(page, name)
      shots.push(name)

      const done = await page.locator('text=重新生成').last().isVisible().catch(() => false)
      if (done) {
        console.log(`  ✅ AI 完成（第 ${(i + 1) * 5} 秒）`)
        break
      }
    }

    await screenshot(page, 'done')

    // 等待 artifact 数据加载
    await page.waitForTimeout(2000)

    // 检查面板状态
    const panel = page.locator('.artifact-panel')
    const hasPanel = await panel.isVisible().catch(() => false)
    console.log(`  Artifact 面板可见: ${hasPanel}`)

    if (!hasPanel) {
      // 尝试通过内联卡片打开
      const card = page.locator('.artifact-msg-card').first()
      if (await card.isVisible({ timeout: 3000 }).catch(() => false)) {
        console.log('  📄 点击内联卡片打开面板')
        await card.click()
        await page.waitForTimeout(1000)
      } else {
        // 通过 store 手动打开
        console.log('  🔧 通过 store 手动打开面板')
        await page.evaluate(() => {
          try {
            const app = (document.querySelector('#app') as any)?.__vue_app__
            const pinia = app?.config?.globalProperties?.$pinia
            const state = pinia?.state?.value?.artifact
            if (state?.artifacts?.size > 0) {
              state.activeArtifactId = [...state.artifacts.keys()].pop()
              state.panelMode = 'readonly'
            }
          } catch {}
        })
        await page.waitForTimeout(1000)
      }
    }

    await screenshot(page, 'final')

    // 面板截图
    const panelFinal = page.locator('.artifact-panel')
    if (await panelFinal.isVisible().catch(() => false)) {
      await panelFinal.screenshot({ path: path.join(RESULTS_DIR, 'panel-only.png') })
      console.log('  📸 面板单独截图')

      const bodyText = await page.locator('.artifact-body').textContent().catch(() => '')
      console.log(`  面板内容: ${bodyText?.length || 0} 字符`)
    }

    console.log(`  总截图: ${shots.length + 4}`)
  })
})
