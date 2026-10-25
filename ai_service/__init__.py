"""Independent, LangGraph-free AI service for WeatherMulti-Agent."""

from typing import Any

from .config import AIServiceConfig
from .executor import (
    BusinessExecutionError,
    BusinessExecutor,
    BusinessTimeoutError,
    CoordinatorMinExecutor,
)
from .service import AIChatService
from .session_store import SQLiteSessionStore, SessionConflictError, StoredSession
from .visualization import (
    CanonicalVisualizationDataProvider,
    VisualizationDataUnavailable,
    VisualizationPlanner,
    VisualizationValidationResult,
    VisualizationValidator,
    empty_visualization_context,
)


def create_app(*args: Any, **kwargs: Any):
    """Lazy application factory so ``python -m ai_service.app`` stays warning-free."""

    from .app import create_app as factory

    return factory(*args, **kwargs)

__all__ = [
    "AIChatService",
    "AIServiceConfig",
    "BusinessExecutionError",
    "BusinessExecutor",
    "BusinessTimeoutError",
    "CoordinatorMinExecutor",
    "SQLiteSessionStore",
    "SessionConflictError",
    "StoredSession",
    "CanonicalVisualizationDataProvider",
    "VisualizationDataUnavailable",
    "VisualizationPlanner",
    "VisualizationValidationResult",
    "VisualizationValidator",
    "empty_visualization_context",
    "create_app",
]
