<template>
  <div class="version-nav" v-if="versionNav.total > 0">
    <button
      class="nav-btn"
      :disabled="!versionNav.canBack"
      @click="artifactStore.navigateVersion('back')"
      title="上一个版本"
    >
      <n-icon size="16"><ChevronBack /></n-icon>
    </button>

    <span class="version-label" :title="versionTooltip">
      <span class="version-num">v{{ versionNav.current }}</span>
      <span class="version-sep">/</span>
      <span class="version-total">{{ versionNav.total }}</span>
    </span>

    <button
      class="nav-btn"
      :disabled="!versionNav.canForward"
      @click="artifactStore.navigateVersion('forward')"
      title="下一个版本"
    >
      <n-icon size="16"><ChevronForward /></n-icon>
    </button>

    <!-- 版本来源图标（内联，非 hover tooltip） -->
    <span v-if="currentVersionMeta" class="version-source">
      <span class="source-icon">{{ currentVersionMeta.icon }}</span>
      <span class="source-label">{{ currentVersionMeta.label }}</span>
    </span>

    <!-- 浏览历史版本时的操作按钮 -->
    <template v-if="versionNav.total > 1 && versionNav.current < versionNav.total">
      <button class="diff-btn" :class="{ active: showDiff }" @click="toggleDiff" title="差异对比">
        差异
      </button>
      <button class="restore-btn" @click="handleRestore" title="恢复到此版本">
        恢复
      </button>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { NIcon } from 'naive-ui'
import { ChevronBack, ChevronForward } from '@vicons/ionicons5'
import { useArtifactStore } from '@/stores/artifact'
import { SOURCE_META, getArtifactContent, type ArtifactSource } from '@/types/artifact'
import { getVersionDiff } from '@/api/artifact'

const artifactStore = useArtifactStore()

const versionNav = computed(() => artifactStore.versionNav)
const showDiff = ref(false)

async function toggleDiff() {
  showDiff.value = !showDiff.value
  if (showDiff.value) {
    await loadDiff()
  } else {
    artifactStore.diffData = null
  }
}

async function loadDiff() {
  const artifact = artifactStore.activeArtifact
  if (!artifact || artifact.currentIndex <= 1) return
  try {
    const result = await getVersionDiff(
      artifact.id,
      artifact.currentIndex - 1,
      artifact.currentIndex,
    )
    artifactStore.diffData = { ops: result.ops, stats: result.stats }
  } catch {
    artifactStore.diffData = null
    showDiff.value = false
  }
}

watch(() => artifactStore.activeArtifact?.currentIndex, () => {
  if (showDiff.value) loadDiff()
})

async function handleRestore() {
  const artifact = artifactStore.activeArtifact
  if (!artifact) return
  try {
    const { restoreVersion } = await import('@/api/artifact')
    const newVersion = await restoreVersion(artifact.id, artifact.currentIndex)
    // 清除 diff 视图（必须在 currentIndex 变更前，避免 watch 重新 loadDiff）
    showDiff.value = false
    artifactStore.diffData = null
    // 更新本地数据
    artifact.contents.push(newVersion)
    artifact.currentIndex = newVersion.versionIndex
    artifactStore.artifactCount = artifactStore.artifacts.size // 触发响应
  } catch (e) {
    console.error('恢复版本失败:', e)
  }
}

const currentVersionMeta = computed(() => {
  const artifact = artifactStore.activeArtifact
  if (!artifact) return null
  const version = getArtifactContent(artifact)
  if (!version) return null
  return SOURCE_META[version.source as ArtifactSource] || null
})

const versionTooltip = computed(() => {
  const artifact = artifactStore.activeArtifact
  if (!artifact) return ''
  const version = getArtifactContent(artifact)
  if (!version) return ''
  const time = version.createdAt ? new Date(version.createdAt).toLocaleString('zh-CN') : ''
  const summary = version.changeSummary || ''
  return `v${version.versionIndex} — ${SOURCE_META[version.source as ArtifactSource]?.label || version.source}\n${time}${summary ? '\n' + summary : ''}`
})
</script>

<style scoped>
.version-nav {
  display: flex;
  align-items: center;
  gap: 4px;
  user-select: none;
}

.nav-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 4px;
  background: transparent;
  cursor: pointer;
  color: var(--le-text-secondary, #666);
  transition: all 0.15s;
}
.nav-btn:hover:not(:disabled) {
  background: var(--le-bg-hover, #f0f0f0);
  color: var(--le-text-primary, #333);
}
.nav-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.version-label {
  font-size: 13px;
  font-family: var(--le-font-mono, monospace);
  color: var(--le-text-secondary, #666);
  cursor: help;
  white-space: pre-line;
}
.version-num {
  font-weight: 600;
  color: var(--le-text-primary, #333);
}

.version-source {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-left: 6px;
  font-size: 12px;
  color: var(--le-text-tertiary, #999);
}
.source-icon {
  font-size: 12px;
}
.source-label {
  max-width: 60px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 恢复按钮 */
.restore-btn {
  margin-left: 4px;
  padding: 2px 8px;
  border: 1px solid #f59e0b;
  border-radius: 4px;
  background: transparent;
  font-size: 11px;
  cursor: pointer;
  color: #f59e0b;
  transition: all 0.15s;
}
.restore-btn:hover {
  background: #fef3c7;
  color: #d97706;
}

/* Diff 按钮 */
.diff-btn {
  margin-left: 6px;
  padding: 2px 8px;
  border: 1px solid #ddd;
  border-radius: 4px;
  background: transparent;
  font-size: 11px;
  cursor: pointer;
  color: #666;
  transition: all 0.15s;
}
.diff-btn:hover, .diff-btn.active {
  background: #f0f4ff;
  border-color: #2080f0;
  color: #2080f0;
}

/* Diff 样式已移至 ArtifactPanel */
</style>
