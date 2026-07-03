<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import ClassroomPlayer from '../../components/super-agent/ClassroomPlayer.vue'
import type { ClassroomData, ClassroomScene } from '../../components/super-agent/ClassroomPlayer.vue'

type EvalScenePayload = {
  case_id?: string
  scene_index?: number | string
  scene_type?: ClassroomScene['scene_type']
  title?: string
  content?: Record<string, any>
} & Record<string, any>

const current = ref<{
  case_id: string
  scene: ClassroomScene
}>({
  case_id: '',
  scene: {
    id: 'eval_scene',
    title: 'Eval Scene',
    scene_type: 'interactive',
    content: {},
    speaker_notes: '',
    audio_file_id: '',
    audio_duration_ms: 0,
    status: 'ready',
    scene_index: 0,
    actions: [],
  },
})
const rootRef = ref<HTMLElement | null>(null)
const lastSetAt = ref(0)

const classroomData = computed<ClassroomData>(() => ({
  session_id: 'eval_session',
  topic: 'Classroom Scene Eval',
  status: 'ready',
  first_usable_scene: 0,
  can_enter: true,
  current_scene_index: 0,
  total_scenes: 1,
  scenes: [current.value.scene],
  config: {},
}))

async function setScene(payload: EvalScenePayload) {
  const sceneIndex = Number(payload?.scene_index || 0)
  current.value = {
    case_id: String(payload?.case_id || ''),
    scene: {
      id: `eval_scene_${sceneIndex}`,
      title: String(payload?.title || 'Eval Scene'),
      scene_type: payload?.scene_type || 'interactive',
      content: payload?.content || {},
      speaker_notes: '',
      audio_file_id: '',
      audio_duration_ms: 0,
      status: 'ready',
      scene_index: sceneIndex,
      actions: [],
    },
  }
  lastSetAt.value = Date.now()
  await nextTick()
}

function evalSceneState() {
  const root = rootRef.value
  return {
    case_id: current.value.case_id,
    scene_index: current.value.scene.scene_index,
    scene_type: current.value.scene.scene_type,
    visible_text: root?.innerText || '',
    iframe_count: root?.querySelectorAll('iframe').length || 0,
    width: root?.clientWidth || 0,
    height: root?.clientHeight || 0,
    last_set_at: lastSetAt.value,
  }
}

onMounted(() => {
  ;(window as any).__setScene = setScene
  ;(window as any).__evalSceneState = evalSceneState
  ;(window as any).__evalReady = true
})

onBeforeUnmount(() => {
  delete (window as any).__setScene
  delete (window as any).__evalSceneState
  delete (window as any).__evalReady
})
</script>

<template>
  <main
    ref="rootRef"
    class="eval-scene-page"
    data-eval-scene-root="true"
    :data-eval-case-id="current.case_id"
    :data-eval-scene-index="String(current.scene.scene_index)"
  >
    <ClassroomPlayer
      :data="classroomData"
      @exit="() => undefined"
    />
  </main>
</template>

<style scoped>
.eval-scene-page {
  position: fixed;
  inset: 0;
  min-width: 100vw;
  min-height: 100vh;
  overflow: hidden;
  background: #f9fafb;
}
</style>
