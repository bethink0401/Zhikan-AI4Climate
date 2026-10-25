<template>
  <div class="main-layout">
    <!-- 顶部导航栏 -->
    <header class="layout-header">
      <div class="header-left">
        <h1 class="system-title">天候智枢</h1>
      </div>
      
      <nav class="header-nav">
        <router-link 
          v-for="item in navItems" 
          :key="item.path"
          :to="item.path"
          class="nav-item"
          active-class="nav-item-active"
        >
          {{ item.label }}
        </router-link>
      </nav>

      <div class="header-actions">
        <el-button v-if="route.path !== '/overview' && aiEnabled" class="chat-open-button" @click="openChat">
          AI 研判助手
        </el-button>
        <el-button @click="handleReport" type="primary">
          智能分析报告
        </el-button>
        <el-button @click="showAgentDrawer = true">
          Agent运行状态
        </el-button>
      </div>
    </header>

    <!-- 内容区域 -->
    <main class="layout-content">
      <slot />
    </main>

    <!-- Agent 状态 Drawer -->
    <AgentStatusDrawer v-model:visible="showAgentDrawer" />

    <!-- 智能分析报告 Drawer（数据来自分析链路 CoordinatorResponse.report） -->
    <ReportDrawer ref="reportDrawerRef" :data="reportData" :loading="reportLoading" />
    <el-drawer v-model="showChatDrawer" title="AI 研判助手" size="min(1120px, 88vw)" class="global-chat-drawer">
      <AIChatPanel />
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import AgentStatusDrawer from '@/components/global/AgentStatusDrawer.vue'
import ReportDrawer from '@/components/report/ReportDrawer.vue'
import AIChatPanel from '@/components/overview/AIChatPanel.vue'
import { useAnalysisStore } from '@/stores/analysis'
import type { ReportDrawerData, ReportResponse } from '@/types/report'

const navItems = [
  { path: '/overview', label: '综合态势' },
  { path: '/restriction', label: '污染溯源与区域交通治理' },
  { path: '/weather', label: '公里级传播预警' },
  { path: '/travel', label: '出行建议与路线推荐' }
]

const showAgentDrawer = ref(false)
const showChatDrawer = ref(false)
const route = useRoute()
const aiEnabled = import.meta.env.VITE_AI_VIZ_ENABLED === 'true'
async function openChat(): Promise<void> {
  showChatDrawer.value = true
  if (analysisStore.analysisSource !== 'api') {
    await analysisStore.loadInitial()
  }
}

// ---------- 智能分析报告 ----------
const analysisStore = useAnalysisStore()
const reportDrawerRef = ref<InstanceType<typeof ReportDrawer> | null>(null)
const reportData = ref<ReportDrawerData | null>(null)
const reportLoading = ref(false)

/** 把 CoordinatorResponse.report 映射成抽屉视图模型 */
function toReportData(report: ReportResponse): ReportDrawerData {
  const missingRate = report.data_quality?.missing_rate ?? 1
  return {
    summary: report.result.summary,
    decision_summary: report.result.decision_summary,
    sections: report.result.sections,
    confidence: report.confidence,
    // 与后端口径一致：missing_rate >= 0.3 时后端会把 status 标成 warning
    data_quality: missingRate <= 0 ? 'high' : missingRate < 0.3 ? 'medium' : 'low',
    report_text: report.result.report_text
  }
}

const handleReport = async () => {
  reportDrawerRef.value?.open()

  // 已有分析结果（含离线快照）就直接展示
  const existing = analysisStore.result?.report
  if (existing) {
    reportData.value = toReportData(existing)
    return
  }

  // 其他页面首次点击：还没有结果，用默认任务跑一遍真实链路
  reportData.value = null
  reportLoading.value = true
  try {
    await analysisStore.loadInitial()
    const report = analysisStore.result?.report
    if (report) {
      reportData.value = toReportData(report)
    } else {
      ElMessage.error(analysisStore.analysisError || '报告生成失败，请稍后重试')
    }
  } catch {
    ElMessage.error(analysisStore.analysisError || '报告生成失败，请稍后重试')
  } finally {
    reportLoading.value = false
  }
}
</script>

<style scoped>
.main-layout {
  width: 100%;
  height: 100vh;
  display: flex;
  flex-direction: column;
}

.layout-header {
  height: 60px;
  background: var(--panel-bg);
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  padding: 0 24px;
  gap: 24px;
}

.header-left {
  flex-shrink: 0;
}

.system-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.header-nav {
  flex: 1;
  display: flex;
  gap: 8px;
  margin-left: 48px;
}

.nav-item {
  padding: 8px 16px;
  color: var(--text-secondary);
  text-decoration: none;
  border-radius: 4px;
  transition: all 0.3s;
}

.nav-item:hover {
  background: var(--bg-page);
  color: var(--text-primary);
}

.nav-item-active {
  background: var(--theme-color);
  color: #fff;
}

.header-actions {
  display: flex;
  gap: 12px;
  flex-shrink: 0;
}

.layout-content {
  flex: 1;
  overflow: auto;
  background: var(--bg-page);
}

.chat-open-button {
  color: #fff;
  background: #21318c;
  border-color: #21318c;
}

.global-chat-drawer :deep(.el-drawer__body) {
  padding: 0 18px 18px;
  min-height: 0;
}

.global-chat-drawer :deep(.ai-chat-panel) {
  height: calc(100vh - 105px);
  border: 1px solid #d5ebbd;
}
</style>
