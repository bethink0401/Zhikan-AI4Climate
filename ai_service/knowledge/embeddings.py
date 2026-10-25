"""Replaceable embedding interface; tests use deterministic mock providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from .exceptions import EmbeddingProviderUnavailable


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError


class SentenceTransformersEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            return self._model
        except Exception as exc:
            raise EmbeddingProviderUnavailable(
                f"embedding provider unavailable: {self.model_name}"
            ) from exc

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            vectors = self._load().encode(
                list(texts), normalize_embeddings=True, show_progress_bar=False
            )
            return [[float(value) for value in vector] for vector in vectors]
        except EmbeddingProviderUnavailable:
            raise
        except Exception as exc:
            raise EmbeddingProviderUnavailable("embedding provider unavailable") from exc

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def create_embedding_provider(provider: str, model_name: str) -> EmbeddingProvider:
    if provider == "sentence_transformers":
        return SentenceTransformersEmbeddingProvider(model_name)
    raise EmbeddingProviderUnavailable(f"unsupported embedding provider: {provider}")
