<template>
  <div class="traffic-pressure-card">
    <!-- 标题行：标题 + 等级标签 -->
    <div class="card-header">
      <div class="header-titles">
        <span class="card-title">交通污染负担与车型结构</span>
        <span class="card-subtitle">Traffic Pollution Burden & Vehicle Mix</span>
      </div>
      <span class="level-tag" :class="`tone-${pressureLevel}`">{{ levelLabel }}</span>
    </div>

    <div v-if="isEmpty" class="empty-state">
      <el-empty description="暂无交通压力数据" :image-size="80" />
    </div>

    <template v-else>
      <!-- 负担指数 + 执法时段 -->
      <div class="burden-row">
        <div class="burden-index">
          <span class="index-number">{{ formattedScore }}</span>
          <span class="index-unit">IRI 指数</span>
        </div>
        <div class="burden-meta">
          <div class="meta-item">
            <span class="meta-label">执法时段与窗口</span>
            <span class="meta-value peak">{{ peakTimeSlot || '—' }}</span>
          </div>
          <div class="meta-item">
            <span class="meta-label">口径说明</span>
            <span class="meta-value">基于 ATC 车型构成的压力合成</span>
          </div>
        </div>
      </div>

      <!-- 车型结构（外部插槽） -->
      <div class="chart-slot">
        <slot></slot>
      </div>

      <!-- 底部建议 -->
      <div v-if="suggestion" class="card-footer">
        <span class="footer-hint">建议优先重点关注:</span>
        <span class="footer-suggestion">{{ suggestion }}</span>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

defineOptions({ name: 'TrafficPressureCard' })

// ---------- 1. Props 类型定义 ----------
export type PressureLevel = 'low' | 'medium' | 'high' | 'very_high'

const props = defineProps<{
  /** 交通压力分数 (0-1) */
  score: number | null
  /** 压力等级 */
  level?: PressureLevel
  /** 峰值时段，如 "08:00-09:00" */
  peakTimeSlot?: string
  /** 底部建议文字（如"专营双层巴士与轻型货车"） */
  suggestion?: string
}>()

// ---------- 2. 判断数据是否为空 ----------
const isEmpty = computed(() => {
  return props.score === null || props.score === undefined
})

// ---------- 3. 格式化分数 ----------
const formattedScore = computed<string | number>(() => {
  if (isEmpty.value || props.score === null) return '—'
  return Math.round(props.score * 100)
})

// ---------- 4. 等级文字 ----------
const levelLabelMap: Record<PressureLevel, string> = {
  low: '低压力',
  medium: '中压力',
  high: '高压力',
  very_high: '极高压力'
}

const pressureLevel = computed(() => {
  return props.level || 'low'
})

const levelLabel = computed(() => {
  return levelLabelMap[pressureLevel.value] || '未知'
})
</script>

<style scoped>
.traffic-pressure-card {
  background: var(--panel-bg);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  transition: box-shadow 0.2s;
}

.traffic-pressure-card:hover {
  box-shadow: var(--shadow-sm);
}

/* ===== 标题行 ===== */
.card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.header-titles {
  min-width: 0;
}

.card-title {
  display: block;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.card-subtitle {
  display: block;
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
}

.level-tag {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  padding: 5px 9px;
  border-radius: 4px;
}

.tone-low       { color: #15803d; background: rgba(34, 197, 94, 0.12); }
.tone-medium    { color: #b45309; background: rgba(245, 158, 11, 0.14); }
.tone-high      { color: #c22f2f; background: rgba(239, 68, 68, 0.1); }
.tone-very_high { color: #ffffff; background: #c22f2f; }

/* ===== 负担指数行 ===== */
.burden-row {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}

.burden-index {
  display: flex;
  align-items: baseline;
  gap: 6px;
  flex-shrink: 0;
}

.index-number {
  font-size: 34px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1;
  font-variant-numeric: tabular-nums;
}

.index-unit {
  font-size: 12px;
  color: var(--text-tertiary);
}

.burden-meta {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.meta-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  font-size: 12px;
  min-width: 0;
}

.meta-label {
  color: var(--text-tertiary);
  white-space: nowrap;
}

.meta-label::before {
  content: '·';
  margin-right: 4px;
  color: var(--border-color);
}

.meta-value {
  color: var(--text-secondary);
  font-weight: 500;
}

.meta-value.peak {
  color: #c22f2f;
  font-weight: 600;
}

/* ===== 图表插槽 ===== */
.chart-slot {
  min-height: 0;
}

/* ===== 底部建议 ===== */
.card-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 6px;
  padding-top: 10px;
  border-top: 1px dashed var(--border-color);
  font-size: 12px;
}

.footer-hint {
  color: var(--text-tertiary);
}

.footer-suggestion {
  color: #b45309;
  font-weight: 600;
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100px;
}
</style>
