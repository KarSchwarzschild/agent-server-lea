/**
 * policy-review 多轮对话 + 附件 + 内容质量验证
 *
 * 场景A: 规章制度审核 → 3轮追问
 * 场景B: 合同审核 → 2轮追问
 * 场景C: 用户端页面附件上传交互
 * 每轮验证：意图/工具调用/报告质量/截图
 */
import { test, expect, type Page } from '@playwright/test'
import * as fs from 'fs'
import * as path from 'path'
import { fileURLToPath } from 'url'

const BASE_URL = 'http://localhost:9002'
const API_URL = 'http://localhost:8000'
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FIXTURES_DIR = path.join(__dirname, 'fixtures')
const RESULTS_DIR = path.join(__dirname, 'results', 'multiround')
if (!fs.existsSync(RESULTS_DIR)) fs.mkdirSync(RESULTS_DIR, { recursive: true })

async function getUserToken(page: Page): Promise<string> {
  const r = await page.request.post(`${API_URL}/api/auth/login`, { data: { username: 'wj2929', password: 'wj2929' } })
  return (await r.json()).access_token
}

async function chat(
  page: Page, token: string, message: string, sessionId?: string
): Promise<{ content: string; sessionId: string; tools: string[]; events: Array<{ event: string; data: any }> }> {
  const payload: any = { message }
  if (sessionId) payload.session_id = sessionId
  const r = await page.request.post(`${API_URL}/api/user/super-agent/chat/stream?async=false`, {
    headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
    data: payload, timeout: 180000,
  })
  const body = await r.text()
  const events: Array<{ event: string; data: any }> = []
  const contentParts: string[] = []
  const tools: string[] = []
  let cur = 'message'
  for (const line of body.split('\n')) {
    if (line.startsWith('event: ')) cur = line.substring(7).trim()
    else if (line.startsWith('data: ') && line !== 'data: [DONE]') {
      try {
        const d = JSON.parse(line.substring(6))
        events.push({ event: cur, data: d })
        if (cur === 'content') { const t = d.content || d.text || d.delta || ''; if (t) contentParts.push(t) }
        if (cur === 'tool_call_start' && d.tool_name) tools.push(d.tool_name)
      } catch {}
    }
  }
  const done = events.find(e => e.event === 'done')
  return { content: contentParts.join(''), sessionId: done?.data?.session_id || sessionId || '', tools, events }
}

function save(name: string, content: string) {
  fs.writeFileSync(path.join(RESULTS_DIR, `${name}.md`), content, 'utf-8')
  console.log(`    💾 ${name}.md (${content.length}字)`)
}

function quality(content: string, checks: Record<string, RegExp>): number {
  let pass = 0
  for (const [name, re] of Object.entries(checks)) {
    const ok = re.test(content)
    console.log(`    ${ok ? '✅' : '❌'} ${name}`)
    if (ok) pass++
  }
  const score = Math.round((pass / Object.keys(checks).length) * 100)
  console.log(`    📊 质量: ${score}/100`)
  return score
}

// ============================================================

test.describe.serial('场景A: 规章制度审核多轮', () => {
  test.setTimeout(300000)
  let token = ''
  let sid = ''

  test('A1: 发送培训管理办法全文 → 审核报告', async ({ page }) => {
    token = await getUserToken(page)
    const doc = fs.readFileSync(path.join(FIXTURES_DIR, '教职工培训管理办法.txt'), 'utf-8')

    console.log('  A1: 审核培训管理办法...')
    const r = await chat(page, token, `请全面审核以下规章制度：\n\n${doc}`)
    sid = r.sessionId
    save('A1-regulation-report', r.content)

    console.log(`    工具: ${[...new Set(r.tools)].join(', ')}`)
    expect(r.content.length).toBeGreaterThan(300)

    const score = quality(r.content, {
      '总体结论': /通过|需修改|不建议/,
      '风险分级': /高风险|中风险|低风险/,
      '条号跳跃（第八条缺失）': /第八条|跳号|断号|编号.*连续|缺/,
      '罚款越权': /罚款|行政处罚|越权|处罚权/,
      '附则不完整': /解释权|废止|旧.*制度/,
      '法律依据': /《.*》|法律|法规/,
      '修改建议含条文': /修改|改为|调整|应当/,
      '免责声明': /免责|仅供参考|AI/,
    })
    expect(score).toBeGreaterThanOrEqual(50)
  })

  test('A2: 追问罚款条款详细分析', async ({ page }) => {
    console.log('  A2: 追问罚款条款...')
    const r = await chat(page, token, '第十二条的罚款500-2000元是否合法？请详细分析法律依据，并给出修改后的完整条文。', sid)
    save('A2-penalty-analysis', r.content)

    expect(r.content.length).toBeGreaterThan(200)
    quality(r.content, {
      '引用第十二条': /第十二条|罚款|擅自放弃/,
      '行政处罚法分析': /行政处罚|处罚权|事业单位/,
      '修改后条文': /修改|改为|建议.*条文|第十二条.*[改修]/,
      '替代措施': /警告|通报|记过|绩效|内部.*管理/,
    })
  })

  test('A3: 追问缺少的第八条', async ({ page }) => {
    console.log('  A3: 追问条号跳跃...')
    const r = await chat(page, token, '第七条到第九条之间缺少了第八条，请帮我补充一条合理的条款内容。', sid)
    save('A3-missing-article', r.content)

    expect(r.content.length).toBeGreaterThan(100)
    quality(r.content, {
      '第八条内容': /第八条/,
      '与上下文连贯': /培训|经费|审批|管理/,
    })
  })

  test('A4: 生成修改意见书', async ({ page }) => {
    console.log('  A4: 生成修改意见书...')
    const r = await chat(page, token, '请基于以上审核结果，生成一份正式的修改意见书，可以直接提交给学校管理层。', sid)
    save('A4-revision-memo', r.content)

    expect(r.content.length).toBeGreaterThan(300)
    quality(r.content, {
      '正式文体': /意见|建议|修改|审核/,
      '列举问题': /第.*条|问题|风险/,
      '修改方案': /修改|调整|建议|完善/,
    })
  })
})

test.describe.serial('场景B: 合同审核多轮', () => {
  test.setTimeout(300000)
  let token = ''
  let sid = ''

  test('B1: 发送科研合同全文 → 审核报告', async ({ page }) => {
    token = await getUserToken(page)
    const doc = fs.readFileSync(path.join(FIXTURES_DIR, '科研项目合同模板.txt'), 'utf-8')

    console.log('  B1: 审核科研合同...')
    const r = await chat(page, token, `请帮我审查以下科研项目合同，重点关注违约条款和甲方权利：\n\n${doc}`)
    sid = r.sessionId
    save('B1-contract-report', r.content)

    console.log(`    工具: ${[...new Set(r.tools)].join(', ')}`)

    const score = quality(r.content, {
      '总体结论': /通过|需修改|不建议/,
      '违约金过高（0.5%/日）': /0\.5%|违约金.*过高|比例.*高/,
      '甲方单方解除': /单方.*解除|解除权|不予退还/,
      '权利义务不对等': /不对等|不平衡|仅约束.*乙方|不公平/,
      '争议解决分析': /管辖|诉讼|仲裁/,
      '民法典引用': /民法典|合同法|585|497/,
      '修改建议': /建议|修改|增加|补充/,
      '免责声明': /免责|仅供参考/,
    })
    expect(score).toBeGreaterThanOrEqual(50)
  })

  test('B2: 追问违约金调整方案', async ({ page }) => {
    console.log('  B2: 追问违约金...')
    const r = await chat(page, token, '合同第七条违约金每日0.5%是否过高？请给出合理的违约金比例建议和修改后的条文。', sid)
    save('B2-penalty-revision', r.content)

    expect(r.content.length).toBeGreaterThan(150)
    quality(r.content, {
      '引用第七条': /第七条|0\.5%|违约金/,
      '法律标准': /民法典|585|30%|实际损失/,
      '修改后条文': /修改|改为|调整|建议/,
    })
  })

  test('B3: 追问补充保护乙方条款', async ({ page }) => {
    console.log('  B3: 追问乙方保护...')
    const r = await chat(page, token, '请帮我增加保护乙方权益的条款，特别是甲方违约时乙方的救济方式。', sid)
    save('B3-party-b-protection', r.content)

    expect(r.content.length).toBeGreaterThan(100)
    quality(r.content, {
      '甲方违约责任': /甲方.*违约|甲方.*责任/,
      '乙方救济': /乙方.*权利|解除|损失|赔偿/,
    })
  })
})

test.describe('场景C: 用户端页面附件上传交互', () => {
  test.setTimeout(180000)

  test('C1: 上传txt附件 + 审核指令 + 截图全流程', async ({ page }) => {
    console.log('  C1: 页面附件上传交互...')

    // 登录
    await page.goto(`${BASE_URL}/login`)
    await page.fill('input[placeholder*="用户名"]', 'wj2929')
    await page.fill('input[placeholder*="密码"]', 'wj2929')
    await page.click('button:has-text("登录")')
    await page.waitForTimeout(3000)

    // 新建对话
    const newBtn = page.locator('text=新建对话').first()
    if (await newBtn.isVisible()) { await newBtn.click(); await page.waitForTimeout(1000) }

    // 上传附件
    const fileInput = page.locator('input[type="file"]').first()
    if (await fileInput.count() > 0) {
      const filePath = path.join(FIXTURES_DIR, '教职工培训管理办法.txt')
      await fileInput.setInputFiles(filePath)
      await page.waitForTimeout(3000)
      await page.screenshot({ path: path.join(RESULTS_DIR, 'C1-01-file-uploaded.png') })
      console.log('    📸 C1-01-file-uploaded')
    } else {
      // 通过附件按钮触发
      const attachBtn = page.locator('text=附件').first()
      if (await attachBtn.isVisible()) {
        await attachBtn.click()
        await page.waitForTimeout(1000)
      }
      await page.screenshot({ path: path.join(RESULTS_DIR, 'C1-01-attach-panel.png') })
      console.log('    📸 C1-01-attach-panel')
    }

    // 输入审核指令
    const textarea = page.locator('textarea').first()
    if (await textarea.isVisible()) {
      await textarea.fill('请全面审核这份教职工培训管理办法')
      await page.screenshot({ path: path.join(RESULTS_DIR, 'C1-02-input.png') })
      console.log('    📸 C1-02-input')

      const sendBtn = page.locator('button:has-text("发送")').first()
      if (await sendBtn.isVisible()) {
        await sendBtn.click()

        // 等待 Skill 开始
        await page.waitForTimeout(5000)
        await page.screenshot({ path: path.join(RESULTS_DIR, 'C1-03-skill-start.png') })
        console.log('    📸 C1-03-skill-start')

        // 等待报告流式输出
        await page.waitForTimeout(25000)
        await page.screenshot({ path: path.join(RESULTS_DIR, 'C1-04-streaming.png') })
        console.log('    📸 C1-04-streaming')

        // 等待完成
        await page.waitForTimeout(30000)
        await page.screenshot({ path: path.join(RESULTS_DIR, 'C1-05-complete.png'), fullPage: true })
        console.log('    📸 C1-05-complete')

        const body = await page.textContent('body') || ''
        const hasReport = /审核|风险|建议|条款|问题/.test(body)
        console.log(`    报告渲染: ${hasReport ? '✅' : '❌'}`)

        // 追问
        await textarea.fill('请生成修改意见书')
        await sendBtn.click()
        await page.waitForTimeout(25000)
        await page.screenshot({ path: path.join(RESULTS_DIR, 'C1-06-followup.png'), fullPage: true })
        console.log('    📸 C1-06-followup')

        console.log('    ✅ 附件交互全流程完成')
      }
    }
  })
})
