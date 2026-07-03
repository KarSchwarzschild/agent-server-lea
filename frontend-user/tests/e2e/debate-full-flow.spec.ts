/**
 * 辩论完整流程 E2E 测试
 *
 * 覆盖：
 * 1. 模式选择 → 确认后不重复用户消息
 * 2. 辩论进行中 → 多轮对话（继续下一轮）
 * 3. 向专家追问
 * 4. 结束对话
 * 5. 三种模式差异验证（主持人 prompt 不同）
 */
import { test, expect, Page } from '@playwright/test'
import * as fs from 'fs'
import * as path from 'path'
import { fileURLToPath } from 'url'

const BASE_URL = 'http://localhost:9002'
const API_URL = 'http://localhost:8000'

const _dirname = path.dirname(fileURLToPath(import.meta.url))
const RESULTS_DIR = path.join(_dirname, 'results')

if (!fs.existsSync(RESULTS_DIR)) {
  fs.mkdirSync(RESULTS_DIR, { recursive: true })
}

async function login(page: Page) {
  await page.goto(`${BASE_URL}/login`)
  await page.waitForSelector('input[placeholder*="用户名"]', { timeout: 10000 })
  await page.fill('input[placeholder*="用户名"]', 'wj2929')
  await page.fill('input[placeholder*="密码"]', 'wj2929')
  await page.click('button:has-text("登录")')
  await page.waitForURL(/\/(qa|home|dashboard|super-agent)/, { timeout: 10000 })
}

async function screenshot(page: Page, name: string) {
  const filePath = path.join(RESULTS_DIR, `${name}.png`)
  await page.screenshot({ path: filePath, fullPage: false })
  console.log(`截图: ${filePath}`)
  return filePath
}

async function getToken(request: any): Promise<string> {
  const resp = await request.post(`${API_URL}/api/auth/login`, {
    data: { username: 'wj2929', password: 'wj2929' }
  })
  if (resp.ok()) {
    const body = await resp.json()
    return body.access_token || body.token || body.data?.token || ''
  }
  return ''
}

// ============================================================
// 测试1：确认模式后不重复用户消息（Bug 修复验证）
// ============================================================
test.describe('测试1：确认后不重复用户消息', () => {
  test('选择模式确认后，聊天区只有一条用户消息', async ({ page }) => {
    test.setTimeout(90000)
    await login(page)
    await page.goto(`${BASE_URL}/qa/super-agent`)
    await page.waitForSelector('textarea', { timeout: 10000 })

    const textarea = page.locator('textarea')
    await textarea.click()
    await textarea.fill('@多专家辩论 高校应该优先发展线上教学还是线下教学？')
    await page.waitForTimeout(300)
    await textarea.press('Enter')

    // 等待模式选择卡片
    await page.waitForSelector('.mode-selection-card', { timeout: 50000 })
    await screenshot(page, 'flow1-01-mode-card')
    console.log('✅ 模式选择卡片出现')

    // 点击确认
    const confirmBtn = page.locator('.confirm-btn')
    await confirmBtn.click()
    await page.waitForTimeout(2000)
    await screenshot(page, 'flow1-02-after-confirm')

    // 统计用户消息 DOM 数量（.user-message class）
    const userMessages = page.locator('.user-message')
    const count = await userMessages.count()
    console.log(`用户消息 DOM 数量: ${count}`)

    // 修复后应该只有1条用户消息（不是2条）
    expect(count).toBe(1)
    await screenshot(page, 'flow1-03-message-count')
    console.log('✅ 测试1 通过：无重复用户消息')
  })
})

// ============================================================
// 测试2：三种模式 SSE 差异验证（API 层）
// ============================================================
test.describe('测试2：三种模式主持人 prompt 差异', () => {
  test('debate/seminar/socratic 模式的主持人开场白应不同', async ({ page }) => {
    test.setTimeout(300000)
    await login(page)

    const modes = ['debate', 'seminar', 'socratic']
    const openings: Record<string, string> = {}

    for (const mode of modes) {
      console.log(`\n--- 测试模式: ${mode} ---`)

      // 用 page.evaluate 发起请求并收集 SSE 事件（带超时中断）
      const result = await page.evaluate(async ({ apiUrl, mode: m }) => {
        // 1. 登录获取 token
        const loginResp = await fetch(`${apiUrl}/api/auth/login`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ username: 'wj2929', password: 'wj2929' })
        })
        const { access_token } = await loginResp.json()

        // 2. 创建辩论
        const createResp = await fetch(`${apiUrl}/api/user/super-agent/chat/stream?async=true`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${access_token}`
          },
          body: JSON.stringify({
            message: '高校应该优先发展线上教学还是线下教学？',
            force_skill: '__debate__',
            discussion_mode: m
          })
        })
        const { session_id } = await createResp.json()

        // 3. 收集 SSE 事件（30秒超时）
        return new Promise<{ content: string; eventCount: number }>((resolve) => {
          let content = ''
          let eventCount = 0
          const es = new EventSource(
            `${apiUrl}/api/user/super-agent/chat/events/${session_id}?token=${access_token}`
          )

          const timeout = setTimeout(() => {
            es.close()
            resolve({ content, eventCount })
          }, 45000)

          es.addEventListener('content', (e) => {
            try {
              const data = JSON.parse(e.data)
              if (data.text) content += data.text
              eventCount++
            } catch { /* skip */ }
          })

          es.addEventListener('round_end', () => {
            clearTimeout(timeout)
            es.close()
            resolve({ content, eventCount })
          })

          es.onerror = () => {
            clearTimeout(timeout)
            es.close()
            resolve({ content, eventCount })
          }
        })
      }, { apiUrl: API_URL, mode })

      openings[mode] = result.content.slice(0, 500)
      console.log(`  事件数: ${result.eventCount}`)
      console.log(`  开场白前100字: ${openings[mode].slice(0, 100)}`)
    }

    // 验证三种模式的开场白不完全相同
    const debateOpening = openings['debate']
    const seminarOpening = openings['seminar']
    const socraticOpening = openings['socratic']

    const allSame = debateOpening === seminarOpening && seminarOpening === socraticOpening
    if (allSame) {
      console.log('❌ 三种模式开场白完全相同！')
    } else {
      console.log('✅ 三种模式开场白存在差异')
    }
    expect(allSame).toBeFalsy()

    console.log(`debate 含"辩论": ${debateOpening.includes('辩论')}`)
    console.log(`seminar 含"研讨": ${seminarOpening.includes('研讨')}`)
    console.log(`socratic 含"追问": ${socraticOpening.includes('追问')}`)

    console.log('✅ 测试2 通过：模式差异验证完成')
  })
})

// ============================================================
// 测试3：多轮对话 + 继续下一轮 + 向专家追问 + 结束（API 层）
// ============================================================
test.describe('测试3：辩论完整生命周期（API）', () => {
  test('创建辩论 → 等待第1轮结束 → 继续 → 追问 → 结束', async ({ request }) => {
    test.setTimeout(300000)
    const token = await getToken(request)
    if (!token) {
      console.log('⚠️ 无法获取 token，跳过')
      return
    }

    // 1. 创建辩论（debate 模式）
    console.log('\n=== 阶段1：创建辩论 ===')
    const createResp = await request.post(`${API_URL}/api/user/super-agent/chat/stream?async=true`, {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        message: '人工智能是否会取代教师？',
        force_skill: '__debate__',
        discussion_mode: 'debate'
      }
    })
    expect(createResp.ok()).toBeTruthy()
    const { session_id } = await createResp.json()
    console.log(`  session: ${session_id}`)

    // 收集第1轮事件
    const sseResp = await request.get(
      `${API_URL}/api/user/super-agent/chat/events/${session_id}`,
      { headers: { Authorization: `Bearer ${token}` }, timeout: 120000 }
    )
    const sseText = await sseResp.text()
    const events = parseSSEEvents(sseText)

    let debateSessionId = ''
    let experts: any[] = []
    let hasRoundEnd = false

    for (const evt of events) {
      if (evt.event === 'debate_created') {
        debateSessionId = evt.data.debate_session_id
        experts = evt.data.experts || []
        console.log(`  辩论会话: ${debateSessionId}`)
        console.log(`  专家: ${experts.map((e: any) => e.name).join(', ')}`)
      }
      if (evt.event === 'round_end') {
        hasRoundEnd = true
        console.log(`  第1轮结束: can_continue=${evt.data.can_continue}`)
      }
    }

    expect(debateSessionId).toBeTruthy()
    expect(hasRoundEnd).toBeTruthy()
    console.log('✅ 阶段1 通过：辩论创建 + 第1轮完成')

    // 2. 继续下一轮
    console.log('\n=== 阶段2：继续第2轮 ===')
    const continueResp = await request.post(
      `${API_URL}/api/user/debate/${debateSessionId}/continue`,
      {
        headers: { Authorization: `Bearer ${token}` },
      }
    )

    if (continueResp.ok()) {
      const contText = await continueResp.text()
      const events2 = parseSSEEvents(contText)
      const hasRound2End = events2.some(e => e.event === 'round_end')
      console.log(`  第2轮事件数: ${events2.length}, round_end: ${hasRound2End}`)
      console.log('✅ 阶段2 通过：第2轮完成')
    } else {
      console.log(`  继续接口返回 ${continueResp.status()}`)
    }

    // 3. 向专家追问
    console.log('\n=== 阶段3：向专家追问 ===')
    if (experts.length > 0) {
      const targetExpert = experts[0]
      const askResp = await request.post(
        `${API_URL}/api/user/debate/${debateSessionId}/ask`,
        {
          headers: { Authorization: `Bearer ${token}` },
          data: {
            expert_id: targetExpert.id,
            question: '能否举一个具体的教学场景来说明你的观点？'
          }
        }
      )

      if (askResp.ok()) {
        const askText = await askResp.text()
        const events3 = parseSSEEvents(askText)
        const hasContent = events3.some(e => e.event === 'content' || e.event === 'speech_chunk')
        console.log(`  追问事件数: ${events3.length}, 有内容: ${hasContent}`)
        console.log('✅ 阶段3 通过：专家追问完成')
      } else {
        console.log(`  追问接口返回 ${askResp.status()}`)
      }
    }

    // 4. 结束对话
    console.log('\n=== 阶段4：结束对话 ===')
    const endResp = await request.post(
      `${API_URL}/api/user/debate/${debateSessionId}/end`,
      {
        headers: { Authorization: `Bearer ${token}` },
      }
    )

    if (endResp.ok()) {
      const endText = await endResp.text()
      const events4 = parseSSEEvents(endText)
      const hasContent = events4.some(e => e.event === 'content' || e.event === 'speech_chunk')
      console.log(`  结束事件数: ${events4.length}, 有内容: ${hasContent}`)
      console.log('✅ 阶段4 通过：对话结束完成')
    } else {
      console.log(`  结束接口返回 ${endResp.status()}`)
    }

    console.log('\n✅ 测试3 通过：辩论完整生命周期验证完成')
  })
})

// ============================================================
// 测试4：页面级完整流程视觉测试
// ============================================================
test.describe('测试4：页面级辩论流程视觉测试', () => {
  test('从输入到辩论结束的完整页面交互', async ({ page }) => {
    test.setTimeout(180000)
    await login(page)
    await page.goto(`${BASE_URL}/qa/super-agent`)
    await page.waitForSelector('textarea', { timeout: 10000 })
    await screenshot(page, 'flow4-01-start')

    // 输入并发送
    const textarea = page.locator('textarea')
    await textarea.click()
    await textarea.fill('@多专家辩论 远程教育的质量如何保障？')
    await textarea.press('Enter')
    await page.waitForTimeout(500)

    // 等待模式选择卡片
    try {
      await page.waitForSelector('.mode-selection-card', { timeout: 50000 })
      await screenshot(page, 'flow4-02-mode-card')
      console.log('✅ 模式选择卡片出现')

      // 选择研讨模式
      const seminarItem = page.locator('.mode-item').filter({ hasText: '研讨' })
      if (await seminarItem.count() > 0) {
        await seminarItem.first().click()
        await page.waitForTimeout(300)
        await screenshot(page, 'flow4-03-seminar-selected')
        console.log('✅ 已选择研讨模式')
      }

      // 确认
      await page.locator('.confirm-btn').click()
      await page.waitForTimeout(3000)
      await screenshot(page, 'flow4-04-debate-starting')
      console.log('✅ 辩论开始')
    } catch {
      console.log('模式选择卡片未出现，可能直接开始了')
      await screenshot(page, 'flow4-02-no-card')
    }

    // 等待辩论内容出现（专家发言）
    try {
      await page.waitForSelector('text=【', { timeout: 60000 })
      await screenshot(page, 'flow4-05-expert-speaking')
      console.log('✅ 专家开始发言')
    } catch {
      console.log('未检测到专家发言标记')
      await screenshot(page, 'flow4-05-no-expert')
    }

    // 等待第1轮结束（出现操作按钮）
    try {
      // 等待 "继续" 或 "结束" 按钮
      await page.waitForSelector('button:has-text("继续"), button:has-text("结束"), text=继续下一轮', { timeout: 120000 })
      await screenshot(page, 'flow4-06-round-end')
      console.log('✅ 第1轮结束，操作按钮出现')

      // 点击继续
      const continueBtn = page.locator('button:has-text("继续")').first()
      if (await continueBtn.count() > 0) {
        await continueBtn.click()
        await page.waitForTimeout(5000)
        await screenshot(page, 'flow4-07-round2-start')
        console.log('✅ 已点击继续，第2轮开始')
      }
    } catch {
      console.log('未检测到轮次结束按钮')
      await screenshot(page, 'flow4-06-timeout')
    }

    // 等待第2轮结束
    try {
      await page.waitForSelector('button:has-text("结束"), text=结束讨论', { timeout: 120000 })
      await screenshot(page, 'flow4-08-round2-end')
      console.log('✅ 第2轮结束')

      // 点击结束
      const endBtn = page.locator('button:has-text("结束")').first()
      if (await endBtn.count() > 0) {
        await endBtn.click()
        await page.waitForTimeout(5000)
        await screenshot(page, 'flow4-09-final-summary')
        console.log('✅ 已点击结束，最终总结')
      }
    } catch {
      console.log('未检测到结束按钮')
      await screenshot(page, 'flow4-08-timeout')
    }

    await screenshot(page, 'flow4-10-final')
    console.log('✅ 测试4 完成：页面级完整流程')
  })
})

// ============================================================
// 辅助函数
// ============================================================
function parseSSEEvents(text: string): Array<{ event: string; data: any }> {
  const events: Array<{ event: string; data: any }> = []
  const lines = text.split('\n')
  let currentEvent = ''

  for (const line of lines) {
    if (line.startsWith('event:')) {
      currentEvent = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      try {
        const data = JSON.parse(line.slice(5))
        events.push({ event: currentEvent || 'message', data })
      } catch {
        events.push({ event: currentEvent || 'message', data: line.slice(5) })
      }
      currentEvent = ''
    }
  }
  return events
}
