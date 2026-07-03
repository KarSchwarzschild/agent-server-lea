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

describe('classroom whiteboard view', () => {
  it('renders structured whiteboard elements', () => {
    const wrapper = mount(ClassroomPlayer, {
      props: {
        data: {
          session_id: 's1',
          topic: '光合作用白板',
          status: 'ready',
          current_scene_index: 0,
          total_scenes: 1,
          config: {},
          scenes: [{
            id: 'scene_0',
            title: '光合作用白板',
            scene_type: 'whiteboard',
            content: {
              kind: 'whiteboard',
              title: '光合作用白板',
              prompt: '用白板推演光合作用',
              viewport: { width: 1000, height: 562 },
              elements: [
                { id: 'e1', type: 'text', text: '光合作用', x: 80, y: 60, width: 180, height: 64 },
                { id: 'e2', type: 'sticky', text: '叶绿体', x: 120, y: 160, width: 160, height: 64 },
                { id: 'e3', type: 'arrow', text: '光能', x: 320, y: 180, width: 160, height: 48 },
                { id: 'e4', type: 'formula', latex: '6CO_2 + 6H_2O', x: 320, y: 260, width: 220, height: 64 },
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

    expect(wrapper.text()).toContain('用白板推演光合作用')
    expect(wrapper.get('[data-test="whiteboard-element-e1"]').text()).toContain('光合作用')
    expect(wrapper.get('[data-test="whiteboard-element-e2"]').text()).toContain('叶绿体')
    expect(wrapper.get('[data-test="whiteboard-element-e3"]').text()).toContain('光能')
    expect(wrapper.get('[data-test="whiteboard-element-e4"]').text()).toContain('6CO_2 + 6H_2O')
  })
})
