<template>
  <div class="ai-visualization-renderer">
    <div v-if="option" class="chart-heading">{{ title }}</div>
    <div ref="chartElement" class="ai-chart" :aria-label="title" />
    <el-empty v-if="!option" description="当前响应没有可绘制的真实数据" :image-size="80" />
    <div v-if="displayWarnings.length" class="viz-warnings">
      <el-tag v-for="warning in displayWarnings" :key="warning" type="warning" size="small">
        {{ warning }}
      </el-tag>
    </div>
    <div v-if="visualization?.data" class="provenance">
      数据来源：{{ providerLabel }} · 香港时间 · 图表版本 {{ visualization.revision }}
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import type { ECharts, EChartsOption } from 'echarts'
import type { ActiveVisualization } from '@/types/aiChat'
import { buildAIVisualizationOption, visualizationDisplayTitle } from '@/utils/aiVisualization'

const props = defineProps<{ visualization: ActiveVisualization | null }>()
const chartElement = ref<HTMLElement | null>(null)
let chart: ECharts | null = null
let observer: ResizeObserver | null = null

const option = computed<EChartsOption | null>(() => buildAIVisualizationOption(props.visualization))
const title = computed(() => visualizationDisplayTitle(props.visualization))
const displayWarnings = computed(() => (props.visualization?.warnings || []).filter(
  warning => !/不同物理量已通过.*显式分轴/.test(warning)))
const providerLabel = computed(() => (
  props.visualization?.data?.provider === 'canonical_agent_results'
    ? '可信业务分析结果'
    : props.visualization?.data?.provider ?? '未知'
))

function disposeChart(): void {
  if (chart) {
    chart.dispose()
    chart = null
  }
}

async function render(): Promise<void> {
  await nextTick()
  if (!chartElement.value || !option.value) {
    disposeChart()
    return
  }
  if (!chart) chart = echarts.init(chartElement.value)
  chart.setOption({ ...option.value, title: { show: false } }, { notMerge: true, lazyUpdate: false })
}

function resize(): void {
  chart?.resize()
}

defineExpose({ resize })
watch(option, () => { void render() }, { deep: true })
onMounted(() => {
  observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(resize) : null
  if (observer && chartElement.value) observer.observe(chartElement.value)
  void render()
})
onBeforeUnmount(() => {
  observer?.disconnect()
  observer = null
  disposeChart()
})
</script>

<style scoped>
.ai-visualization-renderer { position: relative; min-height: 290px; height: 100%; display: flex; flex-direction: column; }
.chart-heading { font-size: 16px; font-weight: 600; line-height: 1.5; padding: 4px 0 10px; overflow-wrap: anywhere; }
.ai-chart { width: 100%; height: 300px; min-height: 280px; }
.viz-warnings { display: flex; flex-wrap: wrap; gap: 6px; padding: 4px 0; }
.provenance { color: var(--text-tertiary); font-size: 11px; padding-top: 6px; }
</style>
