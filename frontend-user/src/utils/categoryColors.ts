/**
 * 智能体分类颜色工具
 * 6色颜色池 + 动态映射函数
 */

/**
 * 分类颜色池（6色）
 * 与管理后台保持一致
 */
export const CATEGORY_COLOR_POOL = [
  { name: 'primary', bg: '#EDF5FF', border: '#3B82F6', text: '#1D4ED8' },   // 蓝色
  { name: 'success', bg: '#ECFDF5', border: '#10B981', text: '#047857' },   // 绿色
  { name: 'warning', bg: '#FFFBEB', border: '#F59E0B', text: '#B45309' },   // 橙色
  { name: 'info', bg: '#F0F9FF', border: '#06B6D4', text: '#0E7490' },      // 青色
  { name: 'purple', bg: '#FAF5FF', border: '#8B5CF6', text: '#6D28D9' },    // 紫色
  { name: 'pink', bg: '#FDF2F8', border: '#EC4899', text: '#BE185D' },      // 粉色
]

/**
 * 根据分类名称获取颜色
 * 使用索引映射，保证同一分类始终对应同一颜色
 *
 * @param category 分类名称
 * @param allCategories 所有分类列表（用于确定索引）
 * @returns 颜色对象 { bg, border, text }
 */
export function getCategoryColor(category: string, allCategories: string[]) {
  const index = allCategories.indexOf(category)
  const colorIndex = index >= 0 ? index % CATEGORY_COLOR_POOL.length : 0
  return CATEGORY_COLOR_POOL[colorIndex]
}

/**
 * 获取分类的CSS样式对象
 *
 * @param category 分类名称
 * @param allCategories 所有分类列表
 * @returns CSS样式对象
 */
export function getCategoryStyle(category: string, allCategories: string[]) {
  const color = getCategoryColor(category, allCategories)!
  return {
    backgroundColor: color.bg,
    borderColor: color.border,
    color: color.text,
  }
}

/**
 * 获取分类的边框颜色（用于卡片选中状态）
 */
export function getCategoryBorderColor(category: string, allCategories: string[]) {
  const color = getCategoryColor(category, allCategories)!
  return color.border
}
