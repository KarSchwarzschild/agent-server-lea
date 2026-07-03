<script setup lang="ts">
import { computed } from 'vue'
import HeadingBlock from './blocks/HeadingBlock.vue'
import ParagraphBlock from './blocks/ParagraphBlock.vue'
import ListBlock from './blocks/ListBlock.vue'
import TableBlock from './blocks/TableBlock.vue'
import QuoteBlock from './blocks/QuoteBlock.vue'
import ReferenceBlock from './blocks/ReferenceBlock.vue'
import CodeBlock from './blocks/CodeBlock.vue'
import MathBlock from './blocks/MathBlock.vue'

interface Block {
  type: string
  [key: string]: any
}

interface Chapter {
  chapter_id: string
  title: string
  blocks: Block[]
  summary?: string
  word_count?: number
}

interface DocumentIR {
  title: string
  abstract?: string
  chapters: Chapter[]
  references?: any[]
  metadata?: any
  source_map?: Record<string, any[]>
}

const props = defineProps<{
  document: DocumentIR
  onCiteClick?: (sourceId: string) => void
}>()

const blockComponents: Record<string, any> = {
  heading: HeadingBlock,
  paragraph: ParagraphBlock,
  list: ListBlock,
  table: TableBlock,
  quote: QuoteBlock,
  reference: ReferenceBlock,
  code: CodeBlock,
  math: MathBlock,
}

function getBlockComponent(type: string) {
  return blockComponents[type] || null
}
</script>

<template>
  <article class="ir-document">
    <h1 class="ir-title">{{ document.title }}</h1>

    <div v-if="document.abstract" class="ir-abstract">
      <h2>摘要</h2>
      <p>{{ document.abstract }}</p>
    </div>

    <div v-for="chapter in document.chapters" :key="chapter.chapter_id" class="ir-chapter" :id="`chapter-${chapter.chapter_id}`">
      <h2 class="ir-chapter-title">{{ chapter.title }}</h2>

      <template v-for="(block, idx) in chapter.blocks" :key="`${chapter.chapter_id}-${idx}`">
        <component
          :is="getBlockComponent(block.type)"
          v-if="getBlockComponent(block.type)"
          :block="block"
          :chapter-id="chapter.chapter_id"
          :block-index="idx"
          @cite-click="onCiteClick?.($event)"
        />
        <div v-else class="ir-unknown-block">
          [未知块类型: {{ block.type }}]
        </div>
      </template>
    </div>

    <div v-if="document.references?.length" class="ir-references">
      <h2>参考文献</h2>
      <ol>
        <li v-for="ref in document.references" :key="ref.ref_id" class="ir-ref-item">
          <span class="ref-authors">{{ ref.authors?.join(', ') }}</span>
          <span v-if="ref.year"> ({{ ref.year }}). </span>
          <span class="ref-title">{{ ref.title }}</span>
          <span v-if="ref.source">. {{ ref.source }}</span>
          <span v-if="ref.doi">. DOI: {{ ref.doi }}</span>
        </li>
      </ol>
    </div>
  </article>
</template>

<style scoped>
.ir-document { max-width: 800px; margin: 0 auto; line-height: 1.8; color: #333; }
.ir-title { font-size: 24px; text-align: center; margin-bottom: 24px; }
.ir-abstract { background: #f8f9fa; padding: 16px 20px; border-radius: 8px; margin-bottom: 32px; }
.ir-abstract h2 { font-size: 16px; color: #666; margin-bottom: 8px; }
.ir-abstract p { font-size: 14px; color: #555; }
.ir-chapter { margin-bottom: 32px; }
.ir-chapter-title { font-size: 20px; border-bottom: 1px solid #eee; padding-bottom: 8px; margin-bottom: 16px; }
.ir-references { margin-top: 48px; border-top: 2px solid #eee; padding-top: 24px; }
.ir-references h2 { font-size: 18px; margin-bottom: 16px; }
.ir-ref-item { margin-bottom: 8px; font-size: 14px; }
.ref-authors { font-weight: 500; }
.ref-title { font-style: italic; }
.ir-unknown-block { color: #999; font-style: italic; padding: 8px; background: #fff3cd; border-radius: 4px; }
</style>
