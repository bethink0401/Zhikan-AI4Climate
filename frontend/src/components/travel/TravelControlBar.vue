<template>
  <section class="tf-control">
    <!-- ① 起终点 -->
    <section class="tf-card tf-points-card">
      <div class="tf-hd">
        <el-icon :size="16" class="tf-hd-icon"><Position /></el-icon>
        <span class="tf-hd-title">起终点</span>
      </div>

      <div class="tf-body">
        <div v-for="row in pointRows" :key="row.role" class="tf-point-entry">
          <div class="tf-point-row">
            <span class="tf-point-badge" :class="`is-${row.role}`">{{ row.badge }}</span>
            <div class="tf-point-field">
              <el-autocomplete
                v-model="pointQueries[row.role]"
                class="tf-place-search"
                :placeholder="row.role === 'origin' ? '输入起点' : '输入终点'"
                :fetch-suggestions="suggestPlaces"
                :trigger-on-focus="false"
                :debounce="250"
                :disabled="planning"
                clearable
                @select="(item: PlaceOption) => selectPlace(row.role, item)"
              >
                <template #default="{ item }">
                  <div class="tf-place-option">
                    <strong>{{ item.name }}</strong>
                    <span>{{ item.detail }}</span>
                  </div>
                </template>
              </el-autocomplete>
              <span v-if="row.point && pointQueries[row.role].trim() === row.point.name" class="tf-point-coord tf-mono">
                {{ `${row.point.lat.toFixed(5)}, ${row.point.lon.toFixed(5)}` }}
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
        </div>
        <div v-if="hasUnconfirmedInput" class="tf-place-hint">输入地点后请点击候选项确认。</div>

        <div class="tf-point-actions">
          <button
            class="tf-btn is-primary"
            :class="{ 'is-attention': dirty }"
            :disabled="planning || hasUnconfirmedInput"
            @click="emit('primary-action')"
          >
            {{ planning ? '计算中…' : dirty ? '按已选地点规划' : '地图选点规划' }}
          </button>
          <button class="tf-btn" :disabled="!canSwap || planning || hasUnconfirmedInput" @click="emit('swap')">交换起终点</button>
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
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, watch, type Component } from 'vue'
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
import { searchTravelPlaces, type TravelPlace } from '@/api/travel'
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
  (event: 'swap'): void
  (event: 'primary-action'): void
  (event: 'select-profile', profileId: string): void
  (event: 'select-point', payload: { role: 'origin' | 'destination'; point: TravelPoint }): void
}>()

type PlaceOption = TravelPlace & { value: string }
const pointQueries = reactive({ origin: props.origin?.name || '', destination: props.destination?.name || '' })
watch(() => props.origin?.name, name => { pointQueries.origin = name || '' })
watch(() => props.destination?.name, name => { pointQueries.destination = name || '' })
const hasUnconfirmedInput = computed(() => (
  pointQueries.origin.trim() !== (props.origin?.name || '')
  || pointQueries.destination.trim() !== (props.destination?.name || '')
))

async function suggestPlaces(query: string, done: (items: PlaceOption[]) => void): Promise<void> {
  if (query.trim().length < 2) { done([]); return }
  try {
    const response = await searchTravelPlaces(query.trim())
    done(response.places.map(place => ({ ...place, value: place.name })))
  } catch {
    done([])
  }
}

function selectPlace(role: 'origin' | 'destination', item: PlaceOption): void {
  pointQueries[role] = item.name
  emit('select-point', { role, point: { name: item.name, lat: item.lat, lon: item.lon } })
}

const pointRows = computed(() => [
  { role: 'origin' as const, badge: '起', point: props.origin },
  { role: 'destination' as const, badge: '终', point: props.destination }
])

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
  /* 两张卡片等高：拉伸到同一行高度，矮的那张由内部 flex 撑开 */
  align-items: stretch;
}

/* 整块控制区收矮一档：行高由更高的那张卡决定，所以两边留白要一起收，
   只收紧一侧只是把另一侧的空隙拉大。 */
.tf-control .tf-card .tf-hd { padding: 9px 14px; }
.tf-control .tf-card .tf-body { padding: 11px 14px; }

.tf-head-note {
  font-size: 12px;
  font-weight: 400;
  color: var(--tf-text-3);
}

/* ① 起终点 */
.tf-points-card .tf-body {
  display: flex;
  flex-direction: column;
}

.tf-point-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 3px;
}

.tf-point-entry { margin-bottom: 6px; }
.tf-place-search { flex: 1; min-width: 0; }
.tf-place-search :deep(.el-input__wrapper) { background: transparent; box-shadow: none; padding: 0; }
.tf-place-search :deep(.el-input__inner) { color: var(--tf-text); font-size: 13px; font-weight: 600; }
.tf-place-option { display: flex; justify-content: space-between; gap: 12px; }
.tf-place-option span { color: var(--tf-text-3); font-size: 11px; }
.tf-place-hint { margin: 0 0 8px 30px; color: var(--tf-amber); font-size: 11px; }

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
  height: 32px;
  padding: 0 10px;
  background: var(--tf-soft);
  border: 1px solid var(--tf-border);
  border-radius: var(--tf-radius-sm);
}

.tf-point-field:focus-within { border-color: var(--tf-blue); }

.tf-point-coord {
  flex-shrink: 0;
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
  /* 贴到卡片底部，与右侧卡片下沿齐平 */
  margin-top: auto;
  padding-top: 8px;
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
  gap: 4px;
  padding: 8px 9px;
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
  line-height: 15px;
  color: var(--tf-text-2);
}

.tf-profile-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 2px 8px;
  font-size: 10px;
  color: var(--tf-text-3);
}

@media (max-width: 1180px) {
  .tf-control { grid-template-columns: minmax(0, 1fr); }
}
</style>
