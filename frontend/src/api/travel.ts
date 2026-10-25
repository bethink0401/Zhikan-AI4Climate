import request from './request'
import type {
  TravelCaseOutput,
  TravelPlanRequest,
  TravelProfileSummary,
  TravelResponse,
  TravelPoint
} from '@/types/travel'

export interface TravelPlace extends TravelPoint {
  id: string
  kind: 'case' | 'rail' | 'monitor' | 'poi'
  detail: string
}

/** Search server-owned named places; returned coordinates are WGS84. */
export function searchTravelPlaces(query: string): Promise<{ places: TravelPlace[] }> {
  return request({
    url: '/api/travel/places',
    method: 'GET',
    params: { q: query },
    timeout: 10000
  }) as Promise<{ places: TravelPlace[] }>
}

/**
 * 出行建议与路线推荐 API
 * 后端入口：api/travel_api.py（前缀 /api/travel）
 */

/**
 * 出行规划的超时时间。
 *
 * 默认实例是 30 s，但一次完整规划要重建暴露场十余次（出发时段曲线 + 蒙特卡洛
 * 稳健性），实测热请求约 5 s、冷启动首次约 34 s。两者都可能超过 30 s，
 * 一旦超时 axios 会抛错，页面就会静默降级回 mock 数据——看起来正常，
 * 实际上一次后端都没调到。所以这里单独放宽到 120 s。
 */
const PLAN_TIMEOUT_MS = 120000

/** 案例文件有 1.6 MB，首次读取 + 传输给它留够时间 */
const CASE_TIMEOUT_MS = 60000

/** 运行一次出行规划 */
export function planTravel(params: TravelPlanRequest): Promise<TravelResponse> {
  return request({
    url: '/api/travel/plan',
    method: 'POST',
    data: params,
    timeout: PLAN_TIMEOUT_MS
  }) as Promise<TravelResponse>
}

/**
 * 拉取案例演示的完整输出（后端直接读 examples/travel_case_output.json）。
 *
 * 页面不再打包一份裁剪过的副本：那份副本的暴露场网格被隔点抽稀、
 * 候选路线被删到每种出行方式只剩一条，和 Agent 的真实输出对不上。
 */
export function fetchTravelCase(): Promise<TravelCaseOutput> {
  return request({
    url: '/api/travel/case',
    method: 'GET',
    timeout: CASE_TIMEOUT_MS
  }) as Promise<TravelCaseOutput>
}

/** 获取可用的出行习惯画像列表 */
export function fetchTravelProfiles(): Promise<{
  count: number
  profiles: TravelProfileSummary[]
}> {
  return request({
    url: '/api/travel/profiles',
    method: 'GET',
    timeout: PLAN_TIMEOUT_MS
  }) as Promise<{ count: number; profiles: TravelProfileSummary[] }>
}
