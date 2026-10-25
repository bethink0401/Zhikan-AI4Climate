import { defineStore } from 'pinia'
import { computed, ref, watch } from 'vue'
import { analyzeMeteorology, fetchMeteorologyStations } from '@/api/meteorology'
import { fetchTravelCase, planTravel } from '@/api/travel'
import type {
  MeteorologyAnalyzeTask,
  MeteorologyDataQuality,
  MeteorologyResult,
  MeteorologySnapshot,
  WeatherView
} from '@/types/meteorology'
import type {
  TravelCaseOutput,
  TravelPlanRequest,
  TravelPoint,
  TravelPollutionAdvice,
  TravelPollutionField,
  TravelProfileOption,
  TravelResponse,
  TravelRouteOption
} from '@/types/travel'

/**
 * 出行建议状态管理
 *
 * 数据来源：
 * 1. 案例演示数据 —— 后端读取 `examples/travel_case_output.json`（run_travel_case.py
 *    的完整输出）后原样返回；后端不可达时退到 public/ 下的同源快照，并标注来源。
 * 2. 用户自定义起终点 —— 调用后端 `/api/travel/plan` 实时规划；后端不可用时
 *    保留上一次结果并给出明确提示，不静默降级。
 */
export const useTravelStore = defineStore('travel', () => {
  /** 首次载入案例数据时的整页加载态 */
  const loading = ref(false)
  /**
   * 实时规划态。刻意与 loading 分开：
   * 规划时页面上已有内容（上一次的结果、地图、图表）都还在，
   * 不能整页替换成加载块，否则每次点选终点都像刷新了页面。
   */
  const planning = ref(false)
  const error = ref<string | null>(null)

  /** 案例数据 */
  const caseOutput = ref<TravelCaseOutput | null>(null)

  /** 当前选中的场景 / 行程 / 画像 */
  const scenarioId = ref<string>('')
  const caseId = ref<string>('')
  const profileId = ref<string>('commuter')

  /** 地图上点选的路线；为空时高亮推荐方案 */
  const selectedRouteId = ref<string>('')

  /** 用户自定义起终点（为空表示正在看案例数据） */
  const customOrigin = ref<TravelPoint | null>(null)
  const customDestination = ref<TravelPoint | null>(null)

  /**
   * 出行时段。由页面顶部查询栏设置，实时规划时随请求发给后端。
   *
   * 污染源**不在这里选**：后端 `/api/travel/plan` 在请求没带污染源时，
   * 会默认把全部路边监测站（旺角/铜锣湾/中环）都作为污染输入 ——
   * 出行建议关心的是整片路网在当前空气条件下的暴露，不是某一个站。
   * 默认时段对齐案例场景 A：8 月 15 日早高峰。
   */
  const departDate = ref('2023-08-15')
  const departStart = ref('08:00')
  const departEnd = ref('11:00')

  /** 真实接口返回的结果 */
  const liveResult = ref<TravelResponse | null>(null)
  const live = ref(false)

  /**
   * 气象数据。
   *
   * 出行结果里那份 `weather_summary` 是**出行 Agent 内部调气象 Agent 得到的快照**，
   * 页面直接读它等于气象链路从没被前端碰过。这里改成显式调
   * `POST /api/meteorology/analyze`，拿气象 Agent 的完整返回
   * （weather_risk、扩散条件、逐小时序列、证据链），顺带把来源标清楚。
   */
  const meteorology = ref<MeteorologySnapshot | null>(null)
  /** 上一次请求的关键字，避免同一起终点反复打接口 */
  let meteorologyKey = ''

  const scenarios = computed(() => caseOutput.value?.scenarios ?? [])

  const currentScenario = computed(() => {
    const list = scenarios.value
    return list.find(item => item.scenario_id === scenarioId.value) ?? list[0] ?? null
  })

  const scenarioCases = computed(() => currentScenario.value?.cases ?? [])

  const currentCase = computed(() => {
    const list = scenarioCases.value
    return list.find(item => item.case_id === caseId.value) ?? list[0] ?? null
  })

  const currentRun = computed(() => {
    if (live.value) return null
    const scenario = currentScenario.value
    if (!scenario) return null
    const runs = currentCase.value?.runs ?? {}
    if (profileId.value in runs) return runs[profileId.value]
    return Object.values(runs)[0] ?? null
  })

  /** 当前生效的完整结果：自定义规划优先，否则用案例数据 */
  const currentResult = computed(() =>
    live.value ? liveResult.value?.result ?? null : currentRun.value?.result ?? null
  )

  const routeOptions = computed<TravelRouteOption[]>(
    () => currentResult.value?.route_options ?? []
  )

  /**
   * 暴露场。
   *
   * 案例数据里的暴露场是**场景级**的：`run.result.pollution_field` 只留元数据
   * （源、峰值、风场、假设），采样点在 `scenario.pollution_grid`。
   * 两者要合起来才是一份完整的场——只取其中一个都会画不出图。
   */
  const pollutionField = computed<TravelPollutionField | null>(() => {
    if (live.value) return liveResult.value?.result?.pollution_field ?? null

    const meta = currentResult.value?.pollution_field ?? null
    const grid = currentScenario.value?.pollution_grid ?? meta?.grid ?? []
    if (!meta) return null
    if (meta.grid?.length === grid.length) return meta
    return { ...meta, grid }
  })

  const pollutionAdvice = computed<TravelPollutionAdvice | null>(
    () => currentResult.value?.pollution_advice ?? null
  )

  /**
   * 当前生效的起终点。
   *
   * **自定义点优先**，而不是"结果优先"：在图上点选时，如果先看 currentResult，
   * 标记会一直挂在上一趟行程的端点上，直到规划返回才跳过去——
   * 用户点完看不到任何反馈，会以为没点上。
   */
  const origin = computed<TravelPoint | null>(
    () => customOrigin.value ?? currentResult.value?.origin ?? null
  )
  const destination = computed<TravelPoint | null>(
    () => customDestination.value ?? currentResult.value?.destination ?? null
  )

  // ── 气象 ────────────────────────────────────────────────────────────────

  /**
   * 该向气象接口查哪个点、哪个时段。
   *
   * 与出行 Agent 内部一致：**取起点坐标 + 本次任务的时间窗**
   * （见 agents/travel_agent.py 里调用 meteorology_run 的参数），
   * 这样两边算出来的是同一份天气，不会出现"指标条和评分口径对不上"。
   */
  const meteorologyRequest = computed<MeteorologyAnalyzeTask | null>(() => {
    const point = origin.value
    const result = currentResult.value
    const start = result?.start_time ?? currentScenario.value?.start_time
    const end = result?.end_time ?? currentScenario.value?.end_time
    if (!point || !start || !end) return null

    const name = point.name || '自定义点'
    return {
      task_id: `UI_METEO_${point.lat.toFixed(5)}_${point.lon.toFixed(5)}`.replace(/[^\w]/g, '_'),
      station_id: name,
      station_name: name,
      lat: point.lat,
      lon: point.lon,
      start_time: start,
      end_time: end
    }
  })

  const meteorologyRequestKey = computed(() => {
    const task = meteorologyRequest.value
    return task
      ? `${task.lat.toFixed(5)}|${task.lon.toFixed(5)}|${task.start_time}|${task.end_time}`
      : ''
  })

  /** 页面统一读这个，不用再判断"这份数是接口来的还是出行结果带的" */
  const weather = computed<WeatherView>(() => {
    // 实时规划优先用**本次结果**里的 3 站合成气象 ——
    // meteorology 接口快照是旺角单点的，而规划用的是全部路边站的合成场，
    // 指标条若还读旧快照就会出现"浮层说 84.9%、指标条显示 84.75%"的对不上。
    const liveEmbedded = live.value ? liveResult.value?.result?.weather_summary : null
    if (liveEmbedded) {
      return {
        mean_humidity: liveEmbedded.mean_humidity ?? null,
        mean_temperature: liveEmbedded.mean_temperature ?? null,
        mean_wind_speed: liveEmbedded.mean_wind_speed ?? null,
        total_precipitation: liveEmbedded.total_precipitation ?? null,
        dominant_wind_direction: liveEmbedded.dominant_wind_direction ?? null,
        dominant_wind_direction_label: liveEmbedded.dominant_wind_direction_label ?? null,
        downwind_direction: liveEmbedded.downwind_direction ?? null,
        source: 'embedded'
      }
    }

    const fromApi = meteorology.value?.result
    if (meteorology.value?.source === 'api' && fromApi) {
      return {
        mean_humidity: fromApi.mean_humidity,
        mean_temperature: fromApi.mean_temperature,
        mean_wind_speed: fromApi.mean_wind_speed,
        total_precipitation: fromApi.total_precipitation,
        dominant_wind_direction: fromApi.dominant_wind_direction,
        dominant_wind_direction_label: fromApi.dominant_wind_direction_label,
        downwind_direction: fromApi.downwind_direction,
        source: 'api'
      }
    }


    return {
      mean_humidity: null,
      mean_temperature: null,
      mean_wind_speed: null,
      total_precipitation: null,
      dominant_wind_direction: null,
      dominant_wind_direction_label: null,
      downwind_direction: null,
      source: 'none'
    }
  })

  /**
   * 调气象接口。失败不抛：气象拿不到时出行结论仍可用，
   * 退回出行结果里内嵌的那份并把 error 记下来，界面上如实标注。
   */
  async function loadMeteorology(): Promise<void> {
    const task = meteorologyRequest.value
    if (!task) {
      meteorology.value = {
        source: 'unavailable',
        result: null,
        evidence: [],
        confidence: 0,
        dataQuality: null,
        request: null,
        error: '缺少起终点或时间窗，无法请求气象数据',
        loading: false,
        fetchedAt: ''
      }
      return
    }

    // 保留上一次的 result，指标条在刷新期间不会闪空
    const previous = meteorology.value
    meteorology.value = {
      source: previous?.source === 'api' ? 'api' : 'embedded',
      result: previous?.result ?? null,
      evidence: previous?.evidence ?? [],
      confidence: previous?.confidence ?? 0,
      dataQuality: previous?.dataQuality ?? null,
      request: task,
      error: null,
      loading: true,
      fetchedAt: previous?.fetchedAt ?? ''
    }

    try {
      const response = await analyzeMeteorology(task)
      const payload = response.result
      if (payload?.status !== 'success' || !payload.result) {
        throw new Error(payload?.message || `气象分析返回 status=${payload?.status ?? 'unknown'}`)
      }
      meteorology.value = {
        source: 'api',
        result: payload.result as MeteorologyResult,
        evidence: payload.evidence ?? [],
        confidence: payload.confidence ?? 0,
        dataQuality: (payload.data_quality as MeteorologyDataQuality) ?? null,
        request: task,
        error: null,
        loading: false,
        fetchedAt: new Date().toISOString()
      }
    } catch (err: any) {
      meteorology.value = {
        source: currentResult.value?.weather_summary ? 'embedded' : 'unavailable',
        result: null,
        evidence: [],
        confidence: 0,
        dataQuality: null,
        request: task,
        error: err?.message || '气象接口调用失败',
        loading: false,
        fetchedAt: ''
      }
    }
  }

  // 起终点或时间窗一变就重新取气象；同一个组合不重复请求
  watch(meteorologyRequestKey, key => {
    if (!key || key === meteorologyKey) return
    meteorologyKey = key
    void loadMeteorology()
  })

  const highlightedRoute = computed<TravelRouteOption | null>(() => {
    const list = routeOptions.value
    if (!list.length) return null
    if (selectedRouteId.value) {
      const picked = list.find(item => item.route_id === selectedRouteId.value)
      if (picked) return picked
    }
    return list.find(item => item.recommended) ?? list[0]
  })

  /** 案例数据中的画像选项：连说明一起带出来，供卡片式选择器展示 */
  const profileOptions = computed<TravelProfileOption[]>(() => {
    const runs = currentCase.value?.runs ?? {}
    return Object.entries(runs).map(([id, run]) => {
      const profile = run.result?.travel_profile
      return {
        id,
        name: profile?.profile_name ?? id,
        description: profile?.description ?? '',
        preferredModes: profile?.preferred_modes ?? [],
        maxWalkKm: profile?.max_walk_km ?? null,
        timeFlexibilityMinutes: profile?.time_flexibility_minutes ?? null,
        pollutionExchangeMinutes: profile?.pollution_exchange_minutes ?? null
      }
    })
  })

  /**
   * 案例数据来源。
   * backend  = 后端读的 examples/travel_case_output.json（正常路径）
   * snapshot = 后端没起来，退到 public/ 下的同源快照
   */
  const caseSource = ref<'backend' | 'snapshot' | 'none'>('none')

  /** 应用一份案例数据，并把场景 / 行程 / 画像的选择落到有效值上 */
  function applyCaseOutput(data: TravelCaseOutput, source: 'backend' | 'snapshot') {
    caseOutput.value = data
    caseSource.value = source

    if (!data.scenarios.length) {
      error.value = '案例数据里没有任何场景'
      return
    }
    if (!data.scenarios.some(item => item.scenario_id === scenarioId.value)) {
      scenarioId.value = data.scenarios[0].scenario_id
    }
    if (!currentScenario.value?.cases.some(item => item.case_id === caseId.value)) {
      caseId.value = currentScenario.value?.cases[0]?.case_id ?? ''
    }
    const runs = currentCase.value?.runs ?? {}
    if (!(profileId.value in runs)) {
      profileId.value = Object.keys(runs)[0] ?? 'commuter'
    }

    // 顶部查询栏的时间窗跟着案例走：案例本身带着 start_time / end_time，
    // 不读出来的话默认值会和案例对不上（页面上显示 08:00–11:00，实际查别的时段）。
    const scenario = currentScenario.value
    const window = currentRun.value?.result ?? scenario
    const start = window?.start_time
    const end = window?.end_time
    if (start && end) {
      const [date, startClock] = start.split(' ')
      const [, endClock] = end.split(' ')
      departDate.value = date
      departStart.value = startClock.slice(0, 5)
      departEnd.value = endClock.slice(0, 5)
    }
  }

  /** EPD 监测站数（路边 + 一般，均有坐标）。加载浮层展示污染输入范围用。 */
  const epdStationCount = ref(0)

  /** 从站点接口取一次 EPD 站数；失败就保持 0，浮层回退到不带数字的表述 */
  async function loadStationCount(): Promise<void> {
    if (epdStationCount.value) return
    try {
      const response = await fetchMeteorologyStations()
      epdStationCount.value = response.stations.filter(
        item => item.lat !== null && item.lon !== null
      ).length
    } catch {
      epdStationCount.value = 0
    }
  }

  /**
   * 载入案例演示数据。
   *
   * 优先从后端取——后端直接读 examples/travel_case_output.json，
   * 也就是 run_travel_case.py 写出来的那份完整结果（全部候选路线、
   * 全量暴露场网格）。只有后端不可达时才退到 public/ 下的离线快照，
   * 并在界面上如实标注来源，不做静默降级。
   */
  async function loadCase() {
    void loadStationCount()
    loading.value = true
    error.value = null
    try {
      const data = await fetchTravelCase()
      applyCaseOutput(data, 'backend')
    } catch (backendError: any) {
      try {
        const response = await fetch(`${import.meta.env.BASE_URL}travel-case.snapshot.json`, {
          cache: 'no-store'
        })
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        applyCaseOutput((await response.json()) as TravelCaseOutput, 'snapshot')
      } catch {
        caseSource.value = 'none'
        error.value =
          backendError?.message ||
          '未连接到后端服务，无法读取出行案例数据（需要 api/app.py 已启动）'
        throw backendError
      }
    } finally {
      loading.value = false
    }
  }

  function selectScenario(id: string) {
    scenarioId.value = id
    caseId.value = currentScenario.value?.cases[0]?.case_id ?? ''
    selectedRouteId.value = ''
    const runs = currentCase.value?.runs ?? {}
    if (!(profileId.value in runs)) {
      profileId.value = Object.keys(runs)[0] ?? profileId.value
    }
    live.value = false
  }

  function selectCase(id: string) {
    caseId.value = id
    selectedRouteId.value = ''
    live.value = false
    const runs = currentCase.value?.runs ?? {}
    if (!(profileId.value in runs)) {
      profileId.value = Object.keys(runs)[0] ?? profileId.value
    }
  }

  function selectProfile(id: string) {
    profileId.value = id
    selectedRouteId.value = ''
    live.value = false
  }

  /**
   * 选中某条候选路线。
   * 不做"再点一次取消"：地图上的高亮和右侧卡片是同一个选择，
   * 取消掉会变成"两边都指不出当前在看哪条"，反而更糊涂。
   */
  function selectRoute(id: string) {
    selectedRouteId.value = id
  }

  /** 在地图上点选起点 / 终点 */
  function setCustomPoint(role: 'origin' | 'destination', point: TravelPoint) {
    if (role === 'origin') customOrigin.value = point
    else customDestination.value = point
  }

  function swapCustomPoints() {
    const origin = customOrigin.value
    customOrigin.value = customDestination.value
    customDestination.value = origin
  }

  function resetCustomPoints() {
    customOrigin.value = null
    customDestination.value = null
    live.value = false
    liveResult.value = null
    selectedRouteId.value = ''
  }

  /**
   * 用当前起终点调用后端实时规划
   * 起点或终点缺失时直接抛错，由页面提示用户先选点。
   *
   * 出行时段来自页面顶部查询栏（`departDate` 等）。请求**不带污染源**：
   * 后端会默认把全部路边监测站（旺角/铜锣湾/中环）都作为污染输入，
   * 三个站的 EPD 逐小时数据一起参与暴露场重建。
   */
  async function planWithCustomPoints(profile?: string) {
    const from = customOrigin.value
    const to = customDestination.value
    if (!from || !to) throw new Error('请先在地图上设定起点和终点')

    planning.value = true
    error.value = null
    try {
      const request: TravelPlanRequest = {
        task_id: `TRAVEL_CUSTOM_${Date.now()}`,
        origin: from,
        destination: to,
        start_time: `${departDate.value} ${departStart.value}:00`,
        end_time: `${departDate.value} ${departEnd.value}:00`,
        travel_profile: profile ?? profileId.value
      }
      const response = await planTravel(request)
      liveResult.value = response
      live.value = true
      selectedRouteId.value = ''
      return response
    } catch (err: any) {
      error.value =
        err?.message ||
        '实时规划失败：请确认后端 /api/travel/plan 已启动（此时仍可浏览案例数据）'
      throw err
    } finally {
      planning.value = false
    }
  }

  function clear() {
    error.value = null
    selectedRouteId.value = ''
    liveResult.value = null
    live.value = false
  }

  return {
    loading,
    planning,
    error,
    caseOutput,
    caseSource,
    scenarioId,
    caseId,
    profileId,
    selectedRouteId,
    customOrigin,
    customDestination,
    departDate,
    departStart,
    departEnd,
    epdStationCount,
    liveResult,
    live,
    meteorology,
    meteorologyRequest,
    weather,
    loadMeteorology,
    scenarios,
    currentScenario,
    scenarioCases,
    currentCase,
    currentRun,
    currentResult,
    routeOptions,
    pollutionField,
    pollutionAdvice,
    origin,
    destination,
    highlightedRoute,
    profileOptions,
    loadCase,
    selectScenario,
    selectCase,
    selectProfile,
    selectRoute,
    setCustomPoint,
    swapCustomPoints,
    resetCustomPoints,
    planWithCustomPoints,
    clear
  }
})
