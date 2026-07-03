/**
 * ContentBlock 结构化内容类型
 *
 * 替代 4 套 HTML 注释标记（STAGE_CONTENT_START / TC:id / REASONING_BLOCK / STAGE_PROGRESS），
 * 统一为 discriminated union，前端按 type 路由到不同渲染组件。
 *
 * 创建日期：2026-04-04
 */

export interface TextBlock {
  type: 'text'
  content: string
}

export interface StageOutputBlock {
  type: 'stage_output'
  stageName: string
  visible: boolean
  content: string
  title?: string
}

export interface StageProgressBlock {
  type: 'stage_progress'
  stageName: string
  index: number
  total: number
  title: string
}

export interface ToolAnchorBlock {
  type: 'tool_anchor'
  toolCallId: string
}

export interface ReasoningBlock {
  type: 'reasoning_block'
  content: string
}

export interface CitationBlock {
  type: 'citation'
  refId: string
  text: string
}

export interface DividerBlock {
  type: 'divider'
}

export interface FileCardBlock {
  type: 'file_card'
  fileId: string
  filename: string
  fileType?: string
  sizeBytes?: number
}

export interface AudioBlock {
  type: 'audio'
  fileId: string
  voice?: string
  provider?: string
  durationMs?: number
}

/** 已知的 ContentBlock 类型 */
export type KnownContentBlock =
  | TextBlock
  | StageOutputBlock
  | StageProgressBlock
  | ToolAnchorBlock
  | ReasoningBlock
  | CitationBlock
  | DividerBlock
  | FileCardBlock
  | AudioBlock

/** 兜底：未知类型渲染为 raw text */
export interface UnknownBlock {
  type: string
  [key: string]: unknown
}

/** ContentBlock union（含兜底） */
export type ContentBlock = KnownContentBlock | UnknownBlock

/**
 * Block 类型 → Vue 组件映射表的 key 类型
 */
export type BlockType = KnownContentBlock['type']

/**
 * 为每个 block 生成稳定 ID（用于 v-for :key）
 */
export function generateBlockId(messageId: string, index: number): string {
  return `${messageId}-blk-${index}`
}
