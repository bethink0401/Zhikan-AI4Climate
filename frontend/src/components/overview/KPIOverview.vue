<template>
  <div class="kpi-overview">
    <div
      v-for="item in data"
      :key="item.key"
      class="kpi-card"
    >
      <!-- 标题行：彩色图标 + 名称 + 右侧等级徽章 -->
      <div class="kpi-head">
        <span class="kpi-icon" :class="`tone-${item.tone}`">
          <el-icon :size="15"><component :is="item.icon" /></el-icon>
        </span>
        <span class="kpi-label">{{ item.label }}</span>
        <span class="kpi-badge" :class="`badge-${item.badgeTone}`">{{ item.badge }}</span>
      </div>

      <!-- 数值行：大数字 + 右侧小注 -->
      <div class="kpi-value-row">
        <span class="kpi-value" :class="{ 'is-text': item.isText }">
          {{ item.value }}<i v-if="item.unit" class="kpi-unit">{{ item.unit }}</i>
        </span>
        <span v-if="item.sideNote" class="kpi-side">{{ item.sideNote }}</span>
      </div>

      <!-- 底部说明行 -->
      <div v-if="item.footMain || item.footSide" class="kpi-foot">
        <span v-if="item.footMain" class="kpi-foot-main" :class="`foot-${item.footTone || 'grey'}`">
          {{ item.footMain }}
        </span>
        <span v-if="item.footSide" class="kpi-foot-side">{{ item.footSide }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { Component } from 'vue'

defineOptions({ name: 'KPIOverview', inheritAttrs: false })

export interface KPIItem {
  key: string
  label: string
  /** 标题图标的色调 */
  tone: 'red' | 'orange' | 'rose' | 'blue' | 'violet'
  /** 标题图标（element-plus 图标组件） */
  icon: Component
  /** 右上角徽章文字，如「高」「首要因子」 */
  badge: string
  /** 徽章配色 */
  badgeTone: 'red' | 'orange' | 'blue' | 'grey'
  /** 主数值（数字或短文本，如「交通源」） */
  value: string | number
  /** 主数值为文本（如「交通源」）时用主题蓝加粗 */
  isText?: boolean
  unit?: string
  /** 大数字右侧的小灰注，如「基线 0.75」 */
  sideNote?: string
  /** 底部左侧说明 */
  footMain?: string
  footTone?: 'red' | 'orange' | 'grey'
  /** 底部右侧补充（灰色） */
  footSide?: string
}

defineProps<{
  data: KPIItem[]
}>()
</script>

<style scoped>
.kpi-overview {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 14px;
}

.kpi-card {
  background: var(--panel-bg);
  border-radius: 12px;
  padding: 14px 16px 12px;
  box-shadow: var(--shadow-sm);
  border: 1px solid rgba(148, 163, 184, 0.14);
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  transition: box-shadow 0.2s, transform 0.2s;
}

.kpi-card:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}

/* ===== 标题行 ===== */
.kpi-head {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}

.kpi-icon {
  width: 24px;
  height: 24px;
  border-radius: 7px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.tone-red    { color: #ef4444; background: rgba(239, 68, 68, 0.1); }
.tone-orange { color: #f59e0b; background: rgba(245, 158, 11, 0.12); }
.tone-rose   { color: #f43f5e; background: rgba(244, 63, 94, 0.1); }
.tone-blue   { color: #3b82f6; background: rgba(59, 130, 246, 0.1); }
.tone-violet { color: #6366f1; background: rgba(99, 102, 241, 0.1); }

.kpi-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kpi-badge {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  padding: 4px 8px;
  border-radius: 999px;
}

.badge-red    { color: #dc2626; background: rgba(239, 68, 68, 0.12); }
.badge-orange { color: #b45309; background: rgba(245, 158, 11, 0.15); }
.badge-blue   { color: #2563eb; background: rgba(59, 130, 246, 0.12); }
.badge-grey   { color: #64748b; background: rgba(100, 116, 139, 0.12); }

/* ===== 数值行 ===== */
.kpi-value-row {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.kpi-value {
  font-size: 28px;
  font-weight: 700;
  color: #0f172a;
  line-height: 1.05;
  letter-spacing: -0.5px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kpi-value.is-text {
  font-size: 24px;
  color: #2563eb;
  letter-spacing: 0;
}

.kpi-unit {
  font-style: normal;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-left: 2px;
}

.kpi-side {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-tertiary);
  padding-bottom: 3px;
  white-space: nowrap;
}

/* ===== 底部说明行 ===== */
.kpi-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.kpi-foot-main {
  font-size: 11.5px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.foot-red    { color: #dc2626; }
.foot-orange { color: #b45309; }
.foot-grey   { color: var(--text-secondary); }

.kpi-foot-side {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

/* 响应式：宽度不足时自动换行（标准桌面宽度保持一行五卡） */
@media (max-width: 1280px) {
  .kpi-overview {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 640px) {
  .kpi-overview {
    grid-template-columns: repeat(2, 1fr);
  }
}
</style>
