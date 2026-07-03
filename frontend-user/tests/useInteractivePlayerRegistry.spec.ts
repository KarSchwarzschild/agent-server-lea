/**
 * useInteractivePlayerRegistry 单元测试
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { useInteractivePlayerRegistry } from '../src/composables/useInteractivePlayerRegistry'

describe('useInteractivePlayerRegistry', () => {
  let registry: ReturnType<typeof useInteractivePlayerRegistry>

  beforeEach(() => {
    registry = useInteractivePlayerRegistry()
  })

  it('初始状态为空', () => {
    expect(registry.allPlayers.value).toEqual([])
  })

  it('register 新增一个 player', () => {
    registry.register({
      msgId: 'msg1',
      safeHtml: '<html>1</html>',
      topic: '分布式ID需求',
      knowledgeIndex: 1,
    })
    expect(registry.allPlayers.value).toHaveLength(1)
    expect(registry.allPlayers.value[0].msgId).toBe('msg1')
    expect(registry.allPlayers.value[0].topic).toBe('分布式ID需求')
  })

  it('register 多个 player 保持插入顺序', () => {
    registry.register({ msgId: 'msg1', safeHtml: '<html>1</html>', topic: 'A', knowledgeIndex: 1 })
    registry.register({ msgId: 'msg2', safeHtml: '<html>2</html>', topic: 'B', knowledgeIndex: 2 })
    registry.register({ msgId: 'msg3', safeHtml: '<html>3</html>', topic: 'C', knowledgeIndex: 3 })

    const players = registry.allPlayers.value
    expect(players).toHaveLength(3)
    expect(players.map(p => p.msgId)).toEqual(['msg1', 'msg2', 'msg3'])
    expect(players.map(p => p.knowledgeIndex)).toEqual([1, 2, 3])
  })

  it('register 相同 msgId 更新而非重复添加', () => {
    registry.register({ msgId: 'msg1', safeHtml: '<html>v1</html>', topic: '旧名称', knowledgeIndex: 1 })
    registry.register({ msgId: 'msg1', safeHtml: '<html>v2</html>', topic: '新名称', knowledgeIndex: 1 })

    expect(registry.allPlayers.value).toHaveLength(1)
    expect(registry.allPlayers.value[0].safeHtml).toBe('<html>v2</html>')
    expect(registry.allPlayers.value[0].topic).toBe('新名称')
  })

  it('unregister 移除指定 player', () => {
    registry.register({ msgId: 'msg1', safeHtml: '1', topic: 'A', knowledgeIndex: 1 })
    registry.register({ msgId: 'msg2', safeHtml: '2', topic: 'B', knowledgeIndex: 2 })
    registry.register({ msgId: 'msg3', safeHtml: '3', topic: 'C', knowledgeIndex: 3 })

    registry.unregister('msg2')

    const players = registry.allPlayers.value
    expect(players).toHaveLength(2)
    expect(players.map(p => p.msgId)).toEqual(['msg1', 'msg3'])
  })

  it('unregister 不存在的 msgId 不报错', () => {
    registry.register({ msgId: 'msg1', safeHtml: '1', topic: 'A', knowledgeIndex: 1 })
    registry.unregister('nonexistent')
    expect(registry.allPlayers.value).toHaveLength(1)
  })

  it('allPlayers 返回的数组修改不影响内部 reactive 源', () => {
    registry.register({ msgId: 'msg1', safeHtml: '1', topic: 'A', knowledgeIndex: 1 })
    const snapshot = registry.allPlayers.value
    snapshot.push({ msgId: 'fake', safeHtml: 'x', topic: 'X', knowledgeIndex: 99 })
    // 触发 reactive 变更使 computed 重新计算
    registry.register({ msgId: 'msg2', safeHtml: '2', topic: 'B', knowledgeIndex: 2 })
    // 重新计算后不包含之前 push 的 fake 条目
    const fresh = registry.allPlayers.value
    expect(fresh.find(p => p.msgId === 'fake')).toBeUndefined()
    expect(fresh).toHaveLength(2)
  })

  it('全注销后回到空状态', () => {
    registry.register({ msgId: 'msg1', safeHtml: '1', topic: 'A', knowledgeIndex: 1 })
    registry.register({ msgId: 'msg2', safeHtml: '2', topic: 'B', knowledgeIndex: 2 })
    registry.unregister('msg1')
    registry.unregister('msg2')
    expect(registry.allPlayers.value).toEqual([])
  })
})
