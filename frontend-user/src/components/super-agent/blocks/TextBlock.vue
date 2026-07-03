<script setup lang="ts">
/**
 * TextBlock — Markdown 正文渲染
 * 流式阶段 throttle 渲染，完成后完整 parse
 */
import { computed, ref, watch } from 'vue'
import { useThrottleFn } from '@vueuse/core'
import MarkdownIt from 'markdown-it'

const props = defineProps<{
  content: string
  isStreaming?: boolean
}>()

const md = new MarkdownIt({ html: true, breaks: true, linkify: true })
const renderedHtml = ref('')

const fullRender = () => {
  renderedHtml.value = md.render(props.content || '')
}

const throttledRender = useThrottleFn(fullRender, 100)

watch(() => props.content, () => {
  if (props.isStreaming) {
    throttledRender()
  } else {
    fullRender()
  }
}, { immediate: true })
</script>

<template>
  <div class="text-block prose prose-sm max-w-none" v-html="renderedHtml" />
</template>
