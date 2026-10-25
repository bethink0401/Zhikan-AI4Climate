<template>
  <div class="prediction-evidence-panel">
    <!-- 标题 -->
    <div class="panel-header">
      <span class="panel-title">🔍 预测依据</span>
      <el-tag v-if="hasPrediction" size="small" type="success" effect="plain">
        可解释
      </el-tag>
    </div>

    <!-- 空状态 -->
    <div v-if="!hasPrediction" class="empty-state">
      <el-empty :description="emptyHint" />
    </div>

    <div v-else class="panel-body">
      <!-- ===== ① 方法说明：让读者知道"这是怎么算出来的" ===== -->
      <section class="block method-block">
        <div class="block-title">预测方法</div>
        <div class="method-row">
          <span class="method-label">风场外推</span>
          <span class="method-value">{{ methodLabel }}</span>
        </div>
        <div class="method-row">
          <span class="method-label">路径推演</span>
          <span class="method-value">{{ pathMethodLabel }}</span>
        </div>
        <div class="method-row">
          <span class="method-label">气团衰减</span>
          <span class="method-value">{{ lifetimeAvailable ? '温度、湿度、气压与污染物浓度共同计算' : '污染物初值不足，仅展示风场路径' }}</span>
        </div>
        <div class="method-note">
          {{ methodExplainer }}
        </div>
        <div v-if="lifetimeAvailable" class="method-note">
          起算气温 {{ formatNumber(lifetime?.weather_inputs.temperature, 1) }} °C ·
          湿度 {{ formatNumber(lifetime?.weather_inputs.humidity, 1) }}% ·
          地面气压 {{ formatNumber(lifetime?.weather_inputs.pressure_hpa, 1) }} hPa。
          {{ lifetimeAssumptionNote }}
          修正系数以既有 {{ lifetime?.reference_half_life_hours }} 小时参考半衰期初始化，待实测标定。
        </div>
      </section>

      <!-- ===== ② 关键依据量：风向集中度 / 转向速率 / 置信度 ===== -->
      <section class="block">
        <div class="block-title">关键依据量</div>
        <div class="metric-grid">
          <div class="metric-item">
            <span class="metric-label">风向集中度</span>
            <span class="metric-value" :style="{ color: resultantColor }">
              {{ formatRatio(forecast?.resultant_length) }}
            </span>
            <el-progress
              :percentage="Math.round((forecast?.resultant_length ?? 0) * 100)"
              :color="resultantColor"
              :show-text="false"
              :stroke-width="6"
            />
            <span class="metric-hint">{{ resultantHint }}</span>
          </div>

          <div class="metric-item">
            <span class="metric-label">转向速率</span>
            <span class="metric-value">{{ formatTurnRate(forecast?.turn_rate_deg_per_hour) }}</span>
            <span class="metric-hint">{{ turnRateHint }}</span>
          </div>

          <div class="metric-item">
            <span class="metric-label">预测置信度</span>
            <span class="metric-value" :style="{ color: confidenceColor }">
              {{ formatRatio(forecast?.confidence) }}
            </span>
            <el-progress
              :percentage="Math.round((forecast?.confidence ?? 0) * 100)"
              :color="confidenceColor"
              :show-text="false"
              :stroke-width="6"
            />
            <span class="metric-hint">由风向集中度与有效样本数共同决定</span>
          </div>

          <div class="metric-item">
            <span class="metric-label">有效回看小时</span>
            <span class="metric-value">{{ forecast?.lookback_hours_used ?? '—' }} h</span>
            <span class="metric-hint">参与圆统计的观测条数</span>
          </div>
        </div>
      </section>

      <!-- ===== ③ 逐小时风向预测表（预测路径的绑定依据） ===== -->
      <section class="block">
        <div class="block-title">逐小时风向预测</div>
        <div class="table-wrapper">
          <table class="forecast-table">
            <thead>
              <tr>
                <th>时效</th>
                <th>风来向</th>
                <th>风去向</th>
                <th>风速</th>
                <th>方位不确定度</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="hour in forecastHours" :key="hour.lead_hour">
                <td class="cell-strong">+{{ hour.lead_hour }} h</td>
                <td>
                  <span class="dir-badge">
                    {{ formatDegree(hour.wind_from_direction) }}
                  </span>
                  <span class="dir-label">{{ compassOf(hour.wind_from_direction) }}</span>
                </td>
                <td>
                  <span class="dir-badge is-to">
                    {{ formatDegree(hour.wind_to_direction) }}
                  </span>
                  <span class="dir-label">{{ compassOf(hour.wind_to_direction) }}</span>
                </td>
                <td>{{ formatNumber(hour.wind_speed_ms, 2) }} m/s</td>
                <td>
                  <span class="uncertainty-value">
                    ±{{ formatNumber(hour.direction_uncertainty_deg, 1) }}°
                  </span>
                  <div class="uncertainty-bar">
                    <div
                      class="uncertainty-fill"
                      :style="{ width: uncertaintyWidth(hour.direction_uncertainty_deg) }"
                    ></div>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="table-note">
          风去向 = 风来向 + 180°，即污染物实际被输送的方位；不确定度随预报时效递增。
        </div>
      </section>

      <!-- ===== ④ 路径推导链：每一步的距离与衰减 ===== -->
      <section class="block">
        <div class="block-title">路径推导链（逐小时推进）</div>
        <ol class="chain-list">
          <li v-for="step in steps" :key="step.lead_hour" class="chain-item">
            <span class="chain-dot">{{ step.lead_hour }}</span>
            <div class="chain-content">
              <div class="chain-head">
                <span class="chain-title">第 {{ step.lead_hour }} 小时末</span>
                <span class="chain-distance">
                  累计 {{ formatNumber(step.distance_from_origin_km, 2) }} km
                </span>
              </div>
              <div class="chain-meta">
                <span>本小时推进 {{ formatNumber(step.distance_this_hour_km, 2) }} km</span>
                <span>·</span>
                <span>扩散半宽 {{ formatNumber(step.corridor_half_width_km, 2) }} km</span>
                <span>·</span>
                <span>气团剩余 {{ formatPercent(step.remaining_fraction) }}</span>
              </div>
              <div v-if="step.remaining_by_pollutant" class="chain-meta">
                <span v-for="(parcel, name) in step.remaining_by_pollutant" :key="name">
                  {{ pollutantLabel(String(name)) }} {{ formatPercent(parcel.remaining_fraction) }}
                </span>
              </div>
            </div>
          </li>
        </ol>
        <div class="table-note">
          距离按起止小时风矢量平均后积分。各污染物分别更新寿命；气团剩余取最慢衰减者，未将不同单位浓度相加。
        </div>
      </section>

      <!-- ===== ⑤ 建模假设与边界（必须展示，避免被误读） ===== -->
      <section v-if="assumptions.length > 0" class="block assumptions-block">
        <div class="block-title">建模假设与适用边界</div>
        <ul class="assumption-list">
          <li v-for="(item, index) in assumptions" :key="index" class="assumption-item">
            {{ item }}
          </li>
        </ul>
      </section>

      <!-- ===== ⑥ 不可用时的原因说明 ===== -->
      <el-alert
        v-if="unavailableReason"
        class="reason-alert"
        type="info"
        :closable="false"
        show-icon
        :title="unavailableReason"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { MeteorologyResult, WindDirectionForecast, WindForecastHour } from '@/types/meteorology'

defineOptions({ name: 'PredictionEvidencePanel' })

// ---------- 1. Props ----------
const props = defineProps<{
  /** 气象结果（含 wind_direction_forecast 与 pollution_path_prediction） */
  meteorology: MeteorologyResult | null
}>()

// ---------- 2. 数据取用 ----------
const forecast = computed<WindDirectionForecast | null>(
  () => props.meteorology?.wind_direction_forecast ?? null
)

const forecastHours = computed<WindForecastHour[]>(() => {
  const hours = forecast.value?.forecast_hours ?? []
  return [...hours].sort((a, b) => a.lead_hour - b.lead_hour)
})

const steps = computed(() => {
  const prediction = props.meteorology?.pollution_path_prediction
  if (prediction?.status !== 'ok') return []
  return [...(prediction.steps ?? [])].sort((a, b) => a.lead_hour - b.lead_hour)
})

const assumptions = computed(
  () => props.meteorology?.pollution_path_prediction?.assumptions ?? []
)
const lifetime = computed(() => props.meteorology?.pollution_path_prediction?.lifetime_model)
const lifetimeAvailable = computed(() => !!lifetime.value && lifetime.value.status !== 'unavailable')
const lifetimeAssumptionNote = computed(() => {
  const labels: Record<string, string> = { temperature: '温度', humidity: '湿度', pressure_hpa: '气压' }
  const missing = lifetime.value?.assumed_inputs ?? []
  return missing.length ? `${missing.map(key => labels[key] || key).join('、')}缺测，采用参考状态。` : ''
})
function pollutantLabel(name: string): string {
  return ({ pm25: 'PM2.5', pm10: 'PM10', no2: '二氧化氮', o3: '臭氧', so2: '二氧化硫', co: '一氧化碳' } as Record<string, string>)[name] || name
}

/** 只要有风场预测就算有依据可讲；路径不可用时仍展示风场依据 */
const hasPrediction = computed(
  () => !!forecast.value && forecast.value.forecast_hours.length > 0
)

const unavailableReason = computed(() => {
  const prediction = props.meteorology?.pollution_path_prediction
  if (prediction?.status === 'ok') return ''
  if (forecast.value?.status === 'calm') {
    return '预测风速低于定向输送阈值（< 0.5 m/s），按弱风处理，不绘制传播路径以免误导。'
  }
  return prediction?.reason || forecast.value?.reason || ''
})

const emptyHint = computed(() =>
  props.meteorology ? '本次结果未包含风场预测数据' : '暂无气象数据'
)

// ---------- 3. 方法名翻译 ----------
const methodLabel = computed(() => {
  const method = forecast.value?.method
  if (method === 'recency_weighted_circular_persistence') return '近期加权圆统计持续外推'
  return method || '—'
})

const pathMethodLabel = computed(() => {
  const method = props.meteorology?.pollution_path_prediction?.method
  if (method === 'predictor_corrector_lagrangian_advection') return '预测—校正拉格朗日轨迹'
  if (method === 'forward_lagrangian_advection') return '前向拉格朗日平流'
  return method || '—'
})

const methodExplainer = computed(
  () =>
    '不使用黑箱模型：对近期观测做指数加权圆平均得到主导来向，再由前后半窗差异外推有限转向速率；' +
    '路径按起止小时风矢量进行预测—校正积分；各污染物的气团寿命按论文公式逐小时更新。'
)

// ---------- 4. 颜色与提示 ----------
const resultantColor = computed(() => {
  const value = forecast.value?.resultant_length ?? 0
  if (value >= 0.8) return '#67c23a'
  if (value >= 0.5) return '#e6a23c'
  return '#f56c6c'
})

const resultantHint = computed(() => {
  const value = forecast.value?.resultant_length ?? 0
  if (value >= 0.8) return '风向高度集中，输送方向稳定'
  if (value >= 0.5) return '风向较集中，输送方向基本可信'
  return '风向离散，输送方向不确定性较高'
})

const confidenceColor = computed(() => {
  const value = forecast.value?.confidence ?? 0
  if (value >= 0.7) return '#67c23a'
  if (value >= 0.4) return '#e6a23c'
  return '#f56c6c'
})

const turnRateHint = computed(() => {
  const rate = forecast.value?.turn_rate_deg_per_hour
  if (rate === undefined || rate === null) return '—'
  if (Math.abs(rate) < 1) return '风向基本稳定'
  return rate > 0 ? '顺时针缓慢转向' : '逆时针缓慢转向'
})

// ---------- 5. 格式化 ----------
const COMPASS_16 = [
  '北', '北东北', '东北', '东东北',
  '东', '东东南', '东南', '南东南',
  '南', '南西南', '西南', '西西南',
  '西', '西西北', '西北', '北西北'
]

/** 角度 → 十六方位中文简称 */
function compassOf(degree: number | null | undefined): string {
  const num = Number(degree)
  if (!Number.isFinite(num)) return ''
  const index = Math.round((((num % 360) + 360) % 360) / 22.5) % 16
  return COMPASS_16[index]
}

function formatDegree(value: number | null | undefined): string {
  const num = Number(value)
  return Number.isFinite(num) ? `${num.toFixed(1)}°` : '—'
}

function formatNumber(value: number | null | undefined, digits: number): string {
  const num = Number(value)
  return Number.isFinite(num) ? num.toFixed(digits) : '—'
}

function formatRatio(value: number | null | undefined): string {
  const num = Number(value)
  return Number.isFinite(num) ? `${(num * 100).toFixed(0)}%` : '—'
}

function formatPercent(value: number | null | undefined): string {
  const num = Number(value)
  return Number.isFinite(num) ? `${(num * 100).toFixed(1)}%` : '—'
}

function formatTurnRate(value: number | null | undefined): string {
  const num = Number(value)
  if (!Number.isFinite(num)) return '—'
  const sign = num > 0 ? '+' : ''
  return `${sign}${num.toFixed(2)}°/h`
}

/** 不确定度条宽度：以 90° 为满格 */
function uncertaintyWidth(degree: number | null | undefined): string {
  const num = Number(degree)
  if (!Number.isFinite(num)) return '0%'
  return `${Math.min(100, (num / 90) * 100).toFixed(1)}%`
}
</script>

<style scoped>
.prediction-evidence-panel {
  background: var(--panel-bg);
  border-radius: 10px;
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}

/* ===== 头部 ===== */
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border-color);
}

.panel-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.empty-state {
  padding: 24px 0;
}

.panel-body {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 16px;
}

/* ===== 区块 ===== */
.block {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.block-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  padding-left: 8px;
  border-left: 3px solid var(--theme-color);
  line-height: 1.2;
}

/* ===== 方法说明 ===== */
.method-block {
  padding: 12px;
  background: var(--bg-page);
  border-radius: 8px;
  border: 1px solid var(--border-color);
}

.method-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.method-label {
  color: var(--text-tertiary);
  min-width: 64px;
}

.method-value {
  color: var(--text-primary);
  font-weight: 500;
}

.method-note {
  font-size: 12px;
  line-height: 18px;
  color: var(--text-secondary);
  padding-top: 6px;
  border-top: 1px dashed var(--border-color);
}

/* ===== 关键依据量 ===== */
.metric-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.metric-item {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 10px 12px;
  background: var(--bg-page);
  border-radius: 8px;
  border: 1px solid var(--border-color);
}

.metric-label {
  font-size: 12px;
  color: var(--text-tertiary);
}

.metric-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-primary);
  line-height: 1.2;
}

.metric-hint {
  font-size: 11px;
  color: var(--text-tertiary);
  line-height: 15px;
}

/* ===== 表格 ===== */
.table-wrapper {
  overflow-x: auto;
  border: 1px solid var(--border-color);
  border-radius: 8px;
}

.forecast-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}

.forecast-table th {
  padding: 8px 10px;
  text-align: left;
  font-weight: 600;
  color: var(--text-secondary);
  background: var(--bg-page);
  border-bottom: 1px solid var(--border-color);
  white-space: nowrap;
}

.forecast-table td {
  padding: 9px 10px;
  color: var(--text-primary);
  border-bottom: 1px solid var(--border-color);
  vertical-align: middle;
}

.forecast-table tbody tr:last-child td {
  border-bottom: none;
}

.cell-strong {
  font-weight: 600;
  color: var(--theme-color);
  white-space: nowrap;
}

.dir-badge {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(64, 132, 224, 0.12);
  color: #2d6ecd;
  font-weight: 600;
  font-size: 11px;
}

.dir-badge.is-to {
  background: rgba(255, 87, 34, 0.12);
  color: #e64a19;
}

.dir-label {
  margin-left: 4px;
  color: var(--text-tertiary);
  font-size: 11px;
}

.uncertainty-value {
  display: block;
  font-size: 11px;
  color: var(--text-secondary);
  margin-bottom: 3px;
}

.uncertainty-bar {
  width: 100%;
  min-width: 54px;
  height: 4px;
  background: var(--border-color);
  border-radius: 2px;
  overflow: hidden;
}

.uncertainty-fill {
  height: 100%;
  background: linear-gradient(90deg, #67c23a, #e6a23c, #f56c6c);
  border-radius: 2px;
}

.table-note {
  font-size: 11px;
  line-height: 16px;
  color: var(--text-tertiary);
}

/* ===== 推导链 ===== */
.chain-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chain-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 9px 10px;
  background: var(--bg-page);
  border-radius: 8px;
  border-left: 3px solid #2d6ecd;
}

.chain-dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #2d6ecd;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
}

.chain-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.chain-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.chain-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-primary);
}

.chain-distance {
  font-size: 12px;
  font-weight: 600;
  color: #2d6ecd;
  white-space: nowrap;
}

.chain-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  font-size: 11px;
  color: var(--text-tertiary);
}

/* ===== 假设 ===== */
.assumptions-block {
  padding: 12px;
  background: rgba(230, 162, 60, 0.08);
  border-radius: 8px;
  border: 1px solid rgba(230, 162, 60, 0.35);
}

.assumption-list {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.assumption-item {
  font-size: 12px;
  line-height: 17px;
  color: var(--text-secondary);
}

.reason-alert {
  margin-top: 2px;
}

/* ===== 响应式 ===== */
@media (max-width: 1200px) {
  .metric-grid {
    grid-template-columns: 1fr;
  }
}
</style>
