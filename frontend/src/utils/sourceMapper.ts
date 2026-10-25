/**
 * 污染来源判断映射工具
 * 将 SourceAgent 返回的英文来源判断映射为中文
 */

import type { SourceAttribution } from '@/types/source'

export const SOURCE_JUDGEMENT_MAP: Record<string, string> = {
  'Insufficient evidence': '证据不足，暂无法判定主要来源',
  'Mixed / uncertain sources': '多源混合或来源尚不确定',
  'Traffic source dominant': '交通源主导',
  'Local accumulation dominant': '局地积聚主导',
  'Regional transport dominant': '区域传输主导',
  'Traffic source with local accumulation': '交通源影响并伴随局地积聚',
  'Traffic source': '交通源影响',
  'Local accumulation': '局地污染积聚',
  'Regional transport': '区域传输影响',
  'Mixed sources': '多源混合影响'
}

/** 概览卡沿用后端结论；未确定来源时不把某一类评分标成首要因子。 */
export function getSourceOverview(judgement: string, scores?: Partial<SourceAttribution>) {
  const description = getSourceJudgementLabel(judgement)
  const text = `${judgement} ${description}`.toLowerCase()
  if (text.includes('insufficient') || text.includes('证据不足')) {
    return { label: '证据不足', description, score: undefined, determined: false }
  }
  if (text.includes('mixed') || text.includes('uncertain') || text.includes('混合') || text.includes('不确定')) {
    return { label: '来源不确定', description, score: undefined, determined: false }
  }
  const categories = [
    { words: ['traffic', '交通源'], label: '交通源', key: 'traffic_source' },
    { words: ['local', '局地'], label: '局地积聚', key: 'local_accumulation' },
    { words: ['regional', '区域'], label: '区域传输', key: 'regional_transport' }
  ] as const
  for (const category of categories) {
    if (category.words.some(word => text.includes(word))) {
      const score = scores?.[category.key]
      return { label: category.label, description,
        score: typeof score === 'number' && Number.isFinite(score) ? score : undefined,
        determined: true }
    }
  }
  return { label: '暂无结论', description: description || '来源分析未返回结论', score: undefined, determined: false }
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
