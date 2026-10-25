"""Stable document, chunk, retrieval, and caller identity contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal


KnowledgeScope = Literal["system", "user"]
DocumentStatus = Literal[
    "uploaded", "parsing", "indexing", "indexed", "failed", "requires_ocr"
]
AuthorityLevel = Literal[
    "official", "peer_reviewed", "internal", "user_uploaded", "unknown"
]

CATEGORY_WHITELIST = frozenset({
    "air_quality_standard", "health_guidance", "pollutant_mechanism",
    "meteorology_dispersion", "traffic_pollution", "governance",
    "environmental_policy", "project_internal", "research", "other",
})
SCOPE_WHITELIST = frozenset({"system", "user"})
AUTHORITY_WHITELIST = frozenset({
    "official", "peer_reviewed", "internal", "user_uploaded", "unknown",
})


@dataclass(frozen=True, slots=True)
class KnowledgePrincipal:
    """Caller identity supplied by an access boundary, never by session_id."""

    principal_id: str
    roles: tuple[str, ...] = ()
    allowed_scopes: tuple[str, ...] = ("system", "user")
    local_development: bool = False


@dataclass(slots=True)
class KnowledgeDocument:
    document_id: str
    title: str
    original_filename: str
    stored_filename: str
    scope: KnowledgeScope
    owner_id: str
    source: str | None
    author: str | None
    year: int | None
    category: str
    topics: list[str]
    authority_level: AuthorityLevel
    mime_type: str
    file_size: int
    checksum: str
    status: DocumentStatus
    enabled: bool
    chunk_count: int
    created_at: str
    updated_at: str
    error_message: str | None = None
    metadata_trust: str = "user_asserted"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class KnowledgeChunk:
    chunk_id: str
    document_id: str
    owner_id: str
    text: str
    chunk_index: int
    page: int | None
    page_start: int | None
    page_end: int | None
    section: str | None
    topics: list[str]
    source: str | None
    title: str
    year: int | None
    category: str
    scope: KnowledgeScope
    authority_level: AuthorityLevel
    enabled: bool
    text_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    text: str
    page: int | None = None
    section: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    chunk_id: str
    document_id: str
    text: str
    similarity_score: float
    title: str
    source: str | None
    author: str | None
    year: int | None
    page: int | None
    page_start: int | None
    page_end: int | None
    section: str | None
    category: str
    scope: str
    owner_id: str
    authority_level: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    query: str
    results: tuple[RetrievalHit, ...]
    total_hits: int
    index_version: str
    embedding_model: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": [item.to_dict() for item in self.results],
            "total_hits": self.total_hits,
            "index_version": self.index_version,
            "embedding_model": self.embedding_model,
        }
