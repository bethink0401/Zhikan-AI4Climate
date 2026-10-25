/**
 * 空间 Agent 类型定义
 * 基于 examples/day9_coordinator_output.json 真实输出
 */

/**
 * GeoJSON LineString 几何
 */
export interface LineStringGeometry {
  type: 'LineString'
  coordinates: number[][]
}

/**
 * GeoJSON Polygon 几何
 */
export interface PolygonGeometry {
  type: 'Polygon'
  coordinates: number[][][]
}

/**
 * 上风向道路
 */
export interface UpwindRoad {
  road_id: string
  street_code: string
  road_name: string
  road_name_zh: string
  distance_to_station_km: number
  bearing_from_station: number
  wind_angle_difference: number
  upwind_alignment: number
  match_score: number
  geometry: LineStringGeometry
}

/**
 * 敏感 POI
 */
export interface SensitivePOI {
  poi_id: string
  poi_type: 'rail_transit' | 'community_service' | 'healthcare' | 'school' | 'community_centre'
  name: string
  name_zh: string
  distance_to_station_km: number
  bearing_from_station: number
  lon: number
  lat: number
}

/**
 * 站点信息
 */
export interface SpatialStation {
  station_id: string
  station_name: string
  aliases: string[]
  lat: number
  lon: number
  station_type: 'roadside' | 'general'
  address: string
}

/**
 * 敏感 POI 按类型统计
 */
export interface SensitivePOIByType {
  rail_transit: number
  community_service: number
  healthcare: number
  school: number
  community_centre: number
}

/**
 * 空间结果
 */
export interface SpatialResult {
  directional_exposure?: {
    status: 'ok' | 'unavailable'
    geometry?: PolygonGeometry | null
    direction: number | null
    sensitive_poi_count: number
    sensitive_poi_by_type: Partial<SensitivePOIByType>
    sensitive_pois: SensitivePOI[]
    note: string
  }
  propagation_exposure?: {
    status: 'ok' | 'unavailable'
    geometry_source: string
    sensitive_poi_count: number
    sensitive_poi_by_type: Partial<SensitivePOIByType>
    sensitive_pois: SensitivePOI[]
    note: string
  }
  upwind_roads: UpwindRoad[]
  nearby_road_count: number
  nearby_road_segment_count: number
  affected_area: string
  affected_area_geojson: PolygonGeometry
  sensitive_poi_count: number
  sensitive_poi_by_type: SensitivePOIByType
  sensitive_pois: SensitivePOI[]
  wind_direction_interpreted_as: string
  station: SpatialStation
  analysis_radius_km: number
  wind_direction: number
  wind_speed: number
  spatial_confidence: 'low' | 'medium' | 'high'
}

/**
 * 空间元数据
 */
export interface SpatialMeta {
  wind_direction_used: number
  wind_direction_source: string
}

/**
 * 空间数据质量
 */
export interface SpatialDataQuality {
  missing_rate: number
  note: string
}

/**
 * 空间 Agent 完整响应
 */
export interface SpatialResponse {
  task_id: string
  agent_name: 'SpatialAgent'
  status: 'success' | 'warning' | 'failed'
  result: SpatialResult
  evidence: string[]
  confidence: number
  data_quality: SpatialDataQuality
  warnings: string[]
  created_at: string
  meta: SpatialMeta
}
