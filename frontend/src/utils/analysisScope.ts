import type { AnalysisRequest } from '@/types/analysis'
import type { AIRequestContext } from '@/types/aiChat'

export type AnalysisScope = Pick<
  AIRequestContext,
  'station_id' | 'station_name' | 'start_time' | 'end_time' | 'lat' | 'lon'
>

const STATION_ALIASES: Record<string, string> = {
  '旺角': 'MONG KOK', '铜锣湾': 'CAUSEWAY BAY', '中环': 'CENTRAL',
  '中西区': 'CENTRAL WESTERN', '东区': 'EASTERN', '葵涌': 'KWAI CHUNG',
  '观塘': 'KWUN TONG', '北区': 'NORTH', '深水埗': 'SHAM SHUI PO',
  '沙田': 'SHATIN', '南区': 'SOUTHERN', '大埔': 'TAI PO', '塔门': 'TAP MUN',
  '将军澳': 'TSEUNG KWAN O', '荃湾': 'TSUEN WAN', '屯门': 'TUEN MUN',
  '东涌': 'TUNG CHUNG', '元朗': 'YUEN LONG'
}

function normalizedToken(value: unknown): string {
  return String(value ?? '').trim().toUpperCase()
    .replace(/\bROADSIDE\b/g, '').replace(/[\\/_-]+/g, ' ')
    .replace(/[^A-Z0-9\u3400-\u9FFF]+/g, ' ').replace(/\s+/g, ' ').trim()
}

export function canonicalStationName(value: unknown): string {
  const token = normalizedToken(value)
  return STATION_ALIASES[token] ?? token
}

function canonicalStationId(value: unknown): string {
  const token = normalizedToken(value)
  const aliases: Record<string, string> = {
    MK: 'MONG KOK', 'MONG KOK': 'MONG KOK',
    'CAUSEWAY BAY': 'CAUSEWAY BAY', CENTRAL: 'CENTRAL'
  }
  return aliases[token] ?? token
}

/** Compare station identity without merging two genuinely different stations. */
export function sameStation(left: AnalysisScope | null, right: AnalysisScope | null): boolean {
  if (!left || !right) return false
  const leftName = canonicalStationName(left.station_name)
  const rightName = canonicalStationName(right.station_name)
  if (leftName && rightName) return leftName === rightName
  const leftId = canonicalStationId(left.station_id)
  const rightId = canonicalStationId(right.station_id)
  return Boolean(leftId && rightId && leftId === rightId)
}

function normalizedTime(value: unknown): string {
  const text = String(value ?? '').trim().replace('T', ' ')
  const match = text.match(/^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})(?::(\d{2}))?/)
  return match ? `${match[1]} ${match[2]}:${match[3] ?? '00'}` : text
}

export function sameAnalysisScope(left: AnalysisScope | null | undefined, right: AnalysisScope | null | undefined): boolean {
  if (!left || !right || !sameStation(left, right)) return false
  return normalizedTime(left.start_time) === normalizedTime(right.start_time)
    && normalizedTime(left.end_time) === normalizedTime(right.end_time)
}

export function toAnalysisScope(value: AnalysisRequest | AIRequestContext | null): AnalysisScope | null {
  if (!value?.station_name || !value.start_time || !value.end_time) return null
  return {
    station_id: value.station_id, station_name: value.station_name,
    start_time: normalizedTime(value.start_time), end_time: normalizedTime(value.end_time),
    lat: value.lat, lon: value.lon
  }
}

export function formatAnalysisScope(value: AnalysisScope | null | undefined): string {
  if (!value) return '未确定'
  const station = String(value.station_name || value.station_id || '未知站点')
  return `${station} · ${normalizedTime(value.start_time)} 至 ${normalizedTime(value.end_time)}`
}
