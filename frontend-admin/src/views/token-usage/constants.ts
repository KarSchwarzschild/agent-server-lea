/** Token 用量分析 — 共享常量 */

export const FEATURE_LABELS: Record<string, string> = {
  skill_llm: 'Skill/QA 主LLM',
  intent_recognition: '意图识别',
  vision: '图片描述',
  memory_extract: '记忆提取',
  memory_dedup: '记忆去重',
  debate_expert: '辩论专家',
  debate_summary: '辩论总结',
  plan_critic: '调研评估',
  context_qa: '上下文追问',
  rerank: 'Rerank 重排',
  kb_search: '知识库搜索',
}

export const FEATURE_COLOR_MAP: Record<string, string> = {
  skill_llm: '#5470c6',
  intent_recognition: '#91cc75',
  vision: '#fac858',
  memory_extract: '#ee6666',
  memory_dedup: '#73c0de',
  debate_expert: '#3ba272',
  debate_summary: '#fc8452',
  plan_critic: '#9a60b4',
  context_qa: '#ea7ccc',
  rerank: '#48b8d0',
  kb_search: '#d4a373',
}

export function featureLabel(key: string): string {
  return FEATURE_LABELS[key] || key
}

export function featureColor(key: string): string {
  return FEATURE_COLOR_MAP[key] || '#999'
}
