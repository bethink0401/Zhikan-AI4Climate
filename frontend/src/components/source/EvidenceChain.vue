<template>
  <div class="evidence-chain">
    <!-- 标题栏：标题 + 取证状态 -->
    <div class="chain-header">
      <div class="header-titles">
        <div class="title-line">
          <span class="chain-icon">⛓</span>
          <span class="chain-title">污染溯源因果证据链推理</span>
        </div>
        <span class="chain-subtitle">Causal Inference Evidence Trace (Bayesian Sequential Engine)</span>
      </div>
      <div class="header-meta">
        <span class="meta-tag done">
          <span class="done-dot"></span>取证完成
        </span>
        <span class="meta-text">共 {{ evidenceList.length }} 条证据 · 置信度 {{ confidencePct }}%</span>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-if="isEmpty" class="empty-state">
      <el-empty description="暂无证据数据" :image-size="80" />
    </div>

    <!-- 证据步骤列表 -->
    <div v-else class="chain-body">
      <div
        v-for="(item, index) in evidenceList"
        :key="index"
        class="chain-step"
        :class="{ 'is-conclusion': isConclusionStep(index) }"
      >
        <!-- 左侧：编号 + 连接线 -->
        <div class="step-rail">
          <span class="step-index">{{ formatIndex(index) }}.</span>
          <span class="step-dot" :class="`dot-${item.status}`"></span>
          <span v-if="index < evidenceList.length - 1" class="step-line"></span>
        </div>

        <!-- 中间：标题 + 中文说明 -->
        <div class="step-content">
          <div class="step-title">{{ item.step }}</div>
          <div v-if="item.detail" class="step-detail">{{ item.detail }}</div>
        </div>

        <!-- 右侧：状态标签 / 结论按钮 -->
        <div class="step-side">
          <span
            v-if="isConclusionStep(index)"
            class="side-btn"
          >查看决策确认 →</span>
          <span v-else class="side-status" :class="`status-${item.status}`">
            {{ statusLabel(item.status) }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

defineOptions({ name: 'EvidenceChain' })

// ---------- 1. Props 类型定义 ----------
export interface EvidenceItem {
  step: string
  detail?: string
  status?: 'success' | 'warning' | 'error' | 'default'
  isActive?: boolean
}

const props = defineProps<{
  data: EvidenceItem[]
  /** 溯源置信度 0-1（可选，用于标题栏展示） */
  confidence?: number
}>()

// ---------- 2. 判断数据是否为空 ----------
const isEmpty = computed(() => {
  return !props.data || props.data.length === 0
})

const confidencePct = computed(() =>
  props.confidence !== undefined ? Math.round(props.confidence * 100) : '—'
)

// ---------- 3. 处理数据：默认状态 + 中文说明 ----------
const evidenceList = computed(() => {
  return props.data.map(item => ({
    ...item,
    status: item.status || 'default',
    detail: item.detail || guessDetail(item.step)
  }))
})

/** 编号格式 01 / 02 ... */
const formatIndex = (index: number): string =>
  String(index + 1).padStart(2, '0')

/** 最后一步为结论步骤 */
const isConclusionStep = (index: number) => {
  return index === evidenceList.value.length - 1
}

// ---------- 4. 状态标签 ----------
const statusLabel = (status: string): string => {
  const map: Record<string, string> = {
    success: '已采纳',
    warning: '待验证',
    error: '已驳回',
    default: '已更新'
  }
  return map[status] ?? '已更新'
}

// ---------- 5. 根据英文证据内容推断中文说明 ----------
function guessDetail(text: string): string {
  const t = text.toLowerCase()
  if (t.includes('traffic-source possibility') || t.includes('traffic source possibility')) {
    return '综合评估公式: 交通源概率由 NO2 异常、交通压力、上风向匹配、静风与路边增强因子加权合成。'
  }
  if (t.includes('roadside')) {
    return '路边监测站位于道路峡谷内部, NO2 浓度显著抬升, 符合交通源影响的近场特征。'
  }
  if (t.includes('anomaly')) {
    return '污染浓度明显高于同时段基线, 判定为显著污染事件, 进入重点取证流程。'
  }
  if (t.includes('wind speed')) {
    return '平均风速偏低, 横向扩散受阻, 利于污染物在源区附近累积。'
  }
  if (t.includes('wind direction')) {
    return '主导风向与上风向候选道路群走向基本一致, 空间匹配关系成立。'
  }
  if (t.includes('precipitation')) {
    return '降水清洗系数偏低, 湿沉降对污染物的清除作用有限。'
  }
  if (t.includes('traffic links') || t.includes('nearby traffic')) {
    return '上风向路网中识别出与站点直接连通的交通干道, 支撑近场传输假设。'
  }
  if (t.includes('confidence') || t.includes('matching')) {
    return '综合判定: 路网结构与风向配置共同指向交通源为当前污染的主要来源。'
  }
  return ''
}
</script>

<style scoped>
/* ===== 容器 ===== */
.evidence-chain {
  background: var(--panel-bg);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  padding: 18px 20px;
}

/* ===== 标题栏 ===== */
.chain-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-color);
}

.title-line {
  display: flex;
  align-items: center;
  gap: 8px;
}

.chain-icon {
  font-size: 14px;
}

.chain-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.chain-subtitle {
  display: block;
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 3px;
  margin-left: 22px;
}

.header-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.meta-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  padding: 5px 9px;
  border-radius: 4px;
}

.meta-tag.done {
  color: #15803d;
  background: rgba(34, 197, 94, 0.12);
}

.done-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.meta-text {
  font-size: 11px;
  color: var(--text-tertiary);
  white-space: nowrap;
}

/* ===== 空状态 ===== */
.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 120px;
}

/* ===== 证据链主体 ===== */
.chain-body {
  display: flex;
  flex-direction: column;
}

/* ===== 单步证据 ===== */
.chain-step {
  display: flex;
  align-items: stretch;
  gap: 10px;
  padding: 10px 0;
}

.chain-step:hover {
  background: var(--bg-page);
  border-radius: 6px;
}

/* 左侧轨道：编号 + 圆点 + 连接线 */
.step-rail {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
  width: 40px;
}

.step-index {
  font-size: 12px;
  font-weight: 700;
  color: var(--text-tertiary);
  line-height: 1.2;
  margin-bottom: 6px;
}

.step-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--border-color);
}

.dot-success { background: #22c55e; }
.dot-warning { background: #f59e0b; }
.dot-error   { background: #ef4444; }
.dot-default { background: #93b4e0; }

.step-line {
  flex: 1;
  width: 2px;
  min-height: 16px;
  margin-top: 4px;
  background: var(--border-color);
  border-radius: 1px;
  opacity: 0.6;
}

.chain-step:last-child .step-line {
  display: none;
}

/* 中间内容 */
.step-content {
  flex: 1;
  min-width: 0;
}

.step-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.5;
}

.step-detail {
  font-size: 12px;
  color: var(--text-tertiary);
  margin-top: 4px;
  line-height: 1.6;
}

/* 右侧状态 */
.step-side {
  flex-shrink: 0;
  display: flex;
  align-items: center;
}

.side-status {
  font-size: 11px;
  line-height: 1;
  padding: 5px 9px;
  border-radius: 4px;
  white-space: nowrap;
}

.status-success {
  color: #15803d;
  background: rgba(34, 197, 94, 0.12);
  font-weight: 600;
}

.status-warning {
  color: #b45309;
  background: rgba(245, 158, 11, 0.14);
}

.status-error {
  color: #c22f2f;
  background: rgba(239, 68, 68, 0.1);
}

.status-default {
  color: var(--text-secondary);
  background: var(--bg-page);
}

/* 结论按钮样式 */
.side-btn {
  font-size: 11px;
  font-weight: 600;
  color: #ffffff;
  background: var(--theme-color);
  padding: 6px 12px;
  border-radius: 4px;
  white-space: nowrap;
  cursor: default;
}

/* ===== 结论步骤整行强调 ===== */
.is-conclusion .step-title {
  color: var(--theme-color);
  font-size: 14px;
}

.is-conclusion .step-dot {
  width: 12px;
  height: 12px;
  background: var(--theme-color);
  box-shadow: 0 0 10px rgba(64, 158, 255, 0.45);
}

/* ===== 响应式 ===== */
@media (max-width: 760px) {
  .header-meta .meta-text {
    display: none;
  }

  .step-side {
    display: none;
  }
}
</style>
