<template>
  <div class="tf-insights">
    <!-- ① 出行条件评估 -->
    <section class="tf-card">
      <div class="tf-hd">
        <el-icon :size="16" class="tf-hd-icon is-green"><CircleCheck /></el-icon>
        <span class="tf-hd-title">出行条件评估</span>
        <div class="tf-hd-right">
          <span class="tf-pill" :class="conditionPillClass">
            综合得分 {{ result.travel_condition.score.toFixed(0) }} ·
            {{ result.travel_condition.level_label }}
          </span>
        </div>
      </div>

      <div class="tf-body">
        <div class="tf-factor-grid">
          <div v-for="item in factorItems" :key="item.label" class="tf-softbox tf-factor">
            <div class="tf-factor-top">
              <span class="tf-factor-label">{{ item.label }}</span>
              <span class="tf-factor-read" :class="item.toneCls">
                {{ item.value.toFixed(2) }}
                <em>{{ item.read }}</em>
              </span>
            </div>
            <div class="tf-bar">
              <div
                class="tf-bar-fill"
                :style="{ width: `${Math.min(100, item.value * 100)}%`, background: item.color }"
              ></div>
            </div>
          </div>
        </div>
      </div>

      <div class="tf-foot">
        <span class="tf-dot is-green"></span>
        <span class="tf-foot-text">推荐方案契合度 {{ fitPct }}%</span>
        <span class="tf-foot-right tf-link-text">{{ preferredRouteNote(result.recommendation.mode) }}</span>
      </div>
    </section>

    <!-- ② 推荐决策对比 -->
    <section class="tf-card">
      <div class="tf-hd">
        <el-icon :size="16" class="tf-hd-icon"><TrendCharts /></el-icon>
        <span class="tf-hd-title">推荐决策对比</span>
        <div class="tf-hd-right">
          <span class="tf-pill" :class="confidencePillClass">{{ confidenceText }}</span>
        </div>
      </div>

      <div class="tf-body">
        <div class="tf-mode-grid">
          <div
            v-for="item in modeCards"
            :key="item.mode"
            class="tf-mode"
            :class="`is-${item.tone}`"
          >
            <span class="tf-mode-name">
              {{ item.label }}<em v-if="item.isBest">（推荐）</em>
            </span>
            <span class="tf-mode-value tf-mono">
              {{ item.utility.toFixed(0) }}<em>min</em>
            </span>
            <span class="tf-mode-sub tf-mono">{{ item.intake.toFixed(2) }} EU</span>
          </div>
        </div>
      </div>

      <div class="tf-foot">
        <span class="tf-foot-text">{{ utilityDiffText }}</span>
        <span class="tf-foot-right tf-tone-muted tf-mono">
          在途 {{ result.recommendation.alternative_by_mode[result.recommendation.mode]?.duration_minutes.toFixed(0) ?? '—' }} min
        </span>
      </div>
    </section>

    <!-- ③ 避空污染量化 -->
    <section class="tf-card">
      <div class="tf-hd">
        <el-icon :size="16" class="tf-hd-icon"><Aim /></el-icon>
        <span class="tf-hd-title">避空污染量化</span>
        <div class="tf-hd-right">
          <span class="tf-pill">走廊均暴露: {{ corridorIndex }}</span>
        </div>
      </div>

      <div class="tf-body">
        <div class="tf-quant-grid">
          <div v-for="item in avoidanceItems" :key="item.label" class="tf-softbox tf-quant">
            <span class="tf-quant-label">{{ item.label }}</span>
            <span class="tf-quant-value tf-mono" :class="item.tone">{{ item.value }}</span>
          </div>
        </div>
      </div>

      <div class="tf-foot">
        <span class="tf-foot-text">{{ avoidanceNote }}</span>
        <span class="tf-foot-right tf-pill" :class="detourPill.cls">{{ detourPill.text }}</span>
      </div>
    </section>

    <!-- ④ 出行时段吸入趋势 -->
    <section class="tf-card">
      <div class="tf-hd">
        <el-icon :size="16" class="tf-hd-icon"><Clock /></el-icon>
        <span class="tf-hd-title">出行时段吸入趋势</span>
        <div class="tf-hd-right">
          <span v-if="curve" class="tf-pill is-green">优选 {{ hourLabel(curve.best_window) }}</span>
        </div>
      </div>

      <div class="tf-body">
        <div v-if="curve" class="tf-hour-grid">
          <div v-for="item in hourBars" :key="item.hour" class="tf-hour">
            <span class="tf-hour-value tf-mono" :style="{ color: item.color }">{{ item.value }}</span>
            <div class="tf-hour-track">
              <div
                class="tf-hour-fill"
                :style="{ height: `${item.height}%`, background: item.color }"
              ></div>
            </div>
            <span class="tf-hour-label" :class="{ 'is-best': item.isBest }">{{ item.hour }}</span>
          </div>
        </div>
        <p v-else class="tf-empty">本次未建立暴露场，无法给出出发时段的吸入差异。</p>
      </div>

      <div class="tf-foot">
        <span class="tf-foot-text">峰谷降幅 −{{ curve ? curve.intake_range.reduction_pct.toFixed(0) : '—' }}%</span>
        <span class="tf-foot-right tf-tone-muted">
          弹性窗口 ±{{ result.departure_advice.time_flexibility_minutes }} min
        </span>
      </div>
    </section>

    <!-- ⑤ 稳健性与防护装备 -->
    <section class="tf-card">
      <div class="tf-hd">
        <el-icon :size="16" class="tf-hd-icon is-green"><Umbrella /></el-icon>
        <span class="tf-hd-title">稳健性与防护装备</span>
        <div class="tf-hd-right">
          <span class="tf-pill" :class="stabilityPillClass">{{ stabilityText }}</span>
        </div>
      </div>

      <div class="tf-body">
        <template v-if="uncertainty">
          <div class="tf-stab-row">
            <span class="tf-label">风向 / 速率扰动（{{ uncertainty.samples }} 次测试）</span>
            <span class="tf-stab-rate tf-tone-green tf-mono">
              第一优选择 {{ (uncertainty.recommended_share * 100).toFixed(0) }}%
            </span>
          </div>
          <div class="tf-bar tf-stab-bar">
            <div
              class="tf-bar-fill"
              :style="{
                width: `${uncertainty.recommended_share * 100}%`,
                background: stabilityColor
              }"
            ></div>
          </div>
          <div class="tf-gear">
            <span class="tf-gear-dot"></span>
            <span class="tf-gear-text">{{ gearText }}</span>
            <span class="tf-gear-extra">{{ gearExtra }}</span>
          </div>
          <div class="tf-stab-list">
            <div v-for="item in uncertainty.top_pick_distribution" :key="item.route_id" class="tf-stab-item">
              <span>{{ item.mode_label }}</span>
              <span class="tf-mono">{{ (item.share * 100).toFixed(0) }}%</span>
            </div>
          </div>
        </template>
        <p v-else class="tf-empty">未执行扰动测试，结论稳健性不可用。</p>
      </div>

      <div class="tf-foot">
        <span class="tf-foot-text">
          {{ uncertainty ? `蒙特卡洛敏感性分析通过（置信等级：${uncertainty.level_label}）` : '—' }}
        </span>
        <el-popover v-if="uncertainty" placement="top" :width="280" trigger="click">
          <template #reference>
            <span class="tf-foot-right tf-link">参数详情 →</span>
          </template>
          <div class="tf-pop">
            <div class="tf-pop-row">
              <span>风速扰动</span>
              <span class="tf-mono">±{{ (uncertainty.perturbations.wind_speed_rel_sigma * 100).toFixed(0) }}%</span>
            </div>
            <div class="tf-pop-row">
              <span>风向扰动</span>
              <span class="tf-mono">±{{ uncertainty.perturbations.wind_direction_sigma_deg.toFixed(0) }}°</span>
            </div>
            <div class="tf-pop-row">
              <span>吸入交换率扰动</span>
              <span class="tf-mono">±{{ (uncertainty.perturbations.exchange_rel_sigma * 100).toFixed(0) }}%</span>
            </div>
            <div class="tf-pop-row">
              <span>扰动次数</span>
              <span class="tf-mono">{{ uncertainty.samples }} 次</span>
            </div>
            <p class="tf-pop-note">{{ uncertainty.note }}</p>
          </div>
        </el-popover>
      </div>
    </section>

    <!-- ⑥ 模型核心边界 -->
    <section class="tf-card">
      <div class="tf-hd">
        <el-icon :size="16" class="tf-hd-icon"><InfoFilled /></el-icon>
        <span class="tf-hd-title">模型核心边界</span>
        <div class="tf-hd-right">
          <span class="tf-kernel tf-mono">Kernel v2.4</span>
        </div>
      </div>

      <div class="tf-body">
        <div class="tf-bound-grid">
          <div v-for="item in boundaryItems" :key="item.label" class="tf-softbox tf-bound">
            <span class="tf-bound-label">{{ item.label }}</span>
            <span class="tf-bound-value">{{ item.value }}</span>
          </div>
        </div>
      </div>

      <div class="tf-foot">
        <span class="tf-foot-text tf-mono">暴露场采样 {{ sampleCount }} 点</span>
        <span class="tf-foot-right tf-tone-muted tf-mono">
          暴露源 {{ sourceCount }} 个 · 走廊 {{ corridorCount }} 段
        </span>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Aim, CircleCheck, Clock, InfoFilled, TrendCharts, Umbrella } from '@element-plus/icons-vue'
import {
  bearingLabel,
  doseReductionPct,
  hourLabel,
  preferredRouteNote,
  severityLabel,
  severityTone
} from '@/utils/travelMapper'
import type {
  TravelPollutionField,
  TravelResult,
  TravelRouteOption,
  TravelSeverity
} from '@/types/travel'

/**
 * TravelInsightCards - 出行页下半部分的六张分析卡
 *
 * 三张一组铺两行：第一行回答"这趟走得顺不顺、该选哪种方式、有没有避开污染"，
 * 第二行回答"什么时候走、结论稳不稳、模型边界在哪"。
 */

interface Props {
  result: TravelResult
  pollutionField: TravelPollutionField | null
}

const props = defineProps<Props>()

/** 推荐方案本身，多处复用 */
const recommended = computed<TravelRouteOption | null>(
  () =>
    props.result.route_options.find(item => item.route_id === props.result.recommendation.route_id) ??
    props.result.route_options.find(item => item.recommended) ??
    null
)

// ── ① 出行条件评估 ──────────────────────────────────────────────────────

const TONE_COLORS: Record<'red' | 'amber' | 'green', string> = {
  red: '#e04a5f',
  amber: '#e8a020',
  green: '#12a06b'
}

const CONDITION_PILL: Record<string, string> = {
  good: 'is-green',
  fair: 'is-amber',
  poor: 'is-red',
  severe: 'is-red'
}

const conditionPillClass = computed(() => CONDITION_PILL[props.result.travel_condition.level] ?? '')

/** 四个影响因子：降水与风阻合并成一条，避免与"风力"重复占位 */
const factorItems = computed(() => {
  const severity: TravelSeverity = props.result.personalized_severity
  const rainWind = Math.max(severity.rain ?? 0, severity.wind ?? 0)
  const rows: Array<{ label: string; value: number }> = [
    { label: '空气质量约束', value: severity.air_quality ?? 0 },
    { label: '湿度负荷', value: severity.humidity ?? 0 },
    { label: '高温因子', value: severity.heat ?? 0 },
    { label: '降水 / 风阻', value: rainWind }
  ]
  return rows.map(row => {
    const tone = severityTone(row.value)
    return {
      ...row,
      read: severityLabel(row.value),
      toneCls: `tf-tone-${tone}`,
      color: TONE_COLORS[tone]
    }
  })
})

const fitPct = computed(() => Math.round(recommended.value?.score ?? 0))

// ── ② 推荐决策对比 ──────────────────────────────────────────────────────

const modeCards = computed(() => {
  const entries = Object.entries(props.result.recommendation.alternative_by_mode)
  const rows = entries.map(([mode, summary]) => ({
    mode,
    label: summary.mode_label,
    utility: summary.utility_minutes,
    intake: summary.intake_index,
    isBest: summary.route_id === props.result.recommendation.route_id
  }))
  rows.sort((a, b) => (a.isBest ? -1 : b.isBest ? 1 : a.utility - b.utility))
  const worstIntake = rows.reduce((peak, row) => Math.max(peak, row.intake), 0)
  return rows.map(row => ({
    ...row,
    tone: row.isBest ? 'best' : row.intake === worstIntake && rows.length > 1 ? 'worst' : 'plain'
  }))
})

const confidenceText = computed(() => {
  const uncertainty = props.result.uncertainty
  if (!uncertainty) return '置信度: —'
  return `置信度: ${uncertainty.level_label} (${(uncertainty.recommended_share * 100).toFixed(1)}%)`
})

const confidencePillClass = computed(() => {
  const level = props.result.uncertainty?.level
  if (level === 'high') return 'is-green'
  if (level === 'medium') return 'is-amber'
  return level ? 'is-red' : ''
})

/** 推荐方案相对次优方案省下的等效时间 */
const utilityDiffText = computed(() => {
  const rows = modeCards.value
  const best = rows.find(row => row.isBest)
  const runnerUp = rows.find(row => !row.isBest)
  if (!best) return '暂无可比方案'
  if (!runnerUp) return '仅一种可行方式'
  const diff = runnerUp.utility - best.utility
  if (diff <= 0.5) return `与「${runnerUp.label}」等效耗时基本持平`
  return `等效耗时节省 ${diff.toFixed(0)} min（vs ${runnerUp.label}）`
})

// ── ③ 避空污染量化 ──────────────────────────────────────────────────────

const corridorIndex = computed(
  () => recommended.value?.exposure.mean_index.toFixed(3) ?? '—'
)

const avoidanceItems = computed(() => {
  const route = recommended.value
  const cut = route ? doseReductionPct(props.result.route_options, route.route_id) : 0
  const avoidance = props.result.pollution_advice?.avoidance
  return [
    {
      label: '吸入降幅',
      value: `−${cut.toFixed(0)}%`,
      tone: cut > 0 ? 'tf-tone-green' : ''
    },
    {
      label: '高暴露绕行',
      value: `${(avoidance?.high_exposure_km_avoided ?? 0).toFixed(2)} km`,
      tone: ''
    },
    {
      label: '额外里程',
      value: `+${(avoidance?.extra_distance_km ?? 0).toFixed(2)}`,
      tone: ''
    },
    {
      label: '额外时间',
      value: `+${(avoidance?.extra_minutes ?? 0).toFixed(0)} min`,
      tone: ''
    }
  ]
})

/** 避污结论：没有暴露场就直说，不编结论 */
const avoidanceNote = computed(() => {
  const advice = props.result.pollution_advice
  if (!props.pollutionField || advice?.available === false) {
    return '本次未建立污染暴露场，避污结论不可用'
  }
  const transport = bearingLabel(props.pollutionField?.transport_bearing)
  const extra = advice?.avoidance.extra_distance_km ?? 0
  if (extra <= 0.005) return `最优解天然避开 ${transport} 向烟羽`
  return `为避开 ${transport} 向烟羽，多走 ${extra.toFixed(2)} km`
})

const detourPill = computed(() => {
  const advice = props.result.pollution_advice
  if (!props.pollutionField || advice?.available === false) {
    return { text: '未建模', cls: '' }
  }
  const extra = advice?.avoidance.extra_distance_km ?? 0
  if (extra <= 0.005) return { text: '无需绕行', cls: 'is-green' }
  return { text: `已绕行 +${extra.toFixed(2)} km`, cls: 'is-amber' }
})

// ── ④ 出行时段吸入趋势 ──────────────────────────────────────────────────

const curve = computed(() =>
  props.result.departure_dose_curve?.available ? props.result.departure_dose_curve : null
)

/** 柱高按本次曲线内最大值归一；颜色按相对水平分档 */
const hourBars = computed(() => {
  const points = curve.value?.points ?? []
  if (!points.length) return []
  const peak = points.reduce((max, item) => Math.max(max, item.intake_index), 0) || 1
  return points.map(item => {
    const ratio = item.intake_index / peak
    const color = ratio >= 0.85 ? '#ef6272' : ratio < 0.5 ? '#12a06b' : '#edb32a'
    return {
      hour: hourLabel(item.time),
      value: item.intake_index.toFixed(2),
      height: Math.max(8, ratio * 100),
      color,
      isBest: item.time === curve.value?.best_window
    }
  })
})

// ── ⑤ 稳健性与防护装备 ──────────────────────────────────────────────────

const uncertainty = computed(() => props.result.uncertainty)

const stabilityColor = computed(() => {
  const level = props.result.uncertainty?.level
  if (level === 'high') return '#12a06b'
  if (level === 'medium') return '#e8a020'
  return '#e04a5f'
})

const stabilityPillClass = computed(() => {
  const level = props.result.uncertainty?.level
  if (level === 'high') return 'is-green'
  if (level === 'medium') return 'is-amber'
  return level ? 'is-red' : ''
})

const stabilityText = computed(() => {
  const item = props.result.uncertainty
  if (!item) return '未测试'
  return `${(item.recommended_share * 100).toFixed(0)}% ${item.level_label}`
})

const gearText = computed(
  () => props.result.gear_reminders[0] ?? '本次无需额外防护装备'
)

const gearExtra = computed(() => {
  const second = props.result.gear_reminders[1]
  if (second) return second
  const mode = props.result.recommendation.mode
  return mode === 'walk' ? '户外步行建议佩戴口罩' : '车厢内建议保持通风'
})

// ── ⑥ 模型核心边界 ──────────────────────────────────────────────────────

const boundaryItems = computed(() => {
  const field = props.pollutionField
  let dispersion = '高斯风向过渡模型'
  if (!field) dispersion = '未建立暴露场'
  else if (field.calm) dispersion = '静风各向同性积聚'
  else if (field.mixed_regime) dispersion = '高斯烟羽与积聚过渡'

  return [
    { label: '路网来源', value: '香港道路中心线' },
    { label: '扩散算法', value: dispersion },
    { label: '吸入折算', value: '动态呼吸速率当量' },
    { label: '实时工况', value: '微气象静态风场' }
  ]
})

const sampleCount = computed(
  () => props.pollutionField?.grid?.length ?? props.pollutionField?.source_count ?? 0
)

const sourceCount = computed(() => props.pollutionField?.source_count ?? 0)
const corridorCount = computed(() => props.pollutionField?.corridor_source_count ?? 0)
</script>

<style scoped>
.tf-insights {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.tf-hd-icon.is-green { color: var(--tf-green); }

.tf-dot {
  width: 6px;
  height: 6px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #c7ccd4;
}

.tf-dot.is-green { background: var(--tf-green); }

.tf-empty {
  margin: 0;
  padding: 18px 0;
  text-align: center;
  font-size: 12px;
  color: var(--tf-text-3);
}

.tf-link-text {
  color: var(--tf-blue);
}

/* ① 出行条件评估 */
.tf-factor-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.tf-factor {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 9px 11px;
}

.tf-factor-top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.tf-factor-label {
  font-size: 12px;
  color: var(--tf-text-3);
  white-space: nowrap;
}

.tf-factor-read {
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.tf-factor-read em {
  font-style: normal;
  font-size: 11px;
  font-weight: 500;
  margin-left: 3px;
}

/* ② 推荐决策对比 */
.tf-mode-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.tf-mode {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 4px;
  padding: 12px 6px;
  border: 1px solid var(--tf-border);
  border-radius: var(--tf-radius-sm);
  background: var(--tf-card);
}

.tf-mode.is-best {
  border-color: #bfe7d5;
  background: #f2fbf7;
}

.tf-mode.is-worst {
  border-color: #f6dee3;
  background: #fef5f6;
}

.tf-mode-name {
  font-size: 12px;
  color: var(--tf-text-2);
  font-weight: 600;
  white-space: nowrap;
}

.tf-mode-name em {
  font-style: normal;
  font-size: 11px;
  color: var(--tf-green);
  font-weight: 500;
}

.tf-mode-value {
  font-size: 19px;
  font-weight: 700;
  line-height: 1.1;
  color: var(--tf-text);
}

.tf-mode.is-best .tf-mode-value { color: var(--tf-green); }
.tf-mode.is-worst .tf-mode-value { color: var(--tf-red); }

.tf-mode-value em {
  font-style: normal;
  font-size: 11px;
  font-weight: 500;
  color: var(--tf-text-3);
  margin-left: 2px;
}

.tf-mode-sub {
  font-size: 11px;
  color: var(--tf-text-3);
}

/* ③ 避空污染量化 */
.tf-quant-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
}

.tf-quant {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 5px;
  padding: 14px 6px;
}

.tf-quant-label {
  font-size: 11px;
  color: var(--tf-text-3);
  white-space: nowrap;
}

.tf-quant-value {
  font-size: 17px;
  font-weight: 700;
  color: var(--tf-text);
}

/* ④ 出行时段吸入趋势 */
.tf-hour-grid {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  height: 118px;
}

.tf-hour {
  flex: 1;
  min-width: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.tf-hour-value {
  font-size: 12px;
  font-weight: 600;
}

.tf-hour-track {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: flex-end;
}

.tf-hour-fill {
  width: 100%;
  border-radius: 3px 3px 0 0;
  transition: height 0.3s;
}

.tf-hour-label {
  font-size: 11px;
  color: var(--tf-text-3);
}

.tf-hour-label.is-best {
  color: var(--tf-text);
  font-weight: 700;
}

/* ⑤ 稳健性与防护装备 */
.tf-stab-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.tf-stab-rate {
  font-size: 13px;
  font-weight: 700;
  white-space: nowrap;
}

.tf-stab-bar {
  margin-top: 7px;
}

.tf-gear {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 12px;
  padding: 9px 11px;
  background: var(--tf-soft);
  border-radius: var(--tf-radius-sm);
}

.tf-gear-dot {
  width: 7px;
  height: 7px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #e8a020;
}

.tf-gear-text {
  min-width: 0;
  font-size: 12px;
  color: var(--tf-text);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.tf-gear-extra {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 11px;
  color: var(--tf-text-3);
}

.tf-stab-list {
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-top: 12px;
}

.tf-stab-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: var(--tf-text-2);
}

.tf-pop-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: var(--tf-text-2);
  padding: 3px 0;
}

.tf-pop-note {
  margin: 8px 0 0;
  font-size: 11px;
  line-height: 17px;
  color: var(--tf-text-3);
}

/* ⑥ 模型核心边界 */
.tf-kernel {
  font-size: 12px;
  color: var(--tf-text-3);
}

.tf-bound-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.tf-bound {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 11px 12px;
}

.tf-bound-label {
  font-size: 11px;
  color: var(--tf-text-3);
}

.tf-bound-value {
  font-size: 13px;
  font-weight: 600;
  color: var(--tf-text);
}

@media (max-width: 1280px) {
  .tf-insights {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 860px) {
  .tf-insights {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
