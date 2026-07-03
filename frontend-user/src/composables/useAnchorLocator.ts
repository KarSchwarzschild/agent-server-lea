/**
 * 锚点三元组定位工具
 *
 * 从 ProseMirror 编辑器选区生成锚点数据：
 * - node_path: 选区所在块级节点的路径（如 "doc/heading[2]/text"）
 * - text_quote: { prefix, exact, suffix } — 选区前后文本上下文
 * - offset: 选区在块内的字符偏移
 *
 * 用于高亮修改时前端→后端的选区定位。
 */

export interface AnchorTriple {
  node_path: string
  text_quote: {
    prefix: string   // 选区前 50 个字符
    exact: string     // 选中的完整文本
    suffix: string    // 选区后 50 个字符
  }
  offset: number      // 选区起始在块级节点内的字符偏移
}

const CONTEXT_CHARS = 50

/**
 * 从 ProseMirror 编辑器状态提取锚点三元组
 */
export function extractAnchor(editor: any): AnchorTriple | null {
  if (!editor) return null

  const { state } = editor
  const { from, to } = state.selection
  if (from === to) return null

  const selectedText = state.doc.textBetween(from, to, ' ')
  if (!selectedText.trim()) return null

  // 获取选区所在的块级节点
  const $from = state.doc.resolve(from)

  // 构建 node_path
  const pathParts: string[] = []
  for (let depth = 0; depth <= $from.depth; depth++) {
    const node = $from.node(depth)
    const nodeName = node.type.name
    // 获取同类型节点的索引
    if (depth > 0) {
      const parent = $from.node(depth - 1)
      let sameTypeIndex = 0
      let found = false
      parent.forEach((child: any, _offset: number, index: number) => {
        if (found) return
        if (child.type.name === nodeName) sameTypeIndex++
        if (index === $from.index(depth - 1)) found = true
      })
      pathParts.push(`${nodeName}[${sameTypeIndex}]`)
    } else {
      pathParts.push(nodeName)
    }
  }
  const node_path = pathParts.join('/')

  // 计算块内偏移
  const blockStart = $from.start($from.depth)
  const offset = from - blockStart

  // 提取前后上下文
  const fullText = state.doc.textContent
  const globalFrom = state.doc.textBetween(0, from, ' ').length
  const globalTo = globalFrom + selectedText.length

  const prefix = fullText.slice(Math.max(0, globalFrom - CONTEXT_CHARS), globalFrom)
  const suffix = fullText.slice(globalTo, globalTo + CONTEXT_CHARS)

  return {
    node_path,
    text_quote: {
      prefix,
      exact: selectedText,
      suffix,
    },
    offset,
  }
}

/**
 * 从 Markdown 全文中按锚点定位目标段落
 * （前端侧快速定位，用于流式重建的 prefix/suffix 计算）
 */
export function locateByAnchor(
  fullMarkdown: string,
  anchor: AnchorTriple,
): { blockStart: number; blockEnd: number; block: string } | null {
  const { text_quote } = anchor

  // 策略 1：精确匹配 exact 文本
  let idx = fullMarkdown.indexOf(text_quote.exact)
  if (idx !== -1) {
    // 验证上下文
    const before = fullMarkdown.slice(Math.max(0, idx - CONTEXT_CHARS), idx)
    if (text_quote.prefix && !before.endsWith(text_quote.prefix.slice(-20))) {
      // 上下文不匹配，尝试下一个匹配
      const nextIdx = fullMarkdown.indexOf(text_quote.exact, idx + 1)
      if (nextIdx !== -1) idx = nextIdx
    }

    // 扩展到完整段落（找到前后换行符）
    let blockStart = fullMarkdown.lastIndexOf('\n\n', idx)
    blockStart = blockStart === -1 ? 0 : blockStart + 2
    let blockEnd = fullMarkdown.indexOf('\n\n', idx + text_quote.exact.length)
    blockEnd = blockEnd === -1 ? fullMarkdown.length : blockEnd

    return {
      blockStart,
      blockEnd,
      block: fullMarkdown.slice(blockStart, blockEnd),
    }
  }

  // 策略 2：用 prefix + suffix 夹逼定位
  if (text_quote.prefix && text_quote.suffix) {
    const prefixIdx = fullMarkdown.indexOf(text_quote.prefix)
    if (prefixIdx !== -1) {
      const searchStart = prefixIdx + text_quote.prefix.length
      const suffixIdx = fullMarkdown.indexOf(text_quote.suffix, searchStart)
      if (suffixIdx !== -1) {
        let blockStart = fullMarkdown.lastIndexOf('\n\n', searchStart)
        blockStart = blockStart === -1 ? 0 : blockStart + 2
        let blockEnd = fullMarkdown.indexOf('\n\n', suffixIdx)
        blockEnd = blockEnd === -1 ? fullMarkdown.length : blockEnd

        return {
          blockStart,
          blockEnd,
          block: fullMarkdown.slice(blockStart, blockEnd),
        }
      }
    }
  }

  return null
}
