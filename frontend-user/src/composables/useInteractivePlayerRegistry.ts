/**
 * InteractivePlayer 注册表
 *
 * 用于全屏模式下多实例标签切换。
 * SuperAgent.vue provide → InteractivePlayer inject。
 * 每个 InteractivePlayer mount 时注册，unmount 时注销。
 */
import { reactive, computed, type InjectionKey } from 'vue'

export interface PlayerEntry {
  msgId: string
  safeHtml: string
  topic: string
  knowledgeIndex: number
}

export interface InteractivePlayerRegistry {
  register: (entry: PlayerEntry) => void
  unregister: (msgId: string) => void
  allPlayers: ReturnType<typeof computed<PlayerEntry[]>>
}

export function useInteractivePlayerRegistry(): InteractivePlayerRegistry {
  const players = reactive<PlayerEntry[]>([])

  function register(entry: PlayerEntry) {
    const idx = players.findIndex(p => p.msgId === entry.msgId)
    if (idx >= 0) {
      Object.assign(players[idx], entry)
    } else {
      players.push(entry)
    }
  }

  function unregister(msgId: string) {
    const idx = players.findIndex(p => p.msgId === msgId)
    if (idx >= 0) players.splice(idx, 1)
  }

  const allPlayers = computed(() => [...players])

  return { register, unregister, allPlayers }
}

export const INTERACTIVE_PLAYER_REGISTRY_KEY: InjectionKey<InteractivePlayerRegistry> =
  Symbol('interactive-player-registry')
