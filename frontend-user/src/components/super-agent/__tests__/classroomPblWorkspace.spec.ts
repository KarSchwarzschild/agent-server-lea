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

describe('classroom pbl workspace', () => {
  it('supports role selection, task progress, resources, and submission draft', async () => {
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
              kind: 'pbl_workspace',
              title: '校园节水项目',
              description: '设计一个校园节水方案',
              roles: [{ id: 'researcher', name: '调研员', responsibility: '收集现状资料' }],
              resources: [{ id: 'water_bill', title: '用水账单', summary: '近三个月用水量' }],
              tasks: [{ id: 'task_1', title: '找出浪费点', status: 'todo' }],
              submission_prompts: ['提交节水方案摘要'],
              milestones: [],
              rubric: [],
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

    expect(wrapper.text()).toContain('用水账单')
    await wrapper.get('[data-test="pbl-role-researcher"]').trigger('click')
    await wrapper.get('[data-test="pbl-task-task_1"]').trigger('click')
    await wrapper.get('[data-test="pbl-submission-0"]').setValue('先统计高峰时段，再提出节水提醒。')

    expect(wrapper.text()).toContain('当前角色：调研员')
    expect(wrapper.text()).toContain('进行中')
    expect((wrapper.get('[data-test="pbl-submission-0"]').element as HTMLTextAreaElement).value).toContain('节水提醒')
  })
})
