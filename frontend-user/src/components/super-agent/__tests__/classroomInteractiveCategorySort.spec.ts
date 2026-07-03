import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ClassroomPlayer from '../ClassroomPlayer.vue'

vi.mock('../SlideRenderer.vue', () => ({ default: { template: '<div data-test="slide-renderer" />' } }))
vi.mock('../ClassroomSidebar.vue', () => ({ default: { template: '<aside />', methods: { clearRegenerating() {} } } }))
vi.mock('../ClassroomToolbar.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../ClassroomSpeechBubble.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../SlideHighlightOverlay.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../ClassroomQuizView.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../ClassroomDiscussion.vue', () => ({ default: { template: '<div />' } }))
vi.mock('../ProactiveCard.vue', () => ({ default: { template: '<div />' } }))

describe('classroom interactive category sort widget', () => {
  it('lets learners assign items to categories and see the score', async () => {
    const wrapper = mount(ClassroomPlayer, {
      props: {
        data: {
          session_id: 's1',
          topic: '分类练习',
          status: 'ready',
          current_scene_index: 0,
          total_scenes: 1,
          config: {},
          scenes: [{
            id: 'scene_0',
            title: '分类练习',
            scene_type: 'interactive',
            content: {
              kind: 'interactive_widget',
              widget_type: 'category_sort',
              prompt: '把物品放入正确类别',
              categories: [
                { id: 'recyclable', label: '可回收物' },
                { id: 'kitchen', label: '厨余垃圾' },
              ],
              items: [
                { id: 'paper', text: '废纸', category_id: 'recyclable' },
                { id: 'leaf', text: '菜叶', category_id: 'kitchen' },
              ],
            },
            speaker_notes: '',
            audio_file_id: '',
            audio_duration_ms: 0,
            status: 'content_ready',
            scene_index: 0,
            actions: [],
          }],
        },
      },
    })

    expect(wrapper.text()).toContain('把物品放入正确类别')
    await wrapper.get('[data-test="category-item-paper"]').trigger('click')
    await wrapper.get('[data-test="category-target-recyclable"]').trigger('click')
    await wrapper.get('[data-test="category-item-leaf"]').trigger('click')
    await wrapper.get('[data-test="category-target-kitchen"]').trigger('click')
    await wrapper.get('[data-test="category-submit"]').trigger('click')

    expect(wrapper.text()).toContain('2 / 2')
    expect(wrapper.text()).toContain('分类全部正确')
  })
})
