/**
 * useHtmlSandbox — 高度测量脚本 + CSP 注入 单元测试
 */
import { describe, it, expect } from 'vitest'
import {
  injectInteractiveCSP,
  validateHtmlSafety,
  detectTruncation,
  extractHtmlBlock,
  getInteractiveSandboxAttr,
  getInteractiveCspAttr,
} from '../src/composables/useHtmlSandbox'

describe('extractHtmlBlock', () => {
  it('从 markdown 中提取 html 代码块', () => {
    const md = '一些说明\n```html\n<div>hello</div>\n```\n尾部'
    expect(extractHtmlBlock(md)).toBe('<div>hello</div>')
  })

  it('无 html 代码块返回 null', () => {
    expect(extractHtmlBlock('普通文本')).toBeNull()
  })
})

describe('validateHtmlSafety', () => {
  it('正常 HTML 通过校验', () => {
    expect(validateHtmlSafety('<div>safe</div>').valid).toBe(true)
  })

  it('含 iframe 标签拒绝', () => {
    const r = validateHtmlSafety('<iframe src="x"></iframe>')
    expect(r.valid).toBe(false)
    expect(r.error).toContain('iframe')
  })

  it('含 object 标签拒绝', () => {
    expect(validateHtmlSafety('<object data="x">').valid).toBe(false)
  })
})

describe('detectTruncation', () => {
  // 有 <html> 标签的完整文档
  it('完整 HTML 文档不截断', () => {
    expect(detectTruncation('<html><body></body></html>')).toBe(false)
  })

  it('</html> 后有空白仍视为完整', () => {
    expect(detectTruncation('<html><body></body></html>  \n')).toBe(false)
  })

  it('<html> 文档缺少 </html> 检测为截断', () => {
    expect(detectTruncation('<html><body><div>')).toBe(true)
  })

  // 有 <body> 但无 <html> 的文档
  it('<body> 文档以 </body> 结尾视为完整', () => {
    expect(detectTruncation('<body><div>content</div></body>')).toBe(false)
  })

  it('<body> 文档缺少 </body> 检测为截断', () => {
    expect(detectTruncation('<body><div>content</div>')).toBe(true)
  })

  // 片段模式（无 <html>/<body>）
  it('完整 div 片段不截断', () => {
    expect(detectTruncation('<div class="container"><p>hello</p></div>')).toBe(false)
  })

  it('以未闭合开标签结尾检测为截断', () => {
    expect(detectTruncation('<div class="container"><p class="')).toBe(true)
  })

  it('未闭合 <script> 检测为截断', () => {
    expect(detectTruncation('<div>ok</div><script>var x = 1')).toBe(true)
  })

  it('闭合 <script> 不截断', () => {
    expect(detectTruncation('<div>ok</div><script>var x = 1</script>')).toBe(false)
  })

  it('未闭合 <style> 检测为截断', () => {
    expect(detectTruncation('<style>.a { color: red }')).toBe(true)
  })

  it('闭合 <style> 不截断', () => {
    expect(detectTruncation('<style>.a { color: red }</style><div>ok</div>')).toBe(false)
  })
})

describe('injectInteractiveCSP', () => {
  const testHtml = '<!DOCTYPE html><html><head><title>T</title></head><body><p>X</p></body></html>'

  it('注入 CSP meta 标签', () => {
    const result = injectInteractiveCSP(testHtml)
    expect(result).toContain('Content-Security-Policy')
    expect(result).toContain("default-src 'none'")
  })

  it('注入高度测量脚本', () => {
    const result = injectInteractiveCSP(testHtml)
    expect(result).toContain('interactive-height')
    expect(result).toContain('postMessage')
  })

  it('高度脚本包含正反馈循环修复（height:0 + scrollHeight）', () => {
    const result = injectInteractiveCSP(testHtml)
    // 验证关键修复：先塌缩 body 到 0 再测 scrollHeight
    expect(result).toContain("setProperty('height','0','important')")
    expect(result).toContain('scrollHeight')
  })

  it('高度脚本注入全局 min-height 重置 style', () => {
    const result = injectInteractiveCSP(testHtml)
    expect(result).toContain('min-height:0!important')
    expect(result).toContain('createElement')
  })

  it('注入 ESC 键转发脚本', () => {
    const result = injectInteractiveCSP(testHtml)
    expect(result).toContain('interactive-escape')
    expect(result).toContain('Escape')
  })

  it('注入基础排版 CSS', () => {
    const result = injectInteractiveCSP(testHtml)
    expect(result).toContain('pre { white-space: pre-wrap')
  })

  it('无 <head> 时仍能注入', () => {
    const noHead = '<html><body>X</body></html>'
    const result = injectInteractiveCSP(noHead)
    expect(result).toContain('Content-Security-Policy')
  })

  it('纯 body 内容也能注入', () => {
    const bare = '<body><p>X</p></body>'
    const result = injectInteractiveCSP(bare)
    expect(result).toContain('Content-Security-Policy')
  })
})

describe('sandbox / csp attrs', () => {
  it('sandbox 包含 allow-scripts allow-modals', () => {
    expect(getInteractiveSandboxAttr()).toBe('allow-scripts allow-modals')
  })

  it('csp attr 阻止外部资源', () => {
    const csp = getInteractiveCspAttr()
    expect(csp).toContain("default-src 'none'")
    expect(csp).toContain("connect-src 'none'")
  })
})
