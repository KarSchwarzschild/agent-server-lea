import type { PlaybackAction } from '../../composables/usePlaybackEngine'

export interface PlaybackSceneLike {
  content?: Record<string, any>
  actions?: PlaybackAction[]
  speaker_notes?: string
  audio_file_id?: string
  audio_duration_ms?: number
}

export function resolveScenePlaybackActions(scene: PlaybackSceneLike | null): PlaybackAction[] {
  if (!scene) return []
  const contentActions = (scene.content as any)?.actions
  if (contentActions?.length) return contentActions
  if (scene.actions?.length) return scene.actions
  if (scene.speaker_notes || scene.audio_file_id) {
    return [{
      type: 'speech',
      text: scene.speaker_notes || '',
      audioFileId: scene.audio_file_id || undefined,
      audioDurationMs: scene.audio_duration_ms || undefined,
      fallbackReason: 'legacy_audio',
    }]
  }
  return []
}
