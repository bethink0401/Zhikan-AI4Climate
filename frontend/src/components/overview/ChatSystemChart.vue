<template>
  <div class="chat-system-chart" :style="{ width: meta.width }">
    <div class="sys-head">
      <el-icon :size="14"><TrendCharts /></el-icon>
      <span class="sys-title">{{ meta.title }}</span>
      <span class="sys-badge">系统图表</span>
    </div>

    <div class="sys-body" :style="{ height: meta.height }">
      <template v-if="hasData">
        <WeatherTrend
          v-if="kind === 'meteo-trend'"
          :data="hourlyTrendData"
        />
        <SourceProbabilityBar
          v-else-if="kind === 'source-probability'"
          :data="sourceAttribution"
        />
        <VehicleStructureChart
          v-else-if="kind === 'vehicle-structure'"
          :data="vehicleProportions"
        />
      </template>

      <div v-else class="sys-empty">
        <el-empty :description="meta.empty" :image-size="52" />
      </div>
    </div>

    <div class="sys-foot">
      <span class="sys-caption">{{ meta.caption }}</span>
      <span class="sys-source" :class="{ 'is-degraded': store.analysisSource !== 'api' }">
        数据来源：{{ sourceLabel }}
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 对话气泡内的「系统图表」宿主。
 *
 * 它不重画图表，而是**原样复用系统里已有的可视化组件**，
 * 把它们在各自页面上的取数与字段映射照搬过来，放进对话气泡里渲染：
 *
 *   meteo-trend        → WeatherTrend（气象传播页的逐小时四联图）
 *   source-probability → SourceProbabilityBar（溯源页/综合态势页的来源评分）
 *   vehicle-structure  → VehicleStructureChart（交通车型占比）
 *
 * 这样对话调出的图，和页面上看到的图是同一份实现、同一份数据，
 * 不会出现"对话里图对不上页面"的问题。
 */
import { computed } from 'vue'
import { TrendCharts } from '@element-plus/icons-vue'
import WeatherTrend from '@/components/meteorology/WeatherTrend.vue'
import SourceProbabilityBar from '@/components/source/SourceProbabilityBar.vue'
import VehicleStructureChart from '@/components/traffic/VehicleStructureChart.vue'
import { useAnalysisStore } from '@/stores/analysis'
import { getVehicleLabel } from '@/utils/vehicleMapper'
import type { HourlyWeatherData } from '@/components/meteorology/WeatherTrend.vue'
import type { SourceAttribution } from '@/components/source/SourceProbabilityBar.vue'
import type { VehicleProportion } from '@/components/traffic/VehicleStructureChart.vue'
import type { MeteorologyHourlyItem } from '@/types/meteorology'

defineOptions({ name: 'ChatSystemChart' })

/** 可被对话调用的系统图表种类 */
export type SystemChartKind = 'meteo-trend' | 'source-probability' | 'vehicle-structure'

const props = defineProps<{
  kind: SystemChartKind
  /** 整链路结果缺逐小时序列时的降级数据（由对话模块按需传入） */
  hourly?: MeteorologyHourlyItem[]
}>()

const store = useAnalysisStore()

// ── 各图表的展示元信息 ──────────────────────────────────────────────────
// 气泡空间有限，高度按"能在对话框里一眼看全"来定，比页面上的版本明显小一圈。
// width 统一取 100%（占满气泡）：两张横向条形图是"名称+进度条+百分比"的行式布局，
// 有最小可读宽度；对话栏收窄后 48cqw 只剩约 190px，会把标题、百分比和页脚挤到折行裁字，
// 因此不再缩半，全部跟随气泡宽度（气泡对系统图表已放开到整行宽）。
// 容器查询单位 cqw 已不再用于宽度，echarts 宽度由父级 flex 布局保证。
const META: Record<
  SystemChartKind,
  { title: string; caption: string; empty: string; height: string; width: string }
> = {
  'meteo-trend': {
    title: '逐小时气象趋势',
    caption: '风速 / 湿度 / 降雨 / 温度 四联图',
    empty: '暂无逐小时气象数据',
    height: '240px',
    width: '100%'
  },
  'source-probability': {
    title: '污染来源可能性评分',
    caption: '筛查评分，非贡献率',
    empty: '暂无来源归因数据',
    // 五行评分条（行高 34px + 行距 15px + 内边距）实际需要约 238px，
    // 之前给 190px 会被 .chat-system-chart 的 overflow:hidden 裁掉最后一行
    height: '246px',
    width: '100%'
  },
  'vehicle-structure': {
    title: '车型结构分析',
    caption: '按车型占比由高到低',
    empty: '暂无车型占比数据',
    height: '196px',
    width: '100%'
  }
}

const meta = computed(() => META[props.kind])

// ── ① 逐小时气象趋势 ────────────────────────────────────────────────────
const trendSeries = computed<MeteorologyHourlyItem[]>(() => {
  const fromStore = store.result?.meteorology?.result?.hourly_series ?? []
  if (fromStore.length) return fromStore
  return props.hourly ?? []
})

const hourlyTrendData = computed<HourlyWeatherData>(() => {
  const series = trendSeries.value
  return {
    time: series.map((item) => {
      // "2023-08-15 08:00:00" → "08:00"
      const clock = item.time.split(' ')[1] ?? item.time
      return clock.substring(0, 5)
    }),
    wind_speed: series.map((item) => item.wind_speed),
    humidity: series.map((item) => item.humidity),
    precipitation: series.map((item) => item.precipitation),
    temperature: series.map((item) => item.temperature)
  }
})

// ── ② 污染来源可能性评分（字段名与组件口径不同，需重映射）────────────────
const sourceAttribution = computed<SourceAttribution>(() => {
  const attr = store.result?.source?.result?.source_attribution
  return {
    traffic_source: attr?.traffic_source ?? 0,
    local_accumulation: attr?.local_accumulation ?? 0,
    regional_transport: attr?.regional_transport ?? 0,
    // 组件期望 road_dust / photochemical，数据里是 dust_source / photochemical_source
    road_dust: attr?.dust_source ?? 0,
    photochemical: attr?.photochemical_source ?? 0
  }
})

// ── ③ 车型结构 ──────────────────────────────────────────────────────────
const vehicleProportions = computed<VehicleProportion[]>(() => {
  const pct = store.result?.traffic?.result?.proportions_pct
  if (!pct) return []

  return Object.entries(pct)
    .filter(([, percent]) => (percent as number) > 0)
    .sort(([, a], [, b]) => (b as number) - (a as number))
    .slice(0, 6)
    .map(([vehicle_type, percent]) => ({
      vehicle_type: getVehicleLabel(vehicle_type),
      percent: percent as number
    }))
})

// ── 是否有数据可画 ──────────────────────────────────────────────────────
const hasData = computed(() => {
  if (props.kind === 'meteo-trend') return trendSeries.value.length > 0
  if (props.kind === 'source-probability') {
    return Object.values(sourceAttribution.value).some((value) => value !== 0)
  }
  return vehicleProportions.value.length > 0
})

// ── 数据来源标注（与页面口径一致）────────────────────────────────────────
const sourceLabel = computed(() => {
  if (store.analysisSource === 'api') return '实时链路数据'
  if (store.analysisSource === 'snapshot') return '离线快照数据'
  return '本地面板数据'
})
</script>

<style scoped>
.chat-system-chart {
  /*
   * width 由 META.width 注入（48cqw / 100%）。
   * 这里留一条 width:100% 作降级：旧浏览器不支持 cqw 时会丢弃内联声明，回落到满宽。
   * 左对齐（不用 auto 居中），与气泡里的文字同一起始边。
   */
  width: 100%;
  margin: 8px 0 0;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--panel-bg);
  overflow: hidden;
}

.sys-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  border-bottom: 1px solid var(--border-color);
  color: var(--text-primary);
}

.sys-title {
  font-size: 12px;
  font-weight: 600;
}

.sys-badge {
  margin-left: auto;
  font-size: 10px;
  line-height: 1;
  padding: 3px 6px;
  border-radius: 8px;
  color: var(--theme-color);
  background: var(--theme-color-lighter);
  border: 1px solid var(--theme-color);
  opacity: 0.85;
  flex-shrink: 0;
}

.sys-body {
  width: 100%;
  padding: 6px 8px 2px;
  box-sizing: border-box;
}

/*
 * 三个系统组件在各自页面里都带较大的 min-height（280 / 200 / 220px），
 * 气泡容器比它们小，不改的话会被子元素撑破、图表下半截被裁掉。
 * 这里把它们在气泡内的最小高度放开，让图表老老实实跟随容器高度。
 */
.sys-body :deep(.weather-trend),
.sys-body :deep(.source-probability-bar),
.sys-body :deep(.vehicle-structure-chart) {
  min-height: 0;
  height: 100%;
}

.sys-body :deep(.chart-container) {
  min-height: 0;
  height: 100%;
}

.sys-empty {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.sys-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 10px 7px;
  font-size: 10px;
  color: var(--text-tertiary);
  line-height: 1.4;
}

.sys-caption {
  flex: 1;
  min-width: 0;
}

.sys-source {
  flex-shrink: 0;
}

.sys-source.is-degraded {
  color: var(--risk-medium, #e6a23c);
}
</style>
