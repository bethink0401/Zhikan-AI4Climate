import { describe, expect, it } from 'vitest'
import { initialPropagationDirection } from '../propagationDirection'
import type { MeteorologyPathPrediction } from '@/types/meteorology'

function path(coordinates: number[][]): MeteorologyPathPrediction {
  return { status: 'ok', method: 'test', steps: [], path_geojson: { type: 'LineString', coordinates } }
}

describe('computed propagation direction', () => {
  it('follows the first path segment and ignores a conflicting step wind vector', () => {
    const prediction = path([[114, 22], [114.01, 22], [114.01, 22.01]])
    prediction.steps = [{ wind_to_direction: 270 }] as MeteorologyPathPrediction['steps']
    expect(initialPropagationDirection(prediction)).toBeCloseTo(90, 1)
  })

  it('preserves due north and skips stationary leading segments', () => {
    expect(initialPropagationDirection(path([[114, 22], [114, 22], [114, 22.01]]))).toBe(0)
  })

  it('does not invent a direction when the path is missing, stationary or unavailable', () => {
    expect(initialPropagationDirection(null)).toBeNull()
    expect(initialPropagationDirection(path([[114, 22], [114, 22]]))).toBeNull()
    expect(initialPropagationDirection({ ...path([[114, 22], [114.01, 22]]), status: 'unavailable' })).toBeNull()
  })
})
