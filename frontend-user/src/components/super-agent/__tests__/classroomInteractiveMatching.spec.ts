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

describe('classroom interactive matching widget', () => {
  it('lets learners complete a matching widget and see the score', async () => {
    const wrapper = mount(ClassroomPlayer, {
      props: {
        data: {
          session_id: 's1',
          topic: '概念匹配',
          status: 'ready',
          current_scene_index: 0,
          total_scenes: 1,
          config: {},
          scenes: [{
            id: 'scene_0',
            title: '概念匹配',
            scene_type: 'interactive',
            content: {
              kind: 'interactive_widget',
              widget_type: 'matching',
              prompt: '匹配概念与例子',
              pairs: [
                { id: 'pair_1', left: '光合作用', right: '植物制造有机物' },
                { id: 'pair_2', left: '呼吸作用', right: '释放能量' },
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

    expect(wrapper.text()).toContain('匹配概念与例子')
    expect(wrapper.text()).not.toContain('完整互动组件尚未启用')

    await wrapper.get('[data-test="matching-left-pair_1"]').trigger('click')
    await wrapper.get('[data-test="matching-right-pair_1"]').trigger('click')
    await wrapper.get('[data-test="matching-left-pair_2"]').trigger('click')
    await wrapper.get('[data-test="matching-right-pair_2"]').trigger('click')
    await wrapper.get('[data-test="matching-submit"]').trigger('click')

    expect(wrapper.text()).toContain('2 / 2')
    expect(wrapper.text()).toContain('全部匹配正确')
  })
})
