<template>
  <section class="recommendation-card" aria-label="个性化建议">
    <header class="recommendation-header">
      <el-icon class="recommendation-icon" :size="17"><DataAnalysis /></el-icon>
      <span class="recommendation-title">{{ title }}</span>
      <el-tag :type="confidenceTagType" size="small" effect="light">
        证据完整度：{{ confidenceLabel }}
      </el-tag>
    </header>

    <p class="recommendation-summary">{{ result.summary }}</p>
    <div v-if="recommendationText" class="recommendation-body">
      <span class="section-label">建议</span>
      <span>{{ recommendationText }}</span>
    </div>

    <div v-if="result.future_risk_assessment" class="risk-screening">
      <div class="secondary-heading">
        <el-icon :size="15"><TrendCharts /></el-icon>
        <span>启发式环境风险筛查</span>
      </div>
      <div class="risk-line">
        <span>筛查等级</span>
        <el-tag :type="riskTagType" size="small">{{ riskLabel }}</el-tag>
        <span class="risk-score">规则分 {{ result.future_risk_assessment.risk_score }}/100</span>
      </div>
      <div v-if="result.future_risk_assessment.prediction_window" class="secondary-text">
        筛查时段：{{ result.future_risk_assessment.prediction_window }}
      </div>
      <div class="risk-disclaimer">非官方风险等级，不表示发生概率、医学健康风险或未来实测污染浓度。</div>
    </div>

    <div v-if="result.limitations?.length" class="limitations">
      <div class="secondary-heading">适用限制</div>
      <ul>
        <li v-for="(limitation, index) in result.limitations" :key="`${limitation}-${index}`">
          {{ limitationLabel(limitation) }}
        </li>
      </ul>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { DataAnalysis, TrendCharts } from '@element-plus/icons-vue'
import type { RecommendationResult } from '@/types/aiChat'

const props = defineProps<{ result: RecommendationResult }>()

const title = computed(() => ({
  public_travel_advice: '居民出行建议',
  health_protection: '健康防护建议',
  governance_advice: '城市治理建议'
}[props.result.recommendation_type] ?? '环境建议'))

const confidenceLabel = computed(() => ({
  sufficient: '高',
  partial: '中',
  needs_enrichment: '低',
  unavailable: '不足',
  high: '高',
  medium: '中',
  low: '低',
  insufficient: '不足'
}[props.result.evidence_summary?.status || props.result.confidence] ?? '不足'))

const confidenceTagType = computed<'success' | 'warning' | 'info'>(() => {
  const status = props.result.evidence_summary?.status
  if (status === 'sufficient') return 'success'
  if (status === 'partial') return 'warning'
  if (status === 'needs_enrichment' || status === 'unavailable') return 'info'
  if (props.result.confidence === 'high') return 'success'
  if (props.result.confidence === 'medium') return 'warning'
  return 'info'
})

const recommendationText = computed(() => ({
  reduce_outdoor_exposure_when_environmental_risk_is_elevated: '环境风险筛查信号升高时，适当减少户外暴露，并结合个人健康状况采取防护。',
  governance_measures_based_on_environmental_signals: '结合当前污染、气象、交通代理指标和空间分析结果，评估有针对性的治理措施。',
  prefer_public_transport_or_reduce_long_roadside_walking: '可优先选择公共交通，或减少沿繁忙道路长时间步行。',
  choose_conditionally_and_avoid_busy_road_exposure: '结合出行距离和实际路况选择方式，并尽量避开繁忙道路及污染较高时段。',
  insufficient_data_for_travel_mode_comparison: '当前未返回完整的出行方式对比结果，请先完成路线规划。'
}[props.result.recommendation] ?? props.result.recommendation))

const riskLabel = computed(() => ({ low: '低', medium: '中', high: '高', limited: '信息有限' }[
  props.result.future_risk_assessment?.risk_level ?? 'limited'
] ?? '信息有限'))

const riskTagType = computed<'success' | 'warning' | 'danger' | 'info'>(() => {
  const level = props.result.future_risk_assessment?.risk_level
  if (level === 'high') return 'danger'
  if (level === 'medium') return 'warning'
  if (level === 'low') return 'success'
  return 'info'
})

function limitationLabel(limitation: string): string {
  return ({
    route_specific_exposure_unavailable: '暂无路线级污染暴露分析',
    aqhi_unavailable: 'AQHI 数据不可用',
    traffic_pressure_unavailable: '交通压力代理指标不可用',
    wind_speed_unavailable: '风速数据不可用',
    air_quality_unavailable: '空气质量数据不可用',
    future_meteorology_unavailable: '未使用未来气象数据',
    dispersion_path_unavailable: '传播路径数据不可用'
  }[limitation] ?? limitation)
}
</script>

<style scoped>
.recommendation-card { margin-bottom: 9px; padding: 12px; border: 1px solid rgba(37, 99, 235, .18); border-left: 3px solid #2563eb; border-radius: 8px; background: linear-gradient(135deg, rgba(59, 130, 246, .09), rgba(255, 255, 255, .72)); }
.recommendation-header { display: flex; align-items: center; gap: 7px; margin-bottom: 8px; }
.recommendation-icon { color: #2563eb; flex: 0 0 auto; }
.recommendation-title { flex: 1; color: var(--text-primary); font-size: 13px; font-weight: 700; }
.recommendation-summary { margin: 0; color: var(--text-primary); font-size: 12px; line-height: 1.6; }
.recommendation-body { display: grid; grid-template-columns: auto 1fr; gap: 7px; margin-top: 8px; padding: 8px; border-radius: 5px; background: rgba(255, 255, 255, .68); color: var(--text-secondary); font-size: 11px; line-height: 1.55; }
.section-label { color: #2563eb; font-weight: 600; }
.risk-screening { margin-top: 9px; padding: 8px; border-radius: 5px; background: rgba(245, 158, 11, .08); }
.secondary-heading { display: flex; align-items: center; gap: 5px; color: var(--text-secondary); font-size: 11px; font-weight: 600; }
.risk-line { display: flex; align-items: center; flex-wrap: wrap; gap: 5px; margin-top: 5px; color: var(--text-secondary); font-size: 10px; }
.risk-score, .secondary-text, .risk-disclaimer { color: var(--text-tertiary); font-size: 10px; }
.secondary-text { margin-top: 4px; }
.risk-disclaimer { margin-top: 5px; line-height: 1.45; }
.limitations { margin-top: 9px; }
.limitations ul { margin: 5px 0 0; padding-left: 16px; color: var(--text-tertiary); font-size: 10px; line-height: 1.5; }
</style>
