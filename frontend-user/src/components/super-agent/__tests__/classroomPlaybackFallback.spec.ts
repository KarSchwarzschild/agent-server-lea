import { describe, expect, it } from 'vitest'
import { resolveScenePlaybackActions } from '../classroomPlaybackActions'

describe('resolveScenePlaybackActions', () => {
  it('prefers content actions', () => {
    const actions = [{ type: 'speech', text: 'content action' }]

    expect(resolveScenePlaybackActions({
      content: { actions },
      actions: [{ type: 'speech', text: 'scene action' }],
    } as any)).toEqual(actions)
  })

  it('falls back to legacy audio and speaker notes', () => {
    expect(resolveScenePlaybackActions({
      content: {},
      actions: [],
      speaker_notes: 'legacy notes',
      audio_file_id: 'file_1',
      audio_duration_ms: 1200,
    } as any)).toEqual([
      {
        type: 'speech',
        text: 'legacy notes',
        audioFileId: 'file_1',
        audioDurationMs: 1200,
        fallbackReason: 'legacy_audio',
      },
    ])
  })
})
