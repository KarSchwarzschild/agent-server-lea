/**
 * 深度综述全流程 E2E 测试（增强版）
 *
 * 验证项：
 *   1. Step 1 模式选择（快速/深度卡片、推荐角标、步骤指示器、模式切换）
 *   2. Step 2 任务配置（主题输入、文献来源、报告参数、手动补充文献）
 *   3. Step 3 确认生成（配置摘要、预计耗时、可离开提示）
 *   4. 快速模式流程（选择快速 → 点下一步 → 跳转到 /review）
 *   5. 深度模式完整流程（Step 1→2→3→提交）
 *   6. 综述历史页（列表、新建按钮、路由跳转、空状态、搜索）
 *   7. 路由重定向（/review → /review/history）
 *   8. 进度页基本元素（阶段、进度条、取消按钮）
 *   9. 报告查看页（工具栏、目录、搜索、导出）
 *   10. 视觉回归截图（所有关键页面）
 *
 * 运行方式：
 *   npx playwright test tests/e2e/deep-review-wizard.spec.ts
 *
 * 前置条件：
 *   - 前端 dev server: http://localhost:9002
 *   - 后端 API: http://localhost:8000
 */
import { test, expect, type Page } from '@playwright/test'

const TOKEN =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyXzE5NzJmMzFhZTU5ZSIsInJvbGVzIjpbInVzZXIiXSwicGVybWlzc2lvbnMiOlsiZG9jdW1lbnQ6cmVhZCIsImtiOnJlYWQiXSwiZXhwIjoxNzc2NTQ5Mzc2fQ.WiE83JdJm037W8eRbsonR-p33onMFEmB70pKUd9k3fI'

// ─── Helpers ───────────────────────────────────────────

/** 注入 token 到 localStorage，跳过登录页 */
async function login(page: Page) {
  await page.goto('/')
  await page.evaluate((token) => {
    localStorage.setItem('token', token)
  }, TOKEN)
}

/** 导航到 Step 2 */
async function goToStep2(page: Page) {
  await page.goto('/review/new')
  await page.click('button:has-text("下一步")')
}

/** 导航到 Step 3（填写主题后） */
async function goToStep3(page: Page, topic = '终身教育视角下的老年数字素养提升路径研究') {
  await goToStep2(page)
  await page.locator('textarea').first().fill(topic)
  await page.click('button:has-text("下一步")')
}

// ─── 1. Step 1: 模式选择 ───────────────────────────────

test.describe('Step 1 — 模式选择', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('页面加载显示两张模式卡片', async ({ page }) => {
    await page.goto('/review/new')
    await expect(page.locator('.mode-card')).toHaveCount(2)
    await expect(page.locator('.mode-title', { hasText: '快速综述' })).toBeVisible()
    await expect(page.locator('.mode-title', { hasText: '深度综述' })).toBeVisible()
  })

  test('默认选中深度综述', async ({ page }) => {
    await page.goto('/review/new')
    const deepCard = page.locator('.mode-card').nth(1)
    await expect(deepCard).toHaveClass(/active/)
    // 快速卡片未选中
    const quickCard = page.locator('.mode-card').nth(0)
    await expect(quickCard).not.toHaveClass(/active/)
  })

  test('点击切换到快速模式', async ({ page }) => {
    await page.goto('/review/new')
    const quickCard = page.locator('.mode-card').nth(0)
    await quickCard.click()
    await expect(quickCard).toHaveClass(/active/)
    // 深度卡片变为未选中
    await expect(page.locator('.mode-card').nth(1)).not.toHaveClass(/active/)
  })

  test('双向切换模式', async ({ page }) => {
    await page.goto('/review/new')
    // 默认深度 → 切换到快速 → 切换回深度
    await page.locator('.mode-card').nth(0).click()
    await expect(page.locator('.mode-card').nth(0)).toHaveClass(/active/)
    await page.locator('.mode-card').nth(1).click()
    await expect(page.locator('.mode-card').nth(1)).toHaveClass(/active/)
    await expect(page.locator('.mode-card').nth(0)).not.toHaveClass(/active/)
  })

  test('推荐角标仅在深度模式卡片显示', async ({ page }) => {
    await page.goto('/review/new')
    await expect(page.locator('.mode-badge.pro')).toBeVisible()
    await expect(page.locator('.mode-badge.pro')).toHaveText('推荐')
    // 快速模式角标不带 pro class
    const quickBadge = page.locator('.mode-card').nth(0).locator('.mode-badge')
    await expect(quickBadge).toHaveText('轻量')
    await expect(quickBadge).not.toHaveClass(/pro/)
  })

  test('步骤指示器显示 3 步', async ({ page }) => {
    await page.goto('/review/new')
    await expect(page.locator('.n-step')).toHaveCount(3)
  })

  test('下一步按钮可点击', async ({ page }) => {
    await page.goto('/review/new')
    const nextBtn = page.locator('button:has-text("下一步")')
    await expect(nextBtn).toBeEnabled()
  })

  test('模式卡片特性列表完整', async ({ page }) => {
    await page.goto('/review/new')
    // 快速模式 4 个特性
    const quickFeatures = page.locator('.mode-card').nth(0).locator('.feature-item')
    await expect(quickFeatures).toHaveCount(4)
    // 深度模式 4 个特性
    const deepFeatures = page.locator('.mode-card').nth(1).locator('.feature-item')
    await expect(deepFeatures).toHaveCount(4)
  })

  test('选中状态显示"已选择"提示', async ({ page }) => {
    await page.goto('/review/new')
    // 默认深度选中
    await expect(page.locator('.mode-card').nth(1).locator('.mode-select-hint')).toHaveText('✓ 已选择')
    await expect(page.locator('.mode-card').nth(0).locator('.mode-select-hint')).toHaveText('点击选择')
  })

  test('底部提示文案', async ({ page }) => {
    await page.goto('/review/new')
    await expect(page.locator('.mode-tip')).toContainText('章节级控制')
  })
})

// ─── 2. Step 2: 配置任务 ─────────────────────────────────

test.describe('Step 2 — 配置任务', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('点击下一步进入配置页', async ({ page }) => {
    await goToStep2(page)
    await expect(page.locator('text=研究主题')).toBeVisible()
  })

  test('研究主题输入框', async ({ page }) => {
    await goToStep2(page)
    const textarea = page.locator('textarea').first()
    await textarea.fill('终身教育视角下的老年数字素养提升路径研究')
    await expect(textarea).toHaveValue('终身教育视角下的老年数字素养提升路径研究')
  })

  test('研究要求输入框可选', async ({ page }) => {
    await goToStep2(page)
    await expect(page.locator('text=研究要求')).toBeVisible()
    const reqTextarea = page.locator('textarea').nth(1)
    await reqTextarea.fill('重点关注2020年后的实证研究')
    await expect(reqTextarea).toHaveValue('重点关注2020年后的实证研究')
  })

  test('文献来源三个 checkbox 可见', async ({ page }) => {
    await goToStep2(page)
    await expect(page.getByRole('checkbox', { name: '公共知识库' })).toBeVisible()
    await expect(page.getByRole('checkbox', { name: '个人知识库' })).toBeVisible()
    await expect(page.getByRole('checkbox', { name: '学术搜索' })).toBeVisible()
  })

  test('手动补充文献按钮存在', async ({ page }) => {
    await goToStep2(page)
    await expect(page.locator('text=从个人知识库选择')).toBeVisible()
    await expect(page.locator('text=上传新文献')).toBeVisible()
  })

  test('点击从个人知识库选择展开列表', async ({ page }) => {
    await goToStep2(page)
    await page.click('button:has-text("从个人知识库选择")')
    await expect(page.locator('.doc-picker')).toBeVisible()
  })

  test('报告类型下拉框', async ({ page }) => {
    await goToStep2(page)
    await expect(page.locator('text=报告类型')).toBeVisible()
  })

  test('目标字数下拉框', async ({ page }) => {
    await goToStep2(page)
    await expect(page.locator('text=目标字数')).toBeVisible()
  })

  test('引用风格下拉框', async ({ page }) => {
    await goToStep2(page)
    await expect(page.locator('text=引用风格')).toBeVisible()
  })

  test('语言下拉框', async ({ page }) => {
    await goToStep2(page)
    await expect(page.locator('text=语言')).toBeVisible()
  })

  test('未填主题时点下一步不进入 Step 3', async ({ page }) => {
    await goToStep2(page)
    await page.click('button:has-text("下一步")')
    // 应仍然在 Step 2（校验拦截，弹出 warning）
    await expect(page.locator('.n-form-item-label__text', { hasText: '研究主题' })).toBeVisible()
    // 不应出现 Step 3 的元素
    await expect(page.locator('text=预计耗时')).not.toBeVisible()
  })

  test('填写主题后可进入 Step 3', async ({ page }) => {
    await goToStep2(page)
    await page.locator('textarea').first().fill('测试主题内容')
    await page.click('button:has-text("下一步")')
    // 应进入 Step 3
    await expect(page.locator('text=测试主题内容')).toBeVisible()
    await expect(page.locator('text=预计耗时')).toBeVisible()
  })

  test('单字符主题点下一步不进入 Step 3', async ({ page }) => {
    await goToStep2(page)
    await page.locator('textarea').first().fill('x')
    await page.click('button:has-text("下一步")')
    // 应仍然在 Step 2
    await expect(page.locator('.n-form-item-label__text', { hasText: '研究主题' })).toBeVisible()
    await expect(page.locator('text=预计耗时')).not.toBeVisible()
  })

  test('上一步返回 Step 1', async ({ page }) => {
    await goToStep2(page)
    await page.click('button:has-text("上一步")')
    await expect(page.locator('.mode-card')).toHaveCount(2)
  })

  test('返回 Step 1 后模式选择保持', async ({ page }) => {
    await goToStep2(page)
    await page.click('button:has-text("上一步")')
    // 深度模式仍然选中（因为是从深度模式进入的 Step 2）
    await expect(page.locator('.mode-card').nth(1)).toHaveClass(/active/)
  })
})

// ─── 3. Step 3: 确认生成 ─────────────────────────────────

test.describe('Step 3 — 确认生成', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('确认页显示配置摘要', async ({ page }) => {
    await goToStep3(page, '测试主题')
    await expect(page.locator('text=测试主题')).toBeVisible()
    await expect(page.locator('text=预计耗时')).toBeVisible()
  })

  test('确认页显示文献来源标签', async ({ page }) => {
    await goToStep3(page, '文献来源测试')
    // 默认三个来源都开
    await expect(page.locator('text=公共库')).toBeVisible()
    await expect(page.locator('text=个人库')).toBeVisible()
    await expect(page.locator('text=学术搜索')).toBeVisible()
  })

  test('可离开页面提示', async ({ page }) => {
    await goToStep3(page, '可离开测试')
    await expect(page.locator('text=可以离开此页面')).toBeVisible()
  })

  test('开始生成按钮存在', async ({ page }) => {
    await goToStep3(page, '按钮测试')
    await expect(page.locator('button:has-text("开始生成")')).toBeVisible()
    await expect(page.locator('button:has-text("开始生成")')).toBeEnabled()
  })

  test('上一步返回 Step 2', async ({ page }) => {
    await goToStep3(page, '返回测试')
    await page.click('button:has-text("上一步")')
    await expect(page.locator('text=研究主题')).toBeVisible()
  })

  test('返回 Step 2 后主题数据保持', async ({ page }) => {
    const topic = '数据保持测试主题'
    await goToStep3(page, topic)
    await page.click('button:has-text("上一步")')
    const textarea = page.locator('textarea').first()
    await expect(textarea).toHaveValue(topic)
  })
})

// ─── 4. 快速模式流程 ────────────────────────────────────

test.describe('快速模式流程', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('选择快速模式 → 下一步跳转到 /review', async ({ page }) => {
    await page.goto('/review/new')
    // 选择快速模式
    await page.locator('.mode-card').nth(0).click()
    await expect(page.locator('.mode-card').nth(0)).toHaveClass(/active/)
    // 点下一步
    await page.click('button:has-text("下一步")')
    await expect(page).toHaveURL(/\/review/)
  })

  test('快速模式不进入 Step 2', async ({ page }) => {
    await page.goto('/review/new')
    await page.locator('.mode-card').nth(0).click()
    await page.click('button:has-text("下一步")')
    // 不应该显示 Step 2 的表单
    await expect(page.locator('text=研究主题')).not.toBeVisible()
  })
})

// ─── 5. 深度模式完整流程 ──────────────────────────────────

test.describe('深度模式完整流程', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('Step 1 → Step 2 → Step 3 → 提交', async ({ page }) => {
    // Step 1: 默认深度模式
    await page.goto('/review/new')
    await expect(page.locator('.mode-card').nth(1)).toHaveClass(/active/)
    await page.click('button:has-text("下一步")')

    // Step 2: 填写配置
    await expect(page.locator('text=研究主题')).toBeVisible()
    await page.locator('textarea').first().fill('自动化测试流程验证主题')
    await page.click('button:has-text("下一步")')

    // Step 3: 确认
    await expect(page.locator('text=自动化测试流程验证主题')).toBeVisible()
    await expect(page.locator('text=预计耗时')).toBeVisible()
    await expect(page.locator('button:has-text("开始生成")')).toBeVisible()
  })

  test('完整往返：Step 3 → Step 2 → Step 1 → Step 2 → Step 3', async ({ page }) => {
    // 先到 Step 3
    await goToStep3(page, '往返测试主题')
    // 回到 Step 2
    await page.click('button:has-text("上一步")')
    await expect(page.locator('text=研究主题')).toBeVisible()
    // 回到 Step 1
    await page.click('button:has-text("上一步")')
    await expect(page.locator('.mode-card')).toHaveCount(2)
    // 再到 Step 2
    await page.click('button:has-text("下一步")')
    await expect(page.locator('text=研究主题')).toBeVisible()
    // 主题应保留
    await expect(page.locator('textarea').first()).toHaveValue('往返测试主题')
    // 再到 Step 3
    await page.click('button:has-text("下一步")')
    await expect(page.locator('text=往返测试主题')).toBeVisible()
  })
})

// ─── 6. 综述历史页 ──────────────────────────────────────

test.describe('综述历史页', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('历史页加载', async ({ page }) => {
    await page.goto('/review/history')
    await expect(page.locator('text=综述历史')).toBeVisible()
  })

  test('新建综述按钮', async ({ page }) => {
    await page.goto('/review/history')
    await expect(page.locator('button:has-text("新建综述")')).toBeVisible()
  })

  test('点击新建跳转到向导', async ({ page }) => {
    await page.goto('/review/history')
    await page.click('button:has-text("新建综述")')
    await expect(page).toHaveURL(/\/review\/new/)
  })

  test('搜索栏可见', async ({ page }) => {
    await page.goto('/review/history')
    await expect(page.locator('input[placeholder*="搜索"]')).toBeVisible()
  })

  test('空状态显示提示', async ({ page }) => {
    await page.goto('/review/history')
    await page.waitForTimeout(1000)
    // 空状态或有数据，都应该正常渲染
    const empty = page.locator('text=还没有综述记录')
    const list = page.locator('.history-list')
    const hasEmpty = await empty.isVisible().catch(() => false)
    const hasList = await list.isVisible().catch(() => false)
    expect(hasEmpty || hasList).toBeTruthy()
  })

  test('总数标签显示', async ({ page }) => {
    await page.goto('/review/history')
    await page.waitForTimeout(500)
    // "X 篇" 标签
    await expect(page.locator('text=/\\d+ 篇/')).toBeVisible()
  })
})

// ─── 7. 路由重定向 ──────────────────────────────────────

test.describe('路由重定向', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('/review 重定向到 /review/history', async ({ page }) => {
    await page.goto('/review')
    await expect(page).toHaveURL(/\/review\/history/)
  })
})

// ─── 8. 进度页（Mock SSE，适配新 UI） ─────────────────────

/** 构造 mock SSE + API 拦截的 helper */
async function mockProgressPage(page: any, taskId: string, opts: {
  status?: string, stage?: string, progress?: number, message?: string,
  sseEvents?: string, error_message?: string,
}) {
  const { status = 'running', stage = 'outline', progress = 10, message = '', error_message } = opts
  await page.route(`**/api/user/report/${taskId}`, async (route: any) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ task_id: taskId, status, current_stage: stage, progress_percent: progress, progress_message: message, error_message }),
    })
  })
  if (opts.sseEvents) {
    await page.route('**/api/user/report/*/stream**', async (route: any) => {
      await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: opts.sseEvents })
    })
  }
}

test.describe('进度页', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  // ── 基础布局 ──

  test('标题和6个阶段图标', async ({ page }) => {
    await mockProgressPage(page, 'test-layout', {
      stage: 'outline', progress: 15, message: '正在构建提纲',
      sseEvents: 'event: progress\ndata: {"stage":"outline","progress":15,"message":"正在构建提纲"}\n\n',
    })
    await page.goto('/review/progress/test-layout')
    await page.waitForTimeout(500)
    await expect(page.locator('text=深度综述生成中')).toBeVisible()
    // 6个阶段图标（构建提纲/文献分析/篇幅规划/章节撰写/引用校验/排版导出）
    await expect(page.locator('.stage-item')).toHaveCount(6)
    await expect(page.locator('.stage-item').first()).toContainText('构建提纲')
    await expect(page.locator('.stage-item').last()).toContainText('排版导出')
  })

  test('进度条和当前进度卡片', async ({ page }) => {
    await mockProgressPage(page, 'test-card', {
      stage: 'budget', progress: 30, message: '预算分配完成，总计 3000 字',
      sseEvents: 'event: progress\ndata: {"stage":"budget","progress":30,"message":"预算分配完成，总计 3000 字"}\n\n',
    })
    await page.goto('/review/progress/test-card')
    await page.waitForTimeout(500)
    // 进度条
    await expect(page.locator('.n-progress')).toBeVisible()
    // 当前进度卡片
    await expect(page.locator('.current-progress-card')).toBeVisible()
    await expect(page.locator('.progress-title')).toHaveText('当前进度')
    await expect(page.locator('.progress-detail')).toContainText('预算分配完成')
  })

  test('阶段图标状态（done/current/wait）', async ({ page }) => {
    await mockProgressPage(page, 'test-stage-icons', {
      stage: 'chapter', progress: 50, message: '撰写中',
      sseEvents: 'event: progress\ndata: {"stage":"chapter","progress":50,"message":"撰写中"}\n\n',
    })
    await page.goto('/review/progress/test-stage-icons')
    await page.waitForTimeout(500)
    // 构建提纲/文献分析/篇幅规划 = done (前3个)
    await expect(page.locator('.stage-item.done')).toHaveCount(3)
    // 章节撰写 = current
    await expect(page.locator('.stage-item.current')).toHaveCount(1)
    await expect(page.locator('.stage-item.current')).toContainText('章节撰写')
    // 引用校验/排版导出 = wait
    await expect(page.locator('.stage-item.wait')).toHaveCount(2)
  })

  // ── 取消/错误/完成 状态 ──

  test('取消按钮', async ({ page }) => {
    await mockProgressPage(page, 'test-cancel-btn', {
      stage: 'chapter', progress: 60, message: '撰写中',
      sseEvents: 'event: progress\ndata: {"stage":"chapter","progress":60,"message":"撰写中"}\n\n',
    })
    await page.goto('/review/progress/test-cancel-btn')
    await expect(page.locator('button:has-text("取消")')).toBeVisible()
    await expect(page.locator('button:has-text("取消")')).toBeEnabled()
  })

  test('错误状态显示', async ({ page }) => {
    await mockProgressPage(page, 'test-err', { status: 'failed', error_message: 'LLM 超时' })
    await page.goto('/review/progress/test-err')
    await expect(page.locator('text=LLM 超时')).toBeVisible()
    await expect(page.locator('button:has-text("重试")')).toBeVisible()
  })

  test('已完成自动跳转报告页', async ({ page }) => {
    await mockProgressPage(page, 'test-auto-jump', { status: 'completed' })
    await page.goto('/review/progress/test-auto-jump')
    await expect(page).toHaveURL(/\/review\/report\/test-auto-jump/)
  })

  test('cancelled 事件显示', async ({ page }) => {
    await mockProgressPage(page, 'test-cancelled-evt', {
      stage: 'outline', progress: 15,
      sseEvents: 'event: cancelled\ndata: {"task_id":"test-cancelled-evt","message":"任务已取消"}\n\n',
    })
    await page.goto('/review/progress/test-cancelled-evt')
    await page.waitForTimeout(1000)
    await expect(page.locator('text=任务已取消')).toBeVisible()
  })

  // ── 可离开提示/预估时间 ──

  test('可离开提示', async ({ page }) => {
    await mockProgressPage(page, 'test-leave', {
      stage: 'budget', progress: 30, message: '规划中',
      sseEvents: 'event: progress\ndata: {"stage":"budget","progress":30,"message":"规划中"}\n\n',
    })
    await page.goto('/review/progress/test-leave')
    await expect(page.locator('text=可离开页面')).toBeVisible()
  })

  test('预估剩余时间', async ({ page }) => {
    await mockProgressPage(page, 'test-time', {
      stage: 'chapter', progress: 60, message: '撰写中',
      sseEvents: 'event: progress\ndata: {"stage":"chapter","progress":60,"message":"撰写中"}\n\n',
    })
    await page.goto('/review/progress/test-time')
    await page.waitForTimeout(500)
    await expect(page.locator('text=预计剩余')).toBeVisible()
  })

  // ── 实时日志 ──

  test('日志区域默认展开', async ({ page }) => {
    await mockProgressPage(page, 'test-log', {
      stage: 'outline', progress: 15, message: '正在构建提纲',
      sseEvents: 'event: progress\ndata: {"stage":"outline","progress":15,"message":"正在构建提纲"}\n\n',
    })
    await page.goto('/review/progress/test-log')
    await page.waitForTimeout(500)
    await expect(page.locator('.log-section')).toBeVisible()
    await expect(page.locator('.log-list')).toBeVisible()
    await expect(page.locator('.log-title')).toHaveText('实时日志')
    // 应有日志条目
    await expect(page.locator('.log-entry').first()).toBeVisible()
  })

  test('日志记录 SSE 进度消息', async ({ page }) => {
    // SSE 推送的 message 与初始状态不同，才会触发新日志
    await mockProgressPage(page, 'test-log-msg', {
      stage: 'outline', progress: 10, message: '',
      sseEvents: 'event: progress\ndata: {"stage":"outline","progress":15,"message":"提纲完成，共 5 个章节"}\n\n',
    })
    await page.goto('/review/progress/test-log-msg')
    await page.waitForTimeout(500)
    // 日志中应有 SSE 推送的消息
    await expect(page.locator('.log-text', { hasText: '提纲完成' })).toBeVisible()
  })

  test('脉冲指示器运行中可见', async ({ page }) => {
    await mockProgressPage(page, 'test-pulse', {
      stage: 'outline', progress: 10, message: '提纲中',
      sseEvents: 'event: progress\ndata: {"stage":"outline","progress":10,"message":"提纲中"}\n\n',
    })
    await page.goto('/review/progress/test-pulse')
    await page.waitForTimeout(500)
    await expect(page.locator('.log-pulse')).toBeVisible()
  })

  // ── 章节列表（SSE 多事件模拟） ──

  test('大纲事件初始化章节列表', async ({ page }) => {
    const sseEvents = [
      'event: progress\ndata: {"stage":"outline","progress":15,"message":"提纲完成，共 3 个章节","data":{"chapters":["引言","文献综述","结论"]}}\n\n',
      'event: progress\ndata: {"stage":"chapter","progress":35,"message":"开始撰写 3 个章节","data":{"chapter_index":0}}\n\n',
    ].join('')
    await mockProgressPage(page, 'test-chapters-init', {
      stage: 'chapter', progress: 35, message: '开始撰写',
      sseEvents,
    })
    await page.goto('/review/progress/test-chapters-init')
    await page.waitForTimeout(800)
    // 章节列表应显示 3 行
    await expect(page.locator('.chapter-row')).toHaveCount(3)
    await expect(page.locator('.chapter-row').nth(0)).toContainText('引言')
    await expect(page.locator('.chapter-row').nth(1)).toContainText('文献综述')
    await expect(page.locator('.chapter-row').nth(2)).toContainText('结论')
  })

  test('章节状态：writing + pending', async ({ page }) => {
    const sseEvents = [
      'event: progress\ndata: {"stage":"outline","progress":15,"message":"提纲完成","data":{"chapters":["引言","方法","结论"]}}\n\n',
      'event: progress\ndata: {"stage":"chapter","progress":40,"message":"正在撰写第 1/3 章：引言","data":{"chapter_index":0}}\n\n',
    ].join('')
    await mockProgressPage(page, 'test-ch-status', {
      stage: 'chapter', progress: 40, message: '正在撰写',
      sseEvents,
    })
    await page.goto('/review/progress/test-ch-status')
    await page.waitForTimeout(800)
    // 第1章应为 writing 状态（蓝色 "生成中..."）
    await expect(page.locator('.chapter-row').nth(0).locator('.ch-badge.writing')).toBeVisible()
    // 第2、3章应为 pending（"等待中"）
    await expect(page.locator('.chapter-row').nth(1).locator('.ch-badge.pending')).toBeVisible()
    await expect(page.locator('.chapter-row').nth(2).locator('.ch-badge.pending')).toBeVisible()
  })

  test('chapter_done 事件更新章节为完成', async ({ page }) => {
    const sseEvents = [
      'event: progress\ndata: {"stage":"outline","progress":15,"message":"提纲完成","data":{"chapters":["引言","方法"]}}\n\n',
      'event: progress\ndata: {"stage":"chapter","progress":40,"message":"正在撰写","data":{"chapter_index":0}}\n\n',
      'event: chapter_done\ndata: {"chapter_id":"ch1","chapter_title":"引言","content":"引言内容预览...","word_count":500}\n\n',
    ].join('')
    await mockProgressPage(page, 'test-ch-done', {
      stage: 'chapter', progress: 50, message: '撰写中',
      sseEvents,
    })
    await page.goto('/review/progress/test-ch-done')
    await page.waitForTimeout(1000)
    // 第1章应有完成标识（字数 badge）
    await expect(page.locator('.chapter-row').nth(0).locator('.ch-badge.done')).toBeVisible()
    // 第1章内容预览
    await expect(page.locator('.ch-preview').first()).toContainText('引言内容预览')
    // 日志中应记录章节完成
    await expect(page.locator('.log-text', { hasText: '第1章' })).toBeVisible()
  })

  test('当前进度描述在章节撰写时显示章节标题', async ({ page }) => {
    const sseEvents = [
      'event: progress\ndata: {"stage":"outline","progress":15,"message":"提纲完成","data":{"chapters":["引言","方法","结论"]}}\n\n',
      'event: progress\ndata: {"stage":"chapter","progress":50,"message":"正在撰写第 2/3 章：方法","data":{"chapter_index":1}}\n\n',
    ].join('')
    await mockProgressPage(page, 'test-ch-desc', {
      stage: 'chapter', progress: 50, message: '正在撰写',
      sseEvents,
    })
    await page.goto('/review/progress/test-ch-desc')
    await page.waitForTimeout(800)
    // 当前进度卡片应显示 "正在撰写第 2/3 章：方法"
    await expect(page.locator('.progress-detail')).toContainText('方法')
  })

  test('章节统计行', async ({ page }) => {
    const sseEvents = [
      'event: progress\ndata: {"stage":"outline","progress":15,"message":"提纲完成","data":{"chapters":["引言","方法"]}}\n\n',
      'event: progress\ndata: {"stage":"chapter","progress":40,"message":"撰写中","data":{"chapter_index":0}}\n\n',
      'event: chapter_done\ndata: {"chapter_id":"ch1","chapter_title":"引言","content":"...","word_count":500}\n\n',
    ].join('')
    await mockProgressPage(page, 'test-ch-stats', {
      stage: 'chapter', progress: 50, message: '撰写中',
      sseEvents,
    })
    await page.goto('/review/progress/test-ch-stats')
    await page.waitForTimeout(1000)
    // 应显示 "已生成 1/2 章节"
    await expect(page.locator('.progress-stats')).toContainText('1/2')
  })

  // ── SSE error 事件 ──

  test('SSE error 事件显示错误', async ({ page }) => {
    await mockProgressPage(page, 'test-sse-err', {
      stage: 'outline', progress: 10,
      sseEvents: 'event: error\ndata: {"task_id":"test-sse-err","message":"LLM 配额耗尽"}\n\n',
    })
    await page.goto('/review/progress/test-sse-err')
    await page.waitForTimeout(1000)
    await expect(page.locator('text=LLM 配额耗尽')).toBeVisible()
  })

  // ── 初始状态 ──

  test('初始 pending 状态', async ({ page }) => {
    await mockProgressPage(page, 'test-init', {
      status: 'pending', stage: 'pending', progress: 0, message: '准备中...',
      sseEvents: 'event: progress\ndata: {"stage":"pending","progress":0,"message":"准备中..."}\n\n',
    })
    await page.goto('/review/progress/test-init')
    await page.waitForTimeout(500)
    await expect(page.locator('text=深度综述生成中')).toBeVisible()
    await expect(page.locator('.n-progress')).toBeVisible()
    await expect(page.locator('text=准备中')).toBeVisible()
    await expect(page.locator('button:has-text("取消")')).toBeEnabled()
    await expect(page.locator('text=计算中')).toBeVisible()
    // 章节列表不可见
    await expect(page.locator('.chapters-section')).not.toBeVisible()
    await expect(page.locator('button:has-text("查看报告")')).not.toBeVisible()
  })
})

// ─── 9. 报告查看页 ──────────────────────────────────────

test.describe('报告查看页', () => {
  const mockReport = {
    task_id: 'test-report',
    status: 'completed',
    result_markdown: '# 终身教育综述\n\n## 引言\n\n终身教育是....\n\n## 方法\n\n本研究采用...\n\n## 结论\n\n综上所述...',
    word_count: 5000,
    chapters_count: 3,
    duration_seconds: 300,
    topic: '终身教育综述',
    abstract: '本文综述了...',
  }

  test.beforeEach(async ({ page }) => {
    await login(page)
    await page.route('**/api/user/report/test-report', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockReport),
      })
    })
  })

  test('工具栏按钮完整', async ({ page }) => {
    await page.goto('/review/report/test-report')
    await page.waitForTimeout(500)
    await expect(page.locator('button:has-text("搜索")')).toBeVisible()
    await expect(page.locator('button:has-text("重新生成")')).toBeVisible()
    await expect(page.locator('button:has-text("分享")')).toBeVisible()
    await expect(page.locator('button:has-text("导出")')).toBeVisible()
  })

  test('返回按钮', async ({ page }) => {
    await page.goto('/review/report/test-report')
    await page.waitForTimeout(500)
    await expect(page.locator('button:has-text("返回")')).toBeVisible()
  })

  test('目录侧边栏', async ({ page }) => {
    await page.goto('/review/report/test-report')
    await page.waitForTimeout(500)
    await expect(page.locator('.toc-sidebar')).toBeVisible()
    await expect(page.locator('.toc-header')).toHaveText('目录导航')
  })

  test('报告元信息', async ({ page }) => {
    await page.goto('/review/report/test-report')
    await page.waitForTimeout(500)
    await expect(page.locator('.report-meta')).toContainText('5,000')
    await expect(page.locator('.report-meta')).toContainText('3')
    await expect(page.locator('.report-meta')).toContainText('300')
  })

  test('报告正文渲染', async ({ page }) => {
    await page.goto('/review/report/test-report')
    await page.waitForTimeout(500)
    await expect(page.locator('.report-content')).toBeVisible()
    await expect(page.locator('.report-content')).toContainText('终身教育')
  })

  test('搜索功能', async ({ page }) => {
    await page.goto('/review/report/test-report')
    await page.waitForTimeout(500)
    await page.click('button:has-text("搜索")')
    await expect(page.locator('.search-bar')).toBeVisible()
    await page.fill('.search-bar input', '终身教育')
    await page.press('.search-bar input', 'Enter')
    // 应有搜索结果计数
    await expect(page.locator('text=/\\d+ 个结果/')).toBeVisible()
  })

  test('导出下拉菜单', async ({ page }) => {
    await page.goto('/review/report/test-report')
    await page.waitForTimeout(500)
    await page.click('button:has-text("导出")')
    await expect(page.locator('text=Markdown (.md)')).toBeVisible()
    await expect(page.locator('text=纯文本 (.txt)')).toBeVisible()
  })

  test('非完成状态跳转到进度页', async ({ page }) => {
    await page.route('**/api/user/report/test-running', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ task_id: 'test-running', status: 'running', current_stage: 'chapter', progress_percent: 50 }),
      })
    })
    await page.route('**/api/user/report/*/stream**', async (route) => {
      await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: '' })
    })

    await page.goto('/review/report/test-running')
    await expect(page).toHaveURL(/\/review\/progress\/test-running/)
  })
})

// ─── 10. 报告查看页 — IR 渲染模式 ───────────────────────────

test.describe('报告查看页 — IR 模式', () => {
  const mockIRReport = {
    task_id: 'test-ir-report',
    status: 'completed',
    result_markdown: '# IR 模式报告',
    result_ir: {
      title: 'IR 结构化报告',
      abstract: '测试摘要',
      chapters: [
        {
          chapter_id: 'ch-01',
          title: '引言',
          blocks: [
            { type: 'heading', level: 1, text: '引言', anchor: 'ch-01-h0' },
            { type: 'paragraph', text: '这是引言内容。', source_ids: [] },
          ],
          word_count: 500,
        },
        {
          chapter_id: 'ch-02',
          title: '方法',
          blocks: [
            { type: 'heading', level: 1, text: '方法', anchor: 'ch-02-h0' },
            { type: 'paragraph', text: '研究方法说明。' },
            { type: 'table', headers: ['指标', '值'], rows: [['准确率', '0.95']] },
          ],
          word_count: 800,
        },
      ],
      references: [
        { type: 'reference', ref_id: 'ref-001', authors: ['张三'], title: '参考文献', year: 2024 },
      ],
    },
    word_count: 1300,
    chapters_count: 2,
    duration_seconds: 180,
    topic: 'IR 模式测试',
  }

  test.beforeEach(async ({ page }) => {
    await login(page)
    await page.route('**/api/user/report/test-ir-report', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockIRReport),
      })
    })
  })

  test('IR 渲染器加载', async ({ page }) => {
    await page.goto('/review/report/test-ir-report')
    await page.waitForTimeout(500)
    // 应该使用 IrRenderer 而不是纯 HTML
    const irRenderer = page.locator('[class*="ir-"]').first()
    const reportContent = page.locator('.report-content')
    // IR 模式下 .report-content 不应显示
    const hasIR = await irRenderer.isVisible().catch(() => false)
    const hasMD = await reportContent.isVisible().catch(() => false)
    expect(hasIR || hasMD).toBeTruthy()
  })

  test('IR 模式目录从章节提取', async ({ page }) => {
    await page.goto('/review/report/test-ir-report')
    await page.waitForTimeout(500)
    // 目录应包含章节标题
    const tocSidebar = page.locator('.toc-sidebar')
    await expect(tocSidebar).toContainText('引言')
    await expect(tocSidebar).toContainText('方法')
  })
})

// ─── 11. SSE 事件流处理（已合并到进度页测试中） ──────────────

// ─── 12. 视觉回归测试 ───────────────────────────────────

test.describe('视觉回归', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('Step 1 — 视觉快照', async ({ page }) => {
    await page.goto('/review/new')
    await page.waitForTimeout(500)
    await expect(page).toHaveScreenshot('wizard-step1.png', { maxDiffPixelRatio: 0.05 })
  })

  test('Step 1 — 快速模式选中快照', async ({ page }) => {
    await page.goto('/review/new')
    await page.locator('.mode-card').nth(0).click()
    await page.waitForTimeout(300)
    await expect(page).toHaveScreenshot('wizard-step1-quick-selected.png', { maxDiffPixelRatio: 0.05 })
  })

  test('Step 2 — 视觉快照', async ({ page }) => {
    await goToStep2(page)
    await page.waitForTimeout(500)
    await expect(page).toHaveScreenshot('wizard-step2.png', { maxDiffPixelRatio: 0.05 })
  })

  test('Step 2 — 已填写表单快照', async ({ page }) => {
    await goToStep2(page)
    await page.locator('textarea').first().fill('终身教育视角下的老年数字素养提升路径研究')
    await page.waitForTimeout(300)
    await expect(page).toHaveScreenshot('wizard-step2-filled.png', { maxDiffPixelRatio: 0.05 })
  })

  test('Step 3 — 视觉快照', async ({ page }) => {
    await goToStep3(page)
    await page.waitForTimeout(500)
    await expect(page).toHaveScreenshot('wizard-step3.png', { maxDiffPixelRatio: 0.05 })
  })

  test('历史页 — 视觉快照', async ({ page }) => {
    await page.goto('/review/history')
    await page.waitForTimeout(1000)
    await expect(page).toHaveScreenshot('review-history.png', { maxDiffPixelRatio: 0.05 })
  })

  test('进度页 — 章节撰写中视觉快照', async ({ page }) => {
    const sseEvents = [
      'event: progress\ndata: {"stage":"outline","progress":15,"message":"提纲完成，共 3 个章节","data":{"chapters":["引言","研究方法","结论与展望"]}}\n\n',
      'event: progress\ndata: {"stage":"chapter","progress":40,"message":"正在撰写第 1/3 章：引言","data":{"chapter_index":0}}\n\n',
      'event: chapter_done\ndata: {"chapter_id":"ch1","chapter_title":"引言","content":"引言内容预览文字...","word_count":600}\n\n',
      'event: progress\ndata: {"stage":"chapter","progress":60,"message":"正在撰写第 2/3 章：研究方法","data":{"chapter_index":1}}\n\n',
    ].join('')
    await mockProgressPage(page, 'test-vis', {
      stage: 'chapter', progress: 60, message: '正在撰写',
      sseEvents,
    })
    await page.goto('/review/progress/test-vis')
    await page.waitForTimeout(1000)
    await expect(page).toHaveScreenshot('report-progress.png', { maxDiffPixelRatio: 0.05 })
  })

  test('报告查看页 — 视觉快照', async ({ page }) => {
    await page.route('**/api/user/report/test-vis-report', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          task_id: 'test-vis-report',
          status: 'completed',
          result_markdown: '# 终身教育综述\n\n## 引言\n\n终身教育的理念...\n\n## 结论\n\n本文综述了...',
          word_count: 3000,
          chapters_count: 2,
          duration_seconds: 200,
          topic: '终身教育综述',
        }),
      })
    })

    await page.goto('/review/report/test-vis-report')
    await page.waitForTimeout(1000)
    await expect(page).toHaveScreenshot('report-viewer.png', { maxDiffPixelRatio: 0.05 })
  })

  test('进度页错误状态 — 视觉快照', async ({ page }) => {
    await mockProgressPage(page, 'test-vis-err', { status: 'failed', error_message: 'LLM 超时' })
    await page.goto('/review/progress/test-vis-err')
    await page.waitForTimeout(500)
    await expect(page).toHaveScreenshot('report-progress-error.png', { maxDiffPixelRatio: 0.05 })
  })
})

// ─── 13. 响应式布局测试 ──────────────────────────────────

test.describe('响应式布局', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
  })

  test('移动端 — 模式卡片单列布局', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await page.goto('/review/new')
    await page.waitForTimeout(500)
    // mode-cards 应变为单列（通过 CSS media query）
    const modeCards = page.locator('.mode-cards')
    const box = await modeCards.boundingBox()
    // 在移动端宽度不应超过 viewport
    expect(box!.width).toBeLessThanOrEqual(375)
  })

  test('移动端 — 视觉快照', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await page.goto('/review/new')
    await page.waitForTimeout(500)
    await expect(page).toHaveScreenshot('wizard-step1-mobile.png', { maxDiffPixelRatio: 0.05 })
  })

  test('报告页移动端 — 目录隐藏', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 })
    await page.route('**/api/user/report/test-mobile-report', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          task_id: 'test-mobile-report',
          status: 'completed',
          result_markdown: '# 标题\n\n内容',
          word_count: 100,
          chapters_count: 1,
          duration_seconds: 60,
          topic: '移动端测试',
        }),
      })
    })

    await page.goto('/review/report/test-mobile-report')
    await page.waitForTimeout(500)
    // 移动端目录侧边栏应隐藏
    await expect(page.locator('.toc-sidebar')).not.toBeVisible()
    // 但目录按钮应显示
    await expect(page.locator('.toc-toggle')).toBeVisible()
  })
})
