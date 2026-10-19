<template>
  <div class="chat-panel" :class="{ 'is-busy': busy }">
    <div class="panel-header">
      <span class="header-icon">
        <el-icon :size="16"><ChatDotRound /></el-icon>
      </span>
      <div class="header-texts">
        <div class="header-title-row">
          <span class="panel-title">智能气象研判助手</span>
          <el-tag class="live-tag" size="small" type="success" effect="light">● 在线研判</el-tag>
        </div>
        <div class="header-sub">数据协同与语义联动 Agent</div>
      </div>
    </div>

    <div ref="bodyRef" class="chat-body">
      <!-- 实时趋势播报：基于当前链路结果自动生成的头条摘要 -->
      <div v-if="broadcast" class="broadcast-card">
        <div class="broadcast-head">
          <el-icon :size="13" color="#f59e0b"><WarningFilled /></el-icon>
          <span>实时趋势播报</span>
        </div>
        <p class="broadcast-text">{{ broadcast }}</p>
      </div>

      <div
        v-for="msg in messages"
        :key="msg.id"
        class="msg"
        :class="msg.role"
      >
        <div class="avatar">
          <el-icon v-if="msg.role === 'assistant'"><Service /></el-icon>
          <el-icon v-else><User /></el-icon>
        </div>
        <div
          class="bubble"
          :class="{
            'has-chart': msg.chart || msg.systemChart,
            // 系统图表（行式列表/四联图）信息密度高，统一放开到整行宽
            'is-wide-chart': !!msg.systemChart
          }"
        >
          <p class="text">{{ msg.text }}</p>
          <!-- 优先调出系统自带的图表组件；没有系统图表时才用轻量内联图 -->
          <ChatSystemChart
            v-if="msg.systemChart"
            :kind="msg.systemChart.kind"
            :hourly="msg.systemChart.hourly"
          />
          <ChatInlineChart v-else-if="msg.chart" :spec="msg.chart" />
          <div v-if="msg.chips && msg.chips.length" class="chips">
            <span
              v-for="chip in msg.chips"
              :key="chip"
              class="chip"
              @click="ask(chip)"
            >
              {{ chip }}
            </span>
          </div>
          <div v-if="msg.footnote" class="footnote">{{ msg.footnote }}</div>
        </div>
      </div>

      <div v-if="pending" class="msg assistant">
        <div class="avatar">
          <el-icon><Service /></el-icon>
        </div>
        <div class="bubble">
          <span class="typing"><i></i><i></i><i></i></span>
          <span class="typing-text">正在识别气象信息并联动视图…</span>
        </div>
      </div>
    </div>

    <div class="chat-input">
      <div class="quick-chips">
        <span
          v-for="chip in quickChips"
          :key="chip"
          class="chip"
          @click="ask(chip)"
        >
          {{ chip }}
        </span>
      </div>
      <div class="input-row">
        <el-input
          v-model="input"
          placeholder="输入问询属性，或直接输入指令…（Enter 发送）"
          :disabled="busy"
          @keyup.enter="ask(input)"
        />
        <el-button type="primary" :loading="busy" @click="ask(input)">
          <el-icon v-if="!busy"><Promotion /></el-icon>
          <span>{{ busy ? '分析中' : '发送' }}</span>
        </el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { ChatDotRound, Promotion, Service, User, WarningFilled } from '@element-plus/icons-vue'
import ChatInlineChart, { type ChatChartSpec } from './ChatInlineChart.vue'
import ChatSystemChart, { type SystemChartKind } from './ChatSystemChart.vue'
import { useAnalysisStore } from '@/stores/analysis'
import { fetchMeteorologyHourly } from '@/api/meteorology'
import { fetchAiAnswer } from '@/api/chat'
import { getVehicleLabel } from '@/utils/vehicleMapper'
import type { MeteorologyHourlyItem } from '@/types/meteorology'

defineOptions({ name: 'ChatQAPanel' })

const emit = defineEmits<{ (e: 'focus-section', topic: string): void }>()

const store = useAnalysisStore()

// ── 类型 ────────────────────────────────────────────────────────────────
type Topic =
  | 'temperature'
  | 'wind_speed'
  | 'wind_direction'
  | 'humidity'
  | 'precipitation'
  | 'cloud_cover'
  | 'weather_risk'
  | 'air_quality'
  | 'spatial'
  | 'meteo_trend'
  | 'source_attribution'
  | 'vehicle_structure'
  | 'overview'
  | 'greeting'
  | 'unknown'

type NumField =
  | 'wind_speed'
  | 'wind_direction'
  | 'humidity'
  | 'precipitation'
  | 'temperature'
  | 'cloud_cover'

/** 调起一张系统自带图表所需的载荷 */
interface SystemChartPick {
  kind: SystemChartKind
  hourly?: MeteorologyHourlyItem[]
}

interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  text: string
  chart?: ChatChartSpec
  systemChart?: SystemChartPick
  chips?: string[]
  footnote?: string
  /** 本条回答是否来自真 AI（后端 /api/chat） */
  viaAi?: boolean
}

/** 统一的气象视图口径：既兼容整链路结果，也兼容小时序列接口的降级数据 */
interface MeteoView {
  stationName: string
  startTime: string
  endTime: string
  hourly: MeteorologyHourlyItem[]
  meanTemperature: number | null
  meanWindSpeed: number | null
  meanHumidity: number | null
  totalPrecipitation: number | null
  meanCloudCover: number | null
  dominantWindDirection: number | null
  windLabel: string | null
  upwindLabel: string | null
  downwindLabel: string | null
  weatherRisk: number | null
  riskType: string | null
  dispersion: string | null
  scores: number[]
  hasRiskScores: boolean
}

interface Answer {
  text: string
  chart?: ChatChartSpec
  systemChart?: SystemChartPick
  chips?: string[]
  topic: Topic
  /** 来自真 AI 的回答（footnote 会标注模型） */
  viaAi?: boolean
  aiModel?: string
}

// ── 常量 ────────────────────────────────────────────────────────────────
const DIR_LABELS = ['北', '东北', '东', '东南', '南', '西南', '西', '西北']

const quickChips = [
  '气象趋势',
  '风速风向',
  '湿度分析',
  '逐小时气象趋势',
  '污染来源评分',
  '车型结构'
]

const TOPIC_KEYWORDS: { topic: Topic; words: string[] }[] = [
  // ── 先匹配"调系统图表"的意图，避免被下方的要素词劫持 ──
  {
    topic: 'meteo_trend',
    words: ['逐小时气象', '逐小时趋势', '气象趋势', '四项趋势', '气象四联', '四联图', '风速湿度', '气象总览图', '综合趋势图']
  },
  {
    topic: 'source_attribution',
    words: ['来源', '溯源', '归因', '可能性评分', '污染源', '哪个源', '什么源', '来源评分']
  },
  {
    topic: 'vehicle_structure',
    words: ['车型', '车种', '车辆结构', '车辆构成', '货车', '巴士', '私家车', '摩托车', '车辆占比']
  },
  { topic: 'wind_direction', words: ['风向', '上风', '下风', '吹向', '哪个方向', '方位', '往哪吹'] },
  { topic: 'wind_speed', words: ['风速', '风力', '风大', '几级风', '多大风'] },
  { topic: 'temperature', words: ['气温', '温度', '多少度', '冷', '热'] },
  { topic: 'humidity', words: ['湿度', '潮湿', '干燥', '闷'] },
  { topic: 'precipitation', words: ['降水', '降雨', '下雨', '雨量', '雨水', '有没有雨'] },
  { topic: 'cloud_cover', words: ['云量', '云层', '多云', '阴天'] },
  { topic: 'weather_risk', words: ['气象风险', '扩散条件', '扩散', '气象条件', '风险'] },
  { topic: 'air_quality', words: ['空气质量', '污染', 'aqhi', 'aqi', '污染物', '超标', 'pm2.5', 'pm25'] },
  { topic: 'spatial', words: ['空间', '敏感点', '影响范围', '半径', '传播', '上风向道路', '受影响'] },
  { topic: 'overview', words: ['综合', '总体', '整体', '概况', '态势', '整体情况'] }
]

const TREND_WORDS = ['趋势', '变化', '走势', '过程', '逐小时', '未来', '预测', '曲线', '怎么变', '如何变']
const GREETING_WORDS = ['你好', '您好', 'hi', 'hello', '在吗', '嗨', 'hey']

// ── 状态 ────────────────────────────────────────────────────────────────
const messages = ref<ChatMessage[]>([])
const input = ref('')
const busy = ref(false)
const pending = ref(false)
const bodyRef = ref<HTMLDivElement | null>(null)

let seq = 0
const nextId = () => ++seq

let meteoPromise: Promise<MeteoView | null> | null = null

// ── 工具函数 ────────────────────────────────────────────────────────────
function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function num(value: unknown): number | null {
  return typeof value === 'number' && !Number.isNaN(value) ? value : null
}

function fmt(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const rounded = Math.round(value * 10 ** digits) / 10 ** digits
  return `${rounded}`
}

function hhmm(time: string): string {
  const matched = time.match(/(\d{2}):(\d{2})/)
  return matched ? `${matched[1]}:${matched[2]}` : time
}

function dirLabel(deg: number | null | undefined): string | null {
  if (deg === null || deg === undefined || Number.isNaN(deg)) return null
  const index = Math.round((((deg % 360) + 360) % 360) / 45) % 8
  return DIR_LABELS[index]
}

function valuesOf(series: MeteorologyHourlyItem[], field: NumField): number[] {
  return series
    .map((row) => num(row[field]))
    .filter((value): value is number => value !== null)
}

function average(values: number[]): number | null {
  if (!values.length) return null
  return values.reduce((sum, value) => sum + value, 0) / values.length
}

function trendOf(values: number[]): string {
  if (values.length < 2) return '数据点不足，暂无法判断趋势'
  const first = values[0]
  const last = values[values.length - 1]
  const span = Math.max(...values) - Math.min(...values)
  const diff = last - first
  if (span > 0 && Math.abs(diff) < span * 0.15) return '整体基本平稳'
  if (Math.abs(diff) < 1e-9) return '整体基本平稳'
  return diff > 0 ? '整体呈上升趋势' : '整体呈下降趋势'
}

function windRoseCounts(series: MeteorologyHourlyItem[]): number[] {
  const counts = new Array(8).fill(0)
  for (const row of series) {
    const deg = num(row.wind_direction)
    if (deg === null) continue
    counts[Math.round((((deg % 360) + 360) % 360) / 45) % 8] += 1
  }
  return counts
}

function sourceLabel(): string {
  if (store.analysisSource === 'api') return '实时链路数据'
  if (store.analysisSource === 'snapshot') return '离线快照数据'
  return '本地面板数据'
}

function timeRange(view: MeteoView): string {
  return `${view.startTime} ~ ${view.endTime}`
}

/** 把 '0800-0900' 这类时段串格式化成 '08:00 ~ 09:00' */
function formatPeakSlot(slot: string | undefined | null): string {
  if (!slot) return ''
  const parts = slot.split('-')
  const fmt = (t: string): string => (t.length === 4 ? `${t.slice(0, 2)}:${t.slice(2)}` : t)
  return parts.map(fmt).join(' ~ ')
}

/**
 * 实时趋势播报：从当前链路结果里自动提炼一条头条摘要。
 * 静稳 + 微风 + 下风向学校密集 + 早高峰，四项都有真实数据支撑才拼出全文。
 */
const broadcast = computed<string | null>(() => {
  const meteo = store.result?.meteorology?.result
  if (!meteo) return null

  const wind = meteo.mean_wind_speed
  const stagnant = (meteo.static_wind_score ?? 0) >= 0.5 && wind !== null && wind !== undefined && wind < 2
  const school = store.result?.spatial?.result?.sensitive_poi_by_type?.school
  const peak = formatPeakSlot(store.result?.traffic?.meta?.peak_time_slot)

  const parts: string[] = []
  if (wind !== null && wind !== undefined) {
    parts.push(`静风潜势点当前风速 ${wind.toFixed(1)} m/s${stagnant ? '（偏静稳）' : ''}`)
  }
  if (school) {
    parts.push(`下风向高密度区内覆盖 ${school} 处学校`)
  }
  if (peak) {
    parts.push(`建议优先关注早高峰（${peak}）排气累积`)
  }
  return parts.length >= 2 ? `${parts.join('，')}。` : null
})

// ── 气象数据获取（优先整链路结果，缺失时降级调用小时序列接口） ──────────
function viewFromAnalysis(): MeteoView | null {
  const result = store.result?.meteorology?.result
  if (!result) return null
  const scores = [
    result.static_wind_score,
    result.wind_shift_score,
    result.humidity_risk_score,
    result.rain_removal_score,
    result.insufficient_rain_removal_score
  ].filter((value): value is number => typeof value === 'number')
  return {
    stationName: result.station_name || store.currentTask?.station_name || '当前站点',
    startTime: result.start_time,
    endTime: result.end_time,
    hourly: result.hourly_series || [],
    meanTemperature: num(result.mean_temperature),
    meanWindSpeed: num(result.mean_wind_speed),
    meanHumidity: num(result.mean_humidity),
    totalPrecipitation: num(result.total_precipitation),
    meanCloudCover: num(result.mean_cloud_cover),
    dominantWindDirection: num(result.dominant_wind_direction),
    windLabel: result.dominant_wind_direction_label || dirLabel(result.dominant_wind_direction),
    upwindLabel: dirLabel(result.upwind_direction),
    downwindLabel: dirLabel(result.downwind_direction),
    weatherRisk: num(result.weather_risk),
    riskType: (result.risk_type as string) ?? null,
    dispersion: result.dispersion_condition ?? null,
    scores,
    hasRiskScores: scores.length >= 3
  }
}

function viewFromHourly(records: MeteorologyHourlyItem[]): MeteoView {
  const task = store.currentTask
  const meanDir = (() => {
    const dirs = valuesOf(records, 'wind_direction')
    if (!dirs.length) return null
    const radians = dirs.map((deg) => (deg * Math.PI) / 180)
    const x = average(radians.map(Math.cos)) ?? 0
    const y = average(radians.map(Math.sin)) ?? 0
    return (((Math.atan2(y, x) * 180) / Math.PI) + 360) % 360
  })()
  return {
    stationName: task?.station_name || '当前站点',
    startTime: records[0]?.time || task?.start_time || '',
    endTime: records[records.length - 1]?.time || task?.end_time || '',
    hourly: records,
    meanTemperature: average(valuesOf(records, 'temperature')),
    meanWindSpeed: average(valuesOf(records, 'wind_speed')),
    meanHumidity: average(valuesOf(records, 'humidity')),
    totalPrecipitation: (() => {
      const values = valuesOf(records, 'precipitation')
      return values.length ? values.reduce((sum, value) => sum + value, 0) : null
    })(),
    meanCloudCover: average(valuesOf(records, 'cloud_cover')),
    dominantWindDirection: meanDir,
    windLabel: dirLabel(meanDir),
    upwindLabel: null,
    downwindLabel: null,
    weatherRisk: null,
    riskType: null,
    dispersion: null,
    scores: [],
    hasRiskScores: false
  }
}

async function resolveMeteo(): Promise<MeteoView | null> {
  const fromAnalysis = viewFromAnalysis()
  if (fromAnalysis) return fromAnalysis
  if (meteoPromise) return meteoPromise

  const task = store.currentTask
  if (!task) return null

  meteoPromise = (async () => {
    try {
      const response = await fetchMeteorologyHourly({
        lat: task.lat,
        lon: task.lon,
        start_time: task.start_time,
        end_time: task.end_time,
        station_id: task.station_id
      })
      const records = response.records || []
      return records.length ? viewFromHourly(records) : null
    } catch {
      return null
    } finally {
      meteoPromise = null
    }
  })()

  return meteoPromise
}

// ── 真 AI 问答（后端 /api/chat，SiliconFlow Qwen3-32B） ─────────────────
/**
 * 从当前链路结果里提炼一份紧凑的结构化上下文发给大模型。
 * 不能整包发 store.result —— 里面带着空间 GeoJSON 等重资产，几十万 token 会撑爆请求。
 */
function buildAiContext(): Record<string, unknown> | null {
  const result = store.result
  if (!result) return null
  const air = result.air_quality?.result
  const meteo = result.meteorology?.result
  const traffic = result.traffic?.result
  const spatial = result.spatial?.result
  const source = result.source?.result
  const decision = result.decision?.result

  return {
    task: store.currentTask,
    air_quality: air
      ? {
          anomaly_score: air.anomaly_score,
          pollution_status: air.pollution_status,
          main_pollutants: air.main_pollutants,
          pollutant_avg: Object.fromEntries(
            Object.entries(air.pollutant_details || {}).map(([key, detail]) => [
              key,
              (detail as { avg?: number | null })?.avg ?? null
            ])
          )
        }
      : null,
    meteorology: meteo
      ? {
          weather_risk: meteo.weather_risk,
          risk_type: meteo.risk_type,
          dispersion_condition: meteo.dispersion_condition,
          mean_wind_speed: meteo.mean_wind_speed,
          mean_temperature: meteo.mean_temperature,
          mean_humidity: meteo.mean_humidity,
          total_precipitation: meteo.total_precipitation,
          dominant_wind_direction_label: meteo.dominant_wind_direction_label,
          hourly: (meteo.hourly_series || []).map((row) => ({
            time: row.time,
            temperature: row.temperature,
            humidity: row.humidity,
            wind_speed: row.wind_speed,
            precipitation: row.precipitation
          }))
        }
      : null,
    traffic: traffic
      ? {
          traffic_pressure_score: traffic.traffic_pressure_score,
          traffic_pressure_level: traffic.traffic_pressure_level,
          peak_time_slot: result.traffic?.meta?.peak_time_slot,
          proportions_pct: traffic.proportions_pct
        }
      : null,
    spatial: spatial
      ? {
          analysis_radius_km: spatial.analysis_radius_km,
          sensitive_poi_count: spatial.sensitive_poi_count,
          sensitive_poi_by_type: spatial.sensitive_poi_by_type
        }
      : null,
    source: source
      ? {
          main_judgement: source.main_judgement,
          risk_level: source.risk_level,
          source_attribution: source.source_attribution
        }
      : null,
    decision: decision
      ? { decision_level: decision.decision_level, actions: decision.actions }
      : null
  }
}

/**
 * 自由问题优先交给真 AI。任何网络/配置问题都返回 null，
 * 由调用方回退到本地规则应答，保证对话永不空转。
 */
async function tryAiAnswer(question: string): Promise<Answer | null> {
  try {
    const history = messages.value.slice(-6).map((msg) => ({
      role: msg.role,
      content: msg.text
    }))
    const response = await fetchAiAnswer({
      question,
      history,
      context: buildAiContext()
    })
    if (response.status !== 'ok' || !response.answer) return null
    return {
      text: response.answer,
      topic: 'overview',
      viaAi: true,
      aiModel: response.model
    }
  } catch {
    return null
  }
}

// ── 意图识别 ────────────────────────────────────────────────────────────
function detectTopic(text: string): Topic {
  const lower = text.toLowerCase()
  if (GREETING_WORDS.some((word) => lower.includes(word)) && text.trim().length <= 8) {
    return 'greeting'
  }
  for (const entry of TOPIC_KEYWORDS) {
    if (entry.words.some((word) => lower.includes(word))) return entry.topic
  }
  if (lower.includes('风')) return 'wind_speed'
  if (lower.includes('天气') || lower.includes('气象')) return 'overview'
  return 'unknown'
}

function wantsTrend(text: string): boolean {
  const lower = text.toLowerCase()
  return TREND_WORDS.some((word) => lower.includes(word))
}

// ── 回答构造 ────────────────────────────────────────────────────────────
function noDataAnswer(): Answer {
  return {
    text: '暂时没有取到该时段的气象数据。请先在上方任务栏点「开始分析」跑一遍链路，或确认后端服务已启动，我就能基于真实数据回答并出图。',
    topic: 'overview'
  }
}

function attributeAnswer(
  topic: Topic,
  text: string,
  chart: ChatChartSpec
): Answer {
  return { text, chart, topic }
}

function buildAnswer(topic: Topic, trend: boolean, meteo: MeteoView | null): Answer {
  const needsMeteo = ['temperature', 'wind_speed', 'wind_direction', 'humidity', 'precipitation', 'cloud_cover', 'weather_risk', 'overview']
  if (needsMeteo.includes(topic) && !meteo) return noDataAnswer()

  const head = meteo ? `【${meteo.stationName}｜${timeRange(meteo)}】` : ''

  switch (topic) {
    case 'temperature': {
      if (!meteo) return noDataAnswer()
      const values = valuesOf(meteo.hourly, 'temperature')
      const mean = meteo.meanTemperature ?? average(values)
      const range = values.length ? `，逐小时区间 ${fmt(Math.min(...values))}–${fmt(Math.max(...values))} °C` : ''
      const text = `${head}平均气温 ${fmt(mean)} °C${range}。${trend ? trendOf(values) + '。' : ''}`
      return attributeAnswer(topic, text, {
        type: 'line',
        title: '气温逐小时变化',
        unit: '°C',
        categories: meteo.hourly.map((row) => hhmm(row.time)),
        series: [{ name: '气温', data: meteo.hourly.map((row) => num(row.temperature)), color: '#e6a23c' }]
      })
    }

    case 'wind_speed': {
      if (!meteo) return noDataAnswer()
      const values = valuesOf(meteo.hourly, 'wind_speed')
      const mean = meteo.meanWindSpeed ?? average(values)
      const dispersion = meteo.dispersion ? `当前扩散条件「${meteo.dispersion}」。` : ''
      const text = `${head}平均风速 ${fmt(mean)} m/s${values.length ? `（区间 ${fmt(Math.min(...values))}–${fmt(Math.max(...values))} m/s）` : ''}。${dispersion}${trend ? trendOf(values) + '。' : ''}`
      return attributeAnswer(topic, text, {
        type: 'line',
        title: '风速逐小时变化',
        unit: 'm/s',
        categories: meteo.hourly.map((row) => hhmm(row.time)),
        series: [{ name: '风速', data: meteo.hourly.map((row) => num(row.wind_speed)), color: '#409eff' }]
      })
    }

    case 'wind_direction': {
      if (!meteo) return noDataAnswer()
      const counts = windRoseCounts(meteo.hourly)
      const directionText = meteo.windLabel
        ? `主导风向为「${meteo.windLabel}」${meteo.dominantWindDirection !== null ? `（${fmt(meteo.dominantWindDirection, 0)}°）` : ''}`
        : '主导风向暂缺'
      const windText = [
        meteo.upwindLabel ? `上风向来自「${meteo.upwindLabel}」` : '',
        meteo.downwindLabel ? `下风向指向「${meteo.downwindLabel}」` : ''
      ].filter(Boolean).join('，')
      const text = `${head}${directionText}${windText ? '；' + windText : ''}。下图是各风向出现的小时数分布。`
      return attributeAnswer(topic, text, {
        type: 'bar',
        title: '风向分布（小时数）',
        unit: 'h',
        categories: DIR_LABELS,
        series: [{ name: '出现小时数', data: counts, color: '#67c23a' }]
      })
    }

    case 'humidity': {
      if (!meteo) return noDataAnswer()
      const values = valuesOf(meteo.hourly, 'humidity')
      const mean = meteo.meanHumidity ?? average(values)
      const text = `${head}平均相对湿度 ${fmt(mean)} %${values.length ? `（区间 ${fmt(Math.min(...values), 0)}–${fmt(Math.max(...values), 0)} %）` : ''}。${trend ? trendOf(values) + '。' : ''}`
      return attributeAnswer(topic, text, {
        type: 'line',
        title: '相对湿度逐小时变化',
        unit: '%',
        categories: meteo.hourly.map((row) => hhmm(row.time)),
        series: [{ name: '湿度', data: meteo.hourly.map((row) => num(row.humidity)), color: '#909399' }]
      })
    }

    case 'precipitation': {
      if (!meteo) return noDataAnswer()
      const values = valuesOf(meteo.hourly, 'precipitation')
      const total = meteo.totalPrecipitation ?? (values.length ? values.reduce((sum, value) => sum + value, 0) : null)
      const text = `${head}时段累计降水 ${fmt(total)} mm${values.length ? `，单小时最大 ${fmt(Math.max(...values))} mm` : ''}。降水对颗粒物有清除作用。`
      return attributeAnswer(topic, text, {
        type: 'bar',
        title: '逐小时降水量',
        unit: 'mm',
        categories: meteo.hourly.map((row) => hhmm(row.time)),
        series: [{ name: '降水', data: meteo.hourly.map((row) => num(row.precipitation)), color: '#409eff' }]
      })
    }

    case 'cloud_cover': {
      if (!meteo) return noDataAnswer()
      const values = valuesOf(meteo.hourly, 'cloud_cover')
      const mean = meteo.meanCloudCover ?? average(values)
      const text = `${head}平均云量 ${fmt(mean)} %${values.length ? `（区间 ${fmt(Math.min(...values), 0)}–${fmt(Math.max(...values), 0)} %）` : ''}。${trend ? trendOf(values) + '。' : ''}`
      return attributeAnswer(topic, text, {
        type: 'line',
        title: '云量逐小时变化',
        unit: '%',
        categories: meteo.hourly.map((row) => hhmm(row.time)),
        series: [{ name: '云量', data: meteo.hourly.map((row) => num(row.cloud_cover)), color: '#909399' }]
      })
    }

    case 'weather_risk': {
      if (!meteo) return noDataAnswer()
      const riskText = meteo.weatherRisk !== null ? `气象风险指数 ${fmt(meteo.weatherRisk)}` : '气象风险指数暂缺'
      const level = meteo.riskType ? `，等级「${riskLevelText(meteo.riskType)}」` : ''
      const dispersion = meteo.dispersion ? `，扩散条件「${meteo.dispersion}」` : ''
      const text = `${head}${riskText}${level}${dispersion}。下方雷达图给出静风、风向切变、湿度、雨洗等分项评分。`
      const scores = meteo.scores
      const maxValue = Math.max(...scores, 0)
      const axisMax = maxValue <= 1.001 ? 1 : 100
      if (!meteo.hasRiskScores) {
        return attributeAnswer(topic, text, {
          type: 'line',
          title: '风速逐小时变化（风险代理指标）',
          unit: 'm/s',
          categories: meteo.hourly.map((row) => hhmm(row.time)),
          series: [{ name: '风速', data: meteo.hourly.map((row) => num(row.wind_speed)), color: '#409eff' }]
        })
      }
      return attributeAnswer(topic, text, {
        type: 'radar',
        title: '气象风险分项评分',
        indicators: [
          { name: '静风', max: axisMax },
          { name: '风向切变', max: axisMax },
          { name: '湿度', max: axisMax },
          { name: '雨洗', max: axisMax },
          { name: '雨洗不足', max: axisMax }
        ],
        radarValues: scores
      })
    }

    case 'air_quality': {
      const air = store.result?.air_quality?.result
      if (!air) {
        return {
          text: '暂时没有取到空气质量结果。请先点「开始分析」跑一遍链路，我就能给出 AQHI、污染物与异常评分，并画出各污染物均值对比图。',
          topic
        }
      }
      const details = air.pollutant_details
      const labels = ['NO2', 'NOx', 'PM2.5', 'PM10', 'O3', 'SO2', 'CO']
      const values = [
        details?.no2?.avg, details?.nox?.avg, details?.pm25?.avg, details?.pm10?.avg,
        details?.o3?.avg, details?.so2?.avg, details?.co?.avg
      ].map((value) => num(value))
      const level = air.aqhi_level ? aqhiText(air.aqhi_level) : '未知'
      const pollutants = air.main_pollutants?.length ? air.main_pollutants.join('、') : '暂无'
      const text = `空气质量等级「${level}」，平均 AQHI ${fmt(air.avg_aqhi)}，主要污染物：${pollutants}，污染异常评分 ${fmt(air.anomaly_score)}。`
      return attributeAnswer(topic, text, {
        type: 'bar',
        title: '各污染物窗口均值',
        unit: 'µg/m³',
        categories: labels,
        series: [{ name: '均值', data: values, color: '#f56c6c' }],
        note: 'CO 单位与其他项不同，此处仅作数量级参考'
      })
    }

    case 'spatial': {
      const spatial = store.result?.spatial?.result
      if (!spatial) {
        return {
          text: '暂时没有取到空间分析结果。请先点「开始分析」跑一遍链路，我就能给出影响半径、敏感点数量与上风向道路，并画出敏感点类型分布图。',
          topic
        }
      }
      const poi = spatial.sensitive_poi_by_type || ({} as Record<string, number>)
      const text = `分析半径 ${fmt(spatial.analysis_radius_km, 0)} km，受影响敏感点 ${spatial.sensitive_poi_count ?? 0} 个，上风向道路 ${spatial.upwind_roads?.length ?? 0} 条，风向按「${spatial.wind_direction_interpreted_as || '—'}」解释。`
      return attributeAnswer(topic, text, {
        type: 'bar',
        title: '敏感点类型分布（个）',
        unit: '个',
        categories: ['轨道交通', '社区服务', '医疗', '学校', '社区中心'],
        series: [
          {
            name: '数量',
            data: [num(poi.rail_transit), num(poi.community_service), num(poi.healthcare), num(poi.school), num(poi.community_centre)],
            color: '#409eff'
          }
        ]
      })
    }

    case 'meteo_trend': {
      const series = meteo?.hourly ?? []
      const text = series.length
        ? `${head}已调出系统同款「逐小时气象趋势」四联图：风速、湿度、降雨、温度，共 ${series.length} 个时次。`
        : '暂时没有取到逐小时序列。请先点「开始分析」跑一遍链路，或确认后端服务在线，我再把四联图调出来。'
      return {
        text,
        topic,
        systemChart: {
          kind: 'meteo-trend',
          hourly: series.length ? series : undefined
        }
      }
    }

    case 'source_attribution': {
      const source = store.result?.source?.result
      if (!source) {
        return {
          text: '暂时没有取到溯源结果。请先点「开始分析」跑一遍链路，我就能调出系统同款的「污染来源可能性评分」图。',
          topic
        }
      }
      const attr = source.source_attribution
      const ranked = [
        { name: '交通源', value: num(attr?.traffic_source) ?? 0 },
        { name: '局地积聚', value: num(attr?.local_accumulation) ?? 0 },
        { name: '区域传输', value: num(attr?.regional_transport) ?? 0 },
        { name: '道路扬尘', value: num(attr?.dust_source) ?? 0 },
        { name: '光化学', value: num(attr?.photochemical_source) ?? 0 }
      ].sort((a, b) => b.value - a.value)
      const top = ranked[0]
      const text = `主要来源判断：${source.main_judgement || '—'}；风险等级「${riskLevelText(source.risk_level)}」。可能性评分最高的是「${top.name}」（${fmt(top.value * 100)}/100，百分制）。已调出系统同款「污染来源可能性评分」图。`
      return {
        text,
        topic,
        systemChart: { kind: 'source-probability' }
      }
    }

    case 'vehicle_structure': {
      const traffic = store.result?.traffic?.result
      if (!traffic) {
        return {
          text: '暂时没有取到交通结果。请先点「开始分析」跑一遍链路，我就能调出系统同款的「车型结构分析」图。',
          topic
        }
      }
      const pct = traffic.proportions_pct || ({} as Record<string, number>)
      const top = Object.entries(pct)
        .map(([type, percent]) => ({ label: getVehicleLabel(type), percent: num(percent) ?? 0 }))
        .filter((item) => item.percent > 0)
        .sort((a, b) => b.percent - a.percent)
        .slice(0, 3)
        .map((item) => `${item.label} ${fmt(item.percent)}%`)
        .join('、')
      const text = `交通污染压力「${riskLevelText(traffic.traffic_pressure_level)}」（评分 ${fmt(traffic.traffic_pressure_score)}）。占比最高的车型：${top || '—'}。已调出系统同款「车型结构分析」图。`
      return {
        text,
        topic,
        systemChart: { kind: 'vehicle-structure' }
      }
    }

    case 'overview':
    default: {
      const parts: string[] = []
      if (meteo) {
        parts.push(
          `${head}平均气温 ${fmt(meteo.meanTemperature)} °C、平均风速 ${fmt(meteo.meanWindSpeed)} m/s、平均湿度 ${fmt(meteo.meanHumidity)} %、累计降水 ${fmt(meteo.totalPrecipitation)} mm`
        )
        if (meteo.weatherRisk !== null) {
          parts.push(`气象风险指数 ${fmt(meteo.weatherRisk)}${meteo.riskType ? `（${riskLevelText(meteo.riskType)}）` : ''}`)
        }
        if (meteo.dispersion) parts.push(`扩散条件「${meteo.dispersion}」`)
      }
      const air = store.result?.air_quality?.result
      if (air?.aqhi_level) parts.push(`空气质量「${aqhiText(air.aqhi_level)}」`)
      const decision = store.result?.decision?.result?.decision_level
      if (decision) parts.push(`决策等级「${decisionText(decision)}」`)

      const text = parts.length
        ? `综合态势：${parts.join('；')}。想看哪一项，直接说「气温趋势」「风向」「湿度」等，我会自动出图；也可以说「逐小时气象趋势」「污染来源评分」「车型结构」调出系统自带的图表。`
        : '暂时没有取到分析结果。请先点「开始分析」，我就能汇总气象、空气与决策要点。'
      return { text, topic: 'overview', chips: quickChips.slice(0, 4) }
    }
  }
}

function riskLevelText(level: string): string {
  return ({ low: '低', medium: '中', high: '高', very_high: '极高' } as Record<string, string>)[level] || level
}

function aqhiText(level: string): string {
  return ({ low: '低', moderate: '中', high: '高', very_high: '极高', unknown: '未知' } as Record<string, string>)[level] || level
}

function decisionText(level: string): string {
  return ({ low: '低', medium: '中', high: '高' } as Record<string, string>)[level] || level
}

function greetingAnswer(): Answer {
  return {
    text: '你好，我可以读当前分析的实时数据。问我气温、风速、风向、湿度、降水、云量、气象风险，或空气质量和空间影响，我会把结果连同图表一起显示出来；说「逐小时气象趋势」「污染来源评分」「车型结构」，还能直接调出系统自带的图表。',
    topic: 'greeting',
    chips: quickChips.slice(0, 3)
  }
}

function unknownAnswer(): Answer {
  return {
    text: '这个问题我暂时识别不到对应的气象要素或系统图表。可以换个说法，比如「气温变化趋势」「风速和风向」「湿度多高」「降水情况」「空气质量怎么样」，或「逐小时气象趋势」「污染来源评分」「车型结构」。',
    topic: 'unknown',
    chips: quickChips
  }
}

// ── 交互 ────────────────────────────────────────────────────────────────
function scrollToBottom(): void {
  const el = bodyRef.value
  if (el) el.scrollTop = el.scrollHeight
}

async function ask(question: string): Promise<void> {
  const text = (question ?? '').trim()
  if (!text || busy.value) return

  messages.value.push({ id: nextId(), role: 'user', text })
  input.value = ''
  busy.value = true
  pending.value = true
  await nextTick()
  scrollToBottom()

  // 轻微延迟，让"识别中 → 出图"的过程可感知
  await sleep(320)

  let answer: Answer
  const topic = detectTopic(text)
  try {
    if (topic === 'greeting') {
      answer = greetingAnswer()
    } else if (topic === 'unknown' || topic === 'overview') {
      // 规则引擎接不住的自由问题 / 综合问询：优先走真 AI（结合当前链路上下文），
      // 后端或模型不可达时回退到本地规则应答，保证对话不空转
      const aiAnswer = await tryAiAnswer(text)
      answer =
        aiAnswer ??
        (topic === 'unknown'
          ? unknownAnswer()
          : buildAnswer('overview', wantsTrend(text), await resolveMeteo()))
    } else {
      const meteo = await resolveMeteo()
      answer = buildAnswer(topic, wantsTrend(text), meteo)
    }
  } catch {
    answer = { text: '解析这条问题时出了点状况，请稍后再试一次。', topic: 'unknown' }
  }

  pending.value = false
  const footnote = answer.viaAi
    ? `AI 生成 · ${answer.aiModel || '大模型'} · 数据来源：${sourceLabel()}`
    : answer.chart || answer.systemChart
      ? `数据来源：${sourceLabel()}`
      : undefined
  messages.value.push({
    id: nextId(),
    role: 'assistant',
    text: answer.text,
    chart: answer.chart,
    systemChart: answer.systemChart,
    chips: answer.chips,
    footnote,
    viaAi: answer.viaAi
  })
  busy.value = false
  // 系统图表只在对话气泡里展示：不触发页面联动、也不滚动页面
  if (!answer.systemChart) {
    emit('focus-section', answer.topic)
  }
  await nextTick()
  scrollToBottom()
}

// 开场白
messages.value.push({
  id: nextId(),
  role: 'assistant',
  text: '您好，我是平台气象与交通研判助手，已接入大模型。问我气象属性或变化趋势，我会实时联动左侧地图并自动出图；系统数据之外的自由问题我也会结合当前分析上下文回答；说「逐小时气象趋势」「污染来源评分」「车型结构」，可直接调出专业分析组件。',
  chips: quickChips
})
</script>

<style scoped>
.chat-panel {
  background: var(--panel-bg);
  border-radius: 8px;
  padding: 16px 16px 14px;
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
  min-height: 360px;
  min-width: 0;
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-color);
}

.header-icon {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: linear-gradient(135deg, #3b82f6, #2563eb);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.header-texts {
  min-width: 0;
  flex: 1;
}

.header-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.live-tag {
  margin-left: auto;
  border: none;
  font-weight: 500;
}

.header-sub {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
}

/* ===== 实时趋势播报卡 ===== */
.broadcast-card {
  background: #fffbeb;
  border: 1px solid rgba(245, 158, 11, 0.25);
  border-radius: 10px;
  padding: 10px 12px;
}

.broadcast-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: #b45309;
  margin-bottom: 5px;
}

.broadcast-text {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: #78716c;
  word-break: break-word;
}

.chat-body {
  flex: 1;
  overflow-y: auto;
  padding-right: 4px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 180px;
  max-height: 440px;
  /*
   * 声明成查询容器：气泡和图表里的 cqw 即"对话区宽度的 1%"。
   * 图宽用 cqw 而不是 %，是为了断开"图宽←气泡宽←图宽"的循环依赖
   * （气泡会随内容收缩，百分比宽度在宽度未定的容器里解析不出确定值，echarts 会画出 0 宽画布）。
   */
  container-type: inline-size;
  container-name: chat;
}

.msg {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.msg.user {
  flex-direction: row-reverse;
}

.avatar {
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--theme-color-lighter);
  color: var(--theme-color);
  font-size: 16px;
}

.msg.user .avatar {
  background: #e6f4ea;
  color: #37a35a;
}

.bubble {
  max-width: calc(100% - 44px);
  background: var(--bg-page);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-primary);
  min-width: 0;
}

.msg.user .bubble {
  background: var(--theme-color);
  color: #fff;
}

/*
 * 带图的气泡：宽度随回答内容自适应（短回答窄、长回答宽），
 * 上限给到"对话区约六成"，避免长回答把气泡拉满整栏、右侧留一大片空白。
 * 不支持容器查询的浏览器会忽略这条，回落到 .bubble 的通用 max-width（满宽），属安全降级。
 */
.bubble.has-chart {
  max-width: 58cqw;
}

/* 逐小时气象趋势是四联图，信息密度高，仍让它占满气泡 */
.bubble.has-chart.is-wide-chart {
  max-width: calc(100% - 36px);
  width: 100%;
}

.bubble .text {
  margin: 0;
  word-break: break-word;
  white-space: pre-wrap;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.chip {
  font-size: 12px;
  padding: 2px 9px;
  border-radius: 12px;
  border: 1px solid var(--border-color);
  background: var(--panel-bg);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}

.chip:hover {
  border-color: var(--theme-color);
  color: var(--theme-color);
  background: var(--theme-color-lighter);
}

.footnote {
  margin-top: 6px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.typing {
  display: inline-flex;
  gap: 3px;
  vertical-align: middle;
}

.typing i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--theme-color);
  display: inline-block;
  animation: blink 1.2s infinite ease-in-out both;
}

.typing i:nth-child(2) {
  animation-delay: 0.2s;
}

.typing i:nth-child(3) {
  animation-delay: 0.4s;
}

.typing-text {
  margin-left: 6px;
  color: var(--text-tertiary);
}

@keyframes blink {
  0%,
  80%,
  100% {
    opacity: 0.25;
  }
  40% {
    opacity: 1;
  }
}

.chat-input {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--border-color);
}

.quick-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.input-row {
  display: flex;
  gap: 8px;
}

.input-row :deep(.el-input) {
  flex: 1;
}

.is-busy .chat-body {
  opacity: 0.98;
}
</style>
