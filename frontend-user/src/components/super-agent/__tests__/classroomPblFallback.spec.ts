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

describe('classroom pbl fallback', () => {
  it('renders a user-friendly disabled state for PBL skeleton scenes', () => {
    const wrapper = mount(ClassroomPlayer, {
      props: {
        data: {
          session_id: 's1',
          topic: '校园节水项目',
          status: 'ready',
          current_scene_index: 0,
          total_scenes: 1,
          config: {},
          scenes: [{
            id: 'scene_0',
            title: '校园节水项目',
            scene_type: 'pbl',
            content: {
              fallback_reason: 'pbl_renderer_not_enabled',
              display_message: '此项目式学习内容暂以项目说明展示，完整 PBL 工作区尚未启用。',
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

    expect(wrapper.text()).toContain('此项目式学习内容暂以项目说明展示')
    expect(wrapper.text()).not.toContain('pbl_renderer_not_enabled')
  })
})
