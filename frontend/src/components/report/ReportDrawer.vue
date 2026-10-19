<template>
  <!-- 抽屉 -->
  <el-drawer
    v-model="visible"
    title="智能治理分析报告"
    size="50%"
    :close-on-click-modal="true"
    :destroy-on-close="true"
    class="report-drawer"
  >
    <!-- 加载状态 -->
    <div v-if="loading" class="report-loading">
      <el-icon class="is-loading"><Loading /></el-icon>
      <span>报告生成中...</span>
    </div>

    <!-- 报告内容 -->
    <div v-else-if="!isEmpty" class="report-content">
      <!-- 顶部：一句话总结 -->
      <div v-if="reportData.summary" class="report-summary">
        <div class="summary-label">📌 一句话总结</div>
        <div class="summary-text">{{ reportData.summary }}</div>
      </div>

      <!-- 决策摘要 -->
      <div v-if="reportData.decision_summary" class="report-section">
        <div class="section-title">决策摘要</div>
        <div class="section-content">{{ reportData.decision_summary }}</div>
      </div>

      <!-- 七节正文 -->
      <div
        v-for="(section, index) in reportData.sections"
        :key="index"
        class="report-section"
      >
        <div class="section-title">
          <span class="section-number">{{ index + 1 }}</span>
          {{ section.title }}
        </div>
        <div class="section-content">{{ section.content }}</div>

        <!-- 子章节（如果有） -->
        <div v-if="section.subsections && section.subsections.length > 0" class="subsection-list">
          <div
            v-for="(sub, subIndex) in section.subsections"
            :key="subIndex"
            class="subsection-item"
          >
            <div class="subsection-title">{{ sub.title }}</div>
            <div class="subsection-content">{{ sub.content }}</div>
          </div>
        </div>
      </div>

      <!-- 底部：置信度 + 数据质量 -->
      <div class="report-footer">
        <div class="footer-item">
          <span class="footer-label">置信度</span>
          <span class="footer-value" :style="{ color: confidenceColor }">
            {{ Math.round((reportData.confidence ?? 0) * 100) }}%
          </span>
        </div>
        <div class="footer-item">
          <span class="footer-label">数据质量</span>
          <span class="footer-value" :style="{ color: dataQualityColor }">
            {{ dataQualityLabel }}
          </span>
        </div>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-else class="report-empty">
      <el-empty description="暂无报告数据" />
    </div>

    <!-- 底部操作按钮 -->
    <template #footer>
      <div class="drawer-footer">
        <el-button @click="handleCopy">📋 复制</el-button>
        <el-button type="primary" @click="handleExport">📥 导出</el-button>
        <el-button @click="handleClose">关闭</el-button>
      </div>
    </template>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

defineOptions({ name: 'ReportDrawer' })

// ---------- 1. 类型定义 ----------
export interface ReportSection {
  title: string
  content: string
  subsections?: ReportSection[]
}

export interface ReportData {
  /** 一句话总结 */
  summary?: string
  /** 决策摘要 */
  decision_summary?: string
  /** 七节正文 */
  sections: ReportSection[]
  /** 置信度 0-1 */
  confidence?: number
  /** 数据质量：high / medium / low */
  data_quality?: 'high' | 'medium' | 'low'
  /** 纯文本报告（备用） */
  report_text?: string
  /** 状态 */
  status?: 'pending' | 'generating' | 'completed' | 'failed'
}

// ---------- 2. Props 和 Emits ----------
const props = defineProps<{
  /** 报告数据 */
  data: ReportData | null
  /** 加载状态 */
  loading?: boolean
}>()

const emit = defineEmits<{
  /** 关闭抽屉 */
  close: []
  /** 复制报告 */
  copy: []
  /** 导出报告 */
  export: []
}>()

// ---------- 3. 抽屉显示状态 ----------
const visible = ref(false)

// 暴露 open/close 方法给父组件
defineExpose({
  open: () => { visible.value = true },
  close: () => { visible.value = false }
})

// ---------- 4. 数据判断 ----------
const isEmpty = computed(() => {
  return !props.data || !props.data.sections || props.data.sections.length === 0
})

const reportData = computed(() => {
  return props.data || { sections: [] }
})

// ---------- 5. 置信度颜色 ----------
const confidenceColor = computed(() => {
  const value = props.data?.confidence ?? 0
  if (value >= 0.8) return 'var(--status-good)'
  if (value >= 0.6) return 'var(--status-moderate)'
  if (value >= 0.4) return 'var(--status-unhealthy)'
  return 'var(--risk-very-high)'
})

// ---------- 6. 数据质量 ----------
const dataQualityMap: Record<string, { label: string; color: string }> = {
  high: { label: '高', color: 'var(--status-good)' },
  medium: { label: '中', color: 'var(--status-moderate)' },
  low: { label: '低', color: 'var(--status-unhealthy)' }
}

const dataQualityLabel = computed(() => {
  const quality = props.data?.data_quality || 'low'
  return dataQualityMap[quality]?.label || '未知'
})

const dataQualityColor = computed(() => {
  const quality = props.data?.data_quality || 'low'
  return dataQualityMap[quality]?.color || 'var(--text-tertiary)'
})

// ---------- 7. 操作按钮 ----------
const handleClose = () => {
  visible.value = false
  emit('close')
}

const handleCopy = () => {
  if (props.data?.report_text) {
    navigator.clipboard.writeText(props.data.report_text)
      .then(() => ElMessage.success('报告已复制到剪贴板'))
      .catch(() => ElMessage.error('复制失败'))
  } else {
    ElMessage.warning('暂无报告内容可复制')
  }
  emit('copy')
}

const handleExport = () => {
  if (props.data?.report_text) {
    const blob = new Blob([props.data.report_text], { type: 'text/plain;charset=utf-8' })
    const link = document.createElement('a')
    link.href = URL.createObjectURL(blob)
    link.download = `智能治理分析报告_${new Date().toLocaleDateString()}.txt`
    link.click()
    URL.revokeObjectURL(link.href)
    ElMessage.success('报告导出成功')
  } else {
    ElMessage.warning('暂无报告内容可导出')
  }
  emit('export')
}

// ---------- 8. 监听 visible 变化 ----------
watch(visible, (val) => {
  if (!val) {
    emit('close')
  }
})
</script>

<style scoped>
/* ===== 抽屉 ===== */
:deep(.el-drawer__header) {
  border-bottom: 1px solid var(--border-color);
  padding-bottom: 16px;
  margin-bottom: 0;
}
:deep(.el-drawer__title) {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
}
:deep(.el-drawer__body) {
  padding: 20px 24px;
}
:deep(.el-drawer__footer) {
  border-top: 1px solid var(--border-color);
  padding: 16px 24px;
}

/* ===== 加载状态 ===== */
.report-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 60px 0;
  font-size: 14px;
  color: var(--text-secondary);
}
.report-loading .is-loading {
  font-size: 24px;
  color: var(--theme-color);
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

/* ===== 报告内容 ===== */
.report-content {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* ===== 一句话总结 ===== */
.report-summary {
  background: var(--theme-color-lighter);
  border-radius: 8px;
  padding: 16px 20px;
  border-left: 4px solid var(--theme-color);
}
.summary-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--theme-color);
  margin-bottom: 4px;
}
.summary-text {
  font-size: 15px;
  font-weight: 500;
  color: var(--text-primary);
  line-height: 1.6;
}

/* ===== 报告章节 ===== */
.report-section {
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border-color);
}
.report-section:last-child {
  border-bottom: none;
  padding-bottom: 0;
}

.section-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 6px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-number {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  background: var(--theme-color);
  color: #fff;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 700;
  flex-shrink: 0;
}
.section-content {
  font-size: 14px;
  color: var(--text-secondary);
  line-height: 1.8;
  padding-left: 28px;
}

/* ===== 子章节 ===== */
.subsection-list {
  margin-top: 8px;
  padding-left: 28px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.subsection-item {
  padding: 8px 12px;
  background: var(--bg-page);
  border-radius: 4px;
}
.subsection-title {
  font-size: 13px;
  font-weight: 500;
  color: var(--text-primary);
  margin-bottom: 2px;
}
.subsection-content {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
}

/* ===== 底部：置信度 + 数据质量 ===== */
.report-footer {
  display: flex;
  gap: 32px;
  padding-top: 16px;
  border-top: 1px solid var(--border-color);
  margin-top: 4px;
}
.footer-item {
  display: flex;
  align-items: center;
  gap: 8px;
}
.footer-label {
  font-size: 13px;
  color: var(--text-tertiary);
}
.footer-value {
  font-size: 14px;
  font-weight: 600;
}

/* ===== 空状态 ===== */
.report-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 200px;
}

/* ===== 抽屉底部按钮 ===== */
.drawer-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>