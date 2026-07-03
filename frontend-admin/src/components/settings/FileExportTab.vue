<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import {
  NSpin, NAlert, NForm, NFormItem, NDynamicTags, NInput,
  NInputNumber, NSpace, NButton, NText, NIcon,
  useMessage,
} from 'naive-ui'
import { SaveOutline } from '@vicons/ionicons5'
import {
  getFileExportConfig,
  updateFileExportConfig,
  type FileExportConfig,
} from '../../api/settings'

const message = useMessage()

const fileExportLoading = ref(false)
const fileExportSaving = ref(false)
const fileExportConfig = reactive<FileExportConfig>({
  file_export_keywords: '',
  file_export_truncate_limit: 16000,
  file_export_prompt: '',
  file_export_patterns: '',
})
const originalFileExportConfig = ref('')

const fileExportKeywordTags = computed({
  get: () => fileExportConfig.file_export_keywords
    ? fileExportConfig.file_export_keywords.split(',').map(s => s.trim()).filter(Boolean)
    : [],
  set: (tags: string[]) => {
    fileExportConfig.file_export_keywords = tags.join(',')
  }
})

const fileExportPatternList = computed({
  get: () => fileExportConfig.file_export_patterns
    ? fileExportConfig.file_export_patterns.split('\n').filter(s => s.trim())
    : [],
  set: (list: string[]) => {
    fileExportConfig.file_export_patterns = list.join('\n')
  }
})

const fileExportRegexTestInput = ref('')
const fileExportRegexTestResults = computed(() => {
  const input = fileExportRegexTestInput.value.trim()
  if (!input) return []
  return fileExportPatternList.value.map((pattern, idx) => {
    if (!pattern.trim()) return { idx, pattern, match: false, error: '' }
    try {
      const re = new RegExp(pattern, 'i')
      const m = re.exec(input)
      return { idx, pattern, match: !!m, error: '', matched: m ? m[0] : '' }
    } catch (e: any) {
      return { idx, pattern, match: false, error: e.message }
    }
  })
})

const FILE_EXPORT_DEFAULTS: FileExportConfig = {
  file_export_keywords: '保存为,导出为,生成Word,生成word,生成PDF,生成pdf,生成Excel,生成excel,生成文档,写成文档,整理成文件,生成docx,保存文件,导出文件',
  file_export_truncate_limit: 16000,
  file_export_prompt: '用户要求将内容保存为文件，你必须调用 write_file 工具完成。文件格式根据用户要求选择后缀（.docx/.pdf/.md）。不要把内容直接输出到对话中。',
  file_export_patterns: '(保存|导出|存).{0,5}(为|成).{0,5}(word|pdf|文件|文档)\n(生成|做成|弄成).{0,5}(word|pdf|文件|文档)\n(save|export|download).{0,10}(as|to).{0,5}(word|pdf|docx|xlsx)\n(保存|导出|下载).{0,3}(文件|文档)\n(写入|写成|写到).{0,5}(文件|文档)',
}

async function loadFileExportConfig() {
  fileExportLoading.value = true
  try {
    const res = await getFileExportConfig()
    Object.assign(fileExportConfig, res)
    originalFileExportConfig.value = JSON.stringify(res)
  } catch (e: any) {
    message.error(e.message || '加载文件导出配置失败')
  } finally {
    fileExportLoading.value = false
  }
}

async function saveFileExportConfig() {
  fileExportSaving.value = true
  try {
    const res = await updateFileExportConfig({ ...fileExportConfig })
    originalFileExportConfig.value = JSON.stringify(fileExportConfig)
    message.success(res.message)
  } catch (e: any) {
    message.error(e.message || '保存文件导出配置失败')
  } finally {
    fileExportSaving.value = false
  }
}

function resetFileExportConfig() {
  Object.assign(fileExportConfig, JSON.parse(originalFileExportConfig.value))
  message.info('已重置文件导出配置')
}

function restoreFileExportDefaults() {
  Object.assign(fileExportConfig, { ...FILE_EXPORT_DEFAULTS })
  message.info('已恢复默认值（需保存后生效）')
}

onMounted(() => { loadFileExportConfig() })
</script>

<template>
  <n-spin :show="fileExportLoading">
    <div class="tab-content">
      <n-alert type="info" title="识别机制说明" style="margin-bottom: 16px">
        系统使用三层机制判断用户是否要求导出文件：<br/>
        1. <strong>Fast-path 关键词</strong> — 精确命中，零延迟（下方可配置）<br/>
        2. <strong>Fast-path 正则</strong> — 覆盖变体和多语言（下方可配置）<br/>
        3. <strong>LLM 意图识别</strong> — 理解隐晦表达（搭载在路由 LLM 中，无额外延迟）<br/><br/>
        命中任一层即启用文件导出。关键词和正则无需穷举所有可能的表达方式，LLM 会兜底处理。
        修改后<strong>立即生效</strong>，无需重启服务。
      </n-alert>

      <n-form label-placement="top">
        <n-form-item label="Fast-path 关键词">
          <n-dynamic-tags v-model:value="fileExportKeywordTags" />
          <template #feedback>
            命中即生效，零延迟。覆盖最常用的中文表达。点击标签右侧 x 删除，输入后按回车添加。
          </template>
        </n-form-item>

        <n-form-item label="Fast-path 正则模式">
          <n-space vertical style="width: 100%">
            <div
              v-for="(_, idx) in fileExportPatternList"
              :key="idx"
              style="display: flex; gap: 8px; align-items: center; width: 100%"
            >
              <n-input
                :value="fileExportPatternList[idx]"
                @update:value="(v: string) => { const list = [...fileExportPatternList]; list[idx] = v; fileExportPatternList = list }"
                placeholder="正则表达式，如：(保存|导出).{0,5}(word|pdf|文件)"
                style="flex: 1; font-family: monospace; font-size: 12px"
              />
              <n-button
                size="small" quaternary type="error"
                @click="() => { const list = [...fileExportPatternList]; list.splice(idx, 1); fileExportPatternList = list }"
              >删除</n-button>
            </div>
            <n-button
              size="small" dashed
              @click="() => { fileExportPatternList = [...fileExportPatternList, ''] }"
            >+ 添加正则</n-button>
            <div style="font-size: 12px; color: #999">
              每行一条正则表达式，覆盖口语变体和简写。全部删除则仅依赖关键词和 LLM 兜底。
            </div>
            <!-- 正则测试沙箱 -->
            <div v-if="fileExportPatternList.length > 0" style="margin-top: 8px; padding: 10px; background: #fafafa; border-radius: 6px; border: 1px solid #eee">
              <div style="font-size: 12px; color: #666; margin-bottom: 6px">测试沙箱</div>
              <n-input
                v-model:value="fileExportRegexTestInput"
                placeholder="输入一句话测试正则匹配效果"
                size="small"
              />
              <div v-if="fileExportRegexTestInput.trim() && fileExportRegexTestResults.length" style="margin-top: 6px">
                <div
                  v-for="r in fileExportRegexTestResults" :key="r.idx"
                  style="font-size: 12px; line-height: 1.8"
                >
                  <span v-if="r.error" style="color: #d03050">正则 #{{ r.idx + 1 }} 语法错误: {{ r.error }}</span>
                  <span v-else-if="r.match" style="color: #18a058">正则 #{{ r.idx + 1 }} 命中「{{ r.matched }}」</span>
                  <span v-else style="color: #999">正则 #{{ r.idx + 1 }} 未命中</span>
                </div>
              </div>
            </div>
          </n-space>
        </n-form-item>

        <n-form-item label="截断字符数限制">
          <n-space align="center">
            <n-input-number
              v-model:value="fileExportConfig.file_export_truncate_limit"
              :min="4000"
              :max="50000"
              :step="1000"
              style="width: 180px"
            />
            <n-text depth="3">字符</n-text>
          </n-space>
          <template #feedback>
            文件操作时 assistant 内容的截断上限。默认 16000。非文件场景固定 8000 不受此配置影响。
          </template>
        </n-form-item>

        <n-form-item label="导出强化提示词">
          <n-input
            v-model:value="fileExportConfig.file_export_prompt"
            type="textarea"
            :autosize="{ minRows: 3, maxRows: 6 }"
            placeholder="文件操作场景追加到 system prompt 的指令"
          />
          <template #feedback>
            仅在检测到文件操作关键词时追加到 system prompt，指导 LLM 调用 write_file 而非直接输出内容。
          </template>
        </n-form-item>
      </n-form>

      <n-space justify="end">
        <n-button @click="restoreFileExportDefaults">
          恢复默认
        </n-button>
        <n-button @click="resetFileExportConfig">
          重置修改
        </n-button>
        <n-button
          type="primary"
          :loading="fileExportSaving"
          @click="saveFileExportConfig"
        >
          <template #icon>
            <n-icon :component="SaveOutline" />
          </template>
          保存配置
        </n-button>
      </n-space>
    </div>
  </n-spin>
</template>
