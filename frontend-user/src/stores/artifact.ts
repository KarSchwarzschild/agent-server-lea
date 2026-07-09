/**
 * Artifact Pinia Store
 *
 * 管理：
 * - 当前会话的所有 Artifact
 * - 面板显隐状态（按需展开，不常驻）
 * - 流式生成状态
 * - 版本导航
 * - SSE 事件处理
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type {
  Artifact,
  ArtifactVersion,
  ArtifactPanelMode,
  ArtifactCreateEvent,
  ArtifactDeltaEvent,
  ArtifactDoneEvent,
  DocCategory,
} from '@/types/artifact'
import { getArtifactContent } from '@/types/artifact'
import type { Reference } from '@/types/super-agent'

export const useArtifactStore = defineStore('artifact', () => {
  // ========================
  // State
  // ========================

  /** 当前会话的所有 Artifact */
  const artifacts = ref<Map<string, Artifact>>(new Map())
  /** 响应式计数器（确保 Map size 变化触发更新） */
  const artifactCount = ref(0)

  /** 当前激活（显示在编辑面板）的 Artifact ID */
  const activeArtifactId = ref<string | null>(null)

  /** 面板模式（状态机） */
  const panelMode = ref<ArtifactPanelMode>('hidden')

  /** 流式生成中的累积内容 */
  const streamingContent = ref('')

  /** 流式 artifact 的 seq 序号（幂等去重） */
  const lastSeq = ref(0)

  /** 全屏/专注模式 */
  const isFullscreen = ref(false)

  /** 编辑中的未保存内容（由 ArtifactEditor content-change 回调设置） */
  const pendingEditContent = ref<string | null>(null)

  /** Diff 视图数据（由 VersionNavigator 设置，ArtifactPanel 读取） */
  const diffData = ref<{ ops: Array<{ type: string; content: string }>; stats: { inserts: number; deletes: number; unchanged: number } } | null>(null)

  /** 当前 artifact 关联的引用列表（从消息的 references 同步） */
  const activeReferences = ref<Reference[]>([])

  // ========================
  // Getters
  // ========================

  /** 面板是否可见 */
  const panelVisible = computed(() => panelMode.value !== 'hidden')

  /** 当前激活的 Artifact */
  const activeArtifact = computed(() => {
    if (!activeArtifactId.value) return null
    return artifacts.value.get(activeArtifactId.value) ?? null
  })

  /** 当前版本内容 */
  const currentContent = computed(() => {
    if (!activeArtifact.value) return null
    return getArtifactContent(activeArtifact.value)
  })

  /** 显示内容（流式 or 当前版本） */
  const displayContent = computed(() => {
    if (panelMode.value === 'streaming') {
      return streamingContent.value
    }
    return currentContent.value?.content ?? ''
  })

  /** 版本导航状态 */
  const versionNav = computed(() => {
    const a = activeArtifact.value
    if (!a) return { current: 0, total: 0, canBack: false, canForward: false }
    const isStreaming = panelMode.value === 'streaming'
    return {
      current: a.currentIndex,
      total: a.contents.length,
      canBack: a.currentIndex > 1 && !isStreaming,
      canForward: a.currentIndex < a.contents.length && !isStreaming,
    }
  })

  /** 是否正在流式生成 */
  const isStreaming = computed(() => panelMode.value === 'streaming')

  // ========================
  // Actions — SSE 事件处理
  // ========================

  /** skill_start 检测到 output_format=artifact → 显示骨架屏 */
  function enterSkeletonMode() {
    panelMode.value = 'skeleton'
  }

  /** 处理 artifact_create SSE 事件 */
  function handleArtifactCreate(event: ArtifactCreateEvent) {
    if (event.seq <= lastSeq.value) return // 幂等去重
    lastSeq.value = event.seq

    const artifact: Artifact = {
      id: event.artifact_id,
      sessionId: '',
      userId: '',
      title: event.title,
      artifactType: (event.artifact_type || 'markdown') as 'markdown' | 'code' | 'slides',
      docCategory: (event.doc_category || 'general') as DocCategory,
      currentIndex: event.version_index,
      contents: [],
      isPinned: false,
      isStandalone: false,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    }
    artifacts.value.set(artifact.id, artifact)
    activeArtifactId.value = artifact.id
    // 后台累积流式内容，对话区预览卡片实时显示，面板等用户点击才展开
    streamingContent.value = ''
    _isBackgroundStreaming.value = true
  }

  /** 后台流式标记（面板未打开但内容在累积） */
  const _isBackgroundStreaming = ref(false)

  /** 处理 artifact_delta SSE 事件 */
  function handleArtifactDelta(event: ArtifactDeltaEvent) {
    if (event.seq <= lastSeq.value) return
    lastSeq.value = event.seq

    // 竞态修复：如果还没收到 artifact_create 但已经收到 delta，自动补创建
    if (!activeArtifactId.value || !artifacts.value.has(event.artifact_id)) {
      handleArtifactCreate({
        artifact_id: event.artifact_id,
        title: '文档生成中...',
        artifact_type: 'markdown',
        version_index: event.version_index,
        seq: 0,
      })
    }

    streamingContent.value += event.delta
  }

  /** 处理 artifact_done SSE 事件 */
  function handleArtifactDone(event: ArtifactDoneEvent) {
    if (event.seq <= lastSeq.value) return
    lastSeq.value = event.seq

    // 竞态修复：如果还没收到 create 但直接收到 done
    if (!artifacts.value.has(event.artifact_id)) {
      handleArtifactCreate({
        artifact_id: event.artifact_id,
        title: '文档',
        artifact_type: 'markdown',
        version_index: event.version_index,
        seq: 0,
      })
    }

    const artifact = artifacts.value.get(event.artifact_id)
    if (!artifact) return

    const finalContent = event.content || streamingContent.value

    const version: ArtifactVersion = {
      id: 0,
      artifactId: event.artifact_id,
      versionIndex: event.version_index,
      content: finalContent,
      title: artifact.title,
      source: 'ai_generate',
      createdAt: new Date().toISOString(),
    }
    artifact.contents.push(version)
    artifact.currentIndex = event.version_index
    // 强制触发 Map reactive 更新（确保依赖 artifacts.get() 的组件重新渲染）
    // 创建新对象引用，让 Vue 检测到 Map value 变化
    artifacts.value.set(event.artifact_id, { ...artifact })
    // 递增计数器（不能用 size，因为 set 同一个 key 时 size 不变，Vue ref 不会触发更新）
    artifactCount.value++

    // 流式完成：如果面板已打开则切到 readonly，否则保持隐藏
    if (panelMode.value === 'streaming') {
      panelMode.value = 'readonly'
    }
    streamingContent.value = ''
    _isBackgroundStreaming.value = false
  }

  // ========================
  // Actions — 版本导航
  // ========================

  function navigateVersion(direction: 'back' | 'forward') {
    const a = activeArtifact.value
    if (!a || isStreaming.value) return

    const newIndex = direction === 'back'
      ? Math.max(1, a.currentIndex - 1)
      : Math.min(a.contents.length, a.currentIndex + 1)
    a.currentIndex = newIndex
    panelMode.value = 'version-browse'
  }

  // ========================
  // Actions — 面板控制
  // ========================

  /** 打开面板查看指定 artifact；若 store 里没有则从 MinIO 后端拉。 */
  async function openPanel(artifactId: string, references?: Reference[]) {
    activeArtifactId.value = artifactId
    panelMode.value = 'readonly'
    if (references) {
      activeReferences.value = references
    }
    // 若已在内存，直接展示；否则从 MinIO lazy-fetch
    if (!artifacts.value.has(artifactId)) {
      try {
        const { fetchArtifactById } = await import('@/api/artifact')
        const data = await fetchArtifactById(artifactId)
        // 后端返回的 payload 含 {version, theme, metadata, slides, artifact_id, type, session_id}
        const jsonStr = JSON.stringify(data)
        const artifact: Artifact = {
          id: artifactId,
          sessionId: (data as any).session_id || '',
          userId: '',
          title: (data as any).metadata?.title || '演示文稿',
          artifactType: ((data as any).type === 'slides' ? 'slides' : 'markdown') as 'markdown' | 'code' | 'slides',
          docCategory: 'general',
          currentIndex: 1,
          contents: [{
            id: 0,
            artifactId,
            versionIndex: 1,
            content: jsonStr,
            title: (data as any).metadata?.title || '演示文稿',
            source: 'ai_generate',
            createdAt: new Date().toISOString(),
          }],
          isPinned: false,
          isStandalone: false,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
        }
        artifacts.value.set(artifactId, artifact)
        artifactCount.value++
      } catch (e) {
        console.warn('[artifact] lazy fetch failed:', e)
      }
    }
  }

  /** 关闭面板 */
  function closePanel() {
    panelMode.value = 'hidden'
    isFullscreen.value = false
    activeArtifactId.value = null
    activeReferences.value = []
    diffData.value = null
  }

  /** SSE 流式过程中同步引用数据 */
  function syncReferences(refs: Reference[]) {
    activeReferences.value = refs
  }

  /** 切换全屏 */
  function toggleFullscreen() {
    isFullscreen.value = !isFullscreen.value
  }

  /** 会话切换时重置 */
  function resetForSession() {
    artifacts.value.clear()
    artifactCount.value = 0
    activeArtifactId.value = null
    panelMode.value = 'hidden'
    streamingContent.value = ''
    lastSeq.value = 0
    isFullscreen.value = false
    activeReferences.value = []
  }

  /** 加载会话的 Artifact 列表（从 API 响应数据填充） */
  function loadArtifacts(list: Artifact[]) {
    artifacts.value.clear()
    for (const a of list) {
      artifacts.value.set(a.id, a)
    }
    artifactCount.value = artifacts.value.size
  }

  // ========================
  // Actions — 编辑模式（Phase 3）
  // ========================

  /** 进入编辑模式 */
  function enterEditMode() {
    if (panelMode.value === 'readonly' || panelMode.value === 'version-browse') {
      panelMode.value = 'editing'
    }
  }

  /** 退出编辑模式 */
  function exitEditMode() {
    if (panelMode.value === 'editing') {
      panelMode.value = 'readonly'
    }
  }

  /** 用户手动保存新版本（Ctrl+S） */
  async function saveUserVersion(content: string, changeSummary?: string) {
    if (!activeArtifactId.value) return null

    try {
      const { createVersion } = await import('@/api/artifact')
      const versionData = await createVersion(activeArtifactId.value, {
        content,
        source: 'user_edit',
        change_summary: changeSummary,
      })
      const artifact = artifacts.value.get(activeArtifactId.value)
      if (artifact) {
        artifact.contents.push(versionData)
        artifact.currentIndex = versionData.versionIndex
      }
      panelMode.value = 'readonly'
      return versionData
    } catch (e) {
      console.error('[ArtifactStore] saveUserVersion failed:', e)
      return null
    }
  }

  /** localStorage 草稿自动保存（不创建版本，防丢失） */
  function saveDraft(content: string) {
    if (!activeArtifactId.value) return
    try {
      localStorage.setItem(`artifact_draft_${activeArtifactId.value}`, content)
    } catch { /* quota exceeded 等 */ }
  }

  /** 恢复草稿 */
  function loadDraft(): string | null {
    if (!activeArtifactId.value) return null
    return localStorage.getItem(`artifact_draft_${activeArtifactId.value}`)
  }

  /** 清除草稿 */
  function clearDraft() {
    if (!activeArtifactId.value) return
    localStorage.removeItem(`artifact_draft_${activeArtifactId.value}`)
  }

  /** 标记编辑器有未保存内容 */
  function setPendingEdit(content: string | null) {
    pendingEditContent.value = content
  }

  /** 发送消息前自动保存编辑中的内容（如果有） */
  async function saveIfDirty(): Promise<boolean> {
    if (panelMode.value !== 'editing' || !pendingEditContent.value) return false
    const saved = await saveUserVersion(pendingEditContent.value, '发送前自动保存')
    if (saved) {
      pendingEditContent.value = null
      clearDraft()
    }
    return !!saved
  }

  return {
    // state
    artifacts, activeArtifactId, panelMode, streamingContent, lastSeq, isFullscreen, artifactCount, diffData,
    // getters
    panelVisible, activeArtifact, currentContent, displayContent, versionNav, isStreaming,
    // SSE handlers
    enterSkeletonMode, handleArtifactCreate, handleArtifactDelta, handleArtifactDone,
    // navigation
    navigateVersion,
    // panel control
    openPanel, closePanel, toggleFullscreen, resetForSession, loadArtifacts, syncReferences,
    // background streaming + references
    _isBackgroundStreaming, activeReferences,
    // editing (Phase 3)
    enterEditMode, exitEditMode, saveUserVersion, saveDraft, loadDraft, clearDraft,
    setPendingEdit, saveIfDirty, pendingEditContent,
  }
})
