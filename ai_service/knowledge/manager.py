"""Document ingestion, scoped retrieval, metadata updates, and safe deletion."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .chunking import KnowledgeChunker
from .config import KnowledgeConfig
from .embeddings import EmbeddingProvider, create_embedding_provider
from .exceptions import (
    DeletionConsistencyError,
    DuplicateDocumentError,
    KnowledgeAccessDenied,
    KnowledgeValidationError,
    RequiresOCRError,
    UnsupportedDocumentType,
    EmbeddingProviderUnavailable,
    VectorStoreUnavailable,
)
from .models import (
    AUTHORITY_WHITELIST,
    CATEGORY_WHITELIST,
    SCOPE_WHITELIST,
    KnowledgeDocument,
    KnowledgePrincipal,
    RetrievalHit,
    RetrievalResult,
)
from .parsing import MIME_BY_EXTENSION, SUPPORTED_EXTENSIONS, DocumentParser, validate_file_signature
from .storage import ChromaVectorStore, DocumentRepository, utc_now


logger = logging.getLogger(__name__)


def _lexical_terms(value: str) -> set[str]:
    """Use Chinese character pairs and full Latin tokens without an extra tokenizer."""
    terms: set[str] = set()
    for part in re.findall(r"[a-z0-9.]+|[\u4e00-\u9fff]+", value.lower()):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            terms.update(part[index:index + 2] for index in range(len(part) - 1))
        elif len(part) >= 2:
            terms.add(part)
    return terms - {"什么", "怎么", "如何", "请问", "一下", "当前", "现在", "相关", "原因", "哪些"}


def _safe_name(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip()
    name = re.sub(r"[^\w.()\-\u4e00-\u9fff ]+", "_", name, flags=re.UNICODE)
    return name[:180] or "document"


def _topics(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = re.split(r"[,，;；]", value)
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = [str(item) for item in value]
    else:
        raise KnowledgeValidationError("topics must be a string or list")
    return list(dict.fromkeys(item.strip()[:80] for item in items if item.strip()))[:30]


class KnowledgeBaseManager:
    def __init__(
        self,
        config: KnowledgeConfig,
        *,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        if not config.enabled:
            raise KnowledgeValidationError("knowledge base is disabled")
        self.config = config
        config.ensure_directories()
        self.repository = DocumentRepository(config.database_path)
        self.embedding_provider = embedding_provider or create_embedding_provider(
            config.embedding_provider, config.embedding_model
        )
        self.vector_store = ChromaVectorStore(config)
        self.parser = DocumentParser()
        self.chunker = KnowledgeChunker(
            chunk_size=config.chunk_size, overlap=config.chunk_overlap
        )

    @staticmethod
    def _can_read(document: KnowledgeDocument, principal: KnowledgePrincipal) -> bool:
        if document.scope not in principal.allowed_scopes:
            return False
        return document.scope == "system" or document.owner_id == principal.principal_id

    @staticmethod
    def _can_manage(document: KnowledgeDocument, principal: KnowledgePrincipal) -> bool:
        if "knowledge_admin" in principal.roles:
            return True
        return document.scope == "user" and document.owner_id == principal.principal_id

    def ingest_bytes(
        self,
        content: bytes,
        filename: str,
        metadata: Mapping[str, Any],
        *,
        principal: KnowledgePrincipal,
    ) -> KnowledgeDocument:
        unknown = set(metadata) - {
            "title", "source", "author", "year", "category", "topics", "scope",
            "authority_level", "enabled",
        }
        if unknown:
            raise KnowledgeValidationError(f"unsupported metadata fields: {sorted(unknown)}")
        safe_name = _safe_name(filename)
        extension = Path(safe_name).suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise UnsupportedDocumentType("仅支持 PDF、DOCX、TXT 和 Markdown 文件。")
        if not content:
            raise KnowledgeValidationError("uploaded file is empty")
        if len(content) > self.config.max_file_mb * 1024 * 1024:
            raise KnowledgeValidationError(
                f"file exceeds AI_KB_MAX_FILE_MB={self.config.max_file_mb}"
            )

        scope = str(metadata.get("scope") or "user")
        if scope not in SCOPE_WHITELIST or scope not in principal.allowed_scopes:
            raise KnowledgeAccessDenied("knowledge scope is not allowed for this caller")
        if scope == "system" and "knowledge_admin" not in principal.roles:
            raise KnowledgeAccessDenied("system knowledge requires a platform maintainer")
        owner_id = "platform" if scope == "system" else principal.principal_id
        checksum = hashlib.sha256(content).hexdigest()
        duplicate = self.repository.get_by_checksum(owner_id, checksum)
        if duplicate is not None:
            raise DuplicateDocumentError(duplicate.document_id)

        title = str(metadata.get("title") or "").strip()[:300]
        if not title:
            raise KnowledgeValidationError(
                "title is required; the upload filename is not accepted as a citation title"
            )
        category = str(metadata.get("category") or "other")
        if category not in CATEGORY_WHITELIST:
            raise KnowledgeValidationError(f"category is not allowed: {category}")
        authority = str(metadata.get("authority_level") or (
            "user_uploaded" if scope == "user" else "internal"
        ))
        if scope == "user":
            authority = "user_uploaded"
        if authority not in AUTHORITY_WHITELIST:
            raise KnowledgeValidationError("invalid authority_level")
        year = metadata.get("year")
        if year not in (None, ""):
            try:
                year = int(year)
            except (TypeError, ValueError) as exc:
                raise KnowledgeValidationError("year must be an integer") from exc
            if year < 1800 or year > 2200:
                raise KnowledgeValidationError("year is outside the supported range")
        else:
            year = None

        document_id = str(uuid4())
        stored_filename = f"{document_id}{extension}"
        now = utc_now()
        document = KnowledgeDocument(
            document_id=document_id, title=title, original_filename=safe_name,
            stored_filename=stored_filename, scope=scope, owner_id=owner_id,
            source=str(metadata.get("source") or "").strip()[:300] or None,
            author=str(metadata.get("author") or "").strip()[:200] or None,
            year=year, category=category, topics=_topics(metadata.get("topics")),
            authority_level=authority, mime_type=MIME_BY_EXTENSION.get(extension)
            or mimetypes.guess_type(safe_name)[0] or "application/octet-stream",
            file_size=len(content), checksum=checksum, status="uploaded",
            enabled=bool(metadata.get("enabled", True)), chunk_count=0,
            created_at=now, updated_at=now,
            metadata_trust="platform_reviewed" if scope == "system" else "user_asserted",
        )
        stored_path = (self.config.documents_path / stored_filename).resolve()
        if stored_path.parent != self.config.documents_path.resolve():
            raise KnowledgeValidationError("unsafe document storage path")
        stored_path.write_bytes(content)
        try:
            validate_file_signature(stored_path)
            self.repository.create_document(document)
        except Exception:
            stored_path.unlink(missing_ok=True)
            raise

        try:
            self.repository.update_document(document_id, {"status": "parsing", "error_message": None})
            blocks = self.parser.parse(stored_path)
            chunks = self.chunker.chunk(document, blocks)
            if not chunks:
                raise KnowledgeValidationError("document produced no non-empty chunks")
            self.repository.update_document(document_id, {"status": "indexing"})
            vectors = self.embedding_provider.embed_documents([chunk.text for chunk in chunks])
            self.vector_store.delete_document(document_id)
            self.vector_store.add_chunks(chunks, vectors)
            self.repository.replace_chunks(document_id, chunks)
            return self.repository.update_document(document_id, {
                "status": "indexed", "chunk_count": len(chunks), "error_message": None,
            })
        except RequiresOCRError as exc:
            return self.repository.update_document(document_id, {
                "status": "requires_ocr", "chunk_count": 0, "error_message": str(exc),
            })
        except Exception as exc:
            try:
                self.vector_store.delete_document(document_id)
            except Exception:
                pass
            return self.repository.update_document(document_id, {
                "status": "failed", "chunk_count": 0,
                "error_message": str(exc)[:1000] or exc.__class__.__name__,
            })

    def get_document(self, document_id: str, *, principal: KnowledgePrincipal) -> KnowledgeDocument:
        document = self.repository.get_document(document_id)
        if not self._can_read(document, principal):
            # Do not reveal whether another owner's document exists.
            raise KnowledgeAccessDenied("knowledge document is not accessible")
        return document

    def list_documents(self, *, principal: KnowledgePrincipal, **filters: Any) -> list[KnowledgeDocument]:
        return self.repository.list_documents(
            owner_id=principal.principal_id,
            allowed_scopes=principal.allowed_scopes,
            **filters,
        )

    def update_document(
        self, document_id: str, updates: Mapping[str, Any], *, principal: KnowledgePrincipal
    ) -> KnowledgeDocument:
        current = self.repository.get_document(document_id)
        if not self._can_manage(current, principal):
            raise KnowledgeAccessDenied("knowledge document is read-only for this caller")
        allowed = {"enabled", "title", "category", "topics", "source", "author", "year"}
        unknown = set(updates) - allowed
        if unknown:
            raise KnowledgeValidationError(f"unsupported update fields: {sorted(unknown)}")
        normalized = dict(updates)
        if "title" in normalized:
            normalized["title"] = str(normalized["title"]).strip()[:300]
            if not normalized["title"]:
                raise KnowledgeValidationError("title cannot be empty")
        if "category" in normalized and normalized["category"] not in CATEGORY_WHITELIST:
            raise KnowledgeValidationError("category is not in the whitelist")
        if "topics" in normalized:
            normalized["topics"] = _topics(normalized["topics"])
        if "year" in normalized and normalized["year"] not in (None, ""):
            normalized["year"] = int(normalized["year"])
        vector_updates = {
            key: (None if value == "" else value)
            for key, value in normalized.items()
            if key in {"enabled", "title", "category", "source", "year"}
        }
        if "topics" in normalized:
            vector_updates["topics_json"] = json.dumps(normalized["topics"], ensure_ascii=False)
        if vector_updates:
            self.vector_store.update_document_metadata(document_id, vector_updates)
        if "enabled" in normalized:
            self.repository.set_chunks_enabled(document_id, bool(normalized["enabled"]))
        return self.repository.update_document(document_id, normalized)

    def delete_document(self, document_id: str, *, principal: KnowledgePrincipal) -> None:
        document = self.repository.get_document(document_id)
        if not self._can_manage(document, principal):
            raise KnowledgeAccessDenied("knowledge document is read-only for this caller")
        self.vector_store.delete_document(document_id)
        if self.vector_store.document_chunk_count(document_id) != 0:
            raise DeletionConsistencyError("vector index deletion could not be verified")
        stored_path = (self.config.documents_path / document.stored_filename).resolve()
        if stored_path.parent != self.config.documents_path.resolve():
            raise DeletionConsistencyError("unsafe stored document path")
        stored_path.unlink(missing_ok=True)
        self.repository.delete_document(document_id)
        if self.repository.count_chunks(document_id) != 0 or stored_path.exists():
            raise DeletionConsistencyError("knowledge deletion consistency check failed")

    def search(
        self, query: str, *, principal: KnowledgePrincipal, top_k: int | None = None,
        scope: Sequence[str] | None = None, categories: Sequence[str] | None = None,
        document_ids: Sequence[str] | None = None, enabled_only: bool = True,
    ) -> RetrievalResult:
        text = query.strip()
        if not text:
            raise KnowledgeValidationError("query is required")
        limit = int(top_k or self.config.top_k)
        if limit < 1 or limit > 50:
            raise KnowledgeValidationError("top_k must be between 1 and 50")
        scopes = list(scope or principal.allowed_scopes)
        invalid_scopes = set(scopes) - SCOPE_WHITELIST
        invalid_categories = set(categories or ()) - CATEGORY_WHITELIST
        if invalid_scopes or not set(scopes).issubset(principal.allowed_scopes):
            raise KnowledgeAccessDenied("requested knowledge scope is not allowed")
        if invalid_categories:
            raise KnowledgeValidationError(f"invalid category filter: {sorted(invalid_categories)}")
        try:
            vector = self.embedding_provider.embed_query(text)
            hits = self.vector_store.search(vector, top_k=limit, filters={
                "scope": scopes, "owner_id": principal.principal_id,
                "category": list(categories or ()), "document_id": list(document_ids or ()),
                "enabled_only": enabled_only,
            })
        except (EmbeddingProviderUnavailable, VectorStoreUnavailable) as exc:
            logger.warning("vector retrieval unavailable; using scoped text search: %s", type(exc).__name__)
            terms = _lexical_terms(text)
            allowed_ids = set(document_ids or ())
            ranked: list[tuple[float, RetrievalHit]] = []
            for row in self.repository.searchable_chunks(
                    owner_id=principal.principal_id, scopes=scopes,
                    categories=categories or ()):
                if allowed_ids and row["document_id"] not in allowed_ids:
                    continue
                content = f"{row['title']} {row['topics_json']} {row['text']}".lower()
                matches = {term for term in terms if term in content}
                if not matches:
                    continue
                title = str(row["title"]).lower()
                title_matches = sum(term in title for term in matches)
                coverage = len(matches) / max(1, len(terms))
                score = round(min(0.95, 0.36 + 0.45 * coverage + 0.025 * title_matches), 6)
                hit = RetrievalHit(
                    chunk_id=row["chunk_id"], document_id=row["document_id"],
                    text=row["text"], similarity_score=score, title=row["title"],
                    source=row["source"], author=row["document_author"], year=row["year"],
                    page=row["page"], page_start=row["page_start"], page_end=row["page_end"],
                    section=row["section"], category=row["category"], scope=row["scope"],
                    owner_id=row["owner_id"], authority_level=row["authority_level"],
                )
                ranked.append((score, hit))
            ranked.sort(key=lambda item: item[0], reverse=True)
            hits = [hit for _, hit in ranked[:limit]]
        enriched = []
        for hit in hits:
            try:
                document = self.get_document(hit.document_id, principal=principal)
            except KnowledgeAccessDenied:
                continue
            enriched.append(replace(hit, author=document.author))
        return RetrievalResult(
            query=text, results=tuple(enriched), total_hits=len(enriched),
            index_version=self.config.index_version,
            embedding_model=self.config.embedding_model,
        )
