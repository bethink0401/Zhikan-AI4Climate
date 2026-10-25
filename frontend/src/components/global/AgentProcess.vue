<template>
  <div class="agent-process">
    <!-- 标题 -->
    <div class="process-header">
      <span class="process-title">🧠 多智能体研判进程</span>
      <span v-if="!isEmpty" class="process-status" :class="overallStatusClass">
        {{ overallStatusText }}
      </span>
    </div>

    <!-- 空状态 -->
    <div v-if="isEmpty" class="empty-state">
      <el-empty description="暂无 Agent 运行数据" />
    </div>

    <!-- 进程节点列表 -->
    <div v-else class="process-body">
      <div
        v-for="(agent, index) in agentList"
        :key="agent.name"
        class="process-node"
        :class="{
          'is-active': agent.status === 'running',
          'is-expanded': expandedAgent === agent.name
        }"
        @click="toggleExpand(agent.name)"
      >
        <!-- 左侧：节点序号 + 状态图标 -->
        <div class="node-indicator">
          <div class="node-icon" :class="`icon-${agent.status}`">
            <el-icon v-if="agent.status === 'success'"><Check /></el-icon>
            <el-icon v-else-if="agent.status === 'running'"><Loading /></el-icon>
            <el-icon v-else-if="agent.status === 'warning'"><Warning /></el-icon>
            <el-icon v-else-if="agent.status === 'error'"><CircleClose /></el-icon>
            <span v-else class="node-number">{{ index + 1 }}</span>
          </div>
          <!-- 连接线 -->
          <div v-if="index < agentList.length - 1" class="node-line"></div>
        </div>

        <!-- 右侧：Agent 信息 -->
        <div class="node-content">
          <!-- 头部：名称 + 状态 + 置信度 + 展开提示 -->
          <div class="node-header">
            <span class="node-name">{{ agent.label }}</span>
            <span class="node-status" :class="`status-${agent.status}`">
              {{ statusMap[agent.status] }}
            </span>
            <span v-if="agent.confidence !== undefined && agent.confidence !== null" class="node-confidence">
              置信度 <span :style="{ color: confidenceColor(agent.confidence) }">
                {{ Math.round(agent.confidence * 100) }}%
              </span>
            </span>
            <span class="expand-hint">
              {{ expandedAgent === agent.name ? '收起 ▲' : '展开 ▼' }}
            </span>
          </div>

          <!-- 摘要证据（最多2条，始终显示） -->
          <div v-if="agent.evidence && agent.evidence.length > 0" class="node-evidence">
            <span class="evidence-tag" v-for="(item, idx) in getSummaryEvidence(agent.evidence)" :key="idx">
              {{ item }}
            </span>
            <span v-if="agent.evidence.length > 2" class="evidence-more">
              +{{ agent.evidence.length - 2 }} 条
            </span>
          </div>

          <!-- ===== 详情区域（展开后显示） ===== -->
          <div v-if="expandedAgent === agent.name" class="node-detail">
            <!-- 完整证据列表 -->
            <div v-if="agent.evidence && agent.evidence.length > 0" class="detail-section">
              <div class="detail-section-title">📋 完整证据</div>
              <ul class="detail-evidence-list">
                <li v-for="(item, idx) in agent.evidence" :key="idx" class="detail-evidence-item">
                  {{ item }}
                </li>
              </ul>
            </div>

            <!-- 数据质量 -->
            <div v-if="agent.data_quality" class="detail-section">
              <div class="detail-section-title">📊 数据质量</div>
              <div class="detail-quality-grid">
                <div class="quality-item">
                  <span class="quality-label">缺失率</span>
                  <span class="quality-value" :class="getMissingRateClass(agent.data_quality.missing_rate)">
                    {{ formatPercent(agent.data_quality.missing_rate) }}
                  </span>
                </div>
                <div v-if="agent.data_quality.note" class="quality-item full-width">
                  <span class="quality-label">说明</span>
                  <span class="quality-value">{{ agent.data_quality.note }}</span>
                </div>
                <div v-if="agent.data_quality.source" class="quality-item">
                  <span class="quality-label">数据来源</span>
                  <span class="quality-value">{{ agent.data_quality.source }}</span>
                </div>
                <div v-if="agent.data_quality.fallback_used !== undefined" class="quality-item">
                  <span class="quality-label">是否使用备用数据</span>
                  <span class="quality-value">{{ agent.data_quality.fallback_used ? '是' : '否' }}</span>
                </div>
              </div>
            </div>

            <!-- 警告信息 -->
            <div v-if="agent.warnings && agent.warnings.length > 0" class="detail-section">
              <div class="detail-section-title">⚠️ 警告</div>
              <ul class="detail-warning-list">
                <li v-for="(item, idx) in agent.warnings" :key="idx" class="detail-warning-item">
                  {{ item }}
                </li>
              </ul>
            </div>

            <!-- 创建时间 -->
            <div v-if="agent.created_at" class="detail-section detail-time">
              <span class="detail-time-label">🕐 创建时间</span>
              <span class="detail-time-value">{{ formatTime(agent.created_at) }}</span>
            </div>

            <!-- 如果没有任何详情数据，显示占位 -->
            <div
              v-if="
                (!agent.evidence || agent.evidence.length === 0) &&
                !agent.data_quality &&
                (!agent.warnings || agent.warnings.length === 0) &&
                !agent.created_at
              "
              class="detail-empty"
            >
              <span class="detail-empty-text">暂无更多详情</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { Check, Loading, Warning, CircleClose } from '@element-plus/icons-vue'
import dayjs from 'dayjs'

defineOptions({ name: 'AgentProcess' })

// ---------- 1. 类型定义 ----------
export type AgentStatus = 'pending' | 'running' | 'success' | 'warning' | 'error'

export interface DataQuality {
  /** 缺失率 (0-1) */
  missing_rate?: number
  /** 数据质量说明 */
  note?: string
  /** 数据来源 */
  source?: string
  /** 是否使用了备用数据 */
  fallback_used?: boolean
}

export interface AgentNode {
  /** Agent 英文名称 */
  name: string
  /** Agent 中文标签（可选，不提供时自动从 name 映射） */
  label?: string
  /** 状态 */
  status: AgentStatus
  /** 置信度 (0-1) */
  confidence?: number
  /** 证据列表 */
  evidence?: string[]
  /** 数据质量 */
  data_quality?: DataQuality
  /** 警告列表 */
  warnings?: string[]
  /** 创建时间 */
  created_at?: string
}

// ---------- 2. Props ----------
const props = defineProps<{
  agents: AgentNode[]
}>()

// ---------- 3. Agent 中文名称映射 ----------
const agentLabelMap: Record<string, string> = {
  'AirQualityAgent': '空气质量分析',
  'MeteorologyAgent': '气象分析',
  'TrafficAgent': '交通分析',
  'SpatialAgent': '空间分析',
  'SourceAgent': '污染溯源',
  'DecisionAgent': '决策建议',
  'ReportAgent': '报告生成'
}

// ---------- 4. 状态文字映射 ----------
const statusMap: Record<AgentStatus, string> = {
  pending: '等待中',
  running: '运行中',
  success: '已完成',
  warning: '⚠️ 警告',
  error: '❌ 异常'
}

// ---------- 5. 展开状态 ----------
const expandedAgent = ref<string | null>(null)

const toggleExpand = (name: string) => {
  expandedAgent.value = expandedAgent.value === name ? null : name
}

// ---------- 6. 计算属性 ----------
const isEmpty = computed(() => {
  return !props.agents || props.agents.length === 0
})

const agentList = computed(() => {
  return props.agents.map(agent => ({
    ...agent,
    label: agent.label || agentLabelMap[agent.name] || agent.name
  }))
})

// ---------- 7. 整体状态 ----------
const overallStatusClass = computed(() => {
  if (isEmpty.value) return ''
  const hasError = agentList.value.some(a => a.status === 'error')
  if (hasError) return 'status-error'
  const hasWarning = agentList.value.some(a => a.status === 'warning')
  if (hasWarning) return 'status-warning'
  const allDone = agentList.value.every(a => a.status === 'success')
  if (allDone) return 'status-success'
  return 'status-running'
})

const overallStatusText = computed(() => {
  if (isEmpty.value) return ''
  const hasError = agentList.value.some(a => a.status === 'error')
  if (hasError) return '分析异常'
  const hasWarning = agentList.value.some(a => a.status === 'warning')
  if (hasWarning) return '部分警告'
  const allDone = agentList.value.every(a => a.status === 'success')
  if (allDone) return '分析完成 ✅'
  return '分析中...'
})

// ---------- 8. 置信度颜色 ----------
const confidenceColor = (value: number) => {
  if (value >= 0.8) return 'var(--status-good)'
  if (value >= 0.6) return 'var(--status-moderate)'
  if (value >= 0.4) return 'var(--status-unhealthy)'
  return 'var(--risk-very-high)'
}

// ---------- 9. 摘要证据（前2条） ----------
const getSummaryEvidence = (evidence: string[]) => {
  return evidence.slice(0, 2)
}

// ---------- 10. 缺失率样式 ----------
const getMissingRateClass = (rate?: number) => {
  if (rate === undefined || rate === null) return ''
  if (rate < 0.1) return 'missing-low'
  if (rate < 0.3) return 'missing-medium'
  return 'missing-high'
}

// ---------- 11. 格式化 ----------
const formatPercent = (value?: number) => {
  if (value === undefined || value === null) return '—'
  return `${(value * 100).toFixed(1)}%`
}

const formatTime = (value?: string) => {
  if (!value) return '—'
  return dayjs(value).format('YYYY-MM-DD HH:mm:ss')
}
</script>

<style scoped>
/* ===== 容器 ===== */
.agent-process {
  background: var(--panel-bg);
  border-radius: 8px;
  padding: 16px 20px;
  box-shadow: var(--shadow-sm);
}

/* ===== 标题 ===== */
.process-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-color);
  margin-bottom: 16px;
}

.process-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.process-status {
  font-size: 13px;
  font-weight: 500;
  padding: 2px 12px;
  border-radius: 12px;
}

.process-status.status-success {
  color: var(--status-good);
  background: rgba(103, 194, 58, 0.1);
}
.process-status.status-warning {
  color: var(--status-moderate);
  background: rgba(230, 162, 60, 0.1);
}
.process-status.status-error {
  color: var(--status-unhealthy);
  background: rgba(245, 108, 108, 0.1);
}
.process-status.status-running {
  color: var(--theme-color);
  background: rgba(64, 158, 255, 0.1);
}

/* ===== 空状态 ===== */
.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 120px;
}

/* ===== 进程主体 ===== */
.process-body {
  display: flex;
  flex-direction: column;
  gap: 0;
}

/* ===== 单个节点（可点击） ===== */
.process-node {
  display: flex;
  gap: 16px;
  padding: 10px 0;
  cursor: pointer;
  border-radius: 6px;
  transition: background 0.2s;
}

.process-node:hover {
  background: var(--bg-page);
}

.process-node.is-active {
  background: rgba(64, 158, 255, 0.05);
}

/* ===== 左侧指示器 ===== */
.node-indicator {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
  width: 32px;
}

.node-icon {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  font-weight: 600;
  flex-shrink: 0;
  transition: all 0.3s;
}

.icon-pending {
  background: var(--bg-page);
  color: var(--text-tertiary);
  border: 2px solid var(--border-color);
}
.icon-pending .node-number {
  font-size: 12px;
  color: var(--text-tertiary);
}

.icon-running {
  background: var(--theme-color);
  color: #fff;
  animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(64, 158, 255, 0.4); }
  50% { box-shadow: 0 0 0 8px rgba(64, 158, 255, 0); }
}

.icon-success {
  background: var(--status-good);
  color: #fff;
}
.icon-warning {
  background: var(--status-moderate);
  color: #fff;
}
.icon-error {
  background: var(--status-unhealthy);
  color: #fff;
}

.node-line {
  width: 2px;
  flex: 1;
  min-height: 16px;
  background: var(--border-color);
  margin: 4px 0;
}
.process-node:last-child .node-line {
  display: none;
}

/* ===== 右侧内容 ===== */
.node-content {
  flex: 1;
  min-width: 0;
}

.node-header {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.node-name {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-primary);
}

.node-status {
  font-size: 12px;
  font-weight: 400;
}

.status-pending { color: var(--text-tertiary); }
.status-running { color: var(--theme-color); }
.status-success { color: var(--status-good); }
.status-warning { color: var(--status-moderate); }
.status-error { color: var(--status-unhealthy); }

.node-confidence {
  font-size: 12px;
  color: var(--text-tertiary);
}
.node-confidence span {
  font-weight: 600;
}

.expand-hint {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-left: auto;
  user-select: none;
}

/* ===== 摘要证据 ===== */
.node-evidence {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}

.evidence-tag {
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg-page);
  padding: 1px 10px;
  border-radius: 12px;
}

.evidence-more {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 1px 6px;
}

/* ===== 详情区域 ===== */
.node-detail {
  margin-top: 10px;
  padding: 12px 16px;
  background: var(--bg-page);
  border-radius: 6px;
  border-left: 3px solid var(--border-color);
}

.detail-section {
  margin-bottom: 12px;
}
.detail-section:last-child {
  margin-bottom: 0;
}

.detail-section-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

/* ===== 完整证据列表 ===== */
.detail-evidence-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.detail-evidence-item {
  font-size: 13px;
  color: var(--text-secondary);
  padding: 4px 0;
  padding-left: 16px;
  position: relative;
  line-height: 1.6;
}
.detail-evidence-item::before {
  content: '•';
  position: absolute;
  left: 0;
  color: var(--theme-color);
}

/* ===== 数据质量网格 ===== */
.detail-quality-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 16px;
}

.quality-item {
  display: flex;
  justify-content: space-between;
  padding: 4px 0;
  font-size: 13px;
}

.quality-item.full-width {
  grid-column: 1 / -1;
}

.quality-label {
  color: var(--text-tertiary);
}

.quality-value {
  color: var(--text-primary);
  font-weight: 500;
}

.missing-low {
  color: var(--status-good);
}
.missing-medium {
  color: var(--status-moderate);
}
.missing-high {
  color: var(--status-unhealthy);
}

/* ===== 警告列表 ===== */
.detail-warning-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

.detail-warning-item {
  font-size: 13px;
  color: var(--status-unhealthy);
  padding: 4px 0;
  padding-left: 4px;
  line-height: 1.6;
}

/* ===== 创建时间 ===== */
.detail-time {
  display: flex;
  gap: 12px;
  align-items: center;
}

.detail-time-label {
  font-size: 12px;
  color: var(--text-tertiary);
}

.detail-time-value {
  font-size: 13px;
  color: var(--text-secondary);
}

/* ===== 详情空状态 ===== */
.detail-empty {
  padding: 8px 0;
  text-align: center;
}
.detail-empty-text {
  font-size: 13px;
  color: var(--text-tertiary);
}

/* ===== 响应式 ===== */
@media (max-width: 640px) {
  .agent-process {
    padding: 12px 14px;
  }

  .node-header {
    gap: 6px;
  }

  .node-name {
    font-size: 13px;
  }

  .detail-quality-grid {
    grid-template-columns: 1fr;
  }

  .expand-hint {
    font-size: 10px;
  }
}
</style>