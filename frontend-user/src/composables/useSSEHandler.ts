/**
 * SSE 事件处理 composable
 *
 * 从 SuperAgent.vue 提取的核心 SSE 事件处理逻辑
 * 职责：
 * - 处理所有 SSE 事件类型（thinking, action, observation, content, sub_agent_*, etc.）
 * - 更新消息状态（thinkingSteps, toolCalls, subAgents, content）
 * - 管理执行阶段状态（currentPhase, latestEventType）
 * - 管理连接心跳
 * - 集成 SSE Debug 捕获
 */
import { ref, triggerRef, computed, type Ref } from 'vue'
import { useSSEDebug } from './useSSEDebug'
import { useSuperAgentGuideStore } from '../stores/superAgentGuide'
import { useArtifactStore } from '@/stores/artifact'
import type {
  ChatMessage,
  ThinkingStep,
  ToolCall,
  SubAgentState,
  SubAgentToolCall,
  Reference,
  ViewMode,
  ClientToolRequest,
  PlanStepState,
  ExecutionPlanState,
  PlanQuestionRequest,
  DiagnosticReportData,
  PeerReviewData,
  AttachmentInfo,
} from '../types/super-agent'
import { TOOL_DISPLAY_NAMES } from '../types/super-agent'
import type { SSEEventData } from '../api/superagent'
import type { DataSourceStatus } from '../components/ErrorState.vue'

export interface UseSSEHandlerOptions {
  messages: Ref<ChatMessage[]>
  viewMode: Ref<ViewMode>
  currentSkill: Ref<string>
  currentIntent: Ref<string>
  isSkillExecuting: Ref<boolean>
  isGeneratingAnswer: Ref<boolean>
  skillExecutionTime: Ref<number>
  currentSessionId: Ref<string>
  currentReferences: Ref<Reference[]>
  dataSources: Ref<DataSourceStatus[]>
  // 辩论相关
  debateStatus: Ref<string>
  currentDebateRound: Ref<number>
  maxDebateRounds: Ref<number>
  canContinueDebate: Ref<boolean>
  roundHint: Ref<unknown>
  currentDebateSessionId: Ref<string>
  discussionMode: Ref<string>
  pendingModeSelection: Ref<{
    topic: string
    recommendedMode: string
    modes: Array<{ value: string; label: string; icon: string; desc: string }>
    timeoutSeconds: number
  } | null>
  // 思考过程
  currentExpertReasoning: Ref<{ expertId: string; expertName: string; reasoning: string } | null>
  expertReasonings: Ref<Map<string, string>>
  // 滚动
  scrollToBottom: (force?: boolean) => void
  throttledScrollToBottom: () => void
  // 消息通知
  messageApi: { error: (msg: string) => void }
}

/** 根据 Skill 类型推断当前工具执行阶段的 key */
function inferPhaseFromSkill(skillName: string): string {
  const phaseMap: Record<string, string> = {
    'academic-search': 'search',
    'web-search': 'fetch',
    'deep-research': 'research',
    'doc-generator': 'outline',
    'course-outline-generator': 'structure',
    'learning-path-planner': 'analyze',
    'policy-analyzer': 'collect',
    'multi-expert-debate': 'expert',
    'animation-generator': 'knowledge',
  }
  return phaseMap[skillName] || 'process'
}

export function useSSEHandler(options: UseSSEHandlerOptions) {
  const {
    messages,
    viewMode,
    currentSkill,
    currentIntent,
    isSkillExecuting,
    isGeneratingAnswer,
    skillExecutionTime,
    currentSessionId,
    currentReferences,
    dataSources,
    debateStatus,
    currentDebateRound,
    maxDebateRounds,
    canContinueDebate,
    roundHint,
    currentDebateSessionId,
    discussionMode,
    pendingModeSelection,
    currentExpertReasoning,
    expertReasonings,
    scrollToBottom,
    throttledScrollToBottom,
    messageApi,
  } = options

  // 阶段进度状态
  const currentPhase = ref<string>('intent')
  let _lastCustomStage = ''  // 追踪 customStages 模式下的当前阶段，用于插入分隔标题
  const latestEventType = ref<string>('')
  const latestEventData = ref<Record<string, unknown>>({})
  const memoryCount = ref<number>(0)

  // Perplexica 迁移新增状态
  /** 当前写作/检索阶段标识（phase 事件） */
  const currentSearchPhase = ref<string>('')
  /** 查询改写信息（query_rewrite 事件） */
  const queryRewriteInfo = ref<{ original: string; rewritten: string } | null>(null)

  // SSE 协议版本号（由 session_created 事件携带，用于 Phase 2 事件分发）
  const protocolVersion = ref<number>(1)

  // Connection Health 状态（两级心跳检测）
  const connectionHealth = ref<'connected' | 'warning' | 'closed'>('closed')
  let heartbeatWarningTimer: ReturnType<typeof setTimeout> | null = null
  let heartbeatDisconnectTimer: ReturnType<typeof setTimeout> | null = null

  // SSE Debug composable
  const isDebugMode = computed(() => viewMode.value === 'debug')
  const debugCapture = useSSEDebug(isDebugMode)

  /**
   * 清理 pending 状态的非工具 thinking 步骤
   * 用于 action/tool_call_start/done 事件中统一清理，避免 thinking 指示器残留
   */
  function cleanupPendingThinking(msg: ChatMessage) {
    if (!msg.thinkingSteps?.length) return
    for (let i = msg.thinkingSteps.length - 1; i >= 0; i--) {
      const step = msg.thinkingSteps[i]
      if (!step.isToolCall && step.status === 'pending') {
        if (step.isStreaming) {
          step.status = 'success'
        } else {
          msg.thinkingSteps.splice(i, 1)
        }
      }
    }
  }

  function resetHeartbeat() {
    connectionHealth.value = 'connected'
    if (heartbeatWarningTimer) clearTimeout(heartbeatWarningTimer)
    if (heartbeatDisconnectTimer) clearTimeout(heartbeatDisconnectTimer)
    // 45s 无心跳 → warning
    heartbeatWarningTimer = setTimeout(() => {
      connectionHealth.value = 'warning'
    }, 45000)
    // 90s 无心跳 → closed（触发断连状态）
    heartbeatDisconnectTimer = setTimeout(() => {
      connectionHealth.value = 'closed'
    }, 90000)
  }

  function closeHeartbeat() {
    connectionHealth.value = 'closed'
    if (heartbeatWarningTimer) {
      clearTimeout(heartbeatWarningTimer)
      heartbeatWarningTimer = null
    }
    if (heartbeatDisconnectTimer) {
      clearTimeout(heartbeatDisconnectTimer)
      heartbeatDisconnectTimer = null
    }
  }

  /** 更新数据源状态 */
  function updateDataSourceStatus(toolName: string, status: DataSourceStatus['status'], error?: string) {
    const displayName = TOOL_DISPLAY_NAMES[toolName] || toolName
    const existingIndex = dataSources.value.findIndex(s => s.name === toolName)

    if (existingIndex >= 0) {
      dataSources.value[existingIndex].status = status
      if (error) {
        dataSources.value[existingIndex].error = error
      }
    } else {
      dataSources.value.push({
        name: toolName,
        displayName,
        status,
        error,
      })
    }
  }

  /** 重置所有 SSE 相关状态（发送新消息前调用） */
  function resetSSEState() {
    currentPhase.value = 'intent'
    _lastCustomStage = ''
    latestEventType.value = ''
    latestEventData.value = {}
    currentSearchPhase.value = ''
    queryRewriteInfo.value = null
    debugCapture.reset()
    resetHeartbeat()
  }

  /** 主 SSE 事件处理函数 */
  function handleSSEEvent(event: string, data: SSEEventData, msgId: string) {
    let msgIndex = messages.value.findIndex(m => m.id === msgId)

    // 特殊处理：mode_selection_required 事件可能在消息创建前到达
    if (msgIndex === -1 && event === 'mode_selection_required') {
      messages.value.push({
        id: msgId,
        role: 'assistant',
        content: '',
        pendingModeSelection: {
          topic: data.topic as string || '',
          recommendedMode: data.recommended_mode as string,
          modes: data.modes as Array<{ value: string; label: string; icon: string; desc: string }>,
          timeoutSeconds: data.timeout_seconds as number
        }
      })
      pendingModeSelection.value = messages.value[messages.value.length - 1].pendingModeSelection!
      return
    }

    // Artifact 事件：不依赖 msgId，优先处理
    if (event === 'artifact_create' || event === 'artifact_delta' || event === 'artifact_done') {
      resetHeartbeat()
      try {
        const artStore = useArtifactStore()
        if (event === 'artifact_create') {
          artStore.handleArtifactCreate(data as any)
          // 同步设置当前消息的 artifactId，让预览卡片在流式中就能显示
          if (data.artifact_id) {
            // 优先用 msgIndex，fallback 找最后一个 streaming assistant 消息
            const targetMsg = msgIndex !== -1
              ? messages.value[msgIndex]
              : [...messages.value].reverse().find(m => m.role === 'assistant' && m.isStreaming)
            if (targetMsg) {
              ;(targetMsg as any).artifactId = data.artifact_id as string
            }
          }
        } else if (event === 'artifact_delta') {
          artStore.handleArtifactDelta(data as any)
        } else {
          artStore.handleArtifactDone(data as any)
        }
      } catch (e) {
        console.warn('[SSE] artifact event handling error:', e)
      }
      return
    }

    if (msgIndex === -1) return
    const msg = messages.value[msgIndex]!

    // Debug 模式：捕获事件
    debugCapture.captureEvent(event, { ...data } as Record<string, unknown>)

    // Connection Health: 收到任何事件都重置心跳
    resetHeartbeat()

    // 更新最新事件状态
    latestEventType.value = event
    latestEventData.value = { ...data } as Record<string, unknown>

    switch (event) {
      case 'thinking': {
        currentPhase.value = 'intent'
        // 后端契约不统一：Claude extended 用 text/thought，supervisor/pipeline 用 content
        const thinkingText = data.text || data.thought || data.content
        if (thinkingText) {
          if (!msg.thinkingSteps) msg.thinkingSteps = []

          // Claude extended thinking — 流式增量累积
          if (data.is_streaming) {
            const lastStep = msg.thinkingSteps[msg.thinkingSteps.length - 1]
            if (lastStep && lastStep.isStreaming) {
              lastStep.text += thinkingText
            } else {
              msg.thinkingSteps.push({
                text: thinkingText,
                step: msg.thinkingSteps.length + 1,
                isToolCall: false,
                status: 'pending',
                isStreaming: true,
              })
            }
            break
          }

          const isWaitingHint = thinkingText.startsWith('💭') || thinkingText.includes('正在分析') || thinkingText.includes('正在思考')
          msg.thinkingSteps.push({
            text: thinkingText,
            step: msg.thinkingSteps.length + 1,
            isToolCall: false,
            status: isWaitingHint ? 'pending' : 'success',
          })
        }
        break
      }

      // Pipeline Skill 阶段推进（slide-generator 等）
      // 追加到 thinkingSteps，让完成后可通过 ExecutionSidebar 折叠重看
      case 'progress': {
        currentPhase.value = 'skill'
        const progressText = (data.content as string) || (data.message as string)
        if (progressText) {
          if (!msg.thinkingSteps) msg.thinkingSteps = []
          // 幂等：连续两条相同文案不重复入列（backend 可能重发相同阶段）
          const last = msg.thinkingSteps[msg.thinkingSteps.length - 1]
          if (!last || last.text !== progressText) {
            msg.thinkingSteps.push({
              text: progressText,
              step: msg.thinkingSteps.length + 1,
              isToolCall: false,
              status: 'success',
            })
          }
        }
        break
      }

      // FIX-3: action 事件已弃用，统一为 tool_call_start
      // 保留 action handler 作为向后兼容层（映射到 tool_call_start 逻辑）
      case 'action': {
        currentPhase.value = inferPhaseFromSkill(currentSkill.value)
        const toolName = data.tool_name || data.tool || ''
        const toolDisplayName = data.tool_display_name || toolName
        const toolCallId = data.tool_call_id
        updateDataSourceStatus(toolName, 'trying')

        // 清理之前的 pending 状态 thinking 步骤
        cleanupPendingThinking(msg)

        if (!msg.thinkingSteps) msg.thinkingSteps = []
        const existingStep = toolCallId
          ? msg.thinkingSteps.find((s: ThinkingStep) => s.toolCallId === toolCallId && s.isToolCall)
          : undefined
        if (existingStep) {
          existingStep.status = 'running'
          existingStep.toolName = toolName
          existingStep.toolDisplayName = toolDisplayName
        } else {
          const newStep: ThinkingStep = {
            text: toolDisplayName,
            step: msg.thinkingSteps.length + 1,
            toolCallId,
            toolName,
            toolDisplayName,
            status: 'running',
            isToolCall: true,
            startTime: Date.now(),
          }
          // 聚合模式：标记路径归属
          if (typeof data.path_index === 'number') {
            newStep.pathIndex = data.path_index
            newStep.pathLabel = data.path_label || ''
          }
          msg.thinkingSteps.push(newStep)
        }

        if (!msg.toolCalls) msg.toolCalls = []
        const existingTC = toolCallId
          ? msg.toolCalls.find((t: ToolCall) => t.toolCallId === toolCallId)
          : null
        if (existingTC) {
          existingTC.input = data.input || existingTC.input
        } else {
          msg.toolCalls.push({
            tool: toolName,
            toolDisplayName,
            toolCallId,
            input: data.input || {},
          })
        }
        break
      }

      case 'observation': {
        if (msg.thinkingSteps && data.tool_call_id) {
          const stepIndex = msg.thinkingSteps.findIndex(
            (s: ThinkingStep) => s.toolCallId === data.tool_call_id
          )
          if (stepIndex !== -1) {
            const step = msg.thinkingSteps[stepIndex]
            step.status = data.status || 'success'
            step.summary = data.summary
            step.durationMs = data.duration_ms
            step.totalDurationMs = data.total_duration_ms
            step.phaseDurations = data.phase_durations
            const toolStatus = data.status === 'error' ? 'failed' : 'success'
            updateDataSourceStatus(data.tool_name || step.toolName || '', toolStatus)
          } else {
            // fallback: 按 tool_name 匹配，再退而求其次取最后一个 running 的工具调用
            const toolName = data.tool_name || data.tool || ''
            const runningIndex = msg.thinkingSteps.findLastIndex(
              (s: ThinkingStep) => s.status === 'running' && s.isToolCall &&
                (!toolName || s.toolName === toolName)
            )
            // 如果按 tool_name 没匹配到，再尝试不限 tool_name
            const fallbackIndex = runningIndex !== -1 ? runningIndex : msg.thinkingSteps.findLastIndex(
              (s: ThinkingStep) => s.status === 'running' && s.isToolCall
            )
            if (fallbackIndex !== -1) {
              const step = msg.thinkingSteps[fallbackIndex]
              step.status = data.status || 'success'
              step.summary = data.summary
              step.durationMs = data.duration_ms
              step.totalDurationMs = data.total_duration_ms
              step.phaseDurations = data.phase_durations
              const toolStatus = data.status === 'error' ? 'failed' : 'success'
              updateDataSourceStatus(data.tool_name || step.toolName || '', toolStatus)
            }
          }
        }

        if (msg.toolCalls && msg.toolCalls.length > 0) {
          const toolCall = data.tool_call_id
            ? msg.toolCalls.find((t: ToolCall) => t.toolCallId === data.tool_call_id)
            : msg.toolCalls[msg.toolCalls.length - 1]

          if (toolCall) {
            toolCall.result = data.result
            toolCall.status = data.status
            toolCall.summary = data.summary
            toolCall.durationMs = data.duration_ms
            // standalone 插件标记（前端侧栏用独立摘要）
            if (data.is_standalone) {
              toolCall.isStandalone = true
            }
          } else {
            console.warn('[SSE] observation: toolCall not found for tool_call_id:', data.tool_call_id)
          }
        }
        break
      }

      // 子代理编排事件
      case 'sub_agent_start': {
        if (!msg.subAgents) msg.subAgents = []
        // 标记阶段归属（用于 invisible stage 完成后清理）
        let _saStage: string | undefined
        if (msg.executionPlan?.steps) {
          const runningStep = msg.executionPlan.steps.find(s => s.status === 'running')
          if (runningStep) {
            _saStage = runningStep.id.startsWith('stage_') ? runningStep.id.slice(6) : runningStep.id
          }
        }
        const subAgentId = `${data.sub_skill}_${Date.now()}`
        msg.subAgents.push({
          id: subAgentId,
          subSkill: data.sub_skill || '',
          displayName: data.display_name || data.sub_skill || '',
          task: data.task || '',
          status: 'running',
          toolCalls: [],
          startTime: Date.now(),
          output: '',
          thinking: '',
          expanded: true,
          userToggled: false,
          stageName: _saStage,
        })
        break
      }

      case 'sub_agent_action': {
        if (msg.subAgents) {
          const agent = msg.subAgents.findLast((a: SubAgentState) =>
            a.subSkill === data.sub_skill && a.status === 'running'
          )
          if (agent) {
            // 如果有 receiving 状态的 toolCall（来自 tool_call_start），更新它
            const receivingTC = data.tool_call_id
              ? agent.toolCalls.find((t: SubAgentToolCall) => t.toolCallId === data.tool_call_id)
              : agent.toolCalls.findLast((t: SubAgentToolCall) => t.status === 'receiving')
            if (receivingTC) {
              receivingTC.status = 'running'
              receivingTC.input = data.input
            } else {
              agent.toolCalls.push({
                tool: data.tool || '',
                displayName: data.tool_display_name || data.tool || '',
                toolCallId: data.tool_call_id,
                status: 'running',
                input: data.input,
              })
            }
          }
        }
        break
      }

      case 'sub_agent_tool_call_start': {
        if (msg.subAgents) {
          const agent = msg.subAgents.findLast((a: SubAgentState) =>
            a.subSkill === data.sub_skill && a.status === 'running'
          )
          if (agent) {
            agent.toolCalls.push({
              tool: data.tool_name || '',
              displayName: data.tool_display_name || data.tool_name || '',
              toolCallId: data.tool_call_id,
              status: 'receiving',
              inputRaw: '',
            })
          }
        }
        break
      }

      case 'sub_agent_tool_call_delta': {
        if (msg.subAgents) {
          const agent = msg.subAgents.findLast((a: SubAgentState) =>
            a.subSkill === data.sub_skill && a.status === 'running'
          )
          if (agent) {
            const tc = agent.toolCalls.find((t: SubAgentToolCall) => t.toolCallId === data.tool_call_id)
            if (tc) {
              tc.inputRaw = (tc.inputRaw || '') + (data.arguments_delta || '')
            }
          }
        }
        break
      }

      case 'sub_agent_answering': {
        // 子代理开始生成答案（可用于 UI 状态提示）
        break
      }

      case 'sub_agent_observation': {
        if (msg.subAgents) {
          const agent = msg.subAgents.findLast((a: SubAgentState) =>
            a.subSkill === data.sub_skill && a.status === 'running'
          )
          if (agent) {
            const tc = agent.toolCalls.findLast((t: SubAgentToolCall) =>
              t.status === 'running'
            )
            if (tc) {
              tc.status = data.status || 'success'
              tc.summary = data.summary
              tc.durationMs = data.duration_ms
            }
          }
        }
        break
      }

      case 'sub_agent_complete': {
        if (msg.subAgents) {
          const agent = msg.subAgents.findLast((a: SubAgentState) =>
            a.subSkill === data.sub_skill && a.status === 'running'
          )
          if (agent) {
            agent.status = 'completed'
            agent.duration = data.duration_ms || (Date.now() - agent.startTime)
            // P0-B: 自动收起（用户未手动操作过）
            if (!agent.userToggled) {
              agent.expanded = false
            }
          }
        }
        break
      }

      case 'sub_agent_error': {
        if (msg.subAgents) {
          const agent = msg.subAgents.findLast((a: SubAgentState) =>
            a.subSkill === data.sub_skill && a.status === 'running'
          )
          if (agent) {
            agent.status = 'failed'
            agent.error = data.error
            agent.duration = Date.now() - agent.startTime
          }
        }
        break
      }

      // NEW-1: 子代理批量事件
      case 'subagent_batch_start': {
        msg.subAgentBatch = {
          batchId: (data.batch_id as string) || '',
          count: (data.count as number) || 0,
          subSkills: (data.sub_skills as string[]) || [],
          status: 'running',
          startTime: Date.now(),
        }
        break
      }

      case 'subagent_batch_done': {
        if (msg.subAgentBatch) {
          msg.subAgentBatch.status = 'completed'
          msg.subAgentBatch.succeeded = (data.succeeded as number) || 0
          msg.subAgentBatch.failed = (data.failed as number) || 0
          msg.subAgentBatch.durationMs = (data.duration_ms as number) || 0
        }
        break
      }

      // P0-B: 子代理思考/内容事件 — 不再丢弃，追加到 SubAgentState
      case 'sub_agent_thinking': {
        if (msg.subAgents && data.sub_skill) {
          const agent = msg.subAgents.findLast((a: SubAgentState) =>
            a.subSkill === data.sub_skill && a.status === 'running'
          )
          if (agent) {
            agent.thinking = (agent.thinking || '') + (data.text || data.thought || '')
          }
        }
        break
      }

      case 'sub_agent_content': {
        if (msg.subAgents && data.sub_skill) {
          const agent = msg.subAgents.findLast((a: SubAgentState) =>
            a.subSkill === data.sub_skill && a.status === 'running'
          )
          if (agent) {
            const text = data.text || ''
            agent.output = (agent.output || '') + text
            // 截断保护：最大 10KB，保留尾部内容（加 buffer 避免每个 chunk 都触发截断）
            if (agent.output.length > 11264) {
              agent.output = '[...前文已截断...]\n' + agent.output.slice(-10240)
            }
          }
        }
        throttledScrollToBottom()
        break
      }

      case 'intent': {
        currentPhase.value = 'intent'
        currentIntent.value = data.type || ''
        // Router 契约：skill=技术名，skill_display_name=中文名；老逻辑读 skill_name 兜底
        const intentSkillName = (data.skill as string) || (data.skill_name as string) || ''
        const intentDisplayName = (data.skill_display_name as string) || intentSkillName
        if (data.type === 'context_qa') {
          // Follow-up REFINE: 不触发 skill 执行面板
          msg.intent = 'context_qa'
          currentSkill.value = ''
          isSkillExecuting.value = false
          if (data.original_skill) {
            msg.skillName = data.original_skill
          }
        } else {
          if (intentSkillName) {
            currentSkill.value = intentSkillName
            msg.skillName = intentSkillName
          }
          msg.intent = data.type
        }
        // 追加到 thinkingSteps，让完成后可通过 ExecutionSidebar 折叠回看
        if (intentDisplayName) {
          if (!msg.thinkingSteps) msg.thinkingSteps = []
          msg.thinkingSteps.push({
            text: `已识别为「${intentDisplayName}」`,
            step: msg.thinkingSteps.length + 1,
            isToolCall: false,
            status: 'success',
          })
        }
        if (data.followup_type) {
          msg.followupType = data.followup_type as 'refine' | 'continuation' | 'acknowledge'
        }
        // 多轮对话引用继承：后续消息可能引用前几轮的 [ref_xxx]
        // 如果当前消息没有 references，从最近的有引用的助手消息继承
        if (!msg.references || msg.references.length === 0) {
          for (let i = messages.value.length - 1; i >= 0; i--) {
            const prev = messages.value[i]
            if (prev !== msg && prev.role === 'assistant' && prev.references && prev.references.length > 0) {
              msg.references = [...prev.references]
              currentReferences.value = msg.references
              break
            }
          }
        }
        break
      }

      case 'intent_confirm':
        // P1b: 低置信度意图确认 — 暂停流式，等待用户选择
        msg.pendingIntentConfirm = {
          skillName: (data.skill_name as string) || '',
          skillDisplayName: (data.skill_display_name as string) || '',
          confidence: (data.confidence as number) || 0,
          originalMessage: (data.message as string) || '',
          clarifyHint: (data.clarify_hint as string) || undefined,
          alternatives: ((data.alternatives as Array<{ skill_name: string; skill_display_name: string; confidence: number }>) || []).map(a => ({
            skillName: a.skill_name,
            skillDisplayName: a.skill_display_name,
            confidence: a.confidence,
          })),
        }
        msg.isStreaming = false
        isSkillExecuting.value = false
        isGeneratingAnswer.value = false
        break

      case 'skill_start':
        isSkillExecuting.value = true
        isGeneratingAnswer.value = false
        msg.skillDisplayName = data.display_name
        msg.skillName = data.skill
        if (data.output_format) {
          msg.outputFormat = data.output_format
        }
        currentSkill.value = data.skill || ''
        currentPhase.value = inferPhaseFromSkill(data.skill || '')
        // intent 是前端虚拟阶段，skill_start 意味着意图识别完成，推进到 analyze
        if (data.skill === 'deep-research') {
          currentPhase.value = 'analyze'
        }
        // Artifact 模式：仅记录标记，不立即展开面板
        // 面板在收到 artifact_create 事件时才展开（避免 AI 先问问题时就弹空面板）
        break

      // Artifact 事件已在 switch 之前处理（不依赖 msgId）

      case 'tool_call': {
        currentPhase.value = inferPhaseFromSkill(currentSkill.value)
        updateDataSourceStatus(data.tool || '', 'trying')
        if (!msg.toolCalls) msg.toolCalls = []
        const existingToolCall = msg.toolCalls.find(
          (t: ToolCall) => t.toolCallId === data.tool_call_id
        )
        if (!existingToolCall) {
          msg.toolCalls.push({
            tool: data.tool || '',
            toolCallId: data.tool_call_id,
            input: data.input || {},
          })
        }
        break
      }

      case 'tool_result': {
        if (msg.toolCalls && msg.toolCalls.length > 0) {
          const lastTool = msg.toolCalls[msg.toolCalls.length - 1]
          if (lastTool) {
            lastTool.result = data.result
            const dataRecord = data as unknown as Record<string, unknown>
            const errorField = dataRecord.error as string | undefined
            const isSuccess = !errorField && data.result
            updateDataSourceStatus(lastTool.tool, isSuccess ? 'success' : 'failed', errorField)
          }
        }
        break
      }

      case 'references':
        if (data.references) {
          const newRefs = data.references as Reference[]
          const mode = (data as any).mode as string | undefined
          const subTaskId = (data as any).sub_task_id as string | undefined

          // 子任务引用：存到对应子任务的独立 references 中
          if (subTaskId && msg.parallelSubtasks) {
            const task = msg.parallelSubtasks.find(t => t.id === subTaskId)
            if (task) {
              const existingMap = new Map((task.references || []).map(r => [r.id, r]))
              for (const ref of newRefs) {
                existingMap.set(ref.id, ref)
              }
              task.references = Array.from(existingMap.values())
            }
          }

          // 全局 references 处理
          if (mode === 'replace') {
            // 完全替换模式（用于并行编排合成后的引用重映射）
            currentReferences.value = newRefs
            msg.references = newRefs
          } else {
            // 增量追加模式（默认）
            // 合并去重（以 id 为 key），支持增量推送
            const existingMap = new Map((msg.references || []).map(r => [r.id, r]))
            for (const ref of newRefs) {
              existingMap.set(ref.id, ref)
            }
            const merged = Array.from(existingMap.values())
            currentReferences.value = merged
            msg.references = merged
          }

          // 同步到 artifact store（如果正在流式生成 artifact）
          const artStore = useArtifactStore()
          if (artStore._isBackgroundStreaming || artStore.isStreaming) {
            artStore.syncReferences(msg.references || [])
          }
        }
        break

      case 'content':
        currentPhase.value = 'answer'
        msg.isDiagnosing = false
        // debug: content 事件链路追踪
        if (import.meta.env.DEV && (data.text as string)?.length > 200) {
          console.log('[SSE] content event, stage=', data.stage, 'text_len=', (data.text as string)?.length)
        }
        // 记录关联的 artifact_id（用于对话气泡中渲染"查看文档"卡片）
        if (data.artifact_id) {
          (msg as any).artifactId = data.artifact_id
        }
        if (data.text) {
          const stage = data.stage as string | undefined
          // 辩论预研背景：独立存储，不拼接到主内容
          if (stage === 'pre_research') {
            msg.preResearchContent = (msg.preResearchContent || '') + (data.text as string)
          // 自定义阶段模式（如 problem-solver）：所有阶段内容直接进入主内容区
          // 默认流水线（deep-research）：只有 write/revise 阶段进入 msg.content
          // 非流水线路径 stage=undefined，正常追加（兼容 PlanModeExecutor 和普通 skill）
          } else if (!stage || stage === 'write' || stage === 'revise') {
            msg.content += data.text
          } else if (msg.executionPlan?.customStages) {
            // 自定义阶段模式：所有阶段执行中都流入正文（用户可见）
            if (stage !== _lastCustomStage) {
              const step = msg.executionPlan.steps.find(s =>
                s.id === `stage_${stage}` || s.id === stage
              )
              const title = step?.title || stage
              const isVisible = msg.executionPlan.visibleStages?.[stage] ?? true
              const divider = _lastCustomStage ? '\n\n---\n\n' : ''
              // invisible 阶段加边界标记（完成后用于移除）
              if (!isVisible) {
                msg.content += `${divider}<!-- STAGE_CONTENT_START:${stage} -->\n### ${title}\n\n`
              } else {
                msg.content += `${divider}### ${title}\n\n`
              }
            }
            _lastCustomStage = stage
            msg.content += data.text

            // invisible 阶段同时累积到 stageOutputs（完成后折叠为 chip）
            const isVisible = msg.executionPlan.visibleStages?.[stage] ?? true
            if (!isVisible) {
              if (!msg.stageOutputs) msg.stageOutputs = []
              let current = msg.stageOutputs.find(s => s.stage === stage)
              if (!current) {
                const step = msg.executionPlan.steps.find(s =>
                  s.id === `stage_${stage}` || s.id === stage
                )
                current = { stage, title: step?.title || stage, content: '' }
                msg.stageOutputs.push(current)
              }
              current.content += data.text
              if (current.content.length > 3000) {
                current.content = current.content.slice(-2500)
              }
            }
          } else {
            // 非 write 阶段：累积到 stageOutputs
            if (!msg.stageOutputs) msg.stageOutputs = []
            let current = msg.stageOutputs.find(s => s.stage === stage)
            if (!current) {
              const step = msg.executionPlan?.steps.find(s =>
                s.id === `stage_${stage}` || s.id === stage
              )
              const stepTitle = step?.title || stage
              current = { stage, title: stepTitle, content: '' }
              msg.stageOutputs.push(current)
            }
            current.content += data.text
            // 截断保护：每个阶段最多 3000 字符（保留尾部）
            if (current.content.length > 3000) {
              current.content = current.content.slice(-2500)
            }
          }
          throttledScrollToBottom()
        }
        break

      case 'answering':
        currentPhase.value = 'answer'
        isGeneratingAnswer.value = true
        isSkillExecuting.value = false
        cleanupPendingThinking(msg)
        if (msg.thinkingSteps) {
          // 修复遗留的 receiving/running 工具调用（observation 事件未送达）
          for (const step of msg.thinkingSteps) {
            if (step.isToolCall && (step.status === 'receiving' || step.status === 'running')) {
              step.status = 'success'
            }
          }
        }
        if (msg.toolCalls) {
          for (const tc of msg.toolCalls) {
            if (!tc.status || tc.status === 'receiving' || tc.status === 'running') {
              tc.status = 'success'
            }
          }
        }
        break

      case 'skill_state': {
        // Guide 学习状态事件 — 更新 Pinia store
        useSuperAgentGuideStore().updateFromSSE(data as any)
        break
      }

      case 'depth_gate':
        // 深度门控事件：done 被拒绝（最小搜索次数不足）
        // 存储到消息元数据，暂不做 UI 展示
        if (msg.metadata === undefined) {
          msg.metadata = {}
        }
        msg.metadata.depthGate = {
          rejected: data.rejected as boolean,
          actual: data.actual as number,
          required: data.required as number,
        }
        break

      case 'skill_done':
        isSkillExecuting.value = false
        skillExecutionTime.value = data.execution_time_ms || 0
        // 存储深度覆盖元数据（前端感知基础，暂不做 UI 展示）
        if (data.depth_requested || data.depth_applied) {
          if (msg.metadata === undefined) {
            msg.metadata = {}
          }
          msg.metadata.depthRequested = data.depth_requested as string
          msg.metadata.depthApplied = data.depth_applied as string
          msg.metadata.depthOverridden = data.depth_overridden as boolean
        }
        // 非 Guide skill 完成时清除 guide 进度（但保留 suggestedReplies，
        // 因为 conversational 模式的 skill 在 skill_done 后仍需展示快捷回复）
        if (data.skill !== 'learning-guide') {
          useSuperAgentGuideStore().clearGuideState()
        } else {
          // R13 兜底：learning-guide 完成时，始终尝试从消息内容提取完整 GUIDE_STATE
          const guideStore = useSuperAgentGuideStore()
          // 无论 SSE skill_state 是否已到达，都尝试从内容补全
          if (msg.content) {
            const match = msg.content.match(/<!--GUIDE_STATE:\s*(\{[\s\S]*?\})\s*-->/)
            if (match) {
              try {
                const state = JSON.parse(match[1])
                guideStore.updateFromSSE({
                  guide_state: {
                    phase: state.phase || 'learning',
                    current_index: state.current_index || 1,
                    total: state.total || 1,
                    current_topic: state.current_topic || '',
                    mastery: state.mastery || {},
                  },
                  suggested_replies: state.suggested_replies || [],
                })
              } catch { /* 静默忽略解析失败 */ }
            }
          }
        }
        // Classroom 关联：skill_done 携带 classroom_session_id 时写入 msg
        if (data.classroom_session_id && msg.classroomData) {
          msg.classroomData.sessionId = data.classroom_session_id as string
        }
        break

      case 'memory_context':
        // 长期记忆检索结果（仅记录，不影响 UI 流程）
        if (data.memories && Array.isArray(data.memories)) {
          memoryCount.value = data.count as number || data.memories.length
        }
        break

      case 'pageindex_status': {
        // PageIndex 索引状态更新（全局搜索所有消息的附件）
        const piAttId = data.attachment_id as string
        const piStatus = data.status as AttachmentInfo['indexStatus']
        if (piAttId) {
          for (const m of messages.value) {
            if (m.attachments) {
              const att = m.attachments.find(a => a.id === piAttId)
              if (att) {
                att.indexStatus = piStatus
                break
              }
            }
          }
          triggerRef(messages)
        }
        break
      }

      case 'mode_selection_required':
        // 模式选择事件
        pendingModeSelection.value = {
          topic: data.topic as string || msg.content || '',
          recommendedMode: data.recommended_mode as string,
          modes: data.modes as Array<{ value: string; label: string; icon: string; desc: string }>,
          timeoutSeconds: data.timeout_seconds as number
        }
        msg.pendingModeSelection = pendingModeSelection.value
        triggerRef(messages)  // 强制触发 Vue 响应式更新
        break

      case 'debate_created':
        msg.debateSessionId = data.debate_session_id
        msg.experts = data.experts
        currentDebateSessionId.value = data.debate_session_id as string
        maxDebateRounds.value = (data.max_rounds as number) || 3
        discussionMode.value = (data.discussion_mode as string) || 'debate'
        debateStatus.value = 'running'
        break

      case 'status': {
        // 辩论预研状态 & 通用状态事件
        const status = data.status as string
        if (status === 'pre_researching') {
          debateStatus.value = 'pre_researching'
        } else if (status === 'pre_research_done') {
          debateStatus.value = 'running'
        }
        break
      }

      case 'round_start':
        currentDebateRound.value = data.round as number
        debateStatus.value = 'running'
        roundHint.value = null
        break

      case 'round_end':
        currentDebateRound.value = data.round as number
        debateStatus.value = 'paused'
        canContinueDebate.value = data.can_continue as boolean
        roundHint.value = data.hint
        scrollToBottom()
        break

      case 'final_summary':
        debateStatus.value = 'ended'
        roundHint.value = null
        break

      case 'debate_done':
        debateStatus.value = 'ended'
        roundHint.value = null
        break

      case 'reasoning_chunk': {
        // 兼容辩论模式（有 expert_id）和普通对话模式（无 expert_id，使用 __model__）
        const rcExpertId = (data.expert_id as string) || '__model__'
        const rcExpertName = (data.expert_name as string) || '思考过程'
        const rcChunk = data.chunk as string
        const rcPhase = data.phase as string | undefined
        const rcRound = data.round as number | undefined

        if (rcChunk) {
          if (!msg.reasoningBlocks) msg.reasoningBlocks = []

          let block = msg.reasoningBlocks.find(b => b.expertId === rcExpertId)
          if (!block) {
            block = {
              expertId: rcExpertId,
              expertName: rcExpertName,
              reasoning: '',
              isCollapsed: false,
              phase: rcPhase,
              round: rcRound,
            }
            msg.reasoningBlocks.push(block)
          }

          block.reasoning += rcChunk
          triggerRef(messages)  // 强制触发响应式更新，实现流式渲染
          currentExpertReasoning.value = { expertId: rcExpertId, expertName: rcExpertName, reasoning: block.reasoning }
          scrollToBottom()
        }
        break
      }

      case 'reasoning_done': {
        const rdExpertId = (data.expert_id as string) || '__model__'
        expertReasonings.value.set(rdExpertId, (data.reasoning as string) || '')

        // 不自动折叠思考块，由用户手动点击收起

        triggerRef(messages)  // 强制触发响应式更新
        currentExpertReasoning.value = null
        scrollToBottom()
        break
      }

      case 'session_created':
        if (data.session_id) {
          currentSessionId.value = data.session_id as string
        }
        if (typeof data.protocol_version === 'number') {
          protocolVersion.value = data.protocol_version
        }
        break

      case 'done':
        msg.isStreaming = false
        msg.isSynthesizing = false
        msg.isDiagnosing = false
        isGeneratingAnswer.value = false
        if (data.session_id && !currentSessionId.value) {
          currentSessionId.value = data.session_id as string
        }
        // 最终防护：清理残留的 pending thinking 指示器（兜底）
        cleanupPendingThinking(msg)
        // 修复所有未完成的工具调用状态
        if (msg.thinkingSteps) {
          for (const step of msg.thinkingSteps) {
            if (step.isToolCall && (step.status === 'receiving' || step.status === 'running')) {
              step.status = 'success'
            }
          }
        }
        if (msg.toolCalls) {
          for (const tc of msg.toolCalls) {
            if (!tc.status || tc.status === 'receiving' || tc.status === 'running') {
              tc.status = 'success'
            }
          }
        }
        // Phase 2.1a: flatten blocks → content（确保 DB 存储纯文本）
        if (msg.blocks && msg.blocks.length > 0 && !msg.content) {
          msg.content = msg.blocks
            .filter((b: any) => b.type === 'text' && b.visible !== false)
            .map((b: any) => b.content || '')
            .join('')
        }
        break

      // Phase 7: 客户端工具请求
      case 'client_tool': {
        const toolName = (data.tool_name as string) || ''
        const toolInput = (data.tool_input as Record<string, unknown>) || {}
        const clientToolType = (data.client_tool_type as string) || ''

        // MIG-2: ask_plan_question → 内联 PlanQuestion 组件
        if (toolName === 'ask_plan_question') {
          // 找到当前正在执行的步骤标题
          const runningStep = msg.executionPlan?.steps.find(s => s.status === 'running')
          msg.pendingPlanQuestion = {
            toolUseId: (data.tool_use_id as string) || '',
            question: (toolInput.question as string) || '',
            options: (toolInput.options as string[]) || undefined,
            context: (toolInput.context as string) || undefined,
            stepTitle: runningStep?.title,
          }
        } else if (clientToolType === 'standalone_plugin') {
          // standalone 插件 → 用 tool_call_id 关联已有 ToolCall，存 tool_use_id 供回传
          if (!msg.toolCalls) msg.toolCalls = []
          const originToolCallId = (data.tool_call_id as string) || (data.tool_use_id as string) || ''
          const toolUseId = (data.tool_use_id as string) || ''
          const existingTC = msg.toolCalls.find(
            (t: ToolCall) => t.toolCallId === originToolCallId
          )
          if (existingTC) {
            existingTC.pendingToolUseId = toolUseId
          }
          // 防御：tool_call_start 未到时创建完整 ToolCall
          if (!existingTC && data.standalone_plugin) {
            const sp = data.standalone_plugin as Record<string, unknown>
            msg.toolCalls.push({
              tool: toolName,
              toolDisplayName: (data.tool_label as string) || '',
              toolCallId: originToolCallId,
              input: toolInput,
              pendingToolUseId: toolUseId,
              standalonePlugin: {
                pluginIdentifier: (sp.plugin_identifier as string) || '',
                originalIdentifier: (sp.original_identifier as string) || '',
                originalApiName: (sp.original_api_name as string) || '',
                iframeUrl: (sp.iframe_url as string) || '',
                iframeHeight: (sp.iframe_height as number) || 350,
                iframeWidth: (sp.iframe_width as number) || 400,
              },
            })
          }
        } else {
          msg.pendingClientTool = {
            toolUseId: (data.tool_use_id as string) || '',
            toolName,
            toolLabel: (data.tool_label as string) || toolName,
            toolInput,
          }
        }
        break
      }

      // Phase 8: Plan Mode 事件
      case 'plan_created': {
        const steps: PlanStepState[] = ((data.steps as Array<{ id: string; title: string; description: string; depends_on?: string[] }>) || []).map(s => ({
          id: s.id,
          title: s.title,
          description: s.description,
          status: 'pending' as const,
          dependsOn: s.depends_on || [],
        }))
        msg.executionPlan = {
          planId: (data.plan_id as string) || '',
          title: (data.title as string) || '',
          summary: (data.summary as string) || '',
          steps,
          status: 'created',
          customStages: !!(data.custom_stages),
          visibleStages: (data.visible_stages as Record<string, boolean>) || {},
        }
        break
      }

      case 'plan_submitted': {
        if (msg.executionPlan) {
          msg.executionPlan.status = 'submitted'
        }
        break
      }

      case 'plan_approved': {
        if (msg.executionPlan) {
          msg.executionPlan.status = 'approved'
          // 如果后端返回了更新后的 steps（用户编辑过），同步到前端
          if (data.steps && Array.isArray(data.steps)) {
            msg.executionPlan.steps = data.steps.map((s: any) => ({
              id: s.id,
              title: s.title,
              description: s.description,
              status: 'pending' as const,
              dependsOn: s.depends_on || [],
            }))
          }
        }
        break
      }

      case 'plan_rejected': {
        if (msg.executionPlan) {
          msg.executionPlan.status = 'rejected'
        }
        break
      }

      case 'plan_step': {
        if (msg.executionPlan) {
          let step = msg.executionPlan.steps.find(s => s.id === data.step_id)
          // P2-2: 动态插入的步骤（质量评估、补充搜索）— 幂等 upsert
          if (!step) {
            const insertIndex = typeof data.step_index === 'number'
              ? Math.min(data.step_index, msg.executionPlan.steps.length)
              : msg.executionPlan.steps.length
            const newStep: PlanStepState = {
              id: (data.step_id as string) || '',
              title: (data.title as string) || '',
              description: '',
              status: 'pending' as const,
            }
            msg.executionPlan.steps.splice(insertIndex, 0, newStep)
            step = msg.executionPlan.steps[insertIndex]
          }
          if (step) {
            // 新步骤开始 running
            if (data.status === 'running' && step.status !== 'running') {
              const stageName = data.stage_name as string || ''
              if (stageName) {
                currentPhase.value = stageName
              }
              const isOutputStage = stageName === 'write' || stageName === 'revise'
              if (isOutputStage) {
                const separator = msg.content.length > 0 ? '\n\n' : ''
                msg.content += separator
              }
              // invisible stage 开始执行时，在正文区插入进度占位
              if (msg.executionPlan?.customStages && stageName
                  && msg.executionPlan.visibleStages?.[stageName] === false) {
                const stepTitle = step.title || stageName
                const total = msg.executionPlan.steps.length
                const idx = msg.executionPlan.steps.indexOf(step) + 1
                msg.content += `\n\n<!-- STAGE_PROGRESS:${stageName}:${idx}/${total}:${stepTitle} -->\n\n`
              }
            }
            step.status = (data.status as PlanStepState['status']) || step.status
            if (data.title) step.title = data.title as string
            if (data.output) step.output = data.output as string
            if (data.reason) step.skipReason = data.reason as string
            if (typeof data.retryCount === 'number') step.retryCount = data.retryCount
            if (typeof data.maxRetries === 'number') step.maxRetries = data.maxRetries

            // invisible 阶段完成后，标记隐藏（不物理删除，避免晚到事件竞态）
            if ((step.status === 'completed' || step.status === 'failed') && msg.executionPlan?.visibleStages) {
              const stageName = data.stage_name as string ||
                (step.id.startsWith('stage_') ? step.id.slice(6) : step.id)
              if (msg.executionPlan.visibleStages[stageName] === false) {
                // 标记工具调用隐藏
                if (msg.thinkingSteps) {
                  msg.thinkingSteps.forEach((s: any) => {
                    if (s.stageName === stageName) s.isHidden = true
                  })
                }
                // 标记 SubAgent 隐藏
                if (msg.subAgents) {
                  msg.subAgents.forEach(sa => {
                    if (sa.stageName === stageName) sa.isHidden = true
                  })
                }
                // 从正文区移除该 invisible 阶段的内容（已同步到 stageOutputs/chip）
                const stageStartMarker = `<!-- STAGE_CONTENT_START:${stageName} -->`
                const startIdx = msg.content.indexOf(stageStartMarker)
                if (startIdx !== -1) {
                  // 找到下一个阶段的开始（---\n\n### 或下一个 STAGE_CONTENT_START）或内容末尾
                  const afterStart = msg.content.indexOf('\n\n---\n\n', startIdx + stageStartMarker.length)
                  const nextStageStart = msg.content.indexOf('<!-- STAGE_CONTENT_START:', startIdx + 1)
                  let endIdx = msg.content.length
                  if (afterStart !== -1 && (nextStageStart === -1 || afterStart < nextStageStart)) {
                    endIdx = afterStart + '\n\n---\n\n'.length
                  } else if (nextStageStart !== -1) {
                    // 找到 nextStageStart 前面的 ---\n\n 分隔符
                    const dividerBefore = msg.content.lastIndexOf('\n\n---\n\n', nextStageStart)
                    endIdx = dividerBefore !== -1 && dividerBefore >= startIdx ? dividerBefore : nextStageStart
                  }
                  msg.content = msg.content.slice(0, startIdx) + msg.content.slice(endIdx)
                }
                // 同时移除进度占位标记
                msg.content = msg.content.replace(
                  new RegExp(`\\n*<!-- STAGE_PROGRESS:${stageName}:[^>]* -->\\n*`, 'g'), ''
                )
              }
            }
          }
          // 如果有步骤在 running，标记计划为 executing
          if (msg.executionPlan.steps.some(s => s.status === 'running')) {
            msg.executionPlan.status = 'executing'
          }
        }
        break
      }

      case 'plan_completed': {
        if (msg.executionPlan) {
          msg.executionPlan.status = 'completed'
        }
        break
      }

      case 'outline_preview': {
        // 聊天侧大纲暂停交互：显示 OutlineEditor
        msg.pendingOutline = {
          outline: data.outline || data,
          planId: (data.plan_id as string) || '',
          sessionId: (data.session_id as string) || opts.currentSessionId.value,
          timeout: (data.timeout as number) || 180,
        }
        triggerRef(opts.messages)
        opts.scrollToBottom(true)
        break
      }

      case 'plan_step_content_reset': {
        const resetStageName = data.stage_name as string || ''
        if (resetStageName === 'write' || resetStageName === 'revise') {
          msg.content = ''
        }
        break
      }

      case 'aggregation_progress': {
        // Aggregation@N 聚合进度事件
        msg.aggregationProgress = {
          done: (data.done as number) || 0,
          total: (data.total as number) || 0,
          phase: (data.phase as 'research' | 'synthesis' | 'complete') || 'research',
          paths: ((data.paths as Array<any>) || []).map((p: any) => ({
            index: p.index ?? 0,
            label: p.label || '',
            status: p.status || 'pending',
            refs_count: p.refs_count ?? 0,
            tool_calls: p.tool_calls ?? 0,
            duration_s: p.duration_s ?? 0,
          })),
        }
        break
      }

      case 'synthesis_content': {
        // 聚合合成流式内容
        const synthText = (data.text as string) || ''
        if (synthText) {
          if (!msg.synthesisContent) msg.synthesisContent = ''
          msg.synthesisContent += synthText
        }
        break
      }

      case 'analyze_plan': {
        msg.analyzePlan = {
          topicCategory: (data.topic_category as string) || '',
          researchTopic: (data.research_topic as string) || '',
          keyQuestions: (data.key_questions as string[]) || [],
          searchSteps: ((data.search_steps as Array<any>) || []).map((s: any) => ({
            stepId: s.step_id,
            toolDisplay: s.tool_display,
            keywords: s.keywords,
            language: s.language,
            purpose: s.purpose,
          })),
        }
        break
      }

      case 'parallel_decompose': {
        const subtasks = data.subtasks as Array<{ id: string; title: string; scope: string; depends_on?: string[] }>
        if (subtasks) {
          msg.parallelSubtasks = subtasks.map(t => ({
            ...t,
            dependsOn: t.depends_on || [],
            status: 'pending' as const,
          }))
        }
        currentPhase.value = 'parallel'
        break
      }

      case 'parallel_subtask_start': {
        if (msg.parallelSubtasks) {
          const task = msg.parallelSubtasks.find(t => t.id === data.sub_task_id)
          if (task) {
            task.status = 'running'
            task.startTime = Date.now()
          }
        }
        break
      }

      case 'parallel_subtask_progress': {
        if (msg.parallelSubtasks) {
          const task = msg.parallelSubtasks.find(t => t.id === data.sub_task_id)
          if (task) {
            task.stageName = data.stage as string
          }
        }
        break
      }

      case 'parallel_subtask_content': {
        if (msg.parallelSubtasks) {
          const task = msg.parallelSubtasks.find(t => t.id === data.sub_task_id)
          if (task) {
            // B5 fix: 只接受当前版本的 content 事件
            const evVersion = (data.content_version as number) ?? 0
            if (evVersion >= (task.contentVersion ?? 0)) {
              const text = (data.text || '') as string
              // 根据 stage 分区存储：research → researchContent, write/缺失 → content
              if (data.stage === 'research') {
                task.researchContent = (task.researchContent || '') + text
              } else {
                task.content = (task.content || '') + text
              }
              task.toolName = undefined
              // Q3 debug: 追踪 content 累积
              console.debug(`[Q3] ${data.sub_task_id} stage=${data.stage} +${text.length}ch → content=${(task.content||'').length} rc=${(task.researchContent||'').length}`)
            }
          }
        }
        break
      }

      case 'parallel_subtask_tool': {
        if (msg.parallelSubtasks) {
          const task = msg.parallelSubtasks.find(t => t.id === data.sub_task_id)
          if (task) {
            task.toolName = data.tool_name as string
          }
        }
        break
      }

      case 'parallel_subtask_done': {
        if (msg.parallelSubtasks) {
          const task = msg.parallelSubtasks.find(t => t.id === data.sub_task_id)
          if (task) {
            task.status = data.status as 'done' | 'failed' | 'cancelled'
            if (data.error) task.error = data.error as string
            task.isRetrying = false
            // B6 fix: 存储仅 write 阶段的最终内容，用于降级显示
            if (data.final_content) task.finalContent = data.final_content as string
          }
        }
        break
      }

      case 'parallel_synthesizing': {
        if (msg) msg.isSynthesizing = true
        currentPhase.value = 'answer'
        break
      }

      case 'parallel_subtask_content_reset': {
        // lazy-replace: write 阶段首个 chunk 到达时清空旧内容，保持面板展开
        const resetTask = msg.parallelSubtasks?.find((t: any) => t.id === data.sub_task_id)
        if (resetTask) {
          resetTask.content = ''
          resetTask.retryReason = data.reason as string
          // B5 fix: 更新版本号，使旧版本的在途 content 事件被忽略
          resetTask.contentVersion = (data.content_version as number) ?? (resetTask.contentVersion ?? 0)
          // 不关闭面板，让用户看到新内容开始流入
        }
        break
      }

      case 'parallel_subtask_scope_retry': {
        const subtask = msg.parallelSubtasks?.find((t: any) => t.id === data.sub_task_id)
        if (subtask) {
          subtask.status = 'running'
          subtask.stageName = '聚焦重试中...'
          subtask.retryReason = data.reason as string
          subtask.isRetrying = true
          // B5 fix: 更新版本号，旧版本在途 content 事件将被忽略
          if (data.content_version != null) subtask.contentVersion = data.content_version as number
        }
        break
      }

      case 'parallel_diagnosing': {
        if (msg) {
          msg.isDiagnosing = true
          msg.diagnosticPhase = '正在优化报告质量...'
        }
        break
      }

      case 'diagnostic_escalation': {
        if (msg) {
          msg.diagnosticReport = {
            rounds: (data.rounds as number) || 0,
            fixed: (data.fixed as boolean) || false,
            should_escalate: (data.should_escalate as boolean) || false,
            score_history: (data.score_history as number[]) || [],
            token_consumed: (data.token_consumed as number) || 0,
            evidence_log: (data.evidence_log as DiagnosticReportData['evidence_log']) || [],
          }
          msg.isDiagnosing = false
        }
        break
      }

      case 'peer_review': {
        if (msg) {
          msg.peerReview = {
            overall_score: (data.overall_score as number) || 0,
            items: (data.items as PeerReviewData['items']) || [],
            has_critical: (data.has_critical as boolean) || false,
            parse_failed: (data.parse_failed as boolean) || false,
            token_consumed: (data.token_consumed as number) || 0,
          }
        }
        break
      }

      case 'parallel_progress': {
        msg.parallelProgress = {
          completed: (data.completed as Array<{ sub_task_id: string; status: string }>) || [],
          failed: (data.failed as Array<{ sub_task_id: string; error: string }>) || [],
          total: (data.total as number) || 0,
        }
        break
      }

      case 'verification': {
        msg.verification = {
          passed: data.passed as boolean,
          score: (data.score as number) || 0,
          issues: (data.issues as Array<{ type: string; severity: string; message: string }>) || [],
          dimensions: (data.dimensions as Array<{ name: string; score: number; reasoning: string; weight: number }>) || undefined,
          llm_score: data.llm_score as number | undefined,
          judge_model: data.judge_model as string | undefined,
          judge_mode: data.judge_mode as string | undefined,
          duration_ms: data.duration_ms as number | undefined,
        }
        break
      }

      case 'cancelled':
        msg.isStreaming = false
        isGeneratingAnswer.value = false
        isSkillExecuting.value = false
        // 清除待处理的 client tool
        msg.pendingClientTool = undefined
        // 标记所有 running 的子代理为 cancelled
        if (msg.subAgents) {
          for (const agent of msg.subAgents) {
            if (agent.status === 'running') {
              agent.status = 'failed'
              agent.error = '已取消'
              agent.duration = Date.now() - agent.startTime
            }
          }
        }
        // 标记所有 running/receiving 的工具调用为取消
        if (msg.thinkingSteps) {
          for (const step of msg.thinkingSteps) {
            if (step.status === 'running' || step.status === 'receiving' || step.status === 'pending') {
              step.status = 'error'
              step.summary = '已取消'
            }
          }
        }
        break

      case 'error': {
        const errorCode = (data.error_code as string) || (data.code as string) || ''
        // v4.1: 统一 Skill 错误事件处理（含超时、执行异常、质量失败）
        if (['generation_quality_failed', 'SKILL_ERROR', 'SKILL_TIMEOUT'].includes(errorCode)) {
          const lastUserMsg = [...messages.value].reverse().find(m => m.role === 'user')
          msg.errorInfo = {
            code: errorCode,
            message: (data.message as string) || '处理遇到问题',
            retryable: data.retryable !== false,
            originalQuestion: lastUserMsg?.content || '',
            skill: (data.skill as string) || '',
          }
          msg.isStreaming = false
          isGeneratingAnswer.value = false
          isSkillExecuting.value = false
        } else {
          msg.content = data.message || '发生错误'
          msg.isStreaming = false
          isGeneratingAnswer.value = false
          isSkillExecuting.value = false
          messageApi.error(data.message || '处理出错')
        }
        break
      }

      // Phase 4: 流式工具调用
      // FIX-3: tool_call_start 是统一的工具调用开始事件
      case 'tool_call_start': {
        // done 是内部控制工具，不显示给用户
        if (data.tool_name === 'done') break
        currentPhase.value = inferPhaseFromSkill(currentSkill.value)
        const tcsToolName = data.tool_name || ''
        const tcsDisplayName = data.tool_display_name || tcsToolName
        updateDataSourceStatus(tcsToolName, 'trying')

        // 清理之前的 pending 状态 thinking 步骤
        cleanupPendingThinking(msg)

        if (!msg.thinkingSteps) msg.thinkingSteps = []
        // 幂等检查：同一 tool_call_id 不重复创建（后端可能发多次 tool_call_start）
        const tcsExistingStep = data.tool_call_id
          ? msg.thinkingSteps.find((s: ThinkingStep) => s.toolCallId === data.tool_call_id && s.isToolCall)
          : undefined
        if (tcsExistingStep) {
          // 已存在，更新状态即可
          tcsExistingStep.status = 'receiving'
          tcsExistingStep.toolName = tcsToolName
          tcsExistingStep.toolDisplayName = tcsDisplayName
          // 聚合模式：补设路径归属（如果之前未设置）
          if (typeof data.path_index === 'number' && tcsExistingStep.pathIndex === undefined) {
            tcsExistingStep.pathIndex = data.path_index
            tcsExistingStep.pathLabel = data.path_label || ''
          }
        } else {
          const tcsNewStep: ThinkingStep = {
            text: tcsDisplayName,
            step: msg.thinkingSteps.length + 1,
            toolCallId: data.tool_call_id,
            toolName: tcsToolName,
            toolDisplayName: tcsDisplayName,
            status: 'receiving',
            isToolCall: true,
            startTime: Date.now(),
            inputRaw: '',
          }
          // 聚合模式：标记路径归属
          if (typeof data.path_index === 'number') {
            tcsNewStep.pathIndex = data.path_index
            tcsNewStep.pathLabel = data.path_label || ''
          }
          // 标记阶段归属（区分 research vs targeted_research 等）
          if (data.stage_name) {
            tcsNewStep.stageName = data.stage_name
          }
          msg.thinkingSteps.push(tcsNewStep)

          // 自定义可见阶段：在内容流中插入工具调用锚点（交错渲染用）
          if (msg.executionPlan?.customStages && data.stage_name
              && msg.executionPlan.visibleStages?.[data.stage_name as string] !== false
              && data.tool_call_id) {
            msg.content += `\n\n<!-- TC:${data.tool_call_id} -->\n\n`
          }
        }

        // 创建 toolCalls 记录（与 action handler 对齐）
        if (!msg.toolCalls) msg.toolCalls = []
        const tcsExistingTC = data.tool_call_id
          ? msg.toolCalls.find((t: ToolCall) => t.toolCallId === data.tool_call_id)
          : null
        if (!tcsExistingTC) {
          const newTC: ToolCall = {
            tool: tcsToolName,
            toolDisplayName: tcsDisplayName,
            toolCallId: data.tool_call_id,
            stageName: data.stage_name as string | undefined,
            input: data.input || {},
          }
          // standalone 插件 → 存储 iframe 元数据（不含 settings）
          if (data.standalone_plugin) {
            const sp = data.standalone_plugin as Record<string, unknown>
            newTC.standalonePlugin = {
              pluginIdentifier: (sp.plugin_identifier as string) || '',
              originalIdentifier: (sp.original_identifier as string) || '',
              originalApiName: (sp.original_api_name as string) || '',
              iframeUrl: (sp.iframe_url as string) || '',
              iframeHeight: (sp.iframe_height as number) || 350,
              iframeWidth: (sp.iframe_width as number) || 400,
            }
          }
          msg.toolCalls.push(newTC)
        } else if (data.standalone_plugin && !tcsExistingTC.standalonePlugin) {
          // 已有 TC 但缺少 standalonePlugin → 补充（防御性更新）
          const sp = data.standalone_plugin as Record<string, unknown>
          tcsExistingTC.standalonePlugin = {
            pluginIdentifier: (sp.plugin_identifier as string) || '',
            originalIdentifier: (sp.original_identifier as string) || '',
            originalApiName: (sp.original_api_name as string) || '',
            iframeUrl: (sp.iframe_url as string) || '',
            iframeHeight: (sp.iframe_height as number) || 350,
            iframeWidth: (sp.iframe_width as number) || 400,
          }
        }
        break
      }

      case 'tool_call_delta': {
        if (msg.thinkingSteps) {
          const step = msg.thinkingSteps.find(
            (s: ThinkingStep) => s.toolCallId === data.tool_call_id
          )
          if (step) {
            step.inputRaw = (step.inputRaw || '') + (data.arguments_delta || '')
            try {
              const parsed = JSON.parse(step.inputRaw)
              step.input = parsed
              // 同步更新 toolCalls 中的 input（tool_call_start 时 input 通常为空）
              if (msg.toolCalls) {
                const tc = msg.toolCalls.find((t: ToolCall) => t.toolCallId === data.tool_call_id)
                if (tc) tc.input = parsed
              }
            } catch { /* partial JSON */ }
          }
        }
        break
      }

      // NEW-3A: 学术检索数据源进度事件
      case 'data_source_progress': {
        const source = (data.source as string) || ''
        const error = data.error as string | undefined
        // 映射后端状态到前端 DataSourceStatus
        const statusMap: Record<string, DataSourceStatus['status']> = {
          'searching': 'trying',
          'completed': 'success',
          'failed': 'failed',
        }
        const mappedStatus = statusMap[data.status as string] || 'trying'
        updateDataSourceStatus(source, mappedStatus, error)
        break
      }

      case 'heartbeat':
        // P1-B: 后端心跳事件
        resetHeartbeat()
        break

      // ==================== 事件补全（阶段 6）====================

      case 'llm_usage': {
        // 累积 Token 统计
        if (!msg.tokenUsage) {
          msg.tokenUsage = { inputTokens: 0, outputTokens: 0 }
        }
        msg.tokenUsage.inputTokens += (data.prompt_tokens as number) ?? (data.input_tokens as number) ?? 0
        msg.tokenUsage.outputTokens += (data.completion_tokens as number) ?? (data.output_tokens as number) ?? 0
        break
      }

      case 'expert_intro': {
        // 辩论模式：专家介绍信息
        const expertId = (data.expert_id as string) || ''
        const intro = (data.intro as string) || (data.description as string) || ''
        if (expertId && intro && msg.subAgents) {
          const agent = msg.subAgents.find(a => a.agentId === expertId)
          if (agent) {
            (agent as any).intro = intro
          }
        }
        break
      }

      case 'speech_chunk': {
        // 辩论模式：专家发言 → 映射到 reasoning_chunk 逻辑
        const speakerId = (data.expert_id as string) || ''
        const speakerName = (data.expert_name as string) || ''
        const chunk = (data.text as string) || (data.chunk as string) || ''
        if (speakerId && chunk) {
          currentExpertReasoning.value = {
            expertId: speakerId,
            expertName: speakerName,
            reasoning: (currentExpertReasoning.value?.expertId === speakerId
              ? currentExpertReasoning.value.reasoning : '') + chunk,
          }
          const existing = expertReasonings.value.get(speakerId) || ''
          expertReasonings.value.set(speakerId, existing + chunk)
        }
        break
      }

      case 'expert_audio': {
        // 辩论模式：专家语音就绪
        const audioExpertId = (data.expert_id as string) || ''
        const audioFileId = (data.file_id as string) || ''
        const audioRound = (data.round_num as number) || 0
        if (audioExpertId && audioFileId && msg.subAgents) {
          const agent = msg.subAgents.find(a => a.agentId === audioExpertId)
          if (agent) {
            if (!(agent as any).audioSegments) (agent as any).audioSegments = []
            ;(agent as any).audioSegments.push({
              round: audioRound,
              fileId: audioFileId,
              voice: (data.voice as string) || '',
              durationMs: (data.duration_ms as number) || 0,
            })
          }
        }
        break
      }

      case 'iteration_start': {
        // 迭代开始：静默消费，仅记录日志（调试用）
        break
      }

      // ==================== 课堂教学事件 ====================
      case 'classroom_outline_ready':
      case 'classroom_scene_generated':
      case 'classroom_scene_audio_ready':
      case 'classroom_scene_failed':
      case 'classroom_ready':
      case 'classroom_scene_change':
      case 'classroom_action_start':
      case 'classroom_action_done':
      case 'classroom_status_change':
      case 'classroom_discussion_start':
      case 'classroom_discussion_message':
      case 'classroom_discussion_speaker_change':
      case 'classroom_discussion_end': {
        // 课堂事件统一存储到 msg.classroomData
        if (!msg.classroomData) {
          msg.classroomData = {
            sessionId: (data.session_id as string) || '',
            status: 'generating',
            events: [],
            scenes: [],  // 实时构建的场景数组，供缩略图渲染
          }
        }
        msg.classroomData.events.push({ type: event, data, ts: Date.now() })

        // 更新关键状态
        if (event === 'classroom_ready' || event === 'classroom_status_change') {
          msg.classroomData.status = (data.status as string) || msg.classroomData.status
          if (data.session_id) msg.classroomData.sessionId = data.session_id as string
          if ('can_enter' in data) msg.classroomData.canEnter = data.can_enter === true
          if ('first_usable_scene' in data) msg.classroomData.firstUsableScene = data.first_usable_scene as number
          if ('first_ready_scene' in data) msg.classroomData.firstReadyScene = data.first_ready_scene as number
        }
        if (event === 'classroom_ready') {
          msg.classroomData.totalScenes = (data.total as number) || 0
        }

        // 实时构建 scenes 数组（边生成边预览）
        if (event === 'classroom_outline_ready') {
          // 大纲就绪 → 占位场景（骨架）
          const idx = data.index as number
          if (!msg.classroomData.scenes) msg.classroomData.scenes = []
          msg.classroomData.scenes[idx] = {
            scene_index: idx,
            title: (data.outline as any)?.title || '',
            scene_type: (data.outline as any)?.scene_type || 'slide',
            status: 'outline_ready',
            content: null,
          }
        }
        if (event === 'classroom_scene_generated') {
          // 内容就绪 → 填入完整 SlideJSON
          const idx = data.scene_index as number
          if (!msg.classroomData.scenes) msg.classroomData.scenes = []
          if (msg.classroomData.scenes[idx]) {
            msg.classroomData.scenes[idx].status = (data.status as string) || 'content_ready'
            msg.classroomData.scenes[idx].content = data.content || null
            msg.classroomData.scenes[idx].speaker_notes = (data.speaker_notes as string) || ''
            msg.classroomData.scenes[idx].actions = (data.content as any)?.actions || []
          } else {
            msg.classroomData.scenes[idx] = {
              scene_index: idx,
              title: (data.title as string) || '',
              scene_type: (data.scene_type as string) || 'slide',
              status: (data.status as string) || 'content_ready',
              content: data.content || null,
              speaker_notes: (data.speaker_notes as string) || '',
              actions: (data.content as any)?.actions || [],
            }
          }
        }
        if (event === 'classroom_scene_audio_ready') {
          const idx = data.scene_index as number
          if (msg.classroomData.scenes?.[idx]) {
            msg.classroomData.scenes[idx].audio_file_id = (data.audio_file_id as string) || ''
            msg.classroomData.scenes[idx].status = 'audio_ready'
            if (data.actions) {
              msg.classroomData.scenes[idx].actions = data.actions as any[]
            }
          }
        }

        // ─── 讨论事件 → 存入 classroomData.discussionMessages ───
        if (event === 'classroom_discussion_start') {
          if (!msg.classroomData.discussionMessages) msg.classroomData.discussionMessages = []
          msg.classroomData.discussionTopic = (data.topic as string) || ''
          msg.classroomData.discussionParticipants = (data.participants as any[]) || []
        }
        if (event === 'classroom_discussion_message') {
          if (!msg.classroomData.discussionMessages) msg.classroomData.discussionMessages = []
          const isDelta = !!(data.is_delta)
          if (isDelta && msg.classroomData.discussionMessages.length > 0) {
            // 增量模式：追加到最后一条同角色消息
            const last = msg.classroomData.discussionMessages[msg.classroomData.discussionMessages.length - 1]
            if (last && last.role === data.role) {
              last.content += (data.content as string) || ''
              last.isDelta = true
            } else {
              msg.classroomData.discussionMessages.push({
                id: `msg_${Date.now()}`,
                role: (data.role as string) || 'teacher',
                name: (data.name as string) || '',
                content: (data.content as string) || '',
                isDelta: true,
              })
            }
          } else {
            msg.classroomData.discussionMessages.push({
              id: `msg_${Date.now()}`,
              role: (data.role as string) || 'teacher',
              name: (data.name as string) || '',
              content: (data.content as string) || '',
              isDelta: false,
            })
          }
        }
        if (event === 'classroom_discussion_speaker_change') {
          // 标记当前发言者（供气泡组件使用）
          msg.classroomData.currentSpeaker = (data.speaker as string) || ''
          msg.classroomData.currentSpeakerName = (data.name as string) || ''
        }
        if (event === 'classroom_discussion_end') {
          msg.classroomData.discussionSummary = (data.summary as string) || ''
        }
        break
      }

      // Perplexica 迁移：搜索建议
      case 'suggestions': {
        import('../stores/searchSuggestions').then(({ useSearchSuggestionsStore }) => {
          const store = useSearchSuggestionsStore()
          store.setSuggestions((data.items as Array<{ text: string; type: 'deeper' | 'broader' }>) || [], currentSessionId.value)
        })
        break
      }

      // Skill 推荐链：skill_done 后推荐后续 Skill
      case 'next_skill_suggestions': {
        msg.nextSkillSuggestions = (data.items as Array<{ skill: string; prompt: string }>) || []
        break
      }

      // Perplexica 迁移：查询改写
      case 'query_rewrite': {
        queryRewriteInfo.value = {
          original: (data.original as string) || '',
          rewritten: (data.rewritten as string) || '',
        }
        break
      }

      // Perplexica 迁移：执行阶段（writing / searching 等）
      case 'phase': {
        const phaseName = (data.phase as string) || ''
        if (phaseName) {
          currentSearchPhase.value = phaseName
          // 将 writing 阶段映射到现有 currentPhase 体系
          if (phaseName === 'writing') {
            currentPhase.value = 'answering'
          } else if (phaseName === 'searching') {
            currentPhase.value = 'search'
          }
        }
        break
      }

      // ──── Phase 2.1a: ContentBlock v2 事件 ────

      case 'content_block_start': {
        if (!msg.blocks) msg.blocks = []
        const blockData = data.block as Record<string, unknown> || {}
        msg.blocks.push({
          ...blockData,
          _id: data.blockId as string,
          _complete: false,
          content: blockData.type === 'text' ? '' : (blockData.content as string || ''),
        } as any)
        break
      }

      case 'content_block_delta': {
        const blockId = data.blockId as string
        if (msg.blocks) {
          const block = msg.blocks.find((b: any) => b._id === blockId)
          if (block && (block as any).type === 'text') {
            ;(block as any).content += (data.delta as string) || ''
          }
        }
        break
      }

      case 'content_block_stop': {
        const stopBlockId = data.blockId as string
        if (msg.blocks) {
          const block = msg.blocks.find((b: any) => b._id === stopBlockId)
          if (block) (block as any)._complete = true
        }
        break
      }

      case 'content_block_update': {
        const updateBlockId = data.blockId as string
        const update = data.update as Record<string, unknown>
        if (msg.blocks && update) {
          const block = msg.blocks.find((b: any) => b._id === updateBlockId)
          if (block) Object.assign(block, update)
        }
        break
      }

      default:
        break
    }

    // 强制触发响应式更新（不创建新数组，避免 GC 压力）
    triggerRef(messages)
  }

  return {
    // 状态
    currentPhase,
    latestEventType,
    latestEventData,
    connectionHealth,
    protocolVersion,
    debugCapture,
    memoryCount,
    // Perplexica 迁移新增状态
    currentSearchPhase,
    queryRewriteInfo,
    // 方法
    handleSSEEvent,
    resetSSEState,
    resetHeartbeat,
    closeHeartbeat,
    updateDataSourceStatus,
    inferPhaseFromSkill,
  }
}
