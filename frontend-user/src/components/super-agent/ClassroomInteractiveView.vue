<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import InteractivePlayer from './InteractivePlayer.vue'

type MatchingPair = {
  id: string
  left: string
  right: string
}

type OrderingItem = {
  id: string
  text: string
  order: number
}

type Category = {
  id: string
  label: string
}

type CategoryItem = {
  id: string
  text: string
  category_id: string
}

const props = defineProps<{
  scene: {
    title: string
    content: Record<string, any>
  }
}>()

const content = computed(() => props.scene?.content || {})
const pairs = computed<MatchingPair[]>(() => {
  const rawPairs = Array.isArray(content.value.pairs) ? content.value.pairs : []
  return rawPairs
    .map((item: any, index: number) => ({
      id: String(item.id || `pair_${index + 1}`),
      left: String(item.left || ''),
      right: String(item.right || ''),
    }))
    .filter((item: MatchingPair) => item.left && item.right)
})

const prompt = computed(() => String(content.value.prompt || props.scene.title || '请完成互动练习。'))
const htmlContent = computed(() => String(content.value.html || content.value.html_content || content.value.htmlContent || ''))
const selectedLeft = ref('')
const answers = ref<Record<string, string>>({})
const submitted = ref(false)
const orderedIds = ref<string[]>([])
const selectedCategoryItem = ref('')
const categoryAnswers = ref<Record<string, string>>({})
const reflectionAnswers = ref<Record<number, string>>({})

const rightOptions = computed(() => {
  return [...pairs.value].reverse().map((item) => ({ id: item.id, label: item.right }))
})

const matchingScore = computed(() => {
  return pairs.value.filter((item) => answers.value[item.id] === item.id).length
})

const matchingComplete = computed(() => {
  return pairs.value.length > 0 && pairs.value.every((item) => answers.value[item.id])
})

const orderingItems = computed<OrderingItem[]>(() => {
  const rawItems = Array.isArray(content.value.items) ? content.value.items : []
  return rawItems
    .map((item: any, index: number) => ({
      id: String(item.id || `item_${index + 1}`),
      text: String(item.text || item.title || item.label || ''),
      order: Number(item.order || index + 1),
    }))
    .filter((item: OrderingItem) => item.text)
})

const orderedItems = computed<OrderingItem[]>(() => {
  const byId = new Map(orderingItems.value.map((item) => [item.id, item]))
  return orderedIds.value.map((id) => byId.get(id)).filter(Boolean) as OrderingItem[]
})

const orderingScore = computed(() => {
  return orderedItems.value.filter((item, index) => item.order === index + 1).length
})

const categories = computed<Category[]>(() => {
  const rawCategories = Array.isArray(content.value.categories) ? content.value.categories : []
  return rawCategories
    .map((item: any, index: number) => ({
      id: String(item.id || `category_${index + 1}`),
      label: String(item.label || item.name || ''),
    }))
    .filter((item: Category) => item.label)
})

const categoryItems = computed<CategoryItem[]>(() => {
  const rawItems = Array.isArray(content.value.items) ? content.value.items : []
  const categoryIds = new Set(categories.value.map((item) => item.id))
  return rawItems
    .map((item: any, index: number) => ({
      id: String(item.id || `item_${index + 1}`),
      text: String(item.text || item.title || item.label || ''),
      category_id: String(item.category_id || item.categoryId || ''),
    }))
    .filter((item: CategoryItem) => item.text && categoryIds.has(item.category_id))
})

const categoryScore = computed(() => {
  return categoryItems.value.filter((item) => categoryAnswers.value[item.id] === item.category_id).length
})

const categoryComplete = computed(() => {
  return categoryItems.value.length > 0 && categoryItems.value.every((item) => categoryAnswers.value[item.id])
})

const reflectionQuestions = computed<string[]>(() => {
  const rawQuestions = Array.isArray(content.value.questions) ? content.value.questions : []
  return rawQuestions.map(String).filter(Boolean)
})

const reflectionCompleteCount = computed(() => {
  const minLength = Number(content.value.min_length || content.value.minLength || 0)
  return reflectionQuestions.value.filter((_, index) => String(reflectionAnswers.value[index] || '').trim().length >= minLength).length
})

watch(orderingItems, (items) => {
  orderedIds.value = items.map((item) => item.id)
}, { immediate: true })

function selectLeft(id: string) {
  submitted.value = false
  selectedLeft.value = selectedLeft.value === id ? '' : id
}

function selectRight(id: string) {
  if (!selectedLeft.value) return
  submitted.value = false
  answers.value = {
    ...answers.value,
    [selectedLeft.value]: id,
  }
  selectedLeft.value = ''
}

function rightLabelFor(leftId: string): string {
  const selectedRightId = answers.value[leftId]
  return rightOptions.value.find((item) => item.id === selectedRightId)?.label || '未选择'
}

function submit() {
  submitted.value = true
}

function reset() {
  answers.value = {}
  categoryAnswers.value = {}
  selectedCategoryItem.value = ''
  reflectionAnswers.value = {}
  orderedIds.value = orderingItems.value.map((item) => item.id)
  selectedLeft.value = ''
  submitted.value = false
}

function moveOrderingItem(id: string, direction: -1 | 1) {
  const index = orderedIds.value.indexOf(id)
  const nextIndex = index + direction
  if (index < 0 || nextIndex < 0 || nextIndex >= orderedIds.value.length) return
  const next = [...orderedIds.value]
  const current = next[index]!
  next[index] = next[nextIndex]!
  next[nextIndex] = current
  orderedIds.value = next
  submitted.value = false
}

function selectCategoryItem(id: string) {
  submitted.value = false
  selectedCategoryItem.value = selectedCategoryItem.value === id ? '' : id
}

function assignCategory(categoryId: string) {
  if (!selectedCategoryItem.value) return
  submitted.value = false
  categoryAnswers.value = {
    ...categoryAnswers.value,
    [selectedCategoryItem.value]: categoryId,
  }
  selectedCategoryItem.value = ''
}

function categoryLabelFor(itemId: string): string {
  const categoryId = categoryAnswers.value[itemId]
  return categories.value.find((item) => item.id === categoryId)?.label || '未分类'
}

function setReflectionAnswer(index: number, value: string) {
  submitted.value = false
  reflectionAnswers.value = {
    ...reflectionAnswers.value,
    [index]: value,
  }
}
</script>

<template>
  <section class="interactive-view" aria-label="互动练习">
    <div v-if="content.widget_type === 'matching'" class="matching-widget">
      <header class="interactive-head">
        <span class="interactive-kicker">互动匹配</span>
        <h3>{{ scene.title }}</h3>
        <p>{{ prompt }}</p>
      </header>

      <div class="matching-grid">
        <div class="matching-column">
          <div class="column-title">概念</div>
          <button
            v-for="item in pairs"
            :key="item.id"
            class="match-card left-card"
            :class="{ selected: selectedLeft === item.id, answered: Boolean(answers[item.id]) }"
            :data-test="`matching-left-${item.id}`"
            type="button"
            @click="selectLeft(item.id)"
          >
            <span>{{ item.left }}</span>
            <small>{{ rightLabelFor(item.id) }}</small>
          </button>
        </div>

        <div class="matching-column">
          <div class="column-title">解释 / 例子</div>
          <button
            v-for="item in rightOptions"
            :key="item.id"
            class="match-card right-card"
            :class="{ selected: Object.values(answers).includes(item.id) }"
            :data-test="`matching-right-${item.id}`"
            type="button"
            @click="selectRight(item.id)"
          >
            {{ item.label }}
          </button>
        </div>
      </div>

      <footer class="interactive-actions">
        <button class="secondary-btn" type="button" @click="reset">重置</button>
        <button
          class="primary-btn"
          data-test="matching-submit"
          type="button"
          :disabled="!matchingComplete"
          @click="submit"
        >
          提交
        </button>
      </footer>

      <div v-if="submitted" class="matching-result" role="status" aria-live="polite">
        <strong>{{ matchingScore }} / {{ pairs.length }}</strong>
        <span>{{ matchingScore === pairs.length ? '全部匹配正确' : '继续调整匹配关系' }}</span>
      </div>
    </div>

    <div v-else-if="content.widget_type === 'ordering'" class="matching-widget">
      <header class="interactive-head">
        <span class="interactive-kicker">互动排序</span>
        <h3>{{ scene.title }}</h3>
        <p>{{ prompt }}</p>
      </header>

      <ol class="ordering-list">
        <li
          v-for="(item, index) in orderedItems"
          :key="item.id"
          class="ordering-item"
        >
          <span class="ordering-index">{{ index + 1 }}</span>
          <span class="ordering-text">{{ item.text }}</span>
          <span class="ordering-controls">
            <button
              type="button"
              :data-test="`ordering-up-${item.id}`"
              :disabled="index === 0"
              @click="moveOrderingItem(item.id, -1)"
            >
              ↑
            </button>
            <button
              type="button"
              :data-test="`ordering-down-${item.id}`"
              :disabled="index === orderedItems.length - 1"
              @click="moveOrderingItem(item.id, 1)"
            >
              ↓
            </button>
          </span>
        </li>
      </ol>

      <footer class="interactive-actions">
        <button class="secondary-btn" type="button" @click="reset">重置</button>
        <button
          class="primary-btn"
          data-test="ordering-submit"
          type="button"
          @click="submit"
        >
          提交
        </button>
      </footer>

      <div v-if="submitted" class="matching-result" role="status" aria-live="polite">
        <strong>{{ orderingScore }} / {{ orderedItems.length }}</strong>
        <span>{{ orderingScore === orderedItems.length ? '顺序完全正确' : '继续调整顺序' }}</span>
      </div>
    </div>

    <div v-else-if="content.widget_type === 'html' && htmlContent" class="html-widget">
      <header class="interactive-head">
        <span class="interactive-kicker">HTML 互动</span>
        <h3>{{ scene.title }}</h3>
        <p>{{ prompt }}</p>
      </header>
      <InteractivePlayer
        :html-content="htmlContent"
        :current-topic="scene.title"
      />
    </div>

    <div v-else-if="content.widget_type === 'category_sort'" class="matching-widget">
      <header class="interactive-head">
        <span class="interactive-kicker">互动分类</span>
        <h3>{{ scene.title }}</h3>
        <p>{{ prompt }}</p>
      </header>

      <div class="matching-grid">
        <div class="matching-column">
          <div class="column-title">待分类项目</div>
          <button
            v-for="item in categoryItems"
            :key="item.id"
            class="match-card"
            :class="{ selected: selectedCategoryItem === item.id, answered: Boolean(categoryAnswers[item.id]) }"
            :data-test="`category-item-${item.id}`"
            type="button"
            @click="selectCategoryItem(item.id)"
          >
            <span>{{ item.text }}</span>
            <small>{{ categoryLabelFor(item.id) }}</small>
          </button>
        </div>
        <div class="matching-column">
          <div class="column-title">类别</div>
          <button
            v-for="category in categories"
            :key="category.id"
            class="match-card right-card"
            :data-test="`category-target-${category.id}`"
            type="button"
            @click="assignCategory(category.id)"
          >
            {{ category.label }}
          </button>
        </div>
      </div>

      <footer class="interactive-actions">
        <button class="secondary-btn" type="button" @click="reset">重置</button>
        <button
          class="primary-btn"
          data-test="category-submit"
          type="button"
          :disabled="!categoryComplete"
          @click="submit"
        >
          提交
        </button>
      </footer>

      <div v-if="submitted" class="matching-result" role="status" aria-live="polite">
        <strong>{{ categoryScore }} / {{ categoryItems.length }}</strong>
        <span>{{ categoryScore === categoryItems.length ? '分类全部正确' : '继续调整分类' }}</span>
      </div>
    </div>

    <div v-else-if="content.widget_type === 'reflection'" class="matching-widget">
      <header class="interactive-head">
        <span class="interactive-kicker">学习反思</span>
        <h3>{{ scene.title }}</h3>
        <p>{{ prompt }}</p>
      </header>

      <div class="reflection-list">
        <label
          v-for="(question, index) in reflectionQuestions"
          :key="`${question}-${index}`"
          class="reflection-item"
        >
          <span>{{ question }}</span>
          <textarea
            :data-test="`reflection-answer-${index}`"
            :value="reflectionAnswers[index] || ''"
            rows="3"
            @input="setReflectionAnswer(index, ($event.target as HTMLTextAreaElement).value)"
          />
        </label>
      </div>

      <footer class="interactive-actions">
        <button class="secondary-btn" type="button" @click="reset">重置</button>
        <button
          class="primary-btn"
          data-test="reflection-submit"
          type="button"
          @click="submit"
        >
          提交
        </button>
      </footer>

      <div v-if="submitted" class="matching-result" role="status" aria-live="polite">
        <strong>{{ reflectionCompleteCount }} / {{ reflectionQuestions.length }}</strong>
        <span>{{ reflectionCompleteCount === reflectionQuestions.length ? '反思已记录' : '还有问题需要补充' }}</span>
      </div>
    </div>

    <div v-else class="unsupported-widget" role="status" aria-live="polite">
      <h3>{{ scene.title }}</h3>
      <p>{{ content.display_message || '此互动内容暂以说明卡展示，当前组件类型尚未启用。' }}</p>
    </div>
  </section>
</template>

<style scoped>
.interactive-view {
  width: min(760px, 100%);
  margin: 0 auto;
  color: #1f2937;
}

.matching-widget,
.html-widget,
.unsupported-widget {
  border: 1px solid #d8dee8;
  border-radius: 8px;
  background: #fff;
  padding: 20px;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
}

.interactive-head {
  margin-bottom: 16px;
}

.interactive-kicker {
  display: block;
  color: #2563eb;
  font-size: 13px;
  font-weight: 700;
  margin-bottom: 4px;
}

.interactive-head h3,
.unsupported-widget h3 {
  margin: 0 0 8px;
  font-size: 22px;
}

.interactive-head p,
.unsupported-widget p {
  margin: 0;
  color: #4b5563;
  line-height: 1.6;
}

.matching-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 16px;
}

.column-title {
  margin-bottom: 8px;
  color: #475569;
  font-size: 13px;
  font-weight: 700;
}

.match-card {
  width: 100%;
  min-height: 58px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #f8fafc;
  color: #111827;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  gap: 4px;
  margin-bottom: 8px;
  padding: 10px 12px;
  text-align: left;
  cursor: pointer;
}

.match-card:hover,
.match-card.selected {
  border-color: #2563eb;
  background: #eff6ff;
}

.match-card.answered small {
  color: #2563eb;
}

.match-card small {
  color: #64748b;
}

.interactive-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 12px;
}

.primary-btn,
.secondary-btn {
  border-radius: 8px;
  border: 1px solid transparent;
  padding: 8px 14px;
  font-weight: 700;
}

.primary-btn {
  background: #2563eb;
  color: #fff;
}

.primary-btn:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}

.secondary-btn {
  border-color: #cbd5e1;
  background: #fff;
  color: #334155;
}

.matching-result {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
  padding: 12px;
  border-radius: 8px;
  background: #ecfdf5;
  color: #166534;
}

.ordering-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.ordering-item {
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  align-items: center;
  gap: 10px;
  min-height: 54px;
  padding: 10px 12px;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  background: #f8fafc;
}

.ordering-index {
  width: 28px;
  height: 28px;
  border-radius: 999px;
  background: #dbeafe;
  color: #1d4ed8;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}

.ordering-text {
  min-width: 0;
}

.ordering-controls {
  display: inline-flex;
  gap: 6px;
}

.ordering-controls button {
  width: 30px;
  height: 30px;
  border: 1px solid #cbd5e1;
  border-radius: 6px;
  background: #fff;
  color: #334155;
  font-weight: 700;
}

.ordering-controls button:disabled {
  color: #94a3b8;
  cursor: not-allowed;
}

.reflection-list {
  display: grid;
  gap: 12px;
}

.reflection-item {
  display: grid;
  gap: 8px;
  color: #334155;
  font-weight: 700;
}

.reflection-item textarea {
  width: 100%;
  resize: vertical;
  border: 1px solid #cbd5e1;
  border-radius: 8px;
  padding: 10px 12px;
  font: inherit;
  color: #111827;
  background: #f8fafc;
}

@media (max-width: 720px) {
  .matching-grid {
    grid-template-columns: 1fr;
  }
}
</style>
