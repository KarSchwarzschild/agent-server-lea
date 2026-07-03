/**
 * useAudioRecorder — 语音录制 & 转录 composable
 *
 * 状态机：idle → requesting-permission → recording → processing → idle
 * 额外状态：permission-denied, error, retry-ready
 */
import { ref, computed, type Ref } from 'vue'

export type AudioRecorderState =
  | 'idle'
  | 'requesting-permission'
  | 'recording'
  | 'processing'
  | 'permission-denied'
  | 'error'
  | 'retry-ready'

export function useAudioRecorder() {
  const state: Ref<AudioRecorderState> = ref('idle')
  const duration = ref(0) // 录制时长(秒)
  const errorMessage = ref('')
  const maxDuration = 60 // 最大录制时长

  let mediaRecorder: MediaRecorder | null = null
  let stream: MediaStream | null = null
  let chunks: Blob[] = []
  let durationTimer: ReturnType<typeof setInterval> | null = null
  let lastBlob: Blob | null = null // 网络错误时保留，用于重试
  let lastSessionId: string = '' // 重试时使用

  // 检测浏览器支持
  const isSupported = computed(
    () => !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== 'undefined',
  )

  // 格式协商：iOS Safari 只支持 audio/mp4
  function getPreferredMimeType(): string {
    if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) return 'audio/webm;codecs=opus'
    if (MediaRecorder.isTypeSupported('audio/webm')) return 'audio/webm'
    if (MediaRecorder.isTypeSupported('audio/mp4')) return 'audio/mp4'
    return '' // 浏览器默认
  }

  function clearTimer() {
    if (durationTimer !== null) {
      clearInterval(durationTimer)
      durationTimer = null
    }
  }

  function stopStreamTracks() {
    if (stream) {
      stream.getTracks().forEach((t) => t.stop())
      stream = null
    }
  }

  async function startRecording() {
    if (!isSupported.value) {
      state.value = 'error'
      errorMessage.value = '当前浏览器不支持语音录制'
      return
    }

    state.value = 'requesting-permission'
    errorMessage.value = ''
    chunks = []
    duration.value = 0

    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch (err: any) {
      if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
        state.value = 'permission-denied'
        errorMessage.value = '麦克风权限被拒绝，请在浏览器设置中允许使用麦克风'
      } else {
        state.value = 'error'
        errorMessage.value = `无法访问麦克风: ${err.message || err}`
      }
      return
    }

    const mimeType = getPreferredMimeType()
    const options: MediaRecorderOptions = mimeType ? { mimeType } : {}

    try {
      mediaRecorder = new MediaRecorder(stream, options)
    } catch {
      stopStreamTracks()
      state.value = 'error'
      errorMessage.value = '无法创建录音器，请换用 Chrome 或 Safari 重试'
      return
    }

    mediaRecorder.ondataavailable = (e: BlobEvent) => {
      if (e.data.size > 0) chunks.push(e.data)
    }

    mediaRecorder.onstop = () => {
      clearTimer()
    }

    mediaRecorder.onerror = () => {
      clearTimer()
      stopStreamTracks()
      state.value = 'error'
      errorMessage.value = '录音过程中发生错误'
    }

    mediaRecorder.start(250) // 每 250ms 收集一个 chunk
    state.value = 'recording'

    // 计时器
    durationTimer = setInterval(() => {
      duration.value++
      // 50s 倒计时警告
      if (duration.value === maxDuration - 10) {
        console.warn(`[AudioRecorder] 录音还剩 10 秒，将在 ${maxDuration}s 时自动停止`)
      }
      // 60s 自动停止
      if (duration.value >= maxDuration) {
        console.warn('[AudioRecorder] 达到最大录制时长，自动停止')
        stopRecording()
      }
    }, 1000)
  }

  function stopRecording(): Blob | null {
    if (!mediaRecorder || mediaRecorder.state === 'inactive') return null

    // 先同步停止，ondataavailable 会在 stop 之前触发 (spec)
    mediaRecorder.stop()
    clearTimer()
    stopStreamTracks()

    if (chunks.length === 0) {
      state.value = 'idle'
      return null
    }

    const mimeType = mediaRecorder.mimeType || 'audio/webm'
    const blob = new Blob(chunks, { type: mimeType })
    chunks = []
    mediaRecorder = null

    return blob
  }

  async function transcribe(blob: Blob, sessionId: string): Promise<string> {
    state.value = 'processing'
    errorMessage.value = ''
    lastBlob = blob
    lastSessionId = sessionId

    const formData = new FormData()
    // 根据 mimeType 推导文件扩展名
    const ext = blob.type.includes('mp4') ? 'mp4' : 'webm'
    formData.append('file', blob, `recording.${ext}`)

    try {
      const resp = await fetch(`/api/user/classroom/${sessionId}/transcribe`, {
        method: 'POST',
        body: formData,
      })

      if (!resp.ok) {
        const detail = await resp.text().catch(() => '')
        throw new Error(detail || `HTTP ${resp.status}`)
      }

      const data = await resp.json()
      const text = data.text || data.transcript || ''
      state.value = 'idle'
      lastBlob = null
      return text
    } catch (err: any) {
      state.value = 'retry-ready'
      errorMessage.value = `语音识别失败: ${err.message || '网络错误'}`
      throw err
    }
  }

  /** 用上一次保留的录音 blob 重试转录 */
  async function retry(): Promise<string> {
    if (!lastBlob) {
      state.value = 'error'
      errorMessage.value = '没有可重试的录音'
      throw new Error('No blob to retry')
    }
    return transcribe(lastBlob, lastSessionId)
  }

  function reset() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
      mediaRecorder.stop()
    }
    clearTimer()
    stopStreamTracks()
    state.value = 'idle'
    duration.value = 0
    errorMessage.value = ''
    chunks = []
    lastBlob = null
    mediaRecorder = null
  }

  /** 组件卸载时调用 */
  function cleanup() {
    reset()
  }

  return {
    state,
    duration,
    errorMessage,
    isSupported,
    maxDuration,
    startRecording,
    stopRecording,
    transcribe,
    retry,
    reset,
    cleanup,
  }
}
