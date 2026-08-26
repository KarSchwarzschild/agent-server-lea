<script setup lang="ts">
/**
 * WorkspaceDrawer — 当前 session 的沙箱工作区文件树
 *
 * 数据源 GET /api/files/list（lea-agent 从 JWT 注入 tenant_id 后反代到 lea-tools）。
 * 后端两种 store：
 *   nfs      共享存储 readdir，沙箱销毁后文件仍在（生产路径）
 *   sandbox  envd 读活沙箱，沙箱一死就读不到（无共享存储的开发环境）
 * store 值直接显示出来，免得排查时搞不清读的是哪一头。
 *
 * 隐藏文件（__pycache__ / *.pyc / *.log 等）后端默认过滤，可切换显示。
 */
import { computed, ref, watch } from 'vue'
import { NButton, NIcon, NSpin, NSwitch, useMessage } from 'naive-ui'
import {
  ChevronDownOutline, ChevronForwardOutline, CloudDownloadOutline,
  DocumentOutline, FolderOutline, FolderOpenOutline, RefreshOutline,
} from '@vicons/ionicons5'
import { listWorkspaceFiles, downloadArtifact } from '../../api/agent-chat'
import type { WorkspaceFile } from '../../api/agent-chat'

const props = defineProps<{
  sessionId: string | null
  /** 父组件在 turn 结束时 +1，触发自动刷新 */
  refreshKey?: number
}>()

const message = useMessage()

const files = ref<WorkspaceFile[]>([])
const store = ref('')
const truncated = ref(false)
const loading = ref(false)
const loaded = ref(false)
const showHidden = ref(false)
const downloading = ref<Set<string>>(new Set())

// ── 扁平路径 → 目录树 ────────────────────────────────
// 后端返回的是 "output/slides/compile.js" 这样的相对路径列表，
// 这里折成层级，否则深目录（node_modules、slides/output）看不出结构。

interface TreeNode {
  name: string
  path: string          // 目录也有 path，用作折叠状态 key
  isDir: boolean
  file?: WorkspaceFile
  children: TreeNode[]
  fileCount: number     // 目录：递归文件数
  totalSize: number     // 目录：递归字节数
}

function buildTree(list: WorkspaceFile[]): TreeNode[] {
  const root: TreeNode = {
    name: '', path: '', isDir: true, children: [], fileCount: 0, totalSize: 0,
  }

  for (const f of list) {
    const parts = f.path.split('/')
    let cur = root
    for (let i = 0; i < parts.length; i++) {
      const isLeaf = i === parts.length - 1
      const segPath = parts.slice(0, i + 1).join('/')
      if (isLeaf) {
        cur.children.push({
          name: parts[i]!, path: segPath, isDir: false, file: f,
          children: [], fileCount: 0, totalSize: f.size,
        })
      } else {
        let dir = cur.children.find(c => c.isDir && c.name === parts[i])
        if (!dir) {
          dir = {
            name: parts[i]!, path: segPath, isDir: true,
            children: [], fileCount: 0, totalSize: 0,
          }
          cur.children.push(dir)
        }
        cur = dir
      }
    }
  }

  // 自底向上累计计数/体积，同时排序：目录在前，同类按名字
  const finish = (n: TreeNode): void => {
    for (const c of n.children) {
      if (c.isDir) finish(c)
      n.fileCount += c.isDir ? c.fileCount : 1
      n.totalSize += c.totalSize
    }
    n.children.sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1
      return a.name.localeCompare(b.name)
    })
  }
  finish(root)

  // output/ 提到最前 —— 那是产物的推荐落点
  root.children.sort((a, b) => {
    const ao = a.name === 'output' ? 0 : 1
    const bo = b.name === 'output' ? 0 : 1
    if (ao !== bo) return ao - bo
    if (a.isDir !== b.isDir) return a.isDir ? -1 : 1
    return a.name.localeCompare(b.name)
  })

  if (!root.children.length) return []

  // 合成 workspace 根节点。沙箱里的真实路径是 /workspace，后端返回的是
  // 相对它的路径；不显示这一层的话 output/ 会跟工作文件平级排开，
  // 看不出它们同属一个工作区。
  root.name = 'workspace'
  root.path = '.'
  return [root]
}

const tree = computed(() => buildTree(files.value))

// 折叠状态：记「收起的目录」而非展开的，这样新出现的目录默认展开
const collapsed = ref<Set<string>>(new Set())

function toggleDir(path: string) {
  const next = new Set(collapsed.value)
  next.has(path) ? next.delete(path) : next.add(path)
  collapsed.value = next
}

/** 树 → 带缩进的可见行（收起的目录不展开子节点） */
interface Row { node: TreeNode; depth: number }
const rows = computed<Row[]>(() => {
  const out: Row[] = []
  const walk = (nodes: TreeNode[], depth: number) => {
    for (const n of nodes) {
      out.push({ node: n, depth })
      if (n.isDir && !collapsed.value.has(n.path)) walk(n.children, depth + 1)
    }
  }
  walk(tree.value, 0)
  return out
})

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

async function load() {
  if (!props.sessionId) {
    files.value = []
    loaded.value = false
    return
  }
  loading.value = true
  try {
    const res = await listWorkspaceFiles(props.sessionId, showHidden.value)
    files.value = res.files || []
    store.value = res.store || ''
    truncated.value = !!res.truncated
    loaded.value = true
  } catch (e: any) {
    message.error(e?.message || '读取工作区失败')
  } finally {
    loading.value = false
  }
}

async function onDownload(f: WorkspaceFile) {
  if (!props.sessionId) return
  const key = f.path
  if (downloading.value.has(key)) return
  downloading.value = new Set(downloading.value).add(key)
  try {
    await downloadArtifact(props.sessionId, f.path)
  } catch (e: any) {
    message.error(e?.message || '下载失败')
  } finally {
    const next = new Set(downloading.value)
    next.delete(key)
    downloading.value = next
  }
}

watch(() => props.sessionId, load, { immediate: true })
watch(() => props.refreshKey, () => { if (loaded.value) load() })
watch(showHidden, load)
</script>

<template>
  <section class="ws-drawer">
    <header class="ws-head">
      <div class="ws-title">
        <NIcon :component="FolderOpenOutline" />
        <span>工作区</span>
        <span v-if="files.length" class="ws-count">{{ files.length }}</span>
      </div>
      <NButton text size="tiny" :disabled="!sessionId || loading" @click="load">
        <template #icon><NIcon :component="RefreshOutline" /></template>
      </NButton>
    </header>

    <div class="ws-toolbar">
      <label class="ws-hidden-toggle">
        <NSwitch v-model:value="showHidden" size="small" :disabled="!sessionId" />
        <span>显示隐藏文件</span>
      </label>
      <span v-if="store" class="ws-store" :title="store === 'nfs'
        ? '共享存储：沙箱销毁后文件仍可访问'
        : '读活沙箱：沙箱销毁后文件不再可读'">{{ store }}</span>
    </div>

    <div class="ws-body">
      <div v-if="!sessionId" class="ws-empty">开始对话后可查看工作区</div>
      <NSpin v-else-if="loading && !loaded" size="small" class="ws-spin" />
      <div v-else-if="!files.length" class="ws-empty">还没有文件</div>

      <ul v-else class="ws-list">
        <li
          v-for="{ node, depth } in rows"
          :key="node.path"
          class="ws-item"
          :class="{ 'ws-item--dir': node.isDir }"
          :style="{ paddingLeft: 14 + depth * 14 + 'px' }"
          @click="node.isDir ? toggleDir(node.path) : onDownload(node.file!)"
        >
          <!-- 目录：折叠箭头 + 文件夹图标 -->
          <template v-if="node.isDir">
            <NIcon
              :component="collapsed.has(node.path) ? ChevronForwardOutline : ChevronDownOutline"
              class="ws-caret"
            />
            <NIcon
              :component="collapsed.has(node.path) ? FolderOutline : FolderOpenOutline"
              class="ws-item-icon ws-item-icon--dir"
            />
            <div class="ws-item-main">
              <div class="ws-item-path" :title="node.path">{{ node.name }}</div>
            </div>
            <span class="ws-dir-meta">{{ node.fileCount }} 项 · {{ fmtSize(node.totalSize) }}</span>
          </template>

          <!-- 文件：图标 + 大小 + 下载 -->
          <template v-else>
            <span class="ws-caret ws-caret--empty" />
            <NIcon :component="DocumentOutline" class="ws-item-icon" />
            <div class="ws-item-main">
              <div class="ws-item-path" :title="node.file!.path">{{ node.name }}</div>
              <div class="ws-item-meta">{{ fmtSize(node.file!.size) }}</div>
            </div>
            <NSpin v-if="downloading.has(node.file!.path)" size="small" />
            <NIcon v-else :component="CloudDownloadOutline" class="ws-item-dl" />
          </template>
        </li>
      </ul>

      <p v-if="truncated" class="ws-truncated">文件过多，仅显示前一部分</p>
    </div>
  </section>
</template>

<style scoped>
.ws-drawer {
  display: flex;
  flex-direction: column;
  height: 100%;
  border-left: 1px solid var(--border-color, #efeff5);
  background: #fff;
}
.ws-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border-color, #efeff5);
}
.ws-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: #1f2225;
}
.ws-count {
  font-size: 11px;
  font-weight: 400;
  color: #9aa0a6;
}
.ws-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  border-bottom: 1px solid var(--border-color, #f4f4f8);
}
.ws-hidden-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #6b7078;
  cursor: pointer;
}
.ws-store {
  font-size: 11px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  color: #9aa0a6;
  cursor: help;
}
.ws-body {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0;
}
.ws-empty {
  padding: 24px 14px;
  font-size: 12px;
  color: #9aa0a6;
  text-align: center;
}
.ws-spin {
  display: block;
  margin: 24px auto;
}
.ws-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.ws-item {
  display: flex;
  align-items: center;
  gap: 6px;
  /* padding-left 由 inline style 按层级深度控制 */
  padding: 6px 14px 6px 0;
  cursor: pointer;
  transition: background 0.15s;
}
.ws-item:hover {
  background: #f7f8fa;
}
.ws-item--dir .ws-item-path {
  font-weight: 500;
}
.ws-caret {
  flex: 0 0 12px;
  font-size: 12px;
  color: #9aa0a6;
}
.ws-caret--empty {
  display: inline-block;
  width: 12px;
}
.ws-item-icon {
  flex: 0 0 auto;
  color: #9aa0a6;
}
.ws-item-icon--dir {
  color: #7f9cc4;
}
.ws-dir-meta {
  flex: 0 0 auto;
  font-size: 11px;
  color: #9aa0a6;
  white-space: nowrap;
}
.ws-item-main {
  flex: 1;
  min-width: 0;
}
.ws-item-path {
  font-size: 12px;
  color: #1f2225;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ws-item-meta {
  font-size: 11px;
  color: #9aa0a6;
}
.ws-item-dl {
  flex: 0 0 auto;
  color: #c4c8cd;
}
.ws-item:hover .ws-item-dl {
  color: #4b5563;
}
.ws-truncated {
  margin: 8px 14px 0;
  font-size: 11px;
  color: #9aa0a6;
}
</style>
