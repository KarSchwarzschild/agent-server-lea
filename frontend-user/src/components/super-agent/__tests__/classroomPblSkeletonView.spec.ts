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

describe('classroom pbl skeleton view', () => {
  it('renders deterministic PBL skeleton content as a project view', () => {
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
              kind: 'pbl_project_skeleton',
              title: '校园节水项目',
              description: '设计一个校园节水方案',
              target_skills: ['问题分析', '方案比较'],
              roles: [{ id: 'role_1', name: '资料研究员', responsibility: '收集并核验资料' }],
              milestones: [
                { id: 'm1', title: '调研现状', description: '观察用水场景', assessment_criterion: '列出至少三个用水场景' },
                { id: 'm2', title: '提出方案', description: '形成节水方案', assessment_criterion: '比较两个方案' },
              ],
              rubric: [{ criterion: '问题理解', level: '达标', description: '目标清楚，约束完整' }],
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

    expect(wrapper.text()).toContain('设计一个校园节水方案')
    expect(wrapper.text()).toContain('资料研究员')
    expect(wrapper.text()).toContain('调研现状')
    expect(wrapper.text()).toContain('列出至少三个用水场景')
    expect(wrapper.text()).not.toContain('完整 PBL 工作区尚未启用')
  })
})
