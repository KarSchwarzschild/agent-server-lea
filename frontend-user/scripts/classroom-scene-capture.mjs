import { chromium } from '@playwright/test'
import { mkdir, readFile } from 'node:fs/promises'
import path from 'node:path'
import { inflateSync } from 'node:zlib'

const MAX_DATA_URI_LENGTH = 2_000_000
const DATA_URI_PATTERN = /data:[^"'\s)]*/gi

function arg(name, fallback = '') {
  const idx = process.argv.indexOf(`--${name}`)
  if (idx < 0) return fallback
  const value = process.argv[idx + 1]
  if (!value || value.startsWith('--')) throw new Error(`missing_arg:${name}`)
  return value
}

function safeFilename(value) {
  return String(value).replace(/[^A-Za-z0-9_.-]/g, '_')
}

function sanitizeManifestValue(value) {
  if (typeof value === 'string') {
    const lower = value.toLowerCase()
    if (lower.startsWith('data:')) return '<data-url-redacted>'
    if (lower.startsWith('blob:')) return '<blob-url-redacted>'
    if (lower.includes('data:')) return value.replace(DATA_URI_PATTERN, '<data-url-redacted>')
    if (lower.includes('base64')) return value.replace(/base64[,:=A-Za-z0-9+/_-]*/gi, '<base64-redacted>')
    return value
  }
  if (Array.isArray(value)) return value.map(item => sanitizeManifestValue(item))
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, sanitizeManifestValue(item)]),
    )
  }
  return value
}

function printManifest(payload) {
  console.log(JSON.stringify(sanitizeManifestValue(payload), null, 2))
}

function assertDataUriSize(value, location = 'scene') {
  if (typeof value === 'string') {
    for (const match of value.matchAll(DATA_URI_PATTERN)) {
      if (match[0].length > MAX_DATA_URI_LENGTH) {
        throw new Error(`data_uri_too_large:${location}`)
      }
    }
    return
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertDataUriSize(item, `${location}[${index}]`))
    return
  }
  if (value && typeof value === 'object') {
    for (const [key, item] of Object.entries(value)) {
      assertDataUriSize(item, `${location}.${key}`)
    }
  }
}

function isAllowedRequestUrl(url, allowedOrigin) {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'data:' || parsed.protocol === 'blob:' || parsed.origin === allowedOrigin
  } catch {
    return false
  }
}

function paethPredictor(left, up, upLeft) {
  const p = left + up - upLeft
  const pa = Math.abs(p - left)
  const pb = Math.abs(p - up)
  const pc = Math.abs(p - upLeft)
  if (pa <= pb && pa <= pc) return left
  if (pb <= pc) return up
  return upLeft
}

function analyzePng(buffer) {
  const signature = '89504e470d0a1a0a'
  if (buffer.subarray(0, 8).toString('hex') !== signature) throw new Error('invalid_png_signature')

  let offset = 8
  let width = 0
  let height = 0
  let bitDepth = 0
  let colorType = 0
  const idatChunks = []

  while (offset < buffer.length) {
    const length = buffer.readUInt32BE(offset)
    const type = buffer.toString('ascii', offset + 4, offset + 8)
    const dataStart = offset + 8
    const dataEnd = dataStart + length
    const data = buffer.subarray(dataStart, dataEnd)

    if (type === 'IHDR') {
      width = data.readUInt32BE(0)
      height = data.readUInt32BE(4)
      bitDepth = data[8]
      colorType = data[9]
    } else if (type === 'IDAT') {
      idatChunks.push(data)
    } else if (type === 'IEND') {
      break
    }
    offset = dataEnd + 4
  }

  if (!width || !height || !idatChunks.length) throw new Error('invalid_png_data')
  if (bitDepth !== 8) throw new Error(`unsupported_png_bit_depth:${bitDepth}`)

  const channelsByColorType = { 0: 1, 2: 3, 4: 2, 6: 4 }
  const channels = channelsByColorType[colorType]
  if (!channels) throw new Error(`unsupported_png_color_type:${colorType}`)

  const rowLength = width * channels
  const raw = inflateSync(Buffer.concat(idatChunks))
  let rawOffset = 0
  let previous = Buffer.alloc(rowLength)
  let nonWhite = 0
  const uniqueColors = new Set()
  const totalPixels = width * height

  for (let y = 0; y < height; y += 1) {
    const filter = raw[rawOffset]
    rawOffset += 1
    const row = Buffer.alloc(rowLength)

    for (let x = 0; x < rowLength; x += 1) {
      const current = raw[rawOffset]
      rawOffset += 1
      const left = x >= channels ? row[x - channels] : 0
      const up = previous[x] || 0
      const upLeft = x >= channels ? previous[x - channels] : 0
      let predictor = 0
      if (filter === 1) predictor = left
      else if (filter === 2) predictor = up
      else if (filter === 3) predictor = Math.floor((left + up) / 2)
      else if (filter === 4) predictor = paethPredictor(left, up, upLeft)
      else if (filter !== 0) throw new Error(`unsupported_png_filter:${filter}`)
      row[x] = (current + predictor) & 0xff
    }

    for (let x = 0; x < rowLength; x += channels) {
      let r
      let g
      let b
      let a = 255
      if (colorType === 0) {
        r = g = b = row[x]
      } else if (colorType === 2) {
        r = row[x]
        g = row[x + 1]
        b = row[x + 2]
      } else if (colorType === 4) {
        r = g = b = row[x]
        a = row[x + 1]
      } else {
        r = row[x]
        g = row[x + 1]
        b = row[x + 2]
        a = row[x + 3]
      }

      if (uniqueColors.size < 10000) uniqueColors.add(`${r},${g},${b},${a}`)
      if (a > 0 && (r < 245 || g < 245 || b < 245)) nonWhite += 1
    }
    previous = row
  }

  return {
    width,
    height,
    non_white_ratio: Number((nonWhite / totalPixels).toFixed(6)),
    unique_color_count: uniqueColors.size,
  }
}

const consoleErrors = []
const pageErrors = []
const blockedRequests = []
const failedRequests = []
const screenshots = []
const usedScreenshotPaths = new Set()
let browser
let baseUrl = 'http://localhost:9002'
let checkedUrl = ''

try {
  baseUrl = arg('base-url', 'http://localhost:9002').replace(/\/$/, '')
  const input = arg('input')
  const outputDir = arg('output-dir')
  if (!input || !outputDir) {
    throw new Error('missing_input_or_output_dir')
  }
  const allowedOrigin = new URL(baseUrl).origin
  checkedUrl = `${baseUrl}/_eval/classroom-scene-render`

  const scenes = JSON.parse(await readFile(input, 'utf8'))
  if (!Array.isArray(scenes)) throw new Error('scenes_input_must_be_array')
  if (scenes.length === 0) throw new Error('scenes_input_empty')
  scenes.forEach((item, index) => assertDataUriSize(item, `scenes[${index}]`))

  await mkdir(outputDir, { recursive: true })

  browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({
    viewport: { width: 1200, height: 760 },
    storageState: { cookies: [], origins: [] },
  })
  const page = await context.newPage()

  page.on('console', msg => {
    if (msg.type() === 'error') consoleErrors.push(msg.text())
  })
  page.on('pageerror', err => pageErrors.push(err.message))
  page.on('requestfailed', req => {
    failedRequests.push(`${req.url()}: ${req.failure()?.errorText || 'failed'}`)
  })

  await page.route('**/*', route => {
    const url = route.request().url()
    if (url.includes('/api/user/classroom/')) {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ messages: [] }),
      })
    }
    if (isAllowedRequestUrl(url, allowedOrigin)) {
      return route.continue()
    }
    blockedRequests.push(url)
    return route.abort()
  })

  await page.goto(checkedUrl, { waitUntil: 'networkidle', timeout: 15000 })
  const ready = await page.evaluate(() => window.__evalReady === true)
  if (!ready) throw new Error('capture_ready_timeout')

  for (const item of scenes) {
    await page.evaluate(payload => window.__setScene(payload), item)
    await page.waitForTimeout(1000)

    const state = await page.evaluate(() => window.__evalSceneState())
    const root = page.locator('[data-eval-scene-root="true"]')
    await root.waitFor({ timeout: 5000 })

    const caseId = String(item.case_id)
    const sceneIndex = Number(item.scene_index || 0)
    const actualCase = await root.getAttribute('data-eval-case-id')
    const actualScene = await root.getAttribute('data-eval-scene-index')
    const sentinelMatched = actualCase === caseId && actualScene === String(sceneIndex)
    const filename = `${safeFilename(caseId)}-scene${safeFilename(sceneIndex)}.png`
    const screenshotPath = path.join(outputDir, filename)
    if (usedScreenshotPaths.has(screenshotPath)) {
      throw new Error(`duplicate_screenshot_path:${filename}`)
    }
    usedScreenshotPaths.add(screenshotPath)

    const screenshotBuffer = await root.screenshot({ path: screenshotPath })
    const pixelStats = analyzePng(screenshotBuffer)
    const nonBlank = pixelStats.non_white_ratio > 0.0005 && pixelStats.unique_color_count > 1
    screenshots.push({
      case_id: caseId,
      scene_index: sceneIndex,
      scene_type: state.scene_type || item.scene_type || '',
      widget_type: item.content?.widget_type || '',
      content_kind: item.content?.kind || '',
      path: screenshotPath,
      width: pixelStats.width,
      height: pixelStats.height,
      non_blank: nonBlank,
      root_found: true,
      eval_ready: true,
      sentinel_matched: sentinelMatched,
      iframe_count: state.iframe_count || 0,
      visible_text: String(state.visible_text || '').slice(0, 1000),
      pixel_stats: pixelStats,
      console_errors: [...consoleErrors],
      page_errors: [...pageErrors],
      blocked_requests: [...blockedRequests],
      failed_requests: [...failedRequests],
    })
  }

  const hasCaptureProblems = screenshots.some(item => (
    item.non_blank !== true
    || item.root_found !== true
    || item.eval_ready !== true
    || item.sentinel_matched !== true
    || item.console_errors.length > 0
    || item.page_errors.length > 0
    || item.blocked_requests.length > 0
    || item.failed_requests.length > 0
  ))
  const status = hasCaptureProblems ? 'error' : 'ok'
  printManifest({
    status,
    base_url: baseUrl,
    checked_url: checkedUrl,
    screenshots,
  })
  if (status !== 'ok') process.exitCode = 1
} catch (error) {
  printManifest({
    status: 'error',
    reason: error instanceof Error ? error.message : String(error),
    base_url: baseUrl,
    checked_url: checkedUrl,
    screenshots,
    console_errors: consoleErrors,
    page_errors: pageErrors,
    blocked_requests: blockedRequests,
    failed_requests: failedRequests,
  })
  process.exitCode = 1
} finally {
  await browser?.close()
}
