<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import {
  NSpin, NAlert, NTag, NSelect, NInput, NInputNumber,
  NSpace, NButton, NIcon,
  useMessage,
} from 'naive-ui'
import { SaveOutline } from '@vicons/ionicons5'
import {
  getFeatureToggles,
  updateFeatureToggles,
  getJudgeEnabledSkills,
  type FeatureToggleItem,
  type JudgeEnabledSkill,
} from '../../api/settings'

const message = useMessage()

const toggleLoading = ref(false)
const toggleSaving = ref(false)
const featureToggles = ref<Record<string, FeatureToggleItem>>({})
const toggleEdits = reactive<Record<string, string>>({})
const originalToggles = ref<string>('')

const hasToggleChanges = computed(() => {
  return JSON.stringify(toggleEdits) !== originalToggles.value
})

const TOGGLE_STATE_LABELS: Record<string, { label: string; type: 'default' | 'warning' | 'success' }> = {
  off: { label: '关闭', type: 'default' },
  shadow: { label: '影子模式', type: 'warning' },
  enforce: { label: '执行模式', type: 'success' },
}

const judgeEnabledSkills = ref<JudgeEnabledSkill[]>([])

async function loadJudgeEnabledSkills() {
  try {
    judgeEnabledSkills.value = await getJudgeEnabledSkills()
  } catch (_e) {
    // 加载失败不影响主流程
  }
}

async function loadFeatureToggles() {
  toggleLoading.value = true
  try {
    const res = await getFeatureToggles()
    featureToggles.value = res
    for (const [key, item] of Object.entries(res)) {
      toggleEdits[key] = String(item.value)
    }
    originalToggles.value = JSON.stringify(toggleEdits)
    loadJudgeEnabledSkills()
  } catch (e: any) {
    message.error(e.message || '加载功能开关失败')
  } finally {
    toggleLoading.value = false
  }
}

async function saveFeatureToggles() {
  toggleSaving.value = true
  try {
    const changes: Record<string, string> = {}
    const original = JSON.parse(originalToggles.value)
    for (const key of Object.keys(toggleEdits)) {
      if (toggleEdits[key] !== original[key]) {
        changes[key] = toggleEdits[key]
      }
    }
    if (Object.keys(changes).length === 0) {
      message.info('没有变更')
      return
    }
    const res = await updateFeatureToggles(changes)
    originalToggles.value = JSON.stringify(toggleEdits)
    message.success(res.message)
  } catch (e: any) {
    message.error(e.message || '保存功能开关失败')
  } finally {
    toggleSaving.value = false
  }
}

function resetToggles() {
  const original = JSON.parse(originalToggles.value)
  Object.assign(toggleEdits, original)
  message.info('已重置功能开关')
}

onMounted(() => { loadFeatureToggles() })
</script>

<template>
  <n-spin :show="toggleLoading">
    <div class="tab-content">
      <n-alert type="info" title="功能开关说明" style="margin-bottom: 16px">
        <p style="margin: 0 0 6px 0">管理系统的功能开关。修改后<strong>立即生效</strong>，无需重启服务。</p>
        <p style="margin: 0">
          每个开关有三种状态：
          <n-tag size="small">关闭</n-tag> 完全不执行 →
          <n-tag size="small" type="warning">影子模式</n-tag> 后台执行但不影响用户看到的结果，用于安全验证 →
          <n-tag size="small" type="success">执行模式</n-tag> 正式生效。
          建议新功能按 关闭 → 影子 → 执行 的顺序逐步上线。
        </p>
      </n-alert>

      <!-- AI 质量验证（LLM Judge） -->
      <div class="toggle-card toggle-card--primary" v-if="featureToggles.llm_judge">
        <div class="toggle-header">
          <span class="toggle-label">AI 质量验证（LLM Judge）</span>
          <n-tag size="small" :type="TOGGLE_STATE_LABELS[toggleEdits.llm_judge]?.type || 'default'">
            {{ TOGGLE_STATE_LABELS[toggleEdits.llm_judge]?.label || toggleEdits.llm_judge }}
          </n-tag>
        </div>
        <div class="toggle-desc" style="margin-bottom: 10px">
          Skill 执行完成后，由 LLM 对输出内容按多个维度（准确性、完整性等）自动打分。
          需要先在此开启全局开关，然后到「Skill 管理 → 编辑 → 质量验证」Tab 配置具体 Skill 的评分维度。
        </div>
        <div class="toggle-hint">
          <strong>关闭：</strong>不进行 AI 评分，仅保留基础规则检查（零额外 token 消耗）<br/>
          <strong>影子模式：</strong>AI 评分在后台运行并记录分数，但用户端<strong>不展示</strong>评分卡片。适合上线初期观察评分质量<br/>
          <strong>执行模式：</strong>AI 评分结果展示在用户对话中，低于阈值的输出会显示质量告警
        </div>
        <div style="margin-top: 10px">
          <n-select
            :value="toggleEdits.llm_judge"
            @update:value="(v: string) => toggleEdits.llm_judge = v"
            :options="[
              { label: '关闭 (OFF)', value: 'off' },
              { label: '影子模式 (SHADOW) — 推荐先用此模式观察', value: 'shadow' },
              { label: '执行模式 (ENFORCE)', value: 'enforce' },
            ]"
            style="width: 340px"
          />
        </div>
        <!-- 子配置 -->
        <div class="toggle-sub" v-if="toggleEdits.llm_judge !== 'off'">
          <div class="toggle-sub-item">
            <span class="toggle-sub-label">评分模型</span>
            <span class="toggle-sub-desc">用于 AI 质量评分的 LLM，建议选择推理能力强且成本适中的模型</span>
            <n-input
              :value="toggleEdits.llm_judge_model"
              @update:value="(v: string) => toggleEdits.llm_judge_model = v"
              style="width: 260px; margin-top: 4px"
            />
          </div>
          <div class="toggle-sub-item">
            <span class="toggle-sub-label">评分超时（秒）</span>
            <span class="toggle-sub-desc">超时后自动放行，不阻断 Skill 输出</span>
            <n-input-number
              :value="Number(toggleEdits.llm_judge_timeout)"
              @update:value="(v: number | null) => toggleEdits.llm_judge_timeout = String(v ?? 10)"
              :min="1" :max="60"
              style="width: 120px; margin-top: 4px"
            />
          </div>
        </div>
        <!-- 已启用质检的 Skill 一览 -->
        <div class="judge-skills-overview" v-if="toggleEdits.llm_judge !== 'off'">
          <div class="judge-skills-title">已配置质检的 Skill</div>
          <div v-if="judgeEnabledSkills.length === 0" class="judge-skills-empty">
            暂无 Skill 配置了 AI 质检。请到「Skill 管理 → 编辑 → 质量验证」中配置。
          </div>
          <div v-else class="judge-skills-list">
            <div v-for="sk in judgeEnabledSkills" :key="sk.name" class="judge-skill-item">
              <span class="judge-skill-name">{{ sk.display_name }}</span>
              <n-tag size="tiny" :type="sk.is_enabled ? 'success' : 'default'">
                {{ sk.is_enabled ? '已启用' : '已禁用' }}
              </n-tag>
              <span class="judge-skill-meta">{{ sk.dimension_count }} 个维度 / 阈值 {{ sk.min_score }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="toggle-card" v-if="featureToggles.quality_filter">
        <div class="toggle-header">
          <span class="toggle-label">搜索质量过滤</span>
          <n-tag size="small" :type="TOGGLE_STATE_LABELS[toggleEdits.quality_filter]?.type || 'default'">
            {{ TOGGLE_STATE_LABELS[toggleEdits.quality_filter]?.label || toggleEdits.quality_filter }}
          </n-tag>
        </div>
        <div class="toggle-desc">
          对深度研究等 Skill 的搜索结果，按数据源权威性（学术数据库 > 百科 > 一般网页）自动过滤低质量条目。
          过滤后至少保留 3 条结果，不会导致搜索结果为空。
        </div>
        <div style="margin-top: 8px">
          <n-select
            :value="toggleEdits.quality_filter"
            @update:value="(v: string) => toggleEdits.quality_filter = v"
            :options="[
              { label: '关闭 (OFF)', value: 'off' },
              { label: '影子模式 (SHADOW)', value: 'shadow' },
              { label: '执行模式 (ENFORCE)', value: 'enforce' },
            ]"
            style="width: 240px"
          />
        </div>
      </div>

      <div class="toggle-card" v-if="featureToggles.negative_signals">
        <div class="toggle-header">
          <span class="toggle-label">负面信号权重</span>
          <n-tag size="small" :type="TOGGLE_STATE_LABELS[toggleEdits.negative_signals]?.type || 'default'">
            {{ TOGGLE_STATE_LABELS[toggleEdits.negative_signals]?.label || toggleEdits.negative_signals }}
          </n-tag>
        </div>
        <div class="toggle-desc">
          防止意图误判。例如用户输入"搜索 XXX 的论文"时，如果被错误路由到"深度研究"Skill（生成类），
          此机制会降低生成类 Skill 的匹配得分，让搜索类 Skill 优先命中。
        </div>
        <div style="margin-top: 8px">
          <n-select
            :value="toggleEdits.negative_signals"
            @update:value="(v: string) => toggleEdits.negative_signals = v"
            :options="[
              { label: '关闭 (OFF)', value: 'off' },
              { label: '影子模式 (SHADOW)', value: 'shadow' },
              { label: '执行模式 (ENFORCE)', value: 'enforce' },
            ]"
            style="width: 240px"
          />
        </div>
      </div>

      <div class="toggle-card" v-if="featureToggles.importance_compression">
        <div class="toggle-header">
          <span class="toggle-label">重要性压缩</span>
          <n-tag size="small" :type="TOGGLE_STATE_LABELS[toggleEdits.importance_compression]?.type || 'default'">
            {{ TOGGLE_STATE_LABELS[toggleEdits.importance_compression]?.label || toggleEdits.importance_compression }}
          </n-tag>
        </div>
        <div class="toggle-desc">
          在 Skill 执行过程中，根据内容重要性对上下文进行差异化压缩：
          引用、数据等高价值内容保留完整，寒暄等低价值内容优先压缩。减少 token 消耗的同时保留关键信息。
        </div>
        <div style="margin-top: 8px">
          <n-select
            :value="toggleEdits.importance_compression"
            @update:value="(v: string) => toggleEdits.importance_compression = v"
            :options="[
              { label: '关闭 (OFF)', value: 'off' },
              { label: '影子模式 (SHADOW)', value: 'shadow' },
              { label: '执行模式 (ENFORCE)', value: 'enforce' },
            ]"
            style="width: 240px"
          />
        </div>
      </div>

      <div class="toggle-card" v-if="featureToggles.query_rewrite">
        <div class="toggle-header">
          <span class="toggle-label">查询改写（代词消解）</span>
          <n-tag size="small" :type="TOGGLE_STATE_LABELS[toggleEdits.query_rewrite]?.type || 'default'">
            {{ TOGGLE_STATE_LABELS[toggleEdits.query_rewrite]?.label || toggleEdits.query_rewrite }}
          </n-tag>
        </div>
        <div class="toggle-desc">
          多轮对话中自动消解指代词、口语书面化，改善 Skill 理解准确度。
        </div>
        <div class="toggle-hint">
          <strong>关闭：</strong>不进行查询改写，用户输入原样传递（零额外 token 消耗）<br/>
          <strong>影子模式：</strong>后台执行改写并记录日志，但不替换原始输入、不展示提示条。适合观察改写质量和准确率<br/>
          <strong>执行模式：</strong>改写结果替换原始输入，用户端展示"查询已优化"提示条
        </div>
        <div style="margin-top: 10px">
          <n-select
            :value="toggleEdits.query_rewrite"
            @update:value="(v: string) => toggleEdits.query_rewrite = v"
            :options="[
              { label: '关闭 (OFF)', value: 'off' },
              { label: '影子模式 (SHADOW) — 推荐先用此模式观察', value: 'shadow' },
              { label: '执行模式 (ENFORCE)', value: 'enforce' },
            ]"
            style="width: 340px"
          />
        </div>
      </div>

      <!-- 编排器超时配置 -->
      <div class="toggle-card" style="margin-top: 16px">
        <div class="toggle-header">
          <span class="toggle-label">编排器超时配置</span>
          <n-tag size="small" type="info">系统参数</n-tag>
        </div>
        <div class="toggle-desc">
          控制不同执行模式的全局超时时间。修改后立即生效。
        </div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 12px">
          <div>
            <div class="toggle-sub-label">普通模式超时(秒)</div>
            <div class="toggle-sub-desc">Skill / QA 模式的全局超时，范围 60-600</div>
            <n-input-number
              :value="Number(toggleEdits.orchestrator_global_timeout || 300)"
              @update:value="(v: number | null) => toggleEdits.orchestrator_global_timeout = String(v ?? 300)"
              :min="60" :max="600"
              style="width: 100%; margin-top: 4px"
            />
          </div>
          <div>
            <div class="toggle-sub-label">Plan 模式超时(秒)</div>
            <div class="toggle-sub-desc">5 阶段规划模式，范围 300-1800</div>
            <n-input-number
              :value="Number(toggleEdits.orchestrator_plan_timeout || 900)"
              @update:value="(v: number | null) => toggleEdits.orchestrator_plan_timeout = String(v ?? 900)"
              :min="300" :max="1800"
              style="width: 100%; margin-top: 4px"
            />
          </div>
          <div>
            <div class="toggle-sub-label">流水线超时(秒)</div>
            <div class="toggle-sub-desc">结构化流水线模式，范围 600-3600</div>
            <n-input-number
              :value="Number(toggleEdits.orchestrator_pipeline_timeout || 1200)"
              @update:value="(v: number | null) => toggleEdits.orchestrator_pipeline_timeout = String(v ?? 1200)"
              :min="600" :max="3600"
              style="width: 100%; margin-top: 4px"
            />
          </div>
          <div>
            <div class="toggle-sub-label">记忆检索超时(秒)</div>
            <div class="toggle-sub-desc">Viking Memory 检索超时，范围 1-10</div>
            <n-input-number
              :value="Number(toggleEdits.orchestrator_memory_timeout || 3)"
              @update:value="(v: number | null) => toggleEdits.orchestrator_memory_timeout = String(v ?? 3)"
              :min="1" :max="10" :step="0.5"
              style="width: 100%; margin-top: 4px"
            />
          </div>
        </div>
      </div>

      <n-space style="margin-top: 20px">
        <n-button @click="resetToggles" :disabled="!hasToggleChanges">
          重置
        </n-button>
        <n-button type="primary" :loading="toggleSaving" :disabled="!hasToggleChanges" @click="saveFeatureToggles">
          <template #icon>
            <n-icon :component="SaveOutline" />
          </template>
          保存功能开关
        </n-button>
      </n-space>

    </div>
  </n-spin>
</template>

<style scoped>
.toggle-card {
  padding: 14px 16px;
  border-radius: 8px;
  background: #f9f9f9;
  border-left: 3px solid #e0e0e0;
  margin-bottom: 10px;
}
.toggle-card--primary {
  border-left-color: #2080f0;
  background: #f5f9ff;
}
.toggle-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.toggle-label {
  font-weight: 600;
  font-size: 14px;
}
.toggle-desc {
  font-size: 12px;
  color: #666;
  line-height: 1.6;
}
.toggle-hint {
  font-size: 12px;
  color: #888;
  line-height: 1.8;
  padding: 8px 12px;
  background: rgba(0,0,0,0.02);
  border-radius: 4px;
  margin-top: 6px;
}
.toggle-sub {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed #e0e0e0;
  display: flex;
  gap: 24px;
}
.toggle-sub-item {
  display: flex;
  flex-direction: column;
}
.toggle-sub-label {
  font-size: 13px;
  font-weight: 500;
  color: #333;
}
.toggle-sub-desc {
  font-size: 11px;
  color: #999;
}
.judge-skills-overview {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px dashed #e0e0e0;
}
.judge-skills-title {
  font-size: 13px;
  font-weight: 500;
  color: #333;
  margin-bottom: 8px;
}
.judge-skills-empty {
  font-size: 12px;
  color: #999;
  padding: 8px 12px;
  background: rgba(0,0,0,0.02);
  border-radius: 4px;
}
.judge-skills-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.judge-skill-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  background: #fff;
  border-radius: 4px;
  border: 1px solid #eee;
  font-size: 13px;
}
.judge-skill-name {
  font-weight: 500;
}
.judge-skill-meta {
  font-size: 11px;
  color: #999;
  margin-left: auto;
}
</style>
