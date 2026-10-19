<template>
  <div class="weather-risk-panel">
    <!-- 标题 -->
    <div class="panel-header">
      <span class="panel-title">🌤️ 气象风险总览</span>
    </div>

    <!-- 空状态 -->
    <div v-if="isEmpty" class="empty-state">
      <el-empty description="暂无气象风险数据" />
    </div>

    <div v-else class="panel-body">
      <!-- ===== 第一行：综合风险（大号主视觉） ===== -->
      <div class="risk-summary">
        <div class="risk-score-wrapper">
          <div class="risk-score-ring">
            <svg viewBox="0 0 120 120" class="progress-ring">
              <circle
                cx="60"
                cy="60"
                r="50"
                class="ring-bg"
              />
              <circle
                cx="60"
                cy="60"
                r="50"
                class="ring-fill"
                :style="ringStyle"
              />
            </svg>
            <div class="risk-score-center">
              <span class="score-number">{{ formattedRisk }}</span>
              <span class="score-unit">%</span>
            </div>
          </div>
          <div class="risk-level-wrapper">
            <StatusBadge :risk-level="riskLevel" :text="levelLabel" />
            <span class="risk-subtitle">综合气象风险</span>
          </div>
        </div>
      </div>

      <!-- ===== 第二行：四维风险拆解（2×2 网格） ===== -->
      <div class="risk-dimensions">
        <div
          v-for="dim in dimensionItems"
          :key="dim.key"
          class="dimension-item"
        >
          <div class="dimension-header">
            <span class="dimension-name">{{ dim.label }}</span>
            <span class="dimension-value" :style="{ color: getRiskColor(dim.value) }">
              {{ dim.value }}%
            </span>
          </div>
          <div class="dimension-bar">
            <div
              class="dimension-fill"
              :style="{ width: dim.value + '%' }"
              :class="getRiskClass(dim.value)"
            ></div>
          </div>
        </div>
      </div>

      <!-- ===== 第三行：气象证据（紧凑标签） ===== -->
      <div v-if="evidenceList.length > 0" class="risk-evidence">
        <div class="evidence-title">📋 气象证据</div>
        <div class="evidence-tags">
          <span
            v-for="(item, index) in evidenceList"
            :key="index"
            class="evidence-tag"
          >
            {{ item }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from '@/components/global/StatusBadge.vue'

defineOptions({ name: 'WeatherRiskPanel' })

// ---------- 1. Props 类型定义 ----------
export type RiskLevel = 'low' | 'medium' | 'high' | 'very_high'

export interface WeatherDimensions {
  staticWind?: number
  windShift?: number
  highHumidity?: number
  rainInsufficient?: number
}

const props = defineProps<{
  riskLevel?: RiskLevel
  riskPercent?: number
  dimensions?: WeatherDimensions
  evidence?: string[]
}>()

// ---------- 2. 判断数据是否为空 ----------
const isEmpty = computed(() => {
  return !props.riskLevel || props.riskPercent === undefined || props.riskPercent === null
})

// ---------- 3. 格式化风险百分比 ----------
const formattedRisk = computed(() => {
  return Math.round(props.riskPercent ?? 0)
})

// ---------- 4. 等级文字映射 ----------
const levelLabelMap: Record<RiskLevel, string> = {
  low: '低风险',
  medium: '中风险',
  high: '高风险',
  very_high: '极高风险'
}

const riskLevel = computed(() => {
  return props.riskLevel || 'low'
})

const levelLabel = computed(() => {
  return levelLabelMap[riskLevel.value] || '未知'
})

// ---------- 5. 进度环样式 ----------
const ringStyle = computed(() => {
  const percent = formattedRisk.value
  const circumference = 2 * Math.PI * 50 // 2πr = 314.16
  const offset = circumference - (percent / 100) * circumference
  const colorMap: Record<string, string> = {
    low: 'var(--risk-low)',
    medium: 'var(--risk-medium)',
    high: 'var(--risk-high)',
    very_high: 'var(--risk-very-high)'
  }
  return {
    strokeDasharray: circumference,
    strokeDashoffset: offset,
    stroke: colorMap[riskLevel.value] || 'var(--theme-color)'
  }
})

// ---------- 6. 维度数据 ----------
const dimensionItems = computed(() => {
  const dims = props.dimensions || {}
  const items = [
    { key: 'staticWind', label: '静风积聚', value: dims.staticWind ?? 0 },
    { key: 'windShift', label: '风向变化', value: dims.windShift ?? 0 },
    { key: 'highHumidity', label: '高湿不利', value: dims.highHumidity ?? 0 },
    { key: 'rainInsufficient', label: '降雨清除不足', value: dims.rainInsufficient ?? 0 }
  ]
  return items
})

// ---------- 7. 证据列表 ----------
const evidenceList = computed(() => {
  return props.evidence || []
})

// ---------- 8. 风险颜色工具 ----------
const getRiskClass = (value: number) => {
  if (value >= 70) return 'fill-very-high'
  if (value >= 50) return 'fill-high'
  if (value >= 30) return 'fill-medium'
  return 'fill-low'
}

const getRiskColor = (value: number) => {
  if (value >= 70) return 'var(--risk-very-high)'
  if (value >= 50) return 'var(--risk-high)'
  if (value >= 30) return 'var(--risk-medium)'
  return 'var(--risk-low)'
}
</script>

<style scoped>
/* ===== 容器 ===== */
.weather-risk-panel {
  background: var(--panel-bg);
  border-radius: 8px;
  padding: 16px 20px;
  box-shadow: var(--shadow-sm);
}

/* ===== 标题 ===== */
.panel-header {
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-color);
}

.panel-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

/* ===== 空状态 ===== */
.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 160px;
}

/* ===== 面板主体 ===== */
.panel-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

/* ===== 综合风险（大号主视觉） ===== */
.risk-summary {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  background: var(--bg-page);
  border-radius: 8px;
}

.risk-score-wrapper {
  display: flex;
  align-items: center;
  gap: 24px;
}

/* 进度环 */
.risk-score-ring {
  position: relative;
  width: 100px;
  height: 100px;
  flex-shrink: 0;
}

.progress-ring {
  width: 100px;
  height: 100px;
  transform: rotate(-90deg);
}

.ring-bg {
  fill: none;
  stroke: var(--border-color);
  stroke-width: 8;
}

.ring-fill {
  fill: none;
  stroke-width: 8;
  stroke-linecap: round;
  transition: stroke-dashoffset 0.8s ease;
}

.risk-score-center {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  text-align: center;
}

.score-number {
  font-size: 28px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1;
}

.score-unit {
  font-size: 14px;
  color: var(--text-tertiary);
  margin-left: 2px;
}

.risk-level-wrapper {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
}

.risk-subtitle {
  font-size: 12px;
  color: var(--text-tertiary);
}

/* ===== 四维风险拆解（2×2 网格） ===== */
.risk-dimensions {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px 20px;
  padding: 4px 0;
}

.dimension-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 6px 0;
}

.dimension-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.dimension-name {
  font-size: 12px;
  color: var(--text-secondary);
}

.dimension-value {
  font-size: 13px;
  font-weight: 600;
}

.dimension-bar {
  width: 100%;
  height: 5px;
  background: var(--bg-page);
  border-radius: 3px;
  overflow: hidden;
}

.dimension-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.5s ease;
}

.fill-low {
  background: var(--risk-low);
}
.fill-medium {
  background: var(--risk-medium);
}
.fill-high {
  background: var(--risk-high);
}
.fill-very-high {
  background: var(--risk-very-high);
}

/* ===== 气象证据（紧凑标签） ===== */
.risk-evidence {
  padding-top: 12px;
  border-top: 1px solid var(--border-color);
}

.evidence-title {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
  margin-bottom: 8px;
}

.evidence-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.evidence-tag {
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg-page);
  padding: 3px 12px;
  border-radius: 12px;
  line-height: 1.5;
}

/* ===== 响应式 ===== */
@media (max-width: 640px) {
  .risk-dimensions {
    grid-template-columns: 1fr;
  }

  .risk-score-wrapper {
    flex-direction: column;
    gap: 12px;
    text-align: center;
  }

  .risk-level-wrapper {
    align-items: center;
  }

  .risk-score-ring {
    width: 80px;
    height: 80px;
  }

  .progress-ring {
    width: 80px;
    height: 80px;
  }

  .score-number {
    font-size: 22px;
  }
}
</style>