/**
 * Knowledge base API client for AERION AI Phase 4B
 * Connects to ai_service/knowledge endpoints
 */

import type {
  KnowledgeDocument,
  DocumentListResponse,
  DocumentUploadRequest,
  DocumentUploadResponse,
  DocumentDeleteResponse,
  RetrievalResult,
  SearchRequest,
  KnowledgeError
} from '@/types/knowledge'

const DEFAULT_AI_BASE = '/ai-service'
const KB_TIMEOUT_MS = 60000  // 60s for first embedding model load

export class KnowledgeHttpError extends Error {
  readonly status: number
  readonly code: string
  readonly retryable: boolean
  readonly existingDocumentId?: string

  constructor(message: string, status: number, code = 'knowledge_error', retryable = false, existingDocumentId?: string) {
    super(message)
    this.name = 'KnowledgeHttpError'
    this.status = status
    this.code = code
    this.retryable = retryable
    this.existingDocumentId = existingDocumentId
  }
}

function baseUrl(): string {
  const configured = import.meta.env.VITE_AI_SERVICE_BASE_URL || DEFAULT_AI_BASE
  return configured.replace(/\/$/, '')
}

function timeoutSignal(timeoutMs: number): { signal: AbortSignal; cancel: () => void } {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  return { signal: controller.signal, cancel: () => window.clearTimeout(timer) }
}

async function handleKnowledgeResponse<T>(response: Response): Promise<T> {
  const body = (await response.json().catch(() => null)) as
    | T
    | { error?: KnowledgeError; existing_document_id?: string; status?: string; message?: string }
    | null

  if (!response.ok) {
    const structured = body && typeof body === 'object' && 'error' in body ? body.error : undefined
    const messageFromBody = body && typeof body === 'object' && 'message' in body ? (body as { message?: string }).message : undefined
    const message = structured?.message || messageFromBody || `Knowledge API HTTP ${response.status}`
    const code = structured?.code || (response.status === 403 ? 'knowledge_access_denied' : response.status === 409 ? 'duplicate_document' : 'knowledge_error')
    const retryable = Boolean(structured?.retryable) || response.status >= 500
    const existingDocumentId = body && typeof body === 'object' && 'existing_document_id' in body ? (body as { existing_document_id?: string }).existing_document_id : undefined

    throw new KnowledgeHttpError(message, response.status, code, retryable, existingDocumentId)
  }

  if (!body || typeof body !== 'object') {
    throw new KnowledgeHttpError('Knowledge API 返回结构无效', response.status, 'invalid_knowledge_response')
  }

  return body as T
}

/**
 * Upload a document to the knowledge base.
 * Returns 201 on success, 409 if duplicate (check error.existingDocumentId).
 */
export async function uploadDocument(request: DocumentUploadRequest): Promise<DocumentUploadResponse> {
  const timeout = timeoutSignal(KB_TIMEOUT_MS)
  const formData = new FormData()
  formData.append('file', request.file)
  formData.append('title', request.title)
  if (request.source) formData.append('source', request.source)
  if (request.author) formData.append('author', request.author)
  if (request.year !== undefined) formData.append('year', String(request.year))
  if (request.category) formData.append('category', request.category)
  if (request.topics && request.topics.length > 0) {
    formData.append('topics', JSON.stringify(request.topics))
  }

  try {
    const response = await fetch(`${baseUrl()}/api/knowledge/documents`, {
      method: 'POST',
      body: formData,
      signal: timeout.signal
    })
    return await handleKnowledgeResponse<DocumentUploadResponse>(response)
  } catch (error) {
    if (error instanceof KnowledgeHttpError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new KnowledgeHttpError('知识库上传超时', 504, 'knowledge_timeout', true)
    }
    throw new KnowledgeHttpError('知识库服务不可达', 503, 'knowledge_unavailable', true)
  } finally {
    timeout.cancel()
  }
}

/**
 * List documents. Optional filters: scope, category, status.
 */
export async function listDocuments(filters?: {
  scope?: string
  category?: string
  status?: string
}): Promise<DocumentListResponse> {
  const timeout = timeoutSignal(KB_TIMEOUT_MS)
  const params = new URLSearchParams()
  if (filters?.scope) params.append('scope', filters.scope)
  if (filters?.category) params.append('category', filters.category)
  if (filters?.status) params.append('status', filters.status)

  try {
    const url = `${baseUrl()}/api/knowledge/documents${params.toString() ? '?' + params.toString() : ''}`
    const response = await fetch(url, {
      method: 'GET',
      signal: timeout.signal
    })
    return await handleKnowledgeResponse<DocumentListResponse>(response)
  } catch (error) {
    if (error instanceof KnowledgeHttpError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new KnowledgeHttpError('知识库列表请求超时', 504, 'knowledge_timeout', true)
    }
    throw new KnowledgeHttpError('知识库服务不可达', 503, 'knowledge_unavailable', true)
  } finally {
    timeout.cancel()
  }
}

/**
 * Get a single document by ID.
 */
export async function getDocument(documentId: string): Promise<KnowledgeDocument> {
  const timeout = timeoutSignal(KB_TIMEOUT_MS)
  try {
    const response = await fetch(`${baseUrl()}/api/knowledge/documents/${encodeURIComponent(documentId)}`, {
      method: 'GET',
      signal: timeout.signal
    })
    const result = await handleKnowledgeResponse<{ status: string; document: KnowledgeDocument }>(response)
    return result.document
  } catch (error) {
    if (error instanceof KnowledgeHttpError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new KnowledgeHttpError('知识库文档请求超时', 504, 'knowledge_timeout', true)
    }
    throw new KnowledgeHttpError('知识库服务不可达', 503, 'knowledge_unavailable', true)
  } finally {
    timeout.cancel()
  }
}

/**
 * Update document metadata. Allowed fields: enabled, title, category, topics, source, author, year.
 */
export async function updateDocument(
  documentId: string,
  updates: Partial<Pick<KnowledgeDocument, 'enabled' | 'title' | 'category' | 'topics' | 'source' | 'author' | 'year'>>
): Promise<KnowledgeDocument> {
  const timeout = timeoutSignal(KB_TIMEOUT_MS)
  try {
    const response = await fetch(`${baseUrl()}/api/knowledge/documents/${encodeURIComponent(documentId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
      signal: timeout.signal
    })
    const result = await handleKnowledgeResponse<{ status: string; document: KnowledgeDocument }>(response)
    return result.document
  } catch (error) {
    if (error instanceof KnowledgeHttpError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new KnowledgeHttpError('知识库更新超时', 504, 'knowledge_timeout', true)
    }
    throw new KnowledgeHttpError('知识库服务不可达', 503, 'knowledge_unavailable', true)
  } finally {
    timeout.cancel()
  }
}

/**
 * Delete a document and its vectors.
 */
export async function deleteDocument(documentId: string): Promise<void> {
  const timeout = timeoutSignal(KB_TIMEOUT_MS)
  try {
    const response = await fetch(`${baseUrl()}/api/knowledge/documents/${encodeURIComponent(documentId)}`, {
      method: 'DELETE',
      signal: timeout.signal
    })
    await handleKnowledgeResponse<DocumentDeleteResponse>(response)
  } catch (error) {
    if (error instanceof KnowledgeHttpError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new KnowledgeHttpError('知识库删除超时', 504, 'knowledge_timeout', true)
    }
    throw new KnowledgeHttpError('知识库服务不可达', 503, 'knowledge_unavailable', true)
  } finally {
    timeout.cancel()
  }
}

/**
 * Search knowledge base.
 */
export async function searchKnowledge(request: SearchRequest): Promise<RetrievalResult> {
  const timeout = timeoutSignal(KB_TIMEOUT_MS)
  try {
    const response = await fetch(`${baseUrl()}/api/knowledge/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal: timeout.signal
    })
    return await handleKnowledgeResponse<RetrievalResult>(response)
  } catch (error) {
    if (error instanceof KnowledgeHttpError) throw error
    if (error instanceof DOMException && error.name === 'AbortError') {
      throw new KnowledgeHttpError('知识库搜索超时', 504, 'knowledge_timeout', true)
    }
    throw new KnowledgeHttpError('知识库服务不可达', 503, 'knowledge_unavailable', true)
  } finally {
    timeout.cancel()
  }
}
