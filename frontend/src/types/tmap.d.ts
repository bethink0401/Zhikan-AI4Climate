/**
 * 腾讯地图 GL JS 的最小类型声明。
 *
 * 官方未发布 TypeScript 类型包，而本项目构建门禁是 strict + noUnusedLocals 的
 * vue-tsc，直接引用全局 TMap 会报 TS2304。这里只声明实际用到的成员，
 * 不去镜像一份会与真实 API 脱节的完整定义。
 *
 * ⚠️ 两个与 MapLibre 相反、极易写错的约定：
 *   1. `TMap.LatLng(lat, lng)` —— **纬度在前，经度在后**；
 *      MapLibre / GeoJSON 都是 [经度, 纬度]，转换时必须显式交换。
 *   2. 坐标系是 **GCJ-02**；项目里的路网、POI、路线全是 WGS84 原始数据，
 *      必须先过 `utils/coordTransform.ts` 的 `wgs84ToGcj02`，否则整体偏移上百米。
 *
 * 图层实例类型统一以 `...Layer` 结尾，与命名空间里的构造函数名区分开，
 * 避免同名 interface 与 const 的合并歧义。
 *
 * 本文件不含 import/export，因此是全局脚本声明。
 */

declare namespace TMap {
  // ── 基础几何 ──────────────────────────────────────────────────────────

  class LatLng {
    constructor(lat: number, lng: number, height?: number)
    getLat(): number
    getLng(): number
    getHeight(): number
  }

  class LatLngBounds {
    constructor(sw: LatLng, ne: LatLng)
    getCenter(): LatLng
    getSouthWest(): LatLng
    getNorthEast(): LatLng
    extend(latlng: LatLng): this
    union(other: LatLngBounds): this
    contains(latlng: LatLng): boolean
    isEmpty(): boolean
  }

  class Point {
    constructor(x: number, y: number)
    getX(): number
    getY(): number
  }

  // ── 事件 ──────────────────────────────────────────────────────────────

  /** 图层事件里的图形数据。不同图层字段不同，因此除 id 外全部可选。 */
  interface OverlayGeometry {
    id: string
    styleId?: string
    properties?: Record<string, unknown>
    position?: LatLng
    paths?: LatLng[]
    center?: LatLng
    radius?: number
  }

  interface MapEvent {
    latLng: LatLng
    point: Point
    type: string
    target: unknown
    originalEvent?: unknown
  }

  interface GeometryOverlayEvent {
    geometry: OverlayGeometry
    latLng: LatLng
    point: Point
    type: string
    target: unknown
    originalEvent?: unknown
  }

  // ── 地图 ──────────────────────────────────────────────────────────────

  interface EaseOptions {
    duration?: number
  }

  interface FitBoundsOptions {
    padding?: number | { top?: number; bottom?: number; left?: number; right?: number }
    minZoom?: number
    maxZoom?: number
    ease?: EaseOptions
  }

  interface MapOptions {
    center?: LatLng
    zoom?: number
    minZoom?: number
    maxZoom?: number
    rotation?: number
    pitch?: number
    scale?: number
    cursor?: string
    draggable?: boolean
    scrollable?: boolean
    doubleClickZoom?: boolean
    showControl?: boolean
    viewMode?: string
    boundary?: LatLngBounds
  }

  class Map {
    constructor(container: string | HTMLElement, options?: MapOptions)
    setCenter(center: LatLng): this
    setZoom(zoom: number): this
    setMinZoom(minZoom: number): this
    setMaxZoom(maxZoom: number): this
    setPitch(pitch: number): this
    setRotation(rotation: number): this
    setCursorStyle(style: string): this
    setDraggable(draggable: boolean): this
    setScrollable(scrollable: boolean): this
    panTo(latLng: LatLng, opts?: EaseOptions): this
    zoomTo(zoom: number, opts?: EaseOptions): this
    easeTo(status: { center?: LatLng; zoom?: number }, opts?: EaseOptions): this
    fitBounds(bounds: LatLngBounds, options?: FitBoundsOptions): this
    getCenter(): LatLng
    getZoom(): number
    getBounds(): LatLngBounds
    projectToContainer(latLng: LatLng): Point
    destroy(): void
    on(
      eventName: 'click' | 'rightclick' | 'dblclick' | 'mousedown' | 'mouseup' | 'mousemove',
      listener: (event: MapEvent) => void
    ): this
    on(eventName: string, listener: (event: never) => void): this
    off(
      eventName: 'click' | 'rightclick' | 'dblclick' | 'mousedown' | 'mouseup' | 'mousemove',
      listener: (event: MapEvent) => void
    ): this
    off(eventName: string, listener: (event: never) => void): this
    once(eventName: string, listener: (event: never) => void): this
  }

  // ── 样式 ──────────────────────────────────────────────────────────────

  interface MarkerStyleOptions {
    width: number
    height: number
    anchor?: { x: number; y: number }
    src?: string
    faceTo?: 'map' | 'screen'
    rotate?: number
    opacity?: number
    color?: string
    size?: number
    direction?: 'center' | 'top' | 'bottom' | 'left' | 'right'
    offset?: { x: number; y: number }
    backgroundColor?: string
    backgroundBorderColor?: string
    backgroundBorderWidth?: number
    backgroundBorderRadius?: number
    padding?: string
    enableRelativeScale?: boolean
  }

  class MarkerStyle {
    constructor(options: MarkerStyleOptions)
  }

  interface PolylineStyleOptions {
    color?: string
    width?: number
    borderWidth?: number
    borderColor?: string
    lineCap?: 'butt' | 'round' | 'square'
    dashArray?: number[]
    showArrow?: boolean
  }

  class PolylineStyle {
    constructor(options: PolylineStyleOptions)
  }

  interface PolygonStyleOptions {
    color?: string
    showBorder?: boolean
    borderColor?: string
    borderWidth?: number
  }

  class PolygonStyle {
    constructor(options: PolygonStyleOptions)
  }

  interface CircleStyleOptions {
    color?: string
    showBorder?: boolean
    borderColor?: string
    borderWidth?: number
    borderDashArray?: number[]
  }

  class CircleStyle {
    constructor(options: CircleStyleOptions)
  }

  // ── 图层数据 ──────────────────────────────────────────────────────────

  interface PointGeometry {
    id: string
    styleId?: string
    position: LatLng
    rank?: number
    properties?: Record<string, unknown>
    content?: string
  }

  interface PolylineGeometry {
    id: string
    styleId?: string
    paths: LatLng[]
    rank?: number
    properties?: Record<string, unknown>
  }

  interface PolygonGeometry {
    id: string
    styleId?: string
    paths: LatLng[] | LatLng[][]
    rank?: number
    properties?: Record<string, unknown>
  }

  interface CircleGeometry {
    id?: string
    styleId?: string
    center: LatLng
    radius: number
    properties?: Record<string, unknown>
  }

  interface LayerOptions {
    id?: string
    map?: Map
    zIndex?: number
    minZoom?: number
    maxZoom?: number
    disableInteractive?: boolean
    isStopPropagation?: boolean
  }

  /** 四个图层类共有的操作方法。 */
  interface LayerCommon<G> {
    setMap(map: Map | null): this
    setGeometries(geometries: G[]): this
    setVisible(visible: boolean): this
    getGeometries(): G[]
    getGeometryById(id: string): G | null
    updateGeometries(geometries: G[]): this
    add(geometries: G[]): this
    remove(ids: string | string[]): this
    on(
      eventName: 'click' | 'mousedown' | 'mouseup' | 'hover',
      listener: (event: GeometryOverlayEvent) => void
    ): this
    on(eventName: string, listener: (event: never) => void): this
    off(eventName: string, listener: (event: never) => void): this
  }

  interface MultiMarkerLayer extends LayerCommon<PointGeometry> {
    setStyles(styles: Record<string, MarkerStyle>): this
  }

  interface MultiPolylineLayer extends LayerCommon<PolylineGeometry> {
    setStyles(styles: Record<string, PolylineStyle>): this
  }

  interface MultiPolygonLayer extends LayerCommon<PolygonGeometry> {
    setStyles(styles: Record<string, PolygonStyle>): this
  }

  interface MultiCircleLayer extends LayerCommon<CircleGeometry> {
    setStyles(styles: Record<string, CircleStyle>): this
  }

  interface MultiMarkerOptions extends LayerOptions {
    styles?: Record<string, MarkerStyle>
    geometries?: PointGeometry[]
  }

  interface MultiPolylineOptions extends LayerOptions {
    styles?: Record<string, PolylineStyle>
    geometries?: PolylineGeometry[]
  }

  interface MultiPolygonOptions extends LayerOptions {
    styles?: Record<string, PolygonStyle>
    geometries?: PolygonGeometry[]
  }

  interface MultiCircleOptions extends LayerOptions {
    styles?: Record<string, CircleStyle>
    geometries?: CircleGeometry[]
  }

  const MultiMarker: { new (options: MultiMarkerOptions): MultiMarkerLayer }
  const MultiPolyline: { new (options: MultiPolylineOptions): MultiPolylineLayer }
  const MultiPolygon: { new (options: MultiPolygonOptions): MultiPolygonLayer }
  const MultiCircle: { new (options: MultiCircleOptions): MultiCircleLayer }

  // ── 贴地图层 ──────────────────────────────────────────────────────────

  /**
   * 贴地图片图层：把一张图片按 LatLngBounds 铺在地图底图上。
   * 用途是画连续场（如污染暴露强度），比用上千个圆几何拼要平滑得多。
   *
   * 注意它内部用 `new Image()` + `crossOrigin='anonymous'` 取图，
   * 因此 src 只能是 data URL 或同源/允许跨域的地址。
   */
  interface ImageGroundLayerOptions extends LayerOptions {
    src: string
    bounds: LatLngBounds
    opacity?: number
  }

  interface GroundImageLayer {
    setMap(map: Map | null): this
    setSrc(src: string): this
    setBounds(bounds: LatLngBounds): this
    getBounds(): LatLngBounds
    setVisible(visible: boolean): this
    on(eventName: string, listener: (event: never) => void): this
    destroy(): void
  }

  const ImageGroundLayer: { new (options: ImageGroundLayerOptions): GroundImageLayer }

  // ── 信息窗 ────────────────────────────────────────────────────────────

  interface InfoWindowOptions {
    map?: Map
    position?: LatLng
    content?: string
    offset?: { x: number; y: number }
    enableCustom?: boolean
  }

  class InfoWindow {
    constructor(options: InfoWindowOptions)
    open(): this
    close(): this
    setPosition(position: LatLng): this
    setContent(content: string): this
    setMap(map: Map | null): this
    destroy(): this
    on(eventName: string, listener: (event: never) => void): this
    off(eventName: string, listener: (event: never) => void): this
  }

  namespace constants {
    const MAP_ZOOM_TYPE: {
      DEFAULT: string
      CENTER: string
    }
  }
}
