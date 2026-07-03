<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import {
  NSpin, NAlert, NSwitch, NSelect, NInputNumber, NTag,
  NSpace, NButton, NIcon,
  useMessage,
} from 'naive-ui'
import { SaveOutline } from '@vicons/ionicons5'
import {
  getSandboxConfig,
  updateSandboxConfig,
  getSandboxEnabledSkills,
  type SandboxConfig,
  type SandboxEnabledSkill,
} from '../../api/settings'

const message = useMessage()

const sandboxLoading = ref(false)
const sandboxSaving = ref(false)
const sandboxConfig = reactive<SandboxConfig>({
  sandbox_enabled: false,
  sandbox_backend: 'docker',
  sandbox_timeout: 600,
  sandbox_memory_limit_mb: 512,
  sandbox_max_per_user: 3,
  sandbox_idle_timeout: 1800,
})
const originalSandboxConfig = ref<string>('')
const sandboxEnabledSkills = ref<SandboxEnabledSkill[]>([])

const hasSandboxChanges = computed(() => {
  return JSON.stringify(sandboxConfig) !== originalSandboxConfig.value
})

async function loadSandboxConfig() {
  sandboxLoading.value = true
  try {
    const res = await getSandboxConfig()
    Object.assign(sandboxConfig, res)
    originalSandboxConfig.value = JSON.stringify(sandboxConfig)
    loadSandboxEnabledSkills()
  } catch (e: any) {
    message.error(e.message || '加载沙箱配置失败')
  } finally {
    sandboxLoading.value = false
  }
}

async function loadSandboxEnabledSkills() {
  try {
    sandboxEnabledSkills.value = await getSandboxEnabledSkills()
  } catch (_e) {
    // 不影响主流程
  }
}

async function saveSandboxConfig() {
  sandboxSaving.value = true
  try {
    const original: SandboxConfig = JSON.parse(originalSandboxConfig.value)
    const changes: Partial<SandboxConfig> = {}
    for (const key of Object.keys(sandboxConfig) as (keyof SandboxConfig)[]) {
      if (sandboxConfig[key] !== original[key]) {
        (changes as any)[key] = sandboxConfig[key]
      }
    }
    if (Object.keys(changes).length === 0) {
      message.info('没有变更')
      return
    }
    const res = await updateSandboxConfig(changes)
    originalSandboxConfig.value = JSON.stringify(sandboxConfig)
    message.success(res.message)
    loadSandboxEnabledSkills()
  } catch (e: any) {
    message.error(e.message || '保存沙箱配置失败')
  } finally {
    sandboxSaving.value = false
  }
}

function resetSandboxConfig() {
  const original = JSON.parse(originalSandboxConfig.value)
  Object.assign(sandboxConfig, original)
  message.info('已重置沙箱配置')
}

onMounted(() => { loadSandboxConfig() })
</script>

<template>
  <n-spin :show="sandboxLoading">
    <div class="tab-content">
      <n-alert type="info" title="说明" style="margin-bottom: 16px">
        沙箱为 Skill 提供隔离的代码执行环境。开启后，配置了沙箱的 Skill 可安全执行代码、安装依赖、生成文件。
        修改后<strong>立即生效</strong>，无需重启服务。
      </n-alert>

      <div class="toggle-card" :class="{ 'toggle-card--sandbox': sandboxConfig.sandbox_enabled }">
        <div class="toggle-header">
          <span class="toggle-label">全局开关</span>
          <n-switch v-model:value="sandboxConfig.sandbox_enabled" />
        </div>
        <div class="toggle-desc">
          关闭后所有 Skill 的沙箱功能将被禁用，代码执行工具不可用。
        </div>
      </div>

      <template v-if="sandboxConfig.sandbox_enabled">
        <div class="sandbox-config-grid">
          <div class="toggle-card">
            <div class="toggle-sub-label">后端类型</div>
            <div class="toggle-sub-desc">沙箱运行时后端</div>
            <n-select
              v-model:value="sandboxConfig.sandbox_backend"
              :options="[
                { label: 'Local (本地进程)', value: 'local' },
                { label: 'Docker (容器隔离)', value: 'docker' },
                { label: 'K8s (Kubernetes Pod)', value: 'k8s' },
              ]"
              style="margin-top: 8px"
            />
          </div>
          <div class="toggle-card">
            <div class="toggle-sub-label">执行超时(秒)</div>
            <div class="toggle-sub-desc">单次执行最长时间，范围 10-3600</div>
            <n-input-number
              v-model:value="sandboxConfig.sandbox_timeout"
              :min="10" :max="3600"
              style="width: 100%; margin-top: 8px"
            />
          </div>
          <div class="toggle-card">
            <div class="toggle-sub-label">内存限制(MB)</div>
            <div class="toggle-sub-desc">每个沙箱的内存上限，范围 64-4096</div>
            <n-input-number
              v-model:value="sandboxConfig.sandbox_memory_limit_mb"
              :min="64" :max="4096"
              style="width: 100%; margin-top: 8px"
            />
          </div>
          <div class="toggle-card">
            <div class="toggle-sub-label">每用户最大沙箱数</div>
            <div class="toggle-sub-desc">同时运行的沙箱上限，范围 1-20</div>
            <n-input-number
              v-model:value="sandboxConfig.sandbox_max_per_user"
              :min="1" :max="20"
              style="width: 100%; margin-top: 8px"
            />
          </div>
          <div class="toggle-card">
            <div class="toggle-sub-label">空闲清理超时(秒)</div>
            <div class="toggle-sub-desc">空闲后自动销毁沙箱，范围 60-7200</div>
            <n-input-number
              v-model:value="sandboxConfig.sandbox_idle_timeout"
              :min="60" :max="7200"
              style="width: 100%; margin-top: 8px"
            />
          </div>
        </div>

        <!-- 已启用沙箱的 Skill 一览 -->
        <div class="toggle-card" style="margin-top: 16px;">
          <div class="toggle-sub-label" style="margin-bottom: 8px">已启用沙箱的 Skill</div>
          <div v-if="sandboxEnabledSkills.length === 0" class="judge-skills-empty">
            暂无 Skill 启用了沙箱。请到「Skill 管理 → 编辑」中开启。
          </div>
          <div v-else class="judge-skills-list">
            <div v-for="sk in sandboxEnabledSkills" :key="sk.name" class="judge-skill-item">
              <span class="judge-skill-name">{{ sk.display_name }}</span>
              <n-tag size="tiny" :type="sk.is_enabled ? 'success' : 'default'">
                {{ sk.is_enabled ? '已启用' : '已禁用' }}
              </n-tag>
              <span class="judge-skill-meta">{{ sk.sandbox_timeout }}s / {{ sk.sandbox_memory_mb }}MB</span>
            </div>
          </div>
        </div>
      </template>

      <n-space style="margin-top: 20px">
        <n-button @click="resetSandboxConfig" :disabled="!hasSandboxChanges">
          重置
        </n-button>
        <n-button type="primary" :loading="sandboxSaving" :disabled="!hasSandboxChanges" @click="saveSandboxConfig">
          <template #icon>
            <n-icon :component="SaveOutline" />
          </template>
          保存沙箱配置
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
.toggle-card--sandbox {
  border-left-color: #18a058;
  background: #f5fff9;
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
.toggle-sub-label {
  font-size: 13px;
  font-weight: 500;
  color: #333;
}
.toggle-sub-desc {
  font-size: 11px;
  color: #999;
}
.sandbox-config-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-top: 12px;
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
