import { describe, expect, it } from 'vitest'
import { getSourceJudgementLabel, getSourceOverview } from '../sourceMapper'

describe('source overview judgement', () => {
  const scores = { traffic_source: 0.8, local_accumulation: 0.6, regional_transport: 0.7 }

  it.each([
    ['Insufficient evidence', '证据不足'],
    ['Mixed / uncertain sources', '来源不确定'],
    ['Mixed sources', '来源不确定']
  ])('shows a completed but inconclusive judgement for %s', (judgement, label) => {
    expect(getSourceOverview(judgement, scores)).toMatchObject({ label, score: undefined, determined: false })
    expect(getSourceJudgementLabel(judgement)).not.toBe(judgement)
  })

  it.each([
    ['Traffic source dominant', '交通源', 0.8],
    ['Local accumulation dominant', '局地积聚', 0.6],
    ['Regional transport dominant', '区域传输', 0.7],
    ['Traffic source with local accumulation', '交通源', 0.8],
    ['局地污染积聚', '局地积聚', 0.6]
  ] as const)('pairs %s with its own source score', (judgement, label, score) => {
    expect(getSourceOverview(judgement, scores)).toMatchObject({ label, score, determined: true })
  })

  it('does not invent a score or a judgement when data is missing', () => {
    expect(getSourceOverview('')).toMatchObject({ label: '暂无结论', score: undefined, determined: false })
    expect(getSourceOverview('Regional transport dominant')).toMatchObject({ label: '区域传输', score: undefined })
    expect(getSourceOverview('Traffic source', { traffic_source: 0 }).score).toBe(0)
    expect(getSourceOverview('Traffic source', { traffic_source: NaN }).score).toBeUndefined()
  })
})
