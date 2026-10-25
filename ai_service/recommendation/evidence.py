"""Evidence completeness resolver for recommendations.

Port of AERION's RecommendationEvidenceResolver with no LangGraph dependency.
This module checks which evidence is available from existing Agent results
and determines if supplementary execution is needed.

This resolver is intentionally data-only: it never calls a business Agent. It
describes which existing evidence is usable and which routes may be run by the
chat service to supplement missing required evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping


EvidenceKey = Literal["air_quality", "meteorology", "traffic", "spatial", "prediction"]
EvidenceStatus = Literal["sufficient", "partial", "needs_enrichment", "unavailable"]


# Evidence requirements for each recommendation type
_REQUIREMENTS: dict[str, tuple[tuple[EvidenceKey, ...], tuple[EvidenceKey, ...]]] = {
    "public_travel_advice": (
        # Required evidence
        ("air_quality", "meteorology", "traffic"),
        # Optional evidence
        ("spatial", "prediction"),
    ),
    "health_protection": (
        # Required evidence
        ("air_quality",),
        # Optional evidence
        ("meteorology", "prediction"),
    ),
    "governance_advice": (
        # Required evidence (governance also benefits from full context)
        ("air_quality", "meteorology"),
        # Optional evidence
        ("traffic", "spatial", "prediction"),
    ),
}

# Map evidence key to Agent route name
_ROUTE_BY_EVIDENCE: dict[EvidenceKey, str] = {
    "air_quality": "air_quality",
    "meteorology": "meteorology",
    "traffic": "traffic",
    "spatial": "spatial",
    "prediction": "meteorology",  # Prediction comes from MeteorologyAgent
}


def _result(agent_results: Mapping[str, Any], agent_name: str) -> Mapping[str, Any]:
    """Extract Agent result from envelope."""
    envelope = agent_results.get(agent_name)
    if not isinstance(envelope, Mapping):
        return {}
    value = envelope.get("result")
    return value if isinstance(value, Mapping) else envelope


def _is_number(value: Any) -> bool:
    """Check if value is a usable number."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _has_numeric_row(rows: Any, fields: tuple[str, ...]) -> bool:
    """Check if any row has numeric values in specified fields."""
    if not isinstance(rows, list):
        return False
    return any(
        isinstance(row, Mapping) and any(_is_number(row.get(field)) for field in fields)
        for row in rows
    )


def _has_air_quality(agent_results: Mapping[str, Any]) -> bool:
    """Check if air quality evidence is available."""
    result = _result(agent_results, "AirQualityAgent")
    
    # Check hourly series
    hourly_fields = ("aqhi", "co", "no2", "nox", "o3", "pm10", "pm25", "so2")
    if _has_numeric_row(result.get("hourly_series"), hourly_fields):
        return True
    
    # Check pollutant details
    details = result.get("pollutant_details")
    if isinstance(details, Mapping):
        for detail in details.values():
            if isinstance(detail, Mapping) and any(
                _is_number(detail.get(field)) for field in ("avg", "min", "max")
            ):
                return True
    
    # Check summary metrics
    return (
        _is_number(result.get("avg_aqhi"))
        or result.get("aqhi_level") not in (None, "", "unknown")
        or result.get("pollution_status") not in (None, "", "unknown")
    )


def _has_meteorology(agent_results: Mapping[str, Any]) -> bool:
    """Check if meteorology evidence is available."""
    result = _result(agent_results, "MeteorologyAgent")
    return _is_number(result.get("mean_wind_speed")) or _has_numeric_row(
        result.get("hourly_series"),
        ("wind_speed", "wind_direction"),
    )


def _has_traffic(agent_results: Mapping[str, Any]) -> bool:
    """Check if traffic evidence is available."""
    result = _result(agent_results, "TrafficAgent")
    return _is_number(result.get("traffic_pressure_score")) or _has_numeric_row(
        result.get("hourly_pressure_series") or result.get("hourly_series"),
        ("traffic_pressure_score",),
    )


def _has_spatial(agent_results: Mapping[str, Any]) -> bool:
    """Check if spatial evidence is available."""
    result = _result(agent_results, "SpatialAgent")
    sensitive_pois = result.get("sensitive_pois")
    return bool(
        result.get("affected_area")
        or result.get("affected_area_geojson")
        or result.get("upwind_roads")
        or (isinstance(sensitive_pois, list) and sensitive_pois)
        or (_is_number(result.get("sensitive_poi_count")) and result["sensitive_poi_count"] > 0)
    )


def _has_prediction(agent_results: Mapping[str, Any]) -> bool:
    """Check if prediction evidence is available."""
    result = _result(agent_results, "MeteorologyAgent")
    prediction = result.get("pollution_path_prediction")
    if not isinstance(prediction, Mapping):
        return False
    return bool(
        prediction.get("status") == "ok"
        and (prediction.get("path_geojson") or prediction.get("steps"))
    )


# Evidence check functions
_CHECKS = {
    "air_quality": _has_air_quality,
    "meteorology": _has_meteorology,
    "traffic": _has_traffic,
    "spatial": _has_spatial,
    "prediction": _has_prediction,
}


@dataclass(frozen=True, slots=True)
class EvidenceResolution:
    """Result of evidence completeness assessment."""

    recommendation_type: str
    required_evidence: tuple[EvidenceKey, ...]
    optional_evidence: tuple[EvidenceKey, ...]
    available_evidence: tuple[EvidenceKey, ...]
    missing_required: tuple[EvidenceKey, ...]
    missing_optional: tuple[EvidenceKey, ...]
    agents_to_run: tuple[str, ...]
    evidence_status: EvidenceStatus
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "recommendation_type": self.recommendation_type,
            "required_evidence": list(self.required_evidence),
            "optional_evidence": list(self.optional_evidence),
            "available_evidence": list(self.available_evidence),
            "missing_required": list(self.missing_required),
            "missing_optional": list(self.missing_optional),
            "agents_to_run": list(self.agents_to_run),
            "evidence_status": self.evidence_status,
            "reason": self.reason,
        }


class RecommendationEvidenceResolver:
    """Check usable core evidence and return a minimal supplementation plan.
    
    This resolver never executes business Agents. It only describes which
    evidence is present and which Agents could be run to supplement gaps.
    The actual execution decision is made by the caller (typically AIChatService).
    """

    def resolve(
        self,
        recommendation_type: str,
        agent_results: Mapping[str, Any] | None,
        *,
        allow_enrichment: bool = True,
    ) -> EvidenceResolution:
        """Assess evidence completeness for a recommendation type.
        
        Args:
            recommendation_type: Type of recommendation to assess
            agent_results: Existing Agent results to check
            allow_enrichment: Whether to suggest supplementary Agents
        
        Returns:
            EvidenceResolution with completeness assessment
        """
        required, optional = _REQUIREMENTS.get(recommendation_type, ((), ()))
        results = agent_results if isinstance(agent_results, Mapping) else {}
        
        # Check which evidence is available
        considered = required + optional
        available = tuple(key for key in considered if _CHECKS[key](results))
        missing_required = tuple(key for key in required if key not in available)
        missing_optional = tuple(key for key in optional if key not in available)
        
        # Determine which Agents to run for missing required evidence
        agents_to_run: list[str] = []
        if allow_enrichment:
            for key in missing_required:
                route = _ROUTE_BY_EVIDENCE[key]
                if route not in agents_to_run:
                    agents_to_run.append(route)
        
        # Assess overall status
        if not missing_required:
            status: EvidenceStatus = "sufficient" if not missing_optional else "partial"
            reason = "all_required_evidence_available"
        elif agents_to_run:
            status = "needs_enrichment"
            reason = "required_evidence_missing_but_supplementable"
        elif available:
            status = "partial"
            reason = "required_evidence_missing_and_enrichment_disabled"
        else:
            status = "unavailable"
            reason = "no_usable_required_evidence"
        
        return EvidenceResolution(
            recommendation_type=recommendation_type,
            required_evidence=required,
            optional_evidence=optional,
            available_evidence=available,
            missing_required=missing_required,
            missing_optional=missing_optional,
            agents_to_run=tuple(agents_to_run),
            evidence_status=status,
            reason=reason,
        )


__all__ = [
    "EvidenceKey",
    "EvidenceResolution",
    "EvidenceStatus",
    "RecommendationEvidenceResolver",
]
