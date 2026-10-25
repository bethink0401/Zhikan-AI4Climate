<template>
  <div class="source-probability-bar">
    <!-- 空状态 -->
    <div v-if="isEmpty" class="empty-state">
      <el-empty description="暂无来源数据" :image-size="80" />
    </div>

    <!-- 评分条列表 -->
    <div v-else class="score-list">
      <div
        v-for="(item, index) in chartData"
        :key="item.key"
        class="score-row"
      >
        <div class="score-row-top">
          <span class="score-name">{{ item.name }}</span>
          <span class="score-value">
            <b :style="{ color: rankColor(index) }">{{ item.value.toFixed(1) }}</b>
            <i>/ 100</i>
          </span>
        </div>
        <div class="score-bar-track">
          <div
            class="score-bar-fill"
            :style="{ width: item.value + '%', background: rankColor(index) }"
          ></div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

defineOptions({ name: 'SourceProbabilityBar' })

// ---------- 1. Props 类型定义 ----------
export interface SourceAttribution {
  traffic_source: number        // 交通源
  local_accumulation: number    // 局地积聚
  regional_transport: number    // 区域传输
  road_dust: number            // 道路扬尘
  photochemical: number        // 光化学
}

const props = defineProps<{
  data: SourceAttribution
}>()

// ---------- 2. 展示配置（名称按截图口径） ----------
const META: Record<keyof SourceAttribution, { name: string }> = {
  road_dust:          { name: '道路扬尘' },
  traffic_source:     { name: '交通源' },
  local_accumulation: { name: '局地积聚' },
  photochemical:      { name: '光化学' },
  regional_transport: { name: '区域传输' }
}

// ---------- 3. 判断数据是否为空 ----------
const isEmpty = computed(() => {
  if (!props.data) return true
  const values = Object.values(props.data)
  return values.every(v => v === undefined || v === null || v === 0)
})

// ---------- 4. 数据转换：[0,1] 评分转 [0,100] + 按评分排序 ----------
const chartData = computed(() => {
  if (isEmpty.value) return []

  const items = (Object.keys(META) as (keyof SourceAttribution)[])
    .map(key => ({
      key,
      name: META[key].name,
      value: (props.data[key] ?? 0) * 100
    }))
    .filter(item => item.value > 0)

  // 按评分从高到低排序
  items.sort((a, b) => b.value - a.value)
  return items
})

// ---------- 5. 排名配色（截图同款：深蓝→蓝→亮蓝→青→灰） ----------
const RANK_COLORS = ['#1e3a8a', '#2563eb', '#3b82f6', '#22b8cf', '#94a3b8']

const rankColor = (index: number): string => {
  return RANK_COLORS[index % RANK_COLORS.length]
}
</script>

<style scoped>
.source-probability-bar {
  width: 100%;
  position: relative;
  min-height: 200px;
}

.empty-state {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* ===== 评分条列表 ===== */
.score-list {
  display: flex;
  flex-direction: column;
  gap: 15px;
  padding: 4px 0;
}

.score-row-top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 7px;
  min-width: 0;
}

.score-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.score-value {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-tertiary);
  display: flex;
  align-items: baseline;
  gap: 3px;
}

.score-value b {
  font-size: 14px;
  font-weight: 700;
  font-style: normal;
  font-variant-numeric: tabular-nums;
}

.score-value i {
  font-style: normal;
}

/* 进度条 */
.score-bar-track {
  height: 9px;
  border-radius: 5px;
  background: #eef2f7;
  overflow: hidden;
}

.score-bar-fill {
  height: 100%;
  border-radius: 5px;
  transition: width 0.6s ease;
}
</style>
