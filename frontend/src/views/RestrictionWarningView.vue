<template>
  <MainLayout>
    <!-- 任务查询栏 -->
    <TaskQueryBar />

    <div class="restriction-container">
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
      <div v-if="analysisStore.result && !analysisStore.analysisScopeDirty" class="restriction-content">

        <!-- ① 顶部六卡指标带 -->
        <section class="kpi-section">
          <RestrictionKpiStrip :items="kpiItems" />
        </section>

        <!-- ② 地图 + 治理决策规程（左右布局） -->
        <section class="map-decision-section">
          <!-- 左：空间地图 -->
          <div class="map-wrapper">
            <div class="card-header-bar">
              <span class="header-title">
                <el-icon :size="16"><MapLocation /></el-icon>
                空间传播与上风向污染态势
              </span>
              <div class="header-chips">
                <span v-if="coordChip" class="chip chip-mono">{{ coordChip }}</span>
                <span v-if="windChip" class="chip">{{ windChip }}</span>
                <span v-if="roadCoverChip" class="chip">路网覆盖 {{ roadCoverChip }}</span>
              </div>
            </div>
            <div class="map-inner">
              <SpatialMap
                :spatial="spatialResult"
                :meteorology="meteorologyResult"
              />
            </div>
            <div class="map-float-badge">建议分流路段: {{ suggestedRoads.length }} 条</div>
          </div>

          <!-- 右：治理决策规程 -->
          <div class="decision-wrapper">
            <RestrictionDecisionPanel
              :decision-level="decisionLevel"
              :suggested-area="suggestedArea"
              :suggested-time="suggestedTime"
              :vehicle-focus="vehicleFocus"
              :actions="actions"
              :review-indicators="reviewIndicators"
              :road-rows="decisionRoadRows"
            />
          </div>
        </section>

        <!-- ③ 来源画像 + 交通污染负担（环形图） + 溯源证据链（三列布局） -->
        <section class="analysis-section">
          <!-- 左：来源可能性综合画像 -->
          <div class="panel-card">
            <div class="panel-header">
              <div class="header-titles">
                <span class="panel-title">污染来源可能性综合画像</span>
                <span class="panel-subtitle">Source Possibility Screening Profile</span>
              </div>
              <span class="panel-note">筛查评分，非贡献率</span>
            </div>
            <SourceProbabilityBar :data="sourceAttributionMapped" />
          </div>

          <!-- 中：交通污染负担与车型结构（车型结构为环形图） -->
          <TrafficPressureCard
            :score="trafficPressureScore"
            :level="trafficPressureLevel"
            :peak-time-slot="trafficPeakTimeSlot"
            :suggestion="trafficSuggestion"
          >
            <VehicleDonutChart :data="vehicleProportions" />
          </TrafficPressureCard>

          <!-- 右：污染溯源因果证据链推理 -->
          <EvidenceChain :data="evidenceItems" :confidence="sourceConfidence" />
        </section>

      </div>
    </div>
  </MainLayout>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { MapLocation } from '@element-plus/icons-vue'
import MainLayout from '@/layouts/MainLayout.vue'
import TaskQueryBar from '@/components/global/TaskQueryBar.vue'
import AnalysisLoading from '@/components/global/AnalysisLoading.vue'
import { initialPropagationDirection } from '@/utils/propagationDirection'
import SpatialMap from '@/components/spatial/SpatialMap.vue'
import SourceProbabilityBar from '@/components/source/SourceProbabilityBar.vue'
import EvidenceChain from '@/components/source/EvidenceChain.vue'
import TrafficPressureCard from '@/components/traffic/TrafficPressureCard.vue'
import VehicleDonutChart from '@/components/traffic/VehicleDonutChart.vue'
import RestrictionKpiStrip from '@/components/restriction/RestrictionKpiStrip.vue'
import RestrictionDecisionPanel from '@/components/restriction/RestrictionDecisionPanel.vue'
import { useAnalysisStore } from '@/stores/analysis'
import { getSourceJudgementLabel } from '@/utils/sourceMapper'
import type { EvidenceItem } from '@/components/source/EvidenceChain.vue'
import type { SourceAttribution } from '@/components/source/SourceProbabilityBar.vue'
import type { VehicleProportion } from '@/components/traffic/VehicleDonutChart.vue'
import type { PressureLevel } from '@/components/traffic/TrafficPressureCard.vue'
import type { KpiItem } from '@/components/restriction/RestrictionKpiStrip.vue'
import type { RoadRow } from '@/components/restriction/RestrictionDecisionPanel.vue'

const analysisStore = useAnalysisStore()

// 页面挂载时确保已有数据
onMounted(async () => {
  if (!analysisStore.result) {
    await analysisStore.loadInitial()
  }
})

// ─── 原始 result 快捷引用 ───────────────────────────────────────
const sourceResponse   = computed(() => analysisStore.result?.source)
const decisionResponse = computed(() => analysisStore.result?.decision)
const spatialResponse  = computed(() => analysisStore.result?.spatial)
const trafficResponse  = computed(() => analysisStore.result?.traffic)
const meteoResponse    = computed(() => analysisStore.result?.meteorology)

// ─── SpatialMap Props ──────────────────────────────────────────
const spatialResult = computed(() => spatialResponse.value?.result ?? null)
const meteorologyResult = computed(() => meteoResponse.value?.result ?? null)

// ─── 通用小工具 ────────────────────────────────────────────────
/** 从 "2023-08-15 08:00:00 至 2023-08-15 11:00:00" 里抠出 "08:00 - 11:00" */
function shortTimeRange(raw: string): string {
  const times = raw.match(/\d{2}:\d{2}/g)
  if (times && times.length >= 2) return `${times[0]} - ${times[1]}`
  return raw || '—'
}

/** 风险等级 → KPI 标签色调 */
function riskTone(level: string): KpiItem['tone'] {
  if (level === 'low') return 'green'
  if (level === 'medium') return 'orange'
  return 'red'
}

/** 车型英文代码 → 短中文 */
const SHORT_VEHICLE: Record<string, string> = {
  'Franchised Bus DD': '专营巴士',
  'Franchised Bus SD': '专营巴士',
  'Private Car': '私家车',
  'Taxi': '出租车',
  'Goods Vehicle Light': '轻型货车',
  'Goods Vehicle M&H': '中重型货车',
  'Motor Cycle': '电单车',
  'Non Franchised Bus': '非专营巴士',
  'Private LB': '私家小型巴士',
  'PLB': '小巴'
}

function shortVehicleName(code: string): string {
  return SHORT_VEHICLE[code] ?? code
}

// ─── 关键字段 ──────────────────────────────────────────────────
const mainJudgement = computed(() =>
  sourceResponse.value?.result?.main_judgement ?? ''
)
const mainJudgementLabel = computed(() => getSourceJudgementLabel(mainJudgement.value))

const sourceRiskLevel = computed((): 'low' | 'medium' | 'high' | 'very_high' => {
  const level = sourceResponse.value?.result?.risk_level
  return (level as 'low' | 'medium' | 'high' | 'very_high') ?? 'low'
})

const sourceConfidence = computed(() => sourceResponse.value?.confidence ?? 0)

const trafficSourceScore = computed(() =>
  sourceResponse.value?.result?.source_attribution?.traffic_source ?? 0
)

const decisionLevel = computed((): 'low' | 'medium' | 'high' | 'very_high' => {
  return (decisionResponse.value?.result?.decision_level ?? 'low') as 'low' | 'medium' | 'high' | 'very_high'
})

const suggestedArea = computed(() =>
  decisionResponse.value?.result?.suggested_area ?? ''
)

const suggestedTime = computed(() =>
  decisionResponse.value?.result?.suggested_time ?? ''
)

const suggestedRoads = computed(() =>
  decisionResponse.value?.result?.suggested_roads ?? []
)

const vehicleFocus = computed(() =>
  decisionResponse.value?.result?.vehicle_focus ?? []
)

const actions = computed(() =>
  decisionResponse.value?.result?.actions ?? []
)

const reviewIndicators = computed(() =>
  decisionResponse.value?.result?.review_indicators ?? []
)

// ─── ① 六卡指标带 ──────────────────────────────────────────────
const RISK_LABEL: Record<string, string> = {
  low: '低风险', medium: '中风险', high: '高风险', very_high: '极高风险'
}
const LEVEL_STRATEGY: Record<string, string> = {
  low: '常规监测', medium: '重点管控', high: '优先级管控', very_high: '优先级管控'
}
const DECISION_LABEL: Record<string, string> = {
  low: '低级响应', medium: '中级响应', high: '高级响应', very_high: '高级响应'
}
const SPATIAL_STABILITY: Record<string, { tag: string; tone: KpiItem['tone'] }> = {
  high: { tag: '高度稳定', tone: 'green' },
  medium: { tag: '基本稳定', tone: 'orange' },
  low: { tag: '待复核', tone: 'gray' }
}
const HEAVY_SIGNAL: Record<string, string> = {
  low: '低', medium: '中', high: '高'
}

const kpiItems = computed((): KpiItem[] => {
  const src  = sourceResponse.value
  const trf  = trafficResponse.value?.result
  const spa  = spatialResponse.value
  const sparResult = spa?.result
  const met  = meteoResponse.value?.result
  const dec  = decisionResponse.value?.result
  if (!src || !trf || !sparResult) return []

  const spatialTag = SPATIAL_STABILITY[sparResult.spatial_confidence]
    ?? { tag: sparResult.spatial_confidence, tone: 'gray' as KpiItem['tone'] }

  const congestionPct = Math.round(trf.traffic_pressure_score * 100)
  const sourcePct = Math.round(trafficSourceScore.value * 100)
  const confPct = Math.round(sourceConfidence.value * 100)

  const items: KpiItem[] = []

  // 卡1 · 溯源诊断总览
  items.push({
    key: 'diagnosis',
    title: '交通移动源限制与区域预警治理',
    subtitle: 'Traffic Mobile Source Advisory',
    tag: RISK_LABEL[sourceRiskLevel.value] ?? sourceRiskLevel.value,
    tone: riskTone(sourceRiskLevel.value),
    main: LEVEL_STRATEGY[dec?.decision_level ?? 'low'] ?? '常规监测',
    bar: sourcePct,
    rows: [
      { label: '来源判断', value: mainJudgementLabel.value },
      { label: '交通源评分', value: `${sourcePct}%` },
      { label: '分析置信度', value: `${confPct}%` }
    ]
  })

  // 卡2 · 交通拥堵评估
  items.push({
    key: 'congestion',
    title: '交通拥堵评估',
    subtitle: 'Traffic Congestion Assessment',
    tag: RISK_LABEL[trf.traffic_pressure_level] ?? trf.traffic_pressure_level,
    tone: riskTone(trf.traffic_pressure_level),
    main: String(congestionPct),
    mainSuffix: '/ 100 拥堵指数',
    bar: congestionPct,
    rows: [
      { label: '峰值时段', value: trafficResponse.value?.meta?.peak_time_slot ?? '—' },
      { label: '主导车型', value: trf.dominant_vehicle_types.map(shortVehicleName).join(' / ') || '—' },
      { label: '交通压力', value: RISK_LABEL[trf.traffic_pressure] ?? trf.traffic_pressure }
    ]
  })

  // 卡3 · 路网匹配稳定性
  items.push({
    key: 'spatial',
    title: '路网匹配稳定性',
    subtitle: 'Spatial Matching Stability',
    tag: spatialTag.tag,
    tone: spatialTag.tone,
    main: `${Math.round((spa?.confidence ?? 0) * 1000) / 10}%`,
    mainSuffix: '路网稳定率',
    bar: Math.round((spa?.confidence ?? 0) * 100),
    rows: [
      { label: '上风向道路', value: `${sparResult.upwind_roads.length} 条` },
      { label: '周边道路', value: `${sparResult.nearby_road_count} 条 / ${sparResult.nearby_road_segment_count} 段` },
      { label: '敏感目标', value: `${sparResult.sensitive_poi_count} 处` }
    ]
  })

  // Preserve the original static radius; only align its orientation with the propagation page.
  const direction = initialPropagationDirection(met?.pollution_path_prediction)
  const scope = sparResult.directional_exposure
  items.push({
    key: 'downwind',
    title: '下风向影响半径',
    subtitle: 'Downwind Impact Radius',
    tag: '静态参考扇区',
    tone: 'blue',
    main: sparResult.analysis_radius_km.toFixed(1),
    mainUnit: 'km 扇区半径',
    rows: [
      { label: '扇区敏感目标', value: scope?.status === 'ok' ? `${scope.sensitive_poi_count} 处` : '—' },
      { label: '传播方位', value: direction !== null ? `${direction.toFixed(1)}°` : '—' },
      { label: '平均风速', value: met?.mean_wind_speed != null ? `${met.mean_wind_speed.toFixed(2)} m/s` : '—' }
    ]
  })

  // 卡5 · 管控响应等级
  items.push({
    key: 'response',
    title: '管控响应等级',
    subtitle: 'Control Response Level',
    tag: `LEVEL-${decisionLevel.value === 'high' || decisionLevel.value === 'very_high' ? 1 : decisionLevel.value === 'medium' ? 2 : 3}`,
    tone: decisionLevel.value === 'high' || decisionLevel.value === 'very_high' ? 'purple' : decisionLevel.value === 'medium' ? 'orange' : 'green',
    main: DECISION_LABEL[decisionLevel.value] ?? decisionLevel.value,
    rows: [
      { label: '管控时段', value: shortTimeRange(suggestedTime.value) },
      { label: '建议区域', value: suggestedArea.value.includes('downwind') ? '下风向 3 km 扇区' : (suggestedArea.value || '—') },
      { label: '重点车型', value: vehicleFocus.value.map(shortVehicleName).join(' / ') || '—' }
    ]
  })

  // 卡6 · 交通压力指数
  items.push({
    key: 'pressure',
    title: '交通压力指数',
    subtitle: 'Traffic Pressure Index',
    tag: RISK_LABEL[trf.traffic_pressure_level] ?? trf.traffic_pressure_level,
    tone: riskTone(trf.traffic_pressure_level),
    main: trf.traffic_pressure_score.toFixed(2),
    mainUnit: '指数',
    bar: Math.round(trf.traffic_pressure_score * 100),
    rows: [
      { label: '峰值时段', value: trafficResponse.value?.meta?.peak_time_slot ?? '—' },
      { label: '重型车信号', value: HEAVY_SIGNAL[trf.heavy_vehicle_signal] ?? trf.heavy_vehicle_signal },
      { label: '巴士占比', value: `${Math.round(trf.bus_ratio * 100)}%` }
    ]
  })

  return items
})

// ─── ② 地图卡片信息条 ──────────────────────────────────────────
const coordChip = computed(() => {
  const station = spatialResult.value?.station
  if (!station) return ''
  return `${station.lon.toFixed(3)}, ${station.lat.toFixed(3)}`
})

const windChip = computed(() => {
  const met = meteorologyResult.value
  if (!met) return ''
  const label = met.dominant_wind_direction_label
  const deg = met.dominant_wind_direction
  const speed = met.mean_wind_speed
  const parts: string[] = []
  if (label) parts.push(label)
  if (deg != null) parts.push(`${Math.round(deg)}°`)
  if (speed != null) parts.push(`${speed.toFixed(2)} m/s`)
  return parts.length > 0 ? `时段主导来风 ${parts.join(' · ')}` : ''
})

const roadCoverChip = computed(() => {
  const sp = spatialResult.value
  if (!sp) return ''
  return `${sp.nearby_road_count} 条 / ${sp.nearby_road_segment_count} 段`
})

// ─── ② 决策面板 · 干道清单（决策道路 × 上风向道路匹配） ─────────
const decisionRoadRows = computed((): RoadRow[] => {
  const upwind = spatialResult.value?.upwind_roads ?? []
  return suggestedRoads.value.map(name => {
    const hit = upwind.find(r => r.road_name.toLowerCase() === name.toLowerCase())
    return {
      name,
      score: hit ? hit.match_score : null,
      distance: hit ? hit.distance_to_station_km : null
    }
  })
})

// ─── ③ 来源可能性评分（字段重映射：Mock 字段名 → 组件字段名） ───
const sourceAttributionMapped = computed((): SourceAttribution => {
  const attr = sourceResponse.value?.result?.source_attribution
  return {
    traffic_source:     attr?.traffic_source ?? 0,
    local_accumulation: attr?.local_accumulation ?? 0,
    regional_transport: attr?.regional_transport ?? 0,
    road_dust:          (attr as any)?.dust_source ?? 0,
    photochemical:      (attr as any)?.photochemical_source ?? 0
  }
})

// ─── ③ 交通污染负担与车型结构 ─────────────────────────────────
const trafficPressureScore = computed(() =>
  trafficResponse.value?.result?.traffic_pressure_score ?? null
)

const trafficPressureLevel = computed((): PressureLevel => {
  return (trafficResponse.value?.result?.traffic_pressure_level ?? 'low') as PressureLevel
})

const trafficPeakTimeSlot = computed(() =>
  trafficResponse.value?.meta?.peak_time_slot ?? undefined
)

/** 占比 >= 1% 的车型，按占比从高到低 */
const vehicleProportions = computed((): VehicleProportion[] => {
  const pct = trafficResponse.value?.result?.proportions_pct
  if (!pct) return []
  return Object.entries(pct)
    .filter(([, percent]) => percent >= 1)
    .sort(([, a], [, b]) => b - a)
    .map(([vehicle_type, percent]) => ({ vehicle_type, percent }))
})

/** 底部建议：占比最高的两种车型 */
const trafficSuggestion = computed(() => {
  const top = vehicleProportions.value.slice(0, 2)
  if (top.length === 0) return ''
  return top.map(item => shortVehicleName(item.vehicle_type)).join('与')
})

// ─── ④ 证据链 ──────────────────────────────────────────────────
const evidenceItems = computed((): EvidenceItem[] => {
  const raw = sourceResponse.value?.evidence ?? []
  return raw.map((text, i) => {
    const isHighScore = /0\.[7-9]\d*|1\.0/.test(text)
    const isHighWord  = /high|异常|超标|显著/i.test(text)
    return {
      step: text,
      status: (isHighScore || isHighWord) ? 'success' : 'default',
      isActive: i === 0
    } satisfies EvidenceItem
  })
})
</script>

<style scoped>
.restriction-container {
  padding: 0 24px 24px;
}

.scope-stale-state { padding: 18px 24px 0; }

/* ===== 状态占位 ===== */
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

.error-state {
  padding: 24px 0;
}

/* ===== 主内容 ===== */
.restriction-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ===== ② 地图 + 决策 ===== */
.map-decision-section {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(360px, 1fr);
  gap: 16px;
  align-items: stretch;
}

.map-wrapper {
  background: var(--panel-bg);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  position: relative;
}

.card-header-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.header-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.header-chips {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.chip {
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-page);
  border-radius: 4px;
  padding: 4px 8px;
  white-space: nowrap;
}

.chip-mono {
  font-variant-numeric: tabular-nums;
}

.map-inner {
  flex: 1;
  min-height: 480px;
}

/* 地图右下角悬浮徽标 */
.map-float-badge {
  position: absolute;
  right: 14px;
  bottom: 14px;
  z-index: 1100;
  font-size: 12px;
  font-weight: 600;
  color: #c22f2f;
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid rgba(239, 68, 68, 0.35);
  border-radius: 6px;
  padding: 7px 12px;
  box-shadow: var(--shadow-sm);
}

.decision-wrapper {
  min-height: 480px;
  display: flex;
  flex-direction: column;
}

/* ===== ③ 分析详情（三列：来源画像 / 交通负担 / 证据链） ===== */
.analysis-section {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.05fr) minmax(0, 1.15fr);
  gap: 16px;
  align-items: stretch;
}

.panel-card {
  background: var(--panel-bg);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.header-titles {
  min-width: 0;
}

.panel-title {
  display: block;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.panel-subtitle {
  display: block;
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
}

.panel-note {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-tertiary);
  background: var(--bg-page);
  border-radius: 4px;
  padding: 4px 8px;
  white-space: nowrap;
}

/* ===== 响应式 ===== */
@media (max-width: 1300px) {
  .map-decision-section {
    grid-template-columns: 1fr;
  }

  .decision-wrapper {
    min-height: auto;
  }

  .analysis-section {
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }

  .analysis-section > :last-child {
    grid-column: 1 / -1;
  }
}

@media (max-width: 900px) {
  .analysis-section {
    grid-template-columns: 1fr;
  }

  .restriction-container {
    padding: 0 16px 16px;
  }
}
</style>
