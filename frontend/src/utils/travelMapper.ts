/**
 * 出行建议展示映射工具
 * 统一出行方式、出行条件等级的中文与颜色映射
 */

import type { TravelConditionLevel, TravelMode, TravelRouteOption } from '@/types/travel'

/** 出行方式 → 中文 */
export const MODE_LABEL_MAP: Record<TravelMode, string> = {
  walk: '步行',
  drive: '驾车',
  transit: '公共交通'
}

/** 出行方式 → 图标 */
export const MODE_ICON_MAP: Record<TravelMode, string> = {
  walk: '🚶',
  drive: '🚗',
  transit: '🚇'
}

/**
 * 出行方式 → 地图线色
 * 与页面语义色对齐：轨道=绿、干道=琥珀、步行=红（步行段即户外暴露段）
 */
export const MODE_COLOR_MAP: Record<TravelMode, string> = {
  walk: '#e0453f',
  drive: '#e8a020',
  transit: '#12a06b'
}

/** 出行条件等级 → 颜色（复用全局风险色变量） */
export const CONDITION_COLOR_MAP: Record<TravelConditionLevel, string> = {
  good: 'var(--risk-low)',
  fair: 'var(--risk-medium)',
  poor: 'var(--risk-high)',
  severe: 'var(--risk-very-high)'
}

/** 影响因子 → 中文 */
export const FACTOR_LABEL_MAP: Record<string, string> = {
  rain: '降雨',
  heat: '高温',
  cold: '低温',
  humidity: '湿度',
  wind: '风力',
  air_quality: '空气质量'
}

/** 扣分项 → 中文 */
export const DEDUCTION_LABEL_MAP: Record<string, string> = {
  duration: '耗时',
  distance: '里程',
  weather_exposure: '天气暴露',
  pollution_exposure: '污染暴露',
  complexity: '路口复杂度',
  over_max_trip_minutes: '单程时间超限'
}

/** 获取方式中文名 */
export function getModeLabel(mode: TravelMode): string {
  return MODE_LABEL_MAP[mode] ?? mode
}

/** 获取方式颜色 */
export function getModeColor(mode: TravelMode): string {
  return MODE_COLOR_MAP[mode] ?? '#909399'
}

/** 获取出行条件颜色 */
export function getConditionColor(level: TravelConditionLevel): string {
  return CONDITION_COLOR_MAP[level] ?? 'var(--text-secondary)'
}

/**
 * 路线评分 → 颜色档位
 * 评分是 0-100 的适配度，越高越推荐
 */
export function getScoreTagType(score: number): 'success' | 'warning' | 'danger' | 'info' {
  if (score >= 90) return 'success'
  if (score >= 75) return 'info'
  if (score >= 55) return 'warning'
  return 'danger'
}

/** 把 [[lon,lat],...] 转成 GeoJSON LineString Feature */
export function toLineFeature(geometry: number[][], properties: Record<string, unknown>) {
  return {
    type: 'Feature' as const,
    properties,
    geometry: {
      type: 'LineString' as const,
      coordinates: geometry
    }
  }
}

/** 逐小时时间字符串 → HH:mm */
export function hourLabel(time: string): string {
  const part = time.includes(' ') ? time.split(' ')[1] : time
  return part.slice(0, 5)
}

/** 方位角（正北为 0，顺时针）→ 十六方位缩写 */
export function bearingLabel(degree: number | null | undefined): string {
  if (degree === null || degree === undefined || Number.isNaN(degree)) return '—'
  const names = [
    'N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
    'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'
  ]
  const index = Math.round((((degree % 360) + 360) % 360) / 22.5) % 16
  return names[index]
}

/** 0-1 的影响因子 → 「偏高 / 中度 / 舒适 / 微弱」四档描述 */
export function severityLabel(value: number): string {
  if (value >= 0.7) return '偏高'
  if (value >= 0.3) return '中度'
  if (value >= 0.1) return '舒适'
  return '微弱'
}

/** 影响因子数值 → 语义色，与全局风险色保持一致的红黄绿 */
export function severityTone(value: number): 'red' | 'amber' | 'green' {
  if (value >= 0.7) return 'red'
  if (value >= 0.3) return 'amber'
  return 'green'
}

/** 出行方式 → 「首选路线」的短描述，用于结论行右侧 */
export function preferredRouteNote(mode: TravelMode): string {
  if (mode === 'transit') return '首选地下路网'
  if (mode === 'drive') return '首选走廊快线'
  return '首选步行路径'
}

/**
 * 某方案相对「吸入最高的方案」的剂量降幅（%）。
 * 同一个行程内横向比较，用来回答"换成这条路我少吸多少"。
 */
export function doseReductionPct(routes: TravelRouteOption[], routeId: string): number {
  const target = routes.find(item => item.route_id === routeId)
  if (!target) return 0
  const worst = routes.reduce((peak, item) => Math.max(peak, item.intake_index), 0)
  if (worst <= 0) return 0
  return Math.max(0, (1 - target.intake_index / worst) * 100)
}

/** 吸入当量 → 三档风险描述 */
export function intakeRiskLabel(index: number): string {
  if (index >= 2.5) return '高风险'
  if (index >= 1) return '中等风险'
  return '低风险'
}
