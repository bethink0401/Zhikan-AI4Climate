"""LangGraph-free compatibility layer for the future AERION AI service."""

from .contracts import (
    CONTRACT_VERSION,
    AgentResultEnvelope,
    ApiError,
    CanonicalAgentResults,
    ChatRequest,
    ChatResponse,
    Context,
    DataUnavailable,
    Evidence,
    ErrorResponse,
    SessionState,
    ViewCommand,
    VisualizationSpec,
)
from .coordinator_adapter import (
    CoordinatorMinResultAdapter,
    CoordinatorResultValidationError,
)
from .visualization_catalog import VisualizationDataCatalog

__all__ = [
    "CONTRACT_VERSION",
    "AgentResultEnvelope",
    "ApiError",
    "CanonicalAgentResults",
    "ChatRequest",
    "ChatResponse",
    "Context",
    "CoordinatorMinResultAdapter",
    "CoordinatorResultValidationError",
    "DataUnavailable",
    "Evidence",
    "ErrorResponse",
    "SessionState",
    "ViewCommand",
    "VisualizationDataCatalog",
    "VisualizationSpec",
]
