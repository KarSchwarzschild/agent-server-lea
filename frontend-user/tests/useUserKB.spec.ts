/**
 * useUserKB composable 测试
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock API
vi.mock('../src/api/user-kb', () => ({
  getKnowledgeBase: vi.fn(),
}))

import { useUserKB } from '../src/composables/useUserKB'
import { getKnowledgeBase } from '../src/api/user-kb'

const mockGetKB = vi.mocked(getKnowledgeBase)

describe('useUserKB', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // 重置模块级缓存
    const { invalidateCache } = useUserKB()
    invalidateCache()
  })

  it('有知识库时 hasKnowledgeBase 为 true', async () => {
    mockGetKB.mockResolvedValueOnce({
      id: 1,
      user_id: 'u1',
      kb_id: 'kb-123',
      name: '我的知识库',
      document_count: 0,
      storage_used_mb: 0,
      quota_mb: 102400,
      max_documents: 10000,
      status: 'active',
    } as any)

    const { hasKnowledgeBase, checkKnowledgeBase } = useUserKB()
    await checkKnowledgeBase()

    expect(hasKnowledgeBase.value).toBe(true)
    expect(mockGetKB).toHaveBeenCalledTimes(1)
  })

  it('无知识库时 hasKnowledgeBase 为 false', async () => {
    mockGetKB.mockRejectedValueOnce({
      response: { status: 404 },
    })

    const { hasKnowledgeBase, checkKnowledgeBase } = useUserKB()
    await checkKnowledgeBase()

    expect(hasKnowledgeBase.value).toBe(false)
  })

  it('缓存生效，不重复请求', async () => {
    mockGetKB.mockResolvedValueOnce({
      id: 1,
      user_id: 'u1',
      kb_id: 'kb-123',
      name: '我的知识库',
    } as any)

    const { checkKnowledgeBase } = useUserKB()
    await checkKnowledgeBase()
    await checkKnowledgeBase()

    expect(mockGetKB).toHaveBeenCalledTimes(1)
  })

  it('invalidateCache 后重新请求', async () => {
    mockGetKB.mockResolvedValue({
      id: 1,
      user_id: 'u1',
      kb_id: 'kb-123',
      name: '我的知识库',
    } as any)

    const { checkKnowledgeBase, invalidateCache } = useUserKB()
    await checkKnowledgeBase()
    expect(mockGetKB).toHaveBeenCalledTimes(1)

    invalidateCache()
    await checkKnowledgeBase()
    expect(mockGetKB).toHaveBeenCalledTimes(2)
  })
})
