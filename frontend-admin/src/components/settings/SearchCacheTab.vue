<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import {
  NSpin, NAlert, NCard, NSwitch, NForm, NFormItem, NInputNumber,
  NSpace, NButton, NText, NGrid, NGridItem, NPopconfirm,
  useMessage,
} from 'naive-ui'
import {
  getSearchCacheConfig,
  updateSearchCacheConfig,
  invalidateSearchCache,
  getSearchCacheStats,
  type SearchCacheConfig,
  type SearchCacheStats,
} from '../../api/settings'

const message = useMessage()

const searchCacheLoading = ref(false)
const searchCacheSaving = ref(false)
const searchCacheConfig = reactive<SearchCacheConfig>({
  enabled: true,
  ttl_web: 3600,
  ttl_academic: 21600,
  ttl_wikipedia: 86400,
  ttl_web_fetch: 7200,
})
const originalSearchCacheConfig = ref('')
const searchCacheStats = ref<SearchCacheStats>({ available: false })
const searchCacheInvalidating = ref<string | null>(null)

const searchCacheHasChanges = computed(() => {
  return JSON.stringify(searchCacheConfig) !== originalSearchCacheConfig.value
})

const cacheSourceLabels: Record<string, string> = {
  web_search: '网络搜索',
  academic_search: '学术搜索',
  wikipedia_search: '维基百科搜索',
  wikipedia_article: '维基百科文章',
  web_fetch: '网页抓取',
}

function formatTtl(seconds: number): string {
  if (seconds >= 86400) return `${seconds / 86400} 天`
  if (seconds >= 3600) return `${seconds / 3600} 小时`
  return `${seconds / 60} 分钟`
}

async function loadSearchCacheConfig() {
  searchCacheLoading.value = true
  try {
    const res = await getSearchCacheConfig()
    Object.assign(searchCacheConfig, res)
    originalSearchCacheConfig.value = JSON.stringify(searchCacheConfig)
    searchCacheStats.value = await getSearchCacheStats()
  } catch (e: any) {
    message.error(e.message || '加载搜索缓存配置失败')
  } finally {
    searchCacheLoading.value = false
  }
}

async function saveSearchCacheConfig() {
  searchCacheSaving.value = true
  try {
    const original = JSON.parse(originalSearchCacheConfig.value)
    const changes: Partial<SearchCacheConfig> = {}
    for (const key of Object.keys(searchCacheConfig) as (keyof SearchCacheConfig)[]) {
      if (searchCacheConfig[key] !== original[key]) {
        (changes as any)[key] = searchCacheConfig[key]
      }
    }
    if (Object.keys(changes).length === 0) {
      message.info('没有需要保存的更改')
      return
    }
    const res = await updateSearchCacheConfig(changes)
    originalSearchCacheConfig.value = JSON.stringify(searchCacheConfig)
    message.success(res.message)
  } catch (e: any) {
    message.error(e.message || '保存搜索缓存配置失败')
  } finally {
    searchCacheSaving.value = false
  }
}

async function handleInvalidateCache(toolName?: string) {
  const scope = toolName || 'all'
  searchCacheInvalidating.value = scope
  try {
    const res = await invalidateSearchCache(toolName)
    message.success(res.message)
    searchCacheStats.value = await getSearchCacheStats()
  } catch (e: any) {
    message.error(e.message || '清理缓存失败')
  } finally {
    searchCacheInvalidating.value = null
  }
}

onMounted(() => { loadSearchCacheConfig() })
</script>

<template>
  <n-spin :show="searchCacheLoading">
    <div class="tab-content">
      <!-- 缓存状态 -->
      <n-alert
        :type="searchCacheStats.available ? 'success' : 'warning'"
        style="margin-bottom: 20px"
      >
        <template v-if="searchCacheStats.available">
          Redis 连接正常 | 总缓存条目: {{ searchCacheStats.total ?? 0 }}
        </template>
        <template v-else>
          {{ searchCacheStats.message || 'Redis 不可用，缓存功能降级' }}
        </template>
      </n-alert>

      <!-- 总开关 -->
      <n-card size="small" style="margin-bottom: 16px">
        <div style="display: flex; align-items: center; justify-content: space-between">
          <div>
            <div style="font-weight: 600; font-size: 15px">搜索结果 Redis 缓存</div>
            <div style="font-size: 13px; color: #999; margin-top: 4px">
              开启后，相同搜索参数在 TTL 内直接返回缓存结果，减少外部 API 调用，加速综述生成中的并行搜索
            </div>
          </div>
          <n-switch v-model:value="searchCacheConfig.enabled" />
        </div>
      </n-card>

      <!-- TTL 配置 -->
      <n-card
        title="各数据源缓存过期时间 (TTL)"
        size="small"
        style="margin-bottom: 16px"
        :style="{ opacity: searchCacheConfig.enabled ? 1 : 0.5 }"
      >
        <n-form label-placement="left" label-width="220px" :disabled="!searchCacheConfig.enabled">
          <n-form-item label="网络搜索 (SearXNG / Tavily)">
            <n-space align="center">
              <n-input-number
                v-model:value="searchCacheConfig.ttl_web"
                :min="60"
                :max="604800"
                :step="60"
                style="width: 140px"
                :disabled="!searchCacheConfig.enabled"
              />
              <n-text depth="3">秒 = {{ formatTtl(searchCacheConfig.ttl_web) }}</n-text>
            </n-space>
            <template #feedback>搜索结果有时效性，建议 1-2 小时</template>
          </n-form-item>
          <n-form-item label="学术搜索 (S2 / arXiv / 万方 等)">
            <n-space align="center">
              <n-input-number
                v-model:value="searchCacheConfig.ttl_academic"
                :min="60"
                :max="604800"
                :step="60"
                style="width: 140px"
                :disabled="!searchCacheConfig.enabled"
              />
              <n-text depth="3">秒 = {{ formatTtl(searchCacheConfig.ttl_academic) }}</n-text>
            </n-space>
            <template #feedback>论文更新频率低，建议 6-24 小时</template>
          </n-form-item>
          <n-form-item label="维基百科">
            <n-space align="center">
              <n-input-number
                v-model:value="searchCacheConfig.ttl_wikipedia"
                :min="60"
                :max="604800"
                :step="60"
                style="width: 140px"
                :disabled="!searchCacheConfig.enabled"
              />
              <n-text depth="3">秒 = {{ formatTtl(searchCacheConfig.ttl_wikipedia) }}</n-text>
            </n-space>
            <template #feedback>百科内容极少变化，建议 24 小时</template>
          </n-form-item>
          <n-form-item label="网页抓取 (web_fetch)">
            <n-space align="center">
              <n-input-number
                v-model:value="searchCacheConfig.ttl_web_fetch"
                :min="60"
                :max="604800"
                :step="60"
                style="width: 140px"
                :disabled="!searchCacheConfig.enabled"
              />
              <n-text depth="3">秒 = {{ formatTtl(searchCacheConfig.ttl_web_fetch) }}</n-text>
            </n-space>
            <template #feedback>同一 URL 内容短期不变，建议 2-4 小时</template>
          </n-form-item>
        </n-form>
      </n-card>

      <!-- 缓存管理 -->
      <n-card title="缓存管理" size="small" style="margin-bottom: 16px">
        <template v-if="searchCacheStats.available && searchCacheStats.sources">
          <n-grid :cols="3" :x-gap="12" :y-gap="12" style="margin-bottom: 16px">
            <n-grid-item
              v-for="(info, toolName) in searchCacheStats.sources"
              :key="toolName"
            >
              <n-card size="small" embedded>
                <div style="font-size: 13px; font-weight: 500">
                  {{ cacheSourceLabels[toolName] || toolName }}
                </div>
                <div style="font-size: 20px; font-weight: 700; margin: 4px 0">
                  {{ info.count }}
                </div>
                <n-button
                  size="tiny"
                  type="warning"
                  secondary
                  :loading="searchCacheInvalidating === toolName"
                  @click="handleInvalidateCache(toolName)"
                >
                  清理此源
                </n-button>
              </n-card>
            </n-grid-item>
          </n-grid>
        </template>
        <n-space>
          <n-popconfirm @positive-click="handleInvalidateCache()">
            <template #trigger>
              <n-button
                type="error"
                secondary
                :loading="searchCacheInvalidating === 'all'"
              >
                清理全部缓存
              </n-button>
            </template>
            确认清理所有搜索缓存？清理后首次搜索响应时间将增加 2-5 秒。
          </n-popconfirm>
        </n-space>
        <div style="font-size: 12px; color: #999; margin-top: 8px">
          清理后，新请求将直接查询外部 API 并重新缓存
        </div>
      </n-card>

      <!-- 保存按钮 -->
      <n-space justify="end">
        <n-button
          type="primary"
          :loading="searchCacheSaving"
          :disabled="!searchCacheHasChanges"
          @click="saveSearchCacheConfig"
        >
          保存配置
        </n-button>
      </n-space>
    </div>
  </n-spin>
</template>
