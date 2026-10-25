/**
 * 出行建议与路线推荐类型定义
 * 基于 examples/travel_case_output.json 真实输出
 */

/** 出行方式 */
export type TravelMode = 'walk' | 'drive' | 'transit'

/** 出行条件等级 */
export type TravelConditionLevel = 'good' | 'fair' | 'poor' | 'severe'

/** 起终点 */
export interface TravelPoint {
  name: string
  lat: number
  lon: number
}

/** 用户出行习惯画像 */
export interface TravelProfile {
  profile_id: string
  profile_name: string
  description: string
  preferred_modes: TravelMode[]
  /** 方式常数（分钟）：正数表示该方式本身让人更不情愿，负数表示更偏好 */
  mode_constant_minutes: Record<string, number>
  /** 吸入交换率：该画像愿意为减少 1 EU 吸入当量多花的分钟数 */
  pollution_exchange_minutes: number
  /** 天气把户外等效时间放大的最大比例 */
  weather_time_multiplier: number
  max_walk_km: number
  max_trip_minutes: number
  time_flexibility_minutes: number
  walk_speed_factor: number
  sensitivities: Record<string, number>
}

/** 画像简要信息（GET /api/travel/profiles） */
export type TravelProfileSummary = Pick<
  TravelProfile,
  'profile_id' | 'profile_name' | 'description' | 'preferred_modes' | 'max_walk_km' | 'max_trip_minutes'
>

/** 气象摘要 */
export interface TravelWeatherSummary {
  available: boolean
  weather_risk: number | null
  risk_type: string | null
  dispersion_condition: string | null
  mean_wind_speed: number | null
  mean_humidity: number | null
  mean_temperature: number | null
  total_precipitation: number | null
  peak_hourly_precipitation: number | null
  dominant_wind_direction: number | null
  dominant_wind_direction_label: string | null
  downwind_direction: number | null
}

/** 逐小时气象（用于出发时段建议） */
export interface TravelHourlyItem {
  time: string
  wind_speed: number | null
  wind_direction: number | null
  humidity: number | null
  precipitation: number | null
  temperature: number | null
}

/** 影响因子（0-1） */
export interface TravelSeverity {
  rain: number
  heat: number
  cold?: number
  humidity: number
  wind: number
  air_quality: number
  /** /plan 后端现场算的真实输入：3 个路边站 EPD 异常分的平均（案例数据里没有） */
  pollution_risk_input?: number
}

/** 出行条件总分 */
export interface TravelCondition {
  score: number
  level: TravelConditionLevel
  level_label: string
  impact: number
  dominant_factors: string[]
}

/** 路线暴露度 */
export interface TravelRouteExposure {
  total_km: number
  /** 吸入剂量代理 = Σ(路段里程 × 暴露指数)，单位 km·指数 */
  dose_index: number
  /** 平均暴露指数，便于不同长度的路线互相比较 */
  mean_index: number
  /** 途经路段中的最高暴露指数 */
  peak_index: number
  /** 处于高暴露区（指数 ≥ 阈值）的里程 */
  high_exposure_km: number
  monitored_corridor_km: number
  named_road_km?: number
  monitored_corridor_ratio: number
  named_road_ratio?: number
  /** 公共交通专用：候车与进出站取上下车站中暴露较高的一侧 */
  station_access_index?: number
}

/** 分段类型：步行 / 驾车 / 候车与进出站 / 轨道区间 */
export type TravelSegmentKind = 'walk' | 'drive' | 'transit_wait' | 'transit_rail'

/** 顶部指标条的一格 */
export interface TravelMetricItem {
  key: string
  label: string
  value: string
  unit?: string
  tone?: 'blue' | 'amber' | 'red' | 'green'
}

/** 出行习惯画像的展示用摘要 */
export interface TravelProfileOption {
  id: string
  name: string
  description: string
  preferredModes: TravelMode[]
  maxWalkKm: number | null
  timeFlexibilityMinutes: number | null
  /** 1 EU 吸入当量在该画像眼里折算成多少分钟 */
  pollutionExchangeMinutes: number | null
}

/** 单个分段的吸入贡献 */
export interface TravelSegmentIntake {
  label: string
  minutes: number
  intake_index: number
}

/** 吸入当量明细。单位 EU = 以步行呼吸量计的污染分钟数 */
export interface TravelIntakeDetail {
  minutes: number
  outdoor_equivalent_minutes: number
  intake_index: number
  by_kind: Partial<Record<TravelSegmentKind, TravelSegmentIntake>>
}

/** 效用构成。全部以分钟为单位，可直接相加比较 */
export interface TravelUtilityDetail {
  outdoor_equivalent_minutes: number
  weather_added_minutes: number
  pollution_added_minutes: number
  mode_constant_minutes: number
  overrun_minutes: number
  walk_overrun_minutes: number
}

/** 各段耗时明细 */
export interface TravelTimeline {
  connector_minutes?: number
  total_minutes?: number
  wait_minutes?: number
  main?: Record<string, number>
  access?: Record<string, number>
  egress?: Record<string, number>
  rail?: Record<string, number>
}

/** 污染暴露场的采样点，用于地图着色 */
export interface TravelFieldSample {
  lon: number
  lat: number
  index: number
}

/** 污染源 */
export interface TravelFieldSource {
  name: string
  lon: number
  lat: number
  strength: number
}

/** 污染暴露场 */
export interface TravelPollutionField {
  source_count: number
  station_sources: TravelFieldSource[]
  corridor_source_count: number
  corridor_source_strength: number
  wind_from_direction: number | null
  transport_bearing: number | null
  wind_speed_ms: number | null
  calm: boolean
  /** 各向同性积聚分支的权重：1=完全积聚，0=完全定向烟羽 */
  calm_weight?: number
  /** 是否处于两个分支的过渡区 */
  mixed_regime?: boolean
  /** 是否缺少风速 */
  wind_missing?: boolean
  peak: { lon: number; lat: number; index: number } | null
  index_meaning: string
  assumptions: string[]
  /** 采样点数组（仅 Agent 输出带，案例 mock 亦保留） */
  grid?: TravelFieldSample[]
}

/** 避污建议 */
export interface TravelPollutionAdvice {
  /** 本次是否真的建立了暴露场。为 false 时其余字段无意义 */
  available?: boolean
  headline: string
  hotspot: {
    peak?: { lon: number; lat: number; index: number } | null
    calm?: boolean
    distance_from_origin_km?: number
    bearing_from_origin?: number
    direction_label?: string
  }
  avoidance: {
    dose_reduction_pct: number
    high_exposure_km_avoided: number
    extra_distance_km: number
    extra_minutes: number
    avoided_high_exposure_fully: boolean
  }
  cleanest_route_id: string | null
  cleanest_route_is_recommended: boolean
  notes: string[]
}

/** 公共交通明细 */
export interface TravelTransitDetail {
  boarding_station: string
  alighting_station: string
  stations: string[]
  stop_count: number
  rail_distance_km: number
  access_distance_km: number
  egress_distance_km: number
  access_junctions?: number
  egress_junctions?: number
  access_mean_index?: number
  egress_mean_index?: number
  /** 上下车站中暴露较高的一侧的暴露指数 */
  wait_index?: number
  platform_access_minutes?: number
  wait_minutes?: number
  access_road_names?: string[]
  egress_road_names?: string[]
}

/** 路线方案 */
export interface TravelRouteOption {
  route_id: string
  mode: TravelMode
  mode_label: string
  alternative_index: number
  rank: number
  recommended: boolean
  distance_km: number
  /** 路网内里程（不含起终点吸附接驳段） */
  road_distance_km?: number
  /** 起终点到路网的直线接驳里程 */
  connector_km?: number
  duration_minutes: number
  score: number
  /** 等效时间（分钟）：排序依据，越小越好 */
  utility_minutes: number
  utility_detail: TravelUtilityDetail
  /** 吸入当量 (EU) */
  intake_index: number
  intake_detail: TravelIntakeDetail
  /** 该画像下 1 EU 折算成多少分钟 */
  exchange_minutes_per_eu?: number
  timeline?: TravelTimeline
  road_names: string[]
  geometry: number[][]
  /** 沿途路口数（相邻 30 m 内视为同一路口），用于估算路口等待 */
  junctions: number
  exposure: TravelRouteExposure
  transit_detail: TravelTransitDetail | null
  reason: string
}

/** 各出行方式的最优方案摘要 */
export interface TravelModeSummary {
  route_id: string
  mode_label: string
  distance_km: number
  duration_minutes: number
  score: number
  utility_minutes: number
  intake_index: number
}

/** 推荐结论 */
export interface TravelRecommendation {
  route_id: string
  mode: TravelMode
  mode_label: string
  headline: string
  utility_minutes: number
  intake_index: number
  reasons: string[]
  alternative_by_mode: Record<string, TravelModeSummary>
}

/** 出发时段 × 吸入当量曲线上的一个点 */
export interface TravelDeparturePoint {
  time: string
  wind_speed_ms: number
  wind_direction: number
  precipitation: number | null
  recommended_route_id: string
  recommended_mode: string
  intake_index: number
  utility_minutes: number
  duration_minutes: number
}

/** 出发时段 × 吸入当量曲线 */
export interface TravelDepartureDoseCurve {
  available: boolean
  unit: string
  note: string
  baseline_window: string
  best_window: string
  worst_window: string
  intake_range: { min: number; max: number; reduction_pct: number }
  recommendation_changes_with_time: boolean
  points: TravelDeparturePoint[]
}

/** 结论稳健性（蒙特卡洛扰动） */
export interface TravelUncertainty {
  available: boolean
  samples: number
  perturbations: {
    wind_speed_rel_sigma: number
    wind_direction_sigma_deg: number
    exchange_rel_sigma: number
  }
  recommended_route_id: string
  recommended_share: number
  level: 'high' | 'medium' | 'low'
  level_label: string
  top_pick_distribution: { route_id: string; mode_label: string; share: number }[]
  intake_intervals: Record<string, { p05: number; p50: number; p95: number }>
  note: string
}

/** 出发时段建议 */
export interface TravelDepartureAdvice {
  time_flexibility_minutes: number
  recommended_window: string | null
  avoid_window: string | null
  note: string
  hourly_burden?: { time: string; burden: number }[]
}

/** TravelAgent 的业务结果 */
export interface TravelResult {
  origin: TravelPoint
  destination: TravelPoint
  straight_line_km: number
  start_time: string | null
  end_time: string | null
  travel_profile: TravelProfile
  weather_summary: TravelWeatherSummary
  weather_severity: TravelSeverity
  personalized_severity: TravelSeverity
  travel_condition: TravelCondition
  route_options: TravelRouteOption[]
  recommendation: TravelRecommendation
  departure_advice: TravelDepartureAdvice
  departure_dose_curve: TravelDepartureDoseCurve | null
  uncertainty: TravelUncertainty | null
  gear_reminders: string[]
  pollution_field: TravelPollutionField | null
  pollution_advice: TravelPollutionAdvice | null
  assumptions: string[]
}

/** TravelAgent 完整响应（遵循统一 Agent 契约） */
export interface TravelResponse {
  evidence_id?: string
  task_id: string
  agent_name: 'TravelAgent'
  status: 'success' | 'warning' | 'error'
  result: TravelResult
  evidence: string[]
  confidence: number
  data_quality: {
    missing_rate: number
    confidence_basis?: string
    meteorology_source?: string | null
    pollution_risk_used?: number | null
    note: string
  }
  warnings: string[]
  created_at: string
}

/** 出行规划请求 */
export interface TravelPlanRequest {
  task_id?: string
  origin: TravelPoint
  destination: TravelPoint
  start_time: string
  end_time: string
  travel_profile?: string
  pollution_source?: {
    station_id?: string
    station_name?: string
    lat?: number
    lon?: number
    radius_km?: number
  }
  air_quality_risk?: number
}

/** 案例演示：一个场景下某个画像的完整运行结果 */
export interface TravelCaseRun {
  task_id: string
  agent_name: string
  status: string
  result: TravelResult
  evidence: string[]
  confidence: number
  data_quality: TravelResponse['data_quality']
  warnings: string[]
  created_at: string
}

/** 案例演示：一个行程（起终点由坐标决定）在某气象场景下、各画像的运行结果 */
export interface TravelCaseItem {
  case_id: string
  label: string
  direction_note: string
  origin: TravelPoint
  destination: TravelPoint
  runs: Record<string, TravelCaseRun>
}

/** 案例演示：一个对照场景，含多个行程 */
export interface TravelCaseScenario {
  scenario_id: string
  label: string
  note: string
  start_time?: string
  end_time?: string
  air_quality_risk: number
  traffic_pressure_score: number
  meteorology: {
    status: string
    result: Partial<TravelWeatherSummary> & { hourly_series?: TravelHourlyItem[] }
    data_quality: { source?: string; note?: string }
  }
  /**
   * 暴露场的采样点。**是场景级数据**：同一场景下各画像的场完全相同，
   * `run.result.pollution_field` 里只留元数据（源、峰值、风场），grid 为空。
   */
  pollution_grid?: TravelFieldSample[] | null
  cases: TravelCaseItem[]
}

/** 案例演示完整输出（前端 mock 数据结构） */
export interface TravelCaseOutput {
  case_id: string
  title: string
  generated_at: string
  pollution_source: Record<string, unknown>
  cases: {
    case_id: string
    label: string
    direction_note: string
    origin: TravelPoint
    destination: TravelPoint
  }[]
  scenarios: TravelCaseScenario[]
}
