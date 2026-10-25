/**
 * 空气质量 Agent 类型定义
 * 基于 docs/AirQualityAgent_接口说明.md 和 examples/day9_coordinator_output.json
 */

/**
 * 单一污染物详情
 */
export interface PollutantDetail {
  avg: number
  max: number
  min: number
}

/**
 * 交通源信号详情
 */
export interface TrafficSignalDetails {
  traffic_pollutant_signal: 'strong' | 'moderate' | 'weak' | 'unknown'
  details: string[]
  morning_peak: {
    no2: number | null
    nox: number | null
  }
  evening_peak: {
    no2: number | null
    nox: number | null
  }
  off_peak: {
    no2: number | null
    nox: number | null
  }
}

/**
 * 空气质量结果
 */
export interface AirQualityResult {
  pollution_status: 'abnormal' | 'moderate' | 'normal' | 'unknown'
  main_pollutants: string[]
  aqhi_level: 'low' | 'moderate' | 'high' | 'very_high' | 'unknown'
  avg_aqhi: number | null
  traffic_pollutant_signal: 'strong' | 'moderate' | 'weak' | 'unknown'
  traffic_pollutant_signal_score: number
  anomaly_score: number
  roadside_station: boolean
  pollutant_details: {
    no2: PollutantDetail
    nox: PollutantDetail
    pm25: PollutantDetail
    pm10: PollutantDetail
    o3: PollutantDetail
    so2: PollutantDetail
    co: PollutantDetail
  }
}

/**
 * 空气质量元数据
 */
export interface AirQualityMeta {
  task_id: string
  station_name: string
  start_time: string
  end_time: string
  row_count: number
  traffic_signal_details: TrafficSignalDetails
}

/**
 * 空气质量数据质量
 */
export interface AirQualityDataQuality {
  missing_rate: number
  note: string
}

/**
 * 空气质量 Agent 完整响应
 */
export interface AirQualityResponse {
  agent_name: 'AirQualityAgent'
  status: 'success' | 'warning' | 'failed'
  result: AirQualityResult
  evidence: string[]
  confidence: number
  data_quality: AirQualityDataQuality
  created_at: string
  meta: AirQualityMeta
}
