<template>
  <div v-if="visible" class="analysis-veil">
    <div class="analysis-card">
      <div class="analysis-ring"></div>
      <div class="analysis-title">{{ title }}</div>
      <div v-if="subtitle" class="analysis-subtitle">{{ subtitle }}</div>

      <ul class="analysis-steps">
        <li
          v-for="(step, index) in STEPS"
          :key="step.name"
          class="analysis-step"
          :class="{ 'is-done': index < stepIndex, 'is-active': index === stepIndex }"
        >
          <span class="analysis-step-head">
            <span class="analysis-dot"></span>
            <span>{{ step.name }}</span>
          </span>

          <!-- 当前这一步在用什么数据：上一步收起，这一步展开 -->
          <Transition name="analysis-evidence">
            <ul v-if="index === stepIndex" class="analysis-evidence">
              <li v-for="line in step.evidence" :key="line">{{ line }}</li>
            </ul>
          </Transition>
        </li>
      </ul>

      <div class="analysis-progress">
        <div class="analysis-bar">
          <div class="analysis-bar-fill" :style="{ width: `${progress}%` }"></div>
        </div>
        <span class="analysis-percent">{{ progress }}%</span>
      </div>
      <div class="analysis-meta">
        已用时 {{ elapsed.toFixed(1) }} s · 七个 Agent 顺序执行，首次调用含预热，通常 3–30 秒
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { AnalysisMeta, AnalysisRequest } from '@/types/analysis'
import type { MeteorologyStation } from '@/types/meteorology'

/**
 * 分析整链路的加载浮层。
 *
 * 后端 `POST /api/analyze` 是一个整包接口，拿不到逐段回传的真实进度，
 * 所以这里摆的是**每一步真正在用的数据**（与 CoordinatorMin 的执行顺序一致），
 * 进度条走时间驱动的估计，不编会走走停停的假百分比。
 *
 * 依据行里没有任何写死的数字：站点与时段来自当前任务，行数 / 站点数 /
 * 检索半径 / 扰动设置全部来自后端 `GET /api/analyze/meta` 的真实统计；
 * meta 还没到就退回不含数字的通用描述，绝不摆假数据。
 *
 * 遮罩压在当前内容之上：点「开始分析」重算时，上一次的结果留在原位不换页。
 */

interface Props {
  active: boolean
  /** 展示依据行要用的任务参数（站点、时段） */
  task?: AnalysisRequest | null
  /** 站点登记表：用来把任务里的英文名解析成中文名与 ATC 映射 */
  stations?: MeteorologyStation[]
  /** 后端真实统计数 */
  meta?: AnalysisMeta | null
  title?: string
}

const props = withDefaults(defineProps<Props>(), {
  task: null,
  stations: () => [],
  meta: null,
  title: 'Agent 正在跑完整分析链路'
})

/** 数字格式化：1,060,416 */
function num(value: number | undefined | null): string {
  if (value === undefined || value === null) return ''
  return value.toLocaleString('en-US')
}

/** 当前任务的站点登记信息（中文名 / ATC 映射都在这里） */
const stationInfo = computed<MeteorologyStation | null>(() => {
  const task = props.task
  if (!task) return null
  const wantedId = (task.station_id || '').toLowerCase()
  const wantedName = (task.station_name || '').toLowerCase().replace(' roadside', '')
  return (
    props.stations.find(item => (item.station_id || '').toLowerCase() === wantedId) ??
    props.stations.find(
      item => (item.station_name || '').toLowerCase().replace(' roadside', '') === wantedName
    ) ??
    null
  )
})

/** 站点显示名：中文名（英文名） */
const stationLabel = computed<string>(() => {
  const task = props.task
  if (!task) return ''
  const info = stationInfo.value
  const zh = info?.station_name_zh
  if (zh && zh !== task.station_name) return `${zh}（${task.station_name}）`
  return task.station_name
})

/** 分析时段的小时数（用来描述 EPD 取数范围） */
const windowHours = computed<number>(() => {
  const task = props.task
  if (!task?.start_time || !task?.end_time) return 0
  const start = new Date(task.start_time.replace(' ', 'T')).getTime()
  const end = new Date(task.end_time.replace(' ', 'T')).getTime()
  if (Number.isNaN(start) || Number.isNaN(end) || end <= start) return 0
  return Math.round((end - start) / 3600000)
})

/** ATC 时段槽：与 traffic_tools.parse_time_range 同一规则（左闭右开、整点） */
const timeSlots = computed<string[]>(() => {
  const task = props.task
  if (!task?.start_time || !task?.end_time) return []
  const start = new Date(task.start_time.replace(' ', 'T'))
  const end = new Date(task.end_time.replace(' ', 'T'))
  if (Number.isNaN(start.getTime()) || end <= start) return []
  const slots: string[] = []
  const cursor = new Date(start)
  cursor.setMinutes(0, 0, 0)
  while (cursor < end && slots.length < 24) {
    const h = cursor.getHours()
    slots.push(`${String(h).padStart(2, '0')}00-${String((h + 1) % 24).padStart(2, '0')}00`)
    cursor.setHours(cursor.getHours() + 1)
  }
  return slots
})

/** 该站点对应的 ATC 路段站编号（手工映射 + 就近匹配，登记表里有） */
const atcIds = computed<string[]>(() => {
  const ids = stationInfo.value?.atc_station_ids ?? []
  return ids.slice(0, 4)
})

const subtitle = computed(() => {
  const parts: string[] = []
  if (stationLabel.value) parts.push(stationLabel.value)
  const task = props.task
  if (task?.start_time) {
    parts.push(`${task.start_time.slice(5, 16)} – ${task.end_time?.slice(11, 16) ?? ''}`)
  }
  return parts.join(' · ')
})

const STEPS = computed(() => {
  const meta = props.meta
  const coord =
    props.task?.lat !== undefined && props.task?.lat !== null
      ? `${Number(props.task.lat).toFixed(5)}, ${Number(props.task.lon).toFixed(5)}`
      : ''

  const airLines: string[] = []
  if (stationLabel.value) airLines.push(`站点 ${stationLabel.value}`)
  if (props.task?.start_time) {
    airLines.push(
      `时段 ${props.task.start_time.slice(0, 16)} – ${props.task.end_time?.slice(11, 16) ?? ''}${
        windowHours.value ? `（${windowHours.value} 个整点小时）` : ''
      }`
    )
  }
  airLines.push(
    meta?.epd_hourly_rows
      ? `EPD 逐小时库 ${num(meta.epd_hourly_rows)} 行 / ${meta.epd_station_count ?? '—'} 站 · AQHI 覆盖 ${Math.round((meta.aqhi_coverage ?? 0) * 100)}%`
      : '数据源：香港环保署 EPICDI 逐小时浓度（本地 SQLite 库）'
  )

  const meteoLines: string[] = []
  if (coord) meteoLines.push(`取数坐标 ${coord}（与所选监测站一致）`)
  if (props.task?.start_time) meteoLines.push(`时段 ${props.task.start_time.slice(5, 16)} – ${props.task.end_time?.slice(11, 16) ?? ''}`)
  meteoLines.push('Open-Meteo Archive API · 逐小时风速 / 风向 / 湿度 / 气温 / 降水')

  const trafficLines: string[] = []
  if (atcIds.value.length) trafficLines.push(`ATC 路段站 ${atcIds.value.join('、')}`)
  if (timeSlots.value.length) trafficLines.push(`统计时段 ${timeSlots.value.join(' / ')}（Mon–Fri 车型构成）`)
  trafficLines.push(
    meta?.atc_mix_rows
      ? `运输署 ATC 库 ${num(meta.atc_mix_rows)} 行 / ${meta.atc_station_count ?? '—'} 个统计站`
      : '数据源：运输署 ATC 交通统计（本地 SQLite 库）'
  )

  const spatialLines: string[] = []
  spatialLines.push(
    meta?.road_segments
      ? `路网中心线 ${num(meta.road_segments)} 段 · 敏感 POI ${num(meta.sensitive_poi)} 个`
      : '本地路网中心线与敏感 POI 登记（GeoJSON）'
  )
  spatialLines.push(
    meta?.spatial_radius_km
      ? `以站点为圆心 ${meta.spatial_radius_km} km 半径检索（SpatialAgent 运行参数）`
      : '以站点为圆心按配置半径检索上风向道路与敏感点'
  )

  const robustness = meta?.travel_robustness
  const finalLines: string[] = [
    '空气质量 / 气象 / 交通 / 空间四块加权归因',
    '输出决策等级、建议管控道路与完整报告（确定性编排，无示例输入）'
  ]
  if (robustness?.samples) {
    finalLines.unshift(
      `稳健性检验：风速 ±${Math.round(robustness.wind_speed_rel_sigma * 100)}% · 风向 ±${Math.round(robustness.wind_direction_sigma_deg)}° · 吸入交换率 ±${Math.round(robustness.exchange_rel_sigma * 100)}%，${robustness.samples} 次重排`
    )
  }

  return [
    { name: 'Agent 正在读取空气质量小时数据', evidence: airLines },
    { name: 'Agent 正在拉取该时段的气象场', evidence: meteoLines },
    { name: 'Agent 正在匹配交通构成与路网', evidence: trafficLines },
    { name: 'Agent 正在叠加空间：上风向道路与敏感 POI', evidence: spatialLines },
    { name: 'Agent 正在归因并生成决策与报告', evidence: finalLines }
  ]
})

const visible = ref(false)
const stepIndex = ref(0)
const elapsed = ref(0)
const progress = ref(0)
let stepTimer: ReturnType<typeof setInterval> | null = null
let elapsedTimer: ReturnType<typeof setInterval> | null = null
let finishTimer: ReturnType<typeof setTimeout> | null = null

function clearTimers(): void {
  if (stepTimer) clearInterval(stepTimer)
  if (elapsedTimer) clearInterval(elapsedTimer)
  if (finishTimer) clearTimeout(finishTimer)
  stepTimer = null
  elapsedTimer = null
  finishTimer = null
}

/**
 * 收尾补亮：结果已到，但步骤动画可能还没走完。
 * 按 ~240ms/步 把剩余步骤逐个推进（stepIndex 推到列表长度即全部 is-done），
 * 全亮完再停一小拍收浮层，保证五条信息都被高亮过。
 */
function finishSteps(): void {
  if (stepIndex.value < STEPS.value.length) {
    stepIndex.value += 1
    finishTimer = setTimeout(finishSteps, 240)
  } else {
    finishTimer = setTimeout(() => {
      visible.value = false
    }, 420)
  }
}

watch(
  () => props.active,
  active => {
    clearTimers()
    if (!active) {
      // 浮层不立刻消失，先把没亮完的步骤快速补亮一遍再收
      if (visible.value) {
        progress.value = 100
        finishTimer = setTimeout(finishSteps, 300)
      }
      return
    }

    visible.value = true
    stepIndex.value = 0
    elapsed.value = 0
    progress.value = 0
    // 停在最后一步等结果，不循环回第一步，免得看起来像卡住了
    stepTimer = setInterval(() => {
      stepIndex.value = Math.min(stepIndex.value + 1, STEPS.value.length - 1)
    }, 1500)
    elapsedTimer = setInterval(() => {
      elapsed.value = Number((elapsed.value + 0.1).toFixed(1))
      // 热请求约 3 s，冷启动（含预热）可能 30 s：时间常数取 4 s，逼近 92% 后停住等结果
      progress.value = Math.round(92 * (1 - Math.exp(-elapsed.value / 4)))
    }, 100)
  },
  { immediate: true }
)

onBeforeUnmount(clearTimers)
</script>

<style scoped>
.analysis-veil {
  position: fixed;
  top: 60px;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 40;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 8vh;
  background: rgba(247, 248, 250, 0.72);
  backdrop-filter: blur(3px);
}

.analysis-card {
  width: 440px;
  max-width: calc(100vw - 32px);
  padding: 24px 26px 18px;
  border: 1px solid var(--border-color, #e6eaf2);
  border-radius: 12px;
  background: var(--panel-bg, #fff);
  box-shadow: 0 12px 40px rgba(23, 32, 48, 0.14);
  text-align: center;
}

.analysis-ring {
  width: 34px;
  height: 34px;
  margin: 0 auto;
  border: 3px solid #dfe5ee;
  border-top-color: var(--el-color-primary, #409eff);
  border-radius: 50%;
  animation: analysis-spin 0.9s linear infinite;
}

@keyframes analysis-spin {
  to { transform: rotate(360deg); }
}

.analysis-title {
  margin-top: 12px;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary, #172030);
}

.analysis-subtitle {
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-tertiary, #8a93a6);
}

.analysis-steps {
  display: flex;
  flex-direction: column;
  gap: 9px;
  margin: 18px 0 0;
  padding: 0;
  list-style: none;
  text-align: left;
}

.analysis-step {
  font-size: 12.5px;
  color: var(--text-tertiary, #8a93a6);
  transition: color 0.2s;
}

.analysis-step-head {
  display: flex;
  align-items: center;
  gap: 9px;
}

.analysis-dot {
  width: 7px;
  height: 7px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #d5dae3;
}

.analysis-step.is-done { color: var(--text-secondary, #5a6478); }
.analysis-step.is-done .analysis-dot { background: var(--el-color-success, #67c23a); }

.analysis-step.is-active {
  color: var(--el-color-primary, #409eff);
  font-weight: 600;
}

.analysis-step.is-active .analysis-dot {
  background: var(--el-color-primary, #409eff);
  animation: analysis-pulse 1.1s ease-in-out infinite;
}

@keyframes analysis-pulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.5); opacity: 0.45; }
}

.analysis-evidence {
  margin: 6px 0 0 16px;
  padding: 8px 10px;
  list-style: none;
  border-left: 2px solid #dbe6fb;
  background: #f6f9ff;
  border-radius: 0 6px 6px 0;
  overflow: hidden;
}

.analysis-evidence li {
  font-size: 11.5px;
  line-height: 18px;
  color: var(--text-secondary, #5a6478);
}

.analysis-evidence-enter-active,
.analysis-evidence-leave-active {
  transition: all 0.25s ease;
  max-height: 140px;
}

.analysis-evidence-enter-from,
.analysis-evidence-leave-to {
  max-height: 0;
  opacity: 0;
  padding-top: 0;
  padding-bottom: 0;
}

.analysis-progress {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 18px;
}

.analysis-bar {
  position: relative;
  flex: 1;
  height: 5px;
  border-radius: 3px;
  background: #eef1f6;
  overflow: hidden;
}

.analysis-bar-fill {
  height: 100%;
  border-radius: 3px;
  background: var(--el-color-primary, #409eff);
  transition: width 0.4s ease;
}

.analysis-percent {
  min-width: 38px;
  font-size: 12px;
  color: var(--text-secondary, #5a6478);
  text-align: right;
}

.analysis-meta {
  margin-top: 10px;
  font-size: 11px;
  color: var(--text-tertiary, #8a93a6);
}
</style>
