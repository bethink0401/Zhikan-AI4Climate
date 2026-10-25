"""SQLite metadata, versioned manifest, and persistent Chroma vector storage."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .config import KnowledgeConfig
from .exceptions import DocumentNotFound, IndexCompatibilityError, VectorStoreUnavailable
from .models import KnowledgeChunk, KnowledgeDocument, RetrievalHit


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IndexManifest:
    """Refuse to mix embeddings from a different model or index contract."""

    def __init__(self, config: KnowledgeConfig) -> None:
        self.config = config

    def ensure(self, dimensions: int | None = None) -> dict[str, Any]:
        expected = {
            "schema_version": 1,
            "index_version": self.config.index_version,
            "embedding_provider": self.config.embedding_provider,
            "embedding_model": self.config.embedding_model,
        }
        path = self.config.manifest_path
        if path.exists():
            try:
                actual = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise IndexCompatibilityError("knowledge index manifest is unreadable") from exc
            for key, value in expected.items():
                if actual.get(key) != value:
                    raise IndexCompatibilityError(
                        f"knowledge index {key} mismatch; reindex into a new storage directory"
                    )
            existing_dimensions = actual.get("dimensions")
            if dimensions is not None and existing_dimensions not in (None, dimensions):
                raise IndexCompatibilityError(
                    "knowledge embedding dimensions changed; reindex is required"
                )
            if dimensions is not None and existing_dimensions is None:
                actual["dimensions"] = dimensions
                self._write(actual)
            return actual
        value = {**expected, "dimensions": dimensions, "created_at": utc_now()}
        self._write(value)
        return value

    def _write(self, value: Mapping[str, Any]) -> None:
        path = self.config.manifest_path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Windows packaged/sandboxed temp directories can deny MoveFileEx even
        # when both paths are writable. The manifest is tiny and protected by
        # model/version validation on every read, so a direct replace-in-place
        # is the portable choice here.
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class DocumentRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    document_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    stored_filename TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    source TEXT,
                    author TEXT,
                    year INTEGER,
                    category TEXT NOT NULL,
                    topics_json TEXT NOT NULL DEFAULT '[]',
                    authority_level TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    status TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error_message TEXT,
                    metadata_trust TEXT NOT NULL DEFAULT 'user_asserted',
                    UNIQUE(owner_id, checksum)
                );
                CREATE INDEX IF NOT EXISTS idx_kb_documents_scope_owner
                    ON knowledge_documents(scope, owner_id, status);
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    page INTEGER,
                    page_start INTEGER,
                    page_end INTEGER,
                    section TEXT,
                    topics_json TEXT NOT NULL DEFAULT '[]',
                    source TEXT,
                    title TEXT NOT NULL,
                    year INTEGER,
                    category TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    authority_level TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    text_hash TEXT NOT NULL,
                    FOREIGN KEY(document_id) REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
                    UNIQUE(document_id, text_hash)
                );
                CREATE INDEX IF NOT EXISTS idx_kb_chunks_document
                    ON knowledge_chunks(document_id);
            """)

    @staticmethod
    def _document(row: sqlite3.Row) -> KnowledgeDocument:
        value = dict(row)
        value["topics"] = json.loads(value.pop("topics_json") or "[]")
        value["enabled"] = bool(value["enabled"])
        return KnowledgeDocument(**value)

    def create_document(self, document: KnowledgeDocument) -> None:
        data = document.to_dict()
        data["topics_json"] = json.dumps(data.pop("topics"), ensure_ascii=False)
        data["enabled"] = int(data["enabled"])
        with self._connect() as connection:
            connection.execute("""INSERT INTO knowledge_documents (
                document_id,title,original_filename,stored_filename,scope,owner_id,source,author,
                year,category,topics_json,authority_level,mime_type,file_size,checksum,status,
                enabled,chunk_count,created_at,updated_at,error_message,metadata_trust
            ) VALUES (
                :document_id,:title,:original_filename,:stored_filename,:scope,:owner_id,:source,:author,
                :year,:category,:topics_json,:authority_level,:mime_type,:file_size,:checksum,:status,
                :enabled,:chunk_count,:created_at,:updated_at,:error_message,:metadata_trust
            )""", data)

    def get_by_checksum(self, owner_id: str, checksum: str) -> KnowledgeDocument | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_documents WHERE owner_id = ? AND checksum = ?",
                (owner_id, checksum),
            ).fetchone()
        return self._document(row) if row else None

    def get_document(self, document_id: str) -> KnowledgeDocument:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        if row is None:
            raise DocumentNotFound(f"knowledge document not found: {document_id}")
        return self._document(row)

    def list_documents(
        self, *, owner_id: str | None = None, allowed_scopes: Sequence[str] = ("system", "user"),
        scope: str | None = None, category: str | None = None, status: str | None = None,
    ) -> list[KnowledgeDocument]:
        clauses: list[str] = []
        values: list[Any] = []
        scopes = [value for value in allowed_scopes if value in {"system", "user"}]
        if not scopes:
            return []
        if scope:
            if scope not in scopes:
                return []
            scopes = [scope]
        scope_placeholders = ",".join("?" for _ in scopes)
        if owner_id and "user" in scopes:
            clauses.append(
                f"(scope = 'system' AND scope IN ({scope_placeholders}) OR scope = 'user' AND owner_id = ?)"
            )
            values.extend(scopes)
            values.append(owner_id)
        else:
            clauses.append(f"scope IN ({scope_placeholders})")
            values.extend(scopes)
        for column, value in (("category", category), ("status", status)):
            if value:
                clauses.append(f"{column} = ?")
                values.append(value)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM knowledge_documents WHERE {' AND '.join(clauses)} ORDER BY created_at DESC",
                values,
            ).fetchall()
        return [self._document(row) for row in rows]

    def update_document(self, document_id: str, updates: Mapping[str, Any]) -> KnowledgeDocument:
        values = dict(updates)
        if not values:
            return self.get_document(document_id)
        if "topics" in values:
            values["topics_json"] = json.dumps(values.pop("topics"), ensure_ascii=False)
        if "enabled" in values:
            values["enabled"] = int(bool(values["enabled"]))
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = :{key}" for key in values)
        values["document_id"] = document_id
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE knowledge_documents SET {assignments} WHERE document_id = :document_id", values
            )
            if cursor.rowcount != 1:
                raise DocumentNotFound(f"knowledge document not found: {document_id}")
        return self.get_document(document_id)

    def replace_chunks(self, document_id: str, chunks: Iterable[KnowledgeChunk]) -> None:
        rows = []
        for chunk in chunks:
            data = chunk.to_dict()
            data["topics_json"] = json.dumps(data.pop("topics"), ensure_ascii=False)
            data["enabled"] = int(data["enabled"])
            rows.append(data)
        with self._connect() as connection:
            connection.execute("DELETE FROM knowledge_chunks WHERE document_id = ?", (document_id,))
            connection.executemany("""INSERT INTO knowledge_chunks (
                chunk_id,document_id,owner_id,text,chunk_index,page,page_start,page_end,section,
                topics_json,source,title,year,category,scope,authority_level,enabled,text_hash
            ) VALUES (
                :chunk_id,:document_id,:owner_id,:text,:chunk_index,:page,:page_start,:page_end,:section,
                :topics_json,:source,:title,:year,:category,:scope,:authority_level,:enabled,:text_hash
            )""", rows)

    def set_chunks_enabled(self, document_id: str, enabled: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE knowledge_chunks SET enabled = ? WHERE document_id = ?",
                (int(enabled), document_id),
            )

    def delete_document(self, document_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM knowledge_documents WHERE document_id = ?", (document_id,)
            )
            if cursor.rowcount != 1:
                raise DocumentNotFound(f"knowledge document not found: {document_id}")

    def count_chunks(self, document_id: str) -> int:
        with self._connect() as connection:
            return int(connection.execute(
                "SELECT COUNT(*) FROM knowledge_chunks WHERE document_id = ?", (document_id,)
            ).fetchone()[0])

    def searchable_chunks(self, *, owner_id: str, scopes: Sequence[str],
                          categories: Sequence[str] = ()) -> list[sqlite3.Row]:
        """Read only indexed, enabled chunks within the caller's document scope."""
        allowed = set(scopes) & {"system", "user"}
        if not allowed:
            return []
        scope_clauses: list[str] = []
        values: list[Any] = []
        if "system" in allowed:
            scope_clauses.append("(d.scope = 'system')")
        if "user" in allowed:
            scope_clauses.append("(d.scope = 'user' AND d.owner_id = ?)")
            values.append(owner_id)
        query = (
            "SELECT c.*, d.author AS document_author FROM knowledge_chunks c "
            "JOIN knowledge_documents d ON d.document_id = c.document_id "
            "WHERE d.status = 'indexed' AND d.enabled = 1 AND c.enabled = 1 "
            f"AND ({' OR '.join(scope_clauses)})"
        )
        if categories:
            query += f" AND c.category IN ({','.join('?' for _ in categories)})"
            values.extend(categories)
        with self._connect() as connection:
            return connection.execute(query, values).fetchall()


def _metadata(chunk: KnowledgeChunk) -> dict[str, Any]:
    value: dict[str, Any] = {
        "document_id": chunk.document_id, "chunk_id": chunk.chunk_id,
        "scope": chunk.scope, "owner_id": chunk.owner_id, "category": chunk.category,
        "source": chunk.source or "", "authority_level": chunk.authority_level,
        "title": chunk.title, "enabled": bool(chunk.enabled),
        "topics_json": json.dumps(chunk.topics, ensure_ascii=False),
    }
    for key in ("year", "page", "page_start", "page_end"):
        item = getattr(chunk, key)
        if item is not None:
            value[key] = int(item)
    if chunk.section:
        value["section"] = chunk.section
    return value


class ChromaVectorStore:
    def __init__(self, config: KnowledgeConfig) -> None:
        self.config = config
        self._client = None
        self._collection = None
        self.manifest = IndexManifest(config)

    def _get_collection(self):
        self.manifest.ensure()
        if self._collection is not None:
            return self._collection
        try:
            import chromadb

            self._client = chromadb.PersistentClient(path=str(self.config.chroma_path))
            self._collection = self._client.get_or_create_collection(
                name=self.config.collection_name, metadata={"hnsw:space": "cosine"}
            )
            return self._collection
        except IndexCompatibilityError:
            raise
        except Exception as exc:
            raise VectorStoreUnavailable("Chroma vector index is unavailable") from exc

    def add_chunks(self, chunks: Sequence[KnowledgeChunk], vectors: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunk and embedding counts do not match")
        if vectors:
            dimensions = len(vectors[0])
            if any(len(vector) != dimensions for vector in vectors):
                raise IndexCompatibilityError("embedding dimensions are inconsistent")
            self.manifest.ensure(dimensions)
        collection = self._get_collection()
        for start in range(0, len(chunks), 100):
            batch = chunks[start:start + 100]
            values = vectors[start:start + 100]
            collection.add(
                ids=[item.chunk_id for item in batch],
                embeddings=[list(vector) for vector in values],
                documents=[item.text for item in batch],
                metadatas=[_metadata(item) for item in batch],
            )

    @staticmethod
    def _where(filters: Mapping[str, Any]) -> dict[str, Any] | None:
        clauses: list[dict[str, Any]] = []
        if filters.get("enabled_only", True):
            clauses.append({"enabled": True})
        allowed = list(filters.get("scope") or [])
        owner = filters.get("owner_id")
        if allowed == ["user"]:
            clauses.extend(({"scope": "user"}, {"owner_id": owner}))
        elif allowed == ["system"]:
            clauses.append({"scope": "system"})
        elif set(allowed) == {"system", "user"} and owner:
            clauses.append({"$or": [
                {"scope": "system"},
                {"$and": [{"scope": "user"}, {"owner_id": owner}]},
            ]})
        for key in ("category", "document_id"):
            values = list(filters.get(key) or [])
            if values:
                clauses.append({key: values[0] if len(values) == 1 else {"$in": values}})
        if not clauses:
            return None
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    def search(self, vector: Sequence[float], *, top_k: int, filters: Mapping[str, Any]) -> list[RetrievalHit]:
        manifest = self.manifest.ensure(len(vector))
        collection = self._get_collection()
        if collection.count() == 0:
            return []
        result = collection.query(
            query_embeddings=[list(vector)], n_results=min(max(1, top_k), collection.count()),
            where=self._where(filters), include=["documents", "metadatas", "distances"],
        )
        hits: list[RetrievalHit] = []
        for chunk_id, text, metadata, distance in zip(
            (result.get("ids") or [[]])[0], (result.get("documents") or [[]])[0],
            (result.get("metadatas") or [[]])[0], (result.get("distances") or [[]])[0],
        ):
            item = metadata or {}
            hits.append(RetrievalHit(
                chunk_id=str(chunk_id), document_id=str(item.get("document_id", "")),
                text=str(text or ""), similarity_score=round(1.0 - float(distance), 6),
                title=str(item.get("title", "")), source=str(item["source"]) if item.get("source") else None,
                author=None, year=int(item["year"]) if item.get("year") is not None else None,
                page=int(item["page"]) if item.get("page") is not None else None,
                page_start=int(item["page_start"]) if item.get("page_start") is not None else None,
                page_end=int(item["page_end"]) if item.get("page_end") is not None else None,
                section=str(item["section"]) if item.get("section") else None,
                category=str(item.get("category", "other")), scope=str(item.get("scope", "user")),
                owner_id=str(item.get("owner_id", "")),
                authority_level=str(item.get("authority_level", "unknown")),
            ))
        _ = manifest
        return hits

    def update_document_metadata(self, document_id: str, updates: Mapping[str, Any]) -> None:
        collection = self._get_collection()
        result = collection.get(where={"document_id": document_id}, include=["metadatas"])
        ids = result.get("ids") or []
        if not ids:
            return
        values = []
        for metadata in result.get("metadatas") or []:
            value = dict(metadata or {})
            for key, item in updates.items():
                if item is None:
                    value.pop(key, None)
                else:
                    value[key] = item
            values.append(value)
        collection.update(ids=ids, metadatas=values)

    def delete_document(self, document_id: str) -> None:
        self._get_collection().delete(where={"document_id": document_id})

    def document_chunk_count(self, document_id: str) -> int:
        result = self._get_collection().get(where={"document_id": document_id}, include=[])
        return len(result.get("ids") or [])
