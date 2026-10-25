import { pointToLatLng } from '@/utils/coordTransform'
import type { MeteorologyPathPrediction } from '@/types/meteorology'

/** Yellow encodes modelled species survival; motion and feathering are decorative. */
export function createPropagationFlow(canvas: HTMLCanvasElement, map: TMap.Map) {
  const context = canvas.getContext('2d')!
  const mask = document.createElement('canvas')
  const cloud = document.createElement('canvas')
  const maskContext = mask.getContext('2d')!
  const cloudContext = cloud.getContext('2d')!
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
  let prediction: MeteorologyPathPrediction | null = null
  let pollutant = ''
  let frame = 0
  let lastFrame = 0
  let dirty = true
  let width = 0
  let height = 0
  let ratio = 1
  let points: { x: number; y: number; width: number; fraction: number | null }[] = []
  let elapsed = 0
  let previousTime = 0
  const project = (coordinate: number[]) => {
    const point = map.projectToContainer(pointToLatLng(coordinate))
    return { x: point.getX(), y: point.getY() }
  }
  const invalidate = () => { dirty = true; start() }

  function rebuild() {
    width = canvas.clientWidth
    height = canvas.clientHeight
    ratio = Math.min(window.devicePixelRatio || 1, 2)
    for (const surface of [canvas, mask, cloud]) {
      surface.width = Math.round(width * ratio)
      surface.height = Math.round(height * ratio)
      surface.getContext('2d')!.setTransform(ratio, 0, 0, ratio, 0, 0)
    }
    points = []
    const geometry = prediction?.propagation_sector_geojson
    if (!geometry || !width || !height) return
    const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates
    const boundary = new Path2D()
    for (const polygon of polygons) for (const ring of polygon) {
      ring.forEach((coordinate, index) => {
        const point = project(coordinate)
        if (index) boundary.lineTo(point.x, point.y)
        else boundary.moveTo(point.x, point.y)
      })
      boundary.closePath()
    }
    maskContext.fillStyle = '#fff'
    maskContext.fill(boundary, 'evenodd')
    // A wide inward alpha ramp avoids exposing the clipped sector silhouette.
    // The calculated coverage remains unchanged, including polygon holes.
    maskContext.globalCompositeOperation = 'destination-out'
    maskContext.filter = 'blur(2px)'
    maskContext.strokeStyle = '#fff'
    const feather = Math.min(70, Math.max(32, Math.min(width, height) * .1))
    const bands = 24
    for (let band = 0; band < bands; band++) {
      maskContext.globalAlpha = 1 / (bands - band)
      maskContext.lineWidth = 2 * feather * (1 - band / bands)
      maskContext.stroke(boundary)
    }
    maskContext.globalAlpha = 1
    maskContext.filter = 'none'
    maskContext.globalCompositeOperation = 'source-over'
    const coordinates = prediction?.path_geojson?.coordinates ?? []
    points = coordinates.map((coordinate, index) => {
      const point = project(coordinate)
      const halfWidth = prediction?.steps[Math.max(0, index - 1)]?.corridor_half_width_km ?? .5
      const lateral = project([coordinate[0] + halfWidth / (111.32 * Math.cos(coordinate[1] * Math.PI / 180)), coordinate[1]])
      const survival = index === 0 ? 1 : prediction?.steps[index - 1]?.remaining_by_pollutant?.[pollutant]?.remaining_fraction
      const fraction = pollutant && typeof survival === 'number' && Number.isFinite(survival)
        ? Math.max(0, Math.min(1, survival)) : null
      return { fraction, x: point.x, y: point.y, width: Math.max(6, Math.hypot(lateral.x - point.x, lateral.y - point.y)) }
    })
    // No uniform wash. Colour uses one species at a time; native units are never mixed.
    // Cloud shape is decorative. The survival fraction is not an ambient concentration estimate.
    const count = Math.min(180, Math.max(32, points.length * 8))
    for (let index = 0; index < count && points.length > 1; index++) {
      const p = sample(index / (count - 1), index * 2.399)
      const radius = Math.min(220, Math.max(14, p.width * (.38 + .16 * Math.sin(index * 1.7))))
      const gradient = cloudContext.createRadialGradient(p.x, p.y, 0, p.x, p.y, radius)
      gradient.addColorStop(0, survivalColor(p.fraction, .20))
      gradient.addColorStop(.55, survivalColor(p.fraction, .105))
      gradient.addColorStop(1, survivalColor(p.fraction, 0))
      cloudContext.fillStyle = gradient
      cloudContext.fillRect(p.x - radius, p.y - radius, radius * 2, radius * 2)
    }
  }

  function sample(progress: number, phase: number) {
    const position = Math.min(.999999, Math.max(0, progress)) * (points.length - 1)
    const index = Math.floor(position)
    const a = points[index], b = points[Math.min(index + 1, points.length - 1)]
    const t = position - index
    const dx = b.x - a.x, dy = b.y - a.y
    const length = Math.hypot(dx, dy) || 1
    const spread = a.width + (b.width - a.width) * t
    const offset = spread * .62 * Math.sin(phase) * Math.sin(Math.PI * progress / 2)
    const fraction = a.fraction !== null && b.fraction !== null
      ? a.fraction + (b.fraction - a.fraction) * t : null
    return { fraction, x: a.x + dx * t - dy / length * offset, y: a.y + dy * t + dx / length * offset, width: spread }
  }

  function draw(time: number) {
    frame = 0
    if (!prediction || document.hidden) { previousTime = 0; return }
    if (time - lastFrame < 40 && !dirty) { start(); return }
    if (previousTime) elapsed += Math.min(time - previousTime, 100)
    previousTime = time
    lastFrame = time
    if (dirty) { rebuild(); dirty = false }
    if (!width || !height) return
    context.clearRect(0, 0, width, height)
    context.drawImage(cloud, 0, 0, width, height)
    if (!motion.matches && points.length > 1) {
      context.lineCap = 'round'
      for (let index = 0; index < 72; index++) {
        const progress = (index / 72 + elapsed / (15000 + index % 5 * 1600)) % 1
        const phase = index * 2.399 + .25 * Math.sin(elapsed / 3500 + index)
        const head = sample(progress, phase)
        const tail = sample(Math.max(0, progress - .018), phase)
        context.strokeStyle = survivalColor(head.fraction, .65 * Math.sin(Math.PI * progress))
        context.lineWidth = 1.3
        context.beginPath()
        context.moveTo(tail.x, tail.y)
        context.lineTo(head.x, head.y)
        context.stroke()
      }
    }
    context.globalCompositeOperation = 'destination-in'
    context.drawImage(mask, 0, 0, width, height)
    context.globalCompositeOperation = 'source-over'
    if (!motion.matches) start()
  }
  function start() { if (!frame && prediction) frame = requestAnimationFrame(draw) }
  const observer = new ResizeObserver(invalidate)
  observer.observe(canvas)
  map.on('bounds_changed', invalidate)
  motion.addEventListener('change', invalidate)
  document.addEventListener('visibilitychange', invalidate)
  return {
    update(value: MeteorologyPathPrediction | null, species = '') {
      prediction = value
      pollutant = species
      if (!value) {
        cancelAnimationFrame(frame)
        frame = 0
        previousTime = 0
        context.clearRect(0, 0, width, height)
      } else invalidate()
    },
    destroy() {
      cancelAnimationFrame(frame)
      observer.disconnect()
      map.off('bounds_changed', invalidate)
      motion.removeEventListener('change', invalidate)
      document.removeEventListener('visibilitychange', invalidate)
    }
  }
}

/** Missing values stay neutral; zero is a valid lower bound, never "missing". */
function survivalColor(fraction: number | null, alpha: number): string {
  if (fraction === null) return `rgba(148,163,184,${alpha})`
  const f = Math.max(0, Math.min(1, fraction))
  return `rgba(${Math.round(255 - 45 * f)},${Math.round(244 - 104 * f)},${Math.round(160 * (1 - f))},${alpha})`
}
