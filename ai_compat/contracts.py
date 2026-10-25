"""Frozen, transport-neutral contracts for phase 1 of the AERION migration.

These types intentionally contain no Flask, database, LLM, or LangGraph imports.
They describe the boundary that a later AI Service may expose; this phase does
not register an API route or alter the existing ``/api/chat`` endpoint.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


CONTRACT_VERSION = "1.0"

DataStatus = Literal["available", "partial", "unavailable", "error"]
BusinessAction = Literal["run", "need_params", "fallback", "recommend", "knowledge"]
# ``update`` is the explicit 3A name for a valid follow-up visualization
# mutation.  The phase-1 names remain valid for older clients.
VisualizationAction = Literal["none", "show", "modify", "update", "reset", "switch_view"]
UserGoal = Literal[
    "analysis",
    "explanation",
    "visualization",
    "governance_advice",
    "public_travel_advice",
    "health_protection",
    "report",
    "capability_intro",
    "unknown",
]
Audience = Literal["citizen", "manager", "unknown"]


class ContractMixin:
    """Convert a frozen contract object into a JSON-serializable mapping."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DataUnavailable(ContractMixin):
    domain: str
    reason: str
    required_adapter: str | None = None
    available_source: str | None = None
    status: Literal["unavailable"] = "unavailable"


@dataclass(frozen=True, slots=True)
class Evidence(ContractMixin):
    """One traceable fact copied from an Agent envelope or result path."""

    evidence_id: str
    domain: str
    statement: str
    source_path: str
    evidence_type: Literal["agent_evidence", "result_field", "data_quality", "limitation"]
    value: Any = None
    unit: str | None = None
    station_id: str | None = None
    station_name: str | None = None
    start_time: str | None = None
    end_time: str | None = None


@dataclass(frozen=True, slots=True)
class AgentResultEnvelope(ContractMixin):
    domain: str
    source_agent: str
    status: DataStatus
    source_status: str | None
    result: dict[str, Any]
    evidence: tuple[Evidence, ...] = ()
    confidence: float | None = None
    data_quality: dict[str, Any] = field(default_factory=dict)
    units: dict[str, str] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)
    limitations: tuple[str, ...] = ()
    unavailable: DataUnavailable | None = None


@dataclass(frozen=True, slots=True)
class CanonicalAgentResults(ContractMixin):
    """Canonical wrapper; ``agent_results`` is directly consumable by AERION."""

    task: dict[str, Any]
    agent_results: dict[str, dict[str, Any]]
    pipeline_status: dict[str, bool]
    warnings: tuple[str, ...] = ()
    contract_version: str = CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class Context(ContractMixin):
    task_id: str | None = None
    station_id: str | None = None
    station_name: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    lat: float | None = None
    lon: float | None = None
    prefer_text_station: bool | None = None
    agent_results: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChatRequest(ContractMixin):
    session_id: str
    user_input: str
    request_context: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class VisualizationSpec(ContractMixin):
    """Semantic visualization request compatible with AERION spec version 1.0."""

    view_type: Literal["chart", "map"]
    title: str
    data_source: Literal["air_quality", "meteorology", "traffic", "source", "spatial"]
    metrics: tuple[str, ...]
    # ``pollutant`` is a compatible extension for the target system's
    # window-aggregate pollutant profile; it is not an hourly series.
    dimension: Literal["time", "time_slot", "source_type", "vehicle_type", "pollutant", "space"]
    filters: dict[str, Any] = field(default_factory=dict)
    grouping: dict[str, Any] | None = None
    unit: str | None = None
    warnings: tuple[str, ...] = ()
    chart_type: str | None = None
    map_type: str | None = None
    layers: tuple[dict[str, Any], ...] = ()
    version: str = CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class ViewCommand(ContractMixin):
    """UI command, deliberately separate from the business action."""

    action: VisualizationAction
    view_id: str | None = None
    spec: VisualizationSpec | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SessionState(ContractMixin):
    """Serializable state only; persistence and orchestration are phase-2 concerns."""

    context: dict[str, Any] = field(default_factory=dict)
    history: tuple[dict[str, Any], ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    visualization_context: dict[str, Any] = field(default_factory=dict)
    user_goal: UserGoal = "unknown"
    audience: Audience = "unknown"
    recommendation_result: dict[str, Any] | None = None
    recommendation_display: dict[str, Any] | None = None
    recommendation_execution: dict[str, Any] | None = None
    execution_steps: tuple[dict[str, Any], ...] = ()
    knowledge_context: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ApiError(ContractMixin):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ErrorResponse(ContractMixin):
    error: ApiError
    request_id: str | None = None
    status: Literal["error"] = "error"
    contract_version: str = CONTRACT_VERSION


@dataclass(frozen=True, slots=True)
class ChatResponse(ContractMixin):
    answer: str
    business_action: BusinessAction
    visualization_action: VisualizationAction
    route: str | None
    context: dict[str, Any]
    agent_results: dict[str, dict[str, Any]]
    missing_fields: tuple[str, ...] = ()
    reason: str = ""
    visualization: dict[str, Any] | None = None
    content_blocks: tuple[dict[str, Any], ...] = ()
    user_goal: UserGoal = "unknown"
    audience: Audience = "unknown"
    goal_reason: str = "not_resolved"
    recommendation_result: dict[str, Any] | None = None
    recommendation_display: dict[str, Any] | None = None
    recommendation_execution: dict[str, Any] | None = None
    execution_steps: tuple[dict[str, Any], ...] = ()
    knowledge: dict[str, Any] | None = None
    fused_evidence: dict[str, Any] | None = None
    error: ApiError | None = None
    contract_version: str = CONTRACT_VERSION
