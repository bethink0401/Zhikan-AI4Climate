/**
 * 风险等级映射工具
 * 统一风险等级的中英文转换和颜色映射
 */

export type RiskLevel = 'low' | 'medium' | 'high' | 'very_high'

/**
 * 风险等级中文映射
 */
export const RISK_LEVEL_MAP: Record<RiskLevel, string> = {
  low: '低',
  medium: '中',
  high: '高',
  very_high: '极高'
}

/**
 * 风险等级颜色映射（CSS 变量名）
 */
export const RISK_COLOR_MAP: Record<RiskLevel, string> = {
  low: 'var(--risk-low)',
  medium: 'var(--risk-medium)',
  high: 'var(--risk-high)',
  very_high: 'var(--risk-very-high)'
}

/**
 * 获取风险等级中文名称
 */
export function getRiskLabel(level: RiskLevel): string {
  return RISK_LEVEL_MAP[level] || '未知'
}

/**
 * 获取风险等级颜色
 */
export function getRiskColor(level: RiskLevel): string {
  return RISK_COLOR_MAP[level] || 'var(--text-secondary)'
}
