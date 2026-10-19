/**
 * 气象 Agent 类型定义
 * 基于 docs/frontend_contract_meteorology.md 和 examples/day9_coordinator_output.json
 */

/**
 * 单小时气象数据
 */
export interface MeteorologyHourlyItem {
  time: string
  wind_speed: number | null
  wind_direction: number | null
  humidity: number | null
  precipitation: number | null
  temperature: number | null
  cloud_cover: number | null
}

/**
 * 逐日天气代码
 */
export interface WeatherCodeDaily {
  date: string
  weather_code: number
}

/**
 * 气象结果（窗口聚合 + 逐小时时序）
 */
export interface MeteorologyResult {
  task_id: string
  station_id: string
  station_name: string
  start_time: string
  end_time: string
  lat: number
  lon: number
  
  // 核心风险字段
  weather_risk: number
  risk_type: 'low' | 'medium' | 'high' | 'very_high'
  dispersion_condition: '优' | '良' | '一般' | '差' | '极差'
  
  // 算法中间量
  static_wind_score: number
  wind_shift_score: number
  humidity_risk_score: number
  rain_removal_score: number
  insufficient_rain_removal_score: number
  
  // 窗口聚合气象字段
  mean_wind_speed: number | null
  dominant_wind_direction: number | null
  dominant_wind_direction_label: string | null
  upwind_direction: number | null
  downwind_direction: number | null
  mean_humidity: number | null
  total_precipitation: number | null
  mean_temperature: number | null
  mean_cloud_cover: number | null
  
  // 天气代码
  weather_code_daily: number | null
  dominant_weather_code_daily: number | null
  weather_code_daily_series: WeatherCodeDaily[]
  
  // 逐小时时序数据（正式冻结接口）
  hourly_count: number
  hourly_series: MeteorologyHourlyItem[]

  // 短时预测字段（筛查级）——可解释预测模块的数据源
  wind_direction_forecast: WindDirectionForecast | null
  pollution_path_prediction: MeteorologyPathPrediction | null

  // 兼容期保留字段（已废弃）
  latest_wind_speed: number | null
  latest_wind_direction: number | null
  upwind: string
  downwind: string
}

/** 单个预测小时的矢量（气象学"来向"约定） */
export interface WindForecastHour {
  lead_hour: number
  /** 风的来向（气象学约定，0-360°） */
  wind_from_direction: number
  /** 风的去向 = 来向 + 180°，平流推进实际使用的方位 */
  wind_to_direction: number
  wind_speed_ms: number
  /** 该小时的方位不确定度（度），随预报时效递增 */
  direction_uncertainty_deg: number
}

/**
 * 逐小时风矢量预测（复现加权圆统计外推）。
 *
 * method = recency_weighted_circular_persistence：
 * 以近期观测加权求圆平均风向，再用前后半窗差异外推有限转向速率。
 * 不含黑箱模型，每一项中间量都可直接展示作为"预测依据"。
 */
export interface WindDirectionForecast {
  /** ok = 可用；calm = 弱风；unavailable = 无法形成稳定输送方向 */
  status: 'ok' | 'calm' | 'unavailable'
  method: string
  reason?: string
  /** 实际参与计算的有效小时数 */
  lookback_hours_used?: number
  /** 风向集中度（圆统计合成矢长度），越接近 1 越稳定 */
  resultant_length?: number
  /** 外推的转向速率（度/小时），正值为顺时针 */
  turn_rate_deg_per_hour?: number
  confidence?: number
  forecast_hours: WindForecastHour[]
}

/**
 * 气象数据质量
 */
export interface MeteorologyDataQuality {
  missing_rate: number
  fallback_used: boolean
  source: 'Open-Meteo Archive API' | 'Local Fallback CSV' | 'None'
  note: string
}

/**
 * 气象 Agent 完整响应
 */
export interface MeteorologyResponse {
  agent_name: 'MeteorologyAgent'
  status: 'success' | 'warning' | 'failed'
  result: MeteorologyResult
  evidence: string[]
  confidence: number
  data_quality: MeteorologyDataQuality
  created_at: string
}

// ── HTTP 层 ───────────────────────────────────────────────────────────────

/** POST /api/meteorology/analyze 的请求体（单任务模式） */
export interface MeteorologyAnalyzeTask {
  task_id?: string
  station_id?: string
  station_name?: string
  lat: number
  lon: number
  start_time: string
  end_time: string
}

/** POST /api/meteorology/analyze 的响应包装 */
export interface MeteorologyAnalyzeResponse {
  mode: 'single' | 'batch'
  count: number
  /** 单任务模式下就是 MeteorologyResponse；校验失败时 status=error 且没有 result */
  result: Partial<MeteorologyResponse> & { status: string; message?: string }
}

/** 平流路径预测的一步（前向拉格朗日推进） */
export interface MeteorologyPathStep {
  lead_hour: number
  lat: number
  lon: number
  distance_from_origin_km: number
  distance_this_hour_km: number
  corridor_half_width_km: number
  /** 一阶衰减代理值（非浓度预测），1 小时后半衰期 6h 应约为 0.89 */
  remaining_fraction: number
  wind_to_direction: number
}

/**
 * 污染路径预测：气象 Agent 顺带给出的下风向轨迹。
 *
 * method = forward_lagrangian_advection，属于筛查级拉格朗日轨迹，
 * 不是浓度模型。path/corridor 为 WGS84 GeoJSON，绘制前需转 GCJ-02。
 */
export interface MeteorologyPathPrediction {
  status: 'ok' | 'unavailable' | string
  method: string
  reason?: string
  /** 建模假设，界面上必须原样展示，避免被误读为浓度预测 */
  assumptions?: string[]
  /** 路径线：LineString，坐标 [lon, lat][]（WGS84） */
  path_geojson?: { type: 'LineString'; coordinates: number[][] } | null
  /** 不确定走廊：Polygon，坐标 [[[lon, lat], ...]]（WGS84） */
  uncertainty_corridor_geojson?: { type: 'Polygon'; coordinates: number[][][] } | null
  steps: MeteorologyPathStep[]
}

/**
 * 页面统一使用的天气展示口径。
 *
 * 气象接口返回的字段名与出行结果内嵌的 `weather_summary` 基本一致，
 * 但两边都可能缺项，所以统一映射成这个形状，页面只认它，
 * 不用到处判断"这份数是接口来的还是出行结果带的"。
 */
export interface WeatherView {
  mean_humidity: number | null
  mean_temperature: number | null
  mean_wind_speed: number | null
  total_precipitation: number | null
  dominant_wind_direction: number | null
  dominant_wind_direction_label: string | null
  downwind_direction: number | null
  /** api = 直接调气象接口；embedded = 出行结果里内嵌的那份；none = 都没有 */
  source: 'api' | 'embedded' | 'none'
}

/** 页面持有的气象快照：接口返回的完整结果 + 来源与状态 */
export interface MeteorologySnapshot {
  source: 'api' | 'embedded' | 'unavailable'
  result: MeteorologyResult | null
  evidence: string[]
  confidence: number
  dataQuality: MeteorologyDataQuality | null
  /** 这次请求用的参数，界面上要能看出"查的是哪个点、哪个时段" */
  request: MeteorologyAnalyzeTask | null
  error: string | null
  loading: boolean
  fetchedAt: string
}

/** 监测站点登记项（后端 GET /api/meteorology/stations） */
export interface MeteorologyStation {
  station_id: string
  station_name: string
  station_name_zh: string
  station_type: string
  lat: number | null
  lon: number | null
  address: string
  atc_station_ids: string[]
  nearby_atc_station_ids?: string[]
}
