<script setup lang="ts">
/**
 * 辩论 UI 组件
 *
 * 从 SuperAgent.vue 提取的辩论相关模板和逻辑：
 * - 辩论轮次操作面板（继续/追问/结束）
 * - 追问专家弹窗
 */
import { NAlert, NSpace, NButton, NModal, NInput } from 'naive-ui'
import type { Expert } from '../../api/superagent'
import DebateAudioBar from './DebateAudioBar.vue'
import type { AudioSegment } from './DebateAudioBar.vue'

defineProps<{
  /** 辩论状态 */
  debateStatus: 'idle' | 'running' | 'paused' | 'ended'
  /** 当前轮次 */
  currentDebateRound: number
  /** 最大轮次 */
  maxDebateRounds: number
  /** 是否可以继续辩论 */
  canContinueDebate: boolean
  /** 轮次提示 */
  roundHint: { message: string; options: Array<{ action: string; label: string; enabled: boolean }>; experts?: string[] } | null
  /** 是否正在处理辩论操作 */
  isDebateProcessing: boolean
  /** 追问弹窗是否显示 */
  showAskExpertModal: boolean
  /** 选中的专家 */
  selectedExpert: Expert | null
  /** 追问问题文本 */
  askExpertQuestion: string
  /** 追问是否加载中 */
  askExpertLoading: boolean
  /** 讨论模式 */
  discussionMode: string
  /** 音频片段（辩论语音化） */
  audioSegments?: AudioSegment[]
}>()

const emit = defineEmits<{
  (e: 'continue-debate'): void
  (e: 'end-debate'): void
  (e: 'show-ask-expert'): void
  (e: 'send-ask-expert'): void
  (e: 'close-ask-expert'): void
  (e: 'update:showAskExpertModal', value: boolean): void
  (e: 'update:askExpertQuestion', value: string): void
}>()
</script>

<template>
  <!-- 辩论轮次操作面板 -->
  <div v-if="debateStatus === 'paused' && roundHint" class="debate-actions-panel">
    <n-alert type="info" :bordered="false">
      <template #icon>
        <span style="font-size: 18px;">💡</span>
      </template>
      <div class="hint-content">
        <div class="hint-message">
          <span v-if="discussionMode && discussionMode !== 'debate'" class="mode-badge">
            {{ discussionMode === 'seminar' ? '🎓 研讨' : discussionMode === 'socratic' ? '💡 苏格拉底' : '' }}
          </span>
          {{ roundHint.message }}
        </div>
        <div class="hint-progress">第 {{ currentDebateRound }} / {{ maxDebateRounds }} 轮</div>
      </div>
    </n-alert>

    <n-space justify="center" style="margin-top: 16px;" :wrap="true">
      <n-button
        v-if="canContinueDebate"
        type="primary"
        @click="emit('continue-debate')"
        :loading="isDebateProcessing"
        :disabled="isDebateProcessing"
      >
        ▶️ 继续第 {{ currentDebateRound + 1 }} 轮
      </n-button>

      <n-button
        @click="emit('show-ask-expert')"
        :disabled="isDebateProcessing"
      >
        💬 追问专家
      </n-button>

      <n-button
        @click="emit('end-debate')"
        :disabled="isDebateProcessing"
        :type="!canContinueDebate ? 'primary' : 'default'"
      >
        🏁 {{ canContinueDebate ? '结束讨论' : '生成总结' }}
      </n-button>
    </n-space>
  </div>

  <!-- 追问专家弹窗 -->
  <n-modal
    :show="showAskExpertModal"
    @update:show="emit('update:showAskExpertModal', $event)"
    preset="dialog"
    :title="`追问 ${selectedExpert?.name || '专家'}`"
    positive-text="发送"
    negative-text="取消"
    :positive-button-props="{ disabled: askExpertQuestion.trim().length < 5 || askExpertLoading }"
    :loading="askExpertLoading"
    @positive-click="emit('send-ask-expert')"
    @negative-click="emit('close-ask-expert')"
    style="width: 90%; max-width: 500px;"
  >
    <div class="ask-expert-modal-content">
      <div v-if="selectedExpert" class="selected-expert-info">
        <div class="expert-avatar-large">{{ selectedExpert.name.charAt(0) }}</div>
        <div class="expert-details">
          <div class="expert-name-large">{{ selectedExpert.name }}</div>
          <div class="expert-title-large">{{ selectedExpert.title }}</div>
          <div class="expert-perspective">{{ selectedExpert.perspective }}</div>
        </div>
      </div>
      <n-input
        :value="askExpertQuestion"
        @update:value="emit('update:askExpertQuestion', $event)"
        type="textarea"
        placeholder="请输入您想追问的问题..."
        :autosize="{ minRows: 3, maxRows: 6 }"
        :disabled="askExpertLoading"
        :status="askExpertQuestion.trim().length > 0 && askExpertQuestion.trim().length < 5 ? 'warning' : undefined"
      />
      <div class="input-hint" :class="{ warning: askExpertQuestion.trim().length > 0 && askExpertQuestion.trim().length < 5 }">
        {{ askExpertQuestion.trim().length }}/5 字符（最少5个字符）
      </div>
    </div>
  </n-modal>

  <!-- 辩论音频单轨播放器（浮动在辩论容器底部） -->
  <DebateAudioBar
    v-if="audioSegments && audioSegments.length > 0 && (debateStatus === 'running' || debateStatus === 'paused' || debateStatus === 'ended')"
    :segments="audioSegments"
  />
</template>

<style scoped>
.debate-actions-panel {
  position: fixed;
  bottom: 80px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 100;
  background: white;
  border-radius: 12px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.15);
  padding: 16px 24px;
  max-width: 600px;
  width: calc(100% - 48px);
}

.hint-content {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.hint-message {
  font-size: 14px;
  color: #1f2937;
}

.mode-badge {
  display: inline-block;
  font-size: 12px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #eef2ff;
  color: #4f46e5;
  margin-right: 6px;
  font-weight: 500;
}

.hint-progress {
  font-size: 12px;
  color: #6b7280;
  white-space: nowrap;
  margin-left: 16px;
}

.ask-expert-modal-content {
  padding: 8px 0;
}

.selected-expert-info {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  padding: 12px;
  background: #f9fafb;
  border-radius: 8px;
}

.expert-avatar-large {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  font-weight: 600;
  flex-shrink: 0;
}

.expert-details {
  flex: 1;
}

.expert-name-large {
  font-size: 16px;
  font-weight: 600;
  color: #1f2937;
}

.expert-title-large {
  font-size: 13px;
  color: #6b7280;
  margin-top: 2px;
}

.expert-perspective {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 4px;
}

.input-hint {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 4px;
  text-align: right;
}

.input-hint.warning {
  color: #f59e0b;
}
</style>
