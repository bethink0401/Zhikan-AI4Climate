<template>
  <MainLayout>
    <!-- 任务查询栏 -->
    <TaskQueryBar />

    <div class="weather-container">
      <!-- 加载状态 -->
      <AnalysisLoading
        :active="analysisStore.loading"
        :task="analysisStore.displayTask"
        :stations="analysisStore.stations"
        :meta="analysisStore.meta"
      />

      <!-- 错误状态 -->
      <div v-if="!analysisStore.loading && analysisStore.error" class="error-state">
        <el-alert
          type="error"
          :title="analysisStore.error"
          :closable="false"
          show-icon
        />
      </div>

      <div v-if="!analysisStore.loading && analysisStore.analysisScopeDirty" class="scope-stale-state">
        <el-alert type="warning" title="分析范围已变化，自动重新分析未完成，可点击「开始分析」重试" :closable="false" show-icon />
      </div>

      <!-- 无数据状态 -->
      <div v-if="!analysisStore.loading && !analysisStore.error && !analysisStore.result" class="empty-state">
        <el-empty description="暂无分析数据，请先提交分析任务" />
      </div>

      <!-- 正常数据展示 -->
      <div v-if="analysisStore.result && !analysisStore.analysisScopeDirty" class="weather-content">

        <!-- 仅在异常状态提醒（后端没连上时才出现） -->
        <p v-if="analysisStore.meteorologySource !== 'api'" class="source-note is-warn">
          <el-icon :size="14"><InfoFilled /></el-icon>
          <template v-if="analysisStore.analysisSource === 'snapshot'">
            <span>
              后端未连接，当前展示的是离线快照（
              {{ analysisStore.analysisError || '后端 /api/analyze 不可达' }}）。
            </span>
          </template>
          <template v-else>
            <span>
              分析数据未取到（{{ analysisStore.analysisError || '未知原因' }}）。
            </span>
          </template>
        </p>

        <!-- ═══════════ 第一屏：主地图（左） + 轨迹推演 / 扩散主控因子（右） ═══════════ -->
        <section class="wp-main">

          <!-- 左：公里级传播主地图 -->
          <div class="map-panel">
            <div class="map-head">
              <span class="map-title">
                <el-icon :size="16"><MapLocation /></el-icon>
                公里级传播风险动态模拟地图
              </span>
              <span class="map-badge">网格：1 km²</span>

              <div class="step-tabs">
                <button
                  v-for="tab in stepTabs"
                  :key="tab.lead"
                  type="button"
                  class="step-tab"
                  :class="{ 'is-active': tab.lead === activeLead }"
                  @click="focusStep(tab.lead)"
                >
                  {{ tab.label }}
                </button>
                <span v-if="!stepTabs.length" class="step-tab is-empty">暂无可用推演时段</span>
              </div>

              <span class="map-res">基础分辨率：1 km</span>
            </div>

            <div class="map-card">
              <SpatialMap
                :spatial="propagationSpatialResult"
                :meteorology="meteorologyResult"
                show-forecast
              />

              <!-- 地图浮层：起算时刻的气象条件 -->
              <div class="map-chips">
                <span class="map-chip">
                  <b>主导风向</b>
                  <span>{{ windLabel }} {{ windDegreeText }}°</span>
                  <em>（{{ windSpeedText }} m/s）</em>
                </span>
                <span class="map-chip">
                  <b>{{ temperatureText }}</b><span>°C</span>
                </span>
                <span class="map-chip">
                  <b>{{ humidityText }}</b><span>%</span>
                  <em>{{ stabilityLabel }}</em>
                </span>
              </div>
            </div>

            <!-- 结论口径条：把"最远可达 / 起始方位 / 时段"压成一行 -->
            <div class="map-stats">
              <div class="stat">
                <span>所选时段跨度</span>
                <b>{{ simulationHorizon }} <small>小时</small></b>
              </div>
              <div class="stat">
                <span>最远可达</span>
                <b>{{ formatNumber(lastStep?.distance_from_origin_km, 1) }} <small>km</small></b>
              </div>
              <div class="stat">
                <span>模拟起始方位</span>
                <b>
                  {{ pathInitialDirection !== null
                    ? formatDegree(pathInitialDirection) + '（' + compassOf(pathInitialDirection) + '）'
                    : '—' }}
                </b>
              </div>
              <div class="stat">
                <span>末端气团留存</span>
                <b>{{ formatPercent(lastStep?.remaining_fraction) }}</b>
              </div>
            </div>
          </div>

          <!-- 右：轨迹推演 + 扩散主控因子 -->
          <div class="side-stack">

            <!-- 逐小时传播演进轨迹推演 -->
            <div class="card">
              <div class="card-head">
                <span class="head-icon"><el-icon :size="14"><Position /></el-icon></span>
                <span class="head-title">逐小时传播演进轨迹推演</span>
                <span class="head-tag" :class="predictionAvailable ? 'is-green' : 'is-gray'">
                  {{ predictionAvailable ? '预测—校正平流' : '轨迹不可用' }}
                </span>
              </div>

              <div v-if="trackCards.length" class="card-body track-list">
                <div
                  v-for="card in trackCards"
                  :id="`wp-step-${card.lead}`"
                  :key="card.lead"
                  class="track-card"
                  :class="[`tone-${card.tone}`, { 'is-focus': card.lead === activeLead }]"
                >
                  <div class="track-head">
                    <span class="track-lead">+{{ card.lead }}h（{{ card.clock }}）</span>
                    <span class="track-place">{{ card.place }}</span>
                    <span class="track-dist">{{ card.distance }}<small>km</small></span>
                  </div>
                  <div class="track-sep"></div>
                  <div class="track-meta">
                    <div class="meta-cell">
                      <span>扩散半宽</span>
                      <b>±{{ card.halfWidth }} km</b>
                    </div>
                    <div class="meta-cell">
                      <span>气团留存率</span>
                      <b>{{ card.remainText }}</b>
                    </div>
                    <div class="meta-cell">
                      <span>输送方位 / 风速</span>
                      <b>{{ card.direction }}° · {{ card.speed }} m/s</b>
                    </div>
                  </div>
                </div>
              </div>

              <div v-else class="card-body">
                <el-empty :image-size="54" :description="predictionUnavailableReason" />
                <div v-if="forecastHours.length > 0" class="inline-note">
                  仍可参考的风场结论：主导来向 {{ windFromLabel }}（{{ formatDegree(forecastWindFrom) }}），
                  平均预测风速 {{ formatNumber(forecastSummary?.wind_speed_ms, 2) }} m/s，但风速偏低或风向不稳，
                  不足以输出定向传播路径。
                </div>
              </div>
            </div>

            <!-- 扩散气象条件评估与主控因子 -->
            <div class="card">
              <div class="card-head">
                <span class="head-icon"><el-icon :size="14"><DataAnalysis /></el-icon></span>
                <span class="head-title">扩散气象条件评估与主控因子</span>
                <span class="head-mono">综合评定：{{ dispersionCondition }} · {{ riskLevelLabel }} {{ riskPercent }}%</span>
              </div>

              <div class="card-body">
                <div class="factor-list">
                  <div v-for="factor in factorRows" :key="factor.label" class="factor-row">
                    <span class="factor-label">
                      {{ factor.label }}
                      <em>（{{ factor.criteria }}）</em>
                    </span>
                    <div class="factor-track">
                      <div
                        class="factor-fill"
                        :style="{ width: factor.value + '%', background: factor.color }"
                      ></div>
                    </div>
                    <span class="factor-value" :style="{ color: factor.color }">
                      {{ factor.value }}%<em v-if="factor.level">（{{ factor.level }}）</em>
                    </span>
                  </div>
                </div>

                <div class="verdict">
                  <div class="verdict-title">
                    <el-icon :size="13"><WarnTriangleFilled /></el-icon>
                    <span>智能研判结论</span>
                  </div>
                  <p>{{ conclusionText }}</p>
                  <div v-if="evidence.length" class="verdict-evidence">
                    <span v-for="(item, index) in evidence" :key="index">{{ item }}</span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <!-- ═══════════ 第二屏：敏感目标分类与空间覆盖统计 ═══════════ -->
        <section class="wp-secondary">

          <!-- 敏感目标分类与空间覆盖统计 -->
          <div class="card">
            <div class="card-head">
              <span class="head-icon"><el-icon :size="14"><OfficeBuilding /></el-icon></span>
              <span class="head-title">敏感目标分类与空间覆盖统计</span>
              <span class="head-total">
                扇区总计：<b>{{ sensitivePoiCount }}</b> 个
              </span>
            </div>

            <div v-if="poiCards.length" class="poi-grid">
              <div v-for="card in poiCards" :key="card.key" class="poi-item">
                <div class="poi-top">
                  <span class="poi-icon" :style="{ color: card.color, background: card.tint }">
                    <el-icon :size="15"><component :is="card.icon" /></el-icon>
                  </span>
                  <span class="poi-num">{{ card.count }}</span>
                  <span class="poi-pct">{{ card.percent }}%</span>
                </div>
                <div class="poi-name">{{ card.label }}</div>
                <div class="poi-track">
                  <div class="poi-fill" :style="{ width: card.percent + '%', background: card.color }"></div>
                </div>
                <div class="poi-note">{{ card.note }}</div>
              </div>
            </div>

            <div v-else class="card-body">
              <p class="validation-note">
                {{ propagationExposure
                  ? '当前传播范围内未统计到敏感目标，或该时段传播范围不可用。'
                  : '请重新分析以更新当前传播范围内的敏感目标，旧版半径统计不再作为本页传播口径。' }}
              </p>
            </div>

            <div class="poi-foot">
              <span class="poi-advice">
                重点管控建议：{{ poiAdvice }}
              </span>
              <span class="poi-foot-note">筛查级提示，需人工复核后对外发布</span>
            </div>
          </div>
        </section>

        <!-- ═══════════ 第三屏：预测依据（左） + 逐小时气象趋势（右） ═══════════ -->
        <section class="wp-detail">

          <!-- 预测依据与逐小时风向流线 -->
          <div class="card">
            <div class="card-head">
              <span class="head-icon"><el-icon :size="14"><DataAnalysis /></el-icon></span>
              <span class="head-title">预测依据与逐小时风向流线</span>
              <span v-if="forecastHours.length > 0" class="head-tag is-green">风矢量外推</span>
            </div>

            <div v-if="forecastHours.length > 0" class="card-body">
              <div class="basis-tiles">
                <div v-for="tile in evidenceTiles" :key="tile.label" class="basis-tile">
                  <span class="basis-label">{{ tile.label }}</span>
                  <span class="basis-value">{{ tile.value }}</span>
                  <span class="basis-underline" :style="{ background: tile.color }"></span>
                  <span class="basis-hint">{{ tile.hint }}</span>
                </div>
              </div>

              <div class="block-title">
                <el-icon :size="13"><Position /></el-icon>
                <span>路径分段（{{ predictionSteps.length }} 小时波段）</span>
              </div>
              <div class="segment-cards">
                <div
                  v-for="(seg, index) in segmentCards"
                  :key="seg.lead"
                  class="segment-card"
                  :style="{ borderLeftColor: stepBarColor(index) }"
                >
                  <div class="seg-head">
                    <span class="seg-title">第 {{ seg.lead }} 小时</span>
                    <span class="seg-dist">{{ seg.dist }} km</span>
                  </div>
                  <div class="seg-meta">推进 {{ seg.thisHour }} km · 扩散半宽 {{ seg.halfWidth }} km</div>
                  <div class="seg-meta">最慢衰减气团剩余 {{ seg.remain }}</div>
                  <div v-if="seg.species" class="seg-meta">{{ seg.species }}</div>
                </div>
              </div>

              <div class="card-foot">
                <span>路径按起止小时风矢量积分推进，仅作筛查级模拟。</span>
                <span class="foot-brand">筛查级模拟</span>
              </div>
            </div>

            <div v-else class="card-body">
              <el-empty :description="predictionUnavailableReason || '暂无风场预测数据'" />
            </div>
          </div>

          <!-- 逐小时气象与沉降趋势（高度跟随左侧预测依据卡） -->
          <div class="card trend-card">
            <div class="card-head">
              <span class="head-icon"><el-icon :size="14"><TrendCharts /></el-icon></span>
              <span class="head-title">逐小时气象与沉降趋势</span>
              <span class="head-tag is-gray">风速 / 湿度 / 降水 / 气温</span>
            </div>
            <div class="trend-inner">
              <WeatherTrend :data="hourlyTrendData" />
            </div>
          </div>
        </section>

      </div>
    </div>
  </MainLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import type { Component } from 'vue'
import {
  MapLocation,
  TrendCharts,
  InfoFilled,
  Position,
  DataAnalysis,
  OfficeBuilding,
  School,
  FirstAidKit,
  Van,
  House,
  WarnTriangleFilled
} from '@element-plus/icons-vue'
import MainLayout from '@/layouts/MainLayout.vue'
import TaskQueryBar from '@/components/global/TaskQueryBar.vue'
import AnalysisLoading from '@/components/global/AnalysisLoading.vue'
import { initialPropagationDirection } from '@/utils/propagationDirection'
import SpatialMap from '@/components/spatial/SpatialMap.vue'
import WeatherTrend from '@/components/meteorology/WeatherTrend.vue'
import { useAnalysisStore } from '@/stores/analysis'
import type { HourlyWeatherData } from '@/components/meteorology/WeatherTrend.vue'
import type {
  MeteorologyHourlyItem,
  MeteorologyPathStep,
  PropagationStationComparison
} from '@/types/meteorology'

const analysisStore = useAnalysisStore()

// 页面挂载时确保已有数据
onMounted(async () => {
  if (!analysisStore.result) {
    await analysisStore.loadInitial()
  }
})

// ─── 原始 result 快捷引用 ───────────────────────────────────────
const meteorologyResponse = computed(() => analysisStore.result?.meteorology)
const spatialResponse = computed(() => analysisStore.result?.spatial)

const meteorologyResult = computed(() => meteorologyResponse.value?.result ?? null)
const spatialResult = computed(() => spatialResponse.value?.result ?? null)
const propagationExposure = computed(() => spatialResult.value?.propagation_exposure)
const propagationSpatialResult = computed(() => {
  const spatial = spatialResult.value
  if (!spatial) return null
  const scope = propagationExposure.value
  return { ...spatial, sensitive_pois: scope?.sensitive_pois ?? [],
    sensitive_poi_count: scope?.sensitive_poi_count ?? 0,
    sensitive_poi_by_type: { rail_transit: 0, school: 0, healthcare: 0,
      community_service: 0, community_centre: 0, ...scope?.sensitive_poi_by_type } }
})

// ─── 气象风险总览（只保留等级与分值，作为扩散卡片的表头口径） ─────
const riskLevel = computed((): 'low' | 'medium' | 'high' | 'very_high' => {
  return (meteorologyResult.value?.risk_type ?? 'low') as 'low' | 'medium' | 'high' | 'very_high'
})

const riskPercent = computed(() => {
  const risk = meteorologyResult.value?.weather_risk ?? 0
  return Math.round(risk * 100)
})

const RISK_LEVEL_LABELS: Record<string, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
  very_high: '极高风险'
}

const riskLevelLabel = computed(() => RISK_LEVEL_LABELS[riskLevel.value] ?? '未知')

const evidence = computed(() =>
  (meteorologyResponse.value?.evidence ?? []).filter(
    (item) => !item.includes('按论文寿命公式计算')
  )
)

// ─── 地图浮层：起算时刻气象条件 ─────────────────────────────────
// 阈值口径来自 config/meteorology_rules.json（static_wind.speed_threshold_ms）
const STATIC_WIND_THRESHOLD = 1.5

const windLabel = computed(() =>
  meteorologyResult.value?.dominant_wind_direction_label ?? '—'
)

const windDegreeText = computed(() =>
  formatNumber(meteorologyResult.value?.dominant_wind_direction, 1)
)

const windSpeedText = computed(() =>
  formatNumber(meteorologyResult.value?.mean_wind_speed, 2)
)

const temperatureText = computed(() =>
  formatNumber(meteorologyResult.value?.mean_temperature, 1)
)

const humidityText = computed(() =>
  formatNumber(meteorologyResult.value?.mean_humidity, 1)
)

const stabilityLabel = computed(() => {
  const speed = Number(meteorologyResult.value?.mean_wind_speed)
  if (!Number.isFinite(speed)) return ''
  if (speed <= STATIC_WIND_THRESHOLD) return '静稳'
  if (speed <= 2.5) return '弱风'
  return '有风'
})

// ─── 逐小时气象趋势 ─────────────────────────────────────────────
const hourlyTrendData = computed((): HourlyWeatherData => {
  const series = meteorologyResult.value?.hourly_series ?? []

  if (series.length === 0) {
    return {
      time: [],
      wind_speed: [],
      humidity: [],
      precipitation: [],
      temperature: []
    }
  }

  const time = series.map((item: MeteorologyHourlyItem) => {
    const t = item.time.split(' ')[1] ?? item.time
    return t.substring(0, 5)
  })

  return {
    time,
    wind_speed: series.map((item: MeteorologyHourlyItem) => item.wind_speed),
    humidity: series.map((item: MeteorologyHourlyItem) => item.humidity),
    precipitation: series.map((item: MeteorologyHourlyItem) => item.precipitation),
    temperature: series.map((item: MeteorologyHourlyItem) => item.temperature)
  }
})

// ─── 可解释预测：数据契约 ────────────────────────────────────────
const windForecast = computed(() => meteorologyResult.value?.wind_direction_forecast ?? null)

const forecastHours = computed(() => {
  const hours = windForecast.value?.forecast_hours ?? []
  return [...hours].sort((a, b) => a.lead_hour - b.lead_hour)
})

const predictionSteps = computed(() => {
  const prediction = meteorologyResult.value?.pollution_path_prediction
  if (prediction?.status !== 'ok') return []
  return [...(prediction.steps ?? [])].sort((a, b) => a.lead_hour - b.lead_hour)
})

const pathInitialDirection = computed(() => initialPropagationDirection(meteorologyResult.value?.pollution_path_prediction))
const predictionAvailable = computed(() => predictionSteps.value.length > 0)
const stationValidation = computed(() => meteorologyResult.value?.pollution_path_prediction?.station_validation)
const simulationHorizon = computed(() => meteorologyResult.value?.pollution_path_prediction?.requested_horizon_hours ?? predictionSteps.value.length)

const lastStep = computed(() => predictionSteps.value[predictionSteps.value.length - 1] ?? null)
const parcelLabels: Record<string, string> = {
  pm25: 'PM2.5', pm10: 'PM10', no2: '二氧化氮', o3: '臭氧', so2: '二氧化硫', co: '一氧化碳'
}

const forecastSummary = computed(() => forecastHours.value[0] ?? null)

const predictionUnavailableReason = computed(() => {
  const prediction = meteorologyResult.value?.pollution_path_prediction
  if (windForecast.value?.status === 'calm') {
    return '预测风速低于定向输送阈值（< 0.5 m/s），按弱风处理，不输出传播路径。'
  }
  return (
    prediction?.reason ||
    windForecast.value?.reason ||
    '弱风或风向离散度过高，无法形成稳定的主导输送方向。'
  )
})

const forecastWindFrom = computed(
  () => windForecast.value?.forecast_hours?.[0]?.wind_from_direction ?? null
)

const COMPASS_16 = [
  '北', '北东北', '东北', '东东北',
  '东', '东东南', '东南', '南东南',
  '南', '南西南', '西南', '西西南',
  '西', '西西北', '西北', '北西北'
]

/** 角度 → 十六方位中文简称 */
function compassOf(degree: number | null | undefined): string {
  if (degree === null || degree === undefined) return '—'
  const num = Number(degree)
  if (!Number.isFinite(num)) return '—'
  const index = Math.round((((num % 360) + 360) % 360) / 22.5) % 16
  return COMPASS_16[index]
}

const windFromLabel = computed(() => compassOf(forecastWindFrom.value))

function formatDegree(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  const num = Number(value)
  return Number.isFinite(num) ? `${num.toFixed(1)}°` : '—'
}

function formatNumber(value: number | null | undefined, digits: number): string {
  const num = Number(value)
  return Number.isFinite(num) ? num.toFixed(digits) : '—'
}

function formatPercent(value: number | null | undefined): string {
  const num = Number(value)
  return Number.isFinite(num) ? `${(num * 100).toFixed(1)}%` : '—'
}

function formatRatio(value: number | null | undefined): string {
  const num = Number(value)
  return Number.isFinite(num) ? `${(num * 100).toFixed(0)}%` : '—'
}

function formatTurnRate(value: number | null | undefined): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  const sign = num > 0 ? '+' : ''
  return `${sign}${num.toFixed(2)}°/h`
}

// ─── 时段切换（只做定位与高亮，不改变后端已算好的轨迹） ──────────
const activeLead = ref<number | null>(null)

const stepTabs = computed(() => predictionSteps.value.map((step) => ({
  lead: step.lead_hour,
  label: `+${step.lead_hour}h ${clockOf(step)}`
})))

watch(
  predictionSteps,
  (steps) => {
    if (!steps.length) {
      activeLead.value = null
      return
    }
    if (activeLead.value === null || !steps.some((step) => step.lead_hour === activeLead.value)) {
      activeLead.value = steps[steps.length - 1]?.lead_hour ?? null
    }
  },
  { immediate: true }
)

function focusStep(lead: number): void {
  activeLead.value = lead
  const target = document.getElementById(`wp-step-${lead}`)
  target?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

/** "2023-08-15 09:00:00" → "09:00" */
function clockOf(step: MeteorologyPathStep): string {
  const stamp = step.time
  if (!stamp) return '—'
  const time = stamp.split(' ')[1] ?? ''
  return time.slice(0, 5) || '—'
}

// ─── 轨迹推演卡片 ───────────────────────────────────────────────
interface TrackCard {
  lead: number
  clock: string
  place: string
  distance: string
  halfWidth: string
  remainText: string
  direction: string
  speed: string
  tone: 'head' | 'mid' | 'tail'
}

const TRACK_ACCENTS = { head: '#1668dc', mid: '#d97706', tail: '#dc2626' }

/** 该小时最接近的沿途站点，作为"途经"读图锚点 */
function nearestStation(step: MeteorologyPathStep): string | null {
  const stations = stationValidation.value?.stations ?? []
  if (!stations.length) return null
  let matched: PropagationStationComparison | null = null
  let minDiff = Number.POSITIVE_INFINITY
  for (const station of stations) {
    const diff = Math.abs(Number(station.arrival_lead_hour) - step.lead_hour)
    if (Number.isFinite(diff) && diff < minDiff) {
      minDiff = diff
      matched = station
    }
  }
  return matched && minDiff <= 1.5 ? matched.station_name_zh : null
}

function remainingLevel(fraction: number | null | undefined): string {
  const num = Number(fraction)
  if (!Number.isFinite(num)) return '—'
  if (num >= 0.9) return '高'
  if (num >= 0.7) return '中'
  return '低'
}

const trackCards = computed((): TrackCard[] => {
  const steps = predictionSteps.value
  if (!steps.length) return []
  const total = steps.length
  return steps.map((step, index) => {
    const ratio = total > 1 ? index / (total - 1) : 0
    const tone: TrackCard['tone'] = ratio < 0.34 ? 'head' : ratio < 0.67 ? 'mid' : 'tail'
    const station = nearestStation(step)
    const forecast = forecastHours.value.find((hour) => hour.lead_hour === step.lead_hour)
    const rawSpeed = forecast?.wind_speed_ms ?? meteorologyResult.value?.mean_wind_speed
    const speed = rawSpeed === null || rawSpeed === undefined ? Number.NaN : Number(rawSpeed)
    const keep = step.remaining_fraction
    const keepPct = keep === null || keep === undefined ? null : Number(keep) * 100
    return {
      lead: step.lead_hour,
      clock: clockOf(step),
      place: station ? `途经 ${station}` : '下风向输送段',
      distance: formatNumber(step.distance_from_origin_km, 1),
      halfWidth: formatNumber(step.corridor_half_width_km, 1),
      remainText: keepPct === null || !Number.isFinite(keepPct)
        ? '—'
        : `${keepPct.toFixed(1)}%（${remainingLevel(keep)}）`,
      direction: formatNumber(step.wind_to_direction, 1),
      speed: Number.isFinite(speed) ? speed.toFixed(2) : '—',
      tone
    }
  })
})

// ─── 扩散主控因子 ───────────────────────────────────────────────
// 判据文字与 config/meteorology_rules.json 中的阈值一一对应
interface FactorRow {
  label: string
  criteria: string
  value: number
  color: string
  level: string
}

function scoreLevel(value: number): string {
  if (value >= 90) return '极高'
  if (value >= 70) return '高'
  if (value >= 50) return '中'
  return '低'
}

const factorRows = computed((): FactorRow[] => {
  const r = meteorologyResult.value
  const raw: Omit<FactorRow, 'level'>[] = [
    {
      label: '静稳积聚',
      criteria: '连续 ≥2h 风速 ≤1.5m/s',
      value: Math.round((r?.static_wind_score ?? 0) * 100),
      color: '#f59e0b'
    },
    {
      label: '风向突变风险',
      criteria: '相邻时段风向变化 ≥45°',
      value: Math.round((r?.wind_shift_score ?? 0) * 100),
      color: '#dc2626'
    },
    {
      label: '高湿雨势屏障',
      criteria: '湿度 ≥85% 且连续 ≥2h',
      value: Math.round((r?.humidity_risk_score ?? 0) * 100),
      color: '#0d9488'
    },
    {
      label: '雨水冲刷不足',
      criteria: '近 6h 累计降水 <1.0mm',
      value: Math.round((r?.insufficient_rain_removal_score ?? 0) * 100),
      color: '#2563eb'
    }
  ]
  return raw.map((row) => ({
    ...row,
    value: Math.max(0, Math.min(100, row.value)),
    level: scoreLevel(Math.max(0, Math.min(100, row.value)))
  }))
})

const dispersionCondition = computed(() =>
  meteorologyResult.value?.dispersion_condition ?? '—'
)

/** 智能研判结论：只由已有量化结论拼装，不引入外部判断 */
const conclusionText = computed(() => {
  const parts: string[] = []
  const horizon = simulationHorizon.value
  const windShift = Number(meteorologyResult.value?.wind_shift_score ?? 0)
  const last = lastStep.value

  if (predictionAvailable.value && last) {
    parts.push(
      `轨迹未来 ${horizon} 小时朝 ${compassOf(last.wind_to_direction)}方向推进，最远约 ` +
      `${formatNumber(last.distance_from_origin_km, 1)} km，扩散条件「${dispersionCondition.value}」`
    )
    parts.push(`末端气团留存 ${formatPercent(last.remaining_fraction)}`)
  } else {
    parts.push(predictionUnavailableReason.value.replace(/。$/, ''))
  }

  if (windShift >= 0.8) {
    parts.push('风向突变风险显著，路径末段方位不确定度上升，需按小时复核轨迹')
  }
  if (Number(meteorologyResult.value?.humidity_risk_score ?? 0) >= 0.5) {
    parts.push('高湿且雨量不足，湿沉降清除有限')
  }

  const school = propagationExposure.value?.sensitive_poi_by_type?.school ?? 0
  if (school > 0) {
    parts.push(`建议对沿线 ${school} 所学校提前发布未来 ${horizon} 小时户外活动限制与室内通风防护提示`)
  }

  return `${parts.filter(Boolean).join('；')}。`
})

// ─── 敏感目标清单 ───────────────────────────────────────────────
const sensitivePoiCount = computed(() =>
  propagationExposure.value?.sensitive_poi_count ?? 0
)

interface PoiCard {
  key: string
  label: string
  icon: Component
  color: string
  tint: string
  note: string
  count: number
  percent: number
}

const POI_META: Record<string, Omit<PoiCard, 'key' | 'count' | 'percent'>> = {
  school: {
    label: '学校与托幼机构',
    icon: School,
    color: '#1668dc',
    tint: 'rgba(22, 104, 220, 0.1)',
    note: '中小学、幼儿园及托育设施'
  },
  healthcare: {
    label: '医疗卫生机构',
    icon: FirstAidKit,
    color: '#dc2626',
    tint: 'rgba(220, 38, 38, 0.1)',
    note: '医院门诊、诊所及长者护理院舍'
  },
  rail_transit: {
    label: '轨道与交通枢纽',
    icon: Van,
    color: '#f59e0b',
    tint: 'rgba(245, 158, 11, 0.12)',
    note: '港铁车站、巴士枢纽及换乘站点'
  },
  community_service: {
    label: '社区综合服务中心',
    icon: House,
    color: '#16a34a',
    tint: 'rgba(22, 163, 74, 0.1)',
    note: '社区会堂、康乐设施与公共服务中心'
  }
}

const POI_ORDER = ['school', 'healthcare', 'rail_transit', 'community_service']

const poiCards = computed((): PoiCard[] => {
  const byType = propagationExposure.value?.sensitive_poi_by_type
  if (!byType) return []

  const total = sensitivePoiCount.value
  if (total === 0) return []

  // 合并 community_service 和 community_centre
  const merged: Record<string, number> = {}
  Object.entries(byType).forEach(([type, count]) => {
    const mappedType = type === 'community_centre' ? 'community_service' : type
    merged[mappedType] = (merged[mappedType] ?? 0) + (count as number)
  })

  return POI_ORDER.filter((type) => merged[type]).map((type) => ({
    key: type,
    ...POI_META[type],
    count: merged[type],
    percent: Math.round((merged[type] / total) * 100)
  }))
})

const poiAdvice = computed(() => {
  const school = propagationExposure.value?.sensitive_poi_by_type?.school ?? 0
  const healthcare = propagationExposure.value?.sensitive_poi_by_type?.healthcare ?? 0
  const rail = propagationExposure.value?.sensitive_poi_by_type?.rail_transit ?? 0
  if (school + healthcare + rail === 0) return '当前传播范围内未统计到敏感目标，维持常规巡查。'
  const horizon = simulationHorizon.value
  const segments: string[] = []
  if (school > 0) segments.push(`${school} 所学校与托幼机构`)
  if (healthcare > 0) segments.push(`${healthcare} 家医疗卫生机构`)
  if (rail > 0) segments.push(`${rail} 处轨道与交通枢纽`)
  return `对传播扇区内 ${segments.join('、')} 发布未来 ${horizon} 小时户外活动与通风防护提示。`
})

// ─── 依据四格 ───────────────────────────────────────────────────
interface BasisTile {
  label: string
  value: string
  color: string
  hint: string
}

const evidenceTiles = computed((): BasisTile[] => {
  const f = windForecast.value
  const concentration = f?.resultant_length ?? 0
  const confidence = f?.confidence ?? 0
  return [
    {
      label: '风向集中度',
      value: formatRatio(concentration),
      color: '#1668dc',
      hint: '圆统计合成矢量长度'
    },
    {
      label: '风向漂移',
      value: formatTurnRate(f?.turn_rate_deg_per_hour),
      color: '#f59e0b',
      hint: '前后半窗转向速率'
    },
    {
      label: '预测置信度',
      value: formatRatio(confidence),
      color: '#0d9488',
      hint: '由集中度与样本数决定'
    },
    {
      label: '预测窗口',
      value: `${f?.lookback_hours_used ?? '—'} h`,
      color: '#64748b',
      hint: '参与圆统计的观测时长'
    }
  ]
})

/** 路径分段卡片 */
interface SegmentCard {
  lead: number
  dist: string
  thisHour: string
  halfWidth: string
  remain: string
  species: string
}

const segmentCards = computed((): SegmentCard[] => {
  return predictionSteps.value.map((step) => ({
    lead: step.lead_hour,
    dist: formatNumber(step.distance_from_origin_km, 2),
    thisHour: formatNumber(step.distance_this_hour_km, 2),
    halfWidth: formatNumber(step.corridor_half_width_km, 2),
    remain: formatPercent(step.remaining_fraction),
    species: Object.entries(step.remaining_by_pollutant ?? {}).map(([name, item]) =>
      `${parcelLabels[name] || name} ${formatPercent(item.remaining_fraction)}`).join(' · ')
  }))
})

function stepBarColor(index: number): string {
  const total = predictionSteps.value.length
  if (total <= 1) return TRACK_ACCENTS.head
  const ratio = index / (total - 1)
  if (ratio < 0.34) return TRACK_ACCENTS.head
  if (ratio < 0.67) return TRACK_ACCENTS.mid
  return TRACK_ACCENTS.tail
}
</script>

<style scoped>
/* ══════════ 设计令牌（本页专用，与参考稿的浅色政务风一致） ══════════ */
.weather-container {
  --wp-blue: #1668dc;
  --wp-blue-soft: #eaf2ff;
  --wp-ink: #101a2c;
  --wp-ink-2: #3f4d63;
  --wp-muted: #7b8799;
  --wp-line: #e7edf6;
  --wp-fill: #f7f9fd;
  /* 页面底色沿用 layout 的 --bg-page，避免与外壳出现色带 */
  padding: 16px 24px 24px;
}

.scope-stale-state { padding: 18px 24px 0; }

/* ===== 状态占位 ===== */
.error-state { padding: 24px 0; }

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 300px;
  gap: 12px;
  color: var(--wp-muted);
  font-size: 14px;
}

/* ===== 主内容 ===== */
.weather-content {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.source-note {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 0;
  padding: 10px 14px;
  border: 1px solid var(--wp-line);
  border-radius: 10px;
  background: #fff;
  font-size: 12px;
  line-height: 18px;
  color: var(--wp-ink-2);
}

.source-note.is-warn {
  border-color: #f0c98b;
  color: #b45309;
}

/* ===== 版式栅格 ===== */
.wp-main {
  display: grid;
  grid-template-columns: minmax(0, 1.52fr) minmax(0, 1fr);
  gap: 14px;
  align-items: stretch;
}

/* 第二屏只剩敏感目标一张卡，占满整行 */
.wp-secondary {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 14px;
  align-items: stretch;
}

.wp-detail {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 14px;
  /* 两卡等高：行高由左侧「预测依据」卡内容决定，右侧趋势卡拉伸对齐 */
  align-items: stretch;
}

/* ===== 通用卡片 ===== */
.card {
  display: flex;
  flex-direction: column;
  background: #fff;
  border: 1px solid var(--wp-line);
  border-radius: 10px;
  box-shadow: 0 1px 2px rgba(16, 26, 44, 0.03);
  overflow: hidden;
  min-width: 0;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--wp-line);
  flex-shrink: 0;
}

.head-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--wp-blue);
  flex-shrink: 0;
}

.head-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--wp-ink);
  letter-spacing: 0.2px;
}

.head-tag {
  margin-left: auto;
  font-size: 11px;
  line-height: 1;
  padding: 4px 9px;
  border-radius: 4px;
  white-space: nowrap;
}

.head-tag.is-green { color: #15803d; background: rgba(22, 163, 74, 0.1); }
.head-tag.is-gray { color: var(--wp-muted); background: var(--wp-fill); }

.head-mono {
  margin-left: auto;
  font-size: 11px;
  color: var(--wp-muted);
  white-space: nowrap;
}

.head-total {
  margin-left: auto;
  font-size: 12px;
  color: var(--wp-ink-2);
  white-space: nowrap;
}

.head-total b {
  font-size: 15px;
  font-weight: 800;
  color: var(--wp-ink);
  margin: 0 2px;
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 16px 16px;
  flex: 1;
  min-width: 0;
}

.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 11px;
  color: var(--wp-muted);
}

.foot-brand {
  font-family: 'SF Mono', Menlo, monospace;
  letter-spacing: 0.4px;
  white-space: nowrap;
}

/* ══════════ 主地图 ══════════ */
.map-panel {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.map-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 2px 2px 10px;
  flex-wrap: wrap;
}

.map-title {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  font-size: 15px;
  font-weight: 800;
  color: var(--wp-ink);
  letter-spacing: 0.2px;
}

.map-title .el-icon { color: var(--wp-blue); }

.map-badge {
  font-size: 11px;
  color: var(--wp-blue);
  background: var(--wp-blue-soft);
  border: 1px solid #cfe1fb;
  border-radius: 4px;
  padding: 3px 8px;
  white-space: nowrap;
}

.step-tabs {
  display: inline-flex;
  align-items: center;
  border: 1px solid #cfe1fb;
  border-radius: 6px;
  overflow: hidden;
  background: #fff;
}

.step-tab {
  appearance: none;
  border: none;
  border-right: 1px solid #e2ecfb;
  background: #fff;
  color: var(--wp-blue);
  font-size: 12px;
  font-weight: 600;
  padding: 6px 12px;
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
  white-space: nowrap;
}

.step-tab:last-child { border-right: none; }
.step-tab:hover { background: var(--wp-blue-soft); }

.step-tab.is-active {
  background: var(--wp-blue);
  color: #fff;
}

.step-tab.is-empty {
  color: var(--wp-muted);
  cursor: default;
  background: var(--wp-fill);
}

.map-res {
  margin-left: auto;
  font-size: 11px;
  color: var(--wp-muted);
  white-space: nowrap;
}

.map-card {
  position: relative;
  display: flex;
  flex: 1;
  min-height: 520px;
  border: 1px solid var(--wp-line);
  border-radius: 10px;
  overflow: hidden;
  background: #eef2f7;
}

/* 地图浮层：起算时刻气象条件 */
.map-chips {
  position: absolute;
  top: 10px;
  left: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  /* 腾讯地图内部有 z-index 1000 的事件捕获层，浮层必须高于它 */
  z-index: 1100;
  pointer-events: none;
}

.map-chip {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  padding: 5px 9px;
  background: rgba(255, 255, 255, 0.94);
  border: 1px solid rgba(16, 26, 44, 0.06);
  border-radius: 5px;
  box-shadow: 0 1px 4px rgba(16, 26, 44, 0.08);
  font-size: 11px;
  color: var(--wp-ink-2);
  line-height: 1.2;
}

.map-chip b {
  font-weight: 700;
  color: var(--wp-ink);
}

.map-chip em {
  font-style: normal;
  color: var(--wp-blue);
}

/* 地图下方口径条 */
.map-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0;
  margin-top: 10px;
  background: #fff;
  border: 1px solid var(--wp-line);
  border-radius: 10px;
  overflow: hidden;
}

.stat {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 14px;
  border-left: 1px solid var(--wp-line);
  min-width: 0;
}

.stat:first-child { border-left: none; }

.stat span {
  font-size: 11px;
  color: var(--wp-muted);
}

.stat b {
  font-size: 16px;
  font-weight: 800;
  color: var(--wp-ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.stat b small {
  font-size: 11px;
  font-weight: 500;
  color: var(--wp-muted);
}

/* ══════════ 右列：轨迹推演 ══════════ */
.side-stack {
  display: grid;
  gap: 14px;
  align-content: start;
  min-width: 0;
}

.track-list {
  gap: 10px;
}

.track-card {
  position: relative;
  padding: 9px 12px 10px 14px;
  border: 1px solid #e3ecf9;
  border-radius: 8px;
  background: #fff;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.track-card::before {
  content: '';
  position: absolute;
  left: 0;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--accent, #1668dc);
}

.track-card.tone-head { --accent: #1668dc; }
.track-card.tone-mid { --accent: #d97706; }
.track-card.tone-tail { --accent: #dc2626; }

.track-card.is-focus {
  background: #f5f9ff;
  border-color: #bed8fb;
}

.track-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.track-lead {
  font-size: 13px;
  font-weight: 800;
  color: var(--accent);
  white-space: nowrap;
}

.track-place {
  font-size: 12px;
  color: var(--wp-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.track-dist {
  margin-left: auto;
  font-size: 18px;
  font-weight: 800;
  color: var(--wp-ink);
  white-space: nowrap;
  line-height: 1;
}

.track-dist small {
  font-size: 10px;
  font-weight: 500;
  color: var(--wp-muted);
  margin-left: 3px;
}

.track-sep {
  height: 1px;
  background: var(--wp-line);
  margin: 8px 0;
}

.track-meta {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0;
}

.meta-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 0 10px;
  border-left: 1px solid var(--wp-line);
  min-width: 0;
}

.meta-cell:first-child {
  padding-left: 0;
  border-left: none;
}

.meta-cell span {
  font-size: 10px;
  color: var(--wp-muted);
  white-space: nowrap;
}

.meta-cell b {
  font-size: 12px;
  font-weight: 700;
  color: var(--wp-ink);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.inline-note {
  padding: 8px 10px;
  border-left: 2px solid #cfe1fb;
  background: var(--wp-fill);
  border-radius: 0 6px 6px 0;
  font-size: 11px;
  line-height: 1.7;
  color: var(--wp-ink-2);
}

/* ══════════ 右列：扩散主控因子 ══════════ */
.factor-list {
  display: flex;
  flex-direction: column;
  gap: 11px;
}

.factor-row {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(60px, 1fr) 78px;
  align-items: center;
  gap: 10px;
}

.factor-label {
  font-size: 12px;
  color: var(--wp-ink);
  line-height: 1.4;
}

.factor-label em {
  font-style: normal;
  font-size: 10.5px;
  color: var(--wp-muted);
  display: block;
  line-height: 1.4;
}

.factor-track {
  height: 7px;
  border-radius: 4px;
  background: #eef2f8;
  overflow: hidden;
}

.factor-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.5s ease;
}

.factor-value {
  text-align: right;
  font-size: 12px;
  font-weight: 800;
  white-space: nowrap;
}

.factor-value em {
  font-style: normal;
  font-size: 10px;
  font-weight: 600;
}

.verdict {
  border: 1px solid #f7dcc8;
  background: #fff7f1;
  border-radius: 8px;
  padding: 10px 12px;
}

.verdict-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 800;
  color: #c2600a;
}

.verdict p {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.8;
  color: #46433d;
}

.verdict-evidence {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-top: 8px;
  padding-top: 7px;
  border-top: 1px dashed #f0d3bd;
  max-height: 76px;
  overflow-y: auto;
}

.verdict-evidence span {
  position: relative;
  padding-left: 10px;
  font-size: 10.5px;
  line-height: 1.5;
  color: #8a7d70;
}

.verdict-evidence span::before {
  content: '';
  position: absolute;
  left: 0;
  top: 6px;
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #d9a279;
}

/* ══════════ 敏感目标统计 ══════════ */
.poi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 14px;
  padding: 16px 16px 12px;
  flex: 1;
}

.poi-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding-left: 14px;
  border-left: 1px dashed #e3e9f3;
  min-width: 0;
}

.poi-item:first-child {
  padding-left: 0;
  border-left: none;
}

.poi-top {
  display: flex;
  align-items: center;
  gap: 8px;
}

.poi-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  flex-shrink: 0;
}

.poi-num {
  font-size: 24px;
  font-weight: 800;
  color: var(--wp-ink);
  line-height: 1;
  letter-spacing: -0.4px;
}

.poi-pct {
  margin-left: auto;
  font-size: 11px;
  color: var(--wp-muted);
}

.poi-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--wp-ink-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.poi-track {
  height: 3px;
  border-radius: 2px;
  background: #eef2f8;
  overflow: hidden;
}

.poi-fill {
  height: 100%;
  border-radius: 2px;
}

.poi-note {
  font-size: 10px;
  line-height: 1.5;
  color: var(--wp-muted);
}

.poi-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 16px;
  border-top: 1px solid var(--wp-line);
  background: #fbfcfe;
}

.poi-advice {
  font-size: 11.5px;
  color: var(--wp-ink-2);
  line-height: 1.6;
}

.poi-foot-note {
  font-size: 10.5px;
  color: var(--wp-muted);
  white-space: nowrap;
}

/* ══════════ 说明文字 ══════════ */
.validation-note {
  margin: 0;
  font-size: 12px;
  line-height: 1.8;
  color: var(--wp-muted);
}

/* ══════════ 依据卡片 ══════════ */
.basis-tiles {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.basis-tile {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 10px 12px;
  background: var(--wp-fill);
  border: 1px solid var(--wp-line);
  border-radius: 8px;
  min-width: 0;
}

.basis-label {
  font-size: 10.5px;
  color: var(--wp-muted);
}

.basis-value {
  font-size: 16px;
  font-weight: 800;
  color: var(--wp-ink);
  white-space: nowrap;
}

.basis-underline {
  height: 3px;
  border-radius: 2px;
}

.basis-hint {
  font-size: 10px;
  line-height: 1.4;
  color: var(--wp-muted);
}

.block-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 700;
  color: var(--wp-ink);
  padding-left: 8px;
  border-left: 3px solid var(--wp-blue);
  line-height: 1.2;
  margin-top: 2px;
}

.segment-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  max-height: 320px;
  overflow-y: auto;
}

.segment-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  background: var(--wp-fill);
  border: 1px solid var(--wp-line);
  border-left: 3px solid var(--wp-blue);
  border-radius: 8px;
}

.seg-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 6px;
}

.seg-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--wp-ink);
}

.seg-dist {
  font-size: 11px;
  font-weight: 700;
  color: var(--wp-blue);
  white-space: nowrap;
}

.seg-meta {
  font-size: 10px;
  color: var(--wp-muted);
  line-height: 1.5;
}

/* 趋势卡：图表填满卡片剩余高度，卡片高由同一网格行决定 */
.trend-card {
  min-height: 0;
}

.trend-inner {
  display: flex;
  flex-direction: column;
  flex: 1;
  /* 允许被压缩到卡片高度，避免把整行撑高 */
  min-height: 0;
  padding: 12px;
}

/* ══════════ 响应式 ══════════ */
@media (max-width: 1280px) {
  .wp-main,
  .wp-secondary,
  .wp-detail {
    grid-template-columns: minmax(0, 1fr);
  }

  .map-card { min-height: 460px; }
  .map-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stat:nth-child(3) { border-left: none; }
  .stat:nth-child(-n + 2) { border-bottom: 1px solid var(--wp-line); }

  /* 单列堆叠后不再有等高参照，趋势图给固定高度 */
  .trend-inner {
    flex: none;
    height: 320px;
  }
}

@media (max-width: 860px) {
  .weather-container { padding: 12px 12px 16px; }
  .poi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); row-gap: 16px; }
  .poi-item:nth-child(3) { padding-left: 0; border-left: none; }
  .basis-tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .track-meta { grid-template-columns: minmax(0, 1fr); gap: 6px; }
  .meta-cell { padding-left: 0; border-left: none; }
  .factor-row { grid-template-columns: minmax(0, 1fr) 70px; }
  .factor-track { grid-column: 1 / -1; grid-row: 2; }
  .map-res { margin-left: 0; }
}
</style>
