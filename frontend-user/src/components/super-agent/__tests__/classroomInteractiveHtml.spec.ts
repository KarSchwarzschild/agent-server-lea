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
vi.mock('../InteractivePlayer.vue', () => ({
  default: {
    props: ['htmlContent', 'currentTopic'],
    template: '<div data-test="interactive-html-player">{{ currentTopic }} {{ htmlContent }}</div>',
  },
}))

describe('classroom interactive html widget', () => {
  it('routes html widget content through the sandboxed interactive player', () => {
    const wrapper = mount(ClassroomPlayer, {
      props: {
        data: {
          session_id: 's1',
          topic: '互动模拟',
          status: 'ready',
          current_scene_index: 0,
          total_scenes: 1,
          config: {},
          scenes: [{
            id: 'scene_0',
            title: '互动模拟',
            scene_type: 'interactive',
            content: {
              kind: 'interactive_widget',
              widget_type: 'html',
              prompt: '操作模拟器观察结果',
              html: '<!doctype html><html><body><button>开始</button></body></html>',
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

    expect(wrapper.get('[data-test="interactive-html-player"]').text()).toContain('互动模拟')
    expect(wrapper.get('[data-test="interactive-html-player"]').text()).toContain('<button>开始</button>')
    expect(wrapper.text()).not.toContain('完整互动组件尚未启用')
  })
})
