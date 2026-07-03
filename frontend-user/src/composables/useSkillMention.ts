/**
 * useSkillMention - @mention 技能选择 composable
 *
 * 管理输入框中 @ 触发的技能选择下拉菜单状态和逻辑。
 * 支持过滤、键盘导航、选择后清除 @文本。
 */
import { ref, computed, type Ref } from 'vue'
import type { Skill } from '../api/superagent'

export interface MentionSkill {
  name: string           // skill name 或 '__debate__'
  display_name: string
  description: string
  category: string
  isSpecial?: boolean    // 辩论等特殊选项
}

export function useSkillMention(skills: Ref<Skill[]>) {
  const showDropdown = ref(false)
  const selectedSkill = ref<MentionSkill | null>(null)
  const dropdownIndex = ref(0)
  const filterText = ref('')
  // @ 符号在输入框中的起始位置
  const mentionStartPos = ref(-1)

  // 构建 mention 列表：辩论 + 所有 skills
  const mentionList = computed<MentionSkill[]>(() => {
    const list: MentionSkill[] = [
      {
        name: '__debate__',
        display_name: '多专家辩论',
        description: '多角度分析讨论',
        category: 'special',
        isSpecial: true,
      },
    ]
    for (const s of skills.value) {
      if (s.enabled !== false) {
        list.push({
          name: s.name,
          display_name: s.display_name || s.name,
          description: s.description || '',
          category: 'skill',
        })
      }
    }
    return list
  })

  // 过滤后的列表
  const filteredList = computed<MentionSkill[]>(() => {
    if (!filterText.value) return mentionList.value
    const q = filterText.value.toLowerCase()
    return mentionList.value.filter(
      s => s.display_name.toLowerCase().includes(q) || s.name.toLowerCase().includes(q)
    )
  })

  /**
   * 检测 @ 触发条件（防止 email 误触发）
   * @ 在行首，或 @ 前面是空格/换行/中文字符
   */
  function shouldTriggerMention(text: string, cursorPos: number): boolean {
    // 找到光标前最近的 @
    const before = text.slice(0, cursorPos)
    const atIdx = before.lastIndexOf('@')
    if (atIdx === -1) return false

    // @ 后面不能有空格（正在输入过滤词）
    const afterAt = text.slice(atIdx + 1, cursorPos)
    if (afterAt.includes(' ') || afterAt.includes('\n')) return false

    // @ 在行首
    if (atIdx === 0) return true

    // @ 前面是空格、换行或中文字符
    const charBefore = text[atIdx - 1]
    if (charBefore === ' ' || charBefore === '\n') return true
    // 中文字符范围
    if (/[\u4e00-\u9fff]/.test(charBefore)) return true

    return false
  }

  /**
   * 输入变化时检测 @ 触发
   * @param text 当前输入框文本
   * @param cursorPos 光标位置
   */
  function handleInput(text: string, cursorPos: number) {
    if (selectedSkill.value) return // 已选中 skill 时不触发

    if (shouldTriggerMention(text, cursorPos)) {
      const before = text.slice(0, cursorPos)
      const atIdx = before.lastIndexOf('@')
      mentionStartPos.value = atIdx
      filterText.value = before.slice(atIdx + 1)
      showDropdown.value = true
      dropdownIndex.value = 0
    } else {
      showDropdown.value = false
      filterText.value = ''
      mentionStartPos.value = -1
    }
  }

  /**
   * 选择 skill
   * @returns 需要替换的文本范围 { start, end }，用于清除输入框中的 @xxx
   */
  function selectSkill(skill: MentionSkill): { start: number; end: number } | null {
    selectedSkill.value = skill
    showDropdown.value = false

    if (mentionStartPos.value >= 0) {
      // 计算需要清除的文本范围（@xxx），在清空 filterText 前保存
      const start = mentionStartPos.value
      const end = start + 1 + filterText.value.length
      filterText.value = ''
      mentionStartPos.value = -1
      return { start, end }
    }
    filterText.value = ''
    mentionStartPos.value = -1
    return null
  }

  /** 清除已选 skill */
  function clearSkill() {
    selectedSkill.value = null
  }

  /**
   * 键盘导航处理
   * @returns true 表示事件已消费，调用方不应继续处理
   */
  function handleKeydown(e: KeyboardEvent): boolean {
    if (!showDropdown.value) return false

    const list = filteredList.value
    if (list.length === 0) return false

    if (e.key === 'ArrowDown') {
      e.preventDefault()
      dropdownIndex.value = (dropdownIndex.value + 1) % list.length
      return true
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      dropdownIndex.value = (dropdownIndex.value - 1 + list.length) % list.length
      return true
    }
    if (e.key === 'Enter' || e.key === 'Tab') {
      e.preventDefault()
      if (list[dropdownIndex.value]) {
        selectSkill(list[dropdownIndex.value])
      }
      return true
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      showDropdown.value = false
      return true
    }
    return false
  }

  return {
    showDropdown,
    filteredList,
    mentionList,
    selectedSkill,
    dropdownIndex,
    handleInput,
    selectSkill,
    clearSkill,
    handleKeydown,
  }
}
