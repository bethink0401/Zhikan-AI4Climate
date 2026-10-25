<template>
  <section class="tf-card tf-rating">
    <div class="tf-hd">
      <el-icon :size="16" class="tf-hd-icon"><DataAnalysis /></el-icon>
      <span class="tf-hd-title">方案综合评级</span>
      <div class="tf-hd-right">
        <span class="tf-pill">{{ routes.length }} 条候选路径</span>
      </div>
    </div>

    <div
      ref="bodyRef"
      class="tf-rating-body"
      @scroll.passive="onScroll"
    >
      <article
        v-for="(route, index) in ordered"
        :key="route.route_id"
        class="tf-rate"
        :class="[`is-${toneOf(route)}`, { 'is-active': route.route_id === activeId }]"
        :title="`点击在地图上高亮：${titleOf(route)}`"
        @click="emit('select-route', route.route_id)"
      >
        <header class="tf-rate-hd">
          <span class="tf-rate-rank" :class="`is-${toneOf(route)}`">{{ index + 1 }}</span>
          <span class="tf-rate-title">{{ titleOf(route) }}</span>
          <span v-if="badgeOf(route).text" class="tf-pill" :class="badgeOf(route).cls">
            {{ badgeOf(route).text }}
          </span>
          <span class="tf-rate-score" :class="`is-${toneOf(route)}`">
            {{ route.score.toFixed(1) }}<em>分</em>
          </span>
        </header>

        <div class="tf-rate-metrics">
          <div v-for="item in metricsOf(route)" :key="item.label" class="tf-rate-metric">
            <span class="tf-rate-metric-label">{{ item.label }}</span>
            <span class="tf-rate-metric-value" :class="item.tone">{{ item.value }}</span>
          </div>
        </div>

        <!-- 推荐方案：结论行 -->
        <footer v-if="toneOf(route) === 'best'" class="tf-rate-foot">
          <span class="tf-rate-dot is-green"></span>
          <span class="tf-rate-note">{{ pathNote(route) }}</span>
          <span v-if="gainOf(route) > 0" class="tf-pill is-green">
            吸入降低 {{ gainOf(route).toFixed(0) }}%
          </span>
        </footer>

        <!-- 普通方案：结论行 -->
        <footer v-else-if="toneOf(route) === 'normal'" class="tf-rate-foot">
          <span class="tf-rate-dot is-gray"></span>
          <span class="tf-rate-note">{{ pathNote(route) }}</span>
          <span v-if="route.route_id === fastestId" class="tf-rate-foot-hint">在途最快</span>
        </footer>

        <!-- 高风险方案：警示条 -->
        <footer v-else class="tf-rate-warn">
          <el-icon :size="13"><WarningFilled /></el-icon>
          <span class="tf-rate-warn-text">{{ warnNote(route) }}</span>
          <span class="tf-rate-warn-cost">+{{ overrunOf(route).toFixed(0) }} min 加权</span>
        </footer>
      </article>

      <el-empty v-if="!routes.length" description="暂无可选路线" :image-size="64" />
    </div>

    <!-- 还有更多候选时压一层渐隐，否则"能滚动"这件事完全看不出来 -->
    <div v-if="showFade" class="tf-rating-fade"></div>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { DataAnalysis, WarningFilled } from '@element-plus/icons-vue'
import { doseReductionPct, intakeRiskLabel } from '@/utils/travelMapper'
import type { TravelRouteOption } from '@/types/travel'

/**
 * RouteRatingPanel - 方案综合评级
 *
 * 每条候选路线一张卡：推荐方案用绿色卡面突出，吸入最高的方案用红色卡面警示，
 * 其余走中性卡面。卡片可点击，用于切换地图上的高亮路线。
 *
 * 面板高度固定，候选项多时在面板内部滚动；只有"还没滚到底"时才压一层渐隐，
 * 免得用户不知道下面还有内容。
 */

interface Props {
  routes: TravelRouteOption[]
  activeId?: string
}

const props = withDefaults(defineProps<Props>(), { activeId: '' })
const emit = defineEmits<{ (event: 'select-route', routeId: string): void }>()

const bodyRef = ref<HTMLElement | null>(null)
const canScroll = ref(false)
const atBottom = ref(false)

function updateScrollState(): void {
  const body = bodyRef.value
  if (!body) return
  const overflow = body.scrollHeight - body.clientHeight > 4
  canScroll.value = overflow
  atBottom.value = !overflow || body.scrollHeight - body.scrollTop - body.clientHeight < 6
}

function onScroll(): void {
  updateScrollState()
}

const showFade = computed(() => canScroll.value && !atBottom.value)

onMounted(() => {
  updateScrollState()
  window.addEventListener('resize', updateScrollState)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', updateScrollState)
})

watch(
  () => props.routes,
  () => {
    void nextTick(updateScrollState)
  }
)

/** 按评分降序，序号与阅读顺序一致 */
const ordered = computed(() => [...props.routes].sort((a, b) => b.score - a.score))

/** 吸入当量最高的方案，用红色卡面提示"这条最脏" */
const worstId = computed(() => {
  const pool = props.routes.filter(item => !item.recommended)
  if (!pool.length) return ''
  return pool.reduce((peak, item) => (item.intake_index > peak.intake_index ? item : peak)).route_id
})

const fastestId = computed(() => {
  if (!props.routes.length) return ''
  return props.routes.reduce((fast, item) =>
    item.duration_minutes < fast.duration_minutes ? item : fast
  ).route_id
})

function toneOf(route: TravelRouteOption): 'best' | 'normal' | 'worst' {
  if (route.recommended) return 'best'
  if (route.route_id === worstId.value) return 'worst'
  return 'normal'
}

/** 标题：方式 + 主要载体，让"哪条路"一眼可辨 */
function titleOf(route: TravelRouteOption): string {
  if (route.transit_detail) {
    return `${route.mode_label}（港铁 ${route.transit_detail.stop_count} 站）`
  }
  const road = route.road_names[0] ?? '无命名道路'
  return `${route.mode_label}（${road}）`
}

function badgeOf(route: TravelRouteOption): { text: string; cls: string } {
  const tone = toneOf(route)
  if (tone === 'best') return { text: '最优解', cls: 'is-green' }
  if (tone === 'worst') return { text: '不推荐', cls: 'is-red' }
  return { text: '', cls: '' }
}

interface RateMetric {
  label: string
  value: string
  tone: string
}

/** 四列指标。第四列随卡片语气切换：推荐看高暴露里程，异常看风险等级，其余看路口 */
function metricsOf(route: TravelRouteOption): RateMetric[] {
  const tone = toneOf(route)
  const base: RateMetric[] = [
    {
      label: '在途时间',
      value: `${route.duration_minutes.toFixed(0)} min`,
      tone: tone === 'best' ? 'tf-tone-green' : ''
    },
    {
      label: '等效耗时',
      value: `${route.utility_minutes.toFixed(0)} min`,
      tone: tone === 'best' ? 'tf-tone-green' : ''
    },
    {
      label: '吸入当量',
      value: `${route.intake_index.toFixed(2)} EU`,
      tone: tone === 'best' ? 'tf-tone-green' : ''
    }
  ]

  if (tone === 'best') {
    base.push({
      label: '高暴露里程',
      value: `${route.exposure.high_exposure_km.toFixed(2)} km`,
      tone: 'tf-tone-green'
    })
  } else if (tone === 'worst') {
    base.push({
      label: '暴露等级',
      value: intakeRiskLabel(route.intake_index),
      tone: 'tf-tone-red'
    })
  } else {
    base.push({
      label: '路口延迟',
      value: `${route.junctions} 处`,
      tone: route.junctions >= 15 ? 'tf-tone-amber' : ''
    })
  }

  return base
}

/**
 * 结论行的路径描述。
 * 公共交通要讲清"哪一段是走过去的"：起终点若不在站上，
 * 接驳段是按步行速度算的，不说明的话会被当成全程坐车。
 */
function pathNote(route: TravelRouteOption): string {
  const detail = route.transit_detail
  if (detail) {
    const linkKm = (detail.access_distance_km ?? 0) + (detail.egress_distance_km ?? 0)
    if (linkKm < 0.05) {
      return `${detail.stop_count} 站直达 · 起终点就在站上，无需接驳`
    }
    const linkMinutes =
      (route.timeline?.access?.minutes ?? 0) + (route.timeline?.egress?.minutes ?? 0)
    return `步行接驳 ${linkKm.toFixed(2)} km（约 ${linkMinutes.toFixed(0)} 分钟）· 轨道 ${detail.stop_count} 站`
  }
  const roads = route.road_names.slice(0, 2).join(' → ')
  return roads ? `主干路径：${roads}` : '主干路径：起终点间最短连通路径'
}

/** 该方案相对吸入最高方案省下的比例 */
function gainOf(route: TravelRouteOption): number {
  return doseReductionPct(props.routes, route.route_id)
}

/** 警示文案：取评分理由的第一句，讲清"为什么别选" */
function warnNote(route: TravelRouteOption): string {
  const first = route.reason.split('；')[0] ?? route.reason
  return first.replace(/。$/, '')
}

/** 超时罚分：等效时间里因为超限多出来的那部分 */
function overrunOf(route: TravelRouteOption): number {
  return route.utility_detail.overrun_minutes + route.utility_detail.walk_overrun_minutes
}
</script>

<style scoped>
.tf-rating {
  position: relative;
  min-width: 0;
  height: var(--tf-main-h);
}

/* 滚到底之前压一层渐隐，提示"下面还有" */
.tf-rating-fade {
  position: absolute;
  left: 1px;
  right: 1px;
  bottom: 1px;
  height: 30px;
  border-radius: 0 0 var(--tf-radius) var(--tf-radius);
  background: linear-gradient(to top, #fff 12%, rgba(255, 255, 255, 0));
  pointer-events: none;
}

/**
 * 候选路线在面板内部滚动，面板本身高度固定。
 * 否则路线一多，整张图跟着被拉长，地图比例就乱了。
 */
.tf-rating-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
}

.tf-rating-body::-webkit-scrollbar {
  width: 6px;
}

.tf-rating-body::-webkit-scrollbar-thumb {
  background: #d5dae3;
  border-radius: 3px;
}

.tf-rating-body::-webkit-scrollbar-thumb:hover {
  background: #b9c0cb;
}

.tf-rating-body::-webkit-scrollbar-track {
  background: transparent;
}

.tf-rate {
  padding: 12px 14px;
  border: 1px solid var(--tf-border);
  border-radius: var(--tf-radius);
  background: var(--tf-card);
  cursor: pointer;
  transition: box-shadow 0.15s, border-color 0.15s;
}

.tf-rate:hover {
  box-shadow: 0 3px 10px rgba(23, 32, 48, 0.08);
}

.tf-rate.is-active {
  box-shadow: 0 0 0 2px rgba(47, 111, 237, 0.4);
}

.tf-rate.is-best {
  border: 1.5px solid var(--tf-green);
  background: #f7fdfa;
}

.tf-rate.is-worst {
  border: 1px solid #f6dee3;
  background: #fef7f8;
}

/* 标题行 */
.tf-rate-hd {
  display: flex;
  align-items: center;
  gap: 8px;
}

.tf-rate-rank {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  font-size: 12px;
  font-weight: 600;
  background: #eef0f4;
  color: var(--tf-text-2);
}

.tf-rate-rank.is-best { background: var(--tf-green); color: #fff; }
.tf-rate-rank.is-worst { background: #fbe3e7; color: var(--tf-red); }

.tf-rate-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--tf-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tf-rate-score {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 26px;
  font-weight: 700;
  line-height: 1;
  color: var(--tf-blue);
  font-variant-numeric: tabular-nums;
}

.tf-rate-score.is-best { color: var(--tf-green); }
.tf-rate-score.is-worst { color: var(--tf-red); }

.tf-rate-score em {
  font-style: normal;
  font-size: 11px;
  font-weight: 500;
  color: var(--tf-text-3);
  margin-left: 2px;
}

/* 四列指标 */
.tf-rate-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  margin-top: 11px;
  padding: 10px 4px;
  background: var(--tf-soft);
  border-radius: var(--tf-radius-sm);
}

.tf-rate-metric {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
}

.tf-rate-metric-label {
  font-size: 11px;
  color: var(--tf-text-3);
  white-space: nowrap;
}

.tf-rate-metric-value {
  font-size: 14px;
  font-weight: 600;
  color: var(--tf-text);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

/* 结论行 */
.tf-rate-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 11px;
}

.tf-rate-dot {
  width: 6px;
  height: 6px;
  flex-shrink: 0;
  border-radius: 50%;
}

.tf-rate-dot.is-green { background: var(--tf-green); }
.tf-rate-dot.is-gray { background: #c7ccd4; }

.tf-rate-note {
  min-width: 0;
  font-size: 12px;
  color: var(--tf-text-2);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.tf-rate-foot .tf-pill,
.tf-rate-foot-hint {
  margin-left: auto;
  flex-shrink: 0;
}

.tf-rate-foot-hint {
  font-size: 12px;
  color: var(--tf-text-3);
}

/* 警示条 */
.tf-rate-warn {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 11px;
  padding: 7px 10px;
  border-radius: var(--tf-radius-sm);
  background: var(--tf-red-soft);
  color: #c0394f;
}

.tf-rate-warn-text {
  min-width: 0;
  font-size: 12px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.tf-rate-warn-cost {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

@media (max-width: 1180px) {
  .tf-rate-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 8px;
  }
}
</style>
