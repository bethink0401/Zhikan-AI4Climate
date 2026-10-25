import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { AIServiceHttpError, getAIChatSession, postAIChatStream } from '@/api/aiChat'
import type {
  AIChatErrorInfo,
  AIChatMessage,
  AIChatResponse,
  AIRequestContext,
  ActiveVisualization,
  VisualizationSpec
} from '@/types/aiChat'
import {
  sameAnalysisScope,
  toAnalysisScope,
  type AnalysisScope
} from '@/utils/analysisScope'

const SESSION_STORAGE_KEY = 'weather-multi-agent.ai-session-id'

function newSessionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `ai-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function loadSessionId(): string {
  try {
    const existing = sessionStorage.getItem(SESSION_STORAGE_KEY)
    if (existing) return existing
  } catch {
    // Private browsing or server-side rendering: use an in-memory id.
  }
  const value = newSessionId()
  try {
    sessionStorage.setItem(SESSION_STORAGE_KEY, value)
  } catch {
    // The service still works for the lifetime of this page.
  }
  return value
}

function saveSessionId(value: string): void {
  try {
    sessionStorage.setItem(SESSION_STORAGE_KEY, value)
  } catch {
    // Best effort only; no server state is overwritten by this failure.
  }
}

function selectorsOnly(context: Record<string, unknown> | null | undefined): AIRequestContext | null {
  if (!context) return null
  const output: AIRequestContext = {}
  const fields: Array<keyof AIRequestContext> = [
    'task_id',
    'station_id',
    'station_name',
    'start_time',
    'end_time',
    'lat',
    'lon'
  ]
  fields.forEach((field) => {
    const value = context[field]
    if (typeof value === 'string' || typeof value === 'number') output[field] = value as never
  })
  return Object.keys(output).length ? output : null
}

function validVisualization(response: AIChatResponse): ActiveVisualization | null {
  const action = response.visualization_action
  const candidate = response.visualization
  if (!candidate || candidate.valid !== true) return null
  // `modify` is retained for older contract producers; it has the same safe
  // state transition as the phase-3 `update` action.
  if (!['show', 'modify', 'update', 'reset'].includes(action)) return null
  if (typeof candidate.revision !== 'number') return null
  if (action !== 'reset' && (!candidate.spec || !candidate.data)) return null
  return candidate
}

export const useAiChatStore = defineStore('aiChat', () => {
  const sessionId = ref(loadSessionId())
  const messages = ref<AIChatMessage[]>([])
  const taskContext = ref<AIRequestContext | null>(null)
  const loading = ref(false)
  const progressMessage = ref('正在结合当前分析思考…')
  const error = ref<AIChatErrorInfo | null>(null)
  const activeVisualization = ref<ActiveVisualization | null>(null)
  const viewMode = ref<'map' | 'chart'>('map')
  const visualizationRevision = ref(0)
  const recentSpec = ref<VisualizationSpec | null>(null)
  const dataSnapshot = ref<ActiveVisualization['data']>(null)
  const requestSequence = ref(0)
  const latestAppliedRequestId = ref(0)
  const latestSuccessfulRequestId = ref<number | null>(null)
  const verifiedBusinessContext = ref<AnalysisScope | null>(null)
  const pendingBusinessContext = ref<AnalysisScope | null>(null)
  const businessScopeStatus = ref<'unverified' | 'aligned' | 'changed'>('unverified')
  /** Incrementing this invalidates every response started under an older UI scope. */
  const scopeEpoch = ref(0)
  const restoredSessionId = ref<string | null>(null)

  const hasMessages = computed(() => messages.value.length > 0)
  const hasActiveChart = computed(() => (
    activeVisualization.value?.valid === true &&
    recentSpec.value?.view_type === 'chart' &&
    Boolean(dataSnapshot.value)
  ))

  function ensureSession(): string {
    if (!sessionId.value) {
      sessionId.value = newSessionId()
      saveSessionId(sessionId.value)
    }
    return sessionId.value
  }

  function newSession(): void {
    scopeEpoch.value += 1
    restoredSessionId.value = null
    sessionId.value = newSessionId()
    saveSessionId(sessionId.value)
    messages.value = []
    taskContext.value = verifiedBusinessContext.value
    loading.value = false
    progressMessage.value = '正在结合当前分析思考…'
    error.value = null
    activeVisualization.value = null
    viewMode.value = 'map'
    visualizationRevision.value = 0
    recentSpec.value = null
    dataSnapshot.value = null
    requestSequence.value = 0
    latestAppliedRequestId.value = 0
    latestSuccessfulRequestId.value = null
    businessScopeStatus.value = verifiedBusinessContext.value ? 'aligned' : 'unverified'
  }

  function clearScopeBoundState(): void {
    restoredSessionId.value = null
    messages.value = []
    taskContext.value = null
    activeVisualization.value = null
    viewMode.value = 'map'
    visualizationRevision.value = 0
    recentSpec.value = null
    dataSnapshot.value = null
    latestSuccessfulRequestId.value = null
  }

  /** Mark form selection as untrusted until the business analysis succeeds. */
  function markBusinessScopeChanged(context: AIRequestContext | null): void {
    const next = toAnalysisScope(context)
    if (next && verifiedBusinessContext.value && sameAnalysisScope(next, verifiedBusinessContext.value)) {
      pendingBusinessContext.value = next
      businessScopeStatus.value = 'aligned'
      return
    }
    // Every selection mutation invalidates responses started under an older scope.
    scopeEpoch.value += 1
    pendingBusinessContext.value = next
    businessScopeStatus.value = 'changed'
    sessionId.value = newSessionId()
    saveSessionId(sessionId.value)
    clearScopeBoundState()
  }

  /** Accept only a scope backed by a successful current business API response. */
  function acceptVerifiedBusinessScope(context: AIRequestContext): void {
    const next = toAnalysisScope(context)
    if (!next) return
    const changed = Boolean(verifiedBusinessContext.value
      && !sameAnalysisScope(next, verifiedBusinessContext.value))
    if (changed) {
      scopeEpoch.value += 1
      clearScopeBoundState()
    }
    verifiedBusinessContext.value = next
    pendingBusinessContext.value = next
    taskContext.value = next
    businessScopeStatus.value = 'aligned'
    error.value = null
  }

  function applyVisualization(response: AIChatResponse): void {
    const action = response.visualization_action
    if (action === 'none') return
    const candidate = validVisualization(response)
    if (!candidate) return
    // A late response must never move the UI backwards or overwrite a newer
    // server revision.  Equal revisions are idempotent duplicates.
    if (candidate.revision < visualizationRevision.value) return
    if (candidate.revision === visualizationRevision.value && action !== 'reset') return
    activeVisualization.value = candidate
    visualizationRevision.value = candidate.revision
    if (action === 'reset') {
      viewMode.value = 'map'
      recentSpec.value = null
      dataSnapshot.value = null
      return
    }
    if (candidate.spec && candidate.data) {
      // 可视化改为在对话气泡内内嵌呈现，主视图保持地图不再切换。
      recentSpec.value = candidate.spec
      dataSnapshot.value = candidate.data
    }
  }

  function setViewMode(mode: 'map' | 'chart'): void {
    // The local switch never mutates the server visualization revision or
    // discards its data. A chart can only be reopened when validated data is
    // still present in this session.
    if (mode === 'chart' && !hasActiveChart.value) return
    viewMode.value = mode
  }

  async function restoreHistory(): Promise<void> {
    if (businessScopeStatus.value !== 'aligned' || !verifiedBusinessContext.value
        || restoredSessionId.value === sessionId.value || messages.value.length) return
    const id = sessionId.value
    const epoch = scopeEpoch.value
    try {
      const saved = await getAIChatSession(id)
      if (id !== sessionId.value || epoch !== scopeEpoch.value || messages.value.length) return
      const actual = toAnalysisScope(saved.context)
      if (!actual || !sameAnalysisScope(actual, verifiedBusinessContext.value)) return
      messages.value = saved.messages.map((item, index) => ({
        restored: true,
        id: index + 1, role: item.role, content: item.content,
        requestId: Math.floor(index / 2) + 1,
        response: item.role === 'assistant' ? {
          contract_version: '1.0', answer: item.content, business_action: 'fallback',
          visualization_action: item.visualization_action || 'none', route: null,
          context: saved.context, agent_results: {},
          visualization: item.visualization,
          content_blocks: item.content_blocks,
          recommendation_display: item.recommendation_display
        } : undefined
      }))
      requestSequence.value = Math.ceil(saved.messages.length / 2)
      latestAppliedRequestId.value = requestSequence.value
      restoredSessionId.value = id
    } catch {
      // The current conversation remains usable when history is unavailable.
    }
  }

  async function send(userInput: string, context?: Record<string, unknown> | null,
    pageContext?: Record<string, string> | null): Promise<void> {
    const content = userInput.trim()
    if (!content || loading.value) return
    const requestId = ++requestSequence.value
    const requestScopeEpoch = scopeEpoch.value
    const requestContext = selectorsOnly(context) || taskContext.value
    messages.value.push({ id: requestId * 2 - 1, role: 'user', content, requestId })
    loading.value = true
    progressMessage.value = '正在准备分析…'
    error.value = null
    try {
      const response = await postAIChatStream(ensureSession(), content, requestContext,
        message => { if (requestId === requestSequence.value) progressMessage.value = message }, pageContext)
      if (requestScopeEpoch !== scopeEpoch.value) return
      if (requestId < latestAppliedRequestId.value) return
      const responseContext = selectorsOnly(response.context as unknown as Record<string, unknown>)
      const expectedScope = toAnalysisScope(requestContext)
      const actualScope = toAnalysisScope(responseContext)
      if (expectedScope && actualScope && !sameAnalysisScope(expectedScope, actualScope)) {
        latestAppliedRequestId.value = requestId
        error.value = {
          code: 'analysis_scope_conflict',
          message: '消息中的站点或时间与顶部已验证分析范围不一致，本次结果未应用。请先在顶部切换范围并重新分析。',
          retryable: false
        }
        messages.value.push({
          id: requestId * 2,
          role: 'assistant',
          content: '检测到你指定了不同的站点或时间。为避免混用旧证据，请先在顶部选择相应范围并点击“开始分析”。',
          requestId
        })
        return
      }
      latestAppliedRequestId.value = requestId
      if (responseContext) taskContext.value = responseContext
      applyVisualization(response)
      messages.value.push({ id: requestId * 2, role: 'assistant', content: response.answer, response, requestId })
      latestSuccessfulRequestId.value = requestId
    } catch (cause) {
      if (requestScopeEpoch !== scopeEpoch.value) return
      if (requestId < latestAppliedRequestId.value) return
      const info: AIChatErrorInfo = cause instanceof AIServiceHttpError
        ? { code: cause.code, message: cause.message, status: cause.status, retryable: cause.retryable }
        : { code: 'ai_service_error', message: 'AI Service 请求失败', retryable: true }
      error.value = info
    } finally {
      if (requestId === requestSequence.value) loading.value = false
    }
  }

  return {
    sessionId,
    messages,
    taskContext,
    loading,
    progressMessage,
    error,
    activeVisualization,
    viewMode,
    visualizationRevision,
    recentSpec,
    dataSnapshot,
    latestSuccessfulRequestId,
    verifiedBusinessContext,
    pendingBusinessContext,
    businessScopeStatus,
    hasMessages,
    hasActiveChart,
    ensureSession,
    newSession,
    setViewMode,
    markBusinessScopeChanged,
    acceptVerifiedBusinessScope,
    restoreHistory,
    send
  }
})
