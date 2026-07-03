import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ClassroomSidebar from '../ClassroomSidebar.vue'

vi.mock('../SlideRenderer.vue', () => ({ default: { template: '<div data-test="slide-renderer" />' } }))

describe('classroom sidebar interactive fallback', () => {
  it('shows a localized interactive thumbnail label instead of the raw scene type', () => {
    const wrapper = mount(ClassroomSidebar, {
      props: {
        currentIndex: 0,
        scenes: [{
          index: 0,
          title: '概念匹配',
          scene_type: 'interactive',
          status: 'content_ready',
          content: {
            fallback_reason: 'interactive_renderer_not_enabled',
            display_message: '此互动内容暂以说明卡展示，完整互动组件尚未启用。',
          },
        }],
      },
      global: {
        provide: {
          playback: {
            engineMode: { value: 'idle' },
          },
        },
      },
    })

    expect(wrapper.text()).toContain('互动')
    expect(wrapper.text()).not.toContain('interactive')
  })
})
