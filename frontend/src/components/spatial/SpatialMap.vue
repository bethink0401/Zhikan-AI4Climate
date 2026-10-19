<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { Loading, Location, WarnTriangleFilled } from '@element-plus/icons-vue'
import type { SpatialResult } from '@/types/spatial'
import type { MeteorologyResult } from '@/types/meteorology'
import {
  createPropagationSector,
  isPointInSector,
  PROPAGATION_VISUALIZATION_CONFIG
} from '@/utils/spatialGeometry'
import { pathToLatLngs, pointToLatLng, toGcj } from '@/utils/coordTransform'
import { MAP_KEY_MISSING_MESSAGE, hasMapKey, loadTMap } from '@/utils/tmapLoader'

/**
 * SpatialMap - 空间影响地图（腾讯地图 GL JS）
 *
 * P3.1: 基础地图初始化 + 站点标记
 * P3.2: Spatial 业务图层（affected_area, upwind_roads, sensitive_pois）
 * P3.3: 可读性与交互（图例、信息窗、悬停、视觉层级）
 * P3.4: 公里级传播风险表达（距离圈、风向指示、传播扇区）
 * P3.5: 底图由 MapLibre demo 瓦片切换为腾讯地图
 *
 * 与 MapLibre 实现的对应关系：
 *   原 GeoJSON Source + 按属性插值的 paint 表达式，在腾讯地图里没有等价物，
 *   改为按取值分桶、预先生成多个样式再绑定到几何上。因此下面的每个 update*
 *   都多了一步"算桶"的逻辑。
 */

interface Props {
  spatial: SpatialResult | null
  meteorology: MeteorologyResult | null
  /** 是否绘制短时预测扩散扇形（仅"恶劣天气与公里级传播预警"页开启） */
  showForecast?: boolean
}

const props = withDefaults(defineProps<Props>(), { showForecast: false })

/** POI 类型 → 中文标签 */
const POI_TYPE_LABELS: Record<string, string> = {
  school: '学校',
  healthcare: '医疗',
  rail_transit: '轨道交通',
  community_service: '社区服务',
  community_centre: '社区中心'
}

/** POI 类型 → 颜色，与图例保持一致 */
const POI_TYPE_COLORS: Record<string, string> = {
  school: '#9c27b0',
  healthcare: '#f44336',
  rail_transit: '#4caf50',
  community_service: '#ff9800',
  community_centre: '#ffc107'
}

const DEFAULT_POI_COLOR = '#757575'
const FALLBACK_CENTER = { lon: 114.1694, lat: 22.3193 }

/** 敏感目标圆的半径（米）。原实现是 4 px，在 z13 约合 70 m。 */
const POI_RADIUS_M = 55

/** 预测扩散中心线上的一个采样点：位置、扩散半宽、输送方位角 */
type FanSample = {
  lon: number
  lat: number
  halfWidthKm: number
  bearing: number
  /** 左右边缘各自的宽度系数（1 为数据原值），用于叠出不规则的烟羽轮廓 */
  leftScale?: number
  rightScale?: number
}

const mapContainer = shallowRef<HTMLDivElement | null>(null)
// 类实例一律用 shallowRef：用 ref 会让 Vue 对类型做深度展开，vue-tsc 报 TS2589。
const map = shallowRef<TMap.Map | null>(null)
const ringLayer = shallowRef<TMap.MultiCircleLayer | null>(null)
const sectorLayer = shallowRef<TMap.MultiPolygonLayer | null>(null)
const affectedLayer = shallowRef<TMap.MultiPolygonLayer | null>(null)
const roadLayer = shallowRef<TMap.MultiPolylineLayer | null>(null)
const poiLayer = shallowRef<TMap.MultiCircleLayer | null>(null)
const stationLayer = shallowRef<TMap.MultiMarkerLayer | null>(null)
const windArrowLayer = shallowRef<TMap.MultiMarkerLayer | null>(null)
// 可解释预测图层：扩散扇形 + 流动亮带（节点已删，预测依据改由右侧面板表达）
const corridorLayer = shallowRef<TMap.MultiPolygonLayer | null>(null)
/** 内层强度环：同形状收缩，叠出由外向内递增的浓度，模拟渲染渐变 */
const fanInnerLayer = shallowRef<TMap.MultiPolygonLayer | null>(null)
/** 流动亮带：沿输送方向从站点向外推移的窄条，负责表达"流动" */
const flowLayer = shallowRef<TMap.MultiPolygonLayer | null>(null)
const infoWindow = shallowRef<TMap.InfoWindow | null>(null)

const isMapLoading = ref(true)
const mapError = ref<string | null>(null)
const needsKey = ref(false)
const layersInitialized = ref(false)

/** 扇区内 POI 统计（P3.4） */
const sectorPOIStats = computed(() => {
  if (
    !props.spatial?.sensitive_pois ||
    !props.spatial?.station ||
    !props.meteorology?.downwind_direction
  ) {
    return null
  }

  const downwindDir = props.meteorology.downwind_direction
  if (downwindDir === null) return null

  const sector = createPropagationSector(
    [props.spatial.station.lon, props.spatial.station.lat],
    downwindDir,
    PROPAGATION_VISUALIZATION_CONFIG.MAX_RADIUS_KM,
    PROPAGATION_VISUALIZATION_CONFIG.SECTOR_HALF_ANGLE_DEG
  )

  const poisInSector = props.spatial.sensitive_pois.filter(poi =>
    isPointInSector([poi.lon, poi.lat], sector)
  )

  const byType: Record<string, number> = {}
  poisInSector.forEach(poi => {
    byType[poi.poi_type] = (byType[poi.poi_type] || 0) + 1
  })

  return { total: poisInSector.length, byType }
})

/** 预测路径是否可绘（决定图例里是否出现"短时预测"分组） */
const hasForecastPath = computed(
  () =>
    props.meteorology?.pollution_path_prediction?.status === 'ok' &&
    (props.meteorology?.pollution_path_prediction?.steps?.length ?? 0) > 0
)

/** 短时预测分组：既要有数据，也要当前页面开启了预测图层 */
const showForecastGroup = computed(() => props.showForecast === true && hasForecastPath.value)

// ── 图标：一律用内联 SVG data URL，不引用任何示例站点图片 ────────────────

function svgUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
}

/** 风向箭头。旋转角度直接写进 SVG，不依赖 SDK 的 rotate 符号约定。 */
function windArrowIcon(direction: number): string {
  return svgUrl(
    `<svg xmlns="http://www.w3.org/2000/svg" width="56" height="56">` +
      `<g transform="rotate(${direction.toFixed(1)} 28 28)">` +
      `<path d="M 28 6 L 34 22 L 30.5 22 L 30.5 44 L 25.5 44 L 25.5 22 L 22 22 Z" ` +
      `fill="#ff5722" stroke="#ffffff" stroke-width="1.5"/></g></svg>`
  )
}

function stationIcon(): string {
  return svgUrl(
    `<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22">` +
      `<circle cx="11" cy="11" r="8" fill="#ff4444" stroke="#ffffff" stroke-width="2"/></svg>`
  )
}

// ── 初始化 ────────────────────────────────────────────────────────────────

async function initMap(): Promise<void> {
  if (!mapContainer.value) return

  if (!hasMapKey()) {
    needsKey.value = true
    isMapLoading.value = false
    return
  }

  try {
    await loadTMap()
    if (!mapContainer.value) return

    const station = props.spatial?.station
    const center = station
      ? pointToLatLng([station.lon, station.lat])
      : pointToLatLng([FALLBACK_CENTER.lon, FALLBACK_CENTER.lat])

    const newMap = new TMap.Map(mapContainer.value, {
      center,
      zoom: 13,
      viewMode: '2D',
      showControl: false
    })
    map.value = newMap

    initLayers()
    updateSpatialLayers()
    isMapLoading.value = false
  } catch (error) {
    isMapLoading.value = false
    mapError.value = error instanceof Error ? error.message : '地图初始化失败'
  }
}

function initLayers(): void {
  const currentMap = map.value
  if (!currentMap || layersInitialized.value) return

  // 图层顺序按 zIndex 从底到顶：距离圈 → 传播扇区 → 影响区域 → 道路 → POI → 箭头 → 站点
  ringLayer.value = new TMap.MultiCircle({
    id: 'spatial-rings',
    map: currentMap,
    zIndex: 1,
    styles: {
      // 只留描边：圆填充设为全透明，视觉上就是"参考圈"
      // ⚠️ 线宽必须用整数：腾讯地图会校验 width / borderWidth，传小数会直接在
      //    控制台报"属性无效"并整条样式失效（实测 1.5 / 1.2 / 2.5 / 4.5 均被拒）。
      ring_1: new TMap.CircleStyle({
        color: 'rgba(158,158,158,0)',
        showBorder: true,
        borderColor: 'rgba(158,158,158,0.5)',
        borderWidth: 2,
        borderDashArray: [4, 3]
      }),
      ring_2: new TMap.CircleStyle({
        color: 'rgba(158,158,158,0)',
        showBorder: true,
        borderColor: 'rgba(158,158,158,0.5)',
        borderWidth: 1,
        borderDashArray: [4, 3]
      }),
      ring_3: new TMap.CircleStyle({
        color: 'rgba(158,158,158,0)',
        showBorder: true,
        borderColor: 'rgba(158,158,158,0.5)',
        borderWidth: 1,
        borderDashArray: [4, 3]
      })
    },
    geometries: []
  })

  sectorLayer.value = new TMap.MultiPolygon({
    id: 'spatial-sector',
    map: currentMap,
    zIndex: 2,
    styles: {
      sector: new TMap.PolygonStyle({
        // 原实现是 0.10，那是针对白色空白底图调的；换成真实的彩色底图后
        // 10% 的填充几乎不可见，这里提到 0.22，仍不至于盖住底图路网。
        color: 'rgba(3,169,244,0.22)',
        showBorder: true,
        borderColor: '#03a9f4',
        borderWidth: 2
      })
    },
    geometries: []
  })

  affectedLayer.value = new TMap.MultiPolygon({
    id: 'spatial-affected',
    map: currentMap,
    zIndex: 3,
    styles: {
      affected: new TMap.PolygonStyle({
        // 同 sector：原为 0.12，在白底上够用，放到真实底图上需要提高
        color: 'rgba(255,152,0,0.20)',
        showBorder: true,
        borderColor: '#ff9800',
        borderWidth: 3
      })
    },
    geometries: []
  })

  roadLayer.value = new TMap.MultiPolyline({
    id: 'spatial-roads',
    map: currentMap,
    zIndex: 5,
    styles: {
      road_high: new TMap.PolylineStyle({ color: '#1976d2', width: 6, lineCap: 'round' }),
      road_mid: new TMap.PolylineStyle({ color: '#1976d2', width: 4, lineCap: 'round' }),
      road_low: new TMap.PolylineStyle({ color: '#1976d2', width: 3, lineCap: 'round' }),
      road_min: new TMap.PolylineStyle({ color: '#1976d2', width: 2, lineCap: 'round' })
    },
    geometries: []
  })
  roadLayer.value.on('click', (event: TMap.GeometryOverlayEvent) => {
    const values = event.geometry.properties ?? {}
    const roadName = String(values.road_name_zh || values.road_name || '未知道路')
    const matchScore = Number(values.match_score)
    const distance = Number(values.distance_to_station_km)
    showInfo(
      event.latLng,
      `<div class="spatial-popup-title" style="color:#1976d2">上风向候选道路</div>` +
        `<div><strong>道路名称：</strong>${roadName}</div>` +
        `<div><strong>空间匹配评分：</strong>${
          Number.isFinite(matchScore) ? (matchScore * 100).toFixed(1) : 'N/A'
        }</div>` +
        `<div><strong>距站点：</strong>${
          Number.isFinite(distance) ? distance.toFixed(2) : 'N/A'
        } km</div>`
    )
  })
  roadLayer.value.on('hover', (event: TMap.GeometryOverlayEvent | null) => {
    map.value?.setCursorStyle(event ? 'pointer' : 'default')
  })

  poiLayer.value = new TMap.MultiCircle({
    id: 'spatial-pois',
    map: currentMap,
    zIndex: 6,
    styles: {
      ...Object.fromEntries(
        Object.entries(POI_TYPE_COLORS).map(([type, color]) => [
          `poi_${type}`,
          new TMap.CircleStyle({
            color: hexToRgba(color, 0.55),
            showBorder: true,
            borderColor: '#ffffff',
            borderWidth: 1
          })
        ])
      ),
      // 兜底：数据里若出现未知 poi_type，不至于落到 SDK 的默认样式上
      poi_unknown: new TMap.CircleStyle({
        color: hexToRgba(DEFAULT_POI_COLOR, 0.55),
        showBorder: true,
        borderColor: '#ffffff',
        borderWidth: 1
      })
    },
    geometries: []
  })
  poiLayer.value.on('click', (event: TMap.GeometryOverlayEvent) => {
    const values = event.geometry.properties ?? {}
    const poiName = String(values.name_zh || values.name || '未知')
    const poiTypeKey = String(values.poi_type ?? '')
    const poiType = POI_TYPE_LABELS[poiTypeKey] || poiTypeKey || '未知'
    const distance = Number(values.distance_to_station_km)
    showInfo(
      event.latLng,
      `<div class="spatial-popup-title">敏感目标</div>` +
        `<div><strong>名称：</strong>${poiName}</div>` +
        `<div><strong>类型：</strong>${poiType}</div>` +
        `<div><strong>距站点：</strong>${
          Number.isFinite(distance) ? distance.toFixed(2) : 'N/A'
        } km</div>`
    )
  })
  poiLayer.value.on('hover', (event: TMap.GeometryOverlayEvent | null) => {
    map.value?.setCursorStyle(event ? 'pointer' : 'default')
  })

  windArrowLayer.value = new TMap.MultiMarker({
    id: 'spatial-wind-arrow',
    map: currentMap,
    zIndex: 7,
    styles: { arrow: new TMap.MarkerStyle({ width: 56, height: 56, src: windArrowIcon(0) }) },
    geometries: []
  })

  // ── 可解释预测图层 ──────────────────────────────────────────────
  // zIndex 低于站点/风向箭头，保证站点仍是视觉焦点。

  // ── 预测扩散：两层静态扇形 + 一条流动光带 ────────────────────────
  // TMap 多边形不支持渐变填充，所以用内层收缩环按更高不透明度叠加，
  // 模拟由外向内递增的浓度。两层都是静态样式，动画只动流动亮带，
  // 整片扇形不再明暗起伏（旧版"呼吸"是闪烁感的来源）。

  // 1) 最外层光晕：面积最大、最淡，负责"扩散出去"的边界感
  corridorLayer.value = new TMap.MultiPolygon({
    id: 'spatial-forecast-fan',
    map: currentMap,
    zIndex: 3,
    styles: {
      fan: new TMap.PolygonStyle({
        color: 'rgba(64,132,224,0.10)',
        showBorder: true,
        borderColor: 'rgba(64,132,224,0.22)',
        borderWidth: 1
      })
    },
    geometries: []
  })

  // 2) 内层强度环：同样形状按比例收缩，颜色更实，制造径向浓淡
  fanInnerLayer.value = new TMap.MultiPolygon({
    id: 'spatial-forecast-fan-core',
    map: currentMap,
    zIndex: 4,
    styles: {
      core: new TMap.PolygonStyle({
        color: 'rgba(45,110,205,0.18)',
        showBorder: false,
        borderWidth: 0
      })
    },
    geometries: []
  })

  // 3) 流动光带：拆成相邻的 5 段窄条，透明度沿推进方向递增，
  //    拼成一条"后淡前亮"的柔性光带，段与段无缝衔接、没有硬边。
  const flowStyles: Record<string, TMap.PolygonStyle> = {}
  FLOW_SLICE_ALPHAS.forEach((alpha, index) => {
    flowStyles[`flow_${index}`] = new TMap.PolygonStyle({
      color: `rgba(58,140,240,${alpha})`,
      showBorder: false,
      borderWidth: 0
    })
  })
  flowLayer.value = new TMap.MultiPolygon({
    id: 'spatial-forecast-flow',
    map: currentMap,
    zIndex: 5,
    styles: flowStyles,
    geometries: []
  })

  stationLayer.value = new TMap.MultiMarker({
    id: 'spatial-station',
    map: currentMap,
    zIndex: 8,
    styles: { station: new TMap.MarkerStyle({ width: 22, height: 22, src: stationIcon() }) },
    geometries: []
  })
  stationLayer.value.on('click', (event: TMap.GeometryOverlayEvent) => {
    showInfo(
      event.latLng,
      `<div class="spatial-popup-title">监测站点</div>` +
        `<div>${String(event.geometry.properties?.station_name ?? '')}</div>`
    )
  })

  layersInitialized.value = true
}

/** 把 #RRGGBB 转成带透明度的 rgba()，用于需要半透明的填充。 */
function hexToRgba(hex: string, alpha: number): string {
  const value = hex.replace('#', '')
  const red = parseInt(value.slice(0, 2), 16)
  const green = parseInt(value.slice(2, 4), 16)
  const blue = parseInt(value.slice(4, 6), 16)
  return `rgba(${red},${green},${blue},${alpha})`
}

function showInfo(position: TMap.LatLng, content: string): void {
  const currentMap = map.value
  if (!currentMap) return
  const html = `<div class="spatial-popup">${content}</div>`
  if (!infoWindow.value) {
    infoWindow.value = new TMap.InfoWindow({ map: currentMap, position, offset: { x: 0, y: -12 } })
  }
  infoWindow.value.setPosition(position)
  infoWindow.value.setContent(html)
  infoWindow.value.open()
}

// ── 图层数据更新 ──────────────────────────────────────────────────────────

function updateSpatialLayers(): void {
  if (!map.value || !layersInitialized.value) return

  updateDistanceRings()
  updatePropagationSector()
  updateAffectedArea()
  updateUpwindRoads()
  updateSensitivePOIs()
  updateStationMarker()
  updateWindDirectionArrow()
  updateForecastLayers()
  fitMapBounds()
}

/**
 * 可解释预测图层：静态扩散烟羽 + 流动光带。
 *
 * 数据来自 meteorology.pollution_path_prediction（前向拉格朗日平流）。
 * 短时预测只在"恶劣天气与公里级传播预警"页绘制（show-forecast 开关），
 * 其他页面复用同一组件时只保留传播扇区等基础图层。
 *
 * 扇形构建分两步，避免只有 3 个时间步时边缘成了生硬色块：
 *   1) 对相邻时间步之间的中心线与扩散半宽做线性插值，加密采样；
 *   2) 每个采样点沿垂直于输送方向左右展开，连成平滑扇面。
 *
 * 后端在弱风/风向不稳时返回 status=calm/unavailable，此时图层清空，
 * 避免把静风误绘成一条传播路径。
 */
function updateForecastLayers(): void {
  const fan = corridorLayer.value
  const inner = fanInnerLayer.value
  const flow = flowLayer.value
  if (!fan || !inner || !flow) return

  if (!props.showForecast) {
    fan.setGeometries([])
    inner.setGeometries([])
    flow.setGeometries([])
    stopFanAnimation()
    return
  }

  const prediction = props.meteorology?.pollution_path_prediction
  const rawSteps = prediction?.status === 'ok' ? prediction.steps ?? [] : []

  const station = props.spatial?.station
  const chain = rawSteps
    .filter(step => Number.isFinite(step.lon) && Number.isFinite(step.lat))
    .sort((a, b) => a.lead_hour - b.lead_hour)

  if (!prediction || prediction.status !== 'ok' || chain.length === 0 || !station) {
    fan.setGeometries([])
    inner.setGeometries([])
    flow.setGeometries([])
    stopFanAnimation()
    return
  }

  // ── 1. 加密采样：相邻时间步之间插入过渡点，使扇面边缘平滑 ────────
  // 顶点从站点出发，之后以时间步为锚点；每段插入 SAMPLES_PER_SEGMENT 个点。
  const anchors: FanSample[] = chain.map(step => ({
    lon: step.lon,
    lat: step.lat,
    halfWidthKm: Math.max(Number(step.corridor_half_width_km) || 0, 0.05),
    bearing: Number(step.wind_to_direction) || 0
  }))

  const samples: FanSample[] = []
  let cursor: FanSample = {
    lon: station.lon,
    lat: station.lat,
    halfWidthKm: 0.05,
    bearing: anchors[0].bearing
  }

  anchors.forEach(anchor => {
    for (let i = 1; i <= FAN_SAMPLES_PER_SEGMENT; i += 1) {
      const t = i / FAN_SAMPLES_PER_SEGMENT
      samples.push({
        lon: cursor.lon + (anchor.lon - cursor.lon) * t,
        lat: cursor.lat + (anchor.lat - cursor.lat) * t,
        halfWidthKm: cursor.halfWidthKm + (anchor.halfWidthKm - cursor.halfWidthKm) * t,
        bearing: smoothBearing(cursor.bearing, anchor.bearing, t)
      })
    }
    cursor = anchor
  })

  const origin: FanSample = {
    lon: station.lon,
    lat: station.lat,
    halfWidthKm: 0.05,
    bearing: anchors[0].bearing
  }

  // ── 2. 烟羽化，再展开成两层轮廓 ──────────────────────────────────
  // 在数据中心线上叠加蛇形摆动与不对称的边缘抖动（幅度取决于风向
  // 摆动），得到不规则的污染扩散轮廓；内层强度环共用同一形态，
  // 只按比例收缩，叠出由外向内递增的浓度。
  const outerSamples = applyPlumeShape(
    [origin, ...samples],
    bearingSpreadDeg(anchors.map(anchor => anchor.bearing))
  )
  const coreSamples = outerSamples.map(sample => ({
    ...sample,
    halfWidthKm: sample.halfWidthKm * FAN_CORE_SCALE
  }))

  fan.setGeometries([
    {
      id: 'forecast_fan',
      styleId: 'fan',
      paths: pathToLatLngs(buildRing(outerSamples)),
      properties: {
        max_distance_km: chain[chain.length - 1]?.distance_from_origin_km ?? 0,
        hours: chain.length
      }
    }
  ])

  inner.setGeometries([
    {
      id: 'forecast_fan_core',
      styleId: 'core',
      paths: pathToLatLngs(buildRing(coreSamples))
    }
  ])

  // 流动光带的几何交给动画逐帧重建：把采样序列存起来备用
  fanSamples = outerSamples

  startFanAnimation()
}

/**
 * 把沿中心线的一组采样点左右展开，闭合成多边形环。
 *
 * 传入的 `samples` 必须是按行程递增的顺序；第一个点通常就是站点本身
 * （半宽接近 0），因此环会自然收拢成以站点为顶点的形状。
 * 左右边缘各自乘 sample 的宽度系数，叠出烟羽的不对称轮廓。
 */
function buildRing(samples: FanSample[]): number[][] {
  const leftEdge: number[][] = []
  const rightEdge: number[][] = []

  samples.forEach(sample => {
    const left = offsetPoint(
      sample.lon,
      sample.lat,
      sample.halfWidthKm * (sample.leftScale ?? 1),
      sample.bearing - 90
    )
    const right = offsetPoint(
      sample.lon,
      sample.lat,
      sample.halfWidthKm * (sample.rightScale ?? 1),
      sample.bearing + 90
    )
    leftEdge.push([left.lon, left.lat])
    rightEdge.push([right.lon, right.lat])
  })

  return [...leftEdge, ...rightEdge.reverse(), leftEdge[0]]
}

/**
 * 截取 [fromT, toT] 行程区间内的采样点，用于生成流动亮带。
 *
 * 采样点在中心线上近似等距分布，因此按索引比例切分即可；
 * 两端各做一次线性插值，保证亮带边界随时间平滑推移而不是跳变。
 */
function sliceSamples(
  samples: FanSample[],
  fromT: number,
  toT: number
): FanSample[] {
  const last = samples.length - 1
  if (last <= 0) return []
  const start = Math.max(0, Math.min(last, fromT * last))
  const end = Math.max(0, Math.min(last, toT * last))
  if (end - start < 0.5) return []

  const lerp = (a: FanSample, b: FanSample, t: number): FanSample => ({
    lon: a.lon + (b.lon - a.lon) * t,
    lat: a.lat + (b.lat - a.lat) * t,
    halfWidthKm: a.halfWidthKm + (b.halfWidthKm - a.halfWidthKm) * t,
    bearing: smoothBearing(a.bearing, b.bearing, t),
    leftScale: (a.leftScale ?? 1) + ((b.leftScale ?? 1) - (a.leftScale ?? 1)) * t,
    rightScale: (a.rightScale ?? 1) + ((b.rightScale ?? 1) - (a.rightScale ?? 1)) * t
  })

  const at = (pos: number): FanSample => {
    const i = Math.floor(pos)
    const t = pos - i
    return i >= last ? samples[last] : lerp(samples[i], samples[i + 1], t)
  }

  const count = Math.max(2, Math.ceil(end - start))
  const out: FanSample[] = []
  for (let i = 0; i <= count; i += 1) out.push(at(start + ((end - start) * i) / count))
  return out
}

/** 相邻锚点之间的方位角插值，走最短弧，避免跨 0° 时反向旋转 */
function smoothBearing(from: number, to: number, t: number): number {
  const diff = ((to - from + 540) % 360) - 180
  return (from + diff * t + 360) % 360
}

// ── 烟羽形态 ─────────────────────────────────────────────────────────
// 真实污染扩散不是标准扇形：烟羽会随风向摆动蛇形前进，左右边缘
// 扩散速度也不一致。这里在数据中心线上叠加三层确定性扰动：
//   · 中心线横向摆动（meander）——蛇形的"游走"
//   · 左右边缘各自独立的宽度抖动——不对称的轮廓
// 扰动幅度随风向上的方位角逐档放大：风越乱，烟羽越散。
// 用不可通约频率的正弦叠加当噪声，同一份数据每次渲染形状一致。

/** 平滑伪随机噪声：几个不可通约频率的正弦叠加，输出约 [-1, 1] */
function plumeNoise(s: number, seed: number): number {
  return (
    0.55 * Math.sin(s * 9.42 + seed) +
    0.3 * Math.sin(s * 21.7 + seed * 1.7) +
    0.15 * Math.sin(s * 41.3 + seed * 2.3)
  )
}

/**
 * 把规整的中心线样本变成烟羽形态。
 *
 * @param samples  含站点起点的中心线采样序列
 * @param spreadDeg 锚点方位角的最短弧极差（0-180），衡量风向上的摆动幅度
 */
function applyPlumeShape(samples: FanSample[], spreadDeg: number): FanSample[] {
  const last = samples.length - 1
  if (last < 2) return samples

  // 风向越乱扰动越强：摆动占本地半宽的 10%~32%，边缘抖动 22%~40%
  const turbulence = Math.min(1, spreadDeg / 90)
  const meanderAmp = 0.1 + 0.22 * turbulence
  const jitterAmp = 0.22 + 0.18 * turbulence

  return samples.map((sample, i) => {
    const s = i / last
    // 站点附近（前 6% 行程）扰动渐入，保证原点固定、源头收窄
    const ramp = Math.min(1, s / 0.06)

    // 中心线沿垂直方向平移，制造蛇形
    const meander = meanderAmp * sample.halfWidthKm * plumeNoise(s, 17.7) * ramp
    const side =
      meander >= 0
        ? offsetPoint(sample.lon, sample.lat, meander, sample.bearing + 90)
        : offsetPoint(sample.lon, sample.lat, -meander, sample.bearing - 90)

    return {
      lon: side.lon,
      lat: side.lat,
      halfWidthKm: Math.max(sample.halfWidthKm, 0.03),
      bearing: sample.bearing,
      leftScale: 1 + jitterAmp * plumeNoise(s, 3.1) * ramp,
      rightScale: 1 + jitterAmp * plumeNoise(s, 43.9) * ramp
    }
  })
}

/** 锚点方位角的两两最短弧极差，量化风向上的摆动（0-180 度） */
function bearingSpreadDeg(bearings: number[]): number {
  let spread = 0
  for (let i = 0; i < bearings.length; i += 1) {
    for (let j = i + 1; j < bearings.length; j += 1) {
      const diff = Math.abs(((bearings[j] - bearings[i] + 540) % 360) - 180)
      spread = Math.max(spread, diff)
    }
  }
  return spread
}

/**
 * 沿给定方位角偏移指定距离（km），返回新的经纬度。
 *
 * 与后端 trajectory_tools.destination_point 同款球面公式，只做几何展开，
 * 不参与任何物理计算。
 */
function offsetPoint(
  lon: number,
  lat: number,
  distanceKm: number,
  bearingDeg: number
): { lon: number; lat: number } {
  const earthRadiusKm = 6371.0088
  const delta = distanceKm / earthRadiusKm
  const bearing = (bearingDeg * Math.PI) / 180
  const phi1 = (lat * Math.PI) / 180
  const lambda1 = (lon * Math.PI) / 180

  const phi2 = Math.asin(
    Math.sin(phi1) * Math.cos(delta) + Math.cos(phi1) * Math.sin(delta) * Math.cos(bearing)
  )
  const lambda2 =
    lambda1 +
    Math.atan2(
      Math.sin(bearing) * Math.sin(delta) * Math.cos(phi1),
      Math.cos(delta) - Math.sin(phi1) * Math.sin(phi2)
    )

  return { lon: (lambda2 * 180) / Math.PI, lat: (phi2 * 180) / Math.PI }
}

// ── 扇形动画：单条柔性光带沿输送方向缓慢外推 ────────────────────────
// 扇形本体（光晕 + 强度环）是静态的，动画只重建流动光带。
// 旧版让整片扇形"呼吸"（透明度大幅起伏），看起来像在闪烁，已去掉；
// 现在的"扩散感"来自光带本身：它从站点出发向外推移，沿途随扇面
// 展开变宽，出发时淡入、抵达远端后淡出，一个周期接一个周期。

/** 一个流动周期的毫秒数：光带从站点推到扇形远端再完全淡出 */
const FLOW_CYCLE_MS = 6500
/** 光带沿行程方向占的比例长度 */
const FLOW_BAND_SPAN = 0.5
/** 光带分段数：段与段透明度递增，拼出"后淡前亮"的渐变观感 */
const FLOW_SLICE_COUNT = 5
/** 各段基准透明度，沿推进方向递增（末段最亮，暗示输送方向） */
const FLOW_SLICE_ALPHAS = [0.05, 0.1, 0.17, 0.26, 0.36]
/** 帧间隔下限：约 30fps 已足够顺滑，降低重建几何的开销 */
const FLOW_FRAME_MIN_MS = 33
/** 相邻时间步之间插入的过渡采样点数，越大轮廓扰动曲线越平滑 */
const FAN_SAMPLES_PER_SEGMENT = 14
/** 内层强度环的半宽收缩系数：越小，径向浓淡对比越强 */
const FAN_CORE_SCALE = 0.52

let fanAnimationRaf: number | null = null
let fanAnimationStart = 0
let fanAnimationLastFrame = 0
/** 当前扇形中心线的采样序列（含站点起点），供逐帧生成光带 */
let fanSamples: FanSample[] = []

function startFanAnimation(): void {
  if (fanAnimationRaf !== null) return
  fanAnimationStart = performance.now()
  fanAnimationLastFrame = 0

  const tick = (now: number): void => {
    fanAnimationRaf = requestAnimationFrame(tick)

    if (now - fanAnimationLastFrame < FLOW_FRAME_MIN_MS) return
    fanAnimationLastFrame = now

    const flow = flowLayer.value
    if (!flow) {
      stopFanAnimation()
      return
    }
    if (fanSamples.length < 3) return

    const t = ((now - fanAnimationStart) % FLOW_CYCLE_MS) / FLOW_CYCLE_MS
    const head = t * (1 + FLOW_BAND_SPAN)
    const tail = head - FLOW_BAND_SPAN

    // 出发时淡入、抵远端后淡出，两端都走缓坡，避免光带凭空出现/消失
    const fadeIn = Math.min(1, head / 0.18)
    const fadeOut = Math.min(1, (1 - tail) / 0.22)
    const fade = fadeIn * fadeOut

    if (fade <= 0.01) {
      flow.setGeometries([])
      return
    }

    // 把 [tail, head] 行程区间切成相邻小段，各段用递增透明度的样式
    const geometries: Array<{ id: string; styleId: string; paths: ReturnType<typeof pathToLatLngs> }> = []
    for (let i = 0; i < FLOW_SLICE_COUNT; i += 1) {
      const from = tail + (FLOW_BAND_SPAN * i) / FLOW_SLICE_COUNT
      const to = tail + (FLOW_BAND_SPAN * (i + 1)) / FLOW_SLICE_COUNT
      const slice = sliceSamples(fanSamples, from, to)
      if (slice.length < 2) continue
      geometries.push({
        id: `forecast_flow_${i}`,
        styleId: `flow_${i}`,
        paths: pathToLatLngs(buildRing(slice))
      })
    }
    if (geometries.length === 0) {
      flow.setGeometries([])
      return
    }

    // 整体淡入淡出通过重设各段透明度实现，只有流动层参与，扇形不动
    const styles: Record<string, TMap.PolygonStyle> = {}
    FLOW_SLICE_ALPHAS.forEach((alpha, index) => {
      styles[`flow_${index}`] = new TMap.PolygonStyle({
        color: `rgba(58,140,240,${(alpha * fade).toFixed(3)})`,
        showBorder: false,
        borderWidth: 0
      })
    })
    flow.setStyles(styles)
    flow.setGeometries(geometries)
  }

  fanAnimationRaf = requestAnimationFrame(tick)
}

function stopFanAnimation(): void {
  if (fanAnimationRaf !== null) {
    cancelAnimationFrame(fanAnimationRaf)
    fanAnimationRaf = null
  }
  flowLayer.value?.setGeometries([])
}

/** 1/2/3 km 距离参考圈。用整圆 + 透明填充实现描边圆环，省掉多边形离散。 */
function updateDistanceRings(): void {
  const layer = ringLayer.value
  const station = props.spatial?.station
  if (!layer) return

  if (!station) {
    layer.setGeometries([])
    return
  }

  const center = pointToLatLng([station.lon, station.lat])
  layer.setGeometries(
    PROPAGATION_VISUALIZATION_CONFIG.DISTANCE_RINGS_KM.map(radiusKm => ({
      id: `ring_${radiusKm}`,
      styleId: `ring_${radiusKm}`,
      center,
      radius: radiusKm * 1000
    }))
  )
}

/** 潜在传播风险扇区（由下风向 + 半角生成的多边形） */
function updatePropagationSector(): void {
  const layer = sectorLayer.value
  if (!layer) return

  const station = props.spatial?.station
  const downwind = props.meteorology?.downwind_direction
  if (!station || downwind === null || downwind === undefined) {
    layer.setGeometries([])
    return
  }

  const sector = createPropagationSector(
    [station.lon, station.lat],
    downwind,
    PROPAGATION_VISUALIZATION_CONFIG.MAX_RADIUS_KM,
    PROPAGATION_VISUALIZATION_CONFIG.SECTOR_HALF_ANGLE_DEG
  )

  layer.setGeometries([
    { id: 'sector', styleId: 'sector', paths: pathToLatLngs(sector.geometry.coordinates[0]) }
  ])
}

/** 潜在影响区域 */
function updateAffectedArea(): void {
  const layer = affectedLayer.value
  const geometry = props.spatial?.affected_area_geojson
  if (!layer) return

  const ring = geometry?.coordinates?.[0]
  if (!ring || ring.length < 3) {
    layer.setGeometries([])
    return
  }

  layer.setGeometries([
    { id: 'affected', styleId: 'affected', paths: pathToLatLngs(ring) }
  ])
}

/** 上风向候选道路，线宽按空间匹配评分分档 */
function updateUpwindRoads(): void {
  const layer = roadLayer.value
  if (!layer) return

  const roads = props.spatial?.upwind_roads
  if (!roads || roads.length === 0) {
    layer.setGeometries([])
    return
  }

  layer.setGeometries(
    roads
      .filter(road => road.geometry?.coordinates?.length >= 2)
      .map(road => ({
        id: road.road_id,
        styleId: roadWidthStyleId(road.match_score),
        paths: pathToLatLngs(road.geometry.coordinates),
        properties: {
          road_id: road.road_id,
          road_name: road.road_name,
          road_name_zh: road.road_name_zh,
          match_score: road.match_score,
          distance_to_station_km: road.distance_to_station_km
        }
      }))
  )
}

/** 匹配评分 → 线宽档位。原实现是连续插值，这里改为四档。 */
function roadWidthStyleId(score: number): string {
  if (score >= 0.98) return 'road_high'
  if (score >= 0.95) return 'road_mid'
  if (score >= 0.9) return 'road_low'
  return 'road_min'
}

/** 敏感目标，颜色按类型区分 */
function updateSensitivePOIs(): void {
  const layer = poiLayer.value
  if (!layer) return

  const pois = props.spatial?.sensitive_pois
  if (!pois || pois.length === 0) {
    layer.setGeometries([])
    return
  }

  layer.setGeometries(
    pois
      .filter(poi => Number.isFinite(poi.lon) && Number.isFinite(poi.lat))
      .map(poi => ({
        id: poi.poi_id,
        styleId: `poi_${POI_TYPE_COLORS[poi.poi_type] ? poi.poi_type : 'unknown'}`,
        center: pointToLatLng([poi.lon, poi.lat]),
        radius: POI_RADIUS_M,
        properties: {
          poi_id: poi.poi_id,
          poi_type: poi.poi_type,
          name: poi.name,
          name_zh: poi.name_zh,
          distance_to_station_km: poi.distance_to_station_km
        }
      }))
  )
}

function updateStationMarker(): void {
  const layer = stationLayer.value
  const station = props.spatial?.station
  if (!layer) return

  if (!station) {
    layer.setGeometries([])
    return
  }

  layer.setGeometries([
    {
      id: 'station',
      styleId: 'station',
      position: pointToLatLng([station.lon, station.lat]),
      properties: { station_id: station.station_id, station_name: station.station_name }
    }
  ])
}

/** 风向箭头。角度改写进图标本身，因此每次数据变化都要重建样式。 */
function updateWindDirectionArrow(): void {
  const layer = windArrowLayer.value
  const station = props.spatial?.station
  const downwind = props.meteorology?.downwind_direction
  if (!layer) return

  if (!station || downwind === null || downwind === undefined) {
    layer.setGeometries([])
    return
  }

  layer.setStyles({
    arrow: new TMap.MarkerStyle({ width: 56, height: 56, src: windArrowIcon(downwind) })
  })
  layer.setGeometries([
    {
      id: 'wind-arrow',
      styleId: 'arrow',
      position: pointToLatLng([station.lon, station.lat]),
      properties: {
        downwind_direction: downwind,
        wind_label: props.meteorology?.dominant_wind_direction_label ?? ''
      }
    }
  ])
}

/** 自动调整视野：优先贴合影响区域，否则回到站点。 */
function fitMapBounds(): void {
  const currentMap = map.value
  if (!currentMap || !props.spatial) return

  const ring = props.spatial.affected_area_geojson?.coordinates?.[0]
  if (ring && ring.length >= 3) {
    const points = ring
      .filter(coordinate => coordinate.length >= 2)
      .map(coordinate => toGcj(Number(coordinate[0]), Number(coordinate[1])))

    if (points.length) {
      let minLat = points[0].lat
      let maxLat = points[0].lat
      let minLng = points[0].lng
      let maxLng = points[0].lng
      points.forEach(point => {
        minLat = Math.min(minLat, point.lat)
        maxLat = Math.max(maxLat, point.lat)
        minLng = Math.min(minLng, point.lng)
        maxLng = Math.max(maxLng, point.lng)
      })

      currentMap.fitBounds(
        new TMap.LatLngBounds(new TMap.LatLng(minLat, minLng), new TMap.LatLng(maxLat, maxLng)),
        { padding: 50, maxZoom: 14, ease: { duration: 800 } }
      )
      return
    }
  }

  const station = props.spatial.station
  if (station) {
    currentMap.easeTo(
      { center: pointToLatLng([station.lon, station.lat]), zoom: 14 },
      { duration: 800 }
    )
  }
}

// ── 生命周期 ──────────────────────────────────────────────────────────────

watch(
  () => [props.spatial, props.meteorology],
  () => {
    if (layersInitialized.value) updateSpatialLayers()
  },
  { deep: true }
)

onMounted(() => {
  void initMap()
})

onBeforeUnmount(() => {
  // 动画循环必须先停，否则组件销毁后仍会持有图层引用
  stopFanAnimation()

  infoWindow.value?.destroy()
  infoWindow.value = null

  ringLayer.value?.setMap(null)
  sectorLayer.value?.setMap(null)
  affectedLayer.value?.setMap(null)
  roadLayer.value?.setMap(null)
  poiLayer.value?.setMap(null)
  stationLayer.value?.setMap(null)
  windArrowLayer.value?.setMap(null)
  corridorLayer.value?.setMap(null)
  fanInnerLayer.value?.setMap(null)
  flowLayer.value?.setMap(null)
  ringLayer.value = null
  sectorLayer.value = null
  affectedLayer.value = null
  roadLayer.value = null
  poiLayer.value = null
  stationLayer.value = null
  windArrowLayer.value = null
  corridorLayer.value = null
  fanInnerLayer.value = null
  flowLayer.value = null
  fanSamples = []

  map.value?.destroy()
  map.value = null
  layersInitialized.value = false
})
</script>

<template>
  <div class="spatial-map-wrapper">
    <!-- 地图容器 -->
    <div ref="mapContainer" class="map-container"></div>

    <!-- 未配置密钥 -->
    <div v-if="needsKey" class="map-overlay key-overlay">
      <el-icon :size="30">
        <WarnTriangleFilled />
      </el-icon>
      <span class="key-title">尚未配置腾讯地图密钥</span>
      <span class="key-detail">{{ MAP_KEY_MISSING_MESSAGE }}</span>
    </div>

    <!-- 图例 -->
    <div v-else-if="!isMapLoading && spatial" class="map-legend">
      <div class="legend-title">图例</div>
      <div class="legend-items">
        <div class="legend-item">
          <span class="legend-icon station-icon"></span>
          <span class="legend-label">监测站</span>
        </div>
        <div class="legend-item">
          <span class="legend-icon ring-icon"></span>
          <span class="legend-label">1/2/3 km 距离参考圈</span>
        </div>
        <div v-if="meteorology?.downwind_direction !== null" class="legend-item">
          <span class="legend-icon arrow-icon">→</span>
          <span class="legend-label">潜在传播方向</span>
        </div>
        <div class="legend-separator"></div>
        <div class="legend-item">
          <span class="legend-icon road-icon"></span>
          <span class="legend-label">上风向候选道路</span>
        </div>
        <div class="legend-item">
          <span class="legend-icon area-icon"></span>
          <span class="legend-label">潜在影响区域</span>
        </div>
        <div v-if="meteorology?.downwind_direction !== null" class="legend-item">
          <span class="legend-icon sector-icon"></span>
          <span class="legend-label">潜在传播风险扇区</span>
        </div>
        <template v-if="showForecastGroup">
          <div class="legend-separator"></div>
          <div class="legend-subtitle">短时预测</div>
          <div class="legend-item">
            <span class="legend-icon forecast-fan-icon"></span>
            <span class="legend-label">预测扩散范围（公里级）</span>
          </div>
          <div class="legend-item">
            <span class="legend-icon forecast-flow-icon"></span>
            <span class="legend-label">流动方向（由站点向外扩散）</span>
          </div>
        </template>
        <div class="legend-separator"></div>
        <div class="legend-subtitle">敏感目标</div>
        <div class="legend-item">
          <span class="legend-icon poi-icon" style="background-color: #9c27b0;"></span>
          <span class="legend-label">学校</span>
        </div>
        <div class="legend-item">
          <span class="legend-icon poi-icon" style="background-color: #f44336;"></span>
          <span class="legend-label">医疗</span>
        </div>
        <div class="legend-item">
          <span class="legend-icon poi-icon" style="background-color: #4caf50;"></span>
          <span class="legend-label">轨道交通</span>
        </div>
        <div class="legend-item">
          <span class="legend-icon poi-icon" style="background-color: #ff9800;"></span>
          <span class="legend-label">社区服务</span>
        </div>
        <div v-if="sectorPOIStats" class="legend-separator"></div>
        <div v-if="sectorPOIStats" class="legend-subtitle">扇区内敏感目标</div>
        <div v-if="sectorPOIStats" class="legend-item">
          <span class="legend-label sector-count">共 {{ sectorPOIStats.total }} 个</span>
        </div>
      </div>
    </div>

    <!-- 加载状态 -->
    <div v-if="isMapLoading && !needsKey" class="map-overlay loading-overlay">
      <el-icon class="is-loading" :size="32">
        <Loading />
      </el-icon>
      <span>地图加载中...</span>
    </div>

    <!-- 错误状态 -->
    <div v-if="mapError" class="map-overlay error-overlay">
      <el-icon :size="32">
        <WarnTriangleFilled />
      </el-icon>
      <span>{{ mapError }}</span>
    </div>

    <!-- 空数据状态 -->
    <div v-if="!spatial && !isMapLoading && !mapError && !needsKey" class="map-overlay empty-overlay">
      <el-icon :size="32">
        <Location />
      </el-icon>
      <span>暂无空间分析数据</span>
    </div>
  </div>
</template>

<style scoped>
.spatial-map-wrapper {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 420px;
  background-color: #f5f7fa;
  border-radius: 4px;
  overflow: hidden;
}

.map-container {
  width: 100%;
  height: 100%;
  min-height: 420px;
}

.map-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background-color: rgba(255, 255, 255, 0.95);
  color: #606266;
  font-size: 14px;
  z-index: 10;
}

.loading-overlay {
  background-color: rgba(255, 255, 255, 0.9);
}

.error-overlay {
  color: #f56c6c;
}

.empty-overlay {
  color: #909399;
}

/* 未配置密钥：明确说清楚，不留一块看不懂的空白 */
.key-overlay {
  color: #e6a23c;
  padding: 0 40px;
  text-align: center;
}

.key-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}

.key-detail {
  max-width: 520px;
  font-size: 12px;
  line-height: 20px;
  color: #909399;
}

/* 图例 */
.map-legend {
  position: absolute;
  bottom: 30px;
  left: 10px;
  background: rgba(255, 255, 255, 0.95);
  border-radius: 6px;
  padding: 10px 12px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  font-size: 12px;
  z-index: 5;
  max-width: 160px;
}

.legend-title {
  font-weight: 600;
  font-size: 13px;
  color: #333;
  margin-bottom: 8px;
  padding-bottom: 6px;
  border-bottom: 1px solid #e0e0e0;
}

.legend-subtitle {
  font-weight: 600;
  font-size: 11px;
  color: #666;
  margin: 4px 0 4px 0;
}

.legend-items {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.legend-icon {
  flex-shrink: 0;
  display: inline-block;
}

.legend-label {
  color: #666;
  line-height: 1.2;
  font-size: 12px;
}

.legend-separator {
  height: 1px;
  background: #e0e0e0;
  margin: 4px 0;
}

.station-icon {
  width: 12px;
  height: 12px;
  background-color: #ff4444;
  border: 2px solid #fff;
  border-radius: 50%;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
}

.road-icon {
  width: 20px;
  height: 3px;
  background-color: #1976d2;
  border-radius: 1.5px;
}

.area-icon {
  width: 14px;
  height: 14px;
  background-color: rgba(255, 152, 0, 0.3);
  border: 1.5px solid #ff9800;
  border-radius: 2px;
}

.sector-icon {
  width: 14px;
  height: 14px;
  background-color: rgba(3, 169, 244, 0.2);
  border: 1.5px solid #03a9f4;
  border-radius: 2px;
}

.forecast-fan-icon {
  width: 0;
  height: 0;
  border-left: 7px solid transparent;
  border-right: 7px solid transparent;
  border-bottom: 14px solid rgba(55, 138, 221, 0.5);
  margin-top: 2px;
}

/* 流动光带：用一深一浅两段叠加，示意"由站点向外推移" */
.forecast-flow-icon {
  width: 16px;
  height: 14px;
  border-radius: 2px;
  background: linear-gradient(
    to bottom,
    rgba(55, 138, 221, 0.2),
    rgba(55, 138, 221, 0.9),
    rgba(55, 138, 221, 0.2)
  );
}

.poi-icon {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 1px solid #fff;
}

.ring-icon {
  width: 18px;
  height: 18px;
  border: 1.5px dashed #9e9e9e;
  border-radius: 50%;
  background: transparent;
}

.arrow-icon {
  width: 16px;
  height: 16px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #ff5722;
  font-size: 16px;
  font-weight: bold;
  line-height: 1;
}

.sector-count {
  font-weight: 600;
  color: #03a9f4;
}
</style>

<style>
/* 信息窗内容由腾讯地图渲染到地图容器内，作用域样式无法命中 */
.spatial-popup {
  padding: 4px 6px;
  min-width: 180px;
  font-size: 13px;
  line-height: 1.6;
  color: #2c2c2a;
}

.spatial-popup-title {
  font-weight: 600;
  font-size: 14px;
  margin-bottom: 6px;
  color: #666;
}
</style>
