/**
 * 交通 Agent 类型定义
 * 基于 docs/TrafficAgent_接口说明.md 和 examples/day9_coordinator_output.json
 */

/**
 * 车型占比（百分比形式）
 */
export interface ProportionsPct {
  'Franchised Bus DD': number
  'Private Car': number
  'Taxi': number
  'Goods Vehicle Light': number
  'Motor Cycle': number
  'Goods Vehicle M&H': number
  'Non Franchised Bus': number
  'Private LB': number
  'Franchised Bus SD': number
  'PLB': number
}

/**
 * 交通结果
 */
export interface TrafficResult {
  traffic_pressure: 'low' | 'medium' | 'high'
  traffic_pressure_score: number
  traffic_pressure_index: number
  traffic_pressure_level: 'low' | 'medium' | 'high'
  dominant_vehicle_types: string[]
  heavy_vehicle_signal: 'low' | 'medium' | 'high'
  bus_ratio: number
  goods_vehicle_ratio: number
  taxi_ratio: number
  private_car_ratio: number
  vehicle_focus: string[]
  proportions_pct: ProportionsPct
  road_links: string[]
}

/**
 * 交通元数据
 */
export interface TrafficMeta {
  task_id: string
  station_name: string
  year_used: number
  atc_station_ids: string[]
  time_slots: string[]
  peak_time_slot: string
  row_count: number
}

/**
 * 交通数据质量
 */
export interface TrafficDataQuality {
  missing_rate: number
  note: string
}

/**
 * 交通 Agent 完整响应
 */
export interface TrafficResponse {
  agent_name: 'TrafficAgent'
  status: 'success' | 'warning' | 'failed'
  result: TrafficResult
  evidence: string[]
  confidence: number
  data_quality: TrafficDataQuality
  created_at: string
  meta: TrafficMeta
}
