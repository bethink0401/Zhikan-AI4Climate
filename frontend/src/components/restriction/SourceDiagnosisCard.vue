<template>
  <div class="source-diagnosis-card" :class="`risk-border-${riskLevel}`">
    <!-- 左侧：主要来源判断 -->
    <div class="diagnosis-main">
      <div class="diagnosis-label">主要来源判断</div>
      <div class="diagnosis-value">{{ mainJudgementLabel }}</div>
      <div class="diagnosis-original" :title="mainJudgement">
        {{ mainJudgement }}
      </div>
    </div>

    <!-- 中间分割线 -->
    <div class="divider-v"></div>

    <!-- 中间：交通源评分 -->
    <div class="diagnosis-score">
      <div class="score-ring-wrapper">
        <svg class="score-ring" viewBox="0 0 64 64" width="72" height="72">
          <!-- 底圆 -->
          <circle
            cx="32" cy="32" r="26"
            fill="none"
            stroke="var(--border-color)"
            stroke-width="6"
          />
          <!-- 进度圆 -->
          <circle
            cx="32" cy="32" r="26"
            fill="none"
            :stroke="ringColor"
            stroke-width="6"
            stroke-linecap="round"
            stroke-dasharray="163.4"
            :stroke-dashoffset="ringOffset"
            transform="rotate(-90 32 32)"
            style="transition: stroke-dashoffset 0.6s ease"
          />
          <!-- 百分比文字 -->
          <text x="32" y="36" text-anchor="middle" font-size="13" font-weight="700" :fill="ringColor">
            {{ trafficScorePct }}%
          </text>
        </svg>
      </div>
      <div class="score-label">交通源评分</div>
    </div>

    <!-- 中间分割线 -->
    <div class="divider-v"></div>

    <!-- 右侧：风险等级 + 置信度 -->
    <div class="diagnosis-meta">
      <div class="meta-item">
        <span class="meta-label">综合风险</span>
        <StatusBadge :risk-level="riskLevel" :text="riskLevelLabel" />
      </div>
      <div class="meta-item">
        <span class="meta-label">分析置信度</span>
        <ConfidenceIndicator :confidence="confidence" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from '@/components/global/StatusBadge.vue'
import ConfidenceIndicator from '@/components/global/ConfidenceIndicator.vue'
import { getSourceJudgementLabel } from '@/utils/sourceMapper'
import { getRiskLabel } from '@/utils/riskMapper'

defineOptions({ name: 'SourceDiagnosisCard' })

// ---------- Props ----------
const props = defineProps<{
  /** 主要来源判断（英文原值，组件内部负责映射） */
  mainJudgement: string
  /** 综合风险等级 */
  riskLevel: 'low' | 'medium' | 'high' | 'very_high'
  /** 置信度 0-1 */
  confidence: number
  /** 交通源评分 0-1 */
  trafficSourceScore: number
}>()

// ---------- 派生数据 ----------
const mainJudgementLabel = computed(() =>
  getSourceJudgementLabel(props.mainJudgement)
)

const riskLevelLabel = computed(() => getRiskLabel(props.riskLevel))

/** 交通源评分百分比（整数） */
const trafficScorePct = computed(() =>
  Math.round(props.trafficSourceScore * 100)
)

/** 环形进度圆的颜色跟随风险等级 */
const ringColor = computed(() => {
  const map: Record<string, string> = {
    low: 'var(--risk-low)',
    medium: 'var(--risk-medium)',
    high: 'var(--risk-high)',
    very_high: 'var(--risk-very-high)'
  }
  return map[props.riskLevel] ?? 'var(--theme-color)'
})

/**
 * SVG circle r=26, 周长 = 2π×26 ≈ 163.4
 * dashoffset = 163.4 × (1 - score)，让进度从顶部顺时针填充
 */
const ringOffset = computed(() =>
  163.4 * (1 - Math.min(Math.max(props.trafficSourceScore, 0), 1))
)
</script>

<style scoped>
.source-diagnosis-card {
  display: flex;
  align-items: center;
  gap: 0;
  background: var(--panel-bg);
  border-radius: 10px;
  padding: 20px 28px;
  box-shadow: var(--shadow-sm);
  border-left: 5px solid var(--border-color);
  transition: box-shadow 0.2s;
}

.source-diagnosis-card:hover {
  box-shadow: var(--shadow-md);
}

/* 左边框随风险等级变色 */
.risk-border-low    { border-left-color: var(--risk-low); }
.risk-border-medium { border-left-color: var(--risk-medium); }
.risk-border-high   { border-left-color: var(--risk-high); }
.risk-border-very_high { border-left-color: var(--risk-very-high); }

/* 左侧：主要判断 */
.diagnosis-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.diagnosis-label {
  font-size: 12px;
  color: var(--text-tertiary);
  font-weight: 500;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.diagnosis-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.3;
}

.diagnosis-original {
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 竖向分割线 */
.divider-v {
  width: 1px;
  height: 60px;
  background: var(--border-color);
  margin: 0 28px;
  flex-shrink: 0;
}

/* 中间：环形进度 */
.diagnosis-score {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}

.score-ring-wrapper {
  line-height: 0;
}

.score-ring {
  display: block;
}

.score-label {
  font-size: 12px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

/* 右侧：风险等级 + 置信度 */
.diagnosis-meta {
  display: flex;
  flex-direction: column;
  gap: 14px;
  flex-shrink: 0;
  min-width: 180px;
}

.meta-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.meta-label {
  font-size: 13px;
  color: var(--text-secondary);
  white-space: nowrap;
}

/* 响应式 */
@media (max-width: 900px) {
  .source-diagnosis-card {
    flex-wrap: wrap;
    gap: 16px;
    padding: 16px 20px;
  }

  .divider-v {
    display: none;
  }

  .diagnosis-meta {
    min-width: 0;
    width: 100%;
    flex-direction: row;
    justify-content: space-between;
  }
}
</style>
