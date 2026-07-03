<script setup lang="ts">
/**
 * ClassroomSpeechBubble — AI教师讲稿气泡 v2
 * 对齐 OpenMAIC PresentationBubbleCard:
 *   - 三色配色 (teacher/agent/user)
 *   - 真头像 + fallback 首字母
 *   - Play/Pause/Restart 按钮 + 呼吸动画
 *   - 宽度 420px, 左对齐
 */
import { ref, inject, computed } from 'vue'
import katex from 'katex'
import AudioIndicatorBars from './AudioIndicatorBars.vue'
import type { UsePlaybackEngineReturn } from '../../composables/usePlaybackEngine'

const props = withDefaults(defineProps<{
  role?: 'teacher' | 'agent' | 'user'
  name?: string
  avatar?: string
  /** 'play' | 'bars' | 'restart' | 'none' */
  buttonState?: string
  isPaused?: boolean
}>(), {
  role: 'teacher',
  name: 'AI 教师',
  avatar: '',
  buttonState: 'none',
  isPaused: false,
})

const emit = defineEmits<{
  (e: 'bubble-click'): void
}>()

const playback = inject<UsePlaybackEngineReturn>('playback')!
const collapsed = ref(false)
const hasText = computed(() => !!playback.currentSpeechText.value)

function toggle() { collapsed.value = !collapsed.value }

// 头像 fallback：取名字第一个字符
const avatarFallback = computed(() => props.name?.charAt(0) || '师')

// 角色配色
const roleClasses = computed(() => {
  switch (props.role) {
    case 'agent': return 'role-agent'
    case 'user': return 'role-user'
    default: return 'role-teacher'
  }
})

const roleLabelColor = computed(() => {
  switch (props.role) {
    case 'agent': return '#3b82f6'
    case 'user': return '#7c3aed'
    default: return '#a855f7'
  }
})

// 渲染公式：$...$（inline）和 $$...$$（display）
const renderedText = computed(() => {
  const text = playback.currentSpeechText.value
  if (!text) return ''
  let result = text.replace(/\$\$([\s\S]+?)\$\$/g, (_m: string, f: string) => {
    try { return katex.renderToString(f.trim(), { displayMode: true, throwOnError: false }) }
    catch { return f }
  })
  result = result.replace(/\$([^\$]+?)\$/g, (_m: string, f: string) => {
    try { return katex.renderToString(f.trim(), { displayMode: false, throwOnError: false }) }
    catch { return f }
  })
  return result
})
</script>

<template>
  <Transition name="bubble-fade">
    <div v-if="hasText" class="speech-bubble-container">
      <!-- Collapsed pill -->
      <Transition name="bubble-switch" mode="out-in">
        <div v-if="collapsed" key="pill" class="speech-pill" :class="roleClasses" @click="toggle">
          <div class="pill-avatar">
            <img v-if="avatar" :src="avatar" :alt="name" class="avatar-img" />
            <span v-else class="avatar-fallback">{{ avatarFallback }}</span>
          </div>
          <span class="pill-name">{{ name }}</span>
          <span class="pill-chevron">&#9650;</span>
        </div>

        <!-- Expanded card -->
        <div v-else key="card" class="speech-card" :class="roleClasses">
          <div class="card-header">
            <div class="card-avatar">
              <img v-if="avatar" :src="avatar" :alt="name" class="avatar-img" />
              <span v-else class="avatar-fallback">{{ avatarFallback }}</span>
            </div>
            <div class="card-meta">
              <div class="card-role-label" :style="{ color: roleLabelColor }">
                {{ role === 'user' ? '你' : role === 'agent' ? '学生' : '教师' }}
              </div>
              <div class="card-name">{{ name }}</div>
            </div>
            <button class="card-collapse-btn" title="收起" @click="toggle">&#9660;</button>
          </div>
          <div class="card-content" v-html="renderedText" />

          <!-- 播放按钮（右下角） -->
          <div
            v-if="buttonState && buttonState !== 'none'"
            class="card-play-btn"
            @click.stop="emit('bubble-click')"
          >
            <!-- bars = 正在播放 -->
            <template v-if="buttonState === 'bars' && !isPaused">
              <AudioIndicatorBars :color="roleLabelColor" />
            </template>
            <!-- play / paused-from-bars -->
            <template v-else-if="buttonState === 'play' || (buttonState === 'bars' && isPaused)">
              <span class="btn-icon">&#9654;</span>
            </template>
            <!-- pause -->
            <template v-else-if="buttonState === 'pause'">
              <span class="btn-icon">&#9646;&#9646;</span>
            </template>
            <!-- restart -->
            <template v-else-if="buttonState === 'restart'">
              <span class="btn-icon">&#8635;</span>
            </template>
          </div>
        </div>
      </Transition>
    </div>
  </Transition>
</template>

<style scoped>
.speech-bubble-container {
  flex-shrink: 0;
  height: 120px;
  padding: 8px 16px 8px;
  display: flex;
  justify-content: flex-start;
}

/* ─── Role colors ─── */
.role-teacher { --bubble-bg: rgba(255,255,255,0.62); --bubble-border: rgba(229,231,235,0.7); }
.role-agent { --bubble-bg: rgba(239,246,255,0.6); --bubble-border: rgba(191,219,254,0.7); }
.role-user { --bubble-bg: rgba(245,243,255,0.6); --bubble-border: rgba(221,214,254,0.7); }

/* ─── Avatar ─── */
.avatar-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: 50%;
}

.avatar-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #667eea, #764ba2);
  color: white;
  font-weight: 600;
  border-radius: 50%;
}

/* ─── Collapsed pill ─── */
.speech-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px 6px 6px;
  background: var(--bubble-bg);
  border: 1px solid var(--bubble-border);
  -webkit-backdrop-filter: blur(24px);
  backdrop-filter: blur(24px);
  border-radius: 9999px;
  cursor: pointer;
  box-shadow: 0 4px 20px -4px rgba(0,0,0,0.15);
  transition: all 0.2s;
}

.speech-pill:hover { box-shadow: 0 6px 24px -4px rgba(0,0,0,0.2); transform: scale(1.02); }
.speech-pill:active { transform: scale(0.98); }

.pill-avatar { width: 24px; height: 24px; border-radius: 50%; overflow: hidden; flex-shrink: 0; }
.pill-name { font-size: 13px; font-weight: 500; color: #333; max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pill-chevron { font-size: 8px; color: #999; }

/* ─── Expanded card ─── */
.speech-card {
  position: relative;
  width: 100%;
  min-width: 0;
  height: 100%;
  background: var(--bubble-bg);
  border: 1px solid var(--bubble-border);
  -webkit-backdrop-filter: blur(24px);
  backdrop-filter: blur(24px);
  border-radius: 16px;
  box-shadow: 0 18px 50px -20px rgba(0,0,0,0.45);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

@supports not (backdrop-filter: blur(1px)) {
  .speech-pill,
  .speech-card { background: rgba(255,255,255,0.92); }
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px 0;
  flex-shrink: 0;
}

.card-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  overflow: hidden;
  border: 2px solid white;
  box-shadow: 0 1px 3px rgba(0,0,0,0.1);
  flex-shrink: 0;
}

.card-meta { flex: 1; min-width: 0; }

.card-role-label {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.16em;
}

.card-name {
  font-size: 14px;
  font-weight: 500;
  color: #1a1a1a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-collapse-btn {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  border-radius: 50%;
  cursor: pointer;
  font-size: 10px;
  color: #999;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.2s;
}

.card-collapse-btn:hover { background: rgba(0,0,0,0.05); color: #666; }

.card-content {
  padding: 4px 16px 8px;
  margin-right: 40px;
  font-size: 14px;
  line-height: 1.5;
  color: #333;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  word-break: break-word;
  white-space: pre-wrap;
}

/* ─── Play button (bottom-right) ─── */
.card-play-btn {
  position: absolute;
  right: 10px;
  bottom: 10px;
  z-index: 20;
  padding: 6px;
  border-radius: 50%;
  background: rgba(255,255,255,0.4);
  -webkit-backdrop-filter: blur(8px);
  backdrop-filter: blur(8px);
  cursor: pointer;
  transition: all 0.2s;
  display: flex;
  align-items: center;
  justify-content: center;
}

.card-play-btn:hover { background: rgba(168,85,247,0.1); }

.btn-icon {
  font-size: 14px;
  color: #999;
  line-height: 1;
}

.card-play-btn:hover .btn-icon { color: #7c3aed; }

/* ─── Transitions ─── */
.bubble-fade-enter-active,
.bubble-fade-leave-active {
  transition: all 0.3s ease;
}
.bubble-fade-enter-from,
.bubble-fade-leave-to {
  opacity: 0;
  transform: translateY(8px);
}

.bubble-switch-enter-active,
.bubble-switch-leave-active {
  transition: all 0.18s ease;
}
.bubble-switch-enter-from {
  opacity: 0;
  transform: scale(0.95);
}
.bubble-switch-leave-to {
  opacity: 0;
  transform: scale(0.9);
}
</style>
