import type { AirQualityResponse } from './airQuality'
import type { MeteorologyResponse } from './meteorology'
import type { TrafficResponse } from './traffic'
import type { SpatialResponse } from './spatial'
import type { SourceResponse } from './source'
import type { DecisionResponse } from './decision'
import type { ReportResponse } from './report'

/**
 * 分析请求参数
 */
export interface AnalysisRequest {
  task_id?: string
  station_name: string
  station_id: string
  start_time: string
  end_time: string
  lat: number
  lon: number
}

/**
 * Coordinator 完整响应
 * 基于 examples/day9_coordinator_output.json 真实输出
 */
export interface CoordinatorResponse {
  task: AnalysisRequest
  air_quality: AirQualityResponse
  meteorology: MeteorologyResponse
  traffic: TrafficResponse
  spatial: SpatialResponse
  source: SourceResponse
  decision: DecisionResponse
  report: ReportResponse
  pipeline_status: {
    air_quality_ready: boolean
    meteo_ready: boolean
    traffic_ready: boolean
    spatial_ready: boolean
    source_ready: boolean
    decision_ready: boolean
    report_ready: boolean
    coordinator_min_ok: boolean
  }
}

/** /api/analyze/meta：各数据源的真实统计数（加载浮层"正在用什么"的依据） */
export interface AnalysisMeta {
  epd_hourly_rows?: number
  epd_station_count?: number
  aqhi_coverage?: number
  atc_mix_rows?: number
  atc_station_count?: number
  road_segments?: number
  sensitive_poi?: number
  spatial_radius_km?: number
  travel_robustness?: {
    samples: number
    wind_speed_rel_sigma: number
    wind_direction_sigma_deg: number
    exchange_rel_sigma: number
  }
  exposure_grid_cell_km?: number
  exposure_min_index?: number
}
