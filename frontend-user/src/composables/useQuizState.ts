/**
 * useQuizState — Quiz 4 态状态管理
 * 对齐 OpenMAIC quiz-view.tsx：
 *   not_started → answering → grading → reviewing
 */
import { ref, watch } from 'vue'

export type QuizPhase = 'not_started' | 'answering' | 'grading' | 'reviewing'

export interface QuestionResult {
  questionId: string
  isCorrect: boolean
  earnedScore: number
  maxScore: number
  feedback?: string
}

export interface GradeResult {
  score: number
  maxScore: number
  details: QuestionResult[]
}

export function useQuizState(sceneId: string) {
  const phase = ref<QuizPhase>('not_started')
  const answers = ref<Record<string, string | string[]>>({})
  const gradeResult = ref<GradeResult | null>(null)

  // 从 sessionStorage 恢复答案缓存
  const cacheKey = `quizDraft:${sceneId}`
  try {
    const cached = sessionStorage.getItem(cacheKey)
    if (cached) {
      const parsed = JSON.parse(cached)
      if (parsed && typeof parsed === 'object' && Object.keys(parsed).length > 0) {
        answers.value = parsed
        phase.value = 'answering'
      }
    }
  } catch { /* ignore */ }

  // 答案变更时缓存
  watch(answers, (val) => {
    try { sessionStorage.setItem(cacheKey, JSON.stringify(val)) } catch { /* ignore */ }
  }, { deep: true })

  function startQuiz() {
    phase.value = 'answering'
  }

  function setAnswer(questionId: string, answer: string | string[]) {
    answers.value = { ...answers.value, [questionId]: answer }
  }

  async function submitAnswers(questions: any[]) {
    phase.value = 'grading'

    try {
      // 客观题本地评分
      const details: QuestionResult[] = []
      let totalScore = 0
      let totalMax = 0

      for (const q of questions) {
        const qId = q.id || `q_${questions.indexOf(q)}`
        const userAnswer = answers.value[qId]
        const maxScore = q.score || 10

        if (q.type === 'choice' || q.type === 'judge') {
          // 客观题：直接比对
          const correct = q.answer
          const isCorrect = Array.isArray(userAnswer)
            ? JSON.stringify([...userAnswer].sort()) === JSON.stringify([...(Array.isArray(correct) ? correct : [correct])].sort())
            : String(userAnswer).toUpperCase() === String(correct).toUpperCase()

          details.push({
            questionId: qId,
            isCorrect,
            earnedScore: isCorrect ? maxScore : 0,
            maxScore,
          })
          totalScore += isCorrect ? maxScore : 0
        } else {
          // 主观题：尝试 API 评分，失败降级 50%
          try {
            const resp = await fetch(`/api/user/classroom/quiz-grade`, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                Authorization: `Bearer ${localStorage.getItem('token') || ''}`,
              },
              body: JSON.stringify({
                question: q.question,
                answer: userAnswer,
                reference: q.answer || q.explanation || '',
              }),
            })
            if (resp.ok) {
              const data = await resp.json()
              const earned = data.score ?? maxScore * 0.5
              details.push({
                questionId: qId,
                isCorrect: earned >= maxScore * 0.8,
                earnedScore: earned,
                maxScore,
                feedback: data.feedback,
              })
              totalScore += earned
            } else {
              throw new Error('API failed')
            }
          } catch {
            // 降级：给 50%
            details.push({
              questionId: qId,
              isCorrect: false,
              earnedScore: maxScore * 0.5,
              maxScore,
              feedback: '评分服务暂时不可用，已给予基础分',
            })
            totalScore += maxScore * 0.5
          }
        }
        totalMax += maxScore
      }

      gradeResult.value = { score: totalScore, maxScore: totalMax, details }
      phase.value = 'reviewing'
    } catch {
      // 全部降级
      gradeResult.value = { score: 50, maxScore: 100, details: [] }
      phase.value = 'reviewing'
    }
  }

  function reset() {
    phase.value = 'not_started'
    answers.value = {}
    gradeResult.value = null
    try { sessionStorage.removeItem(cacheKey) } catch { /* ignore */ }
  }

  return { phase, answers, gradeResult, startQuiz, setAnswer, submitAnswers, reset }
}
