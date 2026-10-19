<template>
  <div class="weather-trend">
    <div ref="chartRef" class="chart-container"></div>
    <div v-if="isEmpty" class="empty-state">
      <el-empty description="暂无逐小时气象数据" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch, nextTick, computed } from 'vue'
import * as echarts from 'echarts'
import type { ECharts } from 'echarts'

defineOptions({ name: 'WeatherTrend' })

export interface HourlyWeatherData {
  time: string[]
  wind_speed: (number | null)[]
  humidity: (number | null)[]
  precipitation: (number | null)[]
  temperature: (number | null)[]
}

const props = defineProps<{
  data: HourlyWeatherData
}>()

const isEmpty = computed(() => {
  return !props.data || !props.data.time || props.data.time.length === 0
})

const chartRef = ref<HTMLDivElement | null>(null)
let chartInstance: ECharts | null = null

const colors = {
  humidity: '#14b8a6',
  temperature: '#f59e0b',
  precipitation: '#3d7eff'
}

// ---------- 核心：单图三系列（湿度 / 温度 / 降雨） ----------
const buildOption = () => {
  const { time, humidity, precipitation, temperature } = props.data

  const series = [
    {
      name: '湿度 (%)',
      type: 'line',
      yAxisIndex: 0,
      data: humidity,
      smooth: true,
      symbol: 'circle',
      symbolSize: 5,
      lineStyle: { color: colors.humidity, width: 2 },
      itemStyle: { color: colors.humidity }
    },
    {
      name: '温度 (°C)',
      type: 'line',
      yAxisIndex: 1,
      data: temperature,
      smooth: true,
      symbol: 'circle',
      symbolSize: 5,
      lineStyle: { color: colors.temperature, width: 2 },
      itemStyle: { color: colors.temperature }
    },
    {
      name: '降雨 (mm)',
      type: 'bar',
      yAxisIndex: 1,
      data: precipitation,
      barWidth: '35%',
      itemStyle: { color: '#a9c8f7', borderRadius: [2, 2, 0, 0] }
    }
  ]

  return {
    color: [colors.humidity, colors.temperature, '#a9c8f7'],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line' }
    },
    legend: {
      top: 0,
      right: 0,
      itemWidth: 14,
      itemHeight: 4,
      itemGap: 14,
      icon: 'roundRect',
      textStyle: { fontSize: 11, color: '#606266' }
    },
    grid: { left: 42, right: 46, top: 30, bottom: 26 },
    xAxis: {
      type: 'category',
      data: time,
      axisLine: { lineStyle: { color: '#dcdfe6' } },
      axisTick: { show: false },
      axisLabel: { fontSize: 10, color: '#909399' }
    },
    yAxis: [
      {
        type: 'value',
        name: '%',
        min: 0,
        max: 100,
        nameTextStyle: { fontSize: 10, color: '#909399' },
        axisLabel: { fontSize: 10, color: '#909399' },
        splitLine: { show: true, lineStyle: { color: '#f0f2f5' } }
      },
      {
        type: 'value',
        name: '°C',
        nameTextStyle: { fontSize: 10, color: '#909399' },
        axisLabel: { fontSize: 10, color: '#909399' },
        splitLine: { show: false }
      }
    ],
    series
  }
}

// ---------- 生命周期管理 ----------
const initChart = () => {
  if (!chartRef.value) return
  if (chartInstance) {
    chartInstance.dispose()
    chartInstance = null
  }
  chartInstance = echarts.init(chartRef.value)

  const option = buildOption()
  chartInstance.setOption(option)

  const resizeHandler = () => {
    chartInstance?.resize()
  }
  window.addEventListener('resize', resizeHandler)
  ;(chartInstance as any).__resizeHandler = resizeHandler
}

watch(
  () => props.data,
  () => {
    if (isEmpty.value) {
      if (chartInstance) {
        chartInstance.clear()
      }
      return
    }
    nextTick(() => {
      if (chartInstance) {
        const option = buildOption()
        chartInstance.setOption(option, true)
        chartInstance.resize()
      } else {
        initChart()
      }
    })
  },
  { deep: true }
)

onMounted(() => {
  if (!isEmpty.value) {
    initChart()
  }
})

onBeforeUnmount(() => {
  if (chartInstance) {
    const handler = (chartInstance as any).__resizeHandler
    if (handler) {
      window.removeEventListener('resize', handler)
    }
    chartInstance.dispose()
    chartInstance = null
  }
})

defineExpose({
  resize: () => {
    chartInstance?.resize()
  }
})
</script>

<style scoped>
.weather-trend {
  width: 100%;
  height: 100%;
  position: relative;
  min-height: 280px;
}

.chart-container {
  width: 100%;
  height: 100%;
  min-height: 280px;
}

.empty-state {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--panel-bg);
  border-radius: 4px;
}
</style>