/**
 * 坐标系转换：WGS84 ↔ GCJ-02。
 *
 * 为什么必须有这一步：
 *   项目里所有空间数据都是 WGS84 原始坐标——路网 12833 段中心线、5830 个 POI、
 *   轨道站、规划出的路线几何、污染暴露场网格，全部来自 GPS / 开放数据。
 *   而腾讯位置服务的底图是国测局加密后的 **GCJ-02**，官方文档明确要求原始 GPS 数据
 *   必须先转换，否则显示位置与实际位置会有偏差。
 *
 * 关于香港是否偏移——**注意别被"境外不偏移"这句话骗了**：
 *   常见的 `outOfChina` 判断用的是中国大陆的粗略外接矩形
 *   （经度 72.004~137.8347、纬度 0.8293~55.8271），而香港（约 114.17, 22.32）
 *   **落在这个矩形之内**，因此会走"施加偏移"的分支，实测偏移约 595 米。
 *   澳门、台湾同理。（台湾纬度 22~25、经度 120~122，也在矩形内。）
 *   真正原样返回的是日本、东南亚、欧美这类确实在矩形外的坐标。
 *
 * 如果接入密钥后发现叠加层整体偏了约 600 米，说明底图侧没加密，
 * 把下面的 `APPLY_GCJ_OFFSET` 改成 false 即可，不用动其它代码。
 *
 * 本模块不含任何 DOM / SDK 依赖，可以单独跑（用 esbuild 转一下即可验证数值）。
 */

const PI = Math.PI
const SEMI_MAJOR_AXIS = 6378245.0
const ECCENTRICITY_SQUARED = 0.00669342162296594323

/**
 * 是否对坐标施加 GCJ-02 偏移。
 *
 * 绝大多数情况下保持 true：腾讯地图的矢量底图按 GCJ-02 加密，叠加层必须同坐标系
 * 才能对齐。这里单独抽成开关，是因为**香港、澳门一带存在不确定性**——
 * 境外区域按官方口径不做加密偏移，但国内地图服务商对港澳数据的处理并不统一。
 *
 * 实测偏移量（本算法在本地算出的值）：
 *   - 香港旺角 / 尖沙咀 / 太子：约 **595 m**
 *   - 北京天安门（参照）：约 555 m
 *
 * 也就是说，如果底图本身其实没加密，叠加层会整体偏出约 600 米——比一个街区还宽，
 * 一眼就能看出来。接入密钥后**核对方法**：找一个已知地标（例如旺角站），
 * 看标出来的位置是否落在底图的对应位置上。若整体偏移约 600 m，
 * 把下面这行改成 false 再刷新即可，不需要改其它任何代码。
 */
const APPLY_GCJ_OFFSET = true

/** 粗略判断是否在中国境外；境外不做偏移。 */
function outOfChina(lat: number, lng: number): boolean {
  return lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271
}

function transformLat(lngOffset: number, latOffset: number): number {
  let value =
    -100.0 +
    2.0 * lngOffset +
    3.0 * latOffset +
    0.2 * latOffset * latOffset +
    0.1 * lngOffset * latOffset +
    0.2 * Math.sqrt(Math.abs(lngOffset))
  value += ((20.0 * Math.sin(6.0 * lngOffset * PI) + 20.0 * Math.sin(2.0 * lngOffset * PI)) * 2.0) / 3.0
  value += ((20.0 * Math.sin(latOffset * PI) + 40.0 * Math.sin((latOffset / 3.0) * PI)) * 2.0) / 3.0
  value += ((160.0 * Math.sin((latOffset / 12.0) * PI) + 320.0 * Math.sin((latOffset * PI) / 30.0)) * 2.0) / 3.0
  return value
}

function transformLng(lngOffset: number, latOffset: number): number {
  let value =
    300.0 +
    lngOffset +
    2.0 * latOffset +
    0.1 * lngOffset * lngOffset +
    0.1 * lngOffset * latOffset +
    0.1 * Math.sqrt(Math.abs(lngOffset))
  value += ((20.0 * Math.sin(6.0 * lngOffset * PI) + 20.0 * Math.sin(2.0 * lngOffset * PI)) * 2.0) / 3.0
  value += ((20.0 * Math.sin(lngOffset * PI) + 40.0 * Math.sin((lngOffset / 3.0) * PI)) * 2.0) / 3.0
  value += ((150.0 * Math.sin((lngOffset / 12.0) * PI) + 300.0 * Math.sin((lngOffset / 30.0) * PI)) * 2.0) / 3.0
  return value
}

export interface GcjPoint {
  lat: number
  lng: number
}

/**
 * 把一对 WGS84 经纬度转换成 GCJ-02。
 *
 * 参数顺序与腾讯地图一致：**先纬度后经度**。注意 GeoJSON 与 MapLibre 用的是
 * [经度, 纬度]，从那些数据结构取值时不要照搬。
 */
export function wgs84ToGcj02(lat: number, lng: number): GcjPoint {
  if (!APPLY_GCJ_OFFSET || outOfChina(lat, lng)) {
    return { lat, lng }
  }

  let deltaLat = transformLat(lng - 105.0, lat - 35.0)
  let deltaLng = transformLng(lng - 105.0, lat - 35.0)

  const radLat = (lat / 180.0) * PI
  let magic = Math.sin(radLat)
  magic = 1 - ECCENTRICITY_SQUARED * magic * magic
  const sqrtMagic = Math.sqrt(magic)

  deltaLat = (deltaLat * 180.0) / (((SEMI_MAJOR_AXIS * (1 - ECCENTRICITY_SQUARED)) / (magic * sqrtMagic)) * PI)
  deltaLng = (deltaLng * 180.0) / ((SEMI_MAJOR_AXIS / sqrtMagic) * Math.cos(radLat) * PI)

  return { lat: lat + deltaLat, lng: lng + deltaLng }
}

/** 把 WGS84 的 `[经度, 纬度]` 转成腾讯地图的 LatLng。 */
export function pointToLatLng(position: number[]): TMap.LatLng {
  const gcj = wgs84ToGcj02(Number(position[1]), Number(position[0]))
  return new TMap.LatLng(gcj.lat, gcj.lng)
}

/** 把 WGS84 的坐标序列转成 LatLng 数组，供折线 / 多边形使用。 */
export function pathToLatLngs(positions: number[][]): TMap.LatLng[] {
  return positions.map(pointToLatLng)
}

/** 把 WGS84 的一点转成 {lat, lng}（GCJ-02），用于计算包围盒与中心点。 */
export function toGcj(lon: number, lat: number): GcjPoint {
  return wgs84ToGcj02(lat, lon)
}

/**
 * GCJ-02 → WGS84，用迭代逼近求逆。
 *
 * 正向变换没有解析逆，但偏差量本身很小，反复"正向算一遍再把差值减掉"五次
 * 就能收敛到亚米级。用户在地图上点选或拖动起终点时，地图给的是 GCJ-02，
 * 而整个后端（路网、规划、气象）都按 WGS84 工作，所以这个逆变换是必需的——
 * 少了它，用户点出来的坐标回传给后端会整体偏 100~200 米。
 */
export function gcj02ToWgs84(lat: number, lng: number): GcjPoint {
  if (!APPLY_GCJ_OFFSET || outOfChina(lat, lng)) {
    return { lat, lng }
  }

  let wgsLat = lat
  let wgsLng = lng
  for (let index = 0; index < 5; index += 1) {
    const forward = wgs84ToGcj02(wgsLat, wgsLng)
    wgsLat -= forward.lat - lat
    wgsLng -= forward.lng - lng
  }
  return { lat: wgsLat, lng: wgsLng }
}

/** 把地图事件得到的 LatLng（GCJ-02）转回 WGS84 的 {lon, lat}。 */
export function latLngToWgs84(position: TMap.LatLng): { lon: number; lat: number } {
  const wgs = gcj02ToWgs84(position.getLat(), position.getLng())
  return { lon: Number(wgs.lng.toFixed(6)), lat: Number(wgs.lat.toFixed(6)) }
}
