<template>
  <MainLayout>
    <!-- 任务查询栏 -->
    <TaskQueryBar />

    <div class="overview-container">
      <!-- 加载状态 --><AnalysisLoading
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
        <el-alert
          type="warning"
          title="分析范围已变化，自动重新分析未完成"
          description="切换后已尝试自动重跑，若因网络或后端异常中断，可点击「开始分析」重试。"
          :closable="false"
          show-icon
        />
      </div>

      <!-- 无数据状态 -->
      <div v-if="!analysisStore.loading && !analysisStore.error && !analysisStore.result" class="empty-state">
        <el-empty description="暂无分析数据" />
      </div>

      <!-- 正常数据展示 -->
      <div v-if="analysisStore.result && !analysisStore.analysisScopeDirty" class="overview-content">
        <!-- KPI 概览（五卡） -->
        <section class="kpi-section">
          <KPIOverview :data="kpiData" />
        </section>

        <!-- 核心区域：地图（左宽）+ 智能研判助手（右窄） -->
        <section class="core-section" :class="{ 'with-ai-chat': aiVizEnabled }">
          <div class="map-container-wrapper" :class="{ 'highlight-pulse': mapHighlight }">
            <div class="map-header">
              <span class="map-header-icon">
                <el-icon :size="16"><MapLocation /></el-icon>
              </span>
              <span class="map-title">空间传播与区域影响</span>
              <span class="map-subtitle">高精度风羽矢量模型 &amp; 敏感目标辐射场模拟</span>
            </div>
            <div class="map-container-inner">
              <div class="main-map-view">
                <SpatialMap :spatial="spatialData" :meteorology="meteorologyData" />
              </div>
            </div>
          </div>

          <!-- 对话问答模块：识别气象文本并实时联动视图 -->
          <AIChatPanel v-if="aiVizEnabled" @focus-section="onChatFocus" />
          <ChatQAPanel v-else @focus-section="onChatFocus" />
        </section>
      </div>
    </div>
  </MainLayout>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import {
  MapLocation,
  TrendCharts,
  MostlyCloudy,
  Van,
  Collection,
  Aim
} from '@element-plus/icons-vue'
import MainLayout from '@/layouts/MainLayout.vue'
import TaskQueryBar from '@/components/global/TaskQueryBar.vue'
import AnalysisLoading from '@/components/global/AnalysisLoading.vue'
import KPIOverview from '@/components/overview/KPIOverview.vue'
import ChatQAPanel from '@/components/overview/ChatQAPanel.vue'
import AIChatPanel from '@/components/overview/AIChatPanel.vue'
import SpatialMap from '@/components/spatial/SpatialMap.vue'
import { useAnalysisStore } from '@/stores/analysis'
import { getSourceOverview } from '@/utils/sourceMapper'
import type { KPIItem } from '@/components/overview/KPIOverview.vue'

const analysisStore = useAnalysisStore()
const aiVizEnabled = import.meta.env.VITE_AI_VIZ_ENABLED === 'true'

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
        icon: TrendCharts,
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
    const summary = getSourceOverview(source.main_judgement || '', source.source_attribution)
    items.push({
      key: 'main_source',
      label: '主要来源',
      tone: 'blue',
      icon: Collection,
      badge: summary.determined ? '首要因子' : '研判结果',
      badgeTone: 'blue',
      value: summary.label,
      isText: true,
      sideNote: summary.score !== undefined ? `${(summary.score * 100).toFixed(1)}分` : undefined,
      footMain: summary.description,
      footTone: 'grey',
      footSide: '来源可能性研判'
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

// ---------- 2. Spatial 地图数据 ----------
const spatialData = computed(() => {
  return analysisStore.result?.spatial?.result ?? null
})

// ---------- 3. Meteorology 数据（P3.4 传播风险表达）----------
const meteorologyData = computed(() => {
  return analysisStore.result?.meteorology?.result ?? null
})
</script>

<style scoped>
.overview-container {
  min-height: calc(100vh - 60px - 66px); /* 减去 header 和 TaskQueryBar */
  background: var(--bg-page);
}

.scope-stale-state { padding: 18px 24px 0; }

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
  --overview-main-card-height: 640px;
  display: grid;
  /* 地图列约占 5/8，右侧研判助手加宽到约 3/8 */
  grid-template-columns: minmax(0, 1.7fr) minmax(0, 1fr);
  gap: 16px;
  align-items: stretch;
}

.core-section.with-ai-chat {
  /* Charts need a readable canvas at laptop widths; the map remains usable. */
  grid-template-columns: minmax(0, 0.9fr) minmax(580px, 1.1fr);
}

/* 地图容器 */
.map-container-wrapper {
  background: var(--panel-bg);
  border-radius: 12px;
  padding: 16px 18px;
  box-shadow: var(--shadow-sm);
  border: 1px solid rgba(148, 163, 184, 0.14);
  box-sizing: border-box;
  height: var(--overview-main-card-height);
  min-height: 0;
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

.main-map-view {
  width: 100%;
  height: 100%;
  min-height: 400px;
}

/* ===== 响应式 ===== */
/* 地图 + 助手并排一直保持到 1100px：1440/1536 这类标准宽度不允许上下堆叠 */
@media (max-width: 1100px) {
  .core-section,
  .core-section.with-ai-chat {
    grid-template-columns: 1fr;
  }
}
</style>
