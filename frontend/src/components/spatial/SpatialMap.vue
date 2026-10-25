<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { Loading, Location, WarnTriangleFilled } from '@element-plus/icons-vue'
import type { SpatialResult } from '@/types/spatial'
import type { MeteorologyResult } from '@/types/meteorology'
import { createDistanceCircle, PROPAGATION_VISUALIZATION_CONFIG } from '@/utils/spatialGeometry'
import { pathToLatLngs, pointToLatLng, toGcj } from '@/utils/coordTransform'
import { initialPropagationDirection } from '@/utils/propagationDirection'
import { createPropagationFlow } from '@/utils/propagationFlow'
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
  /** 绘制共享的模型传播范围、流动烟羽与沿途站点。 */
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

const mapContainer = shallowRef<HTMLDivElement | null>(null)
const mapLegend = shallowRef<HTMLDivElement | null>(null)
let viewportObserver: ResizeObserver | null = null
let fitFrame = 0
const flowCanvas = shallowRef<HTMLCanvasElement | null>(null)
const directionCanvas = shallowRef<HTMLCanvasElement | null>(null)
let directionFrame = 0
let propagationFlow: ReturnType<typeof createPropagationFlow> | null = null
// 类实例一律用 shallowRef：用 ref 会让 Vue 对类型做深度展开，vue-tsc 报 TS2589。
const map = shallowRef<TMap.Map | null>(null)
const ringLayer = shallowRef<TMap.MultiCircleLayer | null>(null)
const sectorLayer = shallowRef<TMap.MultiPolygonLayer | null>(null)
const affectedLayer = shallowRef<TMap.MultiPolygonLayer | null>(null)
const poiLayer = shallowRef<TMap.MultiCircleLayer | null>(null)
const stationLayer = shallowRef<TMap.MultiMarkerLayer | null>(null)
// 后端传播范围、中心路径及沿途独立监测站。
const corridorLayer = shallowRef<TMap.MultiPolygonLayer | null>(null)
const forecastPathLayer = shallowRef<TMap.MultiPolylineLayer | null>(null)
const encounterLayer = shallowRef<TMap.MultiMarkerLayer | null>(null)
// 沿路径的逐小时距离标签（+Nh x.xkm），只做读图锚点，不参与任何计算。
const stepBadgeLayer = shallowRef<TMap.MultiMarkerLayer | null>(null)
const infoWindow = shallowRef<TMap.InfoWindow | null>(null)

const isMapLoading = ref(true)
const mapError = ref<string | null>(null)
const needsKey = ref(false)
const layersInitialized = ref(false)

/** Counts follow the rotated static sector, rather than the old mean-wind sector. */
const sectorPOIStats = computed(() => {
  const scope = props.spatial?.directional_exposure
  return scope?.status === 'ok' ? { total: scope.sensitive_poi_count } : null
})

/** 预测路径是否可绘（决定图例里是否出现"短时预测"分组） */
const hasForecastPath = computed(
  () =>
    props.meteorology?.pollution_path_prediction?.status === 'ok' &&
    (props.meteorology?.pollution_path_prediction?.steps?.length ?? 0) > 0
)

/** 短时预测分组：既要有数据，也要当前页面开启了预测图层 */
const showForecastGroup = computed(() => props.showForecast === true && hasForecastPath.value)
const flowPollutants = computed(() => Object.entries(
  props.meteorology?.pollution_path_prediction?.lifetime_model?.pollutants ?? {}
).filter(([, value]) => Number.isFinite(value.initial_concentration) && value.initial_concentration > 0)
  .map(([name]) => name))
const activeFlowPollutant = computed(() => flowPollutants.value.includes('no2') ? 'no2' : flowPollutants.value[0] ?? '')
// All maps follow the same integrated trajectory; never substitute a window average.
const mapWindDirection = computed(() => initialPropagationDirection(props.meteorology?.pollution_path_prediction))
const mapWindLabel = computed(() => props.showForecast ? '模拟路径起始方向' : '传播方向（与预警页一致）')

// ── 图标：一律用内联 SVG data URL，不引用任何示例站点图片 ────────────────

function svgUrl(svg: string): string {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
}

/**
 * 传播方向箭头不用 MarkerStyle 画：图标锚点（默认居中 / 是否接受普通对象）
 * 无法保证箭尾落在站点坐标上，实测整根箭头会被推离站点。这里在覆盖层 canvas
 * 上按 map.projectToContainer 的真实投影像素自绘——箭尾就是站点投影点，
 * 方向由 downwind 方位角决定，粗细也只由这几个数决定。
 */
const DIRECTION_ARROW = {
  /** 从箭尾到箭尖的总长（屏幕像素） */
  length: 32,
  /** 箭杆线宽：细到不抢底图路网，又要能在深色水系上看清 */
  shaftWidth: 2,
  headLength: 10,
  headHalfWidth: 5,
  /** 站点圆点半径 8：箭尾从圆边起笔，不再压住圆点中心 */
  originGap: 8
}

function stationIcon(): string {
  return svgUrl(
    `<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22">` +
      `<circle cx="11" cy="11" r="8" fill="#ff4444" stroke="#ffffff" stroke-width="2"/></svg>`
  )
}

/** 逐小时距离标签：圆角胶囊 + 白字。宽高必须与 MarkerStyle 一致，否则文字被裁。 */
function stepBadgeIcon(text: string, fill: string, width: number): string {
  const height = 22
  return svgUrl(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">` +
      `<rect x="0.75" y="0.75" width="${width - 1.5}" height="${height - 1.5}" rx="4" ` +
      `fill="${fill}" stroke="#ffffff" stroke-width="1.2"/>` +
      `<text x="${width / 2}" y="15" text-anchor="middle" fill="#ffffff" font-size="11" ` +
      `font-family="sans-serif" font-weight="600">${text}</text></svg>`
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
    if (flowCanvas.value) propagationFlow = createPropagationFlow(flowCanvas.value, newMap)
    // 箭头是画在屏幕像素上的，地图每次平移/缩放都要按新投影重画。
    newMap.on('bounds_changed', scheduleDirectionArrow)

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

  // 图层顺序按 zIndex 从底到顶：距离圈 → 传播扇区 → 影响区域 → POI → 站点
  // （传播方向箭头不在图层里，画在覆盖层 canvas 上）
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

  // 传播方向箭头不在这里建图层：它画在 .spatial-direction-canvas 上（见 drawDirectionArrow）。

  corridorLayer.value = new TMap.MultiPolygon({
    id: 'spatial-forecast-sectors', map: currentMap, zIndex: 3,
    styles: { fan: new TMap.PolygonStyle({
      color: 'rgba(59,130,246,0)', showBorder: false
    }), boundary: new TMap.PolygonStyle({
      color: 'rgba(230,170,20,0.015)', showBorder: true,
      borderColor: 'rgba(176,119,13,0.55)', borderWidth: 1
    }) }, geometries: []
  })
  forecastPathLayer.value = new TMap.MultiPolyline({
    id: 'spatial-forecast-centerline', map: currentMap, zIndex: 6,
    styles: { path: new TMap.PolylineStyle({ color: 'rgba(176,119,13,0.28)', width: 1.5, showArrow: false }) },
    geometries: []
  })
  encounterLayer.value = new TMap.MultiMarker({
    id: 'spatial-receiving-stations', map: currentMap, zIndex: 9,
    styles: { station: new TMap.MarkerStyle({ width: 22, height: 22, src: stationIcon() }) }, geometries: []
  })
  encounterLayer.value.on('click', (event: TMap.GeometryOverlayEvent) => {
    const index = Number(event.geometry.properties?.encounter_index)
    const station = props.meteorology?.pollution_path_prediction?.station_validation?.stations[index]
    if (!station) return
    const comparisons = station.comparisons.map(item => {
      const label = POLLUTANT_LABELS[item.pollutant] || item.pollutant
      return `<div>${escapeHtml(label)}：到达前基线 ${item.baseline_concentration ?? '—'} → ` +
        `到达观测 ${item.arrival_observed_concentration ?? '—'} ${escapeHtml(item.unit)}；${escapeHtml(item.label)}</div>`
    }).join('')
    showInfo(event.latLng,
      `<div class="spatial-popup-title">${escapeHtml(station.station_name_zh)}</div>` +
      `<div>${escapeHtml(station.relation_label)} · 距中心路径 ${station.distance_to_path_km.toFixed(2)} km</div>` +
      `<div>模拟到达：${escapeHtml(station.predicted_arrival_time)}</div>` +
      `<div>观测对照：${escapeHtml(station.label)}</div>${comparisons}` +
      '<div>经过站点和观测一致仅作独立对照，不能单独证明预测正确。</div>')
  })

  // 逐小时距离标签：与站点标记同一套"SVG 写进图标"的做法，样式每次重建。
  stepBadgeLayer.value = new TMap.MultiMarker({
    id: 'spatial-step-badges', map: currentMap, zIndex: 9,
    styles: { badge: new TMap.MarkerStyle({ width: 104, height: 22, src: stepBadgeIcon('+1h 0.0km', '#1668dc', 104) }) },
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
  updateSensitivePOIs()
  updateStationMarker()
  scheduleDirectionArrow()
  updateForecastLayers()
  updateStepBadges()
  scheduleFitMapBounds()
}

/** 动态烟羽裁剪在后端扇区内，动画不参与站点覆盖或观测对照。 */
function updateForecastLayers(): void {
  const prediction = props.meteorology?.pollution_path_prediction
  const geometry = prediction?.propagation_sector_geojson
  if (!props.showForecast || prediction?.status !== 'ok' || !geometry) {
    propagationFlow?.update(null)
    corridorLayer.value?.setGeometries([])
    forecastPathLayer.value?.setGeometries([])
    encounterLayer.value?.setGeometries([])
    return
  }
  propagationFlow?.update(prediction, activeFlowPollutant.value)
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates
  corridorLayer.value?.setGeometries(polygons.map((polygon, index) => ({
    id: `forecast-sector-${index}`, styleId: 'fan',
    paths: polygon.map(ring => pathToLatLngs(ring))
  })))
  const path = prediction.path_geojson?.coordinates ?? []
  forecastPathLayer.value?.setGeometries(path.length > 1 ? [{
    id: 'forecast-centerline', styleId: 'path', paths: pathToLatLngs(path)
  }] : [])
  const stations = prediction.station_validation?.stations ?? []
  if (!stations.length) {
    encounterLayer.value?.setGeometries([])
    return
  }
  const styles: Record<string, TMap.MarkerStyle> = {}
  stations.forEach((station, index) => {
    const color = ENCOUNTER_COLORS[station.status] || '#64748b'
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="28">` +
      `<path d="M12 26L4.5 15.5a9 9 0 1 1 15 0Z" fill="${color}" stroke="white" stroke-width="1.5"/>` +
      `<text x="12" y="14.5" text-anchor="middle" fill="white" font-size="10" font-family="sans-serif">${index + 1}</text></svg>`
    styles[`station_${index}`] = new TMap.MarkerStyle({ width: 24, height: 28, anchor: { x: 12, y: 26 }, src: svgUrl(svg) })
  })
  encounterLayer.value?.setStyles(styles)
  encounterLayer.value?.setGeometries(stations.map((station, index) => ({
    id: `receiving-${station.station_id}-${index}`, styleId: `station_${index}`,
    position: pointToLatLng([station.lon, station.lat]), properties: { encounter_index: index }
  })))
}

/** 沿途逐小时距离标签：只读锚点，不参与覆盖判定或传播计算。 */
function updateStepBadges(): void {
  const layer = stepBadgeLayer.value
  if (!layer) return

  const prediction = props.meteorology?.pollution_path_prediction
  const steps = props.showForecast && prediction?.status === 'ok' ? prediction.steps ?? [] : []
  if (!steps.length) {
    layer.setGeometries([])
    return
  }

  const styles: Record<string, TMap.MarkerStyle> = {}
  const geometries = steps.map((step, index) => {
    const isLast = index === steps.length - 1
    const ratio = steps.length > 1 ? index / (steps.length - 1) : 0
    // 由蓝到红示意路径推进方向；末端用深红并标注"终点"。
    const fill = ratio < 0.34 ? '#1668dc' : ratio < 0.67 ? '#c2410c' : '#b42318'
    const distance = Number(step.distance_from_origin_km)
    const text = `+${step.lead_hour}h ${Number.isFinite(distance) ? distance.toFixed(1) : '—'}km${isLast ? ' · 终点' : ''}`
    const width = isLast ? 150 : 104
    const styleId = `badge_${index}`
    styles[styleId] = new TMap.MarkerStyle({
      width, height: 22, anchor: { x: width / 2, y: 22 }, src: stepBadgeIcon(text, fill, width)
    })
    return { id: `step-badge-${index}`, styleId, position: pointToLatLng([step.lon, step.lat]) }
  })
  layer.setStyles(styles)
  layer.setGeometries(geometries)
}

const POLLUTANT_LABELS: Record<string, string> = {
  pm25: 'PM2.5', pm10: 'PM10', no2: '二氧化氮', o3: '臭氧', so2: '二氧化硫', co: '一氧化碳'
}
const ENCOUNTER_COLORS: Record<string, string> = {
  consistent: '#16a34a', not_supported: '#dc2626', mixed: '#f59e0b', pre_existing: '#d97706',
  coverage_only: '#2563eb', not_testable: '#64748b', unavailable: '#94a3b8'
}
function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character] || character))
}

/** 距离参考圈保留用于读图，不代表预测终点。 */
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

/** Original static sector, rotated to the shared path direction by the backend. */
function updatePropagationSector(): void {
  const geometry = props.spatial?.directional_exposure?.geometry
  const ring = geometry?.coordinates[0]
  sectorLayer.value?.setGeometries(!props.showForecast && ring?.length ? [
    { id: 'sector', styleId: 'sector', paths: pathToLatLngs(ring) }
  ] : [])
}

function updateAffectedArea(): void {
  const geometry = props.spatial?.directional_exposure?.geometry
  const ring = geometry?.coordinates[0]
  affectedLayer.value?.setGeometries(!props.showForecast && ring?.length ? [
    { id: 'affected', styleId: 'affected', paths: pathToLatLngs(ring) }
  ] : [])
}

/** 敏感目标，颜色按类型区分 */
function updateSensitivePOIs(): void {
  const layer = poiLayer.value
  if (!layer) return

  const scope = props.showForecast ? props.spatial?.propagation_exposure : props.spatial?.directional_exposure
  const pois = scope?.sensitive_pois
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

/**
 * 传播方向箭头：从站点圆边起笔、沿下风向伸出。
 *
 * 画在覆盖层 canvas 而不是 marker 上，是因为 marker 的锚点语义（默认居中、
 * 普通对象 anchor 是否生效）无法保证箭尾落在站点坐标上；而 canvas 里箭尾
 * 就是站点投影点本身。角度用方位角（0=北=屏幕上方，顺时针），与
 * initialPropagationDirection 的返回值同一套约定。
 */
function drawDirectionArrow(): void {
  const canvas = directionCanvas.value
  const currentMap = map.value
  if (!canvas) return

  const ratio = Math.min(window.devicePixelRatio || 1, 2)
  const width = canvas.clientWidth
  const height = canvas.clientHeight
  if (!width || !height) return
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio)
    canvas.height = Math.round(height * ratio)
  }
  const context = canvas.getContext('2d')
  if (!context) return
  context.setTransform(ratio, 0, 0, ratio, 0, 0)
  context.clearRect(0, 0, width, height)

  const station = props.spatial?.station
  const downwind = mapWindDirection.value
  if (!currentMap || !station || downwind === null || downwind === undefined) return

  const point = currentMap.projectToContainer(pointToLatLng([station.lon, station.lat]))
  const x = point.getX()
  const y = point.getY()
  if (!Number.isFinite(x) || !Number.isFinite(y)) return

  const { length, shaftWidth, headLength, headHalfWidth, originGap } = DIRECTION_ARROW
  context.save()
  // 原点即站点：先平移到这里再只做旋转，箭尾永远压在站点上。
  context.translate(x, y)
  context.rotate(downwind * Math.PI / 180)

  context.lineCap = 'round'
  context.beginPath()
  context.moveTo(0, -originGap)
  context.lineTo(0, -length + headLength)
  // 先白后橙：底图有深色水系和路网，纯橙细线容易糊在底图里。
  context.lineWidth = shaftWidth + 2
  context.strokeStyle = 'rgba(255,255,255,0.9)'
  context.stroke()
  context.lineWidth = shaftWidth
  context.strokeStyle = '#ff5722'
  context.stroke()

  const tipY = -length
  context.beginPath()
  context.moveTo(0, tipY)
  context.lineTo(headHalfWidth, tipY + headLength)
  context.lineTo(-headHalfWidth, tipY + headLength)
  context.closePath()
  context.fillStyle = '#ff5722'
  context.strokeStyle = 'rgba(255,255,255,0.9)'
  context.lineWidth = 1.5
  context.stroke()
  context.fill()
  context.restore()
}

function scheduleDirectionArrow(): void {
  if (directionFrame) return
  directionFrame = requestAnimationFrame(() => {
    directionFrame = 0
    drawDirectionArrow()
  })
}

/** Fit after Vue has laid out the legend; keep markers in the unobstructed area. */
function scheduleFitMapBounds(): void {
  cancelAnimationFrame(fitFrame)
  fitFrame = requestAnimationFrame(() => {
    fitFrame = 0
    fitMapBounds()
  })
}

/**
 * 视野范围：预警页取"起点 → 终点"整条传播链（中心路径折点 + 不确定走廊），
 * 其余页面仍按静态扇区 / 参考圈。
 * 只框 3 km 参考圈会把远端终点甩出画面，所以有轨迹时不再叠参考圈。
 */
function fitBoundsCoordinates(station: { lon: number; lat: number }): number[][] {
  const coordinates: number[][] = [[station.lon, station.lat]]
  const prediction = props.meteorology?.pollution_path_prediction

  if (props.showForecast && prediction?.status === 'ok') {
    for (const step of prediction.steps ?? []) coordinates.push([step.lon, step.lat])
    for (const point of prediction.path_geojson?.coordinates ?? []) coordinates.push(point.slice(0, 2))
    for (const point of prediction.uncertainty_corridor_geojson?.coordinates?.[0] ?? []) {
      coordinates.push(point.slice(0, 2))
    }
    if (coordinates.length > 2) return coordinates
  }

  const ring = props.showForecast
    ? createDistanceCircle([station.lon, station.lat], PROPAGATION_VISUALIZATION_CONFIG.MAX_RADIUS_KM).geometry.coordinates[0]
    : props.spatial?.directional_exposure?.geometry?.coordinates[0] ?? []
  return [...coordinates, ...ring]
}

function fitMapBounds(): void {
  const currentMap = map.value
  const container = mapContainer.value
  const station = props.spatial?.station
  if (!currentMap || !container || !station || !container.clientWidth || !container.clientHeight) return

  const coordinates = fitBoundsCoordinates(station)
  const points = coordinates.filter(point => point.length >= 2 && point.slice(0, 2).every(Number.isFinite))
    .map(point => toGcj(point[0], point[1]))
  if (!points.length) return

  const minLat = Math.min(...points.map(point => point.lat))
  const maxLat = Math.max(...points.map(point => point.lat))
  const minLng = Math.min(...points.map(point => point.lng))
  const maxLng = Math.max(...points.map(point => point.lng))
  const containerRect = container.getBoundingClientRect()
  const legendRect = mapLegend.value?.getBoundingClientRect()
  // 图例贴在左下角，左侧整条宽度都要让出来，否则起点会被压在下面；
  // 窄屏时最多占 45%，保证右侧还有可视区。
  const legendWidth = legendRect && legendRect.width
    ? legendRect.right - containerRect.left + 26
    : 0
  const left = Math.max(50, Math.min(Math.round(legendWidth), Math.round(containerRect.width * 0.45)))
  // 顶部避开气象浮层，底部避开图例的圆角与页码式留白。
  const top = 56
  const bottom = 48
  currentMap.fitBounds(
    new TMap.LatLngBounds(
      new TMap.LatLng(minLat - .00001, minLng - .00001),
      new TMap.LatLng(maxLat + .00001, maxLng + .00001)
    ),
    { padding: { top, bottom, left, right: 40 }, maxZoom: 14, ease: { duration: 500 } }
  )
}

// ── 生命周期 ──────────────────────────────────────────────────────────────

watch(
  () => [props.spatial, props.meteorology, props.showForecast],
  () => {
    if (layersInitialized.value) updateSpatialLayers()
  },
  { deep: true }
)

watch(activeFlowPollutant, () => {
  if (layersInitialized.value) updateForecastLayers()
})

watch(mapLegend, (legend, previous) => {
  if (previous) viewportObserver?.unobserve(previous)
  if (legend) viewportObserver?.observe(legend)
  scheduleFitMapBounds()
}, { flush: 'post' })

onMounted(() => {
  viewportObserver = new ResizeObserver(scheduleFitMapBounds)
  if (mapContainer.value) viewportObserver.observe(mapContainer.value)
  void initMap()
})

onBeforeUnmount(() => {
  cancelAnimationFrame(fitFrame)
  cancelAnimationFrame(directionFrame)
  directionFrame = 0
  map.value?.off('bounds_changed', scheduleDirectionArrow)
  viewportObserver?.disconnect()
  viewportObserver = null
  propagationFlow?.destroy()
  propagationFlow = null
  infoWindow.value?.destroy()
  infoWindow.value = null

  ringLayer.value?.setMap(null)
  sectorLayer.value?.setMap(null)
  affectedLayer.value?.setMap(null)
  poiLayer.value?.setMap(null)
  stationLayer.value?.setMap(null)
  corridorLayer.value?.setMap(null)
  forecastPathLayer.value?.setMap(null)
  encounterLayer.value?.setMap(null)
  stepBadgeLayer.value?.setMap(null)
  ringLayer.value = null
  sectorLayer.value = null
  affectedLayer.value = null
  poiLayer.value = null
  stationLayer.value = null
  corridorLayer.value = null
  forecastPathLayer.value = null
  encounterLayer.value = null
  stepBadgeLayer.value = null

  map.value?.destroy()
  map.value = null
  layersInitialized.value = false
})

// Keep Tencent map state alive while the Overview chart is shown. This narrow
// boundary only asks the SDK to recalculate its container after returning.
function resizeMap(): void {
  const candidate = map.value as unknown as { resize?: () => void } | null
  candidate?.resize?.()
  scheduleFitMapBounds()
  scheduleDirectionArrow()
}

defineExpose({ resize: resizeMap })
</script>

<template>
  <div class="spatial-map-wrapper">
    <!-- 地图容器 -->
    <div ref="mapContainer" class="map-container"></div>
    <canvas ref="flowCanvas" class="propagation-flow-canvas" aria-hidden="true"></canvas>
    <canvas ref="directionCanvas" class="propagation-direction-canvas" aria-hidden="true"></canvas>

    <!-- 未配置密钥 -->
    <div v-if="needsKey" class="map-overlay key-overlay">
      <el-icon :size="30">
        <WarnTriangleFilled />
      </el-icon>
      <span class="key-title">尚未配置腾讯地图密钥</span>
      <span class="key-detail">{{ MAP_KEY_MISSING_MESSAGE }}</span>
    </div>

    <!-- 图例 -->
    <div v-else-if="!isMapLoading && spatial" ref="mapLegend" class="map-legend">
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
        <div v-if="mapWindDirection !== null" class="legend-item">
          <span class="legend-icon arrow-icon">→</span>
          <span class="legend-label">{{ mapWindLabel }}</span>
        </div>
        <div class="legend-separator"></div>
        <div v-if="!showForecast" class="legend-item">
          <span class="legend-icon area-icon"></span>
          <span class="legend-label">潜在影响区域</span>
        </div>
        <div v-if="!showForecast && spatial?.directional_exposure?.status === 'ok'" class="legend-item">
          <span class="legend-icon sector-icon"></span>
          <span class="legend-label">静态参考扇区（原半径）</span>
        </div>
        <template v-if="showForecastGroup">
          <div class="legend-separator"></div>
          <div class="legend-item">
            <span class="legend-icon forecast-fan-icon"></span>
            <span class="legend-label">黄色流动烟羽（模型范围内）</span>
          </div>
          <div class="legend-item">
            <span class="legend-icon forecast-flow-icon"></span>
            <span class="legend-label">中心路径 · 流动表示输送方向</span>
          </div>
          <div class="legend-item">
            <span class="legend-icon station-icon"></span>
            <span class="legend-label">沿途监测站（点击查看独立观测对照）</span>
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
        <div v-if="sectorPOIStats && !showForecast" class="legend-separator"></div>
        <div v-if="sectorPOIStats && !showForecast" class="legend-subtitle">扇区内敏感目标</div>
        <div v-if="sectorPOIStats && !showForecast" class="legend-item">
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

.propagation-flow-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

/* 传播方向箭头：压在烟羽画布之上、地图 UI 浮层（z-index 1000+）之下 */
.propagation-direction-canvas {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 1;
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
  box-sizing: border-box;
  max-width: min(184px, 45%);
  max-height: calc(100% - 60px);
  overflow-y: auto;
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
  width: 17px;
  height: 13px;
  border-radius: 45% 65% 40% 60%;
  background: radial-gradient(ellipse, rgba(210, 140, 0, .65), rgba(210, 140, 0, 0) 75%);
}

/* 流动光带：用一深一浅两段叠加，示意"由站点向外推移" */
.forecast-flow-icon {
  width: 16px;
  height: 14px;
  border-radius: 2px;
  background: linear-gradient(
    to bottom,
    rgba(230, 170, 20, 0.2),
    rgba(210, 140, 0, 0.9),
    rgba(230, 170, 20, 0.2)
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
