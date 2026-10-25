import { describe, expect, it } from 'vitest'
import { isVerifiedChatChart } from '../chatChartVisibility'
import type { DynamicChart } from '@/types/aiChat'

function chart(selection?: DynamicChart['provenance']['selection']): DynamicChart {
  return { metrics: ['source.screening_scores'], provenance: { selection } } as DynamicChart
}

describe('verified chat charts', () => {
  it('hides old saved charts and unverified model claims', () => {
    expect(isVerifiedChatChart(chart())).toBe(false)
    expect(isVerifiedChatChart(chart({ method: 'model', reason: 'old claim' }))).toBe(false)
    expect(isVerifiedChatChart(chart({ method: 'model', relevance_validated: true }))).toBe(false)
  })

  it('shows a validated chart with a successful model request, except removed metrics', () => {
    const valid = chart({ method: 'model', relevance_validated: true, focus: 'pollution_sources',
      model_request: { status: 'success', model: 'configured-model', provider_host: 'example.test', request_id: 'request-1', received_at: '2026-10-20T10:00:00Z' } })
    expect(isVerifiedChatChart(valid)).toBe(true)
    expect(isVerifiedChatChart({ ...valid, metrics: ['weather.propagation.distance_km'] })).toBe(false)
  })
})
