<template>
  <div class="vehicle-structure-chart">
    <!-- 空状态 -->
    <div v-if="isEmpty" class="empty-state">
      <el-empty description="暂无车型数据" :image-size="80" />
    </div>

    <!-- 占比条列表 -->
    <div v-else class="vehicle-list">
      <div
        v-for="(item, index) in chartData"
        :key="item.vehicle_type"
        class="vehicle-row"
      >
        <span class="vehicle-chip" :style="{ background: colorOf(index) }"></span>
        <span class="vehicle-label">{{ item.label }}</span>
        <div class="vehicle-bar-track">
          <div
            class="vehicle-bar-fill"
            :style="{ width: item.percent + '%', background: colorOf(index) }"
          ></div>
        </div>
        <span class="vehicle-percent" :style="{ color: colorOf(index) }">{{ item.percent.toFixed(1) }}%</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

defineOptions({ name: 'VehicleStructureChart' })

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

// ---------- 4. 配色（截图同款：蓝/绿/橙/红/灰…循环） ----------
const colorPalette = ['#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#94a3b8', '#06b6d4', '#8b5cf6', '#f97316']

const colorOf = (index: number): string => {
  return colorPalette[index % colorPalette.length]
}
</script>

<style scoped>
.vehicle-structure-chart {
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

/* ===== 占比条列表 ===== */
.vehicle-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 2px 0;
}

.vehicle-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.vehicle-chip {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  flex-shrink: 0;
}

.vehicle-label {
  width: 84px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.vehicle-bar-track {
  flex: 1;
  height: 10px;
  border-radius: 5px;
  background: var(--bg-page);
  overflow: hidden;
  min-width: 0;
}

.vehicle-bar-fill {
  height: 100%;
  border-radius: 5px;
  transition: width 0.6s ease;
}

.vehicle-percent {
  width: 48px;
  flex-shrink: 0;
  text-align: right;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}
</style>
