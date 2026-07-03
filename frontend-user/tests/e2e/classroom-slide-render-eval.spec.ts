import { test, expect } from '@playwright/test'

test('dev eval slide route renders injected slide without auth or backend calls', async ({ page }, testInfo) => {
  const apiRequests: string[] = []
  const externalRequests: string[] = []
  const consoleErrors: string[] = []
  const pageErrors: string[] = []
  const baseURL = String(testInfo.project.use.baseURL || 'http://localhost:9002')
  const frontendOrigin = new URL(baseURL).origin

  page.on('request', req => {
    const url = req.url()
    if (url.includes('/api/')) apiRequests.push(url)
    if (!url.startsWith(frontendOrigin) && !url.startsWith('data:') && !url.startsWith('blob:')) {
      externalRequests.push(url)
    }
  })
  page.on('console', msg => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })
  page.on('pageerror', err => pageErrors.push(err.message))

  await page.addInitScript(() => localStorage.clear())
  await page.goto('/_eval/classroom-slide-render')
  await expect.poll(() => page.evaluate(() => (window as any).__evalReady === true)).toBe(true)

  await page.evaluate(() => (window as any).__setSlide({
    case_id: 'eval_case',
    scene_index: 1,
    slide: {
      background: { type: 'solid', color: '#ffffff' },
      elements: [
        {
          id: 'title',
          type: 'text',
          left: 80,
          top: 80,
          width: 800,
          height: 80,
          content: '<p>求根公式</p>',
          defaultFontName: 'Microsoft YaHei',
          defaultColor: '#111111',
        },
      ],
    },
  }))

  const root = page.locator('[data-eval-slide-root="true"]')
  await expect(root).toHaveAttribute('data-eval-case-id', 'eval_case')
  await expect(root).toHaveAttribute('data-eval-scene-index', '1')
  await expect(root).toContainText('求根公式')
  const state = await page.evaluate(() => (window as any).__evalSlideState())
  expect(state.visible_element_count).toBe(1)
  expect(state.width).toBeGreaterThan(900)
  expect(state.height).toBeGreaterThan(500)
  const box = await root.boundingBox()
  expect(box?.width).toBeGreaterThan(900)
  expect(box?.height).toBeGreaterThan(500)
  expect(apiRequests).toEqual([])
  expect(externalRequests).toEqual([])
  expect(consoleErrors).toEqual([])
  expect(pageErrors).toEqual([])
})

test('dev eval slide state reports clipped canvas elements', async ({ page }) => {
  await page.goto('/_eval/classroom-slide-render')
  await expect.poll(() => page.evaluate(() => (window as any).__evalReady === true)).toBe(true)

  await page.evaluate(() => (window as any).__setSlide({
    case_id: 'clipped_case',
    scene_index: 2,
    slide: {
      background: { type: 'solid', color: '#ffffff' },
      elements: [
        {
          id: 'too_tall_table',
          type: 'table',
          left: 80,
          top: 120,
          width: 360,
          height: 60,
          data: [
            [{ id: 'h1', text: '假名', rowspan: 1, colspan: 1 }, { id: 'h2', text: '罗马音', rowspan: 1, colspan: 1 }],
            [{ id: 'r1c1', text: 'あ', rowspan: 1, colspan: 1 }, { id: 'r1c2', text: 'a', rowspan: 1, colspan: 1 }],
            [{ id: 'r2c1', text: 'い', rowspan: 1, colspan: 1 }, { id: 'r2c2', text: 'i', rowspan: 1, colspan: 1 }],
            [{ id: 'r3c1', text: 'う', rowspan: 1, colspan: 1 }, { id: 'r3c2', text: 'u', rowspan: 1, colspan: 1 }],
          ],
          colWidths: [0.5, 0.5],
          cellMinHeight: 36,
          outline: { style: 'solid', width: 1, color: '#e0e0e0' },
        },
      ],
    },
  }))

  const state = await page.evaluate(() => (window as any).__evalSlideState())
  expect(state.clipped_element_count).toBe(1)
  expect(state.clipped_elements[0].id).toBe('too_tall_table')
})

test('dev eval slide state ignores two pixel table border overflow', async ({ page }) => {
  await page.goto('/_eval/classroom-slide-render')
  await expect.poll(() => page.evaluate(() => (window as any).__evalReady === true)).toBe(true)

  await page.evaluate(() => {
    const root = document.querySelector('[data-eval-slide-root="true"]')
    if (!root) throw new Error('eval root missing')
    const element = document.createElement('div')
    element.className = 'screen-element'
    element.dataset.elementId = 'border_tolerance_table'
    element.dataset.elementType = 'table'
    element.style.position = 'absolute'
    element.style.left = '80px'
    element.style.top = '80px'
    element.style.width = '100px'
    element.style.height = '100px'
    element.style.overflow = 'hidden'
    const child = document.createElement('div')
    child.style.width = '102px'
    child.style.height = '100px'
    element.appendChild(child)
    root.appendChild(element)
  })

  const state = await page.evaluate(() => (window as any).__evalSlideState())
  expect(state.clipped_element_count).toBe(0)
  expect(state.clipped_elements).toEqual([])
})

test('dev eval slide state ignores decorative shape overflow', async ({ page }) => {
  await page.goto('/_eval/classroom-slide-render')
  await expect.poll(() => page.evaluate(() => (window as any).__evalReady === true)).toBe(true)

  await page.evaluate(() => (window as any).__setSlide({
    case_id: 'shape_case',
    scene_index: 3,
    slide: {
      background: { type: 'solid', color: '#ffffff' },
      elements: [
        {
          id: 'divider',
          type: 'shape',
          left: 100,
          top: 410,
          width: 800,
          height: 2,
          path: 'M 0 0 L 1 0 L 1 1 L 0 1 Z',
          viewBox: [1, 1],
          fill: '#cbd5e1',
        },
      ],
    },
  }))

  const state = await page.evaluate(() => (window as any).__evalSlideState())
  expect(state.clipped_element_count).toBe(0)
  expect(state.clipped_elements).toEqual([])
})
