/**
 * 空间几何工具
 * 用于生成距离圈、传播扇区等空间要素
 */

// @ts-ignore - Turf ESM 类型解析问题
import circle from '@turf/circle'
// @ts-ignore
import sector from '@turf/sector'
// @ts-ignore
import { point } from '@turf/helpers'
// @ts-ignore
import booleanPointInPolygon from '@turf/boolean-point-in-polygon'

/**
 * 生成距离参考圈（真实地理距离）
 * @param center 中心点 [lon, lat]
 * @param radiusKm 半径（公里）
 * @param steps 圆周分段数（默认 64）
 */
export function createDistanceCircle(
  center: [number, number],
  radiusKm: number,
  steps: number = 64
): GeoJSON.Feature<GeoJSON.Polygon> {
  return circle(center, radiusKm, { steps, units: 'kilometers' })
}

/**
 * 生成潜在传播风险扇区
 * P3.4: 基于主导风向的下风向传播风险可视化
 * 
 * @param center 中心点 [lon, lat]
 * @param downwindDirection 下风向方向（度，0=北，90=东）
 * @param maxRadiusKm 最大半径（公里）
 * @param halfAngleDeg 扇区半角（度，例如 45 表示左右各展开 45°，总共 90°）
 */
export function createPropagationSector(
  center: [number, number],
  downwindDirection: number,
  maxRadiusKm: number,
  halfAngleDeg: number = 45
): GeoJSON.Feature<GeoJSON.Polygon> {
  // 扇区起始角度和终止角度
  const startAngle = downwindDirection - halfAngleDeg
  const endAngle = downwindDirection + halfAngleDeg

  // 使用 turf.sector 生成扇区
  // bearing1: 起始角度，bearing2: 终止角度
  const sectorFeature = sector(
    center,
    maxRadiusKm,
    startAngle,
    endAngle,
    { units: 'kilometers' }
  )

  return sectorFeature
}

/**
 * 计算 POI 是否在扇区内
 * @param poi POI 坐标 [lon, lat]
 * @param sectorFeature 扇区 Polygon
 */
export function isPointInSector(
  poi: [number, number],
  sectorFeature: GeoJSON.Feature<GeoJSON.Polygon>
): boolean {
  const pt = point(poi)
  return booleanPointInPolygon(pt, sectorFeature)
}

/**
 * P3.4 配置：传播扇区可视化参数
 * 这些是前端风险表达参数，不是物理扩散模型输出
 */
export const PROPAGATION_VISUALIZATION_CONFIG = {
  // 扇区最大半径（公里）
  MAX_RADIUS_KM: 3,
  
  // 扇区半角（度）：左右各展开 45°，总共 90° 扇形
  SECTOR_HALF_ANGLE_DEG: 45,
  
  // 距离参考圈半径（公里）
  DISTANCE_RINGS_KM: [1, 2, 3]
} as const
