<template>
  <MainLayout>
    <!-- 顶部查询栏：与其它三页同一套控件。站点在这里是污染源监测站，
         日期与时段是出行时段，点「开始规划」按这三项重算路线。 -->
    <div class="travel-query">
      <TaskQueryBar
        scope="travel"
        :initial-date="travelStore.departDate"
        :initial-start="travelStore.departStart"
        :initial-end="travelStore.departEnd"
        @analyze="onTravelQuery"
      />
    </div>

    <div class="tf-scope travel-page">
      <!-- 加载态 -->
      <div v-if="travelStore.loading" class="state-block">
        <el-icon class="is-loading" :size="30"><Loading /></el-icon>
        <p>正在规划路线…</p>
      </div>

      <!-- 正常内容 -->
      <template v-else-if="result">
        <!-- ① 气象与污染概况 -->
        <TravelMetricStrip :items="metricItems" />

        <!-- ② 起终点 / 行程 / 场景 -->
        <TravelControlBar
          :origin="origin"
          :destination="destination"
          :can-swap="canSwap"
          :pick-role="pickRole"
          :planning="travelStore.planning"
          :dirty="pointsDirty || placeSelectionPending"
          :profile-id="travelStore.profileId"
          :profiles="travelStore.profileOptions"
          @pick="startPicking"
          @swap="travelStore.swapCustomPoints()"
          @primary-action="onPrimaryAction"
          @select-point="onSelectPlace"
          @select-profile="onProfileChange"
        />

        <el-alert
          v-if="travelStore.error"
          :title="travelStore.error"
          type="warning"
          :closable="false"
          show-icon
          class="inline-alert"
        />

        <!-- ③ 地图 + 方案综合评级 -->
        <section class="tf-main">
          <div class="tf-card tf-map-card">
            <div class="tf-hd">
              <el-icon :size="16" class="tf-hd-icon"><MapLocation /></el-icon>
              <span class="tf-hd-title">扩散模拟与九龙核心路网</span>
              <span class="tf-pill" :class="pollutionField ? 'is-green' : ''">
                {{ pollutionField ? '实时高斯烟羽场' : '未建立暴露场' }}
              </span>
              <div class="tf-hd-right tf-map-legend-inline">
                <span v-for="item in mapLegend" :key="item.label" class="tf-map-legend-item">
                  <span class="tf-map-legend-dot" :style="{ background: item.color }"></span>
                  {{ item.label }}
                </span>
              </div>
            </div>

            <div class="tf-map-body">
              <TravelRouteMap
                :routes="travelStore.routeOptions"
                :origin="origin"
                :destination="destination"
                :selected-id="travelStore.selectedRouteId"
                :pollution-grid="pollutionGrid"
                :pick-role="pickRole"
                @select-route="travelStore.selectRoute"
                @pick-point="onPickPoint"
                @move-point="onMovePoint"
                @cancel-pick="pickRole = null"
              />
            </div>

            <div class="tf-map-foot">
              <span class="tf-map-foot-label">当前路线：</span>
              <template v-for="(name, index) in chainNames" :key="`${name}-${index}`">
                <span v-if="index > 0" class="tf-map-foot-arrow">→</span>
                <span
                  class="tf-map-foot-chip"
                  :class="{ 'is-end': index === chainNames.length - 1 }"
                >{{ name }}</span>
              </template>
              <span v-if="!chainNames.length" class="tf-map-foot-label">—</span>

              <div class="tf-map-foot-right">
                <span class="tf-mono">{{ highlighted?.duration_minutes.toFixed(0) ?? '—' }} min</span>
                <span class="tf-mono">{{ highlighted?.junctions ?? 0 }} 路口</span>
                <span class="tf-pill" :class="safetyPill.cls">
                  均暴露 {{ meanExposure }} · {{ safetyPill.text }}
                </span>
              </div>
            </div>
          </div>

          <RouteRatingPanel
            :routes="travelStore.routeOptions"
            :active-id="travelStore.highlightedRoute?.route_id || ''"
            @select-route="travelStore.selectRoute"
          />
        </section>

        <!-- ④ 六张分析卡 -->
        <TravelInsightCards :result="result" :pollution-field="pollutionField" />

        <!-- ⑤ 审计轨迹 -->
        <TravelAuditBar
          :result="result"
          :pollution-field="pollutionField"
          :evidence="currentEvidence"
          :confidence="currentConfidence"
          :data-quality="currentDataQuality"
          :meteorology="travelStore.meteorology"
        />
      </template>

      <!-- 无数据 / 实时规划失败 -->
      <div v-else class="state-block">
        <el-alert
          v-if="travelStore.error"
          :title="travelStore.error"
          type="warning"
          :closable="false"
          show-icon
          description="案例数据由后端读取 examples/travel_case_output.json 返回，请先启动 api/app.py。"
        />
        <el-empty v-else description="暂无出行数据" />
        <el-button type="primary" :loading="travelStore.loading" @click="retryLoad">
          重新连接后端
        </el-button>
      </div>

      <!-- 重新规划时的遮罩：上一次的结果留在原位，只压一层浮层，不整页重载 -->
      <div v-if="veilVisible" class="planning-veil">
        <div class="planning-card">
          <div class="planning-ring"></div>
          <div class="planning-title">Agent 正在重新规划路线</div>
          <div class="planning-route tf-mono">
            {{ pointLabel(travelStore.customOrigin) }} → {{ pointLabel(travelStore.customDestination) }}
          </div>

          <ul class="planning-steps">
            <li
              v-for="(step, index) in PLANNING_STEPS"
              :key="step"
              class="planning-step"
              :class="{ 'is-done': index < planningStep, 'is-active': index === planningStep }"
            >
              <span class="planning-step-head">
                <span class="planning-dot"></span>
                <span>{{ step }}</span>
              </span>

              <!-- 当前这一步的依据：上一步收起来，这一步展开 -->
              <Transition name="planning-evidence">
                <ul v-if="index === planningStep" class="planning-evidence">
                  <li v-for="line in stepEvidence[index]" :key="line">{{ line }}</li>
                </ul>
              </Transition>
            </li>
          </ul>

          <div class="planning-progress">
            <div class="planning-bar">
              <div class="planning-bar-fill" :style="{ width: `${progress}%` }"></div>
            </div>
            <span class="planning-percent tf-mono">{{ progress }}%</span>
          </div>
          <div class="planning-meta">
            已用时 {{ elapsed.toFixed(1) }} s · 首次调用含模型预热，通常 5–35 秒
          </div>
        </div>
      </div>
    </div>
  </MainLayout>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Loading, MapLocation } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import MainLayout from '@/layouts/MainLayout.vue'
import TravelMetricStrip from '@/components/travel/TravelMetricStrip.vue'
import TravelControlBar from '@/components/travel/TravelControlBar.vue'
import RouteRatingPanel from '@/components/travel/RouteRatingPanel.vue'
import TravelInsightCards from '@/components/travel/TravelInsightCards.vue'
import TravelAuditBar from '@/components/travel/TravelAuditBar.vue'
import TravelRouteMap from '@/components/travel/TravelRouteMap.vue'
import TaskQueryBar from '@/components/global/TaskQueryBar.vue'
import { getModeColor, bearingLabel } from '@/utils/travelMapper'
import { useTravelStore } from '@/stores/travel'
import { useAnalysisStore } from '@/stores/analysis'
import '@/styles/travel-theme.css'
import type {
  TravelFieldSample,
  TravelMetricItem,
  TravelPoint,
  TravelResponse,
  TravelRouteOption
} from '@/types/travel'

/**
 * TravelAdviceView - 出行建议与路线推荐页面
 *
 * 两条使用路径：
 * 1. 直接浏览案例演示，切换场景 / 行程 / 出行习惯画像；
 * 2. 自己在地图上点选或拖动设定起终点，调用后端实时规划。
 *
 * 版面自左向右读：气象概况 → 起终点与场景 → 地图与方案评级 → 六张分析卡 → 审计轨迹。
 */
const travelStore = useTravelStore()
const analysisStore = useAnalysisStore()

/** 当前处于点选模式的角色（null 表示不点选） */
const pickRole = ref<'origin' | 'destination' | null>(null)
const placeSelectionPending = ref(false)

/**
 * 实时计算的阶段旁白。
 * 后端是一个整包接口，拿不到真实进度，所以进度条走的是**时间驱动的估计**：
 * 按约 6.5 s 的时间常数逼近 92%，等结果真的回来再补到 100%。
 * 不编"假百分比"（不会走走停停骗人），但也让用户对还要多久有个数。
 */
const PLANNING_STEPS = [
  'Agent 正在拉取起终点时段的气象与空气质量数据',
  'Agent 正在匹配路网与轨道站点、生成候选路线',
  'Agent 正在重建高斯烟羽暴露场',
  'Agent 正在逐方案评分并做稳健性扰动检验'
]
const planningStep = ref(0)
const progress = ref(0)
const elapsed = ref(0)
const veilVisible = ref(false)
let stepTimer: ReturnType<typeof setInterval> | null = null
let tickTimer: ReturnType<typeof setInterval> | null = null
let hideTimer: ReturnType<typeof setTimeout> | null = null

function clearPlanningTimers(): void {
  if (stepTimer) clearInterval(stepTimer)
  if (tickTimer) clearInterval(tickTimer)
  if (hideTimer) clearTimeout(hideTimer)
  stepTimer = null
  tickTimer = null
  hideTimer = null
}

watch(
  () => travelStore.planning,
  active => {
    clearPlanningTimers()
    if (!active) {
      // 结果到了就把进度补满；浮层不立刻收，先把没亮完的步骤快速补亮一遍
      if (veilVisible.value) {
        progress.value = 100
        hideTimer = setTimeout(finishSteps, 300)
      }
      return
    }

    planningStep.value = 0
    progress.value = 0
    elapsed.value = 0
    veilVisible.value = true

    stepTimer = setInterval(() => {
      // 停在最后一步等结果，不循环回第一步，免得看起来像卡住了
      planningStep.value = Math.min(planningStep.value + 1, PLANNING_STEPS.length - 1)
    }, 1500)
    tickTimer = setInterval(() => {
      elapsed.value = Number((elapsed.value + 0.1).toFixed(1))
      progress.value = Math.min(92, Math.round(92 * (1 - Math.exp(-elapsed.value / 6.5))))
    }, 100)
  }
)

onBeforeUnmount(clearPlanningTimers)

/**
 * 收尾补亮：结果已到，但步骤动画可能还没走完。
 * 按 ~260ms/步 把剩余步骤逐个推进（planningStep 推到列表长度即全部 is-done），
 * 全亮完再停一小拍收浮层，保证四条信息都被高亮过。
 */
function finishSteps() {
  if (planningStep.value < PLANNING_STEPS.length) {
    planningStep.value += 1
    hideTimer = setTimeout(finishSteps, 260)
  } else {
    hideTimer = setTimeout(() => {
      veilVisible.value = false
    }, 420)
  }
}

onMounted(async () => {
  if (!travelStore.caseOutput) {
    await travelStore.loadCase().catch(() => undefined)
  }
})

/** 后端没起来时的重试：重新走一遍"后端 → 快照"的加载链路 */
async function retryLoad() {
  await travelStore.loadCase().catch(() => undefined)
}

const result = computed(() => travelStore.currentResult)
const currentRun = computed(() => travelStore.currentRun)
const origin = computed<TravelPoint | null>(() => travelStore.origin)
const destination = computed<TravelPoint | null>(() => travelStore.destination)
const highlighted = computed<TravelRouteOption | null>(() => travelStore.highlightedRoute)

const pollutionGrid = computed<TravelFieldSample[]>(() => travelStore.pollutionField?.grid ?? [])
const pollutionField = computed(() => travelStore.pollutionField)

const currentEvidence = computed(
  () => travelStore.liveResult?.evidence ?? currentRun.value?.evidence ?? []
)
const currentConfidence = computed(
  () => travelStore.liveResult?.confidence ?? currentRun.value?.confidence ?? 0
)
const currentDataQuality = computed<TravelResponse['data_quality'] | null>(
  () => travelStore.liveResult?.data_quality ?? currentRun.value?.data_quality ?? null
)

const canSwap = computed(() => !!travelStore.customOrigin && !!travelStore.customDestination)

/** 起终点被拖动 / 重选过，和当前结果已经不是同一趟行程 */
const pointsDirty = computed(() => {
  const current = result.value
  const from = travelStore.customOrigin
  const to = travelStore.customDestination
  if (!current || !from || !to) return false
  const samePoint = (a: TravelPoint, b: TravelPoint) =>
    Math.abs(a.lat - b.lat) < 1e-5 && Math.abs(a.lon - b.lon) < 1e-5
  return !samePoint(from, current.origin) || !samePoint(to, current.destination)
})

/** 数值统一保留 n 位后去掉多余的 0，避免出现 3.2000 这样的尾巴 */
function num(value: number | null | undefined, digits: number): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return String(Number(value.toFixed(digits)))
}

/** 起终点文案：有地名用地名，地图上随手点的就用经纬度 */
function pointLabel(point: TravelPoint | null): string {
  if (!point) return '未设定'
  if (point.name && point.name !== '地图点选') return point.name
  return `${point.lat.toFixed(5)}, ${point.lon.toFixed(5)}`
}

/**
 * 每一步正在依据什么在算。
 *
 * 后端是一个整包接口，拿不到逐段回传的中间结论，所以这里摆的是**这一步的输入**：
 * 时段、数据源、起终点吸附位置、风场与污染源、评分口径。
 * 这些都是本次请求真正用到的参数，不是编出来的"推理过程"。
 */
const stepEvidence = computed<string[][]>(() => {
  const from = travelStore.customOrigin
  const to = travelStore.customDestination
  const profile = travelStore.profileOptions.find(item => item.id === travelStore.profileId)
  const weather = travelStore.weather
  const field = pollutionField.value

  const weatherLine =
    weather.mean_wind_speed !== null
      ? `Open-Meteo 归档：风速 ${num(weather.mean_wind_speed, 2)} m/s · 湿度 ${num(weather.mean_humidity, 1)}% · 气温 ${num(weather.mean_temperature, 1)} °C`
      : 'Open-Meteo 归档：风速 / 风向 / 湿度 / 气温 / 降水'

  const windLine =
    weather.downwind_direction !== null
      ? `风场：来风 ${weather.dominant_wind_direction_label ?? '—'} → 输送 ${bearingLabel(weather.downwind_direction)} ${weather.downwind_direction.toFixed(0)}°`
      : '风场：按逐小时风向重建输送方向'

  const sourceLine = field
    ? `污染源：监测站 ${field.station_sources?.length ?? 0} 个 + 走廊线源 ${field.corridor_source_count} 段`
    : '污染源：监测站 + 走廊线源'

  const profileLine = profile
    ? `出行习惯：${profile.name}${
        profile.maxWalkKm !== null ? ` · 步行上限 ${profile.maxWalkKm} km` : ''
      }`
    : '出行习惯：当前画像的敏感度权重'

  // 扰动幅度来自后端 /api/analyze/meta 读到的出行配置（travel_agent 实际用的那组）
  const robustness = analysisStore.meta?.travel_robustness
  const robustnessLine = robustness?.samples
    ? `扰动：风速 ±${Math.round(robustness.wind_speed_rel_sigma * 100)}% · 风向 ±${Math.round(robustness.wind_direction_sigma_deg)}° · 吸入交换率 ±${Math.round(robustness.exchange_rel_sigma * 100)}%，${robustness.samples} 次重排`
    : '按出行配置对风速 / 风向 / 吸入交换率做蒙特卡洛重排'

  return [
    [
      `时段 ${travelStore.departDate} ${travelStore.departStart} – ${travelStore.departEnd}`,
      weatherLine,
      `污染输入：${travelStore.epdStationCount ? `全部 ${travelStore.epdStationCount} 个` : '全部'} EPD 监测站（EPD 逐小时浓度 · 气象与交通同步联合取数）`
    ],
    [
      from ? `起点吸附：${from.lat.toFixed(5)}, ${from.lon.toFixed(5)}` : '起点吸附到最近路段',
      to ? `终点吸附：${to.lat.toFixed(5)}, ${to.lon.toFixed(5)}` : '终点吸附到最近路段',
      '路网：香港道路中心线 ｜ 轨道：按邻近站重建站序',
      profileLine
    ],
    [
      windLine,
      sourceLine,
      '0.1 km 网格重建暴露场，逐候选路线累计吸入当量'
    ],
    [
      '等效耗时 = 户外等效 + 天气加成 + 吸入折算 + 方式常数 + 超限罚分',
      robustnessLine,
      '输出：推荐方案、稳健性区间与完整证据链'
    ]
  ]
})

const metricItems = computed<TravelMetricItem[]>(() => {
  // 气象七项直接读 travelStore.weather —— 它优先来自气象 Agent 接口
  const weather = travelStore.weather
  // 污染风险用真实值：/plan 响应里的 pollution_risk_input 是后端对全部 18 个
  // EPD 监测站的逐小时数据联合算出的异常分，不是案例场景的手填值
  const risk = result.value?.weather_severity?.pollution_risk_input
  const peak = pollutionField.value?.peak?.index
  const straight = result.value?.straight_line_km

  return [
    { key: 'humidity', label: '湿度', value: num(weather.mean_humidity, 2), unit: ' %' },
    { key: 'temperature', label: '气温', value: num(weather.mean_temperature, 1), unit: ' °C' },
    { key: 'wind', label: '风速', value: num(weather.mean_wind_speed, 4), unit: ' m/s' },
    {
      key: 'rain',
      label: '降雨',
      value: num(weather.total_precipitation, 1),
      unit: ' mm',
      tone: 'blue'
    },
    {
      key: 'windDirection',
      label: '主导风向',
      value: weather.dominant_wind_direction_label ?? '—'
    },
    {
      key: 'risk',
      label: '污染风险',
      value: risk === null || risk === undefined ? '—' : risk.toFixed(2),
      tone: 'amber'
    },
    {
      key: 'peak',
      label: '暴露峰值',
      value: peak === null || peak === undefined ? '—' : peak.toFixed(2),
      tone: 'red'
    },
    { key: 'straight', label: '直线距离', value: num(straight, 2), unit: ' km' }
  ]
})

/** 地图卡头部的一行图例，说明三种线色各代表什么 */
const mapLegend = computed(() => [
  { label: '港铁推荐', color: getModeColor('transit') },
  { label: '驾车干道', color: getModeColor('drive') },
  { label: '步行方案', color: getModeColor('walk') }
])

/** 当前高亮路线经过的节点名称，公共交通显示站序 */
const chainNames = computed(() => {
  const route = highlighted.value
  if (!route) return []
  return route.road_names.length ? route.road_names : [origin.value?.name ?? '起点', destination.value?.name ?? '终点']
})

const meanExposure = computed(() => (highlighted.value?.exposure.mean_index ?? 0).toFixed(3))

/** 走廊平均暴露指数 → 安全等级描述 */
const safetyPill = computed(() => {
  const mean = highlighted.value?.exposure.mean_index ?? 0
  if (mean < 0.05) return { text: '极安全', cls: 'is-green' }
  if (mean < 0.15) return { text: '较安全', cls: 'is-green' }
  if (mean < 0.3) return { text: '中等暴露', cls: 'is-amber' }
  return { text: '暴露偏高', cls: 'is-red' }
})

/** 案例数据尚未选择自定义点时，用案例端点初始化，便于直接拖动比较 */
watch(
  () => result.value?.origin,
  value => {
    if (value && !travelStore.customOrigin) travelStore.customOrigin = { ...value }
  },
  { immediate: true }
)
watch(
  () => result.value?.destination,
  value => {
    if (value && !travelStore.customDestination) travelStore.customDestination = { ...value }
  },
  { immediate: true }
)

/**
 * 点选起终点。
 * 走"引导式"：点了自定义选点就进入起点模式，起点定完自动切到终点，
 * 终点定完直接发起计算，不用再找按钮。
 */
function startPicking(role: 'origin' | 'destination') {
  pickRole.value = role
}

function startPickGuide() {
  pickRole.value = 'origin'
  ElMessage.info('请在地图上点选起点')
}

function onPrimaryAction() {
  if (pointsDirty.value || placeSelectionPending.value) {
    void runPlan()
    return
  }
  startPickGuide()
}

function onPickPoint(payload: { role: 'origin' | 'destination'; point: TravelPoint }) {
  travelStore.setCustomPoint(payload.role, payload.point)

  if (payload.role === 'origin') {
    pickRole.value = 'destination'
    ElMessage.info('起点已设定，接着点选终点，选完会自动开始计算')
    return
  }

  pickRole.value = null
  void runPlan()
}

function onMovePoint(payload: { role: 'origin' | 'destination'; point: TravelPoint }) {
  travelStore.setCustomPoint(payload.role, payload.point)
}

function onSelectPlace(payload: { role: 'origin' | 'destination'; point: TravelPoint }) {
  pickRole.value = null
  travelStore.setCustomPoint(payload.role, payload.point)
  placeSelectionPending.value = true
}

async function runPlan() {
  pickRole.value = null
  try {
    await travelStore.planWithCustomPoints()
    placeSelectionPending.value = false
    ElMessage.success('已按自定义起终点重新规划')
  } catch (err: any) {
    ElMessage.warning(err?.message || '实时规划失败，请检查后端服务')
  }
}

/**
 * 切换出行习惯。
 * 看的还是案例数据时直接换画像即可（本地就有各画像的结果）；
 * 已经按自定义起终点算过了，就按新画像重算一遍——否则会出现
 * "习惯换了、结论没换"的错觉。
 */
async function onProfileChange(profileId: string) {
  const wasLive = travelStore.live
  travelStore.selectProfile(profileId)
  if (wasLive && travelStore.customOrigin && travelStore.customDestination) {
    await runPlan()
  }
}

/**
 * 顶部查询栏点「开始规划」。
 *
 * 日期与时段是**出行时段**（写进 `start_time` / `end_time`，决定取哪一段气象
 * 与空气质量）。污染源不需要选：后端会默认把**全部 18 个 EPD 监测站**
 * 都作为污染输入。起终点不在这里改，仍然由地图上的起终点决定。
 */
async function onTravelQuery(payload: { date: string; startTime: string; endTime: string }) {
  travelStore.departDate = payload.date
  travelStore.departStart = payload.startTime
  travelStore.departEnd = payload.endTime

  pickRole.value = null
  try {
    await travelStore.planWithCustomPoints()
    placeSelectionPending.value = false
    ElMessage.success(
      `已按 ${payload.date} ${payload.startTime}–${payload.endTime}（18 站联合污染输入）重新规划`
    )
  } catch (err: any) {
    ElMessage.warning(err?.message || '实时规划失败，请检查后端服务')
  }
}
</script>

<style scoped>
/* 顶部查询栏跟着出行页走浅色：这几个变量会在子组件内部被 var() 取到，
   自定义属性是继承的，所以不用穿透就能改掉顶栏的底色与文字色。 */
.travel-query {
  --panel-bg: #ffffff;
  --border-color: #e6eaf2;
  --text-primary: #172030;
  --text-secondary: #5a6478;
  --text-tertiary: #8a93a6;
}

.travel-page {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 100%;
  padding: 14px 20px 20px;
  background: var(--tf-bg);
}

.state-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  min-height: 320px;
  color: var(--tf-text-2);
  font-size: 14px;
}

.state-block .is-loading {
  color: var(--tf-blue);
  animation: travel-view-spin 1s linear infinite;
}

@keyframes travel-view-spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

.inline-alert {
  border-radius: var(--tf-radius-sm);
}

/* ── 地图 + 方案评级：左边地图、右边方案栏，两栏同高 ─────────────────── */

.tf-main {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 450px;
  gap: 12px;
  /* 定高由 --tf-main-h 锁定，候选路线再多也不把地图撑长 */
  align-items: start;
}

/*
 * The fixed 450px rating rail can otherwise consume the complete grid width
 * in a narrow window, leaving the map column at 0px. Tencent Maps rejects a
 * map created in that state (the SDK reports a null camera frustum). Stack
 * the two cards before that can happen; the desktop layout is unchanged.
 */
@media (max-width: 1100px) {
  .tf-main {
    grid-template-columns: minmax(0, 1fr);
  }
}

.tf-map-card {
  display: flex;
  flex-direction: column;
  height: var(--tf-main-h);
  min-width: 0;
}

.tf-map-body {
  position: relative;
  flex: 1;
  min-height: 0;
}

/* 地图卡底部：当前路线的站点链 + 耗时/路口/均暴露 */
.tf-map-foot {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  padding: 8px 12px;
  border-top: 1px solid var(--tf-border);
  font-size: 12px;
  color: var(--tf-text-2);
}

.tf-map-foot-label {
  color: var(--tf-text-3);
}

.tf-map-foot-arrow {
  color: var(--tf-text-3);
}

.tf-map-foot-chip {
  padding: 2px 10px;
  border: 1px solid var(--tf-border);
  border-radius: 999px;
  background: var(--tf-soft);
  font-size: 11.5px;
  color: var(--tf-text-2);
}

.tf-map-foot-chip.is-end {
  border-color: rgba(18, 160, 107, 0.45);
  background: var(--tf-green-soft);
  color: var(--tf-green);
  font-weight: 600;
}

.tf-map-foot-right {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 地图卡头部的行内图例 */
.tf-map-legend-inline {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.tf-map-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11.5px;
  color: var(--tf-text-2);
}

.tf-map-legend-dot {
  width: 14px;
  height: 4px;
  border-radius: 2px;
}

/* ── 重新规划时的浮层：旧结果留在原位，只压一层遮罩 ─────────────────── */

.planning-veil {
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

.planning-card {
  width: 420px;
  max-width: calc(100vw - 32px);
  padding: 24px 26px 18px;
  border: 1px solid var(--tf-border);
  border-radius: var(--tf-radius);
  background: var(--tf-card);
  box-shadow: 0 12px 40px rgba(23, 32, 48, 0.14);
  text-align: center;
}

.planning-ring {
  width: 34px;
  height: 34px;
  margin: 0 auto;
  border: 3px solid var(--tf-border-strong);
  border-top-color: var(--tf-blue);
  border-radius: 50%;
  animation: tf-planning-spin 0.9s linear infinite;
}

@keyframes tf-planning-spin {
  to { transform: rotate(360deg); }
}

.planning-title {
  margin-top: 12px;
  font-size: 15px;
  font-weight: 600;
  color: var(--tf-text);
}

.planning-route {
  margin-top: 4px;
  font-size: 12px;
  color: var(--tf-text-3);
}

.planning-steps {
  display: flex;
  flex-direction: column;
  gap: 9px;
  margin: 18px 0 0;
  padding: 0;
  list-style: none;
  text-align: left;
}

.planning-step {
  font-size: 12.5px;
  color: var(--tf-text-3);
  transition: color 0.2s;
}

.planning-step-head {
  display: flex;
  align-items: center;
  gap: 9px;
}

.planning-dot {
  width: 7px;
  height: 7px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #d5dae3;
}

.planning-step.is-done {
  color: var(--tf-text-2);
}

.planning-step.is-done .planning-dot {
  background: var(--tf-green);
}

.planning-step.is-active {
  color: var(--tf-blue);
  font-weight: 600;
}

.planning-step.is-active .planning-dot {
  background: var(--tf-blue);
  animation: tf-planning-pulse 1.1s ease-in-out infinite;
}

@keyframes tf-planning-pulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.5); opacity: 0.45; }
}

.planning-evidence {
  margin: 6px 0 0 16px;
  padding: 8px 10px;
  list-style: none;
  border-left: 2px solid #dbe6fb;
  background: #f6f9ff;
  border-radius: 0 6px 6px 0;
  overflow: hidden;
}

.planning-evidence li {
  font-size: 11.5px;
  line-height: 18px;
  color: var(--tf-text-2);
}

.planning-evidence-enter-active,
.planning-evidence-leave-active {
  transition: all 0.25s ease;
  max-height: 140px;
}

.planning-evidence-enter-from,
.planning-evidence-leave-to {
  max-height: 0;
  opacity: 0;
  padding-top: 0;
  padding-bottom: 0;
}

.planning-progress {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 18px;
}

.planning-bar {
  position: relative;
  flex: 1;
  height: 5px;
  border-radius: 3px;
  background: #eef1f6;
  overflow: hidden;
}

.planning-bar-fill {
  height: 100%;
  border-radius: 3px;
  background: var(--tf-blue);
  transition: width 0.4s ease;
}

.planning-percent {
  min-width: 38px;
  font-size: 12px;
  color: var(--tf-text-2);
  text-align: right;
}

.planning-meta {
  margin-top: 10px;
  font-size: 11px;
  color: var(--tf-text-3);
}
</style>
