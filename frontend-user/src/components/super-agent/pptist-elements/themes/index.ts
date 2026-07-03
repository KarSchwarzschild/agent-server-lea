/**
 * 幻灯片主题预设系统
 * 规划方案 Phase 2b — 6 种主题预设
 */
import type { SlideTheme } from '../../SlideRenderer.vue'

export const THEME_PRESETS: Record<string, SlideTheme> = {
  'edu-warm': {
    id: 'edu-warm',
    primary: '#1a73e8',
    secondary: '#fbbc04',
    background: '#ffffff',
    text: '#202124',
  },
  'tech-blue': {
    id: 'tech-blue',
    primary: '#0d47a1',
    secondary: '#00bcd4',
    background: '#f5f7fa',
    text: '#1a1a2e',
  },
  'nature-green': {
    id: 'nature-green',
    primary: '#2e7d32',
    secondary: '#ff8f00',
    background: '#fafdf7',
    text: '#1b3409',
  },
  'minimal': {
    id: 'minimal',
    primary: '#424242',
    secondary: '#9e9e9e',
    background: '#ffffff',
    text: '#212121',
  },
  'warm-sunset': {
    id: 'warm-sunset',
    primary: '#e65100',
    secondary: '#ff6d00',
    background: '#fff8f0',
    text: '#3e2723',
  },
  'dark-pro': {
    id: 'dark-pro',
    primary: '#64b5f6',
    secondary: '#ffd54f',
    background: '#1e1e2e',
    text: '#e0e0e0',
  },
  // ─── 以下 10 个主题从 OpenMAIC configs/theme.ts 迁移 ───
  'office-blue': {
    id: 'office-blue',
    primary: '#41719c',
    secondary: '#ed7d31',
    background: '#ffffff',
    text: '#333333',
  },
  'cool-teal': {
    id: 'cool-teal',
    primary: '#4495b0',
    secondary: '#2a7886',
    background: '#ffffff',
    text: '#333333',
  },
  'soft-cream': {
    id: 'soft-cream',
    primary: '#782009',
    secondary: '#c47c1b',
    background: '#e9efd6',
    text: '#333333',
  },
  'dark-night': {
    id: 'dark-night',
    primary: '#800c0b',
    secondary: '#e08816',
    background: '#17444e',
    text: '#ffffff',
  },
  'deep-purple': {
    id: 'deep-purple',
    primary: '#830949',
    secondary: '#c24885',
    background: '#36234d',
    text: '#ffffff',
  },
  'ocean-blue': {
    id: 'ocean-blue',
    primary: '#032e45',
    secondary: '#6b3fa0',
    background: '#247fad',
    text: '#ffffff',
  },
  'sea-green': {
    id: 'sea-green',
    primary: '#2d7f8a',
    secondary: '#3f9167',
    background: '#103f55',
    text: '#ffffff',
  },
  'midnight': {
    id: 'midnight',
    primary: '#7d2b8d',
    secondary: '#2e59a7',
    background: '#242367',
    text: '#ffffff',
  },
  'golden': {
    id: 'golden',
    primary: '#b68317',
    secondary: '#946630',
    background: '#e4b75e',
    text: '#333333',
  },
  'dark-charcoal': {
    id: 'dark-charcoal',
    primary: '#893011',
    secondary: '#b58a43',
    background: '#2b2b2d',
    text: '#ffffff',
  },
}

export const THEME_LIST = Object.entries(THEME_PRESETS).map(([id, theme]) => ({
  id,
  ...theme,
}))

export function getThemeById(id?: string): SlideTheme {
  return THEME_PRESETS[id || 'edu-warm'] || THEME_PRESETS['edu-warm']
}

/** 每个主题的中文标签 */
export const THEME_LABELS: Record<string, string> = {
  'edu-warm': '通用教育',
  'tech-blue': '技术蓝',
  'nature-green': '自然绿',
  'minimal': '简约灰',
  'warm-sunset': '暖阳橙',
  'dark-pro': '深色专业',
  'office-blue': 'Office 蓝',
  'cool-teal': '冷色青',
  'soft-cream': '柔和米',
  'dark-night': '深夜青',
  'deep-purple': '深紫',
  'ocean-blue': '海洋蓝',
  'sea-green': '海绿',
  'midnight': '午夜紫',
  'golden': '金色年华',
  'dark-charcoal': '深炭灰',
}
