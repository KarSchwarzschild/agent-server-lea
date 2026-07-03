/**
 * 搜索建议 Store
 *
 * 管理 Perplexica 风格的搜索建议（deeper/broader 两类）。
 * 生命周期跟随消息轮次，每次发送新消息时由调用方主动 clear()。
 * 支持按 sessionId 缓存，切换会话时恢复上一轮的 suggestions。
 */
import { ref } from 'vue'
import { defineStore } from 'pinia'

export interface SearchSuggestion {
  text: string
  type: 'deeper' | 'broader'
}

export const useSearchSuggestionsStore = defineStore('searchSuggestions', () => {
  const suggestions = ref<SearchSuggestion[]>([])
  // 按 sessionId 缓存最近一轮的 suggestions
  const _cache = new Map<string, SearchSuggestion[]>()
  let _currentSessionId = ''

  function setSuggestions(items: SearchSuggestion[], sessionId?: string) {
    suggestions.value = items
    if (sessionId) {
      _currentSessionId = sessionId
      _cache.set(sessionId, items)
    }
  }

  /** 切换会话时尝试恢复缓存 */
  function restoreForSession(sessionId: string) {
    const cached = _cache.get(sessionId)
    _currentSessionId = sessionId
    suggestions.value = cached || []
  }

  /** 新消息发送时清空当前轮（不清缓存，缓存由 setSuggestions 覆盖） */
  function clear() {
    suggestions.value = []
    if (_currentSessionId) {
      _cache.delete(_currentSessionId)
    }
  }

  return { suggestions, setSuggestions, restoreForSession, clear }
})
