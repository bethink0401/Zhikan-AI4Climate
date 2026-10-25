"""Faithful PDF/DOCX/TXT/Markdown parsing without model rewriting."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from .exceptions import KnowledgeValidationError, RequiresOCRError, UnsupportedDocumentType
from .models import ParsedBlock


SUPPORTED_EXTENSIONS = frozenset({".pdf", ".docx", ".txt", ".md"})
MIME_BY_EXTENSION = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def validate_file_signature(path: Path) -> None:
    """Reject extension spoofing before a third-party parser sees the file."""

    suffix = path.suffix.lower()
    header = path.read_bytes()[:8]
    if suffix == ".pdf" and not header.startswith(b"%PDF-"):
        raise KnowledgeValidationError("file content is not a PDF")
    if suffix == ".docx":
        if not header.startswith(b"PK"):
            raise KnowledgeValidationError("file content is not a DOCX package")
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise KnowledgeValidationError("file content is not a DOCX document")
        except zipfile.BadZipFile as exc:
            raise KnowledgeValidationError("file content is not a DOCX package") from exc
    if suffix in {".txt", ".md"} and b"\x00" in path.read_bytes()[:4096]:
        raise KnowledgeValidationError("text document contains binary content")


def clean_text(value: str) -> str:
    value = value.replace("\u00a0", " ").replace("\x00", "")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if not line:
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(item for item in paragraphs if item)


class DocumentParser:
    def __init__(self, *, min_pdf_text_chars: int = 30) -> None:
        self.min_pdf_text_chars = min_pdf_text_chars

    def parse(self, path: Path) -> list[ParsedBlock]:
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise UnsupportedDocumentType(f"unsupported document type: {suffix or 'unknown'}")
        validate_file_signature(path)
        if suffix == ".pdf":
            return self._parse_pdf(path)
        if suffix == ".docx":
            return self._parse_docx(path)
        return self._parse_text(path, markdown=suffix == ".md")

    def _parse_pdf(self, path: Path) -> list[ParsedBlock]:
        try:
            from pypdf import PdfReader

            blocks: list[ParsedBlock] = []
            total_chars = 0
            for page_number, page in enumerate(PdfReader(str(path)).pages, start=1):
                text = clean_text(page.extract_text() or "")
                if text:
                    total_chars += len(text)
                    blocks.append(ParsedBlock(text=text, page=page_number))
        except Exception as exc:
            raise KnowledgeValidationError("PDF parse failed") from exc
        if total_chars < self.min_pdf_text_chars:
            raise RequiresOCRError("该 PDF 可能为扫描版，本阶段未启用 OCR。")
        return blocks

    @staticmethod
    def _parse_docx(path: Path) -> list[ParsedBlock]:
        try:
            from docx import Document

            document = Document(str(path))
        except Exception as exc:
            raise KnowledgeValidationError("DOCX parse failed") from exc
        blocks: list[ParsedBlock] = []
        section: str | None = None
        for paragraph in document.paragraphs:
            text = clean_text(paragraph.text)
            if not text:
                continue
            if str(getattr(paragraph.style, "name", "") or "").lower().startswith("heading"):
                section = text
            blocks.append(ParsedBlock(text=text, section=section))
        if not blocks:
            raise KnowledgeValidationError("DOCX contains no extractable text")
        return blocks

    @staticmethod
    def _parse_text(path: Path, *, markdown: bool) -> list[ParsedBlock]:
        raw = path.read_bytes()
        text: str | None = None
        for encoding in ("utf-8-sig", "utf-16", "gb18030"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise KnowledgeValidationError("text decode failed: expected UTF-8, UTF-16, or GB18030")
        blocks: list[ParsedBlock] = []
        section: str | None = None
        for paragraph in re.split(r"\n\s*\n", text):
            cleaned = clean_text(paragraph)
            if not cleaned:
                continue
            if markdown:
                heading = re.match(r"^#{1,6}\s+(.+)$", cleaned.splitlines()[0])
                if heading:
                    section = heading.group(1).strip()
            blocks.append(ParsedBlock(text=cleaned, section=section))
        if not blocks:
            raise KnowledgeValidationError("document contains no extractable text")
        return blocks
