/**
 * 决策 Agent 类型定义
 * 基于 examples/day9_coordinator_output.json 真实输出
 * 
 * 重要说明：Decision Agent 只有道路名称，不包含 geometry
 * 地图几何数据必须从 Spatial Agent 获取
 */

/**
 * 决策结果
 */
export interface DecisionResult {
  decision_level: 'low' | 'medium' | 'high' | 'very_high'
  suggested_area: string
  suggested_time: string
  suggested_roads: string[]
  vehicle_focus: string[]
  actions: string[]
  review_indicators: string[]
}

/**
 * 决策数据质量
 */
export interface DecisionDataQuality {
  missing_rate: number
  note: string
}

/**
 * 决策 Agent 完整响应
 */
export interface DecisionResponse {
  agent_name: 'DecisionAgent'
  status: 'success' | 'warning' | 'failed'
  result: DecisionResult
  confidence: number
  evidence: string[]
  data_quality: DecisionDataQuality
  warnings: string[]
  created_at: string
}
