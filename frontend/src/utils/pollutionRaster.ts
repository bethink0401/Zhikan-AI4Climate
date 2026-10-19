/**
 * 把散点污染暴露场重采样成一张连续场图片。
 *
 * 为什么不用一堆色圆去拼：
 * Agent 输出的采样点在经纬度上是**对角线格点**，相邻点实测约 224 m，
 * 而之前圆半径只有 110 m —— 既盖不满（露缝）又只有 8 级色阶（露台阶），
 * 于是看起来是一块块斑点。改成先做高斯核加权平均得到连续场，
 * 再贴到地图底图层，视觉上才是平滑过渡的。
 *
 * 采样与输出的分工：
 *   高斯**溅射**（每个采样点把核铺到邻近像素）而不是逐像素找邻居，
 *   计算量只跟点数 × 核宽有关，与输出分辨率无关，477 点约 3 ms。
 */

import type { TravelFieldSample } from '@/types/travel'

/** 色阶：低=浅灰蓝（安全）→ 高=紫红（务必避开） */
export const POLLUTION_RAMP: Array<{ stop: number; rgb: [number, number, number] }> = [
  { stop: 0.0, rgb: [207, 232, 255] },
  { stop: 0.15, rgb: [127, 196, 245] },
  { stop: 0.3, rgb: [247, 215, 116] },
  { stop: 0.5, rgb: [245, 154, 78] },
  { stop: 0.7, rgb: [236, 95, 95] },
  { stop: 0.85, rgb: [201, 48, 107] },
  { stop: 1.0, rgb: [123, 31, 162] }
]

/** 场值低于这个数就当作背景，完全不显色 */
const VISIBLE_FLOOR = 0.04

/** 场值到这个数时达到最大不透明度 */
const VISIBLE_CEIL = 0.94

/** 最大不透明度。底图是彩色街道图，太实会看不清路网 */
const MAX_ALPHA = 0.82

export interface PollutionRaster {
  /** data URL，直接喂给 ImageGroundLayer */
  src: string
  bounds: { minLon: number; minLat: number; maxLon: number; maxLat: number }
  cols: number
  rows: number
  /** 本场的最小 / 最大值，供图例与文案使用 */
  range: { min: number; max: number }
}

/** 在色阶上按指数插值出一个 rgb 三元组 */
export function rampRgb(index: number): [number, number, number] {
  const value = Math.max(0, Math.min(1, index))
  for (let position = 1; position < POLLUTION_RAMP.length; position += 1) {
    const previous = POLLUTION_RAMP[position - 1]
    const current = POLLUTION_RAMP[position]
    if (value <= current.stop) {
      const span = current.stop - previous.stop || 1
      const ratio = (value - previous.stop) / span
      return [
        Math.round(previous.rgb[0] + (current.rgb[0] - previous.rgb[0]) * ratio),
        Math.round(previous.rgb[1] + (current.rgb[1] - previous.rgb[1]) * ratio),
        Math.round(previous.rgb[2] + (current.rgb[2] - previous.rgb[2]) * ratio)
      ]
    }
  }
  return POLLUTION_RAMP[POLLUTION_RAMP.length - 1].rgb
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

const METERS_PER_DEG_LAT = 110540
const METERS_PER_DEG_LON_AT_EQUATOR = 111320

/**
 * 构建连续场。
 *
 * @param samples Agent 输出的散点
 * @param targetCols 目标列数上限；行数按外接框长宽比自动算
 * @returns 采样点太少或经纬度退化时返回 null，由调用方回退到不画场
 */
export function buildPollutionRaster(
  samples: TravelFieldSample[],
  targetCols = 200
): PollutionRaster | null {
  if (samples.length < 3) return null

  let minLon = Infinity
  let maxLon = -Infinity
  let minLat = Infinity
  let maxLat = -Infinity
  let minValue = Infinity
  let maxValue = -Infinity

  for (const sample of samples) {
    if (!Number.isFinite(sample.lon) || !Number.isFinite(sample.lat)) continue
    minLon = Math.min(minLon, sample.lon)
    maxLon = Math.max(maxLon, sample.lon)
    minLat = Math.min(minLat, sample.lat)
    maxLat = Math.max(maxLat, sample.lat)
    minValue = Math.min(minValue, sample.index)
    maxValue = Math.max(maxValue, sample.index)
  }

  if (!Number.isFinite(minLon) || maxLon - minLon <= 0 || maxLat - minLat <= 0) return null

  const centerLat = (minLat + maxLat) / 2
  const metersPerDegLon =
    METERS_PER_DEG_LON_AT_EQUATOR * Math.cos((centerLat * Math.PI) / 180)

  const spanLonM = Math.max(1, (maxLon - minLon) * metersPerDegLon)
  const spanLatM = Math.max(1, (maxLat - minLat) * METERS_PER_DEG_LAT)

  // 用外接框面积除以点数开方，得到采样点的平均间距，作为核宽的量级。
  // 系数取 0.7：核太小格点会浮出花纹，太大峰值会被抹平。
  const spacingM = Math.sqrt((spanLonM * spanLatM) / samples.length)
  const sigmaM = Math.max(15, spacingM * 0.7)

  // 外扩一圈：让最外圈自然衰减到 0，否则贴图会出现硬边矩形
  const padLon = (maxLon - minLon) * 0.12 + sigmaM / metersPerDegLon
  const padLat = (maxLat - minLat) * 0.12 + sigmaM / METERS_PER_DEG_LAT
  minLon -= padLon
  maxLon += padLon
  minLat -= padLat
  maxLat += padLat

  const totalLonM = (maxLon - minLon) * metersPerDegLon
  const totalLatM = (maxLat - minLat) * METERS_PER_DEG_LAT

  // 像素边长取核宽的 1/3 左右：既保证比核细，又不至于输出过大
  const cols = clamp(Math.round(totalLonM / (sigmaM / 3)), 96, targetCols)
  const rows = clamp(Math.round((cols * totalLatM) / totalLonM), 96, 320)

  const dLon = (maxLon - minLon) / cols
  const dLat = (maxLat - minLat) / rows
  const pxLonM = totalLonM / cols
  const pxLatM = totalLatM / rows

  const weights = new Float32Array(cols * rows)
  const values = new Float32Array(cols * rows)

  const twoSigmaSq = 2 * sigmaM * sigmaM
  const kernelRadiusM = 3 * sigmaM
  const kernelRadiusSq = kernelRadiusM * kernelRadiusM
  const halfPx = Math.ceil(kernelRadiusM / Math.min(pxLonM, pxLatM)) + 1

  for (const sample of samples) {
    if (!Number.isFinite(sample.lon) || !Number.isFinite(sample.lat)) continue
    // 行号自北向南，所以纬度要反过来算
    const cx = (sample.lon - minLon) / dLon
    const cy = (maxLat - sample.lat) / dLat

    const x0 = Math.max(0, Math.floor(cx - halfPx))
    const x1 = Math.min(cols - 1, Math.ceil(cx + halfPx))
    const y0 = Math.max(0, Math.floor(cy - halfPx))
    const y1 = Math.min(rows - 1, Math.ceil(cy + halfPx))

    for (let y = y0; y <= y1; y += 1) {
      const dy = (y + 0.5 - cy) * pxLatM
      const rowOffset = y * cols
      for (let x = x0; x <= x1; x += 1) {
        const dx = (x + 0.5 - cx) * pxLonM
        const distanceSq = dx * dx + dy * dy
        if (distanceSq > kernelRadiusSq) continue
        const weight = Math.exp(-distanceSq / twoSigmaSq)
        const position = rowOffset + x
        weights[position] += weight
        values[position] += weight * sample.index
      }
    }
  }

  const canvas = document.createElement('canvas')
  canvas.width = cols
  canvas.height = rows
  const context = canvas.getContext('2d')
  if (!context) return null

  const image = context.createImageData(cols, rows)
  const data = image.data
  const fadeX = Math.max(1, cols * 0.06)
  const fadeY = Math.max(1, rows * 0.06)

  // 先把平滑后的场值算出来并求峰值：
  // 高斯核必然把峰值压低（本例 0.90 会掉到 0.6 上下），
  // 直接上色的话最高污染区只到橙色、永远到不了色阶顶端。
  // 这里按"平滑峰值 → 原始峰值"做一次线性拉伸，形状不变，对比度回到正常。
  const field = new Float32Array(cols * rows)
  let smoothedMax = 0
  for (let position = 0; position < field.length; position += 1) {
    const weight = weights[position]
    const value = weight > 1e-6 ? values[position] / weight : 0
    field[position] = value
    if (value > smoothedMax) smoothedMax = value
  }

  const gain =
    smoothedMax > 1e-6 && maxValue > 0 ? clamp(maxValue / smoothedMax, 1, 1.9) : 1

  for (let y = 0; y < rows; y += 1) {
    // 边界羽化：即使外扩不够，也不会露出贴图的直角边
    const edgeY = Math.min(1, Math.min(y, rows - 1 - y) / fadeY)
    for (let x = 0; x < cols; x += 1) {
      const position = y * cols + x
      const value = Math.min(1, field[position] * gain)

      const normalized = clamp((value - VISIBLE_FLOOR) / (VISIBLE_CEIL - VISIBLE_FLOOR), 0, 1)
      const edgeX = Math.min(1, Math.min(x, cols - 1 - x) / fadeX)
      const alpha = Math.pow(normalized, 0.65) * MAX_ALPHA * Math.min(edgeX, edgeY)

      const [red, green, blue] = rampRgb(value)
      const offset = position * 4
      data[offset] = red
      data[offset + 1] = green
      data[offset + 2] = blue
      data[offset + 3] = Math.round(alpha * 255)
    }
  }

  context.putImageData(image, 0, 0)

  return {
    src: canvas.toDataURL('image/png'),
    bounds: { minLon, minLat, maxLon, maxLat },
    cols,
    rows,
    range: {
      min: Number.isFinite(minValue) ? minValue : 0,
      max: Number.isFinite(maxValue) ? maxValue : 0
    }
  }
}
