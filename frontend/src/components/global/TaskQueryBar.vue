<template>
  <div class="task-query-bar">
    <div class="query-form">
      <!-- 出行页不选站点：规划默认把 3 个路边监测站**都**作为污染输入 -->
      <div v-if="!isTravel" class="form-item">
        <label class="form-label">监测站点</label>
        <el-select
          v-model="selectedStation"
          placeholder="请选择站点"
          size="default"
          style="width: 280px"
          :loading="analysisStore.loading"
          :empty-text="analysisStore.stationsError ? '站点清单获取失败' : '加载中…'"
        >
          <el-option
            v-for="station in analysisStore.stations"
            :key="station.station_id"
            :label="stationLabel(station)"
            :value="station.station_id"
          />
        </el-select>
      </div>

      <div class="form-item">
        <label class="form-label">{{ isTravel ? '出行日期' : '分析日期' }}</label>
        <el-date-picker
          v-model="selectedDate"
          type="date"
          placeholder="选择日期"
          format="YYYY-MM-DD"
          value-format="YYYY-MM-DD"
          size="default"
          style="width: 160px"
        />
      </div>

      <div class="form-item">
        <label class="form-label">{{ isTravel ? '出行时段' : '时间范围' }}</label>
        <div class="time-range">
          <el-time-select
            v-model="startTime"
            placeholder="开始时间"
            start="00:00"
            step="01:00"
            end="23:00"
            size="default"
            style="width: 120px"
          />
          <span class="time-separator">—</span>
          <el-time-select
            v-model="endTime"
            placeholder="结束时间"
            start="00:00"
            step="01:00"
            end="23:59"
            :min-time="startTime"
            size="default"
            style="width: 120px"
          />
        </div>
      </div>

      <el-button
        type="primary"
        :loading="busy"
        @click="handleAnalyze"
        size="default"
      >
        {{ isTravel ? '开始规划' : '开始分析' }}
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useAnalysisStore } from '@/stores/analysis'
import type { AnalysisRequest } from '@/types/analysis'
import type { MeteorologyStation } from '@/types/meteorology'
import { canonicalStationName, sameAnalysisScope, toAnalysisScope } from '@/utils/analysisScope'

/**
 * 顶部查询栏：选择日期 + 时间范围，然后发起分析。
 *
 * 两个使用口径：
 * - `scope="analysis"`（默认）—— 综合态势 / 污染溯源 / 天气传播三页在用，
 *   带监测站点选择，点「开始分析」跑 Coordinator 整链路。
 * - `scope="travel"` —— 出行建议页在用。**没有站点选择**：规划默认把全部
 *   路边监测站（旺角/铜锣湾/中环）都作为污染输入；日期与时段是出行时段，
 *   点「开始规划」交给页面去重新规划路线。
 *
 * 刻意不 import 出行 store：这个组件被三个分析页共用，引进来会把出行
 * 相关的代码带进它们的加载链路 —— 那些页面根本不会跑出行算法，
 * 没必要连代码一起加载（出行页组件本身是路由懒加载的）。
 */

interface Props {
  scope?: 'analysis' | 'travel'
  /** travel 模式的初始日期 / 时段（出行 store 里的值由页面传入） */
  initialDate?: string
  initialStart?: string
  initialEnd?: string
}

const props = withDefaults(defineProps<Props>(), {
  scope: 'analysis',
  initialDate: '',
  initialStart: '',
  initialEnd: ''
})

const emit = defineEmits<{
  (event: 'analyze', payload: { date: string; startTime: string; endTime: string }): void
}>()

const analysisStore = useAnalysisStore()

const isTravel = computed(() => props.scope === 'travel')
// 自动分析走 store，本地 loading 只覆盖手动点击，两者合一按钮态才完整
const busy = computed(() => loading.value || analysisStore.loading)

// 表单数据
const selectedStation = ref('')
const selectedDate = ref(props.initialDate)
const startTime = ref(props.initialStart)
const endTime = ref(props.initialEnd)
const loading = ref(false)

/** 站点显示名：中文名优先，后面跟登记名，避免只看到一个英文名 */
function stationLabel(station: MeteorologyStation): string {
  const zh = station.station_name_zh
  return zh && zh !== station.station_name
    ? `${zh}（${station.station_name}）`
    : station.station_name || station.station_id
}

/** 站点清单里没有目标站点时退回第一个，避免下拉框显示空白 */
function resolveStation(wantedId: string, wantedName: string): string {
  const canonicalWantedName = canonicalStationName(wantedName)
  const matched = analysisStore.stations.find(item => (
    item.station_id === wantedId
    || (canonicalWantedName && canonicalStationName(item.station_name) === canonicalWantedName)
  ))
  if (matched) return matched.station_id
  if (analysisStore.stations.some(item => item.station_id === selectedStation.value)) {
    return selectedStation.value
  }
  return analysisStore.stations[0]?.station_id ?? ''
}

/** 从当前任务或默认任务里取一个可用的初始值填进表单 */
function initForm() {
  if (isTravel.value) {
    if (!selectedDate.value && props.initialDate) selectedDate.value = props.initialDate
    if (!startTime.value && props.initialStart) startTime.value = props.initialStart
    if (!endTime.value && props.initialEnd) endTime.value = props.initialEnd
    return
  }

  const task = analysisStore.pendingTask ?? analysisStore.currentTask ?? analysisStore.defaultTask()
  selectedStation.value = resolveStation(task.station_id, task.station_name)

  if (task.start_time) {
    const [date, time] = task.start_time.split(' ')
    if (!selectedDate.value) selectedDate.value = date
    if (!startTime.value) startTime.value = time.substring(0, 5)
  }
  if (task.end_time) {
    const [, time] = task.end_time.split(' ')
    if (!endTime.value) endTime.value = time.substring(0, 5)
  }
}

onMounted(async () => {
  // 站点清单必须先有，否则选择器是空的（只有分析模式需要）
  if (!isTravel.value && !analysisStore.stations.length) {
    await analysisStore.loadStations()
  }
  initForm()
})

watch(() => analysisStore.stations, initForm)

/* ===== 自动重新分析 =====
 * 分析模式下，站点 / 日期 / 时间一旦变化，去抖后自动重跑整条链路。
 * 整链路一次十几秒，所以用 1.2s 去抖把连续的快速切换合并成最后一次；
 * 手动点「开始分析」仍可用，点击会取消还没触发的自动任务。
 * 出行页不自动：规划还要带起终点和出行习惯，交给页面自己处理。 */
const AUTO_ANALYZE_DELAY_MS = 1200
let autoAnalyzeTimer: number | null = null

/** 表单当前对应的范围是否已经在分析、或已有验证过的结果 */
function sameScopeInFlightOrVerified(): boolean {
  const scope = toAnalysisScope(analysisStore.pendingTask)
  if (!scope) return false
  if (analysisStore.loading
    && sameAnalysisScope(scope, toAnalysisScope(analysisStore.inflightTask))) {
    return true
  }
  return sameAnalysisScope(scope, toAnalysisScope(analysisStore.currentTask))
}

function scheduleAutoAnalyze(): void {
  if (isTravel.value) return
  if (!selectedDate.value || !startTime.value || !endTime.value) return
  // 该范围已经在分析、或已验证结果就是这个范围时，无需重跑
  if (sameScopeInFlightOrVerified()) return

  if (autoAnalyzeTimer !== null) window.clearTimeout(autoAnalyzeTimer)
  autoAnalyzeTimer = window.setTimeout(() => {
    autoAnalyzeTimer = null
    void runAutoAnalyze()
  }, AUTO_ANALYZE_DELAY_MS)
}

async function runAutoAnalyze(): Promise<void> {
  const station = analysisStore.stations.find(item => item.station_id === selectedStation.value)
  if (!station) return
  if (sameScopeInFlightOrVerified()) return
  if (!selectedDate.value || !startTime.value || !endTime.value) return
  if (endTime.value <= startTime.value) {
    ElMessage.warning('结束时间需要晚于开始时间')
    return
  }
  try {
    await analysisStore.analyze(buildRequest(station))
    if (analysisStore.analysisSource === 'api') {
      ElMessage.success(`已按 ${stationLabel(station)} 自动重新分析完成`)
    }
  } catch {
    // 失败原因已写入 store，由页面错误态展示；用户可再切换或手动重试
  }
}

watch([selectedStation, selectedDate, startTime, endTime], () => {
  syncPendingScope()
  scheduleAutoAnalyze()
})

// 路由切走时取消还没触发的自动分析，别用旧页面的范围去打新页面
onUnmounted(() => {
  if (autoAnalyzeTimer !== null) {
    window.clearTimeout(autoAnalyzeTimer)
    autoAnalyzeTimer = null
  }
})

/** 按当前表单组装分析请求 */
function buildRequest(station: MeteorologyStation): AnalysisRequest {
  const [year, month, day] = selectedDate.value.split('-')
  return {
    task_id: `UI_${station.station_id}_${year}${month}${day}_${startTime.value.replace(':', '')}`,
    station_id: station.station_id,
    station_name: station.station_name,
    lat: station.lat as number,
    lon: station.lon as number,
    start_time: `${selectedDate.value} ${startTime.value}:00`,
    end_time: `${selectedDate.value} ${endTime.value}:00`
  }
}

/** Publish UI selection separately from the last verified analysis result. */
function syncPendingScope(): void {
  if (isTravel.value || !selectedDate.value || !startTime.value || !endTime.value) return
  const station = analysisStore.stations.find(item => item.station_id === selectedStation.value)
  if (!station || station.lat === null || station.lon === null) return
  analysisStore.setPendingTask(buildRequest(station))
}

/** 表单校验 */
function validate(): boolean {
  if (
    !isTravel.value &&
    !analysisStore.stations.find(item => item.station_id === selectedStation.value)
  ) {
    ElMessage.warning('监测站点清单还没拿到，请确认后端 /api/meteorology/stations 可用')
    return false
  }
  if (!selectedDate.value || !startTime.value || !endTime.value) {
    ElMessage.warning('请完整填写日期和时间范围')
    return false
  }
  if (endTime.value <= startTime.value) {
    ElMessage.warning('结束时间需要晚于开始时间')
    return false
  }
  return true
}

async function handleAnalyze() {
  // 手动触发时取消还没到点的自动任务，避免同一范围跑两遍
  if (autoAnalyzeTimer !== null) {
    window.clearTimeout(autoAnalyzeTimer)
    autoAnalyzeTimer = null
  }
  if (!validate()) return

  // 出行页只把选择交出去，请求由页面发起（还要带上起终点与出行习惯）
  if (isTravel.value) {
    emit('analyze', {
      date: selectedDate.value,
      startTime: startTime.value,
      endTime: endTime.value
    })
    return
  }

  const station = analysisStore.stations.find(item => item.station_id === selectedStation.value)
  if (!station) return

  loading.value = true
  try {
    await analysisStore.analyze(buildRequest(station))
    if (analysisStore.analysisSource === 'api') {
      ElMessage.success(`已按 ${stationLabel(station)} 跑完整条分析链路`)
    } else {
      ElMessage.error(analysisStore.analysisError || '分析失败')
    }
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.task-query-bar {
  background: var(--panel-bg);
  border-bottom: 1px solid var(--border-color);
  padding: 16px 24px;
}

.query-form {
  display: flex;
  align-items: center;
  gap: 24px;
  flex-wrap: wrap;
}

.form-item {
  display: flex;
  align-items: center;
  gap: 8px;
}

.form-label {
  font-size: 14px;
  color: var(--text-secondary);
  white-space: nowrap;
  font-weight: 500;
}

.time-range {
  display: flex;
  align-items: center;
  gap: 8px;
}

.time-separator {
  color: var(--text-tertiary);
}
</style>
