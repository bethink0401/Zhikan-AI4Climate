import type { AIChatResponse, AIRequestContext } from '@/types/aiChat'

const DEFAULT_AI_BASE = '/ai-service'
const AI_TIMEOUT_MS = 300000

export class AIServiceHttpError extends Error {
  readonly status: number
  readonly code: string
  readonly retryable: boolean

  constructor(message: string, status: number, code = 'ai_service_error', retryable = false) {
    super(message)
    this.name = 'AIServiceHttpError'
    this.status = status
    this.code = code
    this.retryable = retryable
  }
}

function endpoint(): string {
  const configured = import.meta.env.VITE_AI_SERVICE_BASE_URL || DEFAULT_AI_BASE
  return `${configured.replace(/\/$/, '')}/api/ai/v1/chat`
}

function timeoutSignal(timeoutMs: number): { signal: AbortSignal; reset: () => void; cancel: () => void } {
  const controller = new AbortController()
  let timer = window.setTimeout(() => controller.abort(), timeoutMs)
  return { signal: controller.signal,
    reset: () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => controller.abort(), timeoutMs)
    },
    cancel: () => {
      window.clearTimeout(timer)
      controller.abort()
    } }
}

export async function postAIChat(
  sessionId: string,
  userInput: string,
  requestContext?: AIRequestContext | null
  ,pageContext?: Record<string, string> | null
): Promise<AIChatResponse> {
  const timeout = timeoutSignal(AI_TIMEOUT_MS)
  try {
    const response = await fetch(endpoint(), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        user_input: userInput,
        // Only task selectors are sent.  Evidence, agent_results and URLs are
        // intentionally not accepted from the browser trust boundary.
        request_context: requestContext || undefined,
        page_context: pageContext || undefined
      }),
      signal: timeout.signal
    })
    const body = (await response.json().catch(() => null)) as
      | AIChatResponse
      | { error?: { code?: string; message?: string; retryable?: boolean } }
      | null
    if (!response.ok) {
      const structured = body && 'error' in body ? body.error : undefined
      throw new AIServiceHttpError(
        structured?.message || `AI Service HTTP ${response.status}`,
        response.status,
        structured?.code || (response.status === 409 ? 'session_conflict' : 'ai_service_error'),
        Boolean(structured?.retryable) || response.status === 409 || response.status >= 500
      )
    }
    if (!body || typeof body !== 'object' || !('answer' in body) || !('business_action' in body)) {
      throw new AIServiceHttpError('AI Service 返回结构无效', response.status, 'invalid_ai_response')
    }
    return body as AIChatResponse
  } catch (error) {
    if (error instanceof AIServiceHttpError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new AIServiceHttpError('AI Service 请求超时', 504, 'ai_service_timeout', true)
    }
    throw new AIServiceHttpError('AI Service 当前不可达', 503, 'ai_service_unavailable', true)
  } finally {
    timeout.cancel()
  }
}

export async function postAIChatStream(
  sessionId: string,
  userInput: string,
  requestContext: AIRequestContext | null | undefined,
  onStatus: (message: string) => void,
  pageContext?: Record<string, string> | null
): Promise<AIChatResponse> {
  const timeout = timeoutSignal(AI_TIMEOUT_MS)
  try {
    const response = await fetch(`${endpoint()}/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ session_id: sessionId, user_input: userInput,
        request_context: requestContext || undefined,
        page_context: pageContext || undefined }),
      signal: timeout.signal
    })
    if (response.status === 404) return postAIChat(sessionId, userInput, requestContext, pageContext)
    if (!response.ok || !response.body) {
      throw new AIServiceHttpError(`AI Service HTTP ${response.status}`, response.status,
        'ai_service_error', response.status >= 500)
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let result: AIChatResponse | null = null
    function consume(chunk: string): void {
      const name = chunk.match(/^event: (.+)$/m)?.[1]
      const data = chunk.match(/^data: (.+)$/m)?.[1]
      if (!name || !data) return
      const value = JSON.parse(data) as Record<string, unknown>
      if (name === 'status' && typeof value.message === 'string') onStatus(value.message)
      if (name === 'response') result = value as unknown as AIChatResponse
      if (name === 'error') {
        const message = String(value.message || 'AI Service 请求失败')
        const detail = message === 'Invalid travel mode'
          ? 'AI 服务仍在运行旧版路线规划代码，请重启 8010 端口的独立 AI 服务后重试。'
          : message
        throw new AIServiceHttpError(detail, Number(value.status) || 500,
          String(value.code || 'ai_service_error'), Boolean(value.retryable))
      }
    }
    while (true) {
      const { done, value } = await reader.read()
      if (!done) timeout.reset()
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        consume(buffer.slice(0, boundary))
        buffer = buffer.slice(boundary + 2)
        boundary = buffer.indexOf('\n\n')
      }
      if (done) break
    }
    if (!result) throw new AIServiceHttpError('AI Service 未返回完整结果', 502, 'incomplete_ai_response', true)
    return result
  } catch (error) {
    if (error instanceof AIServiceHttpError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new AIServiceHttpError('AI Service 请求超时', 504, 'ai_service_timeout', true)
    }
    throw new AIServiceHttpError('AI Service 当前不可达', 503, 'ai_service_unavailable', true)
  } finally {
    timeout.cancel()
  }
}

export async function getAIChatSession(sessionId: string): Promise<{
  context: AIRequestContext
  messages: Array<{ role: 'user' | 'assistant'; content: string;
    content_blocks?: AIChatResponse['content_blocks'];
    visualization_action?: AIChatResponse['visualization_action'];
    visualization?: AIChatResponse['visualization'];
    recommendation_display?: AIChatResponse['recommendation_display'] }>
}> {
  const base = endpoint().replace(/\/chat$/, '')
  const response = await fetch(`${base}/session/${encodeURIComponent(sessionId)}/messages`)
  if (!response.ok) throw new AIServiceHttpError('无法恢复对话记录', response.status)
  return response.json()
}
