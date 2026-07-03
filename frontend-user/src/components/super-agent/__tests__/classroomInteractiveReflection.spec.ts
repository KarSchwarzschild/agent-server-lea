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

describe('classroom interactive reflection widget', () => {
  it('collects reflection answers and shows completion feedback', async () => {
    const wrapper = mount(ClassroomPlayer, {
      props: {
        data: {
          session_id: 's1',
          topic: '学习反思',
          status: 'ready',
          current_scene_index: 0,
          total_scenes: 1,
          config: {},
          scenes: [{
            id: 'scene_0',
            title: '学习反思',
            scene_type: 'interactive',
            content: {
              kind: 'interactive_widget',
              widget_type: 'reflection',
              prompt: '写下你的理解',
              questions: ['我学到了什么？', '还有什么问题？'],
              min_length: 4,
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

    await wrapper.get('[data-test="reflection-answer-0"]').setValue('理解概念')
    await wrapper.get('[data-test="reflection-answer-1"]').setValue('继续练习')
    await wrapper.get('[data-test="reflection-submit"]').trigger('click')

    expect(wrapper.text()).toContain('2 / 2')
    expect(wrapper.text()).toContain('反思已记录')
  })
})
