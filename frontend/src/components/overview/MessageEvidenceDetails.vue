<template>
  <el-collapse v-if="hasEvidence" v-model="activeNames" class="evidence-collapse">
    <el-collapse-item name="evidence">
      <template #title>
        <span class="collapse-title">
          <el-icon :size="14"><Collection /></el-icon>
          {{ collapseLabel }}
          <span class="collapse-count">{{ totalCount }} 项</span>
        </span>
      </template>

      <div v-if="scopeText" class="evidence-scope">{{ scopeText }}</div>

      <section v-if="chartItems.length" class="evidence-section">
        <div class="section-title">图表数据与口径</div>
        <div v-for="(item, index) in chartItems" :key="`${item.label}-${index}`" class="business-item">
          <strong class="item-label">{{ item.label }}</strong>
          <span>{{ item.detail }}</span>
        </div>
        <p class="chart-note">图表使用本次分析返回的数据；缺失的时点不会补造数值。</p>
      </section>

      <section v-if="businessItems.length" class="evidence-section" :class="{ 'following-section': chartItems.length }">
        <div class="section-title">{{ isRecommendationResponse ? '建议所依据的数据' : '本次分析数据' }}</div>
        <div v-for="(item, index) in businessItems" :key="`${item.label}-${index}`" class="business-item">
          <strong class="item-label">{{ item.label }}</strong>
          <span>{{ item.detail }}</span>
        </div>
      </section>

      <section v-if="literatureItems.length" class="evidence-section literature-section">
        <div class="section-title">文献知识依据</div>
        <article v-for="(source, index) in literatureItems" :key="source.key" class="literature-item">
          <div class="literature-title">{{ index + 1 }}. {{ source.title }}</div>
          <div class="literature-meta">
            <span v-if="source.author">作者：{{ source.author }}</span>
            <span v-if="source.year">年份：{{ source.year }}</span>
            <span v-if="source.page">页码：{{ source.page }}</span>
            <span v-if="source.section">章节：{{ source.section }}</span>
            <span v-if="source.authorityLevel">权威等级：{{ authorityLabel(source.authorityLevel) }}</span>
            <span v-if="source.metadataTrust">元数据：{{ trustLabel(source.metadataTrust) }}</span>
            <span v-if="source.citationVerified !== undefined">引用核验：{{ source.citationVerified ? '已核验' : '未外部核验' }}</span>
          </div>
          <p v-if="source.excerpt" class="literature-excerpt">{{ source.excerpt }}</p>
        </article>
      </section>

      <RecommendationKnowledgeSupplement
        v-if="hasProfessionalKnowledge"
        :knowledge="professionalKnowledge"
      />

      <div v-if="limitations.length" class="evidence-limitations">
        限制：{{ limitations.join('；') }}
      </div>
    </el-collapse-item>
  </el-collapse>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { Collection } from '@element-plus/icons-vue'
import type { AIChatResponse } from '@/types/aiChat'
import RecommendationKnowledgeSupplement from './RecommendationKnowledgeSupplement.vue'
import { chartDisplayTitle, metricDisplayName, visualizationDisplayTitle } from '@/utils/aiVisualization'
import { isVerifiedChatChart } from '@/utils/chatChartVisibility'

type BusinessItem = { label: string; detail: string }
type LiteratureItem = {
  key: string
  title: string
  author?: string | null
  year?: number | null
  page?: string
  section?: string | null
  authorityLevel?: string
  metadataTrust?: string
  citationVerified?: boolean
  excerpt?: string
}

const props = defineProps<{ response: AIChatResponse }>()
const activeNames = ref<string[]>([])
const isRecommendationResponse = computed(() => Boolean(
  (props.response.recommendation_display
    && props.response.user_goal === props.response.recommendation_display.recommendation_type)
  || (props.response.recommendation_result
    && props.response.user_goal === props.response.recommendation_result.recommendation_type)
))
const professionalKnowledge = computed(() => isRecommendationResponse.value
  ? props.response.recommendation_display?.professional_knowledge
  : null)
const hasProfessionalKnowledge = computed(() => Boolean(
  professionalKnowledge.value?.summary?.trim() || professionalKnowledge.value?.sources?.length
))
const collapseLabel = computed(() => isRecommendationResponse.value
  ? '查看建议依据'
  : chartItems.value.length > 0
    ? '查看图表数据与说明'
    : '查看回答依据')

const domainLabels: Record<string, string> = {
  AirQualityAgent: '空气质量',
  MeteorologyAgent: '气象',
  TrafficAgent: '交通代理指标',
  SpatialAgent: '空间影响',
  SourceAgent: '来源筛查',
  DecisionAgent: '管理决策',
  TravelAgent: '出行规划',
  ReportAgent: '综合报告',
  air_quality: '空气质量',
  meteorology: '气象',
  traffic: '交通代理指标',
  spatial: '空间影响',
  source: '来源筛查',
  decision: '管理决策',
  report: '综合报告'
}

function displayScalar(value: unknown): string {
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(3).replace(/0+$/, '').replace(/\.$/, '')
  if (typeof value === 'string') return value.length > 120 ? `${value.slice(0, 120)}…` : value
  if (typeof value === 'boolean') return value ? '是' : '否'
  return ''
}

function recommendationEvidenceDetail(item: Record<string, unknown>): string {
  const parts: string[] = []
  const value = displayScalar(item.value)
  if (value) parts.push(value)
  const unit = displayScalar(item.unit)
  if (unit) parts.push(unit)
  const level = displayScalar(item.level)
  if (level) parts.push(`等级 ${level}`)
  const window = displayScalar(item.prediction_window)
  if (window) parts.push(`时段 ${window}`)
  return parts.join(' · ') || '已纳入当前建议'
}

function recommendationEvidenceLabel(type: string): string {
  return ({
    pollution_status: '污染状态',
    air_quality_anomaly_score: '空气质量异常评分',
    aqhi: 'AQHI',
    traffic_pressure: '交通压力代理指标',
    wind_speed: '风速',
    affected_area: '影响区域',
    future_environmental_risk: '启发式环境风险筛查'
  }[type] ?? type)
}

function displayUnit(unit: string | null): string {
  if (unit === 'AQHI index') return '指数'
  if (unit === 'EU') return '吸入当量'
  if (unit === 'dimensionless independent screening score') return '独立筛查评分'
  if (unit === 'dimensionless screening score') return '筛查评分'
  if (unit?.startsWith('degree (meteorological')) return '°（来向）'
  return unit || ''
}

const scopeText = computed(() => {
  const task = props.response.context
  if (!task?.station_name && !task?.station_id) return ''
  const station = task.station_name || task.station_id
  const period = task.start_time && task.end_time
    ? ` · ${task.start_time} 至 ${task.end_time}` : ''
  return `分析范围：${station}${period}`
})

const chartItems = computed<BusinessItem[]>(() => {
  const items: BusinessItem[] = []
  for (const block of props.response.content_blocks ?? []) {
    if (block.type !== 'chart' || !isVerifiedChatChart(block.chart)) continue
    const chart = block.chart
    const bySource = new Map<string, string[]>()
    for (const metric of chart.provenance.metrics) {
      const source = domainLabels[metric.source_agent] ?? metric.source_agent
      const names = bySource.get(source) ?? []
      names.push(`${metricDisplayName(metric.key)}${metric.unit ? `（${displayUnit(metric.unit)}）` : ''}`)
      bySource.set(source, names)
    }
    const metrics = [...bySource].map(([source, names]) => `${source}：${names.join('、')}`)
    const count = chart.type === 'scatter'
      ? chart.series[0]?.data.length ?? 0
      : chart.categories.length
    const countLabel = chart.type === 'scatter' ? '组同时间点配对数据'
      : chart.dimension === 'time' ? '个时间点' : '个类别'
    const grouping = chart.transform.bucket_hours > 1
      ? `；每 ${chart.transform.bucket_hours} 小时取平均`
      : ''
    const selection = chart.provenance.selection
    const request = selection?.model_request
    const selectedAt = request?.received_at && Number.isFinite(Date.parse(request.received_at))
      ? new Date(request.received_at).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : ''
    const decision = request ? `；选图模型：${request.model}${selectedAt ? `（${selectedAt}）` : ''}${selection?.reason ? `；选图理由：${selection.reason}` : ''}` : ''
    items.push({
      label: chartDisplayTitle(chart.title, chart.metrics, chart.type),
      detail: `${metrics.join('；')}。展示 ${count} ${countLabel}${grouping}；时间均按香港时间${decision}。`
    })
  }
  const legacy = props.response.visualization
  if (!items.length && legacy?.data && legacy.spec?.view_type === 'chart'
      && props.response.visualization_action !== 'none' && props.response.visualization_action !== 'reset'
      && legacy.status !== 'unchanged') {
    const metrics = Object.values(legacy.data.metrics)
    items.push({
      label: visualizationDisplayTitle(legacy),
      detail: `${metrics.map(item => `${metricDisplayName(item.metric)}${item.unit ? `（${displayUnit(item.unit)}）` : ''}，${domainLabels[item.source_agent] ?? item.source_agent}`).join('；')}；香港时间`
    })
  }
  return items
})

function agentFact(domain: string, result: Record<string, unknown>): BusinessItem | null {
  const label = domainLabels[domain] ?? domain
  if (domain === 'AirQualityAgent') {
    const pollutants = Array.isArray(result.main_pollutants) ? result.main_pollutants.join('、') : ''
    const aqhi = displayScalar(result.avg_aqhi)
    return { label, detail: [pollutants && `主要污染物：${pollutants}`, aqhi && `平均 AQHI：${aqhi}`].filter(Boolean).join('；') }
  }
  if (domain === 'MeteorologyAgent') {
    const wind = displayScalar(result.mean_wind_speed)
    const humidity = displayScalar(result.mean_humidity)
    return { label, detail: [wind && `平均风速：${wind} m/s`, humidity && `平均湿度：${humidity}%`].filter(Boolean).join('；') }
  }
  if (domain === 'TrafficAgent') {
    const score = displayScalar(result.traffic_pressure_score)
    return { label, detail: score && `交通压力筛查评分：${score}（不是实测车流量）` }
  }
  if (domain === 'SourceAgent') {
    const judgement = displayScalar(result.main_judgement)
    return { label, detail: judgement && `来源筛查判断：${judgement}（不是排放贡献率）` }
  }
  if (domain === 'SpatialAgent') {
    const count = displayScalar(result.sensitive_poi_count)
    return { label, detail: count && `潜在影响区域内敏感目标：${count} 处（模型筛查）` }
  }
  return null
}

const businessItems = computed<BusinessItem[]>(() => {
  if (props.response.recommendation_display) {
    const categoryLabels: Record<string, string> = {
      observed: '实际监测事实',
      estimated: '模型估计或参考期统计',
      screening: '启发式规则筛查',
      limitation: '数据范围与适用说明'
    }
    return props.response.recommendation_display.evidence.map(item => ({
      label: categoryLabels[item.category] ?? item.category,
      detail: [
        `${item.label}：${item.value}${item.unit ? ` ${item.unit}` : ''}`,
        `来源：${item.source}`,
        `时间口径：${item.time_basis}`,
        item.note
      ].filter(Boolean).join('；')
    }))
  }
  if (isRecommendationResponse.value) {
    return (props.response.recommendation_result?.evidence ?? [])
      .filter(item => item.type !== 'future_environmental_risk')
      .map(item => {
      const type = typeof item.type === 'string' ? item.type : '业务证据'
      return {
        label: recommendationEvidenceLabel(type),
        detail: recommendationEvidenceDetail(item)
      }
    })
  }
  const items: BusinessItem[] = []
  for (const evidence of props.response.recommendation_result?.evidence ?? []) {
    const type = typeof evidence.type === 'string' ? evidence.type : '业务证据'
    items.push({ label: recommendationEvidenceLabel(type), detail: recommendationEvidenceDetail(evidence) })
  }
  for (const fact of props.response.fused_evidence?.facts ?? []) {
    const result = fact.result ?? {}
    const available = Object.entries(result)
      .filter(([, value]) => ['string', 'number', 'boolean'].includes(typeof value) && value !== null)
      .slice(0, 3)
      .map(([key, value]) => `${key}: ${displayScalar(value)}`)
    items.push({
      label: domainLabels[fact.agent] ?? fact.agent,
      detail: available.join('；') || '结构化业务结果已纳入回答'
    })
  }
  if (!items.length && !chartItems.value.length) {
    for (const [domain, envelope] of Object.entries(props.response.agent_results ?? {})) {
      if (!envelope || typeof envelope !== 'object') continue
      const record = envelope as Record<string, unknown>
      const result = record.result
      if (!result || typeof result !== 'object') continue
      const fact = agentFact(domain, result as Record<string, unknown>)
      if (fact?.detail) items.push(fact)
    }
  }
  return items.filter((item, index, all) => all.findIndex((candidate) => candidate.label === item.label && candidate.detail === item.detail) === index)
})

const literatureItems = computed<LiteratureItem[]>(() => {
  if (isRecommendationResponse.value) return []
  const items: LiteratureItem[] = (props.response.knowledge?.sources ?? []).map((source, index) => ({
    key: `${source.document_id}-${source.page ?? source.page_start ?? index}`,
    title: source.title,
    author: source.author,
    year: source.year,
    page: source.page
      ? String(source.page)
      : source.page_start
        ? `${source.page_start}${source.page_end && source.page_end !== source.page_start ? `-${source.page_end}` : ''}`
        : undefined,
    section: source.section,
    authorityLevel: source.authority_level,
    metadataTrust: source.metadata_trust,
    citationVerified: source.citation_verified,
    excerpt: source.excerpt
  }))
  for (const [index, hit] of (props.response.fused_evidence?.knowledge_evidence ?? []).entries()) {
    const key = `${hit.document_id}-${hit.page ?? hit.page_start ?? index}`
    if (items.some((item) => item.key === key)) continue
    items.push({
      key,
      title: hit.title,
      author: hit.author,
      year: hit.year,
      page: hit.page ? String(hit.page) : hit.page_start ? `${hit.page_start}${hit.page_end ? `-${hit.page_end}` : ''}` : undefined,
      section: hit.section,
      authorityLevel: hit.authority_level,
      excerpt: hit.text
    })
  }
  return items
})

const limitations = computed(() => Array.from(new Set([
  ...(isRecommendationResponse.value ? props.response.recommendation_result?.limitations ?? [] : []),
  ...(isRecommendationResponse.value ? [] : props.response.fused_evidence?.limitations ?? []),
  ...(isRecommendationResponse.value ? [] : props.response.fused_evidence?.warnings ?? []),
  ...(isRecommendationResponse.value ? [] : props.response.knowledge?.warnings ?? [])
])))
const hasEvidence = computed(() => chartItems.value.length > 0 || businessItems.value.length > 0 || literatureItems.value.length > 0 || limitations.value.length > 0 || hasProfessionalKnowledge.value)
const totalCount = computed(() => chartItems.value.length + businessItems.value.length + literatureItems.value.length + Number(hasProfessionalKnowledge.value))

function authorityLabel(level: string): string {
  return ({ official: '官方', peer_reviewed: '同行评审', internal: '内部资料', user_uploaded: '用户上传', unknown: '未知' }[level] ?? level)
}

function trustLabel(trust: string): string {
  return ({ verified: '已核验', system_curated: '系统维护', user_asserted: '用户声明', unknown: '未知' }[trust] ?? trust)
}
</script>

<style scoped>
.evidence-collapse { margin-top: 8px; border-top: 1px solid rgba(148, 163, 184, .18); border-bottom: 0; }
.evidence-collapse :deep(.el-collapse-item__header) { height: 34px; border-bottom: 0; background: transparent; font-size: 11px; }
.evidence-collapse :deep(.el-collapse-item__wrap) { border-bottom: 0; background: transparent; }
.evidence-collapse :deep(.el-collapse-item__content) { padding-bottom: 2px; }
.collapse-title { display: flex; align-items: center; gap: 5px; color: var(--text-secondary); font-weight: 600; }
.collapse-count { color: var(--text-tertiary); font-weight: 400; }
.evidence-section { padding: 7px 8px; border-radius: 5px; background: rgba(148, 163, 184, .07); }
.evidence-scope { padding: 6px 8px; color: var(--text-secondary); font-size: 11px; line-height: 1.5; }
.following-section { margin-top: 6px; }
.literature-section { margin-top: 6px; }
.section-title { margin-bottom: 6px; color: var(--text-secondary); font-size: 11px; font-weight: 600; }
.business-item { margin-top: 9px; color: var(--text-secondary); font-size: 11px; line-height: 1.55; overflow-wrap: anywhere; }
.business-item span { display: block; }
.item-label { display: block; margin-bottom: 2px; color: var(--text-primary); font-size: 11px; font-weight: 600; }
.chart-note { margin: 10px 0 0; color: var(--text-secondary); font-size: 11px; line-height: 1.55; }
.literature-item + .literature-item { margin-top: 8px; padding-top: 8px; border-top: 1px dashed rgba(148, 163, 184, .22); }
.literature-title { color: var(--text-primary); font-size: 11px; font-weight: 600; }
.literature-meta { display: flex; flex-wrap: wrap; gap: 3px 10px; margin-top: 3px; color: var(--text-secondary); font-size: 10px; }
.literature-excerpt { margin: 4px 0 0; color: var(--text-tertiary); font-size: 10px; line-height: 1.45; }
.evidence-limitations { margin-top: 6px; color: #b45309; font-size: 10px; line-height: 1.45; }
</style>
