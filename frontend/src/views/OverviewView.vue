<template>
  <MainLayout>
    <!-- 任务查询栏 -->
    <TaskQueryBar />

    <div class="overview-container">
      <!-- 加载状态 --><AnalysisLoading
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
        <el-empty description="暂无分析数据" />
      </div>

      <!-- 正常数据展示 -->
      <div v-if="analysisStore.result" class="overview-content">
        <!-- KPI 概览（五卡） -->
        <section class="kpi-section">
          <KPIOverview :data="kpiData" />
        </section>

        <!-- 核心区域：地图（左宽）+ 智能研判助手（右窄） -->
        <section class="core-section">
          <div class="map-container-wrapper" :class="{ 'highlight-pulse': mapHighlight }">
            <div class="map-header">
              <span class="map-header-icon">
                <el-icon :size="16"><MapLocation /></el-icon>
              </span>
              <span class="map-title">空间传播与区域影响</span>
              <span class="map-subtitle">高精度风羽矢量模型 &amp; 敏感目标辐射场模拟</span>
            </div>
            <div class="map-container-inner">
              <SpatialMap :spatial="spatialData" :meteorology="meteorologyData" />
            </div>
          </div>

          <!-- 对话问答模块：识别气象文本并实时联动视图 -->
          <ChatQAPanel @focus-section="onChatFocus" />
        </section>

        <!-- 底部三卡：综合研判 / 来源评分 / 车型结构 -->
        <section class="bottom-section">
          <!-- 当前综合研判 -->
          <div class="judgment-panel">
            <div class="panel-header">
              <span class="panel-header-icon">
                <el-icon :size="14"><Document /></el-icon>
              </span>
              <span class="panel-title">当前综合研判</span>
              <span class="panel-id">ID: {{ decisionId }}</span>
            </div>

            <!-- 主要来源研判结论（高亮框） -->
            <div class="conclusion-box">
              <div class="conclusion-label">主要来源研判结论</div>
              <div class="conclusion-value">{{ mainJudgementText }}</div>
            </div>

            <!-- 决策依据引导 -->
            <div class="decision-row">
              <span class="decision-label">决策依据引导</span>
              <span class="decision-tag" :class="`level-${decisionLevel}`">{{ decisionLevelText }}</span>
            </div>

            <!-- 现场治理调控建议 -->
            <div class="advice-section">
              <div class="advice-title">
                <el-icon :size="13"><Flag /></el-icon>
                <span>现场治理调控建议</span>
              </div>
              <ul class="advice-list">
                <li
                  v-for="(action, index) in topActions"
                  :key="index"
                  :class="index === topActions.length - 1 ? 'dot-red' : 'dot-blue'"
                >
                  {{ action }}
                </li>
              </ul>
              <p v-if="hasMoreActions" class="more-hint">（共 {{ totalActionsCount }} 项建议）</p>
            </div>

            <div class="panel-footer">
              <span>协同部门：交通运输署 / 环保局</span>
              <span>自动研判生成</span>
            </div>
          </div>

          <!-- 污染来源可能性评分 -->
          <el-card class="chart-card" shadow="never">
            <template #header>
              <div class="card-header-content">
                <span class="card-header-icon">
                  <el-icon :size="14"><Search /></el-icon>
                </span>
                <span class="card-title">污染来源可能性评分</span>
                <span class="card-subtitle">筛查评分 / 非贡献率</span>
              </div>
            </template>
            <div class="chart-body">
              <SourceProbabilityBar :data="sourceAttributionData" />
            </div>
            <div class="card-footer">
              <span>算法：多源指标反射模型</span>
              <span>高敏感置信度</span>
            </div>
          </el-card>

          <!-- 车型结构分析 -->
          <el-card class="chart-card" shadow="never">
            <template #header>
              <div class="card-header-content">
                <span class="card-header-icon icon-cyan">
                  <el-icon :size="14"><Refresh /></el-icon>
                </span>
                <span class="card-title">车型结构分析</span>
                <span class="card-subtitle">主要干道车型特征</span>
              </div>
            </template>
            <div class="chart-body">
              <VehicleStructureChart :data="vehicleStructureData" />

              <!-- 交通污染压力指标（内嵌框） -->
              <div v-if="trafficPressureScore !== null" class="pressure-box">
                <div class="pressure-left">
                  <span class="pressure-label">交通污染压力指标</span>
                  <div class="pressure-value">
                    <b>{{ trafficPressureScoreDisplay }}</b>
                    <i>/ 100</i>
                    <span v-if="trafficPressureLevel === 'high' || trafficPressureLevel === 'very_high'" class="pressure-pill">高</span>
                  </div>
                </div>
                <div class="pressure-right">
                  <span class="pressure-label">峰值时段</span>
                  <span class="peak-chip">{{ trafficPeakTimeSlotDisplay || '—' }}</span>
                </div>
              </div>
            </div>
            <div class="card-footer">
              <span>ATC 车型自动识别</span>
              <span>{{ trafficYearNote }}</span>
            </div>
          </el-card>
        </section>
      </div>
    </div>
  </MainLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  MapLocation,
  Document,
  Search,
  Warning,
  MostlyCloudy,
  Van,
  DataAnalysis,
  Aim,
  Flag,
  Refresh
} from '@element-plus/icons-vue'
import MainLayout from '@/layouts/MainLayout.vue'
import TaskQueryBar from '@/components/global/TaskQueryBar.vue'
import AnalysisLoading from '@/components/global/AnalysisLoading.vue'
import KPIOverview from '@/components/overview/KPIOverview.vue'
import ChatQAPanel from '@/components/overview/ChatQAPanel.vue'
import VehicleStructureChart from '@/components/traffic/VehicleStructureChart.vue'
import SourceProbabilityBar from '@/components/source/SourceProbabilityBar.vue'
import SpatialMap from '@/components/spatial/SpatialMap.vue'
import { useAnalysisStore } from '@/stores/analysis'
import { getSourceJudgementLabel } from '@/utils/sourceMapper'
import { getVehicleLabel } from '@/utils/vehicleMapper'
import type { KPIItem } from '@/components/overview/KPIOverview.vue'
import type { VehicleProportion } from '@/components/traffic/VehicleStructureChart.vue'
import type { SourceAttribution } from '@/components/source/SourceProbabilityBar.vue'

const analysisStore = useAnalysisStore()

// ---------- 对话问答：识别到气象要素后联动高亮地图区 ----------
const mapHighlight = ref(false)
let highlightTimer: ReturnType<typeof setTimeout> | null = null

function onChatFocus(topic: string): void {
  void topic
  if (highlightTimer) clearTimeout(highlightTimer)
  mapHighlight.value = false
  requestAnimationFrame(() => {
    mapHighlight.value = true
    highlightTimer = setTimeout(() => {
      mapHighlight.value = false
      highlightTimer = null
    }, 1600)
  })
}

// ---------- 页面挂载时跑一遍真实分析链路 ----------
onMounted(async () => {
  if (!analysisStore.result) {
    await analysisStore.loadInitial()
  }
})

// ---------- 小工具 ----------
/** 把 '0800-0900' 这类时段串格式化成 '08:00 ~ 09:00' */
function formatPeakSlot(slot: string | undefined): string {
  if (!slot) return ''
  const parts = slot.split('-')
  const fmt = (t: string): string => (t.length === 4 ? `${t.slice(0, 2)}:${t.slice(2)}` : t)
  return parts.map(fmt).join(' ~ ')
}

// ---------- 1. KPI 数据映射（五卡） ----------
const kpiData = computed<KPIItem[]>(() => {
  const result = analysisStore.result
  if (!result) return []

  const items: KPIItem[] = []

  // 1) 污染异常：异常评分相对交通污染物信号基线的超出幅度
  if (result.air_quality?.result) {
    const air = result.air_quality.result
    const anomaly = air.anomaly_score
    if (anomaly !== undefined && anomaly !== null) {
      const baseline = air.traffic_pollutant_signal_score
      const exceed = baseline ? ((anomaly - baseline) / baseline) * 100 : null
      items.push({
        key: 'pollution_anomaly',
        label: '污染异常',
        tone: 'red',
        icon: Warning,
        badge: '高',
        badgeTone: 'red',
        value: anomaly.toFixed(2),
        sideNote: baseline ? `基线 ${baseline.toFixed(2)}` : undefined,
        footMain: exceed !== null ? `↑ 超标幅度 ${exceed.toFixed(1)}%` : undefined,
        footTone: 'red',
        footSide: '强度类别'
      })
    }
  }

  // 2) 气象风险：静稳 + 微风时给出橙色提示
  if (result.meteorology?.result) {
    const meteo = result.meteorology.result
    const risk = meteo.weather_risk
    if (risk !== undefined && risk !== null) {
      const wind = meteo.mean_wind_speed
      const stagnant = (meteo.static_wind_score ?? 0) >= 0.5 && wind !== null && wind !== undefined && wind < 2
      items.push({
        key: 'weather_risk',
        label: '气象风险',
        tone: 'orange',
        icon: MostlyCloudy,
        badge: '高',
        badgeTone: 'orange',
        value: risk.toFixed(4),
        sideNote: '风险预警',
        footMain: stagnant ? '微风不利扩散静稳' : `扩散条件${meteo.dispersion_condition ?? '一般'}`,
        footTone: stagnant ? 'orange' : 'grey',
        footSide: wind !== null && wind !== undefined ? `${wind.toFixed(1)}m/s` : undefined
      })
    }
  }

  // 3) 交通污染压力：突出早高峰时段
  if (result.traffic?.result) {
    const traffic = result.traffic.result
    const score = traffic.traffic_pressure_score
    if (score !== undefined && score !== null) {
      const peak = analysisStore.result?.traffic?.meta?.peak_time_slot
      items.push({
        key: 'traffic_pressure',
        label: '交通污染压力',
        tone: 'rose',
        icon: Van,
        badge: '高',
        badgeTone: 'red',
        value: score.toFixed(4),
        sideNote: '高峰叠加',
        footMain: '早高峰密集度居高',
        footTone: 'grey',
        footSide: peak ? `峰值 ${formatPeakSlot(peak)}` : undefined
      })
    }
  }

  // 4) 主要来源：主判断 + 首要因子评分
  if (result.source?.result) {
    const source = result.source.result
    const mainJudgement = source.main_judgement || ''
    let sourceLabel = '待研判'
    if (mainJudgement.includes('交通源') || mainJudgement.toLowerCase().includes('traffic')) {
      sourceLabel = '交通源'
    } else if (mainJudgement.includes('局地') || mainJudgement.toLowerCase().includes('local')) {
      sourceLabel = '局地积聚'
    } else if (mainJudgement.includes('区域') || mainJudgement.toLowerCase().includes('regional')) {
      sourceLabel = '区域传输'
    }

    const topScore = source.source_attribution?.traffic_source
    items.push({
      key: 'main_source',
      label: '主要来源',
      tone: 'blue',
      icon: DataAnalysis,
      badge: '首要因子',
      badgeTone: 'blue',
      value: sourceLabel,
      isText: true,
      sideNote: topScore !== undefined ? `${(topScore * 100).toFixed(1)}分` : undefined,
      footMain: mainJudgement.toLowerCase().includes('local') ? '伴随局地积聚效应' : getSourceJudgementLabel(mainJudgement),
      footTone: 'grey',
      footSide: '源解析指纹比对'
    })
  }

  // 5) 空间影响：分析半径 + 敏感点（突出学校）
  if (result.spatial?.result) {
    const spatial = result.spatial.result
    const radius = spatial.analysis_radius_km ?? 3
    const poi = spatial.sensitive_poi_by_type || {}
    items.push({
      key: 'spatial_impact',
      label: '空间影响',
      tone: 'violet',
      icon: Aim,
      badge: '扩散半径',
      badgeTone: 'grey',
      value: Number.isInteger(radius) ? `${radius}` : radius.toFixed(1),
      unit: 'km',
      footMain: `覆盖 ${spatial.sensitive_poi_count ?? 0} 个敏感点`,
      footTone: 'grey',
      footSide: poi.school !== undefined ? `学校 ${poi.school} 处` : undefined
    })
  }

  return items
})

// ---------- 2. 研判卡：ID / 结论 / 等级 / 建议 ----------
/** 用任务起始时间生成研判单号，如 DEC-20230815-08 */
const decisionId = computed<string>(() => {
  const start = analysisStore.currentTask?.start_time || ''
  const matched = start.match(/(\d{4})-(\d{2})-(\d{2})[ T](\d{2})/)
  if (!matched) return 'DEC-———'
  return `DEC-${matched[1]}${matched[2]}${matched[3]}-${matched[4]}`
})

const mainJudgementText = computed<string>(() => {
  const judgement = analysisStore.result?.source?.result?.main_judgement
  if (!judgement) return '暂无来源研判结果'
  return getSourceJudgementLabel(judgement)
})

const decisionLevel = computed<string>(() => {
  return analysisStore.result?.decision?.result?.decision_level || 'low'
})

const decisionLevelText = computed<string>(() => {
  const levelMap: Record<string, string> = {
    low: '低等级提示',
    medium: '中等级预警',
    high: '高等级预警'
  }
  return levelMap[decisionLevel.value] || decisionLevel.value
})

const topActions = computed<string[]>(() => {
  const actions = analysisStore.result?.decision?.result?.actions || []
  return actions.slice(0, 3) // 只显示前3条
})

const totalActionsCount = computed<number>(() => {
  return analysisStore.result?.decision?.result?.actions?.length || 0
})

const hasMoreActions = computed<boolean>(() => {
  return totalActionsCount.value > 3
})

// ---------- 3. 车型结构数据（使用中文映射） ----------
const vehicleStructureData = computed<VehicleProportion[]>(() => {
  const proportions = analysisStore.result?.traffic?.result?.proportions_pct
  if (!proportions) return []

  const items = Object.entries(proportions).map(([vehicle_type, percent]) => ({
    vehicle_type: getVehicleLabel(vehicle_type), // 使用中文映射
    percent: percent as number
  }))

  return items.sort((a, b) => b.percent - a.percent).slice(0, 5)
})

// ---------- 4. 来源归因数据 ----------
const sourceAttributionData = computed<SourceAttribution>(() => {
  const attribution = analysisStore.result?.source?.result?.source_attribution
  if (!attribution) {
    return {
      traffic_source: 0,
      local_accumulation: 0,
      regional_transport: 0,
      road_dust: 0,
      photochemical: 0
    }
  }

  // 字段名映射（组件期望 road_dust 和 photochemical，而数据是 dust_source 和 photochemical_source）
  return {
    traffic_source: attribution.traffic_source,
    local_accumulation: attribution.local_accumulation,
    regional_transport: attribution.regional_transport,
    road_dust: attribution.dust_source,
    photochemical: attribution.photochemical_source
  }
})

// ---------- 5. 交通压力（并入车型卡内嵌框） ----------
const trafficPressureScore = computed<number | null>(() => {
  return analysisStore.result?.traffic?.result?.traffic_pressure_score ?? null
})

const trafficPressureScoreDisplay = computed<string>(() => {
  const score = trafficPressureScore.value
  return score === null ? '—' : `${Math.round(score * 100)}`
})

const trafficPressureLevel = computed<string>(() => {
  return analysisStore.result?.traffic?.result?.traffic_pressure_level || 'low'
})

const trafficPeakTimeSlotDisplay = computed<string>(() => {
  const slot = analysisStore.result?.traffic?.meta?.peak_time_slot
  return slot ? formatPeakSlot(slot) : ''
})

const trafficYearNote = computed<string>(() => {
  const year = analysisStore.result?.traffic?.meta?.year_used
  return year ? `${year} 年 Mon–Fri 口径` : 'ATC 车型结构口径'
})

// ---------- 6. Spatial 地图数据 ----------
const spatialData = computed(() => {
  return analysisStore.result?.spatial?.result ?? null
})

// ---------- 7. Meteorology 数据（P3.4 传播风险表达）----------
const meteorologyData = computed(() => {
  return analysisStore.result?.meteorology?.result ?? null
})
</script>

<style scoped>
.overview-container {
  min-height: calc(100vh - 60px - 66px); /* 减去 header 和 TaskQueryBar */
  background: var(--bg-page);
}

/* ===== 状态展示 ===== */
.error-state,
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
  gap: 16px;
}

/* ===== 内容区域 ===== */
.overview-content {
  padding: 18px 24px 24px;
  max-width: 1920px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ===== 核心区域：地图 + 助手 ===== */
.core-section {
  display: grid;
  /* 地图列占约三分之二，右侧研判助手占三分之一 */
  grid-template-columns: minmax(0, 2.05fr) minmax(0, 1fr);
  gap: 16px;
  align-items: stretch;
}

/* 地图容器 */
.map-container-wrapper {
  background: var(--panel-bg);
  border-radius: 12px;
  padding: 16px 18px;
  box-shadow: var(--shadow-sm);
  border: 1px solid rgba(148, 163, 184, 0.14);
  min-height: 480px;
  display: flex;
  flex-direction: column;
}

/* 对话联动：地图区脉冲高亮 */
.map-container-wrapper.highlight-pulse {
  animation: section-pulse 1.6s ease-out;
}

@keyframes section-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(64, 158, 255, 0.55);
  }
  70% {
    box-shadow: 0 0 0 12px rgba(64, 158, 255, 0);
  }
  100% {
    box-shadow: var(--shadow-sm);
  }
}

.map-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-color);
  min-width: 0;
}

.map-header-icon {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  background: rgba(37, 99, 235, 0.1);
  color: #2563eb;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.map-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.map-subtitle {
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.map-container-inner {
  flex: 1;
  min-height: 400px;
  border-radius: 8px;
  overflow: hidden;
}

/* ===== 底部三卡 ===== */
.bottom-section {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  align-items: stretch;
}

/* --- 综合研判卡 --- */
.judgment-panel {
  background: var(--panel-bg);
  border-radius: 12px;
  padding: 16px 18px;
  box-shadow: var(--shadow-sm);
  border: 1px solid rgba(148, 163, 184, 0.14);
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.panel-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 14px;
}

.panel-header-icon {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  background: rgba(37, 99, 235, 0.1);
  color: #2563eb;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.panel-id {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-tertiary);
  font-variant-numeric: tabular-nums;
}

/* 结论高亮框 */
.conclusion-box {
  background: #eef4fe;
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 14px;
}

.conclusion-label {
  font-size: 12px;
  color: #2563eb;
  font-weight: 500;
  margin-bottom: 4px;
}

.conclusion-value {
  font-size: 16px;
  font-weight: 700;
  color: #0f172a;
  line-height: 1.4;
}

/* 决策依据引导行 */
.decision-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}

.decision-label {
  font-size: 12px;
  color: var(--text-tertiary);
}

.decision-tag {
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  padding: 4px 9px;
  border-radius: 4px;
}

.level-high   { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.level-medium { color: #b45309; background: rgba(245, 158, 11, 0.15); }
.level-low    { color: #15803d; background: rgba(34, 197, 94, 0.12); }

/* 治理建议 */
.advice-section {
  flex: 1;
}

.advice-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 10px;
}

.advice-list {
  margin: 0;
  padding: 0;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.advice-list li {
  position: relative;
  padding-left: 16px;
  font-size: 13px;
  line-height: 1.55;
  color: var(--text-secondary);
}

.advice-list li::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 7px;
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.advice-list li.dot-blue::before { background: #3b82f6; }
.advice-list li.dot-red::before  { background: #ef4444; }

.more-hint {
  margin: 8px 0 0 0;
  font-size: 12px;
  color: var(--text-tertiary);
  font-style: italic;
}

/* 卡片页脚（研判卡） */
.panel-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 12px;
  margin-top: 12px;
  border-top: 1px solid var(--border-color);
  font-size: 11.5px;
  color: var(--text-tertiary);
}

/* --- 图表卡通用 --- */
.chart-card {
  border-radius: 12px;
  border: 1px solid rgba(148, 163, 184, 0.14);
  box-shadow: var(--shadow-sm);
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.chart-card :deep(.el-card__header) {
  padding: 14px 18px 10px;
  border-bottom: none;
}

.chart-card :deep(.el-card__body) {
  padding: 4px 18px 14px;
  flex: 1;
  display: flex;
  flex-direction: column;
}

.card-header-content {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.card-header-icon {
  width: 26px;
  height: 26px;
  border-radius: 8px;
  background: rgba(37, 99, 235, 0.1);
  color: #2563eb;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.card-header-icon.icon-cyan {
  background: rgba(6, 182, 212, 0.1);
  color: #0891b2;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.card-subtitle {
  margin-left: auto;
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chart-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

/* 卡片页脚（图表卡） */
.card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 10px;
  margin-top: 12px;
  border-top: 1px solid var(--border-color);
  font-size: 11.5px;
  color: var(--text-tertiary);
}

/* --- 车型卡：交通压力内嵌框 --- */
.pressure-box {
  margin-top: 14px;
  background: #fff7ed;
  border-radius: 10px;
  padding: 12px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.pressure-left,
.pressure-right {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.pressure-right {
  align-items: flex-end;
  flex-shrink: 0;
}

.pressure-label {
  font-size: 11.5px;
  color: var(--text-tertiary);
}

.pressure-value {
  display: flex;
  align-items: baseline;
  gap: 3px;
}

.pressure-value b {
  font-size: 24px;
  font-weight: 700;
  color: #0f172a;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.pressure-value i {
  font-style: normal;
  font-size: 11px;
  color: var(--text-tertiary);
}

.pressure-pill {
  margin-left: 4px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  padding: 3px 7px;
  border-radius: 4px;
  color: #dc2626;
  background: rgba(239, 68, 68, 0.12);
}

.peak-chip {
  font-size: 12px;
  font-weight: 600;
  color: #0f172a;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 6px;
  padding: 4px 9px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* ===== 响应式 ===== */
/* 地图 + 助手并排一直保持到 1100px：1440/1536 这类标准宽度不允许上下堆叠 */
@media (max-width: 1100px) {
  .core-section {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 1100px) {
  .bottom-section {
    grid-template-columns: 1fr;
  }
}
</style>
