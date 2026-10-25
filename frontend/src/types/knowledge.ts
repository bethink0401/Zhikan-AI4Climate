/**
 * Knowledge base types for AERION AI Phase 4B
 * Matches ai_service/knowledge/models.py contract
 */

export type KnowledgeScope = 'system' | 'user'
export type DocumentStatus = 'uploaded' | 'parsing' | 'indexing' | 'indexed' | 'failed' | 'requires_ocr'
export type AuthorityLevel = 'official' | 'peer_reviewed' | 'internal' | 'user_uploaded' | 'unknown'

export interface KnowledgeDocument {
  document_id: string
  title: string
  original_filename: string
  stored_filename: string
  scope: KnowledgeScope
  owner_id: string
  source: string | null
  author: string | null
  year: number | null
  category: string
  topics: string[]
  authority_level: AuthorityLevel
  mime_type: string
  file_size: number
  checksum: string
  status: DocumentStatus
  enabled: boolean
  chunk_count: number
  created_at: string
  updated_at: string
  error_message: string | null
  metadata_trust: string
}

export interface RetrievalHit {
  chunk_id: string
  document_id: string
  text: string
  similarity_score: number
  title: string
  source: string | null
  author: string | null
  year: number | null
  page: number | null
  page_start: number | null
  page_end: number | null
  section: string | null
  category: string
  scope: string
  owner_id: string
  authority_level: string
}

/** Citation-oriented source returned inside ChatResponse.knowledge.sources. */
export interface KnowledgeSource {
  document_id: string
  title: string
  source: string | null
  author: string | null
  year: number | null
  page: number | null
  page_start: number | null
  page_end: number | null
  section: string | null
  category: string
  scope: string
  authority_level: string
  label: string
  excerpt: string
  similarity_score?: number
  citation_verified: boolean
  metadata_trust: string
}

export interface RetrievalResult {
  query: string
  results: RetrievalHit[]
  total_hits: number
  index_version: string
  embedding_model: string
}

export interface KnowledgeContext {
  used: boolean
  retrieval_status: 'success' | 'no_hits' | 'failed' | 'not_attempted' | 'disabled'
  sources: KnowledgeSource[]
  warnings: string[]
  reason: string | null
  query: string | null
  total_hits: number
  index_version: string | null
  embedding_model: string | null
}

export interface FusedEvidence {
  /** Current Phase 4A payload: validated Agent envelopes used as business facts. */
  facts?: Array<{
    agent: string
    result: Record<string, unknown>
  }>
  /** Current Phase 4A payload: traceable retrieval hits only. */
  knowledge_evidence?: RetrievalHit[]
  limitations?: string[]
  /** Older compatible payload fields retained for already-saved responses. */
  business_facts_preserved?: boolean
  knowledge_sources_count?: number
  fused_sections?: Array<{
    type: 'business' | 'literature' | 'combined'
    content: string
    sources: string[]
  }>
  warnings?: string[]
}

export interface DocumentUploadRequest {
  file: File
  title: string
  source?: string
  author?: string
  year?: number
  category?: string
  topics?: string[]
}

export interface DocumentListResponse {
  status: 'success'
  documents: KnowledgeDocument[]
  categories: string[]
  access_mode: string
}

export interface DocumentUploadResponse {
  status: DocumentStatus
  document: KnowledgeDocument
}

export interface DocumentDeleteResponse {
  status: 'deleted'
  document_id: string
}

export interface KnowledgeError {
  code: string
  message: string
  retryable?: boolean
  existing_document_id?: string
}

export interface SearchRequest {
  query: string
  top_k?: number
  scope?: KnowledgeScope[]
  categories?: string[]
  document_ids?: string[]
  enabled_only?: boolean
}
