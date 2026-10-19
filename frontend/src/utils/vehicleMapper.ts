/**
 * 车型映射工具
 * 将 TrafficAgent 返回的英文车型名称映射为中文
 */

export const VEHICLE_TYPE_MAP: Record<string, string> = {
  'Franchised Bus DD': '专营双层巴士',
  'Franchised Bus SD': '专营单层巴士',
  'Private Car': '私家车',
  'Taxi': '出租车',
  'Goods Vehicle Light': '轻型货车',
  'Goods Vehicle M&H': '中重型货车',
  'Motor Cycle': '摩托车',
  'Non Franchised Bus': '非专营巴士',
  'Private LB': '私人轻型巴士',
  'PLB': '公共轻型巴士'
}

/**
 * 获取车型中文名称
 * @param type 英文车型名称
 * @returns 中文车型名称，如果没有映射则返回原值
 */
export function getVehicleLabel(type: string): string {
  return VEHICLE_TYPE_MAP[type] || type
}
