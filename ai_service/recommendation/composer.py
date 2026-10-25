"""Recommendation composer for citizen-facing advice.

Port of AERION's RecommendationComposer with no LangGraph dependency.
This module composes recommendations from already-computed Agent data,
never querying data or running business Agents.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, TYPE_CHECKING

from .evidence import EvidenceResolution
from .prediction import FutureRiskAssessment
from .types import ConfidenceLabel, UserGoal

if TYPE_CHECKING:
    from ai_service.resolvers import GoalResolution


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    """Complete recommendation with evidence and limitations."""

    recommendation_type: UserGoal
    summary: str
    recommendation: str
    evidence: tuple[dict[str, Any], ...]
    evidence_summary: dict[str, Any]
    limitations: tuple[str, ...]
    confidence: ConfidenceLabel
    future_risk_assessment: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "recommendation_type": self.recommendation_type,
            "summary": self.summary,
            "recommendation": self.recommendation,
            "evidence": [dict(item) for item in self.evidence],
            "evidence_summary": dict(self.evidence_summary),
            "limitations": list(self.limitations),
            "confidence": self.confidence,
        }
        if self.future_risk_assessment is not None:
            payload["future_risk_assessment"] = dict(self.future_risk_assessment)
        return payload


def _result(agent_results: Mapping[str, Any], agent_name: str) -> Mapping[str, Any]:
    """Extract Agent result from envelope."""
    envelope = agent_results.get(agent_name)
    if not isinstance(envelope, Mapping):
        return {}
    value = envelope.get("result")
    return value if isinstance(value, Mapping) else envelope


def _number(value: Any) -> float | None:
    """Check if value is a usable number."""
    return float(value) if isinstance(value, (int, float)) else None


class RecommendationComposer:
    """Compose recommendations using existing evidence only.
    
    This class never queries data or runs business Agents. It only composes
    recommendations from Agent results already available in the session.
    """

    def compose(
        self,
        *,
        agent_results: Mapping[str, Any],
        context: Mapping[str, Any],
        resolution: "GoalResolution",
        previous_recommendation: Mapping[str, Any] | None = None,
        evidence_resolution: EvidenceResolution | None = None,
        future_risk: FutureRiskAssessment | Mapping[str, Any] | None = None,
    ) -> RecommendationResult:
        """Compose recommendation from existing Agent results.
        
        Args:
            agent_results: Dictionary of Agent results
            context: Session context (station, date, etc.)
            resolution: User goal resolution
            previous_recommendation: Previous recommendation for explain queries
            evidence_resolution: Evidence completeness assessment
            future_risk: Future risk assessment
        
        Returns:
            RecommendationResult with summary, evidence, and limitations
        """
        # Import at runtime to avoid circular dependency
        if not TYPE_CHECKING:
            from ai_service.resolvers import GoalResolution  # noqa: F401
        
        # Handle explain queries by returning previous recommendation
        if resolution.reason == "explain_previous_recommendation" and previous_recommendation:
            return RecommendationResult(
                recommendation_type=resolution.user_goal,
                summary=str(previous_recommendation.get("summary") or "上一轮建议基于已有环境风险信号。"),
                recommendation=str(previous_recommendation.get("recommendation") or "conditional_environmental_risk_reference"),
                evidence=tuple(
                    dict(item)
                    for item in previous_recommendation.get("evidence", [])
                    if isinstance(item, Mapping)
                ),
                evidence_summary=dict(previous_recommendation.get("evidence_summary") or {}),
                limitations=tuple(str(item) for item in previous_recommendation.get("limitations", [])),
                confidence=str(previous_recommendation.get("confidence") or "low"),  # type: ignore[arg-type]
                future_risk_assessment=(
                    dict(value)
                    if isinstance(
                        value := previous_recommendation.get("future_risk_assessment"),
                        Mapping,
                    )
                    else None
                ),
            )

        # Extract Agent results
        air = _result(agent_results, "AirQualityAgent")
        weather = _result(agent_results, "MeteorologyAgent")
        traffic = _result(agent_results, "TrafficAgent")
        spatial = _result(agent_results, "SpatialAgent")
        evidence: list[dict[str, Any]] = []
        limitations: list[str] = ["route_specific_exposure_unavailable"]
        future_risk_payload = (
            future_risk.to_dict()
            if isinstance(future_risk, FutureRiskAssessment)
            else dict(future_risk)
            if isinstance(future_risk, Mapping)
            else None
        )

        # Collect air quality evidence
        pollution_status = air.get("pollution_status")
        if pollution_status:
            evidence.append({"type": "pollution_status", "value": pollution_status})
        anomaly_score = _number(air.get("anomaly_score"))
        if anomaly_score is not None:
            evidence.append({"type": "air_quality_anomaly_score", "value": anomaly_score})
        aqhi_level = air.get("aqhi_level")
        avg_aqhi = _number(air.get("avg_aqhi"))
        if aqhi_level not in (None, "unknown") or avg_aqhi is not None:
            evidence.append({
                "type": "aqhi",
                "value": avg_aqhi,
                "level": aqhi_level,
                "unit": "AQHI index",
            })
        else:
            limitations.append("aqhi_unavailable")

        # Collect traffic evidence
        pressure_score = _number(traffic.get("traffic_pressure_score"))
        pressure_level = traffic.get("traffic_pressure_level") or traffic.get("traffic_pressure")
        if pressure_score is not None or pressure_level:
            evidence.append({
                "type": "traffic_pressure",
                "value": pressure_score,
                "level": pressure_level,
                "unit": "0-1 relative risk score",
            })
        else:
            limitations.append("traffic_pressure_unavailable")

        # Collect meteorology evidence
        wind_speed = _number(weather.get("mean_wind_speed"))
        if wind_speed is not None:
            evidence.append({"type": "wind_speed", "value": wind_speed, "unit": "m/s"})
        else:
            limitations.append("wind_speed_unavailable")

        # Collect spatial evidence
        affected_area = spatial.get("affected_area")
        if affected_area:
            evidence.append({"type": "affected_area", "value": affected_area})
        
        # Collect future risk evidence
        future_risk_level = None
        if future_risk_payload is not None:
            future_risk_level = str(future_risk_payload.get("risk_level") or "limited")
            evidence.append({
                "type": "future_environmental_risk",
                "value": future_risk_payload.get("risk_score"),
                "level": future_risk_level,
                "prediction_window": future_risk_payload.get("prediction_window"),
                "affected_area": future_risk_payload.get("affected_area"),
            })
            future_limitations = future_risk_payload.get("limitations")
            if isinstance(future_limitations, (list, tuple)):
                limitations.extend(str(item) for item in future_limitations)

        # Add missing evidence limitations
        if not air:
            limitations.append("air_quality_unavailable")
        if evidence_resolution is not None:
            required_limitations = {
                "air_quality": "air_quality_unavailable",
                "meteorology": "wind_speed_unavailable",
                "traffic": "traffic_pressure_unavailable",
            }
            limitations.extend(
                required_limitations[key]
                for key in evidence_resolution.missing_required
                if key in required_limitations
            )

        # Determine risk signals
        high_pollution = (
            str(pollution_status).lower() in {"moderate", "abnormal", "high", "very_high"}
            or (anomaly_score is not None and anomaly_score >= 0.7)
            or (avg_aqhi is not None and avg_aqhi >= 7)
        )
        high_traffic = (
            str(pressure_level).lower() in {"high", "very_high"}
            or (pressure_score is not None and pressure_score >= 0.7)
        )
        low_wind = wind_speed is not None and wind_speed < 1.5

        # Generate recommendation based on user goal and risk signals
        if resolution.user_goal == "health_protection":
            summary = "当前建议以减少户外污染暴露为目标。"
            recommendation = "reduce_outdoor_exposure_when_environmental_risk_is_elevated"
        elif resolution.user_goal == "governance_advice":
            # User-facing governance copy comes from the evidence-grounded model answer.
            summary = ""
            recommendation = "governance_measures_based_on_environmental_signals"
        elif high_pollution or high_traffic or low_wind or future_risk_level in {"medium", "high"}:
            summary = (
                "当前存在较高污染、交通压力或不利扩散信号。若需要沿主干道长时间步行，"
                "建议缩短近道路暴露时间；公共交通方便时可优先考虑公共交通。"
            )
            recommendation = "prefer_public_transport_or_reduce_long_roadside_walking"
        else:
            summary = (
                "现有数据不足以断言开车或步行哪一种一定更安全。可结合出行距离，"
                "优先避开繁忙道路和污染较高时段。"
            )
            recommendation = "choose_conditionally_and_avoid_busy_road_exposure"

        # Determine confidence based on evidence completeness
        usable_evidence = len(evidence)
        if evidence_resolution is not None:
            if evidence_resolution.missing_required:
                confidence: ConfidenceLabel = "low"
            elif evidence_resolution.evidence_status == "sufficient":
                confidence = "high"
            else:
                confidence = "medium"
        else:
            confidence = "medium" if usable_evidence >= 2 else "low"
        
        if usable_evidence == 0 and resolution.user_goal != "governance_advice":
            summary = "当前缺少足够的空气质量、交通和气象结果，暂不能比较开车与步行。"
            recommendation = "insufficient_data_for_travel_mode_comparison"

        return RecommendationResult(
            recommendation_type=resolution.user_goal,
            summary=summary,
            recommendation=recommendation,
            evidence=tuple(evidence),
            evidence_summary=(
                {
                    "status": evidence_resolution.evidence_status,
                    "available": list(evidence_resolution.available_evidence),
                    "missing_required": list(evidence_resolution.missing_required),
                    "missing_optional": list(evidence_resolution.missing_optional),
                    "reason": evidence_resolution.reason,
                }
                if evidence_resolution is not None
                else {
                    "status": "unassessed",
                    "available": [],
                    "missing_required": [],
                    "missing_optional": [],
                    "reason": "evidence_resolver_not_provided",
                }
            ),
            limitations=tuple(dict.fromkeys(limitations)),
            confidence=confidence,
            future_risk_assessment=future_risk_payload,
        )

__all__ = ["RecommendationComposer", "RecommendationResult"]
