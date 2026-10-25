import type { DynamicChart } from '@/types/aiChat'

/** Historical templates without a successful model request are not new AI charts. */
export function isVerifiedChatChart(chart: DynamicChart): boolean {
  const selection = chart.provenance.selection
  const request = selection?.model_request
  return !chart.metrics.includes('weather.propagation.distance_km')
    && selection?.relevance_validated === true
    && request?.status === 'success'
    && Boolean(request.request_id && request.model && request.provider_host)
}
