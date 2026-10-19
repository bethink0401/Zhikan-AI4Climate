import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { fetchAnalysisMeta, runAnalysis } from '@/api/analysis'
import { fetchMeteorologyStations } from '@/api/meteorology'
import type { AnalysisMeta, AnalysisRequest, CoordinatorResponse } from '@/types/analysis'
import type { MeteorologyStation } from '@/types/meteorology'

/**
 * 分析状态管理
 *
 * 页面上的七块内容全部来自后端 `POST /api/analyze` 的真实链路
 * （`agents/coordinator_min.py` 的确定性编排）：
 *
 *   AirQuality → Meteorology → Traffic → Spatial → Source → Decision → Report
 *
 * 各块的数据源：
 * - 空气质量：`data/air_quality.db`（香港环保署 EPICDI 逐小时多污染物浓度）
 * - 气象：Open-Meteo Archive API
 * - 交通：`data/traffic.db`（运输署 ATC 交通统计的车型构成）
 * - 空间：`spatial_agent/data/` 的路网中心线与敏感 POI
 * - 溯源 / 决策 / 报告：由上面四块推导
 *
 * 只有后端完全不可达时，才会退到 `public/analysis-snapshot.json`
 *（某次真实运行的输出快照），并把来源标成 snapshot，让页面显示得出区别。
 * 用户主动点「开始分析」失败时**不降级**——保留上一次结果并报错，
 * 免得看着像"分析成功了"。
 */
export const useAnalysisStore = defineStore('analysis', () => {
  const loading = ref(false)
  const error = ref<string | null>(null)
  const currentTask = ref<AnalysisRequest | null>(null)
  const result = ref<CoordinatorResponse | null>(null)

  /** 这一份结果从哪来 */
  const analysisSource = ref<'api' | 'snapshot' | 'none'>('none')
  const analysisError = ref<string | null>(null)
  const analyzedAt = ref('')

  /** 监测站点登记表 */
  const stations = ref<MeteorologyStation[]>([])
  const stationsSource = ref('')
  const stationsError = ref<string | null>(null)

  /** 各数据源的真实统计数（加载浮层"正在用什么"的依据），进程内只取一次 */
  const meta = ref<AnalysisMeta | null>(null)

  async function loadMeta(): Promise<void> {
    if (meta.value) return
    try {
      meta.value = await fetchAnalysisMeta()
    } catch {
      // 取不到就让浮层回退到不含数字的通用描述，不阻塞分析
      meta.value = null
    }
  }

  /** 拉取监测站点清单 */
  async function loadStations(): Promise<void> {
    try {
      const response = await fetchMeteorologyStations()
      stations.value = response.stations.filter(item => item.lat !== null && item.lon !== null)
      stationsSource.value = response.source
      stationsError.value = null
    } catch (err: any) {
      stations.value = []
      stationsSource.value = ''
      stationsError.value = err?.message || '监测站点清单获取失败'
    }
  }

  /** 默认任务：项目里已验证的旺角路边站早高峰污染事件 */
  function defaultTask(): AnalysisRequest {
    return {
      task_id: 'HK_MK_2023_0800',
      station_name: 'Mong Kok Roadside',
      station_id: 'MK',
      start_time: '2023-08-15 08:00:00',
      end_time: '2023-08-15 11:00:00',
      lat: 22.3227,
      lon: 114.1683
    }
  }

  /** 按指定任务跑整条链路 */
  async function analyze(params: AnalysisRequest): Promise<void> {
    loading.value = true
    error.value = null
    // 任务参数先写进去：加载浮层要立刻显示"正在分析哪个站、哪个时段"，
    // 等响应回来再写就只剩半程能看到
    currentTask.value = params
    try {
      const response = await runAnalysis(params)
      result.value = response
      currentTask.value = response.task ?? params
      analysisSource.value = 'api'
      analysisError.value = null
      analyzedAt.value = new Date().toISOString()
    } catch (err: any) {
      analysisError.value = err?.message || '分析失败'
      error.value = analysisError.value
      throw err
    } finally {
      loading.value = false
    }
  }

  /**
   * 页面首次载入：拿默认任务跑一遍真实链路。
   *
   * 用 in-flight promise 去重 —— 查询栏和页面本体都会在挂载时调它，
   * 不去重的话同一个整链路请求会发两遍（一次要十几秒，代价很大）。
   */
  let inflight: Promise<void> | null = null

  async function loadInitial(): Promise<void> {
    if (inflight) return inflight

    // meta 与站点清单不阻塞主链路，拿到就更新浮层
    void loadMeta()
    if (!stations.value.length) void loadStations()

    inflight = (async () => {
      try {
        await analyze(defaultTask())
      } catch (err: any) {
        // 后端没起来时才退到快照，并如实标注来源
        try {
          const response = await fetch(`${import.meta.env.BASE_URL}analysis-snapshot.json`, {
            cache: 'no-store'
          })
          if (!response.ok) throw new Error(`HTTP ${response.status}`)
          const snapshot = (await response.json()) as CoordinatorResponse
          result.value = snapshot
          currentTask.value = snapshot.task ?? defaultTask()
          analysisSource.value = 'snapshot'
          error.value = err?.message || '未连接到后端服务，当前展示的是离线快照'
        } catch {
          analysisSource.value = 'none'
          error.value = err?.message || '未连接到后端服务，无法获取分析数据'
        }
      }
    })().finally(() => {
      inflight = null
    })

    return inflight
  }

  /**
   * 旧的字段名保留下来：天气传播页用它渲染顶部的数据来源条。
   * 整链路通了就说明气象块也是真取的，两者是同一件事。
   */
  const meteorologySource = computed<'api' | 'mock' | 'none'>(() => {
    if (analysisSource.value === 'api') return 'api'
    if (analysisSource.value === 'none') return 'none'
    return 'mock'
  })
  const meteorologyError = computed(() => analysisError.value)

  function clear(): void {
    loading.value = false
    error.value = null
    currentTask.value = null
    result.value = null
    analysisSource.value = 'none'
    analysisError.value = null
    analyzedAt.value = ''
  }

  return {
    loading,
    error,
    currentTask,
    result,
    analysisSource,
    analysisError,
    analyzedAt,
    stations,
    stationsSource,
    stationsError,
    meta,
    loadMeta,
    meteorologySource,
    meteorologyError,
    defaultTask,
    loadStations,
    loadInitial,
    analyze,
    clear
  }
})
