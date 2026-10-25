import { describe, expect, it } from 'vitest'
import { recommendationDisplayFor } from '../recommendationDisplay'
import type { AIChatResponse } from '@/types/aiChat'

function legacyResponse(type: 'public_travel_advice' | 'health_protection' | 'governance_advice'): AIChatResponse {
  return {
    contract_version: '1.0',
    answer: '旧后端返回的大段监测数据和无关城市治理知识不应继续显示。',
    business_action: 'fallback',
    visualization_action: 'none',
    route: null,
    context: { station_name: 'Mong Kok Roadside' },
    agent_results: {},
    user_goal: type,
    recommendation_result: {
      recommendation_type: type,
      summary: '基于已验证历史结果的简短解释。',
      recommendation: 'legacy-code',
      evidence: [],
      evidence_summary: { status: 'partial', available: [], missing_required: [], missing_optional: [], reason: 'test' },
      limitations: [],
      confidence: 'medium'
    }
  }
}

describe('recommendationDisplayFor', () => {
  it.each(['public_travel_advice', 'health_protection', 'governance_advice'] as const)(
    'does not invent advice when model output is missing for %s', (type) => {
      expect(recommendationDisplayFor(legacyResponse(type))).toBeNull()
    }
  )

  it('does not insert canned governance advice for legacy responses', () => {
    expect(recommendationDisplayFor(legacyResponse('governance_advice'))).toBeNull()
  })

  it.each([true, false])('preserves model governance output or failure state (success=%s)', (success) => {
    const response = legacyResponse('governance_advice')
    response.recommendation_display = {
      version: '1.0', recommendation_type: 'governance_advice', title: '城市治理建议',
      direct_advice: success ? [{ title: '模型生成的措施', detail: '依据本次分析提出的具体行动。' }] : [],
      explanation: success ? '模型生成的原因。' : '城市治理建议暂未生成，请稍后重试。',
      evidence: [], scope: {}, markdown_fallback: ''
    }
    expect(recommendationDisplayFor(response)).toBe(response.recommendation_display)
  })

  it.each(['public_travel_advice', 'health_protection'] as const)(
    'preserves an empty model failure state for %s', (type) => {
      const response = legacyResponse(type)
      response.recommendation_display = {
        version: '1.0', recommendation_type: type, title: '建议', direct_advice: [],
        explanation: '建议暂未生成，请稍后重试。', evidence: [], scope: {}, markdown_fallback: ''
      }
      expect(recommendationDisplayFor(response)).toBe(response.recommendation_display)
    }
  )

  it('does not force recommendation layout on a normal explanation', () => {
    const response = legacyResponse('health_protection')
    response.user_goal = 'explanation'
    expect(recommendationDisplayFor(response)).toBeNull()
  })
})
