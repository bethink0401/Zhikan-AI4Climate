<template>
  <div class="kpi-strip">
    <div
      v-for="item in items"
      :key="item.key"
      class="kpi-card"
    >
      <!-- 标题行：标题 + 状态标签 -->
      <div class="kpi-head">
        <div class="kpi-titles">
          <div class="kpi-title">{{ item.title }}</div>
          <div class="kpi-subtitle">{{ item.subtitle }}</div>
        </div>
        <span class="kpi-tag" :class="`tone-${item.tone}`">{{ item.tag }}</span>
      </div>

      <!-- 主体数值 -->
      <div class="kpi-main">
        <span class="kpi-main-value">{{ item.main }}</span>
        <span v-if="item.mainUnit" class="kpi-main-unit">{{ item.mainUnit }}</span>
        <span v-if="item.mainSuffix" class="kpi-main-suffix">{{ item.mainSuffix }}</span>
      </div>

      <!-- 进度条 -->
      <div v-if="item.bar !== undefined && item.bar !== null" class="kpi-bar-track">
        <div
          class="kpi-bar-fill"
          :class="`tone-${item.tone}`"
          :style="{ width: Math.min(Math.max(item.bar, 0), 100) + '%' }"
        ></div>
      </div>

      <!-- 补充信息行 -->
      <div class="kpi-rows">
        <div v-for="(row, i) in item.rows" :key="i" class="kpi-row">
          <span class="kpi-row-label">{{ row.label }}</span>
          <span class="kpi-row-value" :title="row.value">{{ row.value }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineOptions({ name: 'RestrictionKpiStrip' })

export interface KpiRow {
  label: string
  value: string
}

export interface KpiItem {
  key: string
  title: string
  subtitle: string
  /** 右上角状态标签文字 */
  tag: string
  /** 标签色调 */
  tone: 'blue' | 'green' | 'red' | 'orange' | 'purple' | 'gray'
  /** 主体数值（大字） */
  main: string
  /** 数值单位（跟在后面的小字） */
  mainUnit?: string
  /** 数值后缀（如 "/ 100"） */
  mainSuffix?: string
  /** 进度条百分比 0-100，不传则不显示 */
  bar?: number | null
  rows: KpiRow[]
}

defineProps<{
  items: KpiItem[]
}>()
</script>

<style scoped>
.kpi-strip {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 12px;
}

.kpi-card {
  background: var(--panel-bg);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
  transition: box-shadow 0.2s;
}

.kpi-card:hover {
  box-shadow: var(--shadow-sm);
}

/* ===== 标题行 ===== */
.kpi-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 8px;
}

.kpi-titles {
  min-width: 0;
}

.kpi-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.4;
}

.kpi-subtitle {
  font-size: 11px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 状态标签 */
.kpi-tag {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  padding: 4px 8px;
  border-radius: 4px;
  white-space: nowrap;
}

.tone-blue   { color: #1d6fd8; background: rgba(59, 130, 246, 0.1); }
.tone-green  { color: #15803d; background: rgba(34, 197, 94, 0.12); }
.tone-red    { color: #c22f2f; background: rgba(239, 68, 68, 0.1); }
.tone-orange { color: #b45309; background: rgba(245, 158, 11, 0.14); }
.tone-purple { color: #6d28d9; background: rgba(139, 92, 246, 0.12); }
.tone-gray   { color: var(--text-secondary); background: var(--bg-page); }

/* ===== 主体数值 ===== */
.kpi-main {
  display: flex;
  align-items: baseline;
  gap: 4px;
  min-width: 0;
}

.kpi-main-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kpi-main-unit {
  font-size: 12px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.kpi-main-suffix {
  font-size: 12px;
  color: var(--text-tertiary);
  flex-shrink: 0;
}

/* ===== 进度条 ===== */
.kpi-bar-track {
  height: 5px;
  border-radius: 3px;
  background: var(--bg-page);
  overflow: hidden;
}

.kpi-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.6s ease;
}

.kpi-bar-fill.tone-blue   { background: #3b82f6; }
.kpi-bar-fill.tone-green  { background: #22c55e; }
.kpi-bar-fill.tone-red    { background: #ef4444; }
.kpi-bar-fill.tone-orange { background: #f59e0b; }
.kpi-bar-fill.tone-purple { background: #8b5cf6; }
.kpi-bar-fill.tone-gray   { background: var(--text-tertiary); }

/* ===== 补充信息行 ===== */
.kpi-rows {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.kpi-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 11px;
  min-width: 0;
}

.kpi-row-label {
  color: var(--text-tertiary);
  white-space: nowrap;
  flex-shrink: 0;
}

.kpi-row-label::before {
  content: '·';
  margin-right: 4px;
  color: var(--border-color);
}

.kpi-row-value {
  color: var(--text-secondary);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ===== 响应式 ===== */
@media (max-width: 1500px) {
  .kpi-strip {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 760px) {
  .kpi-strip {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
