export interface SourceAttribution {
  traffic_source: number
  local_accumulation: number
  regional_transport: number
  dust_source: number
  photochemical_source: number
}

export interface ComponentScores {
  no2_anomaly: number
  traffic_pressure: number
  upwind_match: number
  static_wind: number
  roadside_boost: number
  anomaly_score: number
}

export interface MeteorologySummary {
  wind_direction: number
  mean_wind_speed: number
  rain_removal_score: number
  total_precipitation: number
}

export interface SpatialSummary {
  spatial_confidence: string
  upwind_roads_count: number
}

export interface SourceResult {
  source_attribution: SourceAttribution
  main_judgement: string
  risk_level: string
  component_scores: ComponentScores
  meteorology_summary: MeteorologySummary
  spatial_summary: SpatialSummary
}

export interface SourceMeta {
  task_id: string
  station_name: string
}

export interface SourceDataQuality {
  missing_rate: number
  note: string
}

export interface SourceResponse {
  agent_name: string
  status: string
  result: SourceResult
  evidence: string[]
  confidence: number
  data_quality: SourceDataQuality
  created_at: string
  meta: SourceMeta
}