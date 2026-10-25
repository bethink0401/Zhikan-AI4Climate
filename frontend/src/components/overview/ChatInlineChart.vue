<template>
  <div class="chat-inline-chart">
    <div v-if="spec.title" class="chart-title">{{ spec.title }}</div>
    <div ref="chartRef" class="chart-canvas"></div>
    <div v-if="spec.note" class="chart-note">{{ spec.note }}</div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import type { ECharts } from 'echarts'

defineOptions({ name: 'ChatInlineChart' })

/** 单条数据序列 */
export interface ChatChartSeries {
  name: string
  data: (number | null)[]
  color?: string
}

/**
 * 对话气泡内嵌图表规格。
 * type=line/bar 用 categories + series；type=radar 用 indicators + radarValues。
 */
export interface ChatChartSpec {
  type: 'line' | 'bar' | 'radar'
  title?: string
  unit?: string
  categories?: string[]
  series?: ChatChartSeries[]
  indicators?: { name: string; max: number }[]
  radarValues?: number[]
  note?: string
}

const props = defineProps<{ spec: ChatChartSpec }>()

// 画布由 echarts 绘制，CSS 变量在这里不生效，统一用与浅色主题一致的固定色值
const PALETTE = ['#409eff', '#e6a23c', '#67c23a', '#f56c6c', '#909399']
const TEXT_COLOR = '#606266'
const AXIS_COLOR = '#c0c4cc'
const SPLIT_COLOR = '#eef0f4'

const chartRef = ref<HTMLDivElement | null>(null)
let chart: ECharts | null = null
let observer: ResizeObserver | null = null

function buildOption(spec: ChatChartSpec): echarts.EChartsOption {
  if (spec.type === 'radar') {
    return {
      tooltip: { trigger: 'item' },
      radar: {
        indicator: spec.indicators || [],
        radius: '62%',
        center: ['50%', '52%'],
        axisName: { color: TEXT_COLOR, fontSize: 10 },
        splitLine: { lineStyle: { color: SPLIT_COLOR } },
        splitArea: { areaStyle: { color: ['#ffffff', '#f7f9fc'] } },
        axisLine: { lineStyle: { color: SPLIT_COLOR } }
      },
      series: [
        {
          type: 'radar',
          symbolSize: 4,
          data: [
            {
              value: spec.radarValues || [],
              name: spec.title || '评分',
              areaStyle: { color: 'rgba(64, 158, 255, 0.25)' },
              lineStyle: { color: PALETTE[0], width: 2 },
              itemStyle: { color: PALETTE[0] }
            }
          ]
        }
      ]
    }
  }

  const categories = spec.categories || []
  const series = spec.series || []
  const isBar = spec.type === 'bar'
  const showLegend = series.length > 1

  return {
    tooltip: {
      trigger: 'axis',
      valueFormatter: (value: unknown) =>
        value === null || value === undefined ? '—' : `${value}${spec.unit || ''}`
    },
    legend: showLegend
      ? { bottom: 0, itemWidth: 12, itemHeight: 8, textStyle: { color: TEXT_COLOR, fontSize: 10 } }
      : undefined,
    grid: {
      left: 6,
      right: 14,
      top: 16,
      bottom: showLegend ? 26 : 20,
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: categories,
      boundaryGap: isBar,
      axisLine: { lineStyle: { color: AXIS_COLOR } },
      axisTick: { show: false },
      axisLabel: { color: TEXT_COLOR, fontSize: 10, hideOverlap: true }
    },
    yAxis: {
      type: 'value',
      name: spec.unit || '',
      nameTextStyle: { color: TEXT_COLOR, fontSize: 10 },
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: TEXT_COLOR, fontSize: 10 },
      splitLine: { lineStyle: { color: SPLIT_COLOR, type: 'dashed' } }
    },
    series: series.map((item, index) => {
      const color = item.color || PALETTE[index % PALETTE.length]
      return {
        name: item.name,
        type: isBar ? 'bar' : 'line',
        smooth: !isBar,
        symbol: isBar ? 'none' : 'circle',
        symbolSize: 5,
        data: item.data,
        itemStyle: { color },
        lineStyle: { width: 2, color },
        barMaxWidth: 22,
        connectNulls: true
      }
    })
  }
}

function render(): void {
  if (!chartRef.value) return
  if (!chart) {
    chart = echarts.init(chartRef.value)
  }
  chart.setOption(buildOption(props.spec), true)
}

onMounted(() => {
  render()
  if (chartRef.value && typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => chart?.resize())
    observer.observe(chartRef.value)
  }
})

watch(
  () => props.spec,
  () =>
    nextTick(() => {
      render()
      chart?.resize()
    }),
  { deep: true }
)

onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.chat-inline-chart {
  /*
   * 宽度：48cqw = 对话区宽度的 48%（容器查询单位，见 ChatQAPanel 的 .chat-body）。
   * 第一行 width:100% 是降级 —— 不支持容器查询的浏览器会丢弃第二行，回落到满宽。
   * 左对齐、不拉满：气泡随内容收缩时，百分比宽度解析不出确定值，echarts 会画出 0 宽画布。
   */
  width: 100%;
  width: 48cqw;
  max-width: 100%;
  margin: 8px 0 0;
  background: #ffffff;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  padding: 8px 8px 4px;
}

.chart-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 2px;
}

.chart-canvas {
  width: 100%;
  height: 156px;
}

.chart-note {
  font-size: 11px;
  color: var(--text-tertiary);
  padding: 2px 2px 4px;
  line-height: 1.4;
}
</style>
