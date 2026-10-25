import type { MeteorologyPathPrediction } from '@/types/meteorology'

/** Bearing of the first moving segment of the computed WGS84 path (wind-to).
 * Do not substitute the window mean or the next-hour forecast wind vector:
 * predictor/corrector integration can produce a different path bearing.
 */
export function initialPropagationDirection(prediction: MeteorologyPathPrediction | null | undefined): number | null {
  if (prediction?.status !== 'ok') return null
  const coordinates = prediction.path_geojson?.coordinates ?? []
  for (let index = 1; index < coordinates.length; index++) {
    const [lon1, lat1] = coordinates[index - 1]
    const [lon2, lat2] = coordinates[index]
    if (![lon1, lat1, lon2, lat2].every(Number.isFinite)) continue
    if (Math.abs(lon2 - lon1) + Math.abs(lat2 - lat1) < 1e-9) continue
    const radians = Math.PI / 180
    const phi1 = lat1 * radians, phi2 = lat2 * radians
    const delta = (lon2 - lon1) * radians
    const east = Math.sin(delta) * Math.cos(phi2)
    const north = Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(delta)
    return (Math.atan2(east, north) / radians + 360) % 360
  }
  return null
}
