/**
 * ContentBlock 组件注册表
 *
 * 静态 Map（不用 defineAsyncComponent），因为所有组件 < 20KB 总和
 */
import type { Component } from 'vue'
import TextBlock from './TextBlock.vue'
import StageChip from './StageChip.vue'
import StageProgressBar from './StageProgressBar.vue'
import ReasoningCollapse from './ReasoningCollapse.vue'
import BlockDivider from './BlockDivider.vue'
import CitationFooter from './CitationFooter.vue'
import RawTextBlock from './RawTextBlock.vue'
// ToolCallCard 复用已有组件
import ToolCallCard from '../ToolCallCard.vue'

const blockComponentMap: Record<string, Component> = {
  text: TextBlock,
  stage_output: StageChip,    // visible=false → StageChip, visible=true → 可扩展为 StagePanel
  stage_progress: StageProgressBar,
  tool_anchor: ToolCallCard,
  reasoning_block: ReasoningCollapse,
  citation: CitationFooter,
  divider: BlockDivider,
}

/**
 * 根据 block type 获取 Vue 组件，未知类型 fallback 到 RawTextBlock
 */
export function getBlockComponent(type: string): Component {
  return blockComponentMap[type] ?? RawTextBlock
}

export {
  TextBlock,
  StageChip,
  StageProgressBar,
  ReasoningCollapse,
  BlockDivider,
  CitationFooter,
  RawTextBlock,
  ToolCallCard,
}
