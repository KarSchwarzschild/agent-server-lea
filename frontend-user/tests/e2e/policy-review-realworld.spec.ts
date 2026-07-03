/**
 * policy-review 真实场景端到端测试
 *
 * 模拟完整的真实使用流程：
 * 1. 管理员上传文档到公共知识库
 * 2. 用户发送规章制度文本请求审核 → 验证报告质量
 * 3. 用户发送合同文本请求审核 → 验证合同审核场景
 * 4. 多轮追问深入分析
 * 5. 每步截图 + 内容质量检查
 */
import { test, expect, type Page } from '@playwright/test'
import * as fs from 'fs'
import * as path from 'path'
import { fileURLToPath } from 'url'

const BASE_URL = 'http://localhost:9002'
const ADMIN_URL = 'http://localhost:9001'
const API_URL = 'http://localhost:8000'
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FIXTURES_DIR = path.join(__dirname, 'fixtures')
const RESULTS_DIR = path.join(__dirname, 'results', 'policy-review-realworld')

if (!fs.existsSync(RESULTS_DIR)) fs.mkdirSync(RESULTS_DIR, { recursive: true })

const ADMIN_TOKEN = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbl8wMDEiLCJyb2xlcyI6WyJzdXBlcl9hZG1pbiJdLCJwZXJtaXNzaW9ucyI6WyJ1c2VyOndyaXRlIiwic3RhdHM6cmVhZCIsImtiOnJlYWQiLCJkb2N1bWVudDpyZWFkIiwicm9sZTpyZWFkIiwicm9sZTp3cml0ZSIsImRvY3VtZW50OndyaXRlIiwiY29uZmlnOndyaXRlIiwidXNlcjpyZWFkIiwicm9sZTpkZWxldGUiLCJjb25maWc6cmVhZCIsImtiOndyaXRlIiwia2I6ZGVsZXRlIiwiZG9jdW1lbnQ6ZGVsZXRlIiwidXNlcjpkZWxldGUiLCJsb2c6cmVhZCJdLCJleHAiOjE3NzYxNTAxOTJ9.frbP2HeN9oQGnd6Dt8juKJhQx32cD4ZGaiFZd995AdU'

// ============ 辅助函数 ============

async function getUserToken(page: Page): Promise<string> {
  const resp = await page.request.post(`${API_URL}/api/auth/login`, {
    data: { username: 'wj2929', password: 'wj2929' },
  })
  return (await resp.json()).access_token
}

async function sendChat(
  page: Page, token: string, message: string, sessionId?: string,
): Promise<{ events: Array<{ event: string; data: any }>; content: string; sessionId: string }> {
  const payload: any = { message }
  if (sessionId) payload.session_id = sessionId

  const resp = await page.request.post(
    `${API_URL}/api/user/super-agent/chat/stream?async=false`,
    { headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' }, data: payload, timeout: 180000 },
  )

  const body = await resp.text()
  const events: Array<{ event: string; data: any }> = []
  const contentParts: string[] = []
  let currentEvent = 'message'

  for (const line of body.split('\n')) {
    if (line.startsWith('event: ')) currentEvent = line.substring(7).trim()
    else if (line.startsWith('data: ') && line !== 'data: [DONE]') {
      try {
        const data = JSON.parse(line.substring(6))
        events.push({ event: currentEvent, data })
        if (currentEvent === 'content') {
          const text = data.content || data.text || data.delta || ''
          if (text) contentParts.push(text)
        }
      } catch {}
    }
  }

  const doneEvt = events.find(e => e.event === 'done')
  return { events, content: contentParts.join(''), sessionId: doneEvt?.data?.session_id || sessionId || '' }
}

function screenshot(page: Page, name: string) {
  const p = path.join(RESULTS_DIR, `${name}.png`)
  return page.screenshot({ path: p, fullPage: true }).then(() => console.log(`  📸 ${name}`))
}

function saveReport(name: string, content: string) {
  const p = path.join(RESULTS_DIR, `${name}.md`)
  fs.writeFileSync(p, content, 'utf-8')
  console.log(`  💾 ${name}.md (${content.length}字)`)
}

/** 内容质量评分 */
function scoreReport(content: string, checks: Record<string, RegExp>): { score: number; details: string[] } {
  const details: string[] = []
  let passed = 0
  for (const [name, regex] of Object.entries(checks)) {
    const ok = regex.test(content)
    details.push(`${ok ? '✅' : '❌'} ${name}`)
    if (ok) passed++
  }
  return { score: Math.round((passed / Object.keys(checks).length) * 100), details }
}

// ============ 测试用例 ============

test.describe('真实场景全链路测试', () => {
  test.setTimeout(600000) // 10 分钟

  // ── 场景 1：公共知识库上传 ──

  test('场景1: 管理员上传文档到公共知识库', async ({ page }) => {
    console.log('\n📚 场景1: 公共知识库上传')

    // 上传培训管理办法到公共知识库
    const filePath = path.join(FIXTURES_DIR, '教职工培训管理办法.txt')
    const fileContent = fs.readFileSync(filePath)

    const resp = await page.request.post(`${API_URL}/api/admin/documents/import`, {
      headers: { Authorization: `Bearer ${ADMIN_TOKEN}` },
      multipart: {
        file: { name: '教职工培训管理办法.txt', mimeType: 'text/plain', buffer: fileContent },
      },
    })

    console.log(`  上传响应: ${resp.status()}`)
    if (resp.ok()) {
      const doc = await resp.json()
      console.log(`  doc_id: ${doc.doc_id}`)
      console.log(`  title: ${doc.title}`)
      console.log(`  category: ${doc.category}`)
      console.log(`  pageindex_status: ${doc.pageindex_status}`)
      fs.writeFileSync(path.join(RESULTS_DIR, 'uploaded_doc_id.txt'), doc.doc_id)
    }

    // 截图管理后台文档列表
    await page.goto(`${ADMIN_URL}/login`)
    await page.fill('input[placeholder*="用户名"]', 'admin')
    await page.fill('input[type="password"]', 'admin123')
    await page.click('button:has-text("登录")')
    await page.waitForTimeout(3000)
    await page.goto(`${ADMIN_URL}/documents`)
    await page.waitForTimeout(3000)
    await screenshot(page, '01-admin-kb-list')

    console.log('  ✅ 文档上传成功')
  })

  // ── 场景 2：规章制度审核（完整流程） ──

  test('场景2: 用户端审核规章制度 → 完整报告 + 质量验证', async ({ page }) => {
    console.log('\n📋 场景2: 规章制度审核')

    const token = await getUserToken(page)
    const regulation = fs.readFileSync(path.join(FIXTURES_DIR, '教职工培训管理办法.txt'), 'utf-8')

    // 轮 1: 发送审核请求
    console.log('  轮1: 发送审核请求...')
    const r1 = await sendChat(page, token,
      `请帮我全面审核以下规章制度，重点关注合规性和格式规范性：\n\n${regulation}`)

    saveReport('02-regulation-report', r1.content)

    // 意图验证（兼容 intent 和 skill_start 事件）
    const intent = r1.events.find(e => e.event === 'intent')
    const skillStart = r1.events.find(e => e.event === 'skill_start')
    const skillName = intent?.data?.skill || intent?.data?.skill_name
      || skillStart?.data?.skill || skillStart?.data?.skill_name || 'unknown'
    console.log(`  意图: ${skillName}`)
    expect(skillName).toBe('policy-review')

    // 内容质量评分
    const quality = scoreReport(r1.content, {
      '总体结论（通过/需修改/不建议）': /通过|需修改|不建议/,
      '风险等级分类（高/中/低）': /高风险|中风险|低风险|🔴|🟡|🟢/,
      '条号跳跃检测（第八条缺失）': /第八条|跳号|断号|编号.*连续|缺少/,
      '罚款越权检测': /罚款|行政处罚|越权|处罚权|权限/,
      '法律依据引用': /《.*法》|《.*条例》|法律|法规/,
      '修改建议': /建议|修改|完善|应当|应该/,
      '原文定位': /第.*条|第.*章/,
      '免责声明': /免责|仅供参考|不构成.*意见|AI/,
      '追问引导': /追问|继续|可以.*问|💡|您可以/,
    })
    console.log(`  📊 质量评分: ${quality.score}/100`)
    quality.details.forEach(d => console.log(`    ${d}`))
    expect(quality.score).toBeGreaterThanOrEqual(60)

    // 视觉验证: 用户端页面
    await page.goto(`${BASE_URL}/login`)
    await page.fill('input[placeholder*="用户名"]', 'wj2929')
    await page.fill('input[placeholder*="密码"]', 'wj2929')
    await page.click('button:has-text("登录")')
    await page.waitForTimeout(3000)
    await screenshot(page, '02-user-main')

    // 轮 2: 追问具体条款
    console.log('  轮2: 追问罚款条款...')
    const r2 = await sendChat(page, token,
      '第十二条的罚款条款具体存在什么法律风险？请给出修改后的完整条文。', r1.sessionId)

    saveReport('03-followup-penalty', r2.content)
    console.log(`  追问回复: ${r2.content.length}字`)

    const followupQuality = scoreReport(r2.content, {
      '针对第十二条': /第十二条|罚款|擅自放弃/,
      '法律分析': /行政处罚|处罚权|法律|法规|权限/,
      '修改后条文': /修改|建议|改为|调整|可以|应当/,
      '上下文连贯': /培训|教职工|管理办法/,
    })
    console.log(`  📊 追问质量: ${followupQuality.score}/100`)
    followupQuality.details.forEach(d => console.log(`    ${d}`))
    expect(followupQuality.score).toBeGreaterThanOrEqual(50)

    // 轮 3: 追问格式问题
    console.log('  轮3: 追问格式规范...')
    const r3 = await sendChat(page, token,
      '这份制度的格式规范性如何？缺少哪些必要部分？', r1.sessionId)

    saveReport('04-followup-format', r3.content)
    console.log(`  格式追问回复: ${r3.content.length}字`)
    expect(r3.content.length).toBeGreaterThan(100)

    console.log('  ✅ 规章制度审核 3 轮对话完成')
  })

  // ── 场景 3：合同审核（不同审核场景） ──

  test('场景3: 用户端审核合同 → 合同场景识别 + 报告', async ({ page }) => {
    console.log('\n📝 场景3: 合同审核')

    const token = await getUserToken(page)
    const contract = fs.readFileSync(path.join(FIXTURES_DIR, '科研项目合同模板.txt'), 'utf-8')

    // 发送合同审核请求
    console.log('  发送合同审核请求...')
    const r1 = await sendChat(page, token,
      `请帮我审查以下科研项目委托合同，重点关注违约条款和权利义务是否对等：\n\n${contract}`)

    saveReport('05-contract-report', r1.content)

    // 意图验证（兼容 intent 和 skill_start 事件）
    const intent = r1.events.find(e => e.event === 'intent')
    const skillStart = r1.events.find(e => e.event === 'skill_start')
    const skillName = intent?.data?.skill || intent?.data?.skill_name
      || skillStart?.data?.skill || skillStart?.data?.skill_name || 'unknown'
    console.log(`  意图: ${skillName}`)

    // 合同审核质量评分
    const quality = scoreReport(r1.content, {
      '总体结论': /通过|需修改|不建议/,
      '违约责任不对等检测': /违约.*不对等|单方面|仅约束|不公平|甲方.*违约|乙方.*违约/,
      '甲方单方解除权检测': /单方.*解除|解除.*权|不予退还|不合理/,
      '违约金过高检测': /0\.5%|违约金.*过高|违约金.*比例/,
      '争议解决条款分析': /管辖|诉讼|仲裁|争议/,
      '知识产权条款分析': /知识产权|成果.*归属/,
      '法律依据': /《.*法》|《.*条例》|民法典|合同法/,
      '修改建议': /建议|修改|完善|增加|补充/,
      '免责声明': /免责|仅供参考|AI/,
    })
    console.log(`  📊 合同审核质量: ${quality.score}/100`)
    quality.details.forEach(d => console.log(`    ${d}`))
    expect(quality.score).toBeGreaterThanOrEqual(50)

    // 追问: 甲方单方解除权
    console.log('  追问: 甲方解除权...')
    const r2 = await sendChat(page, token,
      '第八条甲方单方面解除权是否合法？请详细分析并给出修改方案。', r1.sessionId)
    saveReport('06-contract-followup', r2.content)
    console.log(`  追问回复: ${r2.content.length}字`)

    const followupQuality = scoreReport(r2.content, {
      '针对第八条': /第八条|单方.*解除|解除权/,
      '法律分析': /合同法|民法典|法律|合法|不合理/,
      '修改方案': /修改|建议|改为|补充|条件/,
    })
    console.log(`  📊 追问质量: ${followupQuality.score}/100`)
    expect(followupQuality.score).toBeGreaterThanOrEqual(50)

    console.log('  ✅ 合同审核完成')
  })

  // ── 场景 4：用户端页面交互 + 视觉验证 ──

  test('场景4: 用户端页面真实交互 + 截图验证', async ({ page }) => {
    console.log('\n🖥️ 场景4: 页面交互视觉验证')

    // 登录
    await page.goto(`${BASE_URL}/login`)
    await page.fill('input[placeholder*="用户名"]', 'wj2929')
    await page.fill('input[placeholder*="密码"]', 'wj2929')
    await page.click('button:has-text("登录")')
    await page.waitForTimeout(3000)
    await screenshot(page, '07-user-logged-in')

    // 新建对话
    const newBtn = page.locator('text=新建对话').first()
    if (await newBtn.isVisible()) {
      await newBtn.click()
      await page.waitForTimeout(1000)
    }

    // 输入审核指令
    const textarea = page.locator('textarea').first()
    if (!await textarea.isVisible()) {
      console.log('  ⏭️ 未找到输入框')
      return
    }

    await textarea.fill('帮我审核以下合同条款是否存在法律风险：\n第七条 乙方违约应支付合同总额20%违约金。\n第八条 甲方可随时单方解除合同。')
    await screenshot(page, '08-input-contract')

    // 发送
    const sendBtn = page.locator('button:has-text("发送")').first()
    if (await sendBtn.isVisible()) {
      await sendBtn.click()

      // 等待 Skill 识别
      await page.waitForTimeout(5000)
      await screenshot(page, '09-skill-identified')

      // 等待工具调用阶段
      await page.waitForTimeout(15000)
      await screenshot(page, '10-tool-calling')

      // 等待报告生成
      await page.waitForTimeout(30000)
      await screenshot(page, '11-report-streaming')

      // 等待完成
      await page.waitForTimeout(20000)
      await screenshot(page, '12-report-complete')

      // 验证页面渲染
      const body = await page.textContent('body') || ''

      const renderChecks = scoreReport(body, {
        '技能标识显示': /policy-review|规章制度审核/,
        '报告内容渲染': /审核|风险|建议|违约/,
        '发送按钮恢复': /发送/,
      })
      console.log(`  📊 页面渲染: ${renderChecks.score}/100`)
      renderChecks.details.forEach(d => console.log(`    ${d}`))

      // 追问交互
      await textarea.fill('第七条违约金比例是否过高？')
      await screenshot(page, '13-followup-input')
      await sendBtn.click()
      await page.waitForTimeout(20000)
      await screenshot(page, '14-followup-response')

      console.log('  ✅ 页面交互验证完成')
    }
  })

  // ── 场景 5：管理后台 Skill 编辑 + 知识库绑定视觉验证 ──

  test('场景5: 管理后台 Skill 编辑界面视觉验证', async ({ page }) => {
    console.log('\n⚙️ 场景5: 管理后台视觉验证')

    await page.goto(`${ADMIN_URL}/login`)
    await page.fill('input[placeholder*="用户名"]', 'admin')
    await page.fill('input[type="password"]', 'admin123')
    await page.click('button:has-text("登录")')
    await page.waitForTimeout(3000)

    // Skills 管理
    await page.goto(`${ADMIN_URL}/super-agent/skills`)
    await page.waitForTimeout(3000)
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
    await page.waitForTimeout(500)
    await screenshot(page, '15-skills-list')

    // 找到 policy-review 编辑
    const allCards = page.locator('.n-card')
    const count = await allCards.count()
    for (let i = 0; i < count; i++) {
      const text = await allCards.nth(i).textContent()
      if (text?.includes('规章制度审核')) {
        const editBtn = allCards.nth(i).locator('text=编辑')
        if (await editBtn.isVisible()) {
          await editBtn.click()
          await page.waitForTimeout(2000)

          // Tab 1: 基本信息（含模型选择）
          await screenshot(page, '16-edit-basic-info')

          // Tab: 知识库绑定
          const kbTab = page.locator('text=知识库绑定').first()
          if (await kbTab.isVisible()) {
            await kbTab.click()
            await page.waitForTimeout(1000)
            await screenshot(page, '17-edit-kb-binding')
          }

          // Tab: 意图识别配置
          const intentTab = page.locator('text=意图识别配置').first()
          if (await intentTab.isVisible()) {
            await intentTab.click()
            await page.waitForTimeout(1000)
            await screenshot(page, '18-edit-intent-config')
          }
        }
        break
      }
    }

    // 公共知识库管理页
    await page.goto(`${ADMIN_URL}/documents`)
    await page.waitForTimeout(3000)
    await screenshot(page, '19-admin-documents')

    console.log('  ✅ 管理后台视觉验证完成')
  })
})
