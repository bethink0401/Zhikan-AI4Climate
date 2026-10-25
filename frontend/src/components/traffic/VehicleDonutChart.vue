<template>
  <div class="vehicle-donut-chart">
    <!-- 空状态 -->
    <div v-if="isEmpty" class="empty-state">
      <el-empty description="暂无车型数据" :image-size="80" />
    </div>

    <!-- 环形图 + 图例列表 -->
    <div v-else class="donut-layout">
      <div ref="chartEl" class="donut-canvas"></div>
      <div class="donut-legend">
        <div
          v-for="(item, index) in chartData"
          :key="item.vehicle_type"
          class="legend-row"
        >
          <span class="legend-chip" :style="{ background: colorOf(index) }"></span>
          <span class="legend-label">{{ item.label }}</span>
          <span class="legend-percent" :style="{ color: colorOf(index) }">{{ item.percent.toFixed(1) }}%</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

defineOptions({ name: 'VehicleDonutChart' })

// ---------- 1. Props 类型定义 ----------
export interface VehicleProportion {
  vehicle_type: string   // 车型英文代码
  percent: number        // 占比 (0-100)
}

const props = defineProps<{
  data: VehicleProportion[]
}>()

// ---------- 2. 判断数据是否为空 ----------
const isEmpty = computed(() => {
  return !props.data || props.data.length === 0
})

// ---------- 3. 车型中文映射 + 排序 ----------
// 车型中文映射（与项目 utils/vehicleMapper.ts 保持一致）
const vehicleNameMap: Record<string, string> = {
  'Franchised Bus DD': '专营双层巴士',
  'Franchised Bus SD': '专营单层巴士',
  'Private Car': '私家车',
  'Taxi': '出租车',
  'Goods Vehicle Light': '轻型货车',
  'Goods Vehicle M&H': '中重型货车',
  'Motor Cycle': '摩托车',
  'Non Franchised Bus': '非专营巴士',
  'Private LB': '私家小型巴士',
  'PLB': '公共小型巴士'
}

const chartData = computed(() => {
  if (isEmpty.value) return []
  return [...props.data]
    .sort((a, b) => b.percent - a.percent)
    .map(item => ({
      ...item,
      label: vehicleNameMap[item.vehicle_type] || item.vehicle_type
    }))
})

// ---------- 4. 配色（与条形图同款：蓝/绿/橙/红/灰…循环） ----------
const colorPalette = ['#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#94a3b8', '#06b6d4', '#8b5cf6', '#f97316']

const colorOf = (index: number): string => {
  return colorPalette[index % colorPalette.length]
}

// ---------- 5. ECharts 环形图 ----------
const chartEl = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null

function renderChart(): void {
  if (!chartEl.value || isEmpty.value) return
  if (!chart) chart = echarts.init(chartEl.value)

  const option: echarts.EChartsOption = {
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => `${params.name}：${params.value}%`
    },
    legend: { show: false },
    series: [
      {
        type: 'pie',
        radius: ['56%', '82%'],
        center: ['50%', '50%'],
        avoidLabelOverlap: true,
        padAngle: 1.5,
        itemStyle: {
          borderRadius: 4,
          borderColor: '#ffffff',
          borderWidth: 2
        },
        label: { show: false },
        emphasis: {
          label: { show: false },
          itemStyle: { shadowBlur: 8, shadowColor: 'rgba(0, 0, 0, 0.2)' }
        },
        data: chartData.value.map((item, index) => ({
          name: item.label,
          value: Number(item.percent.toFixed(1)),
          itemStyle: { color: colorOf(index) }
        }))
      }
    ]
  }

  chart.setOption(option, true)
}

// 数据变化后重绘
watch(() => props.data, renderChart, { deep: true })

// 窗口变化时自适应
function handleResize(): void {
  chart?.resize()
}

onMounted(() => {
  renderChart()
  window.addEventListener('resize', handleResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', handleResize)
  chart?.dispose()
  chart = null
})
</script>

<style scoped>
.vehicle-donut-chart {
  width: 100%;
  position: relative;
  min-height: 160px;
}

.empty-state {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ===== 环形图 + 图例 ===== */
.donut-layout {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 2px 0;
}

.donut-canvas {
  width: 180px;
  height: 200px;
  flex-shrink: 0;
}

.donut-legend {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 200px;
  overflow-y: auto;
}

.legend-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.legend-chip {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.legend-label {
  flex: 1;
  min-width: 0;
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.legend-percent {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
</style>
