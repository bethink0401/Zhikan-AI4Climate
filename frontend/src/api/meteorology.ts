import request from './request'
import type {
  MeteorologyAnalyzeResponse,
  MeteorologyAnalyzeTask,
  MeteorologyHourlyItem,
  MeteorologyStation
} from '@/types/meteorology'

/**
 * 气象 Agent API
 * 后端入口：api/app.py（POST /api/meteorology/analyze、GET /api/meteorology/hourly）
 */

/**
 * 超时时间。
 * 同一个进程里第一次走气象 ETL 要 24.6 s（pandas 时区处理等一次性初始化），
 * 之后 2.7 s。api/app.py 的 warm_up() 会在启动时把这一路预热掉，
 * 但预热失败时服务仍会启动，所以这里仍要给足时间。
 */
const METEOROLOGY_TIMEOUT_MS = 90000

/** 气象风险分析（单任务） */
export function analyzeMeteorology(
  task: MeteorologyAnalyzeTask
): Promise<MeteorologyAnalyzeResponse> {
  return request({
    url: '/api/meteorology/analyze',
    method: 'POST',
    data: task,
    timeout: METEOROLOGY_TIMEOUT_MS
  }) as Promise<MeteorologyAnalyzeResponse>
}

/** 监测站点清单（后端读项目里登记的 stations.csv + station_mapping.json） */
export function fetchMeteorologyStations(): Promise<{
  count: number
  source: string
  stations: MeteorologyStation[]
}> {
  return request({
    url: '/api/meteorology/stations',
    method: 'GET',
    timeout: METEOROLOGY_TIMEOUT_MS
  }) as Promise<{ count: number; source: string; stations: MeteorologyStation[] }>
}

/** 标准小时级气象序列 */export function fetchMeteorologyHourly(params: {
  lat: number
  lon: number
  start_time: string
  end_time: string
  station_id?: string
}): Promise<{
  station_id: string
  lat: number
  lon: number
  start_time: string
  end_time: string
  hourly_count: number
  fallback_used: boolean
  records: MeteorologyHourlyItem[]
}> {
  return request({
    url: '/api/meteorology/hourly',
    method: 'GET',
    params,
    timeout: METEOROLOGY_TIMEOUT_MS
  }) as Promise<{
    station_id: string
    lat: number
    lon: number
    start_time: string
    end_time: string
    hourly_count: number
    fallback_used: boolean
    records: MeteorologyHourlyItem[]
  }>
}
