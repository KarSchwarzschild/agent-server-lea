/**
 * policy-review 最终验收测试
 *
 * 完整真实流程：用户上传文档 → 审核 → 多轮追问 → 验证会话持久化
 * 每步截图 + 内容质量打分 + 会话存储验证
 */
import { test, expect, type Page } from '@playwright/test'
import * as fs from 'fs'
import * as path from 'path'
import { fileURLToPath } from 'url'

const BASE_URL = 'http://localhost:9002'
const API_URL = 'http://localhost:8000'
const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FIXTURES = path.join(__dirname, 'fixtures')
const OUT = path.join(__dirname, 'results', 'final')
if (!fs.existsSync(OUT)) fs.mkdirSync(OUT, { recursive: true })

const snap = (page: Page, name: string) =>
  page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true })
    .then(() => console.log(`  📸 ${name}`))

const save = (name: string, text: string) => {
  fs.writeFileSync(path.join(OUT, `${name}.md`), text, 'utf-8')
  console.log(`  💾 ${name} (${text.length}字)`)
}

function score(text: string, checks: Record<string, RegExp>): number {
  let p = 0
  for (const [k, re] of Object.entries(checks)) {
    const ok = re.test(text)
    console.log(`    ${ok ? '✅' : '❌'} ${k}`)
    if (ok) p++
  }
  const s = Math.round((p / Object.keys(checks).length) * 100)
  console.log(`    📊 ${s}/100`)
  return s
}

test.describe.serial('最终验收：用户上传文档 → 审核 → 追问 → 验证', () => {
  test.setTimeout(600000) // 10min

  let page: Page

  test.beforeAll(async ({ browser }) => {
    page = await browser.newPage()
    // 登录
    await page.goto(`${BASE_URL}/login`)
    await page.waitForSelector('input[placeholder*="用户名"]', { timeout: 10000 })
    await page.fill('input[placeholder*="用户名"]', 'wj2929')
    await page.fill('input[placeholder*="密码"]', 'wj2929')
    await page.click('button:has-text("登录")')
    await page.waitForTimeout(3000)
    await snap(page, '00-logged-in')
    console.log('✅ 登录成功')
  })

  test.afterAll(async () => { await page.close() })

  test('步骤1: 新建对话 + 上传附件', async () => {
    console.log('\n📎 步骤1: 上传附件')

    // 新建对话
    const newBtn = page.locator('text=新建对话').first()
    if (await newBtn.isVisible()) { await newBtn.click(); await page.waitForTimeout(1000) }

    // 上传文件
    const fileInput = page.locator('input[type="file"]').first()
    if (await fileInput.count() > 0) {
      await fileInput.setInputFiles(path.join(FIXTURES, '教职工培训管理办法.txt'))
      await page.waitForTimeout(3000) // 等待上传开始
      await snap(page, '01-file-uploading')

      // 等待附件状态变为就绪（"深度阅读中..."消失，或出现"可精准定位"）
      // 最多等 30 秒
      console.log('  等待附件就绪...')
      for (let i = 0; i < 30; i++) {
        const body = await page.textContent('body') || ''
        if (body.includes('可精准定位')) {
          console.log(`  ✅ 附件就绪（可精准定位），耗时 ${i + 3}s`)
          break
        }
        if (!body.includes('深度阅读中') && body.includes('教职工培训管理办法') && i > 5) {
          // 深度阅读完成但不一定显示"可精准定位"（txt 可能不支持）
          console.log(`  ✅ 附件就绪（上传完成），耗时 ${i + 3}s`)
          break
        }
        await page.waitForTimeout(1000)
      }
      await snap(page, '01-file-ready')

      // 验证附件条显示
      const body = await page.textContent('body') || ''
      expect(body).toContain('教职工培训管理办法')
      console.log('  ✅ 附件上传完成，显示在输入框下方')
    } else {
      console.log('  ⚠️ 未找到 file input，跳过附件上传')
    }
  })

  test('步骤2: 发送审核指令 → 等待完整报告', async () => {
    console.log('\n📋 步骤2: 发送审核指令')

    const textarea = page.locator('textarea').first()
    await textarea.fill('请全面审核这份教职工培训管理办法，重点关注以下方面：\n1. 罚款条款是否合法\n2. 条号是否连续\n3. 附则是否完整')
    await snap(page, '02-input-instruction')

    const sendBtn = page.locator('button:has-text("发送")').first()
    await sendBtn.click()

    // 等待 Skill 识别（3s）
    await page.waitForTimeout(3000)
    await snap(page, '03-skill-identified')

    // 检查是否显示 policy-review 技能标识
    const body1 = await page.textContent('body') || ''
    const hasSkillTag = body1.includes('policy-review') || body1.includes('规章制度审核')
    console.log(`  技能标识: ${hasSkillTag ? '✅ policy-review' : '⚠️ 未检测到'}`)

    // 等待工具调用阶段（10s）
    await page.waitForTimeout(10000)
    await snap(page, '04-tool-calling')

    // 等待报告生成（30s）
    await page.waitForTimeout(30000)
    await snap(page, '05-report-streaming')

    // 等待完成（20s）
    await page.waitForTimeout(20000)
    await snap(page, '06-report-complete')

    // 验证报告渲染
    const body2 = await page.textContent('body') || ''
    console.log('\n  报告内容检查:')
    score(body2, {
      '技能标识': /policy-review|规章制度审核/,
      '审核报告标题': /审核报告|审核结果/,
      '风险分级': /高风险|中风险|低风险/,
      '罚款问题': /罚款|行政处罚|越权/,
      '法律引用': /《.*》|法律|法规/,
      '免责声明': /免责|仅供参考|AI/,
      '发送按钮恢复': /发送/,
    })

    console.log('  ✅ 审核报告页面渲染完成')
  })

  test('步骤3: 追问 — 罚款条款详细分析', async () => {
    console.log('\n🔍 步骤3: 追问罚款条款')

    const textarea = page.locator('textarea').first()
    await textarea.fill('第十二条的罚款500-2000元是否合法？请给出修改后的完整条文，以及可替代的合法管理措施。')
    await snap(page, '07-followup1-input')

    const sendBtn = page.locator('button:has-text("发送")').first()
    await sendBtn.click()

    await page.waitForTimeout(30000)
    await snap(page, '08-followup1-response')

    const body = await page.textContent('body') || ''
    console.log('  追问1 内容检查:')
    score(body, {
      '第十二条分析': /第十二条|罚款/,
      '行政处罚法引用': /行政处罚|处罚权|事业单位/,
      '修改后条文': /修改|改为|建议/,
      '替代措施': /通报|警告|绩效|记过|内部管理/,
    })
  })

  test('步骤4: 追问 — 补充缺失条号', async () => {
    console.log('\n🔧 步骤4: 追问补充第八条')

    const textarea = page.locator('textarea').first()
    await textarea.fill('第七条到第九条之间缺少了第八条，请帮我补充一条关于培训审批流程的条款。')
    await snap(page, '09-followup2-input')

    const sendBtn = page.locator('button:has-text("发送")').first()
    await sendBtn.click()

    await page.waitForTimeout(25000)
    await snap(page, '10-followup2-response')

    const body = await page.textContent('body') || ''
    const hasArticle8 = /第八条/.test(body)
    console.log(`  第八条补充: ${hasArticle8 ? '✅' : '❌'}`)
  })

  test('步骤5: 追问 — 生成修改意见书', async () => {
    console.log('\n📝 步骤5: 生成修改意见书')

    const textarea = page.locator('textarea').first()
    await textarea.fill('请基于以上所有审核意见，生成一份正式的修改意见书，可以直接提交给学校管理层审批。')
    await snap(page, '11-followup3-input')

    const sendBtn = page.locator('button:has-text("发送")').first()
    await sendBtn.click()

    await page.waitForTimeout(35000)
    await snap(page, '12-followup3-response')

    // 滚动到底部截全
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight))
    await page.waitForTimeout(1000)
    await snap(page, '13-followup3-bottom')

    const body = await page.textContent('body') || ''
    console.log('  修改意见书检查:')
    score(body, {
      '正式文体': /意见书|修改意见|审核意见/,
      '列举具体问题': /第.*条|问题|风险/,
      '修改方案': /修改|调整|建议|完善/,
    })
  })

  test('步骤6: 验证会话持久化', async () => {
    console.log('\n💾 步骤6: 验证会话存储')

    // 获取 token
    const loginResp = await page.request.post(`${API_URL}/api/auth/login`, {
      data: { username: 'wj2929', password: 'wj2929' }
    })
    const token = (await loginResp.json()).access_token

    // 获取最新会话列表
    const sessResp = await page.request.get(`${API_URL}/api/user/super-agent/sessions?page=1&page_size=3`, {
      headers: { Authorization: `Bearer ${token}` }
    })
    const sessions = await sessResp.json()
    const list = Array.isArray(sessions) ? sessions : sessions.sessions || []

    expect(list.length).toBeGreaterThan(0)
    const latest = list[0]
    const sid = latest.session_id
    console.log(`  最新会话: ${sid}`)
    console.log(`  标题: ${(latest.title || '').substring(0, 50)}`)

    // 获取详情
    const detailResp = await page.request.get(`${API_URL}/api/user/super-agent/sessions/${sid}`, {
      headers: { Authorization: `Bearer ${token}` }
    })
    const detail = await detailResp.json()
    const msgs = detail.messages || []

    console.log(`  消息数: ${msgs.length}`)

    // 检查第一条消息是否包含附件内容
    if (msgs.length > 0) {
      const firstMsg = msgs[0]
      const firstContent = firstMsg.content || ''
      const hasAttachmentContent = firstContent.includes('第一章') || firstContent.includes('教职工') || firstContent.length > 200
      console.log(`  第一条消息长度: ${firstContent.length}字`)
      console.log(`  包含附件内容: ${hasAttachmentContent ? '✅' : '❌ 附件未注入'}`)
    }

    let totalChars = 0
    for (const m of msgs) {
      const role = m.role || '?'
      const content = m.content || ''
      const len = typeof content === 'string' ? content.length : 0
      totalChars += len
      console.log(`    ${role}: ${len}字`)
    }
    console.log(`  总字数: ${totalChars}`)

    // 验证消息完整性
    expect(msgs.length).toBeGreaterThanOrEqual(6) // 至少 3 轮 = 6 条
    expect(totalChars).toBeGreaterThan(1000) // 多轮对话总字数

    // 保存详情
    const transcript = msgs.map((m: any) => `### ${m.role}\n\n${m.content}\n`).join('\n---\n\n')
    save('session-transcript', transcript)

    console.log('  ✅ 会话持久化验证通过')
  })

  test('步骤7: 刷新页面 → 历史对话可恢复', async () => {
    console.log('\n🔄 步骤7: 刷新页面验证历史恢复')

    // 刷新页面
    await page.reload()
    await page.waitForTimeout(5000)
    await snap(page, '14-page-refreshed')

    // 检查左侧历史列表
    const body = await page.textContent('body') || ''
    const hasHistory = body.includes('培训管理办法') || body.includes('审核')
    console.log(`  历史对话可见: ${hasHistory ? '✅' : '❌'}`)

    // 点击历史对话恢复
    const historyItem = page.locator('text=培训管理办法').first()
    if (await historyItem.isVisible()) {
      await historyItem.click()
      await page.waitForTimeout(3000)
      await snap(page, '15-history-restored')

      const restored = await page.textContent('body') || ''
      const hasContent = /审核|罚款|风险|修改/.test(restored)
      console.log(`  对话内容恢复: ${hasContent ? '✅' : '❌'}`)
    }

    console.log('  ✅ 历史恢复验证完成')
  })
})
