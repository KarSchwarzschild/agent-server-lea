import { test, expect } from '@playwright/test'
import { execFile } from 'node:child_process'
import { mkdtemp, readFile, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)

type EvalScenePayload = {
  case_id: string
  scene_index?: number
  scene_type: 'interactive' | 'pbl' | 'whiteboard'
  title: string
  content: Record<string, any>
}

async function openEval(page: any) {
  await page.route('**/api/user/classroom/**', route => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ messages: [] }),
    })
  })
  await page.addInitScript(() => localStorage.clear())
  await page.goto('/_eval/classroom-scene-render')
  await expect.poll(() => page.evaluate(() => (window as any).__evalReady === true)).toBe(true)
}

async function setScene(page: any, payload: EvalScenePayload) {
  await page.evaluate(scene => (window as any).__setScene(scene), payload)
  await expect(page.locator('[data-eval-scene-root="true"]')).toHaveAttribute('data-eval-case-id', payload.case_id)
}

test('dev eval scene route renders matching widget and supports scoring', async ({ page }) => {
  await openEval(page)
  await setScene(page, {
    case_id: 'matching_case',
    scene_type: 'interactive',
    title: '概念匹配',
    content: {
      kind: 'interactive_widget',
      widget_type: 'matching',
      prompt: '匹配概念和解释。',
      pairs: [
        { id: 'a', left: '形成性评价', right: '学习过程中持续收集证据' },
        { id: 'b', left: '总结性评价', right: '阶段末判断学习结果' },
      ],
    },
  })

  await expect(page.getByText('互动匹配')).toBeVisible()
  await page.locator('[data-test="matching-left-a"]').click()
  await page.locator('[data-test="matching-right-a"]').click()
  await page.locator('[data-test="matching-left-b"]').click()
  await page.locator('[data-test="matching-right-b"]').click()
  await page.locator('[data-test="matching-submit"]').click()
  await expect(page.getByText('2 / 2')).toBeVisible()

  const state = await page.evaluate(() => (window as any).__evalSceneState())
  expect(state.visible_text).toContain('全部匹配正确')
  expect(state.width).toBeGreaterThan(900)
  expect(state.height).toBeGreaterThan(500)
})

test('dev eval scene route renders ordering widget and supports scoring', async ({ page }) => {
  await openEval(page)
  await setScene(page, {
    case_id: 'ordering_case',
    scene_type: 'interactive',
    title: '流程排序',
    content: {
      kind: 'interactive_widget',
      widget_type: 'ordering',
      prompt: '把研究流程排成正确顺序。',
      items: [
        { id: 'one', text: '提出问题', order: 1 },
        { id: 'two', text: '收集资料', order: 2 },
        { id: 'three', text: '形成结论', order: 3 },
      ],
    },
  })

  await expect(page.getByText('互动排序')).toBeVisible()
  await page.locator('[data-test="ordering-submit"]').click()
  await expect(page.getByText('3 / 3')).toBeVisible()

  const state = await page.evaluate(() => (window as any).__evalSceneState())
  expect(state.visible_text).toContain('顺序完全正确')
})

test('dev eval scene route renders html widget through sandbox player', async ({ page }) => {
  await openEval(page)
  await setScene(page, {
    case_id: 'html_case',
    scene_type: 'interactive',
    title: 'HTML 互动',
    content: {
      kind: 'interactive_widget',
      widget_type: 'html',
      prompt: '观察计数器。',
      html: '<!doctype html><html><body><button id="inc">点击</button><script>document.body.dataset.ready="yes"</script></body></html>',
    },
  })

  await expect(page.getByRole('heading', { name: 'HTML 互动' })).toBeVisible()
  await expect(page.locator('iframe')).toHaveCount(1)

  const state = await page.evaluate(() => (window as any).__evalSceneState())
  expect(state.iframe_count).toBe(1)
})

test('dev eval scene route renders category sort and reflection widgets', async ({ page }) => {
  await openEval(page)
  await setScene(page, {
    case_id: 'category_case',
    scene_type: 'interactive',
    title: '分类练习',
    content: {
      kind: 'interactive_widget',
      widget_type: 'category_sort',
      prompt: '把物品放入正确类别。',
      categories: [
        { id: 'recyclable', label: '可回收物' },
        { id: 'kitchen', label: '厨余垃圾' },
      ],
      items: [
        { id: 'paper', text: '废纸', category_id: 'recyclable' },
        { id: 'leaf', text: '菜叶', category_id: 'kitchen' },
      ],
    },
  })
  await page.locator('[data-test="category-item-paper"]').click()
  await page.locator('[data-test="category-target-recyclable"]').click()
  await page.locator('[data-test="category-item-leaf"]').click()
  await page.locator('[data-test="category-target-kitchen"]').click()
  await page.locator('[data-test="category-submit"]').click()
  await expect(page.getByText('分类全部正确')).toBeVisible()

  await setScene(page, {
    case_id: 'reflection_case',
    scene_type: 'interactive',
    title: '学习反思',
    content: {
      kind: 'interactive_widget',
      widget_type: 'reflection',
      prompt: '写下你的理解。',
      questions: ['我学到了什么？', '还有什么问题？'],
      min_length: 2,
    },
  })
  await page.locator('[data-test="reflection-answer-0"]').fill('理解概念')
  await page.locator('[data-test="reflection-answer-1"]').fill('继续练习')
  await page.locator('[data-test="reflection-submit"]').click()
  await expect(page.getByText('反思已记录')).toBeVisible()
})

test('dev eval scene route renders pbl skeleton view', async ({ page }) => {
  await openEval(page)
  await setScene(page, {
    case_id: 'pbl_case',
    scene_type: 'pbl',
    title: '社区学习项目',
    content: {
      kind: 'pbl_project_skeleton',
      title: '社区学习项目',
      description: '围绕社区问题完成一次调研与方案设计。',
      target_skills: ['调研', '协作'],
      roles: [
        { id: 'researcher', name: '调研员', responsibility: '收集访谈材料' },
      ],
      milestones: [
        { id: 'm1', title: '定义问题', description: '确认核心问题', assessment_criterion: '问题表述清晰' },
        { id: 'm2', title: '提交方案', description: '形成行动方案', assessment_criterion: '方案可执行' },
      ],
      rubric: [
        { criterion: '证据质量', level: '优秀', description: '引用充分且相关' },
      ],
    },
  })

  await expect(page.getByText('项目式学习')).toBeVisible()
  await expect(page.getByText('定义问题')).toBeVisible()
  await expect(page.getByText('证据质量')).toBeVisible()

  const state = await page.evaluate(() => (window as any).__evalSceneState())
  expect(state.visible_text).toContain('社区学习项目')
  expect(state.visible_text).toContain('项目角色')
})

test('dev eval scene route renders pbl workspace and whiteboard view', async ({ page }) => {
  await openEval(page)
  await setScene(page, {
    case_id: 'pbl_workspace_case',
    scene_type: 'pbl',
    title: '校园节水项目',
    content: {
      kind: 'pbl_workspace',
      title: '校园节水项目',
      description: '设计一个校园节水方案',
      roles: [{ id: 'researcher', name: '调研员', responsibility: '收集现状资料' }],
      resources: [{ id: 'water_bill', title: '用水账单', summary: '近三个月用水量' }],
      tasks: [{ id: 'task_1', title: '找出浪费点', status: 'todo' }],
      submission_prompts: ['提交节水方案摘要'],
    },
  })
  await expect(page.getByText('用水账单')).toBeVisible()
  await page.locator('[data-test="pbl-role-researcher"]').click()
  await expect(page.getByText('当前角色：调研员')).toBeVisible()

  await setScene(page, {
    case_id: 'whiteboard_case',
    scene_type: 'whiteboard',
    title: '光合作用白板',
    content: {
      kind: 'whiteboard',
      title: '光合作用白板',
      prompt: '用白板推演光合作用',
      viewport: { width: 1000, height: 562 },
      elements: [
        { id: 'e1', type: 'text', text: '光合作用', x: 80, y: 60, width: 180, height: 64 },
        { id: 'e2', type: 'sticky', text: '叶绿体', x: 120, y: 160, width: 160, height: 64 },
      ],
    },
  })
  await expect(page.locator('[data-test="whiteboard-element-e1"]')).toContainText('光合作用')
  await expect(page.locator('[data-test="whiteboard-element-e2"]')).toContainText('叶绿体')
})

test('scene capture script writes screenshot manifest artifacts', async ({}, testInfo) => {
  const baseURL = String(testInfo.project.use.baseURL || 'http://localhost:9002')
  const runDir = await mkdtemp(path.join(tmpdir(), 'classroom-scene-capture-'))
  const inputPath = path.join(runDir, 'scenes.json')
  const outputDir = path.join(runDir, 'screenshots')
  await writeFile(inputPath, JSON.stringify([
    {
      case_id: 'matching_capture',
      scene_index: 1,
      scene_type: 'interactive',
      title: '概念匹配截图',
      content: {
        kind: 'interactive_widget',
        widget_type: 'matching',
        prompt: '匹配概念和解释。',
        pairs: [
          { id: 'a', left: '形成性评价', right: '过程证据' },
          { id: 'b', left: '总结性评价', right: '阶段结果' },
        ],
      },
    },
    {
      case_id: 'whiteboard_capture',
      scene_index: 3,
      scene_type: 'whiteboard',
      title: '光合作用白板截图',
      content: {
        kind: 'whiteboard',
        title: '光合作用白板截图',
        prompt: '用白板推演光合作用',
        viewport: { width: 1000, height: 562 },
        elements: [
          { id: 'e1', type: 'text', text: '光合作用', x: 80, y: 60, width: 180, height: 64 },
          { id: 'e2', type: 'sticky', text: '叶绿体', x: 120, y: 160, width: 160, height: 64 },
        ],
      },
    },
    {
      case_id: 'pbl_capture',
      scene_index: 2,
      scene_type: 'pbl',
      title: '社区学习项目截图',
      content: {
        kind: 'pbl_project_skeleton',
        title: '社区学习项目截图',
        description: '围绕社区问题完成一次调研与方案设计。',
        roles: [{ id: 'researcher', name: '调研员', responsibility: '收集访谈材料' }],
        milestones: [
          { id: 'm1', title: '定义问题', assessment_criterion: '问题表述清晰' },
          { id: 'm2', title: '提交方案', assessment_criterion: '方案可执行' },
        ],
        rubric: [{ criterion: '证据质量', level: '优秀', description: '引用充分且相关' }],
      },
    },
  ], null, 2), 'utf8')

  const { stdout } = await execFileAsync('node', [
    'scripts/classroom-scene-capture.mjs',
    '--base-url',
    baseURL,
    '--input',
    inputPath,
    '--output-dir',
    outputDir,
  ], { cwd: process.cwd(), timeout: 120000 })

  const manifest = JSON.parse(stdout)
  expect(manifest.status).toBe('ok')
  expect(manifest.checked_url).toContain('/_eval/classroom-scene-render')
  expect(manifest.screenshots).toHaveLength(3)
  expect(manifest.screenshots.map((item: any) => item.case_id).sort()).toEqual([
    'matching_capture',
    'pbl_capture',
    'whiteboard_capture',
  ])
  for (const item of manifest.screenshots) {
    expect(item.non_blank).toBe(true)
    expect(item.eval_ready).toBe(true)
    expect(item.sentinel_matched).toBe(true)
    expect(item.pixel_stats.non_white_ratio).toBeGreaterThan(0.0005)
    expect(item.visible_text.length).toBeGreaterThan(20)
    const file = await stat(item.path)
    expect(file.size).toBeGreaterThan(1000)
    const bytes = await readFile(item.path)
    expect(bytes.subarray(0, 8).toString('hex')).toBe('89504e470d0a1a0a')
  }
})
