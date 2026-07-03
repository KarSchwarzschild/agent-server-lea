/**
 * 高亮修改的流式内容重建
 *
 * 原理（与 Open Canvas 一致）：
 * 1. 记录 fullMarkdown 中目标段落之前的内容（prefix）
 * 2. 记录目标段落之后的内容（suffix）
 * 3. 每收到一个 delta token，追加到"新 block"累积值
 * 4. 实时拼接：prefix + 新 block + suffix → 更新显示内容
 *
 * 增强：使用锚点三元组定位（而非简单文本替换）
 */

import { locateByAnchor, type AnchorTriple } from './useAnchorLocator'

export function useHighlightStream() {
  let prefix = ''
  let suffix = ''
  let newBlock = ''

  /**
   * 初始化：按锚点定位目标段落，分离 prefix 和 suffix
   */
  function init(fullMarkdown: string, anchor: AnchorTriple): boolean {
    const result = locateByAnchor(fullMarkdown, anchor)
    if (!result) {
      // 定位失败：fallback 为全文替换模式
      prefix = ''
      suffix = ''
      newBlock = ''
      return false
    }

    prefix = fullMarkdown.slice(0, result.blockStart)
    suffix = fullMarkdown.slice(result.blockEnd)
    newBlock = ''
    return true
  }

  /**
   * 简化初始化：直接传入 prefix/suffix（不需要锚点定位）
   */
  function initDirect(prefixText: string, suffixText: string) {
    prefix = prefixText
    suffix = suffixText
    newBlock = ''
  }

  /**
   * 追加 delta token，返回重建后的完整文档
   */
  function appendDelta(delta: string): string {
    newBlock += delta
    return prefix + newBlock + suffix
  }

  /**
   * 获取最终结果
   */
  function getResult(): string {
    return prefix + newBlock + suffix
  }

  /**
   * 获取新 block 累积值
   */
  function getNewBlock(): string {
    return newBlock
  }

  /**
   * 重置
   */
  function reset() {
    prefix = ''
    suffix = ''
    newBlock = ''
  }

  return { init, initDirect, appendDelta, getResult, getNewBlock, reset }
}
