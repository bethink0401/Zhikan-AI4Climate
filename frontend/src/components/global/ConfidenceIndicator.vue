<template>
  <div class="confidence-indicator">
    <!-- 进度条 -->
    <div class="confidence-bar">
      <div 
        class="confidence-fill" 
        :style="{ width: percent + '%', background: barColor }"
      ></div>
    </div>
    <!-- 百分比文本 -->
    <span class="confidence-text" :style="{ color: barColor }">
      {{ percent }}%
    </span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

defineOptions({ name: 'ConfidenceIndicator' })

const props = defineProps<{
  /** 置信度值，范围 0~1 */
  confidence: number
  /** 是否显示百分比文字，默认 true */
  showText?: boolean
}>()

// 计算百分比（0~100）
const percent = computed(() => {
  const val = props.confidence ?? 0
  return Math.min(Math.max(Math.round(val * 100), 0), 100)
})

// 根据置信度返回颜色（与项目风险色一致）
const barColor = computed(() => {
  const p = percent.value
  if (p >= 80) return 'var(--status-good)'      
  if (p >= 60) return 'var(--status-moderate)'   
  if (p >= 40) return 'var(--status-unhealthy)'  
  return 'var(--risk-very-high)'                  
})
</script>

<style scoped>
.confidence-indicator {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  max-width: 200px;
}

.confidence-bar {
  flex: 1;
  height: 6px;
  background: var(--bg-page, #f0f0f0);
  border-radius: 4px;
  overflow: hidden;
  box-shadow: inset 0 1px 2px rgba(0, 0, 0, 0.06);
}

.confidence-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.5s ease, background 0.3s ease;
}

.confidence-text {
  font-size: 13px;
  font-weight: 600;
  min-width: 40px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  transition: color 0.3s ease;
}
</style>