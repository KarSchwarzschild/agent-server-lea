<template>
  <svg :viewBox="`0 0 ${W} ${H}`" class="flow-graph" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5"
              markerWidth="6" markerHeight="6" orient="auto-start-reverse">
        <path d="M 0 0 L 10 5 L 0 10 z" fill="#999" />
      </marker>
    </defs>

    <!-- Edges -->
    <g class="edges">
      <line v-for="(e, i) in edges" :key="'e'+i"
            :x1="e.x1" :y1="e.y1" :x2="e.x2" :y2="e.y2"
            :stroke-dasharray="e.dash ? '6 3' : 'none'"
            stroke="#bbb" stroke-width="1.5" marker-end="url(#arrow)" />
      <!-- HITL retry arcs (reject → redo) -->
      <path v-for="(a, i) in arcs" :key="'a'+i"
            :d="a.d" fill="none" stroke="#e8a040" stroke-width="1.2"
            stroke-dasharray="4 3" marker-end="url(#arrow)" />
    </g>

    <!-- Nodes -->
    <g v-for="n in nodes" :key="n.id"
       :transform="`translate(${n.x}, ${n.y})`"
       class="node-group">
      <rect :width="NW" :height="NH" rx="6"
            :class="['node-rect', statusClass(n.id)]" />
      <text :x="NW/2" :y="NH/2 + 1" text-anchor="middle" dominant-baseline="middle"
            class="node-label" :font-size="n.label.length > 4 ? 11 : 12">
        {{ n.label }}
      </text>
      <!-- HITL indicator -->
      <circle v-if="n.hitl" :cx="NW - 4" :cy="4" r="4" fill="#faad14" />
    </g>
  </svg>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { NodeStatus } from '../composables/useReviewSSE'

const props = defineProps<{
  statuses: Record<string, NodeStatus>
}>()

const NW = 100
const NH = 36
const GX = 130
const GY = 56
const PAD_X = 20
const PAD_Y = 20
const COLS = 2
const ROWS = 6
const W = PAD_X * 2 + COLS * GX
const H = PAD_Y * 2 + ROWS * GY

interface NodeDef { id: string; label: string; row: number; col: number; hitl?: boolean }

const nodeDefs: NodeDef[] = [
  { id: 'init',            label: '初始化',   row: 0, col: 0 },
  { id: 'decompose_topic', label: '主题分解', row: 0, col: 1 },
  { id: 'search',          label: '文献检索', row: 1, col: 0 },
  { id: 'hitl_search',     label: '检索审查', row: 1, col: 1, hitl: true },
  { id: 'analyze',         label: '文献分析', row: 2, col: 0 },
  { id: 'hitl_analysis',   label: '分析审查', row: 2, col: 1, hitl: true },
  { id: 'outline',         label: '大纲生成', row: 3, col: 0 },
  { id: 'hitl_outline',    label: '大纲审查', row: 3, col: 1, hitl: true },
  { id: 'generate',        label: '综述生成', row: 4, col: 0 },
  { id: 'hitl_final',      label: '终审',     row: 4, col: 1, hitl: true },
  { id: 'finalize',        label: '完成',     row: 5, col: 0 },
]

const nodes = computed(() =>
  nodeDefs.map(n => ({
    ...n,
    x: PAD_X + n.col * GX,
    y: PAD_Y + n.row * GY,
  }))
)

function pos(id: string) {
  const n = nodes.value.find(n => n.id === id)!
  return { cx: n.x + NW / 2, cy: n.y + NH / 2, x: n.x, y: n.y }
}

const linearEdges: [string, string][] = [
  ['init', 'decompose_topic'],
  ['decompose_topic', 'search'],
  ['search', 'hitl_search'],
  ['hitl_search', 'analyze'],
  ['analyze', 'hitl_analysis'],
  ['hitl_analysis', 'outline'],
  ['outline', 'hitl_outline'],
  ['hitl_outline', 'generate'],
  ['generate', 'hitl_final'],
  ['hitl_final', 'finalize'],
]

const edges = computed(() =>
  linearEdges.map(([from, to]) => {
    const a = pos(from)
    const b = pos(to)
    let x1: number, y1: number, x2: number, y2: number

    if (a.cy === b.cy) {
      x1 = a.x + NW; y1 = a.cy
      x2 = b.x; y2 = b.cy
    } else if (b.x <= a.x) {
      x1 = a.cx; y1 = a.y + NH
      x2 = b.cx; y2 = b.y
    } else {
      x1 = a.cx; y1 = a.y + NH
      x2 = b.cx; y2 = b.y
    }
    return { x1, y1, x2, y2, dash: false }
  })
)

// HITL reject → redo arcs
const retryPairs: [string, string][] = [
  ['hitl_search', 'search'],
  ['hitl_analysis', 'analyze'],
  ['hitl_outline', 'outline'],
  ['hitl_final', 'generate'],
]

const arcs = computed(() =>
  retryPairs.map(([from, to]) => {
    const a = pos(from)
    const b = pos(to)
    const midY = a.cy - 20
    return {
      d: `M ${a.x} ${a.cy} Q ${a.x - 15} ${midY} ${b.x + NW} ${b.cy}`,
    }
  })
)

function statusClass(id: string): string {
  return props.statuses[id] || 'pending'
}
</script>

<style scoped>
.flow-graph {
  width: 100%;
  max-width: 340px;
}

.node-rect {
  stroke-width: 1.5;
  transition: fill 0.3s, stroke 0.3s;
}

.node-rect.pending { fill: #f5f5f5; stroke: #d9d9d9; }
.node-rect.completed { fill: #f6ffed; stroke: #b7eb8f; }
.node-rect.waiting { fill: #fff7e6; stroke: #ffd591; }

.node-rect.running {
  fill: #e6f4ff;
  stroke: #91caff;
  animation: pulse 1.5s ease-in-out infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.node-label {
  fill: #333;
  font-family: system-ui, sans-serif;
  pointer-events: none;
  user-select: none;
}
</style>
