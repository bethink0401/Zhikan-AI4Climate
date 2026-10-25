<template>
  <aside class="ai-chat-panel">
    <div class="panel-header">
      <span class="header-icon">
        <el-icon :size="16"><ChatDotRound /></el-icon>
      </span>
      <div class="header-texts">
        <div class="header-title-row">
          <span class="panel-title">AI 研判助手</span>
        </div>
        <div class="header-sub">环境分析 · 知识问答 · 智能可视化</div>
      </div>
      <div class="header-actions">
        <el-tag class="live-tag" size="small" type="success" effect="light">● 在线研判</el-tag>
        <el-button v-if="kbEnabled" class="kb-btn" size="small" text @click="showKnowledgeManager = true">
          <el-icon :size="15"><Reading /></el-icon>
          知识库
        </el-button>
      </div>
    </div>

    <div class="meta-note">服务端会话：{{ aiStore.sessionId.slice(0, 8) }}… · 刷新后会重新校验当前分析范围</div>
    <div v-if="isTravelPage" class="scope-note">当前页面：出行建议与路线推荐 · {{ travelScopeLabel }}</div>
    <div v-else-if="trustedScopeLabel" class="scope-note">当前可信范围：{{ trustedScopeLabel }}</div>
    <el-alert
      v-if="scopeNeedsAnalysis && (!isTravelPage || crossPageFromTravel)"
      class="scope-warning"
      type="warning"
      :closable="false"
      show-icon
      title="分析范围已变化，需要重新分析"
    >
      <div>待分析范围：{{ pendingScopeLabel }}</div>
      <div>重新分析前，旧站点的图表、建议和监测结论不会被继续使用。</div>
    </el-alert>

    <div class="messages" aria-live="polite">
      <!-- 开场白：与截图风格一致的头像 + 气泡 + 快捷追问 -->
      <div v-if="!aiStore.hasMessages" class="msg assistant">
        <div class="avatar">
          <el-icon><Service /></el-icon>
        </div>
        <div class="bubble">
          <p class="text">{{ welcomeText }}</p>
          <div class="chips">
            <span v-for="chip in quickChips" :key="chip" class="chip" @click="send(chip)">
              {{ chip }}
            </span>
          </div>
        </div>
      </div>

      <div v-for="message in aiStore.messages" :key="message.id" class="msg" :class="message.role">
        <div class="avatar">
          <el-icon v-if="message.role === 'assistant'"><Service /></el-icon>
          <el-icon v-else><User /></el-icon>
        </div>
        <div class="bubble" :class="{ 'with-chart': dynamicChartsFor(message.response).length > 0 || hasRouteMap(message.response) || Boolean(inlineVizFor(message.response)) }">
          <div v-if="message.role === 'assistant' && message.restored" class="history-note">历史回答 · 非本次生成</div>
          <RecommendationAnswer
            v-if="recommendationDisplayFor(message.response)"
            :display="recommendationDisplayFor(message.response)!"
          />
          <template v-else-if="message.response?.content_blocks?.length">
            <template v-for="(block, index) in groupedBlocksFor(message.response)" :key="`${message.id}-${index}`">
              <SafeMarkdown v-if="block.type === 'text'" class="message-content" :content="visualizationAnswerText(block.text)" />
              <div v-else-if="block.type === 'chart_group'" class="chart-grid" :class="{ 'single-chart': block.charts.length === 1 }">
                <DynamicChatChart v-for="chart in block.charts" :key="chart.id" :chart="chart" />
              </div>
              <ChatRouteMap v-else-if="block.type === 'map'" :map="block.map" />
              <ChatPropagationMap v-else-if="block.type === 'propagation_map'" :map="block.map" />
            </template>
          </template>
          <SafeMarkdown v-else class="message-content" :content="visualizationAnswerText(message.content)" />

          <div v-if="recommendationDisplayFor(message.response) && dynamicChartsFor(message.response).length"
               class="chart-grid" :class="{ 'single-chart': dynamicChartsFor(message.response).length === 1 }">
            <DynamicChatChart v-for="chart in dynamicChartsFor(message.response)"
                              :key="`${message.id}-${chart.id}`" :chart="chart" />
          </div>

          <template v-if="recommendationDisplayFor(message.response)">
            <template v-for="block in message.response?.content_blocks ?? []" :key="block.type === 'text' ? 'text' : block.id">
              <ChatPropagationMap v-if="block.type === 'propagation_map'" :map="block.map" />
            </template>
          </template>

          <!-- 生成的图表以对话气泡形式内嵌在回答里 -->
          <div v-if="!message.response?.content_blocks?.length && inlineVizFor(message.response)" class="inline-chart-card">
            <AIVisualizationRenderer :visualization="inlineVizFor(message.response)" />
          </div>

          <!-- Answer-specific provenance and interpretation, collapsed by default. -->
          <MessageEvidenceDetails v-if="message.response" :response="message.response" />

          <!-- Knowledge retrieval status -->
          <div
            v-if="message.response?.knowledge && !message.response.knowledge.used && !recommendationDisplayFor(message.response)"
            class="knowledge-status"
          >
            <span v-if="message.response.knowledge.retrieval_status === 'no_hits'" class="status-note">
              💡 未找到相关文献
            </span>
            <span v-else-if="message.response.knowledge.retrieval_status === 'disabled'" class="status-note">
              知识库未启用
            </span>
            <span v-else-if="message.response.knowledge.retrieval_status === 'failed'" class="status-note">
              ⚠️ 知识库检索失败
            </span>
          </div>
        </div>
      </div>

      <!-- 等待回答时的打字指示器 -->
      <div v-if="aiStore.loading" class="msg assistant">
        <div class="avatar">
          <el-icon><Service /></el-icon>
        </div>
        <div class="bubble">
          <span class="typing"><i></i><i></i><i></i></span>
          <span class="typing-text">{{ aiStore.progressMessage }}</span>
        </div>
      </div>
    </div>

    <el-alert v-if="aiStore.error" class="service-error" type="error" :closable="false" show-icon>
      <template #title>{{ aiStore.error.message }}</template>
      <div>错误码：{{ aiStore.error.code }}<span v-if="aiStore.error.status"> · HTTP {{ aiStore.error.status }}</span></div>
      <div v-if="aiStore.error.status === 409">服务端会话存在并发冲突，本次状态未覆盖，请重试消息。</div>
    </el-alert>

    <div class="chat-input">
      <div class="quick-chips">
        <span
          v-for="chip in quickChips"
          :key="chip"
          class="chip"
          @click="send(chip)"
        >
          {{ chip }}
        </span>
      </div>
      <div class="input-row">
        <el-input
          v-model="draft"
          maxlength="500"
          placeholder="输入问题，或直接输入指令…（Enter 发送）"
          @keyup.enter="send()"
        />
        <el-button type="primary" :loading="aiStore.loading" :disabled="!draft.trim() || aiStore.loading || !canSend" @click="send()">
          <el-icon v-if="!aiStore.loading"><Promotion /></el-icon>
          <span>{{ aiStore.loading ? '分析中' : '发送' }}</span>
        </el-button>
      </div>
    </div>

    <!-- Knowledge Manager Drawer -->
    <el-drawer v-model="showKnowledgeManager" title="专家知识库管理" size="60%" direction="rtl">
      <KnowledgeManager />
    </el-drawer>
  </aside>
</template>

<script setup lang="ts">
import { ref, computed, watch, defineAsyncComponent } from 'vue'
import { useRoute } from 'vue-router'
import { ChatDotRound, Promotion, Reading, Service, User } from '@element-plus/icons-vue'
import { useAiChatStore } from '@/stores/aiChat'
import { useAnalysisStore } from '@/stores/analysis'
import { useTravelStore } from '@/stores/travel'
import KnowledgeManager from './KnowledgeManager.vue'
import MessageEvidenceDetails from './MessageEvidenceDetails.vue'
import RecommendationAnswer from './RecommendationAnswer.vue'
import SafeMarkdown from './SafeMarkdown.vue'
import AIVisualizationRenderer from './AIVisualizationRenderer.vue'
import DynamicChatChart from './DynamicChatChart.vue'
import { formatAnalysisScope, toAnalysisScope } from '@/utils/analysisScope'
import { recommendationDisplayFor } from '@/utils/recommendationDisplay'
import { isVerifiedChatChart } from '@/utils/chatChartVisibility'
import { visualizationAnswerText } from '@/utils/aiVisualization'
import type { AIChatResponse, ActiveVisualization, DynamicChart } from '@/types/aiChat'

const ChatRouteMap = defineAsyncComponent(() => import('./ChatRouteMap.vue'))
const ChatPropagationMap = defineAsyncComponent(() => import('./ChatPropagationMap.vue'))
const aiStore = useAiChatStore()
const analysisStore = useAnalysisStore()
const travelStore = useTravelStore()
const route = useRoute()
const draft = ref('')
const isTravelPage = computed(() => route.path === '/travel')
const asksOtherPage = (question: string): boolean => /综合态势|污染溯源|区域交通治理|交通治理|污染源|来源筛查|公里级传播|传播预警|扩散预警|气象风险|敏感目标|上风向道路|管控路段|智能分析报告|分析报告|车型结构/.test(question)
const crossPageFromTravel = computed(() => isTravelPage.value && asksOtherPage(draft.value))
const travelScopeLabel = computed(() => travelStore.live ? '实时规划' : '当前行程')
const showKnowledgeManager = ref(false)
const kbEnabled = computed(() => {
  return import.meta.env.VITE_KB_ENABLED === 'true'
})

// 开场白：按页面功能区分说明
const welcomeText = computed(() => isTravelPage.value
  ? '你好，我是 AI 研判助手。你可以问当前路线的推荐理由、污染暴露和耗时，也可以直接说“从奥运站到九龙寨城公园怎么走”，我会按页面所选时段规划并展示路线地图。'
  : '你好，我是 AI 研判助手。你可以询问综合态势、污染溯源、传播预警与出行建议；需要时我会生成图表。也可以直接说“从旺角到油麻地怎么走”，我会规划路线并展示地图。专业问题可检索知识库。')

// Keep four concise prompts shared by every chat entry point.
const quickChips = [
  '污染从哪来？',
  '污染往哪传播？',
  '旺角到油麻地怎么走更好？',
  '什么时候出发污染更少？'
]

// 提取该条回答里可内嵌渲染的有效可视化（reset / 无数据的不渲染）
function inlineVizFor(response?: AIChatResponse | null): ActiveVisualization | null {
  const viz = response?.visualization
  if (!viz || viz.valid !== true || !viz.spec || !viz.data) return null
  if (response?.visualization_action === 'none' || response?.visualization_action === 'reset') return null
  return viz
}

function dynamicChartsFor(response?: AIChatResponse | null): DynamicChart[] {
  return response?.content_blocks?.flatMap(block => block.type === 'chart'
    && isVerifiedChatChart(block.chart) ? [block.chart] : []) ?? []
}
type ContentBlock = NonNullable<AIChatResponse['content_blocks']>[number]
type DisplayBlock = Exclude<ContentBlock, { type: 'chart' }> | { type: 'chart_group'; charts: DynamicChart[] }
function groupedBlocksFor(response?: AIChatResponse | null): DisplayBlock[] {
  const output: DisplayBlock[] = []
  let charts: DynamicChart[] = []
  const flush = () => {
    if (charts.length) output.push({ type: 'chart_group', charts })
    charts = []
  }
  for (const block of response?.content_blocks ?? []) {
    if (block.type === 'chart') {
      if (isVerifiedChatChart(block.chart)) charts.push(block.chart)
    }
    else { flush(); output.push(block) }
  }
  flush()
  return output
}
function hasRouteMap(response?: AIChatResponse | null): boolean {
  return Boolean(response?.content_blocks?.some(block => block.type === 'map' || block.type === 'propagation_map'))
}

const scopeNeedsAnalysis = computed(() => (
  analysisStore.analysisSource !== 'api'
  || analysisStore.analysisScopeDirty
  || aiStore.businessScopeStatus !== 'aligned'
))
const canSend = computed(() => Boolean(draft.value.trim()) && !aiStore.loading)
const trustedScopeLabel = computed(() => (
  aiStore.businessScopeStatus === 'aligned'
    ? formatAnalysisScope(aiStore.verifiedBusinessContext)
    : ''
))
const pendingScopeLabel = computed(() => formatAnalysisScope(toAnalysisScope(analysisStore.pendingTask)))

watch(
  () => [
    analysisStore.pendingTask,
    analysisStore.currentTask,
    analysisStore.analysisSource,
    analysisStore.analysisScopeDirty
  ] as const,
  () => {
    if (analysisStore.analysisSource === 'api'
      && analysisStore.currentTask
      && !analysisStore.analysisScopeDirty) {
      aiStore.acceptVerifiedBusinessScope(analysisStore.currentTask)
      return
    }
    aiStore.markBusinessScopeChanged(analysisStore.pendingTask)
  },
  { immediate: true }
)

watch(() => aiStore.businessScopeStatus, (status) => {
  if (status === 'aligned') void aiStore.restoreHistory()
}, { immediate: true })

function send(text?: string): void {
  const content = (text ?? draft.value).trim()
  const travelReady = Boolean(travelStore.currentResult
    && (!travelStore.live || travelStore.liveResult?.evidence_id))
  if (!content || aiStore.loading) return
  draft.value = ''
  const page = isTravelPage.value ? 'travel'
    : route.path === '/restriction' ? 'restriction'
    : route.path === '/weather' ? 'weather' : 'overview'
  const pageContext: Record<string, string> = isTravelPage.value && travelReady
    ? travelStore.live ? {
      page: 'travel', mode: 'live', evidence_id: travelStore.liveResult?.evidence_id || '',
      route_id: travelStore.selectedRouteId || '',
      profile_id: travelStore.profileId,
      depart_date: travelStore.departDate,
      depart_start: travelStore.departStart,
      depart_end: travelStore.departEnd
    } : {
      page: 'travel', mode: 'case',
      scenario_id: travelStore.currentScenario?.scenario_id || '',
      case_id: travelStore.currentCase?.case_id || '',
      profile_id: travelStore.profileId,
      route_id: travelStore.selectedRouteId || '',
      depart_date: travelStore.departDate,
      depart_start: travelStore.departStart,
      depart_end: travelStore.departEnd
    }
    : { page, ...(isTravelPage.value ? {
      profile_id: travelStore.profileId,
      depart_date: travelStore.departDate,
      depart_start: travelStore.departStart,
      depart_end: travelStore.departEnd
    } : {}) }
  const task = (!scopeNeedsAnalysis.value && !analysisStore.loading
    ? analysisStore.currentTask : null) as unknown as Record<string, unknown> | null
  void aiStore.send(content, task, pageContext)
}

</script>

<style scoped>
.ai-chat-panel {
  background: var(--panel-bg);
  border-radius: 12px;
  box-shadow: var(--shadow-sm);
  box-sizing: border-box;
  padding: 16px 16px 14px;
  height: var(--overview-main-card-height, 720px);
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* ===== 头部：蓝渐变圆角图标 + 标题 + 在线徽标（与截图一致） ===== */
.panel-header {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-color);
}

.header-icon {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  background: linear-gradient(135deg, var(--theme-color), #72baff);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.header-texts {
  min-width: 0;
  flex: 1;
}

.header-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.panel-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.live-tag {
  border: none;
  font-weight: 500;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.header-sub {
  font-size: 11px;
  color: var(--text-tertiary);
  margin-top: 2px;
}

.kb-btn {
  flex-shrink: 0;
  color: var(--text-secondary);
}

/* ===== 会话/范围说明 ===== */
.meta-note {
  flex: 0 0 auto;
  font-size: 11px;
  color: var(--text-tertiary);
  margin-bottom: 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.scope-note {
  flex: 0 0 auto;
  font-size: 11px;
  color: var(--text-secondary);
  margin-bottom: 6px;
}

.scope-warning {
  flex: 0 0 auto;
  font-size: 11px;
  margin-bottom: 6px;
}

/* ===== 消息区：头像 + 气泡布局 ===== */
.messages {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding-right: 4px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.msg {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.msg.user {
  flex-direction: row-reverse;
}

.avatar {
  flex: 0 0 28px;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--theme-color-lighter);
  color: var(--theme-color);
  font-size: 16px;
}

.msg.user .avatar {
  background: #e6f4ea;
  color: #37a35a;
}

.bubble {
  max-width: calc(100% - 44px);
  background: var(--bg-page);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-primary);
  min-width: 0;
}

.msg.assistant .bubble.with-chart {
  width: calc(100% - 44px);
  box-sizing: border-box;
  align-self: flex-start;
  container-type: inline-size;
  container-name: assistant-chart;
}

.msg.user .bubble {
  background: var(--theme-color);
  color: #fff;
}

.chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  width: 100%;
  margin: 16px auto 0;
}

.chart-grid.single-chart { grid-template-columns: minmax(0, 1fr); }
.chart-grid :deep(.dynamic-chart-card) { max-width: none; margin: 0; min-width: 0; }
.chart-grid:not(.single-chart) :deep(.dynamic-chart-card:last-child:nth-child(odd)) { grid-column: 1 / -1; }
@container assistant-chart (max-width: 560px) {
  .chart-grid { grid-template-columns: minmax(0, 1fr); }
}

/* 建议卡片等内容撑满气泡可用宽度 */
.bubble :deep(.recommendation-answer) {
  width: 100%;
}

.bubble :deep(.message-content) {
  white-space: pre-wrap;
  word-break: break-word;
}

.text {
  margin: 0;
  word-break: break-word;
  white-space: pre-wrap;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.history-note { font-size: 11px; color: var(--text-tertiary); margin-bottom: 6px; }

.chip {
  font-size: 12px;
  padding: 2px 9px;
  border-radius: 12px;
  border: 1px solid var(--border-color);
  background: var(--panel-bg);
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s;
}

.chip:hover {
  border-color: var(--theme-color);
  color: var(--theme-color);
  background: var(--theme-color-lighter);
}

/* ===== 气泡内内嵌图表卡片 ===== */
.inline-chart-card {
  width: 100%;
  margin: 16px 0 0;
}

.inline-chart-card :deep(.ai-visualization-renderer) {
  min-height: 0;
  height: auto;
}

.inline-chart-card :deep(.ai-chart) {
  height: 280px;
  min-height: 260px;
}

.inline-chart-card :deep(.provenance) {
  padding-top: 4px;
}

/* ===== 气泡内的元信息徽标 ===== */
.message-meta {
  font-size: 10px;
  color: var(--text-tertiary);
  margin-top: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.knowledge-status {
  margin-top: 6px;
}

.status-note {
  font-size: 10px;
  color: var(--text-tertiary);
}

/* ===== 打字指示器 ===== */
.typing {
  display: inline-flex;
  gap: 3px;
  vertical-align: middle;
}

.typing i {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--theme-color);
  display: inline-block;
  animation: blink 1.2s infinite ease-in-out both;
}

.typing i:nth-child(2) {
  animation-delay: 0.2s;
}

.typing i:nth-child(3) {
  animation-delay: 0.4s;
}

.typing-text {
  margin-left: 6px;
  color: var(--text-tertiary);
}

@keyframes blink {
  0%,
  80%,
  100% {
    opacity: 0.25;
  }
  40% {
    opacity: 1;
  }
}

/* ===== 错误与输入区 ===== */
.service-error {
  flex: 0 0 auto;
  font-size: 12px;
  margin-top: 8px;
}

.chat-input {
  flex: 0 0 auto;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--border-color);
}

.quick-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
  max-height: 144px;
  overflow-y: auto;
}

.input-row {
  display: flex;
  gap: 8px;
}

.input-row :deep(.el-input) {
  flex: 1;
}
</style>
