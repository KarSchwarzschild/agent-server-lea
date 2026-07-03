<script setup lang="ts">
/**
 * 文档索引（PageIndex）配置组件
 */
import { ref, reactive, computed, onMounted } from 'vue'
import {
  NForm,
  NFormItem,
  NButton,
  NSpace,
  NAlert,
  NSpin,
  NInputNumber,
  NInput,
  NRadioGroup,
  NRadioButton,
  NCheckboxGroup,
  NCheckbox,
  NCollapse,
  NCollapseItem,
  NTag,
  useMessage,
} from 'naive-ui'
import {
  getPageIndexConfig,
  updatePageIndexConfig,
  type PageIndexConfig,
} from '../../api/settings'

const message = useMessage()

const piLoading = ref(false)
const piSaving = ref(false)

const piConfig = reactive<PageIndexConfig>({
  pageindex_enabled: 'off',
  pageindex_supported_exts: 'pdf,txt,md',
  pageindex_model: '',
  pageindex_search_model: '',
  pageindex_small_scope_threshold: 30,
  pageindex_hybrid_vector_top_k: 20,
  pageindex_hybrid_tree_max_docs: 3,
  pageindex_hybrid_final_top_n: 5,
  pageindex_max_concurrent_index: 3,
  pageindex_doc_timeout: 600,
  pageindex_llm_call_timeout: 30,
  pageindex_tree_search_max_depth: 3,
  pageindex_tree_search_confidence_threshold: 0.4,
  pageindex_root_relevance_threshold: 0.3,
  pageindex_cache_ttl: 3600,
})

const originalPiConfig = ref<string>('')
const hasPiChanges = computed(() => JSON.stringify(piConfig) !== originalPiConfig.value)
const isOff = computed(() => piConfig.pageindex_enabled === 'off')

// 引擎能力白名单
const ENGINE_EXTS = ['pdf', 'txt', 'md', 'markdown']

// Phase 3 混合检索功能（尚未上线，隐藏相关配置项）
const PHASE3_ENABLED = false

// 当前选中的扩展名数组
const selectedExts = computed({
  get: () => piConfig.pageindex_supported_exts.split(',').filter(Boolean),
  set: (vals: string[]) => {
    piConfig.pageindex_supported_exts = vals.join(',')
  },
})

async function loadPiConfig() {
  piLoading.value = true
  try {
    const data = await getPageIndexConfig()
    Object.assign(piConfig, data)
    originalPiConfig.value = JSON.stringify(piConfig)
  } catch (e: any) {
    message.error('加载文档索引配置失败: ' + (e.message || e))
  } finally {
    piLoading.value = false
  }
}

async function savePiConfig() {
  piSaving.value = true
  try {
    const current = JSON.parse(JSON.stringify(piConfig)) as PageIndexConfig
    const original = JSON.parse(originalPiConfig.value) as PageIndexConfig
    const changed: Partial<PageIndexConfig> = {}
    for (const key of Object.keys(current) as (keyof PageIndexConfig)[]) {
      if (current[key] !== original[key]) {
        ;(changed as any)[key] = current[key]
      }
    }
    if (Object.keys(changed).length === 0) {
      message.info('没有修改')
      return
    }
    const res = await updatePageIndexConfig(changed)
    message.success(res.message)
    originalPiConfig.value = JSON.stringify(piConfig)
  } catch (e: any) {
    message.error('保存失败: ' + (e.response?.data?.detail || e.message || e))
  } finally {
    piSaving.value = false
  }
}

function resetPiConfig() {
  const original = JSON.parse(originalPiConfig.value)
  Object.assign(piConfig, original)
}

onMounted(() => loadPiConfig())
</script>

<template>
  <n-spin :show="piLoading">
    <div class="pi-settings">
      <n-alert type="info" :bordered="false" style="margin-bottom: 16px">
        文档索引为上传文档构建树结构索引，支持按章节精准定位。修改后立即生效（并发数除外）。
      </n-alert>

      <n-form label-placement="left" label-width="160" :show-feedback="false">
        <!-- 基本配置 -->
        <n-form-item label="总开关">
          <n-radio-group v-model:value="piConfig.pageindex_enabled">
            <n-space vertical>
              <n-radio-button value="off">
                关闭
              </n-radio-button>
              <n-radio-button value="shadow">
                试运行
              </n-radio-button>
              <n-radio-button value="enforce">
                正式启用
              </n-radio-button>
            </n-space>
          </n-radio-group>
          <div class="field-desc">
            <template v-if="piConfig.pageindex_enabled === 'off'">完全关闭，不执行索引构建和树搜索</template>
            <template v-else-if="piConfig.pageindex_enabled === 'shadow'">执行但不影响对话结果，仅记录日志</template>
            <template v-else>执行并增强对话，用户可见精准定位效果</template>
          </div>
        </n-form-item>

        <n-form-item label="支持的文件类型">
          <n-checkbox-group v-model:value="selectedExts">
            <n-space>
              <n-checkbox v-for="ext in ENGINE_EXTS" :key="ext" :value="ext" :label="ext" />
            </n-space>
          </n-checkbox-group>
        </n-form-item>

        <n-form-item label="索引构建模型">
          <n-input v-model:value="piConfig.pageindex_model" placeholder="如 qwen3-30b-a3b" />
          <n-tag size="small" :bordered="false" type="info" style="margin-left: 8px; white-space: nowrap">新索引立即使用</n-tag>
        </n-form-item>

        <n-form-item label="树搜索模型">
          <n-input v-model:value="piConfig.pageindex_search_model" placeholder="如 qwen3-30b-a3b" />
          <n-tag size="small" :bordered="false" type="info" style="margin-left: 8px; white-space: nowrap">新搜索立即使用</n-tag>
        </n-form-item>

        <!-- 高级配置（折叠） -->
        <div :style="{ opacity: isOff ? 0.5 : 1, pointerEvents: isOff ? 'none' : 'auto' }">
          <n-collapse style="margin-top: 16px">
            <n-collapse-item title="高级配置（搜索 + 性能）" name="advanced">
              <!-- 搜索配置 -->
              <div class="section-title">搜索配置</div>
              <n-form-item label="树搜索最大深度">
                <n-input-number v-model:value="piConfig.pageindex_tree_search_max_depth" :min="1" :max="10" />
              </n-form-item>
              <n-form-item label="搜索置信度阈值">
                <n-input-number v-model:value="piConfig.pageindex_tree_search_confidence_threshold" :min="0" :max="1" :step="0.05" />
              </n-form-item>
              <n-form-item label="根节点相关性阈值">
                <n-input-number v-model:value="piConfig.pageindex_root_relevance_threshold" :min="0" :max="1" :step="0.05" />
              </n-form-item>
              <n-form-item label="缓存 TTL">
                <n-input-number v-model:value="piConfig.pageindex_cache_ttl" :min="0" :max="86400" :step="60" />
                <span class="field-suffix">秒</span>
              </n-form-item>

              <!-- 性能配置 -->
              <div class="section-title" style="margin-top: 16px">性能配置</div>
              <n-form-item label="最大并发索引数">
                <n-input-number v-model:value="piConfig.pageindex_max_concurrent_index" :min="1" :max="10" />
                <n-tag size="small" :bordered="false" type="warning" style="margin-left: 8px; white-space: nowrap">需重启生效</n-tag>
              </n-form-item>
              <n-form-item label="文档索引超时">
                <n-input-number v-model:value="piConfig.pageindex_doc_timeout" :min="60" :max="3600" :step="60" />
                <span class="field-suffix">秒</span>
              </n-form-item>
              <n-form-item label="LLM 调用超时">
                <n-input-number v-model:value="piConfig.pageindex_llm_call_timeout" :min="5" :max="120" :step="5" />
                <span class="field-suffix">秒</span>
              </n-form-item>
              <!-- Phase 3: 混合检索配置（尚未上线） -->
              <template v-if="PHASE3_ENABLED">
                <n-form-item label="小范围阈值">
                  <n-input-number v-model:value="piConfig.pageindex_small_scope_threshold" :min="1" :max="100" />
                  <span class="field-suffix">页</span>
                </n-form-item>
                <n-form-item label="向量检索 TopK">
                  <n-input-number v-model:value="piConfig.pageindex_hybrid_vector_top_k" :min="1" :max="100" />
                </n-form-item>
                <n-form-item label="树检索最大文档数">
                  <n-input-number v-model:value="piConfig.pageindex_hybrid_tree_max_docs" :min="1" :max="20" />
                </n-form-item>
                <n-form-item label="混合检索 TopN">
                  <n-input-number v-model:value="piConfig.pageindex_hybrid_final_top_n" :min="1" :max="20" />
                </n-form-item>
              </template>
            </n-collapse-item>
          </n-collapse>
        </div>
      </n-form>

      <!-- 操作按钮 -->
      <n-space style="margin-top: 20px">
        <n-button @click="resetPiConfig" :disabled="!hasPiChanges">重置</n-button>
        <n-button type="primary" @click="savePiConfig" :loading="piSaving" :disabled="!hasPiChanges">
          保存文档索引配置
        </n-button>
      </n-space>
    </div>
  </n-spin>
</template>

<style scoped>
.pi-settings {
  max-width: 700px;
}
.field-desc {
  margin-left: 12px;
  font-size: 12px;
  color: #999;
}
.field-suffix {
  margin-left: 8px;
  font-size: 13px;
  color: #666;
}
.section-title {
  font-size: 14px;
  font-weight: 600;
  color: #333;
  margin-bottom: 12px;
  padding-bottom: 4px;
  border-bottom: 1px solid #eee;
}
</style>
