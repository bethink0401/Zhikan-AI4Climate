<template>
  <section class="tf-card tf-audit">
    <div class="tf-hd">
      <el-icon :size="16" class="tf-hd-icon"><DataBoard /></el-icon>
      <span class="tf-hd-title">算法推理审计轨迹（Audit Trail）</span>
      <span class="tf-pill" :class="weatherPill.cls">{{ weatherPill.text }}</span>
      <div class="tf-hd-right">
        <span class="tf-audit-meta tf-mono">
          采样源: {{ sampleCount }} 点 · 决策置信度: {{ (confidence * 100).toFixed(0) }}%
        </span>
        <span class="tf-link" @click="logVisible = true">查看完整日志</span>
      </div>
    </div>

    <div class="tf-body tf-audit-body">
      <div class="tf-audit-grid">
        <div v-for="item in blocks" :key="item.label" class="tf-softbox tf-audit-cell">
          <span class="tf-audit-label">{{ item.label }}</span>
          <span class="tf-audit-value">{{ item.value }}</span>
          <span class="tf-audit-note">{{ item.note }}</span>
        </div>
      </div>
    </div>

    <el-dialog v-model="logVisible" title="算法推理完整日志" width="720px" top="8vh">
      <div class="tf-log">
        <div class="tf-log-meta">
          <span>Agent {{ agentName }}</span>
          <span>置信度 {{ (confidence * 100).toFixed(0) }}%</span>
          <span v-if="dataQuality?.meteorology_source">
            气象来源 {{ dataQuality.meteorology_source }}
          </span>
          <span v-if="dataQuality">数据缺失率 {{ (dataQuality.missing_rate * 100).toFixed(1) }}%</span>
        </div>

        <h4 class="tf-log-h">推理证据链</h4>
        <ol class="tf-log-list">
          <li v-for="(item, index) in evidence" :key="index">{{ item }}</li>
        </ol>

        <!-- 气象 Agent 是独立的一次调用，把它的输入与输出原样摊开 -->
        <template v-if="meteorology">
          <h4 class="tf-log-h">气象 Agent（独立调用 POST /api/meteorology/analyze）</h4>
          <div class="tf-log-meta">
            <span v-if="meteorology.request">
              采样点 {{ meteorology.request.station_name }}
              （{{ meteorology.request.lat.toFixed(5) }}, {{ meteorology.request.lon.toFixed(5) }}）
            </span>
            <span v-if="meteorology.request">
              时段 {{ meteorology.request.start_time }} – {{ meteorology.request.end_time }}
            </span>
            <span v-if="meteorology.dataQuality?.source">
              数据源 {{ meteorology.dataQuality.source }}
            </span>
            <span v-if="meteorology.confidence">
              置信度 {{ (meteorology.confidence * 100).toFixed(0) }}%
            </span>
            <span v-if="meteorology.loading">拉取中…</span>
            <span v-else-if="meteorology.error" class="tf-log-warn">{{ meteorology.error }}</span>
          </div>

          <template v-if="meteorology.result">
            <div class="tf-log-scores">
              <span>weather_risk {{ meteorology.result.weather_risk.toFixed(3) }}</span>
              <span>扩散条件 {{ meteorology.result.dispersion_condition }}</span>
              <span>静风分 {{ meteorology.result.static_wind_score.toFixed(3) }}</span>
              <span>风向突变分 {{ meteorology.result.wind_shift_score.toFixed(3) }}</span>
              <span>湿度分 {{ meteorology.result.humidity_risk_score.toFixed(3) }}</span>
              <span>清除不足分 {{ meteorology.result.insufficient_rain_removal_score.toFixed(3) }}</span>
            </div>

            <ol v-if="meteorology.evidence.length" class="tf-log-list">
              <li v-for="(item, index) in meteorology.evidence" :key="index">{{ item }}</li>
            </ol>

            <table v-if="meteorology.result.hourly_series.length" class="tf-log-table">
              <thead>
                <tr>
                  <th>时刻</th>
                  <th>气温 °C</th>
                  <th>湿度 %</th>
                  <th>风速 m/s</th>
                  <th>风向 °</th>
                  <th>降水 mm</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="point in meteorology.result.hourly_series" :key="point.time">
                  <td>{{ point.time.slice(11, 16) }}</td>
                  <td>{{ point.temperature ?? '—' }}</td>
                  <td>{{ point.humidity ?? '—' }}</td>
                  <td>{{ point.wind_speed ?? '—' }}</td>
                  <td>{{ point.wind_direction ?? '—' }}</td>
                  <td>{{ point.precipitation ?? '—' }}</td>
                </tr>
              </tbody>
            </table>
          </template>
        </template>

        <template v-if="assumptions.length">
          <h4 class="tf-log-h">模型假设与局限</h4>
          <ol class="tf-log-list">
            <li v-for="(item, index) in assumptions" :key="index">{{ item }}</li>
          </ol>
        </template>

        <p v-if="dataQuality?.note" class="tf-log-note">{{ dataQuality.note }}</p>
        <p v-if="!evidence.length" class="tf-log-note">本次运行未回传证据链。</p>
      </div>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { DataBoard } from '@element-plus/icons-vue'
import { bearingLabel, hourLabel } from '@/utils/travelMapper'
import type { MeteorologySnapshot } from '@/types/meteorology'
import type { TravelPollutionField, TravelResponse, TravelResult } from '@/types/travel'

/**
 * TravelAuditBar - 算法推理审计轨迹
 *
 * 把"结论是怎么来的"压缩成四格：气象输入、候选筛选、折算口径、时段对比。
 * 完整的证据链与模型假设收进弹窗，默认不占用版面。
 */

interface Props {
  result: TravelResult
  pollutionField: TravelPollutionField | null
  evidence: string[]
  confidence: number
  dataQuality: TravelResponse['data_quality'] | null
  /** 气象 Agent 的独立调用结果 */
  meteorology: MeteorologySnapshot | null
}

const props = defineProps<Props>()

const logVisible = ref(false)

/** 气象来源标签：接上了就直说接口与数据源，没接上也不装作有 */
const weatherPill = computed(() => {
  const snapshot = props.meteorology
  if (!snapshot) return { text: '气象未取数', cls: 'is-red' }
  if (snapshot.source === 'api') {
    const source = snapshot.dataQuality?.source ?? 'MeteorologyAgent'
    return { text: `气象直连 · ${source}`, cls: 'is-green' }
  }
  if (snapshot.source === 'embedded') {
    return { text: '气象取出行结果内嵌', cls: 'is-amber' }
  }
  return { text: '气象未取数', cls: 'is-red' }
})

const sampleCount = computed(
  () => props.pollutionField?.grid?.length ?? props.pollutionField?.source_count ?? 0
)

const agentName = computed(() => 'TravelAgent')

const assumptions = computed(() => props.result.assumptions)

/** 推荐方案，用于折算口径那一格 */
const recommended = computed(
  () =>
    props.result.route_options.find(item => item.route_id === props.result.recommendation.route_id) ??
    null
)

const blocks = computed(() => {
  const meteo = props.meteorology?.result ?? null
  const embedded = props.result.weather_summary
  const route = recommended.value
  const curve = props.result.departure_dose_curve?.available
    ? props.result.departure_dose_curve
    : null

  // 优先用气象接口直连的结果；没接上才退回出行结果内嵌的那份
  const direction = meteo?.dominant_wind_direction_label ?? embedded.dominant_wind_direction_label
  const directionDeg = meteo?.dominant_wind_direction ?? embedded.dominant_wind_direction
  const downwindDeg = meteo?.downwind_direction ?? embedded.downwind_direction
  const downwind = bearingLabel(downwindDeg)

  const windText = direction
    ? `来风 ${direction}${directionDeg !== null ? ` ${directionDeg.toFixed(0)}°` : ''} → 下风向 ${downwind}`
    : '气象输入缺失，未建立扩散方向'

  const meteoNote = meteo
    ? `weather_risk ${meteo.weather_risk.toFixed(2)} · 扩散条件${meteo.dispersion_condition} · 气象 Agent 直连`
    : props.meteorology?.source === 'embedded'
      ? '气象取出行结果内嵌（未直连气象接口）'
      : '气象数据不可用'

  const modes = new Set(props.result.route_options.map(item => item.mode)).size
  const exchange = route?.exchange_minutes_per_eu
  const pollutionAdded = route?.utility_detail.pollution_added_minutes ?? 0

  const hourCell = curve
    ? {
        value: `${hourLabel(curve.best_window)} 谷值 ${curve.intake_range.min.toFixed(2)} EU`,
        note: `较 ${hourLabel(curve.worst_window)} 峰值降低 ${curve.intake_range.reduction_pct.toFixed(0)}%`
      }
    : { value: '未建立时段曲线', note: '本次未做逐小时重构' }

  return [
    { label: '气象主导', value: windText, note: meteoNote },
    {
      label: '候选方案筛选',
      value: `${modes} 种模式 · ${props.result.route_options.length} 条候选路径`,
      note: '按等效耗时对候选升序排列'
    },
    {
      label: '推荐折算',
      value: `在途 ${route?.duration_minutes.toFixed(0) ?? '—'} min · 吸入折算 +${pollutionAdded.toFixed(1)} min`,
      note: exchange ? `1 EU ≈ ${exchange.toFixed(1)} 分钟加权` : '未回传交换率'
    },
    {
      label: '时段对比',
      value: hourCell.value,
      note: hourCell.note
    }
  ]
})
</script>

<style scoped>
.tf-audit-body {
  padding: 12px 16px;
}

.tf-audit-meta {
  font-size: 12px;
  color: var(--tf-text-3);
}

.tf-audit-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.tf-audit-cell {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 10px 12px;
}

.tf-audit-label {
  font-size: 11px;
  color: var(--tf-text-3);
}

.tf-audit-value {
  font-size: 13px;
  font-weight: 600;
  color: var(--tf-text);
  line-height: 18px;
}

.tf-audit-note {
  font-size: 11px;
  color: var(--tf-text-3);
}

.tf-log-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  padding-bottom: 10px;
  margin-bottom: 12px;
  border-bottom: 1px dashed var(--tf-border);
  font-size: 12px;
  color: var(--tf-text-3);
}

.tf-log-h {
  margin: 14px 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--tf-text);
}

.tf-log-list {
  margin: 0;
  padding-left: 20px;
  list-style: decimal;
  font-size: 13px;
  line-height: 22px;
  color: var(--tf-text-2);
}

.tf-log-note {
  margin: 12px 0 0;
  font-size: 12px;
  line-height: 19px;
  color: var(--tf-text-3);
}

.tf-log-warn {
  color: var(--tf-amber);
}

.tf-log-scores {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 8px;
  margin-bottom: 10px;
}

.tf-log-scores span {
  padding: 2px 9px;
  border-radius: 999px;
  background: var(--tf-soft);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--tf-text-2);
}

.tf-log-table {
  width: 100%;
  margin-top: 10px;
  border-collapse: collapse;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.tf-log-table th,
.tf-log-table td {
  padding: 5px 8px;
  border-bottom: 1px solid var(--tf-border);
  text-align: right;
  color: var(--tf-text-2);
}

.tf-log-table th:first-child,
.tf-log-table td:first-child {
  text-align: left;
  color: var(--tf-text);
}

.tf-log-table th {
  color: var(--tf-text-3);
  font-weight: 500;
}

@media (max-width: 1280px) {
  .tf-audit-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 860px) {
  .tf-audit-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
