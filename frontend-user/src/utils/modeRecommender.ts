/**
 * 讨论模式推荐器
 * 基于话题关键词分析推荐最合适的讨论模式
 */

const KEYWORDS: Record<string, string[]> = {
  debate: ['对比', '优劣', '利弊', '争议', '分歧', '冲突', 'vs', '还是', '哪个更', '好坏', '正反'],
  seminar: ['如何', '怎样', '方法', '策略', '实践', '经验', '建议', '措施', '路径', '方案', '实施'],
  socratic: ['为什么', '本质', '深层', '根源', '反思', '哲学', '意义', '价值', '原因', '探讨', '思考'],
}

export class ModeRecommender {
  recommend(topic: string): string {
    const scores: Record<string, number> = {}
    for (const [mode, keywords] of Object.entries(KEYWORDS)) {
      scores[mode] = keywords.filter(kw => topic.includes(kw)).length
    }
    const maxScore = Math.max(...Object.values(scores))
    if (maxScore === 0) return 'debate'
    return Object.entries(scores).reduce((a, b) => (a[1] >= b[1] ? a : b))[0]
  }
}
