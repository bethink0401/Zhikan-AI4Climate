<template>
  <div class="restriction-decision-panel">
    <!-- 标题栏 -->
    <div class="panel-header">
      <div class="header-titles">
        <span class="panel-title">区域交通治理与智能调度决策规程</span>
        <span class="panel-subtitle">Intelligent Traffic Response Program</span>
      </div>
      <span class="level-tag" :class="`tone-${toneClass}`">
        <i class="tag-dot"></i>{{ decisionLevelLabel }}
      </span>
    </div>

    <!-- 空状态 -->
    <div v-if="isEmpty" class="empty-state">
      <el-empty description="暂无决策数据" :image-size="80" />
    </div>

    <div v-else class="panel-body">

      <!-- 时段 + 范围信息条 -->
      <div class="info-row">
        <div class="info-item">
          <span class="info-icon"><el-icon><Clock /></el-icon></span>
          <div class="info-texts">
            <span class="info-label">执法时段</span>
            <span class="info-value">{{ shortTime }}</span>
          </div>
        </div>
        <div class="info-item">
          <span class="info-icon"><el-icon><Location /></el-icon></span>
          <div class="info-texts">
            <span class="info-label">影响范围</span>
            <span class="info-value">{{ suggestedAreaLabel }}</span>
          </div>
        </div>
      </div>

      <!-- 重点管控车型 -->
      <div class="section">
        <div class="section-title">重点管控车型</div>
        <div class="vehicle-tags">
          <span
            v-for="(v, i) in vehicleFocus"
            :key="v"
            class="vehicle-tag"
            :class="{ 'is-primary': i === 0 }"
          >
            <template v-if="i === 0">优先管控 · {{ v }}</template>
            <template v-else>{{ v }}</template>
          </span>
          <span v-if="vehicleFocus.length === 0" class="no-data">暂无数据</span>
        </div>
      </div>

      <!-- 执行措施建议 -->
      <div class="section">
        <div class="section-title">
          执行措施建议
          <span class="section-hint">按优先级排布</span>
        </div>
        <ol class="action-list">
          <li v-for="(action, i) in actions" :key="i" class="action-item">
            <span class="action-badge">P{{ i + 1 }}</span>
            <span class="action-text">{{ action }}</span>
          </li>
          <li v-if="actions.length === 0" class="no-data">暂无行动建议</li>
        </ol>
      </div>

      <!-- 建议干预干道清单 -->
      <div class="section">
        <div class="section-title">
          建议干预干道清单
          <span class="section-hint">共 {{ roadRows.length }} 条主要干道</span>
        </div>
        <div v-if="roadRows.length > 0" class="road-table">
          <div class="road-head">
            <span class="col-name">干道</span>
            <span class="col-num">上风向评分</span>
            <span class="col-num">距站距离</span>
          </div>
          <div class="road-body">
            <div v-for="row in roadRows" :key="row.name" class="road-row">
              <span class="col-name" :title="row.name">{{ row.name }}</span>
              <span class="col-num" :class="{ 'num-strong': row.score !== null }">
                {{ row.score !== null ? row.score.toFixed(2) : '—' }}
              </span>
              <span class="col-num">
                {{ row.distance !== null ? row.distance.toFixed(2) + ' km' : '—' }}
              </span>
            </div>
          </div>
        </div>
        <span v-else class="no-data">暂无干道清单</span>
      </div>

      <!-- 复查指标 -->
      <div v-if="reviewIndicators.length > 0" class="section review-section">
        <div class="section-title">效果复查指标</div>
        <ul class="review-list">
          <li v-for="(item, i) in reviewIndicators" :key="i" class="review-item">
            {{ item }}
          </li>
        </ul>
      </div>

    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Clock, Location } from '@element-plus/icons-vue'

defineOptions({ name: 'RestrictionDecisionPanel' })

// ---------- 干道清单行（由视图层把决策道路与空间上风向道路匹配后传入） ----------
export interface RoadRow {
  name: string
  /** 上风向匹配评分 0-1，匹配不到为 null */
  score: number | null
  /** 距站点距离 km，匹配不到为 null */
  distance: number | null
}

// ---------- Props ----------
const props = defineProps<{
  decisionLevel: 'low' | 'medium' | 'high' | 'very_high'
  suggestedArea: string
  suggestedTime: string
  vehicleFocus: string[]
  actions: string[]
  reviewIndicators: string[]
  /** 干道清单（含空间匹配出的评分/距离） */
  roadRows?: RoadRow[]
}>()

// ---------- 派生数据 ----------
const isEmpty = computed(() =>
  !props.decisionLevel && props.actions.length === 0
)

const roadRows = computed<RoadRow[]>(() => props.roadRows ?? [])

// 响应等级 → 标签：高级响应 LEVEL-1 / 中级 LEVEL-2 / 低级 LEVEL-3
const decisionLevelLabel = computed(() => {
  const map: Record<string, string> = {
    low: '低级响应 LEVEL-3',
    medium: '中级响应 LEVEL-2',
    high: '高级响应 LEVEL-1',
    very_high: '高级响应 LEVEL-1'
  }
  return map[props.decisionLevel] ?? props.decisionLevel
})

// very_high 与 high 共用同一套徽章配色
const toneClass = computed(() =>
  props.decisionLevel === 'very_high' ? 'high' : props.decisionLevel
)

/** suggested_time 形如 "2023-08-15 08:00:00 至 2023-08-15 11:00:00"，取时间部分 */
const shortTime = computed(() => {
  const raw = props.suggestedTime || ''
  const times = raw.match(/\d{2}:\d{2}/g)
  if (times && times.length >= 2) return `${times[0]} - ${times[1]}`
  return raw || '—'
})

/** suggested_area 英文 → 简短中文描述 */
const suggestedAreaLabel = computed(() => {
  const raw = props.suggestedArea || ''
  if (raw.includes('downwind')) return '下风向 3 km 扇区'
  if (raw.includes('km')) return raw
  return raw || '—'
})
</script>

<style scoped>
.restriction-decision-panel {
  background: var(--panel-bg);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  height: 100%;
  box-sizing: border-box;
  overflow: hidden;
}

/* ===== 标题栏 ===== */
.panel-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-color);
  flex-shrink: 0;
}

.header-titles {
  min-width: 0;
}

.panel-title {
  display: block;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.panel-subtitle {
  display: block;
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
}

.level-tag {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  padding: 6px 10px;
  border-radius: 4px;
  white-space: nowrap;
}

.tag-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.tone-low    { color: #15803d; background: rgba(34, 197, 94, 0.12); }
.tone-medium { color: #b45309; background: rgba(245, 158, 11, 0.14); }
.tone-high   { color: #c22f2f; background: rgba(239, 68, 68, 0.1); }

/* ===== 空状态 ===== */
.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
}

/* ===== 面板主体（可滚动） ===== */
.panel-body {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-right: 4px;
  min-height: 0;
}

.panel-body::-webkit-scrollbar {
  width: 4px;
}
.panel-body::-webkit-scrollbar-thumb {
  background: var(--border-color);
  border-radius: 2px;
}

/* ===== 信息条（时段 + 范围） ===== */
.info-row {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.info-item {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--bg-page);
  border-radius: 8px;
  padding: 10px 12px;
  flex: 1;
  min-width: 150px;
}

.info-icon {
  display: flex;
  align-items: center;
  font-size: 15px;
  color: var(--text-tertiary);
  flex-shrink: 0;
}

.info-texts {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.info-label {
  font-size: 11px;
  color: var(--text-tertiary);
}

.info-value {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ===== 通用 Section ===== */
.section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.section-title::before {
  content: '';
  width: 3px;
  height: 12px;
  border-radius: 2px;
  background: var(--theme-color);
  align-self: center;
}

.section-hint {
  font-size: 11px;
  font-weight: 400;
  color: var(--text-tertiary);
  margin-left: auto;
}

.no-data {
  font-size: 12px;
  color: var(--text-tertiary);
}

/* ===== 重点车型标签 ===== */
.vehicle-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.vehicle-tag {
  font-size: 12px;
  line-height: 1;
  padding: 6px 10px;
  border-radius: 4px;
  background: var(--bg-page);
  color: var(--text-secondary);
  border: 1px solid var(--border-color);
}

.vehicle-tag.is-primary {
  background: var(--theme-color);
  border-color: var(--theme-color);
  color: #ffffff;
  font-weight: 600;
}

/* ===== 执行措施建议 ===== */
.action-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.action-item {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.55;
  padding: 8px 10px;
  background: var(--bg-page);
  border-radius: 6px;
}

.action-badge {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 700;
  color: #ffffff;
  background: var(--theme-color);
  border-radius: 3px;
  padding: 2px 5px;
  margin-top: 1px;
}

/* ===== 干道清单表格 ===== */
.road-table {
  border: 1px solid var(--border-color);
  border-radius: 8px;
  overflow: hidden;
}

.road-head {
  display: grid;
  grid-template-columns: 1fr 76px 76px;
  gap: 8px;
  padding: 7px 12px;
  background: var(--bg-page);
  font-size: 11px;
  color: var(--text-tertiary);
}

.road-body {
  max-height: 180px;
  overflow-y: auto;
}

.road-body::-webkit-scrollbar {
  width: 4px;
}
.road-body::-webkit-scrollbar-thumb {
  background: var(--border-color);
  border-radius: 2px;
}

.road-row {
  display: grid;
  grid-template-columns: 1fr 76px 76px;
  gap: 8px;
  align-items: center;
  padding: 7px 12px;
  font-size: 12px;
  color: var(--text-secondary);
  border-top: 1px solid var(--border-color);
  transition: background 0.15s;
}

.road-row:hover {
  background: var(--bg-page);
}

.col-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.col-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
  color: var(--text-tertiary);
}

.col-num.num-strong {
  color: var(--text-primary);
  font-weight: 600;
}

/* ===== 复查指标 ===== */
.review-section {
  padding-top: 10px;
  border-top: 1px solid var(--border-color);
}

.review-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.review-item {
  font-size: 11px;
  color: var(--text-secondary);
  background: var(--bg-page);
  border-radius: 4px;
  padding: 4px 8px;
  line-height: 1.4;
}
</style>
