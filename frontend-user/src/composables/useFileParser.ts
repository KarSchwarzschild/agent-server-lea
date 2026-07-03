import type { GeneratedFileInfo } from '../types/super-agent'
import { FILE_GENERATION_TOOLS } from '../types/super-agent'

/**
 * 解析工具结果，提取文件信息
 *
 * 支持三种格式：
 * 1. 沙箱文件 JSON：{"__file_meta__": true, "download_url": "...", "filename": "...", "size": 123}
 * 2. 旧版 JSON：{"__file_meta__": {"filename": "...", "download_url": "..."}}
 * 3. 旧版 Markdown 格式（兼容历史消息）
 *
 * sandbox_bash 的结果可能包含多个文件 JSON（每行一个），
 * 此函数只返回第一个匹配。用 parseAllFileResults 获取全部。
 */
export function parseFileResult(result: string): GeneratedFileInfo | null {
  const all = parseAllFileResults(result)
  return all.length > 0 ? all[0] : null
}

/**
 * 从工具结果中提取所有文件信息
 */
export function parseAllFileResults(result: string): GeneratedFileInfo[] {
  if (!result) return []
  const files: GeneratedFileInfo[] = []
  const seen = new Set<string>()

  // 尝试逐行解析 JSON（沙箱工具返回格式）
  for (const line of result.split('\n')) {
    const trimmed = line.trim()
    if (!trimmed.startsWith('{') || !trimmed.includes('__file_meta__')) continue
    try {
      const obj = JSON.parse(trimmed)
      // 格式1: 沙箱扁平格式 {"__file_meta__": true, "download_url": "...", "filename": "..."}
      // 格式2: write_file 嵌套格式 {"__file_meta__": {"filename": "...", "download_url": "..."}}
      const meta = (typeof obj.__file_meta__ === 'object' && obj.__file_meta__)
        ? obj.__file_meta__
        : obj
      if (meta.download_url && meta.filename) {
        if (seen.has(meta.download_url)) continue
        seen.add(meta.download_url)
        const rawSize = meta.size_display || meta.size
        files.push({
          filename: meta.filename,
          downloadUrl: meta.download_url,
          size: typeof rawSize === 'number' ? `${rawSize} B` : (rawSize || '未知'),
        })
      }
    } catch { /* not valid JSON, skip */ }
  }

  if (files.length > 0) return files

  // 兼容旧版嵌套 JSON 格式
  const metaMatch = result.match(/"__file_meta__"\s*:\s*(\{[^}]+\})/)
  if (metaMatch) {
    try {
      const meta = JSON.parse(`{${metaMatch[0]}}`)['__file_meta__']
      if (meta?.filename && meta?.download_url) {
        files.push({
          filename: meta.filename,
          downloadUrl: meta.download_url,
          size: meta.size || '未知',
        })
        return files
      }
    } catch { /* fallback */ }
  }

  // 兼容旧版 Markdown 格式
  const filenameMatch = result.match(/文件已生成:\s*\*{0,2}([^\n*]+?)\*{0,2}\s*\n/)
    || result.match(/\[文件已保存\]\s*([^\n(]+)/)
  const urlMatch = result.match(/\[点击下载\]\(([^)]+)\)/) || result.match(/下载链接:\s*(\S+)/)
  const sizeMatch = result.match(/文件大小:\s*([^\n]+)/) || result.match(/\(([^)]*字节[^)]*)\)/)
  if (filenameMatch && urlMatch) {
    files.push({
      filename: filenameMatch[1].trim(),
      downloadUrl: urlMatch[1].trim(),
      size: sizeMatch ? sizeMatch[1].trim() : '未知',
    })
  }

  return files
}

export function isFileGenerationTool(tool: string): boolean {
  return (FILE_GENERATION_TOOLS as readonly string[]).includes(tool)
}
