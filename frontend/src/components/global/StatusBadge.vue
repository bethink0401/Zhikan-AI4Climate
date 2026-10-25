<template>
  <span class="status-badge" :class="badgeClass">
    {{ displayText }}
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

defineOptions({ name: 'StatusBadge' })

const props = defineProps<{
  status?: 'success' | 'warning' | 'error' | 'info'
  riskLevel?: 'low' | 'medium' | 'high' | 'very_high'
  text?: string
}>()

// 合并类名（riskLevel 优先）
const badgeClass = computed(() => {
  if (props.riskLevel) return `risk-${props.riskLevel}`
  if (props.status) return `status-${props.status}`
  return ''
})

// 文字映射
const statusTextMap: Record<string, string> = {
  success: '正常',
  warning: '警告',
  error: '异常',
  info: '信息'
}
const riskTextMap: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
  very_high: '极高'
}

const displayText = computed(() => {
  if (props.text) return props.text
  if (props.riskLevel) return riskTextMap[props.riskLevel] || '未知'
  if (props.status) return statusTextMap[props.status] || '未知'
  return '—'
})
</script>

<style scoped>
.status-badge {
  display: inline-block;
  padding: 2px 12px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
}

/* 状态颜色 */
.status-success {
  background: var(--status-good);
  color: #fff;
}
.status-warning {
  background: var(--status-moderate);
  color: #fff;
}
.status-error {
  background: var(--status-unhealthy);
  color: #fff;
}
.status-info {
  background: var(--theme-color);
  color: #fff;
}

/* 风险等级颜色 */
.risk-low {
  background: var(--risk-low);
  color: #fff;
}
.risk-medium {
  background: var(--risk-medium);
  color: #fff;
}
.risk-high {
  background: var(--risk-high);
  color: #fff;
}
.risk-very-high {
  background: var(--risk-very-high);
  color: #fff;
}
</style>