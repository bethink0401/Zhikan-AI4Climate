"""Safe retrieval adapter; document text is always treated as untrusted."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable, Literal

from .config import KnowledgeConfig
from .manager import KnowledgeBaseManager
from .models import KnowledgePrincipal, RetrievalHit
from .resolver import KnowledgeNeedResolution


logger = logging.getLogger(__name__)


RetrievalStatus = Literal["success", "no_hit", "disabled", "failed"]
_AUTHORITY = {"official": 4, "peer_reviewed": 3, "internal": 2, "user_uploaded": 1, "unknown": 0}
_INSTRUCTIONS = (
    r"ignore\s+(all\s+)?previous\s+instructions?", r"system\s+prompt",
    r"call\s+(a\s+)?tool", r"you\s+are\s+now", r"忽略.{0,12}(指令|提示)",
    r"系统提示", r"调用.{0,8}工具", r"你必须回答", r"泄露.{0,8}(密钥|配置)",
)
_CURRENT_OBSERVATION = re.compile(
    r"(?:当前|本次|该站|本站|监测到|实测).{0,80}(?:\d|aqhi|pm2\.5|no2)",
    re.IGNORECASE,
)


def contains_instruction_content(text: str) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in _INSTRUCTIONS)


def safe_excerpt(text: str, limit: int = 280) -> str:
    # 英文句点只在后随空白时才视为句子边界，避免把 "PM2.5" 截成两段
    parts = re.split(r"(?<=[。！？])\s*|(?<=\.)\s+|[\r\n]+", text)
    safe = [
        part.strip() for part in parts
        if part.strip()
        and not contains_instruction_content(part)
        and not _CURRENT_OBSERVATION.search(part)
    ]
    value = " ".join(safe)
    if not value:
        return ""
    return value if len(value) <= limit else value[:limit - 1].rstrip() + "…"


@dataclass(frozen=True, slots=True)
class KnowledgeEvidence:
    chunk_id: str
    document_id: str
    excerpt: str
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
    authority_level: str
    similarity_score: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class KnowledgeSource:
    document_id: str
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
    authority_level: str
    label: str
    excerpt: str
    similarity_score: float
    citation_verified: bool
    metadata_trust: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class KnowledgeContext:
    used: bool
    query: str
    topics: tuple[str, ...]
    categories: tuple[str, ...]
    hits: tuple[KnowledgeEvidence, ...]
    sources: tuple[KnowledgeSource, ...]
    retrieval_status: RetrievalStatus
    warnings: tuple[str, ...] = ()
    need_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "used": self.used, "query": self.query, "topics": list(self.topics),
            "categories": list(self.categories), "hits": [item.to_dict() for item in self.hits],
            "sources": [item.to_dict() for item in self.sources],
            "retrieval_status": self.retrieval_status, "warnings": list(self.warnings),
            "need_reason": self.need_reason,
        }

    @classmethod
    def not_used(cls, resolution: KnowledgeNeedResolution) -> "KnowledgeContext":
        return cls(False, resolution.query, resolution.knowledge_topics, resolution.categories,
                   (), (), "disabled", (), resolution.reason)


class KnowledgeRAGService:
    def __init__(
        self, *, config: KnowledgeConfig,
        manager_provider: Callable[[], KnowledgeBaseManager],
        principal: KnowledgePrincipal,
    ) -> None:
        self.config = config
        self.manager_provider = manager_provider
        self.principal = principal

    def retrieve(self, resolution: KnowledgeNeedResolution) -> KnowledgeContext:
        if not resolution.needs_rag:
            return KnowledgeContext.not_used(resolution)
        if not self.config.enabled:
            return KnowledgeContext(False, resolution.query, resolution.knowledge_topics,
                resolution.categories, (), (), "disabled", ("rag_disabled",), resolution.reason)
        try:
            manager = self.manager_provider()
            result = manager.search(
                resolution.query, principal=self.principal,
                top_k=min(resolution.max_hits, self.config.top_k),
                scope=resolution.scope, categories=resolution.categories,
            )
            raw = [hit for hit in result.results if hit.similarity_score >= self.config.retrieval_min_score]
            if not raw and resolution.categories:
                # 请求的类别在库中不存在（或过滤过严）时回退为不限类别，
                # 避免"触发成功但必然检索为空"的死路。
                result = manager.search(
                    resolution.query, principal=self.principal,
                    top_k=min(resolution.max_hits, self.config.top_k),
                    scope=resolution.scope,
                )
                raw = [hit for hit in result.results
                       if hit.similarity_score >= self.config.retrieval_min_score]
            ranked = sorted(raw, key=self._rank, reverse=True)
            selected = self._deduplicate(ranked, self.config.max_context_chunks)
            if not selected:
                return KnowledgeContext(False, resolution.query, resolution.knowledge_topics,
                    resolution.categories, (), (), "no_hit", ("no_reliable_knowledge_hit",), resolution.reason)
            warnings: list[str] = []
            evidence: list[KnowledgeEvidence] = []
            sources: list[KnowledgeSource] = []
            for hit in selected:
                document = manager.get_document(hit.document_id, principal=self.principal)
                excerpt = safe_excerpt(hit.text)
                if contains_instruction_content(hit.text):
                    warnings.append("untrusted_instruction_content_removed")
                if _CURRENT_OBSERVATION.search(hit.text):
                    warnings.append("untrusted_current_observation_removed")
                item = KnowledgeEvidence(
                    hit.chunk_id, hit.document_id, excerpt, hit.title, hit.source, hit.author,
                    hit.year, hit.page, hit.page_start, hit.page_end, hit.section, hit.category,
                    hit.scope, hit.authority_level, hit.similarity_score,
                )
                evidence.append(item)
                sources.append(KnowledgeSource(
                    document_id=hit.document_id, title=hit.title, source=hit.source,
                    author=hit.author, year=hit.year, page=hit.page, page_start=hit.page_start,
                    page_end=hit.page_end, section=hit.section, category=hit.category,
                    scope=hit.scope, authority_level=hit.authority_level,
                    label="系统知识" if hit.scope == "system" else "用户资料",
                    excerpt=excerpt, similarity_score=hit.similarity_score,
                    citation_verified=hit.scope == "system",
                    metadata_trust=document.metadata_trust,
                ))
            return KnowledgeContext(True, resolution.query, resolution.knowledge_topics,
                resolution.categories, tuple(evidence), tuple(sources), "success",
                tuple(dict.fromkeys(warnings)), resolution.reason)
        except Exception:
            logger.exception("knowledge retrieval failed")
            return KnowledgeContext(False, resolution.query, resolution.knowledge_topics,
                resolution.categories, (), (), "failed", ("knowledge_retrieval_failed",), resolution.reason)

    @staticmethod
    def _rank(hit: RetrievalHit) -> tuple[float, int, int]:
        authority = _AUTHORITY.get(hit.authority_level, 0)
        return hit.similarity_score + authority * 0.025 + (0.01 if hit.scope == "system" else 0), authority, hit.scope == "system"

    @staticmethod
    def _deduplicate(hits: list[RetrievalHit], limit: int) -> list[RetrievalHit]:
        output: list[RetrievalHit] = []
        seen_text: set[str] = set()
        seen_location: set[tuple[str, int | None, str | None]] = set()
        for hit in hits:
            digest = hashlib.sha256(re.sub(r"\s+", "", hit.text).lower().encode()).hexdigest()
            location = (hit.document_id, hit.page, hit.section)
            if digest in seen_text or location in seen_location:
                continue
            seen_text.add(digest)
            seen_location.add(location)
            output.append(hit)
            if len(output) >= max(1, limit):
                break
        return output
