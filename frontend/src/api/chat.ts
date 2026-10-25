import request from './request'

/**
 * AI 研判问答接口。
 *
 * 所有问题交给后端 /api/chat，
 * 由大模型结合当前分析链路的结构化上下文作答。
 * 后端 LLM 不可达时返回 502，不使用本地关键词规则应答。
 */

export interface AiChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface AiAnswerParams {
  question: string
  /** 最近几轮对话（不含本次提问），后端只取最后 6 条 */
  history?: AiChatMessage[]
  /** 当前分析链路的结构化上下文（由前端从 store 里提炼） */
  context?: Record<string, unknown> | null
}

export interface AiAnswerResponse {
  status: string
  answer: string
  model: string
}

/** LLM 生成较慢（经代理实测单请求可超 1 分钟），超时给足 120 s */
const AI_TIMEOUT_MS = 120000

export function fetchAiAnswer(params: AiAnswerParams): Promise<AiAnswerResponse> {
  return request({
    url: '/api/chat',
    method: 'POST',
    data: params,
    timeout: AI_TIMEOUT_MS
  }) as Promise<AiAnswerResponse>
}
