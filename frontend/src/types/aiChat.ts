import type { SpatialResult } from './spatial'
import type { MeteorologyResult } from './meteorology'
import type { KnowledgeContext, FusedEvidence } from './knowledge'

export type BusinessAction = 'run' | 'need_params' | 'fallback' | 'recommend' | 'knowledge'
export type VisualizationAction = 'none' | 'show' | 'modify' | 'update' | 'reset' | 'switch_view'

// Phase 5: Recommendation types
export interface RecommendationResult {
  recommendation_type: 'public_travel_advice' | 'health_protection' | 'governance_advice'
  summary: string
  recommendation: string
  evidence: Array<Record<string, unknown>>
  evidence_summary: {
    status: string
    available: string[]
    missing_required: string[]
    missing_optional: string[]
    reason: string
  }
  limitations: string[]
  confidence: 'low' | 'medium' | 'high'
  future_risk_assessment?: {
    risk_level: 'low' | 'medium' | 'high' | 'limited'
    risk_score: number
    prediction_window: string
    affected_area: string
    risk_factors: string[]
    evidence: Array<Record<string, unknown>>
    recommended_actions: string[]
    limitations: string[]
  } | null
}

export interface RecommendationDisplayEvidence {
  category: 'observed' | 'estimated' | 'screening' | 'limitation'
  label: string
  value: string
  unit?: string
  source: string
  time_basis: string
  note?: string
}

export interface RecommendationDisplay {
  version: string
  recommendation_type: RecommendationResult['recommendation_type']
  title: string
  direct_advice: Array<{ title: string; detail: string }>
  explanation: string
  evidence: RecommendationDisplayEvidence[]
  scope: AIRequestContext
  professional_knowledge?: {
    summary: string
    sources: Array<Record<string, unknown>>
  } | null
  markdown_fallback: string
}

export interface AIRequestContext {
  task_id?: string
  station_id?: string
  station_name?: string
  start_time?: string
  end_time?: string
  lat?: number
  lon?: number
}

export interface VisualizationSpec {
  version: string
  view_type: 'chart' | 'map'
  title: string
  data_source: 'air_quality' | 'meteorology' | 'traffic' | 'source' | 'spatial'
  metrics: string[]
  dimension: 'time' | 'time_slot' | 'source_type' | 'vehicle_type' | 'pollutant' | 'space'
  filters?: Record<string, unknown>
  grouping?: {
    field?: string
    mode?: string
    y_axis_by_metric?: Record<string, 'left' | 'right'>
    y_axis_index_by_metric?: Record<string, number>
    series_type_by_metric?: Record<string, 'line' | 'bar'>
  } | null
  unit?: string | null
  warnings?: string[]
  chart_type?: string | null
  map_type?: string | null
  layers?: Array<{
    id: string
    metric: string
    layer_type: 'fill' | 'line' | 'circle' | 'symbol'
    visible?: boolean
  }>
}

export interface ViewCommand {
  action: VisualizationAction
  view_id?: string | null
  spec?: VisualizationSpec | null
  reason?: string | null
}

export interface VisualizationMetricValue {
  time?: string
  category?: string
  // Numeric chart values are the common case; spatial metrics may carry a
  // validated GeoJSON object and are intentionally not rendered as a scalar.
  value?: number | Record<string, unknown> | null
  avg?: number | null
  max?: number | null
  min?: number | null
  unit?: string
  level?: string | null
}

export interface VisualizationMetricData {
  metric: string
  unit: string | null
  field_path: string
  source_agent: string
  semantic_type: string
  values: VisualizationMetricValue[]
}

export interface VisualizationDataSnapshot {
  provider: 'canonical_agent_results'
  timezone: 'Asia/Hong_Kong'
  task?: AIRequestContext
  metrics: Record<string, VisualizationMetricData>
}

export interface ActiveVisualization {
  status: 'planned' | 'unchanged' | 'reset'
  valid: boolean
  revision: number
  view_id?: string | null
  spec?: VisualizationSpec | null
  view_command?: ViewCommand | null
  data?: VisualizationDataSnapshot | null
  warnings?: string[]
  reason?: string
}

export interface ChatPropagationMapData {
  title: string
  station_name: string
  start_time: string | null
  end_time: string | null
  spatial: SpatialResult
  meteorology: MeteorologyResult
}

export interface AIChatResponse {
  contract_version: string
  answer: string
  business_action: BusinessAction
  visualization_action: VisualizationAction
  route: string | null
  context: AIRequestContext & { agent_results?: Record<string, unknown> }
  agent_results: Record<string, unknown>
  missing_fields?: string[]
  reason?: string
  visualization?: ActiveVisualization | null
  content_blocks?: Array<
    | { type: 'text'; text: string }
    | { type: 'chart'; id: string; chart: DynamicChart }
    | { type: 'map'; id: string; map: ChatRouteMapData }
    | { type: 'propagation_map'; id: string; map: ChatPropagationMapData }
  >
  user_goal?: string
  audience?: string
  goal_reason?: string
  recommendation_result?: RecommendationResult | null
  recommendation_display?: RecommendationDisplay | null
  recommendation_execution?: Record<string, unknown> | null
  knowledge?: KnowledgeContext | null
  fused_evidence?: FusedEvidence | null
  error?: { code: string; message: string; retryable?: boolean; details?: Record<string, unknown> } | null
}

export interface ChatRouteMapData {
  title: string
  routes: import('@/types/travel').TravelRouteOption[]
  origin: import('@/types/travel').TravelPoint
  destination: import('@/types/travel').TravelPoint
  selected_route_id: string
}

export interface DynamicChart {
  id: string
  title: string
  type: 'line' | 'area' | 'bar' | 'scatter' | 'pie' | 'donut'
  metrics: string[]
  dimension: string
  categories: string[]
  series: Array<{ name: string; unit: string | null; data: Array<number | null | number[]> }>
  warnings: string[]
  transform: { time_filter: 'all' | 'morning' | 'afternoon'; bucket_hours: number; start_hour: number; end_hour: number }
  provenance: {
    provider: string
    timezone: string
    task: AIRequestContext
    metrics: Array<{ key: string; unit: string | null; field_path: string; source_agent: string }>
    selection?: { method: 'model'; reason?: string; focus?: string; relevance_validated?: boolean;
      model_request?: { status: 'success'; model: string; provider_host: string; request_id: string; received_at: string } }
  }
}

export interface AIChatMessage {
  restored?: boolean
  id: number
  role: 'user' | 'assistant'
  content: string
  response?: AIChatResponse
  requestId?: number
}

export interface AIChatErrorInfo {
  code: string
  message: string
  status?: number
  retryable?: boolean
}
