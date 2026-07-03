/**
 * 用户知识库状态 Composable
 *
 * 提供全局单例缓存，供多个组件判断用户是否已创建知识库。
 */
import { ref } from 'vue'
import type { Ref } from 'vue'
import { getKnowledgeBase } from '../api/user-kb'
import type { UserKnowledgeBase } from '../api/user-kb'

// 模块级缓存（全局单例）
let _cached = false
const _hasKB = ref<boolean | null>(null) // null=未加载
const _kbInfo = ref<UserKnowledgeBase | null>(null)
const _loading = ref(false)

export function useUserKB() {
  async function checkKnowledgeBase() {
    if (_cached && _hasKB.value !== null) return
    _loading.value = true
    try {
      const data = await getKnowledgeBase()
      _kbInfo.value = data
      _hasKB.value = true
      _cached = true
    } catch (error: any) {
      if (error?.response?.status === 404) {
        _hasKB.value = false
        _kbInfo.value = null
        _cached = true
      } else {
        _hasKB.value = null
      }
    } finally {
      _loading.value = false
    }
  }

  function invalidateCache() {
    _cached = false
    _hasKB.value = null
    _kbInfo.value = null
  }

  return {
    hasKnowledgeBase: _hasKB as Ref<boolean | null>,
    knowledgeBaseInfo: _kbInfo as Ref<UserKnowledgeBase | null>,
    loading: _loading as Ref<boolean>,
    checkKnowledgeBase,
    invalidateCache,
  }
}
