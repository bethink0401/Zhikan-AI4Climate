<template>
  <div class="travel-map">
    <!-- 地图容器 -->
    <div ref="mapContainer" class="map-canvas"></div>

    <!-- 未配置密钥 -->
    <div v-if="needsKey" class="map-overlay map-key-missing">
      <el-icon :size="26"><WarningFilled /></el-icon>
      <div class="key-title">尚未配置腾讯地图密钥</div>
      <div class="key-steps">
        <p>{{ MAP_KEY_MISSING_MESSAGE }}</p>
        <p>申请入口：lbs.qq.com → 控制台 → 应用管理 → 我的应用 → 添加 Key</p>
      </div>
    </div>

    <!-- 加载态 -->
    <div v-else-if="isMapLoading" class="map-overlay">
      <el-icon class="is-loading" :size="28"><Loading /></el-icon>
      <span>地图加载中…</span>
    </div>

    <!-- 错误态 -->
    <div v-else-if="mapError" class="map-overlay map-error">
      <el-icon :size="24"><WarningFilled /></el-icon>
      <span>{{ mapError }}</span>
    </div>

    <!-- 图例 -->
    <div v-if="!needsKey" class="map-legend">
      <div class="legend-hd">
        <span class="legend-title">图层与图例</span>
        <span class="legend-step">采样步长 100 m</span>
      </div>
      <div v-for="item in legendItems" :key="item.mode" class="legend-item">
        <span
          class="legend-line"
          :class="{ 'is-dashed': item.dashed }"
          :style="{ background: item.color, borderColor: item.color }"
        ></span>
        <span class="legend-label">{{ item.label }}</span>
        <span class="legend-count">（{{ item.count }} 条）</span>
      </div>
      <div class="legend-item">
        <span class="legend-dot legend-dot-origin"></span>
        <span class="legend-label">起点</span>
        <span class="legend-count">{{ origin?.name || '—' }}</span>
      </div>
      <div class="legend-item">
        <span class="legend-dot legend-dot-destination"></span>
        <span class="legend-label">终点</span>
        <span class="legend-count">{{ destination?.name || '—' }}</span>
      </div>

      <template v-if="pollutionGrid.length">
        <div class="legend-divider"></div>
        <div class="legend-scale">
          <span class="legend-scale-label">污染暴露指数</span>
          <span class="legend-low">低</span>
          <span class="legend-gradient"></span>
          <span class="legend-high">高</span>
        </div>
      </template>
    </div>

    <!-- 拾取提示 -->
    <div v-if="pickRole && !needsKey" class="pick-hint">
      <span class="pick-step">{{ pickRole === 'origin' ? '第 1 步' : '第 2 步' }}</span>
      <el-icon :size="14"><Aim /></el-icon>
      <span class="pick-text">
        在地图上点选{{ pickRole === 'origin' ? '起点' : '终点' }}
        <em>{{ pickRole === 'origin' ? '（先定起点，再定终点）' : '（选完自动开始计算）' }}</em>
      </span>
      <button class="pick-cancel" @click="emit('cancel-pick')">取消</button>
    </div>

  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, shallowRef, watch } from 'vue'
import { Aim, Loading, WarningFilled } from '@element-plus/icons-vue'
import { getModeColor } from '@/utils/travelMapper'
import { buildPollutionRaster } from '@/utils/pollutionRaster'
import { latLngToWgs84, pathToLatLngs, pointToLatLng, wgs84ToGcj02 } from '@/utils/coordTransform'
import { MAP_KEY_MISSING_MESSAGE, hasMapKey, loadTMap } from '@/utils/tmapLoader'
import type { TravelFieldSample, TravelPoint, TravelRouteOption } from '@/types/travel'

/**
 * TravelRouteMap - 出行路线地图（腾讯地图 GL JS）
 *
 * 同一张图上叠加：污染暴露连续场、多种出行方式的候选路线、带名称标签的起终点标记。
 *
 * 从 MapLibre 迁到腾讯地图的三个主要差异，改代码时最容易踩：
 *   1. 坐标是 **{纬度, 经度}** 且为 **GCJ-02**，而项目数据是 WGS84 的 [经度, 纬度]，
 *      进出地图都要过 utils/coordTransform.ts。
 *   2. 图层没有"按属性插值"的样式表达式，颜色只能按指数分桶，先算好样式再绑定。
 *   3. 点标记图层原生不支持拖拽，拖动是自己用 mousedown/mousemove/mouseup 实现的。
 *
 * 污染场用**贴地图片图层**（ImageGroundLayer）而不是色圆：
 *   采样点在对角线格点上、间距约 224 m，圆半径 110 m 盖不满，加上只有 8 级色阶，
 *   看起来就是一块块斑点。改成 utils/pollutionRaster.ts 先重采样成连续场图片再贴，
 *   既平滑，又天然铺在路线与标记之下（ground 层的层级本来就低）。
 */

interface Props {
  routes: TravelRouteOption[]
  origin: TravelPoint | null
  destination: TravelPoint | null
  /** 用户明确点选的那条路线；为空表示还没点过，走默认主次画法 */
  selectedId?: string
  pollutionGrid?: TravelFieldSample[]
  /** 非空时进入点选模式，点击地图即设定该角色 */
  pickRole?: 'origin' | 'destination' | null
}

const props = withDefaults(defineProps<Props>(), {
  selectedId: '',
  pollutionGrid: () => [],
  pickRole: null
})
const emit = defineEmits<{
  (event: 'select-route', routeId: string): void
  (event: 'pick-point', payload: { role: 'origin' | 'destination'; point: TravelPoint }): void
  (event: 'move-point', payload: { role: 'origin' | 'destination'; point: TravelPoint }): void
  (event: 'cancel-pick'): void
}>()

/** 未配置密钥时兜底显示的中心（香港旺角）。 */
const FALLBACK_CENTER = { lon: 114.1694, lat: 22.3193 }
const MAP_ZOOM = 14

/**
 * 图例里对三种线色的叫法——直接说"这条线代表什么"，比讲出行方式更省解释
 */
const MODES = ['walk', 'drive', 'transit'] as const

const LEGEND_LABEL: Record<(typeof MODES)[number], string> = {
  transit: '港铁推荐',
  drive: '驾车干道',
  walk: '步行方案'
}

/** 公共交通的接驳段（起点→站、站→终点）按步行处理，图例里单独说明 */
const LINK_LABEL = '接驳步行段'

/** 标记图标尺寸：上方圆点 + 下方名称牌 */
const PIN_WIDTH = 172
const PIN_HEIGHT = 62
const PIN_ANCHOR = { x: PIN_WIDTH / 2, y: 13 }

const MAP_FONT = '-apple-system,PingFang SC,sans-serif'

const mapContainer = shallowRef<HTMLDivElement | null>(null)
// 统一用 shallowRef 持有腾讯地图的类实例：它们带私有成员，用 ref 会让 Vue 对类型做
// 深度展开，vue-tsc 会报 TS2589（类型实例化过深）。
const map = shallowRef<TMap.Map | null>(null)
/** 污染暴露连续场：贴地图片图层，层级低于路线与标记 */
const groundLayer = shallowRef<TMap.GroundImageLayer | null>(null)
const routeLayer = shallowRef<TMap.MultiPolylineLayer | null>(null)
const markerLayer = shallowRef<TMap.MultiMarkerLayer | null>(null)

const isMapLoading = shallowRef(true)
const mapError = shallowRef('')
const needsKey = shallowRef(false)

let picking = false
/** 正在被拖动的标记角色；null 表示当前没有拖动。 */
let grabbedRole: 'origin' | 'destination' | null = null

/** 公共交通里有没有需要步行接驳的（起终点不在站上） */
const transitLinkKm = computed(() =>
  props.routes
    .filter(route => route.mode === 'transit' && route.transit_detail)
    .reduce(
      (total, route) =>
        total +
        (route.transit_detail?.access_distance_km ?? 0) +
        (route.transit_detail?.egress_distance_km ?? 0),
      0
    )
)

const legendItems = computed(() => {
  const items: Array<{ mode: string; label: string; color: string; count: number; dashed?: boolean }> =
    MODES.map(mode => ({
      mode,
      label: LEGEND_LABEL[mode],
      color: getModeColor(mode),
      count: props.routes.filter(item => item.mode === mode).length
    })).filter(item => item.count > 0)

  if (transitLinkKm.value > 0.005) {
    items.push({
      mode: 'transit_link',
      label: LINK_LABEL,
      color: getModeColor('walk'),
      count: props.routes.filter(
        route =>
          route.mode === 'transit' &&
          (route.transit_detail?.access_distance_km ?? 0) +
            (route.transit_detail?.egress_distance_km ?? 0) >
            0.005
      ).length,
      dashed: true
    })
  }

  return items
})

/** 暴露场里指数最高的那个采样点，取景时要把画面拉到它 */
const peakSample = computed<TravelFieldSample | null>(() => {
  let peak: TravelFieldSample | null = null
  for (const sample of props.pollutionGrid) {
    if (!peak || sample.index > peak.index) peak = sample
  }
  return peak
})

// ── 颜色与样式 ────────────────────────────────────────────────────────────

/**
 * 线型档位。
 * focus = 用户当前点选的那条，最粗、压在最上面；
 * dim   = 已经点了别的方案，这条退到背景里。
 */
type RouteTier = 'focus' | 'primary' | 'alternative' | 'dim'

function routeStyleId(mode: string, tier: RouteTier): string {
  return `${mode}_${tier}`
}

function linkStyleId(tier: RouteTier): string {
  if (tier === 'focus') return 'link_focus'
  if (tier === 'dim') return 'link_dim'
  return 'link_plain'
}

/** 往白色里混，用来把"当前没在看"的路线压淡（PolylineStyle 没有透明度属性） */
function fadeColor(hex: string, ratio: number): string {
  const value = hex.replace('#', '')
  const mix = (channel: string): number => {
    const c = parseInt(channel, 16)
    return Math.round(c + (255 - c) * ratio)
  }
  return `rgb(${mix(value.slice(0, 2))},${mix(value.slice(2, 4))},${mix(value.slice(4, 6))})`
}

function buildRouteStyles(): Record<string, TMap.PolylineStyle> {
  const styles: Record<string, TMap.PolylineStyle> = {}
  MODES.forEach(mode => {
    const color = getModeColor(mode)
    // 用 white 描边代替 MapLibre 那层独立的 casing，一层的效果等价且少一次绘制。
    // 轨道段画成虚线：它不是道路，和路面路线混在一起时容易看错层级。
    const dashes = mode === 'transit' ? { dashArray: [14, 9] } : {}

    styles[routeStyleId(mode, 'focus')] = new TMap.PolylineStyle({
      color,
      width: 8,
      borderWidth: 5,
      borderColor: '#ffffff',
      lineCap: 'round',
      ...dashes
    })
    styles[routeStyleId(mode, 'primary')] = new TMap.PolylineStyle({
      color,
      width: 6,
      borderWidth: 4,
      borderColor: '#ffffff',
      lineCap: 'round',
      ...dashes
    })
    styles[routeStyleId(mode, 'alternative')] = new TMap.PolylineStyle({
      color,
      width: 4,
      borderWidth: 3,
      borderColor: '#ffffff',
      lineCap: 'round',
      ...dashes
    })
    styles[routeStyleId(mode, 'dim')] = new TMap.PolylineStyle({
      color: fadeColor(color, 0.52),
      width: 4,
      borderWidth: 2,
      borderColor: '#ffffff',
      lineCap: 'round',
      ...dashes
    })
  })

  // 公共交通的接驳段：与步行方案同色，但画成虚线，
  // 免得和"整段都走过去的步行方案"看成一回事。
  const walkColor = getModeColor('walk')
  styles.link_plain = new TMap.PolylineStyle({
    color: walkColor,
    width: 4,
    borderWidth: 3,
    borderColor: '#ffffff',
    lineCap: 'round',
    dashArray: [9, 7]
  })
  styles.link_focus = new TMap.PolylineStyle({
    color: walkColor,
    width: 6,
    borderWidth: 4,
    borderColor: '#ffffff',
    lineCap: 'round',
    dashArray: [12, 8]
  })
  styles.link_dim = new TMap.PolylineStyle({
    color: fadeColor(walkColor, 0.52),
    width: 4,
    borderWidth: 2,
    borderColor: '#ffffff',
    lineCap: 'round',
    dashArray: [9, 7]
  })

  return styles
}

function escapeXml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/**
 * 圆形徽标 + 名称牌的图标（内联 data URL，符合底图规范：不得引用示例站点图片）。
 * 名称太长就把字号压到 12px，并按字符数撑宽牌子，避免文字溢出圆角矩形。
 */
function pinIcon(
  badge: string,
  badgeColor: string,
  label: string,
  chip: { bg: string; fg: string; border: string }
): string {
  const text = escapeXml(label.length > 12 ? `${label.slice(0, 11)}…` : label)
  const fontSize = label.length > 8 ? 11.5 : 13
  const chipWidth = Math.min(PIN_WIDTH - 6, Math.max(64, text.length * fontSize + 20))
  const chipX = (PIN_WIDTH - chipWidth) / 2
  const centerX = PIN_WIDTH / 2

  const svg =
    `<svg xmlns="http://www.w3.org/2000/svg" width="${PIN_WIDTH}" height="${PIN_HEIGHT}">` +
    `<rect x="${chipX}" y="28" width="${chipWidth}" height="25" rx="7" ` +
    `fill="${chip.bg}" stroke="${chip.border}" stroke-width="1"/>` +
    `<text x="${centerX}" y="45.5" text-anchor="middle" font-size="${fontSize}" ` +
    `font-weight="600" fill="${chip.fg}" font-family="${MAP_FONT}">${text}</text>` +
    `<circle cx="${centerX}" cy="13" r="11.5" fill="${badgeColor}" stroke="#ffffff" stroke-width="2.5"/>` +
    `<text x="${centerX}" y="13" text-anchor="middle" dominant-baseline="central" ` +
    `font-size="13" font-weight="600" fill="#ffffff" font-family="${MAP_FONT}">${badge}</text>` +
    `</svg>`
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
}

/** 起终点、污染源站与最热点的标记样式。名称写在图标里，位置一变就要重建。 */
function buildMarkerStyles(): Record<string, TMap.MarkerStyle> {
  const styles: Record<string, TMap.MarkerStyle> = {
    origin: new TMap.MarkerStyle({
      width: PIN_WIDTH,
      height: PIN_HEIGHT,
      anchor: { ...PIN_ANCHOR },
      enableRelativeScale: false,
      src: pinIcon('起', '#2f6fed', props.origin?.name ?? '起点', {
        bg: '#1f3557',
        fg: '#ffffff',
        border: '#1f3557'
      })
    }),
    destination: new TMap.MarkerStyle({
      width: PIN_WIDTH,
      height: PIN_HEIGHT,
      anchor: { ...PIN_ANCHOR },
      enableRelativeScale: false,
      src: pinIcon('终', '#e04a5f', props.destination?.name ?? '终点', {
        bg: '#ffffff',
        fg: '#e04a5f',
        border: '#e04a5f'
      })
    })
  }

  return styles
}

// ── 数据 → 图层几何 ───────────────────────────────────────────────────────

const METERS_PER_DEG_LAT = 110540
const METERS_PER_DEG_LON_EQ = 111320

function metersBetween(a: number[], b: number[]): number {
  const midLat = ((a[1] + b[1]) / 2) * (Math.PI / 180)
  const dx = (b[0] - a[0]) * METERS_PER_DEG_LON_EQ * Math.cos(midLat)
  const dy = (b[1] - a[1]) * METERS_PER_DEG_LAT
  return Math.hypot(dx, dy)
}

function cumulativeMeters(path: number[][]): number[] {
  const acc = [0]
  for (let index = 1; index < path.length; index += 1) {
    acc.push(acc[index - 1] + metersBetween(path[index - 1], path[index]))
  }
  return acc
}

/** 取折线上累计里程为 distance 处的位置，落在两点之间时线性插值 */
function pointAtDistance(path: number[][], acc: number[], distance: number): number[] {
  const total = acc[acc.length - 1]
  const target = Math.max(0, Math.min(total, distance))
  for (let index = 1; index < path.length; index += 1) {
    if (acc[index] >= target) {
      const span = acc[index] - acc[index - 1] || 1
      const ratio = (target - acc[index - 1]) / span
      return [
        path[index - 1][0] + (path[index][0] - path[index - 1][0]) * ratio,
        path[index - 1][1] + (path[index][1] - path[index - 1][1]) * ratio
      ]
    }
  }
  return path[path.length - 1]
}

function slicePath(path: number[][], acc: number[], fromM: number, toM: number): number[][] {
  const out: number[][] = [pointAtDistance(path, acc, fromM)]
  for (let index = 0; index < path.length; index += 1) {
    if (acc[index] > fromM && acc[index] < toM) out.push(path[index])
  }
  out.push(pointAtDistance(path, acc, toM))
  return out
}

interface RouteSegment {
  kind: 'access' | 'rail' | 'egress' | 'whole'
  path: number[][]
}

/**
 * 把公共交通方案切成「步行接驳 → 轨道区间 → 步行接驳」三段。
 *
 * 起终点通常不在站上，Agent 输出的是一整条连续折线，但时间模型里
 * access / egress 用的是 3.99 km/h 的步行速度，也就是说这两段确实是走过去的。
 * 整条画成轨道色会让人以为"从起点坐车到终点"，所以按累计里程切开。
 */
function splitTransit(route: TravelRouteOption): RouteSegment[] {
  const detail = route.transit_detail
  const path = route.geometry
  if (!detail || path.length < 2) return [{ kind: 'whole', path }]

  const acc = cumulativeMeters(path)
  const total = acc[acc.length - 1]
  const accessM = (detail.access_distance_km ?? 0) * 1000
  const egressM = (detail.egress_distance_km ?? 0) * 1000

  const boardM = Math.min(Math.max(accessM, 0), total)
  const alightM = Math.max(boardM, total - Math.max(egressM, 0))

  const segments: RouteSegment[] = []
  if (boardM > 5) segments.push({ kind: 'access', path: slicePath(path, acc, 0, boardM) })
  if (alightM - boardM > 5) segments.push({ kind: 'rail', path: slicePath(path, acc, boardM, alightM) })
  if (total - alightM > 5) segments.push({ kind: 'egress', path: slicePath(path, acc, alightM, total) })

  return segments.length ? segments : [{ kind: 'whole', path }]
}

/**
 * 路线几何。
 *
 * 画序即层级：rank 小的先画、大的压在上面。
 * 用户点选的那条排在最上面，其余按推荐 / 普通下沉——
 * 这三条候选在本例里大段重合（都沿弥敦道走廊），不排序的话谁排最后谁盖住别人。
 */
function buildRouteGeometries(): TMap.PolylineGeometry[] {
  const selectedId = props.selectedId
  const items: Array<{
    id: string
    styleId: string
    paths: TMap.LatLng[]
    rank: number
    routeId: string
  }> = []

  for (const route of props.routes) {
    if (route.geometry.length < 2) continue

    const isSelected = Boolean(selectedId) && route.route_id === selectedId
    const tier: RouteTier = selectedId
      ? isSelected
        ? 'focus'
        : 'dim'
      : route.recommended
        ? 'primary'
        : 'alternative'
    const rank = isSelected ? 40 : route.recommended ? 30 : 10

    if (route.mode === 'transit') {
      splitTransit(route).forEach((segment, index) => {
        const isRail = segment.kind === 'rail' || segment.kind === 'whole'
        items.push({
          // 一条路线拆成多段，id 要能反查回路线
          id: `${route.route_id}@${index}`,
          styleId: isRail ? routeStyleId('transit', tier) : linkStyleId(tier),
          paths: pathToLatLngs(segment.path),
          // 接驳段压在轨道段下面，交叠处以轨道为准
          rank: isRail ? rank : rank - 5,
          routeId: route.route_id
        })
      })
      continue
    }

    items.push({
      id: route.route_id,
      styleId: routeStyleId(route.mode, tier),
      paths: pathToLatLngs(route.geometry),
      rank,
      routeId: route.route_id
    })
  }

  return items
    .sort((a, b) => a.rank - b.rank)
    .map(item => ({
      id: item.id,
      styleId: item.styleId,
      paths: item.paths,
      properties: { route_id: item.routeId }
    }))
}

function markerGeometry(
  role: 'origin' | 'destination',
  lon: number,
  lat: number,
  name: string
): TMap.PointGeometry {
  return {
    id: role,
    styleId: role,
    position: pointToLatLng([lon, lat]),
    properties: { role, name }
  }
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
    // await 期间组件可能已卸载，容器ref 会被置空
    if (!mapContainer.value) return

    const origin = props.origin
    const center = origin
      ? pointToLatLng([origin.lon, origin.lat])
      : pointToLatLng([FALLBACK_CENTER.lon, FALLBACK_CENTER.lat])

    const newMap = new TMap.Map(mapContainer.value, {
      center,
      zoom: MAP_ZOOM,
      viewMode: '2D',
      showControl: false
    })
    map.value = newMap

    initLayers()
    syncPollution()
    syncRoutes()
    syncMarkers()
    fitToBounds()
    syncPicking()
    isMapLoading.value = false
  } catch (error) {
    isMapLoading.value = false
    mapError.value = error instanceof Error ? error.message : '地图初始化失败'
  }
}

function initLayers(): void {
  const current = map.value
  if (!current) return

  routeLayer.value = new TMap.MultiPolyline({
    id: 'travel-route-layer',
    map: current,
    zIndex: 3,
    styles: buildRouteStyles(),
    geometries: []
  })
  routeLayer.value.on('click', (event: TMap.GeometryOverlayEvent) => {
    if (picking) return
    // 公共交通会被拆成多段，id 形如 transit_0@2，要还原成路线 id
    const routeId = String(event.geometry.id ?? '').split('@')[0]
    if (routeId) emit('select-route', routeId)
  })

  markerLayer.value = new TMap.MultiMarker({
    id: 'travel-marker-layer',
    map: current,
    zIndex: 5,
    styles: buildMarkerStyles(),
    geometries: []
  })

  // 点标记图层原生不支持拖拽，这里自己实现：按住标记后暂停地图拖拽，
  // 跟随 mousemove 更新坐标，抬起时回传并恢复地图拖拽。
  markerLayer.value.on('mousedown', (event: TMap.GeometryOverlayEvent) => {
    const id = event.geometry.id
    grabbedRole = id === 'origin' ? 'origin' : 'destination'
    current.setDraggable(false)
    current.setCursorStyle('grabbing')
  })

  current.on('mousemove', (event: TMap.MapEvent) => {
    if (!grabbedRole) return
    markerLayer.value?.updateGeometries([
      {
        id: grabbedRole,
        styleId: grabbedRole,
        position: event.latLng,
        properties: { role: grabbedRole }
      }
    ])
  })

  current.on('mouseup', (event: TMap.MapEvent) => {
    if (!grabbedRole) return
    const role = grabbedRole
    grabbedRole = null
    current.setDraggable(true)
    syncPicking()
    emitPoint('move-point', role, event.latLng)
  })

  // 点选起终点
  current.on('click', (event: TMap.MapEvent) => {
    if (!picking || !props.pickRole) return
    emitPoint('pick-point', props.pickRole, event.latLng)
  })

}

/** 把地图事件里的 GCJ-02 坐标转回 WGS84 再回传，后端全线按 WGS84 工作。 */
function emitPoint(
  eventName: 'pick-point' | 'move-point',
  role: 'origin' | 'destination',
  position: TMap.LatLng
): void {
  const wgs = latLngToWgs84(position)
  const point = {
    // 不拿坐标当名字：控制区里已经有一行经纬度了，重复显示反而难读
    name: '地图点选',
    lon: wgs.lon,
    lat: wgs.lat
  }
  // 分开写是因为 emit 按字面量重载，传联合类型匹配不上
  if (eventName === 'pick-point') {
    emit('pick-point', { role, point })
  } else {
    emit('move-point', { role, point })
  }
}

// ── 数据同步 ──────────────────────────────────────────────────────────────

/**
 * 污染暴露场：重采样成连续场图片后贴地。
 *
 * 图层**只建一次**，之后换场走 setBounds + setSrc。
 * 试过"每次先 setMap(null) 再 new 一个新的"，第二次开始就完全不显示——
 * 同一个 id 的图层在底图内部有注册，反复增删会留下脏引用。
 * 复用同一个实例反而更稳，代价只是要在图层不存在时才构造。
 */
function syncPollution(): void {
  const current = map.value
  if (!current) return

  const raster = buildPollutionRaster(props.pollutionGrid)

  if (groundLayer.value) {
    if (!raster) {
      groundLayer.value.setMap(null)
      groundLayer.value = null
      return
    }
    groundLayer.value.setBounds(toBounds(raster.bounds))
    groundLayer.value.setSrc(raster.src)
    return
  }

  if (!raster) return

  groundLayer.value = new TMap.ImageGroundLayer({
    id: 'travel-pollution-ground',
    map: current,
    zIndex: 1,
    opacity: 1,
    src: raster.src,
    bounds: toBounds(raster.bounds)
  })
}

function toBounds(bounds: {
  minLon: number
  minLat: number
  maxLon: number
  maxLat: number
}): TMap.LatLngBounds {
  return new TMap.LatLngBounds(
    new TMap.LatLng(bounds.minLat, bounds.minLon),
    new TMap.LatLng(bounds.maxLat, bounds.maxLon)
  )
}

function syncRoutes(): void {
  routeLayer.value?.setGeometries(buildRouteGeometries())
}

function syncMarkers(): void {
  const layer = markerLayer.value
  if (!layer) return
  // 名称写在图标 SVG 里，起终点/站点/峰值一变必须重建样式
  layer.setStyles(buildMarkerStyles())
  const geometries: TMap.PointGeometry[] = []
  if (props.origin) {
    geometries.push(markerGeometry('origin', props.origin.lon, props.origin.lat, props.origin.name))
  }
  if (props.destination) {
    geometries.push(
      markerGeometry('destination', props.destination.lon, props.destination.lat, props.destination.name)
    )
  }
  layer.setGeometries(geometries)
}

/**
 * 某点离起终点走廊有多远。超过这个距离的点不纳入自动取景：
 * 换一个 7 km 外的污染源站，fitBounds 会把视野拉到装下两片区域，
 * 结果两片污染场都缩成淡色小块，看起来就像"什么都没变"。
 */
function metersFromCorridor(lon: number, lat: number): number {
  if (!props.origin || !props.destination) return 0
  const midLat = (props.origin.lat + props.destination.lat) / 2
  const midLon = (props.origin.lon + props.destination.lon) / 2
  const dx = (lon - midLon) * METERS_PER_DEG_LON_EQ * Math.cos((midLat * Math.PI) / 180)
  const dy = (lat - midLat) * METERS_PER_DEG_LAT
  return Math.hypot(dx, dy)
}

/** 离走廊超过这个距离的点交给"视野外提示条"引导，不硬塞进取景框 */
const FIT_MAX_DISTANCE_M = 2500

/**
 * 自动缩放到能容纳全部路线、起终点，以及**贴近走廊**的暴露场峰值与污染源站。
 *
 * 峰值必须算进来：自定义起终点常常落在污染源下游，
 * 只按路线取景会把最脏的那一块挤出画面，看起来像"没有污染场"。
 * 但远离走廊的峰值/源站不在此列——那会让画面缩到什么都看不清，
 * 这种情况由 offscreen 提示条接手（点击直接飞过去）。
 */
function fitToBounds(): void {
  const current = map.value
  if (!current) return

  const points: Array<{ lat: number; lng: number }> = []
  props.routes.forEach(route =>
    route.geometry.forEach(vertex =>
      points.push(wgs84ToGcj02(Number(vertex[1]), Number(vertex[0])))
    )
  )
  if (props.origin) points.push(wgs84ToGcj02(props.origin.lat, props.origin.lon))
  if (props.destination) points.push(wgs84ToGcj02(props.destination.lat, props.destination.lon))

  const peak = peakSample.value
  if (peak && metersFromCorridor(peak.lon, peak.lat) <= FIT_MAX_DISTANCE_M) {
    points.push(wgs84ToGcj02(peak.lat, peak.lon))
  }
  if (!points.length) return

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

  current.fitBounds(
    new TMap.LatLngBounds(new TMap.LatLng(minLat, minLng), new TMap.LatLng(maxLat, maxLng)),
    { padding: 64, maxZoom: 15, ease: { duration: 500 } }
  )

}

/** 进入 / 退出点选模式 */
function syncPicking(): void {
  picking = props.pickRole !== null
  map.value?.setCursorStyle(picking ? 'crosshair' : 'default')
}

/**
 * 取景要等这一批数据都落定。
 * 路线与暴露场是两个 prop，同一个 tick 里两个 watch 都会触发，
 * 直接各自调 fitToBounds 会连跳两次；这里用 rAF 合并成一次。
 */
let fitHandle: number | null = null

function scheduleFit(): void {
  if (fitHandle !== null) cancelAnimationFrame(fitHandle)
  fitHandle = requestAnimationFrame(() => {
    fitHandle = null
    fitToBounds()
  })
}

const onKeydown = (event: KeyboardEvent): void => {
  if (event.key === 'Escape' && picking) emit('cancel-pick')
}

// ── 生命周期 ──────────────────────────────────────────────────────────────

onMounted(() => {
  void initMap()
  window.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  if (fitHandle !== null) cancelAnimationFrame(fitHandle)

  groundLayer.value?.setMap(null)
  groundLayer.value = null
  routeLayer.value?.setMap(null)
  markerLayer.value?.setMap(null)
  routeLayer.value = null
  markerLayer.value = null

  map.value?.destroy()
  map.value = null
})

watch(
  () => props.routes,
  () => {
    syncRoutes()
    syncMarkers()
    scheduleFit()
  },
  { deep: false }
)

// 点选哪条方案只换线型，不动视野——正看着的那条被重新取景会很晃
watch(
  () => props.selectedId,
  () => syncRoutes()
)

watch(
  () => props.pollutionGrid,
  () => {
    // 峰值标记跟着场走，换场后要一起重建
    syncPollution()
    syncMarkers()
    scheduleFit()
  },
  { deep: false }
)


watch(
  () => [props.origin, props.destination],
  () => syncMarkers(),
  { deep: false }
)

watch(
  () => props.pickRole,
  () => syncPicking()
)
</script>

<style scoped>
.travel-map {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  border-radius: 0 0 var(--tf-radius) var(--tf-radius);
}

.map-canvas {
  width: 100%;
  height: 100%;
}

.map-overlay {
  position: absolute;
  inset: 0;
  /* 腾讯地图的交互捕获层是 z-index:1000，自绘浮层必须压过它 */
  z-index: 1100;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  background: var(--tf-card);
  color: var(--tf-text-2);
  font-size: 13px;
}

.map-overlay .is-loading {
  color: var(--tf-blue);
  animation: travel-spin 1s linear infinite;
}

@keyframes travel-spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

.map-error { color: var(--tf-red); }

/* 未配置密钥时的引导面板：宁可明确说清楚，也不要留一块看不懂的空白 */
.map-key-missing {
  color: var(--tf-amber);
  padding: 0 32px;
  text-align: center;
}

.key-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--tf-text);
}

.key-steps {
  max-width: 520px;
  text-align: left;
  font-size: 12px;
  line-height: 20px;
  color: var(--tf-text-3);
}

.key-steps p { margin: 4px 0; }

/* ── 图例 ───────────────────────────────────────────────────────────── */

.map-legend {
  position: absolute;
  left: 12px;
  bottom: 12px;
  /* 腾讯地图的交互捕获层是 z-index:1000，自绘浮层必须压过它 */
  z-index: 1100;
  min-width: 190px;
  padding: 10px 12px;
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid var(--tf-border);
  border-radius: var(--tf-radius-sm);
  box-shadow: 0 2px 8px rgba(23, 32, 48, 0.1);
  font-size: 12px;
  color: var(--tf-text-2);
}

.legend-hd {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 7px;
}

.legend-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--tf-text);
}

.legend-step {
  font-size: 10px;
  color: var(--tf-text-3);
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 6px;
  line-height: 19px;
}

.legend-line {
  width: 18px;
  height: 4px;
  border-radius: 2px;
  flex-shrink: 0;
}

/* 接驳步行段：与实线区分开，说明它是"从起点走过去的"那一段 */
.legend-line.is-dashed {
  background: none !important;
  height: 0;
  border-top: 3px dashed;
}

.legend-count {
  color: var(--tf-text-3);
  font-size: 11px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.legend-dot {
  width: 9px;
  height: 9px;
  border-radius: 50%;
  flex-shrink: 0;
}

.legend-dot-origin { background: #2f6fed; }
.legend-dot-destination { background: #e04a5f; }

.legend-divider {
  height: 1px;
  background: var(--tf-border);
  margin: 8px 0 7px;
}

.legend-scale {
  display: flex;
  align-items: center;
  gap: 5px;
}

.legend-scale-label {
  color: var(--tf-text-3);
  font-size: 11px;
}

.legend-low,
.legend-high {
  font-size: 10px;
}

.legend-low { color: #6da2e6; }
.legend-high { color: #c0392b; }

/* 污染暴露指数色阶，颜色与 utils/pollutionRaster.ts 的 POLLUTION_RAMP 保持一致 */
.legend-gradient {
  flex: 1;
  height: 7px;
  border-radius: 4px;
  background: linear-gradient(
    90deg,
    #cfe8ff 0%,
    #7fc4f5 15%,
    #f7d774 30%,
    #f59a4e 50%,
    #ec5f5f 70%,
    #c9306b 85%,
    #7b1fa2 100%
  );
}

/* ── 点选提示 ───────────────────────────────────────────────────────── */

.pick-hint {
  position: absolute;
  top: 12px;
  left: 50%;
  transform: translateX(-50%);
  /* 腾讯地图的交互捕获层是 z-index:1000，自绘浮层必须压过它 */
  z-index: 1100;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px 6px 10px;
  border-radius: 999px;
  background: var(--tf-blue);
  color: #fff;
  font-size: 12px;
  box-shadow: 0 4px 14px rgba(47, 111, 237, 0.32);
}

.pick-step {
  flex-shrink: 0;
  padding: 1px 7px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.22);
  font-size: 11px;
  font-weight: 600;
}

.pick-text {
  white-space: nowrap;
}

.pick-text em {
  font-style: normal;
  opacity: 0.75;
}

.pick-cancel {
  flex-shrink: 0;
  padding: 2px 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.18);
  color: #fff;
  font-size: 11px;
}

.pick-cancel:hover {
  background: rgba(255, 255, 255, 0.32);
}

</style>
