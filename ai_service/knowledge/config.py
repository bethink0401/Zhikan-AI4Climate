"""Environment-backed configuration for the isolated AI knowledge store."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class KnowledgeConfig:
    enabled: bool
    storage_path: Path
    max_file_mb: int = 20
    chunk_size: int = 900
    chunk_overlap: int = 120
    embedding_provider: str = "sentence_transformers"
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    index_version: str = "aerion-compatible-v1"
    collection_name: str = "weather_expert_knowledge"
    top_k: int = 5
    retrieval_min_score: float = 0.35
    max_context_chunks: int = 4
    local_development_only: bool = True

    @classmethod
    def from_env(cls, project_root: Path) -> "KnowledgeConfig":
        storage = Path(os.getenv(
            "AI_KB_STORAGE_PATH", str(project_root / "data" / "ai_knowledge")
        )).expanduser().resolve()
        value = cls(
            enabled=_bool("AI_KB_ENABLED", True),
            storage_path=storage,
            max_file_mb=int(os.getenv("AI_KB_MAX_FILE_MB", "20")),
            chunk_size=int(os.getenv("AI_KB_CHUNK_SIZE", "900")),
            chunk_overlap=int(os.getenv("AI_KB_CHUNK_OVERLAP", "120")),
            embedding_provider=os.getenv("AI_KB_EMBEDDING_PROVIDER", "sentence_transformers").strip(),
            embedding_model=os.getenv(
                "AI_KB_EMBEDDING_MODEL",
                "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            ).strip(),
            index_version=os.getenv("AI_KB_INDEX_VERSION", "aerion-compatible-v1").strip(),
            collection_name=os.getenv("AI_KB_COLLECTION", "weather_expert_knowledge").strip(),
            top_k=int(os.getenv("AI_KB_TOP_K", "5")),
            retrieval_min_score=float(os.getenv("AI_KB_MIN_SCORE", "0.35")),
            max_context_chunks=int(os.getenv("AI_KB_MAX_CONTEXT_CHUNKS", "4")),
            local_development_only=_bool("AI_KB_LOCAL_DEV_ONLY", True),
        )
        value.validate()
        return value

    def validate(self) -> None:
        if self.max_file_mb < 1 or self.max_file_mb > 100:
            raise ValueError("AI_KB_MAX_FILE_MB must be between 1 and 100")
        if self.chunk_size < 200:
            raise ValueError("AI_KB_CHUNK_SIZE must be at least 200")
        if self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_size:
            raise ValueError("AI_KB_CHUNK_OVERLAP must be >= 0 and < chunk size")
        if not self.embedding_provider or not self.embedding_model or not self.index_version:
            raise ValueError("knowledge embedding provider/model/index version are required")

    @property
    def documents_path(self) -> Path:
        return self.storage_path / "documents"

    @property
    def metadata_path(self) -> Path:
        return self.storage_path / "metadata"

    @property
    def database_path(self) -> Path:
        return self.metadata_path / "knowledge.db"

    @property
    def chroma_path(self) -> Path:
        return self.storage_path / "chroma"

    @property
    def manifest_path(self) -> Path:
        return self.storage_path / "index_manifest.json"

    def ensure_directories(self) -> None:
        for path in (self.documents_path, self.metadata_path, self.chroma_path):
            path.mkdir(parents=True, exist_ok=True)
