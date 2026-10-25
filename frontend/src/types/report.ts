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

/**
 * 报告抽屉的单节视图（只保留标题与正文，后端 section_id 不进视图）
 */
export interface ReportSectionView {
  title: string
  content: string
  subsections?: ReportSectionView[]
}

/**
 * 报告抽屉的视图模型。
 * 由顶栏「智能分析报告」按钮从 CoordinatorResponse.report 映射而来。
 */
export interface ReportDrawerData {
  /** 一句话总结 */
  summary?: string
  /** 决策摘要 */
  decision_summary?: string
  /** 七节正文 */
  sections: ReportSectionView[]
  /** 置信度 0-1 */
  confidence?: number
  /** 数据质量：high / medium / low（按 missing_rate 分档） */
  data_quality?: 'high' | 'medium' | 'low'
  /** 纯文本报告（复制 / 导出用） */
  report_text?: string
}
