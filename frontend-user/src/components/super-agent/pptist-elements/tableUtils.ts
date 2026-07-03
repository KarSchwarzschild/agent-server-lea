/**
 * 表格辅助工具
 * 来源：PPTist (MIT License)
 */
import type { CSSProperties } from 'vue'
import { computed, ref, watch, type Ref } from 'vue'
import type { TableCellStyle, TableCell, TableTheme } from './types'

/** 计算单元格文本样式 */
export const getTextStyle = (style?: TableCellStyle): CSSProperties => {
  if (!style) return {}
  const { bold, em, underline, strikethrough, color, backcolor, fontsize, fontname, align } = style
  let textDecoration = `${underline ? 'underline' : ''} ${strikethrough ? 'line-through' : ''}`
  if (textDecoration === ' ') textDecoration = 'none'
  return {
    fontWeight: bold ? 'bold' : 'normal',
    fontStyle: em ? 'italic' : 'normal',
    textDecoration,
    color: color || '#000',
    backgroundColor: backcolor || '',
    fontSize: fontsize || '14px',
    fontFamily: fontname || '',
    textAlign: align || 'left',
  }
}

export const formatText = (text: string) => {
  return text.replace(/\n/g, '</br>').replace(/ /g, '&nbsp;')
}

/** 计算被合并的单元格位置集合 */
export function useHideCells(cells: Ref<TableCell[][]>) {
  const hideCells = computed(() => {
    const result: string[] = []
    for (let i = 0; i < cells.value.length; i++) {
      const rowCells = cells.value[i]
      for (let j = 0; j < rowCells.length; j++) {
        const cell = rowCells[j]
        if (cell.colspan > 1 || cell.rowspan > 1) {
          for (let row = i; row < i + cell.rowspan; row++) {
            for (let col = row === i ? j + 1 : j; col < j + cell.colspan; col++) {
              result.push(`${row}_${col}`)
            }
          }
        }
      }
    }
    return result
  })
  return { hideCells }
}

/** 通过主题色计算辅助颜色 */
export function useSubThemeColor(theme: Ref<TableTheme | undefined>) {
  const subThemeColor = ref(['', ''])
  watch(() => theme.value, () => {
    if (theme.value) {
      // 简化实现：hex → rgba，不依赖 tinycolor2
      const c = theme.value.color
      const hex = c.replace('#', '')
      const r = parseInt(hex.slice(0, 2), 16)
      const g = parseInt(hex.slice(2, 4), 16)
      const b = parseInt(hex.slice(4, 6), 16)
      subThemeColor.value = [
        `rgba(${r},${g},${b},0.3)`,
        `rgba(${r},${g},${b},0.1)`,
      ]
    }
  }, { immediate: true })
  return { subThemeColor }
}
