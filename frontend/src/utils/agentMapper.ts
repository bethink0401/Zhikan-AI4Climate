/**
 * Agent 名称映射工具
 */

export const AGENT_NAME_MAP: Record<string, string> = {
  'AirQualityAgent': '空气质量分析',
  'MeteorologyAgent': '气象分析',
  'TrafficAgent': '交通分析',
  'SpatialAgent': '空间分析',
  'SourceAgent': '污染溯源',
  'DecisionAgent': '决策建议',
  'ReportAgent': '报告生成'
}

/**
 * 获取 Agent 中文名称
 */
export function getAgentLabel(name: string): string {
  return AGENT_NAME_MAP[name] || name
}
