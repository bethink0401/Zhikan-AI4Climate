<template>
  <MainLayout>
    <!-- 任务查询栏 -->
    <TaskQueryBar />

    <div class="weather-container">
      <!-- 加载状态 -->
      <AnalysisLoading
        :active="analysisStore.loading"
        :task="analysisStore.currentTask"
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

      <!-- 无数据状态 -->
      <div v-if="!analysisStore.loading && !analysisStore.error && !analysisStore.result" class="empty-state">
        <el-empty description="暂无分析数据，请先提交分析任务" />
      </div>

      <!-- 正常数据展示 -->
      <div v-if="analysisStore.result" class="weather-content">

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

        <!-- ═══════════ 第一行：主地图（左） + 气象风险总览（右） ═══════════ -->
        <section class="row row-main">
          <!-- 左：公里级传播主地图 -->
          <div class="card map-card">
            <div class="card-head">
              <span class="head-icon is-blue">
                <el-icon :size="14"><MapLocation /></el-icon>
              </span>
              <span class="head-title">公里级传播风险主地图</span>
              <span class="head-live">实时综合计算</span>
              <span v-if="predictionAvailable" class="pill-danger">
                主导污染及输送：{{ formatNumber(lastStep?.distance_from_origin_km, 1) }} km
              </span>
            </div>
            <div class="map-inner">
              <SpatialMap
                :spatial="spatialResult"
                :meteorology="meteorologyResult"
                show-forecast
              />
            </div>
          </div>

          <!-- 右：气象风险总览 -->
          <div class="card risk-card">
            <div class="card-head">
              <span class="head-icon is-blue">
                <el-icon :size="14"><Odometer /></el-icon>
              </span>
              <span class="head-title">气象风险总览</span>
              <span class="head-sub">综合气象风险评估</span>
            </div>

            <div class="risk-body">
              <!-- 综合风险环 -->
              <div class="gauge-block">
                <div class="gauge-ring">
                  <svg viewBox="0 0 110 110">
                    <circle cx="55" cy="55" r="45" class="ring-bg" />
                    <circle
                      cx="55"
                      cy="55"
                      r="45"
                      class="ring-fill"
                      :style="gaugeRingStyle"
                    />
                  </svg>
                  <div class="gauge-center">
                    <span class="gauge-value">{{ riskPercent }}<small>%</small></span>
                    <span class="gauge-level" :class="'lv-' + riskLevel">{{ riskLevelLabel }}</span>
                  </div>
                </div>
                <div class="gauge-caption">综合气象风险等级</div>
              </div>

              <!-- 四维拆解条 -->
              <div class="dim-bars">
                <div v-for="dim in dimensionBars" :key="dim.label" class="dim-row">
                  <span class="dim-label">{{ dim.label }}</span>
                  <div class="dim-track">
                    <div class="dim-fill" :style="{ width: dim.value + '%', background: dim.color }"></div>
                  </div>
                  <span class="dim-value" :style="{ color: dim.color }">{{ dim.value }}%</span>
                </div>
              </div>

              <!-- 证据链 -->
              <div v-if="evidence.length > 0" class="evi-box">
                <div class="evi-title">
                  <el-icon :size="13"><DataAnalysis /></el-icon>
                  <span>结果证据链系统</span>
                </div>
                <ul class="evi-list">
                  <li v-for="(item, index) in evidence" :key="index" class="evi-item">
                    {{ item }}
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </section>

        <!-- ═══════════ 第二行：最终预测结果（左） + 预测依据（右） ═══════════ -->
        <section class="row row-half">
          <!-- 左：最终预测结果 -->
          <div class="card">
            <div class="card-head">
              <span class="head-icon is-blue">
                <el-icon :size="14"><Position /></el-icon>
              </span>
              <span class="head-title">最终预测结果</span>
              <span class="head-tag" :class="predictionAvailable ? 'is-green' : 'is-gray'">
                {{ predictionAvailable ? '路径可用' : '路径不可用' }}
              </span>
            </div>

            <div v-if="predictionAvailable" class="card-body">
              <!-- 结论三指标 -->
              <div class="hero-tiles">
                <div class="hero-tile">
                  <span class="tile-label is-blue">预测时效</span>
                  <span class="tile-value">
                    {{ predictionSteps.length }} <small>小时</small>
                  </span>
                </div>
                <div class="hero-tile">
                  <span class="tile-label is-teal">最远可达</span>
                  <span class="tile-value is-blue">
                    {{ formatNumber(lastStep?.distance_from_origin_km, 1) }} <small>km</small>
                  </span>
                </div>
                <div class="hero-tile">
                  <span class="tile-label is-gray">输送方位</span>
                  <span class="tile-value">
                    {{ forecastSummary?.wind_to_direction !== undefined
                      ? formatDegree(forecastSummary.wind_to_direction) + '（' + windToLabel + '）'
                      : '—' }}
                  </span>
                </div>
              </div>

              <!-- 结论 → 依据 的绑定说明 -->
              <div class="binding-note">
                <el-icon :size="14"><Connection /></el-icon>
                <span>
                  本结论由右侧 {{ forecastHours.length }} 条逐小时风向预测逐段平流推得；
                  主导来向 {{ windFromLabel }}（{{ formatDegree(forecastWindFrom) }}），
                  故污染向 {{ windToLabel }}（{{ formatDegree(forecastSummary?.wind_to_direction) }}）方向输送。
                </span>
              </div>

              <!-- 逐小时结论摘要 -->
              <div class="result-steps">
                <div
                  v-for="(step, index) in predictionSteps"
                  :key="step.lead_hour"
                  class="result-step"
                >
                  <div class="step-head">
                    <span class="step-chip">+{{ step.lead_hour }}h</span>
                    <span class="step-name">{{ stageName(index) }}</span>
                    <span class="step-distance">
                      {{ formatNumber(step.distance_from_origin_km, 1) }} km
                    </span>
                  </div>
                  <div class="step-track">
                    <div
                      class="step-fill"
                      :style="{
                        width: distancePercent(step.distance_from_origin_km) + '%',
                        background: stepBarColor(index)
                      }"
                    ></div>
                  </div>
                  <div class="step-meta">
                    扩散宽度 ±{{ formatNumber(step.corridor_half_width_km, 1) }} km ·
                    剩余 {{ formatPercent(step.remaining_fraction) }}
                  </div>
                </div>
              </div>

              <!-- 地图提示 -->
              <div class="map-hint">
                <el-icon :size="14"><MapLocation /></el-icon>
                <span>
                  预测扩散扇形与逐小时节点已叠加于上方主地图；扇形由中心向外渲染出浓度，其中的亮带表示输送方向
                </span>
              </div>
            </div>

            <!-- 路径不可用：说明原因但仍给风场结论 -->
            <div v-else class="card-body">
              <el-alert
                type="info"
                :closable="false"
                show-icon
                :title="predictionUnavailableReason"
              />
              <div v-if="forecastHours.length > 0" class="binding-note">
                <el-icon :size="14"><Connection /></el-icon>
                <span>
                  仍可参考的风场预测结论：主导来向 {{ windFromLabel }}（{{ formatDegree(forecastWindFrom) }}），
                  平均预测风速 {{ formatNumber(forecastSummary?.wind_speed_ms, 2) }} m/s，
                  但风速偏低或风向不稳，不足以输出定向传播路径。
                </span>
              </div>
            </div>
          </div>

          <!-- 右：预测依据与逐小时风向流线 -->
          <div class="card">
            <div class="card-head">
              <span class="head-icon is-blue">
                <el-icon :size="14"><DataAnalysis /></el-icon>
              </span>
              <span class="head-title">预测依据与逐小时风向流线</span>
              <span v-if="forecastHours.length > 0" class="head-tag is-green">逻辑验证链路 v3.2</span>
            </div>

            <div v-if="forecastHours.length > 0" class="card-body">
              <!-- 关键依据量四格 -->
              <div class="basis-tiles">
                <div v-for="tile in evidenceTiles" :key="tile.label" class="basis-tile">
                  <span class="tile-label">{{ tile.label }}</span>
                  <span class="tile-value">{{ tile.value }}</span>
                  <span class="tile-underline" :style="{ background: tile.color }"></span>
                  <span class="tile-hint">{{ tile.hint }}</span>
                </div>
              </div>

              <!-- 逐小时风向预测表 -->
              <div class="block-title">
                <el-icon :size="13"><Histogram /></el-icon>
                <span>逐小时风向预测与方位偏差表</span>
              </div>
              <table class="forecast-table">
                <thead>
                  <tr>
                    <th>时效</th>
                    <th>风向（输送方向）</th>
                    <th>预测风速</th>
                    <th>方位不确定度（±5°）</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="row in forecastRows" :key="row.lead">
                    <td class="cell-lead">+{{ row.lead }}h</td>
                    <td class="cell-to">
                      {{ row.to }}<small>（{{ row.toLabel }}）</small>
                    </td>
                    <td>{{ row.speed }} m/s</td>
                    <td>
                      <span class="unc-value">±{{ row.unc }}°</span>
                      <div class="unc-track">
                        <div class="unc-fill" :style="{ width: row.uncWidth }"></div>
                      </div>
                    </td>
                  </tr>
                </tbody>
              </table>

              <!-- 路径分段 -->
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
                    <span class="seg-dist">距离 {{ seg.dist }} km</span>
                  </div>
                  <div class="seg-meta">
                    推进 {{ seg.thisHour }} km · 扩散半宽 {{ seg.halfWidth }} km
                  </div>
                  <div class="seg-meta">剩余比例 {{ seg.remain }}</div>
                </div>
              </div>

              <div class="card-foot">
                <span>路径按风速 × 3600 s 逐小时平流推进；方位不确定度随风速离散度放大。</span>
                <span class="foot-brand">Causal Engine Verified</span>
              </div>
            </div>

            <div v-else class="card-body">
              <el-empty :description="predictionUnavailableReason || '暂无风场预测数据'" />
            </div>
          </div>
        </section>

        <!-- ═══════════ 第三行：逐小时气象趋势（左） + 扩散条件综合评估（右） ═══════════ -->
        <section class="row row-half">
          <!-- 左：逐小时气象与沉降趋势 -->
          <div class="card trend-card">
            <div class="card-head">
              <span class="head-icon is-blue">
                <el-icon :size="14"><TrendCharts /></el-icon>
              </span>
              <span class="head-title">逐小时气象与沉降趋势</span>
            </div>
            <div class="trend-inner">
              <WeatherTrend :data="hourlyTrendData" />
            </div>
          </div>

          <!-- 右：扩散条件综合评估 -->
          <div class="card">
            <div class="card-head">
              <span class="head-icon is-blue">
                <el-icon :size="14"><DataAnalysis /></el-icon>
              </span>
              <span class="head-title">扩散条件综合评估</span>
              <span class="head-tag is-orange">综合评定：{{ dispersionCondition }}</span>
            </div>

            <div class="card-body">
              <!-- 六格指标 -->
              <div class="cond-grid">
                <div class="cond-item">
                  <span class="cond-label">平均风速</span>
                  <span class="cond-value">
                    {{ meanWindSpeed }} <small>m/s</small>
                  </span>
                </div>
                <div class="cond-item">
                  <span class="cond-label">主导风向</span>
                  <span class="cond-value">{{ windDirectionLabel }} <small>({{ upwindDirection }}°)</small></span>
                </div>
                <div class="cond-item">
                  <span class="cond-label">平均湿度</span>
                  <span class="cond-value">{{ meanHumidity }} <small>%</small></span>
                </div>
                <div class="cond-item">
                  <span class="cond-label">降雨总量</span>
                  <span class="cond-value">{{ totalPrecipitation }} <small>mm</small></span>
                </div>
                <div class="cond-item">
                  <span class="cond-label">平均温度</span>
                  <span class="cond-value">{{ meanTemperature }} <small>°C</small></span>
                </div>
                <div class="cond-item">
                  <span class="cond-label">扩散评分</span>
                  <span class="cond-value is-danger">
                    {{ dispersionScore }} <small>（{{ riskLevelLabel }}）</small>
                  </span>
                </div>
              </div>

              <!-- 编号证据列表 -->
              <ul class="num-evidence">
                <li
                  v-for="(item, index) in evidence"
                  :key="index"
                  class="num-evidence-item"
                  :class="{ 'is-warn': index === evidence.length - 1 }"
                >
                  <span class="num-chip">{{ index + 1 }}</span>
                  <span class="num-text">{{ item }}</span>
                </li>
              </ul>
            </div>
          </div>
        </section>

        <!-- ═══════════ 第四行：潜在传播风险扇区内敏感目标清单 ═══════════ -->
        <section v-if="hasSpatialData" class="card poi-card">
          <div class="card-head">
            <span class="head-icon is-blue">
              <el-icon :size="14"><OfficeBuilding /></el-icon>
            </span>
            <span class="head-title">潜在传播风险扇区内敏感目标清单</span>
            <span class="head-sub">基于 211 个监测站与敏感设施位置就近归属</span>
            <span class="poi-total">
              扇区总计：<b>{{ sensitivePoiCount }}</b> 个敏感目标
            </span>
          </div>

          <div class="poi-grid">
            <div v-for="card in poiCards" :key="card.key" class="poi-item">
              <div class="poi-head">
                <span class="poi-dot" :style="{ background: card.color }"></span>
                <span class="poi-name">{{ card.label }}</span>
                <span class="poi-tag" :class="card.tagClass">{{ card.tag }}</span>
              </div>
              <div class="poi-coverage">
                <span>覆盖占比：{{ card.percent }}%</span>
                <div class="poi-track">
                  <div class="poi-fill" :style="{ width: card.percent + '%', background: card.color }"></div>
                </div>
              </div>
              <div class="poi-count" :style="{ color: card.color }">{{ card.count }}</div>
              <div class="poi-note">重点点位：{{ card.note }}</div>
            </div>
          </div>
        </section>

      </div>
    </div>
  </MainLayout>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import {
  MapLocation,
  TrendCharts,
  InfoFilled,
  Position,
  Connection,
  DataAnalysis,
  Odometer,
  Histogram,
  OfficeBuilding
} from '@element-plus/icons-vue'
import MainLayout from '@/layouts/MainLayout.vue'
import TaskQueryBar from '@/components/global/TaskQueryBar.vue'
import AnalysisLoading from '@/components/global/AnalysisLoading.vue'
import SpatialMap from '@/components/spatial/SpatialMap.vue'
import WeatherTrend from '@/components/meteorology/WeatherTrend.vue'
import { useAnalysisStore } from '@/stores/analysis'
import type { HourlyWeatherData } from '@/components/meteorology/WeatherTrend.vue'
import type { MeteorologyHourlyItem } from '@/types/meteorology'

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

// ─── 气象风险总览 ───────────────────────────────────────────────
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

const RISK_RING_COLORS: Record<string, string> = {
  low: '#22c55e',
  medium: '#f59e0b',
  high: '#f97316',
  very_high: '#ef4444'
}

/** 综合风险环形仪表 */
const gaugeRingStyle = computed(() => {
  const circumference = 2 * Math.PI * 45
  const offset = circumference - (Math.min(100, riskPercent.value) / 100) * circumference
  return {
    strokeDasharray: `${circumference}`,
    strokeDashoffset: `${offset}`,
    stroke: RISK_RING_COLORS[riskLevel.value] ?? '#3d7eff'
  }
})

/** 四维风险拆解（固定配色，对应截图） */
interface DimBar {
  label: string
  value: number
  color: string
}

const dimensionBars = computed((): DimBar[] => {
  const r = meteorologyResult.value
  return [
    {
      label: '静风积聚',
      value: Math.round((r?.static_wind_score ?? 0) * 100),
      color: '#f59e0b'
    },
    {
      label: '风向突变风险',
      value: Math.round((r?.wind_shift_score ?? 0) * 100),
      color: '#f5615c'
    },
    {
      label: '高湿且雨量不足',
      value: Math.round((r?.humidity_risk_score ?? 0) * 100),
      color: '#14b8a6'
    },
    {
      label: '雨水冲刷不足',
      value: Math.round((r?.insufficient_rain_removal_score ?? 0) * 100),
      color: '#2bb3a3'
    }
  ]
})

const evidence = computed(() => meteorologyResponse.value?.evidence ?? [])

// ─── 风向标签 ───────────────────────────────────────────────────
const windDirectionLabel = computed(() =>
  meteorologyResult.value?.dominant_wind_direction_label ?? '—'
)

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

const predictionAvailable = computed(() => predictionSteps.value.length > 0)

const lastStep = computed(() => predictionSteps.value[predictionSteps.value.length - 1] ?? null)

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
const forecastWindTo = computed(
  () => windForecast.value?.forecast_hours?.[0]?.wind_to_direction ?? null
)

const COMPASS_16 = [
  '北', '北东北', '东北', '东东北',
  '东', '东东南', '东南', '南东南',
  '南', '南西南', '西南', '西西南',
  '西', '西西北', '西北', '北西北'
]

/** 角度 → 十六方位中文简称 */
function compassOf(degree: number | null | undefined): string {
  const num = Number(degree)
  if (!Number.isFinite(num)) return '—'
  const index = Math.round((((num % 360) + 360) % 360) / 22.5) % 16
  return COMPASS_16[index]
}

const windFromLabel = computed(() => compassOf(forecastWindFrom.value))
const windToLabel = computed(() => compassOf(forecastWindTo.value))

function formatDegree(value: number | null | undefined): string {
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

/** 进度条百分比：以本次预测最远距离为满格 */
function distancePercent(distanceKm: number | null |undefined): number {
  const max = lastStep.value?.distance_from_origin_km ?? 0
  const value = Number(distanceKm)
  if (!Number.isFinite(value) || max <= 0) return 0
  return Math.min(100, Math.round((value / max) * 100))
}

// ─── 最终预测结果：阶段命名与配色 ────────────────────────────────
const STAGE_NAMES = ['近距离扩散段', '污染区域输送', '边界层顶扩散段', '远端消散段']

function stageName(index: number): string {
  return STAGE_NAMES[index] ?? `扩散阶段 ${index + 1}`
}

/** 末段橙色（边界层顶扩散），其余蓝色系 */
function stepBarColor(index: number): string {
  const isLast = index === predictionSteps.value.length - 1
  if (isLast) return '#f59e0b'
  return index === 0 ? '#3d7eff' : '#2b5fd9'
}

// ─── 预测依据：关键依据量四格 ────────────────────────────────────
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
      color: '#3d7eff',
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
      color: '#14b8a6',
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

/** 表格行 */
interface ForecastRow {
  lead: number
  to: string
  toLabel: string
  speed: string
  unc: string
  uncWidth: string
}

const forecastRows = computed((): ForecastRow[] => {
  return forecastHours.value.map((hour) => {
    const unc = Number(hour.direction_uncertainty_deg)
    const width = Number.isFinite(unc) ? Math.min(100, (unc / 90) * 100) : 0
    return {
      lead: hour.lead_hour,
      to: formatDegree(hour.wind_to_direction),
      toLabel: compassOf(hour.wind_to_direction),
      speed: formatNumber(hour.wind_speed_ms, 2),
      unc: Number.isFinite(unc) ? unc.toFixed(1) : '—',
      uncWidth: `${width.toFixed(1)}%`
    }
  })
})

/** 路径分段卡片 */
interface SegmentCard {
  lead: number
  dist: string
  thisHour: string
  halfWidth: string
  remain: string
}

const segmentCards = computed((): SegmentCard[] => {
  return predictionSteps.value.map((step) => ({
    lead: step.lead_hour,
    dist: formatNumber(step.distance_from_origin_km, 2),
    thisHour: formatNumber(step.distance_this_hour_km, 2),
    halfWidth: formatNumber(step.corridor_half_width_km, 2),
    remain: formatPercent(step.remaining_fraction)
  }))
})

// ─── 扩散条件综合评估 ───────────────────────────────────────────
const dispersionCondition = computed(() =>
  meteorologyResult.value?.dispersion_condition ?? '—'
)

const dispersionScore = computed(() => {
  const risk = meteorologyResult.value?.weather_risk
  return Number.isFinite(Number(risk)) ? Number(risk).toFixed(2) : '—'
})

const meanWindSpeed = computed(() =>
  meteorologyResult.value?.mean_wind_speed?.toFixed(2) ?? '—'
)

const upwindDirection = computed(() =>
  meteorologyResult.value?.upwind_direction?.toFixed(1) ?? '—'
)

const meanHumidity = computed(() =>
  meteorologyResult.value?.mean_humidity?.toFixed(1) ?? '—'
)

const totalPrecipitation = computed(() =>
  meteorologyResult.value?.total_precipitation?.toFixed(2) ?? '—'
)

const meanTemperature = computed(() =>
  meteorologyResult.value?.mean_temperature?.toFixed(1) ?? '—'
)

// ─── 敏感目标清单 ───────────────────────────────────────────────
const hasSpatialData = computed(() => !!spatialResult.value)

const sensitivePoiCount = computed(() =>
  spatialResult.value?.sensitive_poi_count ?? 0
)

interface PoiCard {
  key: string
  label: string
  tag: string
  tagClass: string
  color: string
  note: string
  count: number
  percent: number
}

const POI_META: Record<string, Omit<PoiCard, 'key' | 'count' | 'percent'>> = {
  school: {
    label: '学校与托幼机构',
    tag: '一般风险',
    tagClass: 'is-blue',
    color: '#3d7eff',
    note: '中小学、幼儿园及托育设施'
  },
  healthcare: {
    label: '医疗卫生机构',
    tag: '高风险',
    tagClass: 'is-red',
    color: '#ef4444',
    note: '医院门诊、诊所及长者护理院舍'
  },
  rail_transit: {
    label: '轨道与交通枢纽',
    tag: '重点关注',
    tagClass: 'is-orange',
    color: '#f59e0b',
    note: '港铁车站、巴士枢纽及换乘站点'
  },
  community_service: {
    label: '社区综合服务中心',
    tag: '常规监测',
    tagClass: 'is-green',
    color: '#22c55e',
    note: '社区会堂、康乐设施与公共服务中心'
  }
}

const POI_ORDER = ['school', 'healthcare', 'rail_transit', 'community_service']

const poiCards = computed((): PoiCard[] => {
  const byType = spatialResult.value?.sensitive_poi_by_type
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
</script>

<style scoped>
.weather-container {
  padding: 0 24px 24px;
}

/* ===== 状态占位 ===== */
.error-state {
  padding: 24px 0;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 300px;
  gap: 12px;
  color: var(--text-secondary);
  font-size: 14px;
}

/* ===== 主内容 ===== */
.weather-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.source-note {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin: 0;
  padding: 10px 14px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--panel-bg);
  font-size: 12px;
  line-height: 18px;
  color: var(--text-secondary);
}

.source-note.is-warn {
  border-color: var(--risk-medium, #e6a23c);
  color: var(--risk-medium, #e6a23c);
}

/* ===== 卡片骨架 ===== */
.row {
  display: grid;
  gap: 16px;
  align-items: stretch;
}

.row-main {
  grid-template-columns: 2fr 1fr;
}

.row-half {
  grid-template-columns: 1fr 1fr;
}

.card {
  background: var(--panel-bg);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.head-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 7px;
  color: #3d7eff;
  background: rgba(61, 126, 255, 0.1);
  flex-shrink: 0;
}

.head-title {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
}

.head-live {
  font-size: 11px;
  color: #3d7eff;
}

.head-sub {
  font-size: 11px;
  color: var(--text-tertiary);
}

.head-tag {
  margin-left: auto;
  font-size: 11px;
  line-height: 1;
  padding: 4px 10px;
  border-radius: 999px;
  white-space: nowrap;
}

.head-tag.is-green {
  color: #16a34a;
  background: rgba(34, 197, 94, 0.1);
}

.head-tag.is-orange {
  color: #d97706;
  background: rgba(245, 158, 11, 0.12);
}

.head-tag.is-gray {
  color: var(--text-tertiary);
  background: var(--bg-page);
}

.pill-danger {
  margin-left: auto;
  font-size: 11px;
  font-weight: 600;
  color: #fff;
  background: #ef4444;
  padding: 4px 12px;
  border-radius: 999px;
  white-space: nowrap;
}

.card-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 14px 16px 16px;
  flex: 1;
}

.card-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.foot-brand {
  font-family: 'SF Mono', Menlo, monospace;
  letter-spacing: 0.4px;
  white-space: nowrap;
}

/* ===== ① 主地图卡片 ===== */
.map-card {
  display: flex;
  flex-direction: column;
}

.map-inner {
  min-height: 560px;
  flex: 1;
}

/* ===== ① 气象风险总览 ===== */
.risk-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 14px 16px 16px;
  flex: 1;
}

.gauge-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 12px 0 4px;
}

.gauge-ring {
  position: relative;
  width: 128px;
  height: 128px;
}

.gauge-ring svg {
  width: 100%;
  height: 100%;
  transform: rotate(-90deg);
}

.ring-bg {
  fill: none;
  stroke: #eef1f6;
  stroke-width: 11;
}

.ring-fill {
  fill: none;
  stroke-width: 11;
  stroke-linecap: round;
  transition: stroke-dashoffset 0.8s ease;
}

.gauge-center {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
}

.gauge-value {
  font-size: 30px;
  font-weight: 800;
  color: var(--text-primary);
  line-height: 1;
}

.gauge-value small {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-tertiary);
}

.gauge-level {
  font-size: 12px;
  font-weight: 600;
}

.gauge-level.lv-low { color: #22c55e; }
.gauge-level.lv-medium { color: #f59e0b; }
.gauge-level.lv-high { color: #f97316; }
.gauge-level.lv-very_high { color: #ef4444; }

.gauge-caption {
  font-size: 11px;
  color: var(--text-tertiary);
}

/* 四维拆解条 */
.dim-bars {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.dim-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.dim-label {
  width: 96px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-secondary);
}

.dim-track {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: #eef1f6;
  overflow: hidden;
}

.dim-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.5s ease;
}

.dim-value {
  width: 40px;
  text-align: right;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}

/* 证据链 */
.evi-box {
  border-top: 1px solid var(--border-color);
  padding-top: 12px;
}

.evi-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 8px;
}

.evi-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.evi-item {
  position: relative;
  padding-left: 12px;
  font-size: 11px;
  line-height: 17px;
  color: var(--text-secondary);
}

.evi-item::before {
  content: '';
  position: absolute;
  left: 0;
  top: 7px;
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #3d7eff;
}

/* ===== ② 结论三指标 ===== */
.hero-tiles {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}

.hero-tile {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px;
  background: #f8fafc;
  border: 1px solid var(--border-color);
  border-radius: 10px;
}

.tile-label {
  font-size: 11px;
  color: var(--text-tertiary);
  padding-bottom: 5px;
  border-bottom: 2px solid transparent;
  width: fit-content;
}

.tile-label.is-blue { color: #3d7eff; border-color: #3d7eff; }
.tile-label.is-teal { color: #14b8a6; border-color: #14b8a6; }
.tile-label.is-gray { color: var(--text-tertiary); border-color: #cbd5e1; }

.tile-value {
  font-size: 22px;
  font-weight: 800;
  color: var(--text-primary);
  line-height: 1.1;
}

.tile-value.is-blue {
  color: #3d7eff;
}

.tile-value small {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-tertiary);
}

/* 绑定说明 */
.binding-note {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  background: var(--bg-page);
  border-radius: 8px;
  border-left: 3px solid #3d7eff;
  font-size: 12px;
  line-height: 18px;
  color: var(--text-secondary);
}

/* 逐小时结论 */
.result-steps {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.result-step {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.step-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.step-chip {
  font-size: 11px;
  font-weight: 700;
  color: #3d7eff;
  background: rgba(61, 126, 255, 0.1);
  padding: 2px 8px;
  border-radius: 999px;
  flex-shrink: 0;
}

.step-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.step-distance {
  margin-left: auto;
  font-size: 14px;
  font-weight: 800;
  color: var(--text-primary);
}

.step-track {
  height: 8px;
  border-radius: 4px;
  background: #eef1f6;
  overflow: hidden;
}

.step-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.5s ease;
}

.step-meta {
  font-size: 11px;
  color: var(--text-tertiary);
  text-align: right;
}

.map-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  background: rgba(61, 126, 255, 0.07);
  border-radius: 8px;
  font-size: 11px;
  line-height: 16px;
  color: #3d7eff;
}

/* ===== ② 预测依据四格 ===== */
.basis-tiles {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}

.basis-tile {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 10px 12px;
  background: #f8fafc;
  border: 1px solid var(--border-color);
  border-radius: 10px;
}

.basis-tile .tile-label {
  border-bottom: none;
  padding-bottom: 0;
}

.basis-tile .tile-value {
  font-size: 17px;
}

.tile-underline {
  height: 3px;
  border-radius: 2px;
}

.tile-hint {
  font-size: 10px;
  line-height: 13px;
  color: var(--text-tertiary);
}

/* 区块小标题 */
.block-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  padding-left: 8px;
  border-left: 3px solid #3d7eff;
  line-height: 1.2;
  margin-top: 2px;
}

/* 预测表 */
.forecast-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.forecast-table th {
  padding: 7px 8px;
  text-align: left;
  font-weight: 600;
  color: var(--text-secondary);
  background: #f8fafc;
  border-bottom: 1px solid var(--border-color);
  white-space: nowrap;
}

.forecast-table td {
  padding: 8px;
  color: var(--text-primary);
  border-bottom: 1px solid var(--border-color);
  vertical-align: middle;
}

.forecast-table tbody tr:last-child td {
  border-bottom: none;
}

.cell-lead {
  font-weight: 700;
  color: #3d7eff;
  white-space: nowrap;
}

.cell-to {
  font-weight: 700;
  color: #f59e0b;
  white-space: nowrap;
}

.cell-to small {
  font-size: 10px;
  font-weight: 500;
  color: var(--text-tertiary);
}

.unc-value {
  display: block;
  font-size: 11px;
  color: var(--text-secondary);
  margin-bottom: 3px;
}

.unc-track {
  width: 100%;
  min-width: 54px;
  height: 4px;
  background: var(--border-color);
  border-radius: 2px;
  overflow: hidden;
}

.unc-fill {
  height: 100%;
  background: linear-gradient(90deg, #14b8a6, #f59e0b, #ef4444);
  border-radius: 2px;
}

/* 路径分段卡 */
.segment-cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
}

.segment-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px;
  background: #f8fafc;
  border: 1px solid var(--border-color);
  border-left: 3px solid #3d7eff;
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
  font-weight: 600;
  color: var(--text-primary);
}

.seg-dist {
  font-size: 11px;
  font-weight: 700;
  color: #3d7eff;
  white-space: nowrap;
}

.seg-meta {
  font-size: 10px;
  color: var(--text-tertiary);
}

/* ===== ③ 趋势图 ===== */
.trend-inner {
  min-height: 280px;
  padding: 12px;
  flex: 1;
}

/* ===== ③ 扩散条件六格 ===== */
.cond-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
}

.cond-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 12px 14px;
  border-bottom: 1px solid var(--border-color);
}

.cond-item:nth-last-child(-n + 3) {
  border-bottom: none;
}

.cond-label {
  font-size: 11px;
  color: var(--text-tertiary);
}

.cond-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
}

.cond-value.is-danger {
  color: #ef4444;
}

.cond-value small {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-tertiary);
}

.cond-value.is-danger small {
  color: #ef4444;
}

/* 编号证据 */
.num-evidence {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.num-evidence-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
}

.num-evidence-item.is-warn {
  background: #fff7e6;
}

.num-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #eef1f6;
  color: var(--text-secondary);
  font-size: 10px;
  font-weight: 700;
  flex-shrink: 0;
  margin-top: 1px;
}

.is-warn .num-chip {
  background: #f59e0b;
  color: #fff;
}

.num-text {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-secondary);
}

/* ===== ④ 敏感目标清单 ===== */
.poi-total {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
}

.poi-total b {
  font-size: 18px;
  font-weight: 800;
  color: #ef4444;
  margin: 0 2px;
}

.poi-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  padding: 14px 16px 16px;
}

.poi-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px;
  background: #f8fafc;
  border: 1px solid var(--border-color);
  border-radius: 10px;
}

.poi-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.poi-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.poi-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.poi-tag {
  margin-left: auto;
  font-size: 10px;
  line-height: 1;
  padding: 3px 8px;
  border-radius: 999px;
  white-space: nowrap;
}

.poi-tag.is-blue { color: #3d7eff; background: rgba(61, 126, 255, 0.1); }
.poi-tag.is-red { color: #ef4444; background: rgba(239, 68, 68, 0.1); }
.poi-tag.is-orange { color: #d97706; background: rgba(245, 158, 11, 0.12); }
.poi-tag.is-green { color: #16a34a; background: rgba(34, 197, 94, 0.1); }

.poi-coverage {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 11px;
  color: var(--text-tertiary);
}

.poi-track {
  height: 5px;
  border-radius: 3px;
  background: #eef1f6;
  overflow: hidden;
}

.poi-fill {
  height: 100%;
  border-radius: 3px;
}

.poi-count {
  font-size: 30px;
  font-weight: 800;
  line-height: 1;
}

.poi-note {
  font-size: 10px;
  line-height: 14px;
  color: var(--text-tertiary);
  margin-top: auto;
}

/* ===== 响应式 ===== */
@media (max-width: 1200px) {
  .row-main,
  .row-half {
    grid-template-columns: 1fr;
  }

  .poi-grid {
    grid-template-columns: repeat(2, 1fr);
  }

  .basis-tiles {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 800px) {
  .weather-container {
    padding: 0 16px 16px;
  }

  .hero-tiles {
    grid-template-columns: 1fr;
  }

  .cond-grid {
    grid-template-columns: 1fr;
  }

  .cond-item:nth-last-child(-n + 3) {
    border-bottom: 1px solid var(--border-color);
  }

  .cond-item:last-child {
    border-bottom: none;
  }

  .poi-grid {
    grid-template-columns: 1fr;
  }
}
</style>
