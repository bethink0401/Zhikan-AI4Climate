<template>
  <section class="tf-control">
    <!-- ① 起终点 -->
    <section class="tf-card tf-points-card">
      <div class="tf-hd">
        <el-icon :size="16" class="tf-hd-icon"><Position /></el-icon>
        <span class="tf-hd-title">起终点</span>
        <div class="tf-hd-right">
          <span class="tf-pill" :class="sourcePill.cls">{{ sourcePill.text }}</span>
        </div>
      </div>

      <div class="tf-body">
        <div v-for="row in pointRows" :key="row.role" class="tf-point-row">
          <span class="tf-point-badge" :class="`is-${row.role}`">{{ row.badge }}</span>
          <div class="tf-point-field">
            <span class="tf-point-name">{{ row.point?.name || '未设定' }}</span>
            <span class="tf-point-coord tf-mono">
              {{ row.point ? `${row.point.lat.toFixed(5)}, ${row.point.lon.toFixed(5)}` : '—' }}
            </span>
            <button
              class="tf-point-pick"
              :class="{ 'is-active': pickRole === row.role }"
              @click="emit('pick', row.role)"
            >
              {{ pickRole === row.role ? '点图中…' : '点选' }}
            </button>
          </div>
        </div>

        <div class="tf-point-actions">
          <button class="tf-btn is-primary" @click="emit('pick-guide')">
            地图自定义选点
          </button>
          <button
            class="tf-btn"
            :class="{ 'is-attention': dirty }"
            :disabled="!canSwap || planning"
            @click="emit('plan')"
          >
            {{ planning ? '计算中…' : '重新规划' }}
          </button>
          <button class="tf-btn" :disabled="!canSwap" @click="emit('swap')">交换起终点</button>
        </div>
      </div>
    </section>

    <!-- ② 出行习惯 -->
    <section class="tf-card tf-profiles-card">
      <div class="tf-hd">
        <el-icon :size="16" class="tf-hd-icon"><User /></el-icon>
        <span class="tf-hd-title">出行习惯</span>
        <div class="tf-hd-right">
          <span class="tf-head-note">选谁在出行，评分口径与推荐结论就按谁的习惯来</span>
        </div>
      </div>

      <div class="tf-body tf-profile-grid">
        <button
          v-for="item in profiles"
          :key="item.id"
          class="tf-profile"
          :class="{ 'is-active': item.id === profileId }"
          :disabled="planning"
          :title="item.description"
          @click="emit('select-profile', item.id)"
        >
          <span class="tf-profile-hd">
            <el-icon :size="16" class="tf-profile-icon">
              <component :is="iconOf(item.id)" />
            </el-icon>
            <span class="tf-profile-name">{{ item.name }}</span>
          </span>
          <span class="tf-profile-desc">{{ item.description || '—' }}</span>
          <span class="tf-profile-meta">
            <span>偏好 {{ modeText(item.preferredModes) }}</span>
            <span v-if="item.maxWalkKm !== null">步行上限 {{ item.maxWalkKm }} km</span>
          </span>
        </button>
      </div>
    </section>

    <p class="tf-hint">
      提示：点「地图自定义选点」后先点起点、再点终点，选完终点自动开始计算；
      也可以直接拖动地图上的「起 / 终」标记微调位置，再点「重新规划」。
    </p>
  </section>
</template>

<script setup lang="ts">
import { computed, type Component } from 'vue'
import {
  Briefcase,
  Camera,
  Odometer,
  Position,
  School,
  Umbrella,
  User
} from '@element-plus/icons-vue'
import { getModeLabel } from '@/utils/travelMapper'
import type { TravelMode, TravelPoint, TravelProfileOption } from '@/types/travel'

/**
 * TravelControlBar - 出行页控制区
 *
 * 只有两件事可控：起终点，以及按谁的习惯来算。
 * 演示行程与对照场景是固定的（进页面就是那一趟），不再做切换控件——
 * 免得把"看结论"变成"先配置"。
 *
 * 出行习惯做成带图标的可选项卡片：光看"通勤族 / 长者"两个词，
 * 用户不知道换了它会改变什么，所以把画像自己的说明摆出来。
 */

interface Props {
  origin: TravelPoint | null
  destination: TravelPoint | null
  canSwap: boolean
  pickRole: 'origin' | 'destination' | null
  live: boolean
  /** 案例数据的来源，用来如实标注页面上这堆数是从哪来的 */
  caseSource: 'backend' | 'snapshot' | 'none'
  /** 正在向后端请求实时规划 */
  planning: boolean
  /** 起终点被拖动过、与当前结果不再一致 */
  dirty: boolean
  profileId: string
  profiles: TravelProfileOption[]
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (event: 'pick', role: 'origin' | 'destination'): void
  (event: 'pick-guide'): void
  (event: 'swap'): void
  (event: 'plan'): void
  (event: 'select-profile', profileId: string): void
}>()

const pointRows = computed(() => [
  { role: 'origin' as const, badge: '起', point: props.origin },
  { role: 'destination' as const, badge: '终', point: props.destination }
])

/** 数据来源标签。三种状态分开写，免得"看着像演示数据"再引起误解 */
const sourcePill = computed(() => {
  if (props.live) return { text: '本趟为实时规划结果', cls: 'is-green' }
  if (props.caseSource === 'backend') return { text: '案例数据 · Agent 输出', cls: 'is-blue' }
  if (props.caseSource === 'snapshot') return { text: '离线快照（后端未连接）', cls: 'is-amber' }
  return { text: '无可用数据', cls: 'is-red' }
})

/**
 * 画像图标。挑的是"这个画像最在意什么"而不是身份本身：
 * 通勤看时间、带娃看孩子、长者怕风雨、跑者看里程配速、游客是观光。
 */
const PROFILE_ICONS: Record<string, Component> = {
  commuter: Briefcase,
  parent_with_child: School,
  elderly: Umbrella,
  runner: Odometer,
  tourist: Camera
}

function iconOf(profileId: string): Component {
  return PROFILE_ICONS[profileId] ?? User
}

function modeText(modes: TravelMode[]): string {
  if (!modes.length) return '不限'
  return modes.map(getModeLabel).join(' · ')
}
</script>

<style scoped>
.tf-control {
  display: grid;
  grid-template-columns: minmax(340px, 430px) minmax(0, 1fr);
  gap: 12px;
  align-items: start;
}

.tf-head-note {
  font-size: 12px;
  font-weight: 400;
  color: var(--tf-text-3);
}

/* ① 起终点 */
.tf-point-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 7px;
}

.tf-point-badge {
  width: 22px;
  height: 22px;
  flex-shrink: 0;
  border-radius: 50%;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
}

.tf-point-badge.is-origin { background: #2f6fed; }
.tf-point-badge.is-destination { background: #e04a5f; }

.tf-point-field {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 10px;
  background: var(--tf-soft);
  border: 1px solid var(--tf-border);
  border-radius: var(--tf-radius-sm);
}

.tf-point-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--tf-text);
  white-space: nowrap;
}

.tf-point-coord {
  flex: 1;
  min-width: 0;
  font-size: 11px;
  color: var(--tf-text-3);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.tf-point-pick {
  flex-shrink: 0;
  padding: 0;
  background: none;
  font-size: 12px;
  color: var(--tf-blue);
  white-space: nowrap;
}

.tf-point-pick.is-active {
  color: var(--tf-amber);
  font-weight: 600;
}

.tf-point-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

/* ② 出行习惯 */
.tf-profile-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

/* 用 flex 而不是 grid：auto-fit 的列数在"项数少于列数"时行为不好猜，
   flex 的 basis + 拉伸能保证一行摆满、摆不下才换行。 */
.tf-profile {
  flex: 1 1 168px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 9px 10px;
  text-align: left;
  background: var(--tf-card);
  border: 1px solid var(--tf-border);
  border-radius: var(--tf-radius-sm);
  transition: all 0.15s;
}

.tf-profile:hover:not(:disabled) {
  border-color: #cfe0fb;
  background: #fafcff;
}

.tf-profile.is-active {
  border-color: var(--tf-blue);
  background: #f5f9ff;
}

.tf-profile:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.tf-profile-hd {
  display: flex;
  align-items: center;
  gap: 6px;
}

.tf-profile-icon {
  color: var(--tf-text-3);
}

.tf-profile.is-active .tf-profile-icon {
  color: var(--tf-blue);
}

.tf-profile-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--tf-text);
}

.tf-profile-desc {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  font-size: 11px;
  line-height: 16px;
  color: var(--tf-text-2);
}

.tf-profile-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 8px;
  font-size: 10px;
  color: var(--tf-text-3);
}

.tf-hint {
  grid-column: 1 / -1;
  margin: 0;
  font-size: 12px;
  color: var(--tf-text-3);
}

@media (max-width: 1180px) {
  .tf-control { grid-template-columns: minmax(0, 1fr); }
}
</style>
