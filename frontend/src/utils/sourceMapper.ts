/**
 * 污染来源判断映射工具
 * 将 SourceAgent 返回的英文来源判断映射为中文
 */

export const SOURCE_JUDGEMENT_MAP: Record<string, string> = {
  'Traffic source with local accumulation': '交通源影响并伴随局地积聚',
  'Traffic source': '交通源影响',
  'Local accumulation': '局地污染积聚',
  'Regional transport': '区域传输影响',
  'Mixed sources': '多源混合影响'
}

/**
 * 获取来源判断的中文描述
 * @param judgement 英文来源判断
 * @returns 中文描述，如果没有映射则返回原值
 */
export function getSourceJudgementLabel(judgement: string): string {
  // 精确匹配
  if (SOURCE_JUDGEMENT_MAP[judgement]) {
    return SOURCE_JUDGEMENT_MAP[judgement]
  }
  
  // 部分匹配（fallback）
  const lowerJudgement = judgement.toLowerCase()
  if (lowerJudgement.includes('traffic') && lowerJudgement.includes('local')) {
    return '交通源影响并伴随局地积聚'
  }
  if (lowerJudgement.includes('traffic')) {
    return '交通源影响'
  }
  if (lowerJudgement.includes('local')) {
    return '局地污染积聚'
  }
  if (lowerJudgement.includes('regional')) {
    return '区域传输影响'
  }
  
  // 如果都不匹配，返回原值
  return judgement
}

/**
 * 格式化来源评分显示
 * 将 [0, 1] 范围的评分转换为 [0, 100] 显示
 * @param score 原始评分 (0-1)
 * @returns 格式化后的显示文本，例如 "79.1"
 */
export function formatSourceScore(score: number): string {
  return (score * 100).toFixed(1)
}
