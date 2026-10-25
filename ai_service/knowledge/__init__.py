"""Independent expert knowledge base used by the standalone AI Service.

The package intentionally has no LangGraph/LangChain imports and owns its
storage independently from both the business database and AERION runtime.
"""

from .config import KnowledgeConfig
from .manager import KnowledgeBaseManager
from .rag import KnowledgeContext, KnowledgeRAGService
from .resolver import KnowledgeNeedResolution, KnowledgeNeedResolver

__all__ = [
    "KnowledgeBaseManager",
    "KnowledgeConfig",
    "KnowledgeContext",
    "KnowledgeNeedResolution",
    "KnowledgeNeedResolver",
    "KnowledgeRAGService",
]
