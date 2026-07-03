/**
 * HTML 沙箱渲染 Composable
 *
 * 提供 HTML 代码提取、CSP 注入、安全校验、截断检测等功能。
 * 设计为可复用的 composable，未来 html-slides / html-interactive 等 Skill 可直接复用。
 */

/** CSP 策略：阻止所有外部资源加载和网络请求 */
const CSP_POLICY =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; " +
  "img-src data: blob:; font-src data:; connect-src 'none'"

/** 危险标签黑名单（AI 生成的 HTML 不应包含这些） */
const DANGEROUS_TAGS = ['iframe', 'object', 'embed', 'applet', 'form']

/**
 * 从 Markdown 内容中提取 ```html 代码块
 *
 * 支持续写拼接场景：提取第一个完整的 html 代码块
 */
export function extractHtmlBlock(markdown: string): string | null {
  // 匹配 ```html ... ``` 代码块（贪婪匹配最后一个 ```）
  const match = markdown.match(/```html\s*\n([\s\S]*?)```(?:\s*$|\n)/)
  if (match && match[1]) {
    return match[1].trim()
  }

  // 兜底：如果没有闭合的 ```，尝试提取开放的代码块（流式场景）
  const openMatch = markdown.match(/```html\s*\n([\s\S]*)$/)
  if (openMatch && openMatch[1] && openMatch[1].includes('<!DOCTYPE')) {
    return openMatch[1].trim()
  }

  // 兜底 2：LLM 直接输出完整 HTML 文档（不在代码块中）
  // 匹配从 <!DOCTYPE 到 </html> 的完整文档
  const directMatch = markdown.match(/(<!DOCTYPE\s+html[\s\S]*<\/html>)/i)
  if (directMatch && directMatch[1]) {
    return directMatch[1].trim()
  }

  // 兜底 3：流式场景，有 <!DOCTYPE 但 </html> 还没到
  const partialMatch = markdown.match(/(<!DOCTYPE\s+html[\s\S]*)$/i)
  if (partialMatch && partialMatch[1] && partialMatch[1].includes('<body')) {
    return partialMatch[1].trim()
  }

  return null
}

/**
 * 防止动画结束后白屏的全局 CSS 后处理
 * 即使 LLM 忘记写 fill-mode: forwards，前端也保证动画结束后内容保持可见
 */
const ANIMATION_FILL_CSS =
  '<style>*, *::before, *::after { animation-fill-mode: forwards !important; }</style>'

/**
 * 高度自适应脚本（注入到 Animation iframe）
 * 1. flatten() 强制剥离 html/body/body>* 的 vh 高度和 overflow:hidden（阻断正反馈循环）
 * 2. 测量 body.offsetHeight 并通过 postMessage 上报
 * 3. ESC 键转发给父窗口
 */
const ANIMATION_HEIGHT_SCRIPT = `<script>(function(){` +
  `var last=0,tid=0;` +
  `function flatten(){` +
    `var d=document.documentElement,b=document.body;` +
    `d.style.setProperty('height','auto','important');` +
    `d.style.setProperty('min-height','0','important');` +
    `b.style.setProperty('height','auto','important');` +
    `b.style.setProperty('min-height','0','important');` +
    `b.style.setProperty('overflow','visible','important');` +
    `var cs=b.children;for(var i=0;i<cs.length;i++){` +
      `var el=cs[i];if(el.tagName==='SCRIPT'||el.tagName==='STYLE')continue;` +
      `var s=getComputedStyle(el);` +
      `if(s.minHeight&&s.minHeight!=='0px')el.style.setProperty('min-height','0','important');` +
      `if(s.height&&(s.height.indexOf('vh')>-1||parseInt(s.height)>800))el.style.setProperty('height','auto','important');` +
    `}` +
  `}` +
  `function r(){` +
    `if(!document.body)return;` +
    `flatten();` +
    `var h=document.body.offsetHeight;` +
    `if(h<10)h=document.body.scrollHeight;` +
    `h=Math.ceil(h);` +
    `if(Math.abs(h-last)>8){last=h;window.parent.postMessage({type:'animation-height',height:h},'*')}` +
  `}` +
  `function dr(){clearTimeout(tid);tid=setTimeout(r,150)}` +
  `function init(){flatten();r();setTimeout(r,300);setTimeout(r,1000);` +
    `window.addEventListener('resize',dr);` +
    `new MutationObserver(dr).observe(document.body,{childList:true,subtree:true})` +
  `}` +
  `if(document.body){init()}else{document.addEventListener('DOMContentLoaded',init)}` +
  `document.addEventListener('keydown',function(e){if(e.key==='Escape')window.parent.postMessage({type:'animation-escape'},'*')})` +
`})()</script>`

/**
 * 在 HTML 的 <head> 中注入 CSP meta + 动画保持 CSS + 高度自适应脚本
 *
 * 双重防护：即使浏览器不支持 iframe csp 属性（Firefox），
 * 内嵌的 <meta> CSP 标签仍会生效。
 */
export function injectCSP(html: string): string {
  const cspMeta = `<meta http-equiv="Content-Security-Policy" content="${CSP_POLICY}">`
  const injections = `${cspMeta}\n${ANIMATION_FILL_CSS}\n${ANIMATION_HEIGHT_SCRIPT}`

  // 在 <head> 标签后注入
  if (html.includes('<head>')) {
    return html.replace('<head>', `<head>\n${injections}`)
  }
  if (html.includes('<head ')) {
    return html.replace(/<head\s[^>]*>/, `$&\n${injections}`)
  }

  // 如果没有 <head>，在 <html> 后注入
  if (html.includes('<html')) {
    return html.replace(/<html[^>]*>/, `$&\n<head>${injections}</head>`)
  }

  // 兜底：在最前面注入
  return `${injections}\n${html}`
}

/**
 * 校验 HTML 安全性
 *
 * 拒绝包含危险标签的 HTML（iframe/object/embed 等）
 */
export function validateHtmlSafety(html: string): { valid: boolean; error?: string } {
  const lowerHtml = html.toLowerCase()

  for (const tag of DANGEROUS_TAGS) {
    // 匹配 <iframe, <object 等（避免误匹配属性中包含这些词的情况）
    const regex = new RegExp(`<${tag}[\\s>/]`, 'i')
    if (regex.test(lowerHtml)) {
      return { valid: false, error: `HTML 包含不安全标签: <${tag}>` }
    }
  }

  return { valid: true }
}

/**
 * 检测 HTML 是否被截断
 *
 * 分三级检测：
 * 1. 有 <html> 标签 → 必须有 </html> 闭合
 * 2. 有 <body> 标签 → 必须有 </body> 闭合
 * 3. 片段模式 → 检查是否在标签中间被截断、是否有未闭合的 script/style
 */
export function detectTruncation(html: string): boolean {
  const trimmed = html.trim().toLowerCase()

  // 有 <html 标签 → 必须有 </html> 闭合
  if (trimmed.includes('<html')) {
    return !/<\/html>\s*$/.test(trimmed)
  }

  // 有 <body 标签 → 必须有 </body> 闭合
  if (trimmed.includes('<body')) {
    return !/<\/body>\s*$/.test(trimmed)
  }

  // 片段模式：检查明显截断信号

  // 以未闭合的开标签结尾（如 <div class="）
  if (/<[a-z][a-z0-9]*(?:\s[^>]*)?$/.test(trimmed)) {
    return true
  }

  // 未闭合的 <script> 或 <style>
  const scriptOpen = (trimmed.match(/<script[\s>]/g) || []).length
  const scriptClose = (trimmed.match(/<\/script>/g) || []).length
  if (scriptOpen > scriptClose) return true

  const styleOpen = (trimmed.match(/<style[\s>]/g) || []).length
  const styleClose = (trimmed.match(/<\/style>/g) || []).length
  if (styleOpen > styleClose) return true

  // 片段无明显截断信号 → 视为完整
  return false
}

/**
 * 获取 iframe sandbox 属性值
 */
export function getSandboxAttr(): string {
  return 'allow-scripts'
}

/**
 * 获取 iframe csp 属性值
 */
export function getCspAttr(): string {
  return CSP_POLICY
}

/**
 * 获取 Interactive iframe sandbox 属性值（比 Animation 多 allow-modals）
 */
export function getInteractiveSandboxAttr(): string {
  return 'allow-scripts allow-modals'
}

/** Interactive CSP：与 Animation CSP 同级严格，不放行外部 CDN */
const CSP_INTERACTIVE_POLICY =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; " +
  "img-src data: blob:; font-src data:; connect-src 'none'"

/** Interactive 基础排版 CSS：确保代码块、列表、表格等在 iframe 内有正确样式 */
const INTERACTIVE_BASE_CSS = `<style>
/* 代码块 */
pre { white-space: pre-wrap; word-wrap: break-word; overflow-x: auto; background: #1e1e2e; color: #cdd6f4; border-radius: 8px; padding: 12px 16px; margin: 12px 0; font-family: 'Menlo', 'Monaco', 'Courier New', monospace; font-size: 13px; line-height: 1.6; }
code { font-family: inherit; font-size: inherit; }
:not(pre) > code { background: #f0f0f0; color: #e74c3c; padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }
/* 基础排版 */
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 16px; box-sizing: border-box; }
h1, h2, h3, h4 { margin: 16px 0 8px; color: #1a1a2e; }
ul, ol { padding-left: 24px; margin: 8px 0; }
li { margin: 4px 0; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; }
th, td { border: 1px solid #e0e0e0; padding: 8px 12px; text-align: left; }
th { background: #f5f5f5; font-weight: 600; }
/* 响应式 */
img, svg, canvas { max-width: 100%; height: auto; }
</style>`

/**
 * 在 HTML 的 <head> 中注入 Interactive CSP meta + 基础排版 CSS + 高度自适应脚本
 */
export function injectInteractiveCSP(html: string): string {
  const cspMeta = `<meta http-equiv="Content-Security-Policy" content="${CSP_INTERACTIVE_POLICY}">`
  // 注入高度自适应上报脚本：
  // 1. 测量前用 JS inline !important 强制剥离 html/body/body>* 的高度膨胀样式（最高优先级）
  // 2. 测量 body.offsetHeight（包含 padding，不包含 margin）
  // 3. 防抖 150ms + 阈值 8px 防止动画抖动
  // 4. ESC 键转发给父窗口
  const heightScript = `<script>(function(){` +
    `var last=0,tid=0;` +
    // 强制剥离高度膨胀样式（inline !important 胜过一切 CSS）
    `function flatten(){` +
      `var d=document.documentElement,b=document.body;` +
      `d.style.setProperty('height','auto','important');` +
      `d.style.setProperty('min-height','0','important');` +
      `b.style.setProperty('height','auto','important');` +
      `b.style.setProperty('min-height','0','important');` +
      `b.style.setProperty('overflow','visible','important');` +
      `var cs=b.children;for(var i=0;i<cs.length;i++){` +
        `var el=cs[i];if(el.tagName==='SCRIPT'||el.tagName==='STYLE')continue;` +
        `var s=getComputedStyle(el);` +
        `if(s.minHeight&&s.minHeight!=='0px')el.style.setProperty('min-height','0','important');` +
        `if(s.height&&(s.height.indexOf('vh')>-1||parseInt(s.height)>800))el.style.setProperty('height','auto','important');` +
      `}` +
    `}` +
    // 测量 body 真实内容高度（打破正反馈循环）
    // 核心：flatten() 用 inline !important 设了 body height:auto，
    //       stylesheet !important 无法覆盖 inline !important，
    //       因此必须用 inline style 在 flatten 之后重新设 height:0
    `function r(){` +
      `if(!document.body)return;` +
      `flatten();` +
      // 注入临时 style：全局 * min-height:0（对子孙元素生效，它们没有 inline 冲突）
      `var ms=document.createElement('style');` +
      `ms.textContent='*{min-height:0!important}';` +
      `document.head.appendChild(ms);` +
      // inline !important 覆盖 flatten 设的 height:auto
      `var d=document.documentElement,b=document.body;` +
      `d.style.setProperty('height','0','important');` +
      `b.style.setProperty('height','0','important');` +
      `b.style.setProperty('overflow','hidden','important');` +
      `var h=b.scrollHeight;` +
      // 还原
      `ms.remove();` +
      `d.style.setProperty('height','auto','important');` +
      `b.style.setProperty('height','auto','important');` +
      `b.style.setProperty('overflow','visible','important');` +
      `h=Math.ceil(h);` +
      `if(Math.abs(h-last)>8){last=h;window.parent.postMessage({type:'interactive-height',height:h},'*')}` +
    `}` +
    `function dr(){clearTimeout(tid);tid=setTimeout(r,150)}` +
    `function init(){flatten();r();setTimeout(r,300);setTimeout(r,1000);` +
      `window.addEventListener('resize',dr);` +
      `new MutationObserver(dr).observe(document.body,{childList:true,subtree:true})` +
    `}` +
    `if(document.body){init()}else{document.addEventListener('DOMContentLoaded',init)}` +
    // ESC 转发
    `document.addEventListener('keydown',function(e){if(e.key==='Escape')window.parent.postMessage({type:'interactive-escape'},'*')})` +
  `})()</script>`

  const injection = `${cspMeta}\n${INTERACTIVE_BASE_CSS}\n${heightScript}`

  if (html.includes('<head>')) {
    return html.replace('<head>', `<head>\n${injection}`)
  }
  if (html.includes('<head ')) {
    return html.replace(/<head\s[^>]*>/, `$&\n${injection}`)
  }
  if (html.includes('<html')) {
    return html.replace(/<html[^>]*>/, `$&\n<head>${injection}</head>`)
  }
  return `${injection}\n${html}`
}

/**
 * 获取 Interactive iframe csp 属性值
 */
export function getInteractiveCspAttr(): string {
  return CSP_INTERACTIVE_POLICY
}

export function useHtmlSandbox() {
  return {
    extractHtmlBlock,
    injectCSP,
    injectInteractiveCSP,
    validateHtmlSafety,
    detectTruncation,
    getSandboxAttr,
    getInteractiveSandboxAttr,
    getCspAttr,
    getInteractiveCspAttr,
  }
}
