<template>
  <div class="dynamic-chart-card">
    <div class="chart-heading">{{ displayTitle }}</div>
    <div ref="chartEl" class="chart-canvas" :aria-label="displayTitle" role="img" />
    <div v-if="displayWarnings.length" class="chart-warnings">{{ displayWarnings.join('；') }}</div>
    <div class="chart-source">
      数据来源：{{ chart.provenance.metrics.map(item => sourceLabel(item.source_agent)).filter((item, index, all) => all.indexOf(item) === index).join('、') }}
      · {{ displayStation }}
      · 香港时间
      <span v-if="chart.transform.bucket_hours > 1"> · {{ chart.transform.bucket_hours }} 小时平均</span>
      <span v-if="chart.transform.start_hour > 0 || chart.transform.end_hour < 24">
        · {{ chart.transform.start_hour }}:00–{{ chart.transform.end_hour }}:00
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch, nextTick, computed } from 'vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { DynamicChart } from '@/types/aiChat'
import { chartCategoryLabel, chartDisplayTitle, chartSeriesLabel, metricDisplayName } from '@/utils/aiVisualization'
import { useAnalysisStore } from '@/stores/analysis'

const props = defineProps<{ chart: DynamicChart }>()
const analysisStore = useAnalysisStore()
const isPollutantProfile = computed(() => props.chart.metrics.includes('air_quality.pollutant_profile'))
const hasCo = computed(() => props.chart.categories.some(item => item.toLowerCase() === 'co' || item === '一氧化碳'))
const displayTitle = computed(() => isPollutantProfile.value
  ? '污染物时段平均浓度对比'
  : chartDisplayTitle(props.chart.title, props.chart.metrics, props.chart.type))
const visibleWarnings = computed(() => props.chart.warnings.filter(item =>
  !item.includes('按论文寿命公式计算') && !item.includes('系数为参考状态匹配')))
const displayWarnings = computed(() => isPollutantProfile.value && hasCo.value
  ? [...visibleWarnings.value.filter(item => !item.startsWith('CO 已从')),
    ...(!visibleWarnings.value.some(item => item.includes('左右刻度'))
      ? ['CO 使用右侧 mg/m³ 刻度，其他污染物使用左侧 μg/m³ 刻度；柱高不能跨刻度直接比较。'] : [])]
  : visibleWarnings.value)
const displayStation = computed(() => {
  const task = props.chart.provenance.task
  const name = task.station_name || task.station_id || ''
  const station = analysisStore.stations.find(item => item.station_id === task.station_id || item.station_name === name)
  return station?.station_name_zh || ({
    'Mong Kok Roadside': '旺角路边站',
    'Causeway Bay Roadside': '铜锣湾路边站',
    'Central Roadside': '中环路边站'
  } as Record<string, string>)[name] || name || '当前站点'
})
const chartEl = ref<HTMLDivElement | null>(null)
let instance: ECharts | null = null
let observer: ResizeObserver | null = null
const CHART_PALETTE = ['#21318c', '#30a5c2', '#9bd5b9', '#eef5b2', '#082059',
  '#2254a3', '#1e80b8', '#62bfbf', '#d5ebbd', '#fdfed7']

function sourceLabel(source: string): string {
  return ({ AirQualityAgent: '空气质量监测', MeteorologyAgent: '气象数据',
    TrafficAgent: '交通参考数据', SourceAgent: '来源筛查',
    TravelAgent: '出行规划', SpatialAgent: '空间传播分析',
    DecisionAgent: '治理决策' } as Record<string, string>)[source] ?? '业务分析数据'
}

function unitLabel(unit: string | null | undefined): string {
  if (unit === 'AQHI index') return '指数'
  if (unit === 'EU') return '吸入当量'
  if (unit === 'dimensionless independent screening score') return '独立筛查评分'
  if (unit === 'dimensionless screening score') return '筛查评分'
  if (unit?.startsWith('degree (meteorological')) return '°'
  return unit || ''
}

function buildOption(chart: DynamicChart): EChartsOption {
  const metricNames = chart.series.map((item, index) => chartSeriesLabel(item.name, chart.metrics[index] || chart.metrics[0] || ''))
  const categories = chart.categories.map(chartCategoryLabel)
  if (chart.metrics.includes('air_quality.pollutant_profile')
      && chart.categories.some(item => item.toLowerCase() === 'co' || item === '一氧化碳')) {
    const coIndex = chart.categories.findIndex(item => item.toLowerCase() === 'co' || item === '一氧化碳')
    const legacy = chart.series.length === 1
    const first = chart.series[0]?.data || []
    const micro = legacy ? first.map((value, index) => index === coIndex ? null : value) : first
    const co = legacy
      ? first.map((value, index) => index === coIndex && typeof value === 'number'
        ? value / (chart.series[0]?.unit === 'μg/m³' ? 1000 : 1) : null)
      : chart.series[1]?.data || []
    const kind = chart.type === 'bar' ? 'bar' as const : 'line' as const
    return {
      color: CHART_PALETTE,
      tooltip: { trigger: 'axis' },
      legend: { type: 'scroll', bottom: 0 },
      grid: { left: 58, right: 58, top: 45, bottom: 58, containLabel: true },
      xAxis: { type: 'category', data: categories, axisLabel: { hideOverlap: true } },
      yAxis: [
        { type: 'value', name: '其他污染物（μg/m³）', position: 'left', min: 0 },
        { type: 'value', name: 'CO（mg/m³）', position: 'right', min: 0 }
      ],
      series: [
        { name: '其他污染物（μg/m³）', type: kind, data: micro as Array<number | null>,
          yAxisIndex: 0, barMaxWidth: 48, areaStyle: chart.type === 'area' ? {} : undefined },
        { name: '一氧化碳 CO（mg/m³）', type: kind, data: co as Array<number | null>,
          yAxisIndex: 1, barMaxWidth: 48, areaStyle: chart.type === 'area' ? {} : undefined }
      ]
    }
  }
  if (chart.type === 'pie' || chart.type === 'donut') {
    return {
      color: CHART_PALETTE,
      tooltip: { trigger: 'item' },
      legend: { type: 'scroll', bottom: 0 },
      series: [{
        type: 'pie', radius: chart.type === 'donut' ? ['42%', '70%'] : '68%',
        data: categories.flatMap((name, index) => {
          const value = chart.series[0]?.data[index]
          return typeof value === 'number' && Number.isFinite(value) ? [{ name, value }] : []
        })
      }]
    }
  }
  if (chart.type === 'scatter') {
    const xUnit = unitLabel(chart.provenance.metrics[0]?.unit)
    const yUnit = unitLabel(chart.provenance.metrics[1]?.unit)
    return {
      color: CHART_PALETTE,
      tooltip: { trigger: 'item' },
      grid: { left: 42, right: 12, top: 38, bottom: 56, containLabel: false },
      xAxis: { type: 'value', name: `${metricDisplayName(chart.metrics[0] || '')}${xUnit ? ` (${xUnit})` : ''}`,
        nameLocation: 'middle', nameGap: 32 },
      yAxis: { type: 'value', name: `${metricDisplayName(chart.metrics[1] || '')}${yUnit ? ` (${yUnit})` : ''}` },
      series: [{ type: 'scatter', data: chart.series[0]?.data as number[][] || [], symbolSize: 9 }]
    }
  }
  const units = [...new Set(chart.series.map(item => item.unit || ''))]
  const leftAxes = Math.ceil(units.length / 2)
  const rightAxes = Math.floor(units.length / 2)
  const axisSpacing = 30
  const yAxis = units.map((unit, index) => ({
    type: 'value' as const, name: unitLabel(unit),
    position: index % 2 === 0 ? 'left' as const : 'right' as const,
    offset: Math.floor(index / 2) * axisSpacing,
    axisLabel: { margin: 6 },
    min: 0,
    max: unit === '%' ? 100 : undefined
  }))
  return {
    color: CHART_PALETTE,
    tooltip: { trigger: 'axis' }, legend: { type: 'scroll', bottom: 0 },
    grid: {
      left: 45 + Math.max(0, leftAxes - 1) * axisSpacing,
      right: (rightAxes ? 36 : 12) + Math.max(0, rightAxes - 1) * axisSpacing,
      top: 38, bottom: 58, containLabel: false
    },
    xAxis: {
      type: 'category', data: categories,
      axisLabel: {
        hideOverlap: true,
        formatter: (value: string) => chart.dimension === 'time'
          ? (value.match(/(?:\d{4}-\d{2}-\d{2}[ T])?(\d{2}:\d{2})/)?.[1] ?? value)
          : value
      }
    },
    yAxis,
    dataZoom: chart.categories.length > 24 ? [{ type: 'inside' }, { type: 'slider', height: 14, bottom: 24 }] : undefined,
    series: chart.series.map((item, index) => ({
      name: metricNames[index], type: chart.type === 'bar' ? 'bar' as const : 'line' as const,
      yAxisIndex: units.indexOf(item.unit || ''), data: item.data as Array<number | null>,
      areaStyle: chart.type === 'area' ? {} : undefined,
      smooth: false, connectNulls: false
    }))
  }
}

async function render(): Promise<void> {
  await nextTick()
  if (!chartEl.value) return
  if (!instance) instance = echarts.init(chartEl.value)
  instance.setOption(buildOption(props.chart), { notMerge: true })
  instance.resize()
}

onMounted(() => {
  void render()
  if (typeof ResizeObserver !== 'undefined' && chartEl.value) {
    observer = new ResizeObserver(() => instance?.resize())
    observer.observe(chartEl.value)
  }
})
watch(() => props.chart, () => { void render() }, { deep: true })
onBeforeUnmount(() => { observer?.disconnect(); instance?.dispose(); instance = null })
</script>

<style scoped>
.dynamic-chart-card { width: 100%; margin: 16px 0 0; padding: 12px; box-sizing: border-box; background: #fff; border: 1px solid var(--border-color); border-radius: 8px; }
.chart-heading { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.chart-canvas { width: 100%; height: 270px; min-height: 270px; }
.chart-warnings { color: #ad6800; font-size: 11px; margin-top: 6px; }
.chart-source { color: var(--text-tertiary); font-size: 11px; margin-top: 6px; line-height: 1.5; }
@media (max-width: 700px) { .chart-canvas { height: 240px; min-height: 240px; } }
</style>
