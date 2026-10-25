import request from './request'
import type { AnalysisMeta, AnalysisRequest, CoordinatorResponse } from '@/types/analysis'

/**
 * 分析整链路的超时时间。
 *
 * 一次 `/api/analyze` 要顺序跑完 7 个 Agent：气象要联网取数（同进程首次约 24 s，
 * `api/app.py` 的 warm_up 会预热）、空间要读 5.7 MB 路网建图、
 * 溯源与决策要在其之上再算一遍。热请求实测十几秒，冷启动会超过 30 s，
 * 而默认实例只有 30 s —— 一超时 axios 就抛错，页面会静默回落到快照数据。
 */
const ANALYSIS_TIMEOUT_MS = 180000

/**
 * 运行完整分析（调用后端 Coordinator 链路）
 * @param params 分析请求参数
 * @returns 七个 Agent 的完整响应
 */
export function runAnalysis(params: AnalysisRequest): Promise<CoordinatorResponse> {
  return request({
    url: '/api/analyze',
    method: 'POST',
    data: params,
    timeout: ANALYSIS_TIMEOUT_MS
  }) as Promise<CoordinatorResponse>
}

/** 获取各数据源的真实统计数（行数 / 站点数 / 网格粒度），供加载浮层展示 */
export function fetchAnalysisMeta(): Promise<AnalysisMeta> {
  return request({
    url: '/api/analyze/meta',
    method: 'GET',
    timeout: 15000
  }) as Promise<AnalysisMeta>
}
