"""Section-aware bounded chunking with per-document text deduplication."""

from __future__ import annotations

import hashlib
import re
from uuid import uuid4

from .models import KnowledgeChunk, KnowledgeDocument, ParsedBlock


class KnowledgeChunker:
    def __init__(self, *, chunk_size: int, overlap: int) -> None:
        if chunk_size < 200 or overlap < 0 or overlap >= chunk_size:
            raise ValueError("invalid chunk_size/chunk_overlap")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def _split_long_text(self, text: str) -> list[str]:
        if len(text) <= self.chunk_size:
            return [text]
        sentences = [
            item.strip() for item in re.split(r"(?<=[。！？.!?])\s*", text) if item.strip()
        ]
        if len(sentences) <= 1:
            step = self.chunk_size - self.overlap
            return [text[start:start + self.chunk_size] for start in range(0, len(text), step)]
        parts: list[str] = []
        current = ""
        for sentence in sentences:
            candidate = current + sentence
            if current and len(candidate) > self.chunk_size:
                parts.append(current)
                current = (current[-self.overlap:] if self.overlap else "") + sentence
            else:
                current = candidate
        if current:
            parts.append(current)
        return parts

    def chunk(self, document: KnowledgeDocument, blocks: list[ParsedBlock]) -> list[KnowledgeChunk]:
        logical: list[tuple[str, int | None, int | None, str | None]] = []
        text = ""
        start: int | None = None
        end: int | None = None
        section: str | None = None

        def flush() -> None:
            nonlocal text, start, end, section
            if text.strip():
                logical.append((text.strip(), start, end, section))
            text, start, end, section = "", None, None, None

        for block in blocks:
            for part in self._split_long_text(block.text):
                changed = bool(text and block.section != section)
                candidate = f"{text}\n\n{part}" if text else part
                if changed or (text and len(candidate) > self.chunk_size):
                    previous = text[-self.overlap:] if self.overlap and not changed else ""
                    flush()
                    text = f"{previous}\n\n{part}".strip() if previous else part
                else:
                    text = candidate
                section = block.section
                if block.page is not None:
                    start = block.page if start is None else min(start, block.page)
                    end = block.page if end is None else max(end, block.page)
        flush()

        chunks: list[KnowledgeChunk] = []
        seen: set[str] = set()
        for value, page_start, page_end, heading in logical:
            normalized = re.sub(r"\s+", " ", value).strip()
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            if not normalized or digest in seen:
                continue
            seen.add(digest)
            chunks.append(KnowledgeChunk(
                chunk_id=str(uuid4()), document_id=document.document_id,
                owner_id=document.owner_id, text=value, chunk_index=len(chunks),
                page=page_start if page_start == page_end else None,
                page_start=page_start, page_end=page_end, section=heading,
                topics=list(document.topics), source=document.source, title=document.title,
                year=document.year, category=document.category, scope=document.scope,
                authority_level=document.authority_level, enabled=document.enabled,
                text_hash=digest,
            ))
        return chunks
