/**
 * 报告 Agent 类型定义
 * 基于 examples/day9_coordinator_output.json 真实输出
 */

/**
 * 报告章节
 */
export interface ReportSection {
  section_id: string
  title: string
  content: string
}

/**
 * 报告结果
 */
export interface ReportResult {
  report_title: string
  report_text: string
  summary: string
  decision_summary: string
  sections: ReportSection[]
}

/**
 * 报告数据质量
 */
export interface ReportDataQuality {
  missing_rate: number
  note: string
}

/**
 * 报告 Agent 完整响应
 */
export interface ReportResponse {
  agent_name: 'ReportAgent'
  status: 'success' | 'warning' | 'failed'
  result: ReportResult
  confidence: number
  evidence: string[]
  data_quality: ReportDataQuality
  warnings: string[]
  created_at: string
}
