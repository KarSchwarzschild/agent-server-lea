<script setup lang="ts">
/**
 * ContentRenderer — Phase 2.1b ContentBlock 路由中间层
 *
 * 接收 msg.blocks，通过 useBlockRenderer 合并相邻 TextBlock，
 * 按 block.type 路由到对应的渲染组件。
 *
 * 职责边界：
 * - 不负责 msg.content 字符串解析（那是旧路径的 useContentParser 的工作）
 * - 不负责 SSE 事件处理（那是 useSSEHandler 的工作）
 * - 只负责 blocks → 渲染单元 → 组件路由
 */
import { computed, toRef } from 'vue'
import type { ContentBlock } from '../../types/content-block'
import type { ChatMessage, Reference, ToolCall, ThinkingStep } from '../../types/super-agent'
import { useBlockRenderer, type RenderUnit, type MergedTextUnit } from '../../composables/useBlockRenderer'
import StreamingContent from './StreamingContent.vue'
import AudioPlayer from './AudioPlayer.vue'

const props = defineProps<{
  blocks: ContentBlock[]
  msg: ChatMessage
}>()

const blocksRef = toRef(props, 'blocks')
const { renderUnits } = useBlockRenderer(blocksRef)

/** 根据 toolCallId 找到对应的 ToolCall 记录 */
function getToolCall(toolCallId: string): ToolCall | undefined {
  return props.msg.toolCalls?.find(tc => tc.toolCallId === toolCallId)
}

/** 根据 toolCallId 找到对应的 ThinkingStep（工具调用卡片数据） */
function getThinkingStep(toolCallId: string): ThinkingStep | undefined {
  return props.msg.thinkingSteps?.find(ts => ts.toolCallId === toolCallId)
}

function unitKey(unit: RenderUnit, index: number): string {
  if (unit.type === 'merged_text') return (unit as MergedTextUnit).key || `mt-${index}`
  return (unit as any)._id || `blk-${index}`
}
</script>

<template>
  <div class="content-renderer">
    <template v-for="(unit, idx) in renderUnits" :key="unitKey(unit, idx)">
      <!-- 合并文本块 → StreamingContent（Markdown 渲染） -->
      <StreamingContent
        v-if="unit.type === 'merged_text'"
        :content="(unit as MergedTextUnit).content"
        :is-streaming="(unit as MergedTextUnit).isStreaming"
        :references="msg.references"
        :reasoning-blocks="msg.reasoningBlocks"
        :msg-id="msg.id"
      />

      <!-- 工具锚点 → 工具调用卡片 -->
      <div
        v-else-if="unit.type === 'tool_anchor'"
        class="tool-anchor-block"
      >
        <slot
          name="tool-call"
          :tool-call-id="(unit as any).toolCallId"
          :tool-call="getToolCall((unit as any).toolCallId)"
          :thinking-step="getThinkingStep((unit as any).toolCallId)"
        >
          <!-- 默认渲染：简单的工具调用标记 -->
          <div v-if="getThinkingStep((unit as any).toolCallId)" class="tool-anchor-placeholder">
            {{ getThinkingStep((unit as any).toolCallId)?.toolDisplayName || '工具调用' }}
          </div>
        </slot>
      </div>

      <!-- 阶段进度 -->
      <div
        v-else-if="unit.type === 'stage_progress'"
        class="stage-progress-block"
      >
        <span class="stage-progress-chip">
          {{ (unit as any).title }}
          {{ (unit as any).index }}/{{ (unit as any).total }}
        </span>
      </div>

      <!-- 阶段输出（折叠面板） -->
      <details
        v-else-if="unit.type === 'stage_output'"
        class="stage-output-block"
      >
        <summary>{{ (unit as any).title || (unit as any).stageName }}</summary>
        <div class="stage-output-content">
          <StreamingContent
            :content="(unit as any).content || ''"
            :is-streaming="false"
          />
        </div>
      </details>

      <!-- 推理过程 -->
      <details
        v-else-if="unit.type === 'reasoning_block'"
        class="reasoning-block"
      >
        <summary>思考过程</summary>
        <pre class="reasoning-content">{{ (unit as any).content }}</pre>
      </details>

      <!-- 文件卡片 -->
      <div
        v-else-if="unit.type === 'file_card'"
        class="file-card-block"
      >
        <slot name="file-card" :file-id="(unit as any).fileId" :filename="(unit as any).filename">
          <div class="file-card-placeholder">
            📄 {{ (unit as any).filename }}
          </div>
        </slot>
      </div>

      <!-- 音频播放器 -->
      <AudioPlayer
        v-else-if="unit.type === 'audio'"
        :file-id="(unit as any).fileId"
      />

      <!-- 分隔线 -->
      <hr v-else-if="unit.type === 'divider'" class="block-divider" />

      <!-- 引用 -->
      <span
        v-else-if="unit.type === 'citation'"
        class="citation-ref"
        :title="(unit as any).text"
      >
        [{{ (unit as any).refId }}]
      </span>

      <!-- 兜底：未知类型 -->
      <div v-else class="unknown-block">
        <!-- {{ (unit as any).type }} -->
      </div>
    </template>
  </div>
</template>

<style scoped>
.content-renderer {
  /* 继承父容器样式 */
}

.tool-anchor-placeholder {
  display: inline-block;
  padding: 2px 8px;
  background: #f0f0f0;
  border-radius: 4px;
  font-size: 12px;
  color: #666;
}

.stage-progress-chip {
  display: inline-block;
  padding: 2px 10px;
  background: #e8f5e9;
  border-radius: 12px;
  font-size: 12px;
  color: #2e7d32;
  margin: 4px 0;
}

.stage-output-block {
  margin: 8px 0;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  overflow: hidden;
}

.stage-output-block summary {
  padding: 8px 12px;
  background: #fafafa;
  cursor: pointer;
  font-size: 13px;
  color: #555;
}

.stage-output-content {
  padding: 8px 12px;
  font-size: 14px;
}

.reasoning-block {
  margin: 8px 0;
  border: 1px solid #e3f2fd;
  border-radius: 8px;
}

.reasoning-block summary {
  padding: 6px 12px;
  background: #e3f2fd;
  cursor: pointer;
  font-size: 12px;
  color: #1565c0;
}

.reasoning-content {
  padding: 8px 12px;
  font-size: 12px;
  white-space: pre-wrap;
  color: #666;
  max-height: 300px;
  overflow-y: auto;
}

.file-card-placeholder {
  display: inline-flex;
  align-items: center;
  padding: 6px 12px;
  background: #f5f5f5;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  font-size: 13px;
  margin: 4px 0;
}

.block-divider {
  border: none;
  border-top: 1px solid #e0e0e0;
  margin: 12px 0;
}

.citation-ref {
  color: #1976d2;
  cursor: pointer;
  font-size: 12px;
  vertical-align: super;
}

.unknown-block {
  /* 隐藏未知类型 */
}
</style>
