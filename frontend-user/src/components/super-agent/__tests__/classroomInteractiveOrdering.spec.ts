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

describe('classroom interactive ordering widget', () => {
  it('lets learners reorder items and see the score', async () => {
    const wrapper = mount(ClassroomPlayer, {
      props: {
        data: {
          session_id: 's1',
          topic: '实验步骤排序',
          status: 'ready',
          current_scene_index: 0,
          total_scenes: 1,
          config: {},
          scenes: [{
            id: 'scene_0',
            title: '实验步骤排序',
            scene_type: 'interactive',
            content: {
              kind: 'interactive_widget',
              widget_type: 'ordering',
              prompt: '按正确顺序排列实验步骤',
              items: [
                { id: 'step_1', text: '提出假设', order: 1 },
                { id: 'step_2', text: '设计实验', order: 2 },
                { id: 'step_3', text: '记录数据', order: 3 },
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

    expect(wrapper.text()).toContain('按正确顺序排列实验步骤')
    expect(wrapper.text()).not.toContain('完整互动组件尚未启用')

    await wrapper.get('[data-test="ordering-down-step_1"]').trigger('click')
    await wrapper.get('[data-test="ordering-submit"]').trigger('click')
    expect(wrapper.text()).toContain('1 / 3')

    await wrapper.get('[data-test="ordering-up-step_1"]').trigger('click')
    await wrapper.get('[data-test="ordering-submit"]').trigger('click')
    expect(wrapper.text()).toContain('3 / 3')
    expect(wrapper.text()).toContain('顺序完全正确')
  })
})
