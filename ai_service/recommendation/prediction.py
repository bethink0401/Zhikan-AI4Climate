"""Future risk assessment from existing predictions.

Port of AERION's PredictionFusion with no LangGraph dependency.
This module fuses existing wind, path, spatial, and traffic predictions
into a screening-level future environmental risk view.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping


FutureRiskLevel = Literal["low", "medium", "high", "limited"]


@dataclass(frozen=True, slots=True)
class FutureRiskAssessment:
    """Future environmental risk assessment result."""

    risk_level: FutureRiskLevel
    risk_score: float
    prediction_window: str
    affected_area: str
    risk_factors: tuple[str, ...]
    evidence: tuple[dict[str, Any], ...]
    recommended_actions: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "risk_score": self.risk_score,
            "prediction_window": self.prediction_window,
            "affected_area": self.affected_area,
            "risk_factors": list(self.risk_factors),
            "evidence": [dict(item) for item in self.evidence],
            "recommended_actions": list(self.recommended_actions),
            "limitations": list(self.limitations),
        }


def _result(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Extract result from Agent envelope."""
    if not isinstance(value, Mapping):
        return {}
    result = value.get("result")
    return result if isinstance(result, Mapping) else value


def _number(value: Any) -> float | None:
    """Check if value is a usable number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _path_exists(prediction: Mapping[str, Any]) -> bool:
    """Check if prediction path is available."""
    if prediction.get("status") != "ok":
        return False
    path = prediction.get("path_geojson")
    coordinates = path.get("coordinates") if isinstance(path, Mapping) else None
    steps = prediction.get("steps")
    return (
        isinstance(coordinates, list) and len(coordinates) >= 2
    ) or (isinstance(steps, list) and bool(steps))


def _forecast_hours(weather: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Extract forecast hours from weather result."""
    forecast = weather.get("wind_direction_forecast")
    if not isinstance(forecast, Mapping) or forecast.get("status") != "ok":
        return []
    hours = forecast.get("forecast_hours")
    if not isinstance(hours, list):
        return []
    return [item for item in hours if isinstance(item, Mapping)]


def _stable_direction(weather: Mapping[str, Any]) -> tuple[bool, float | None, float | None]:
    """Check if wind direction is stable."""
    forecast = weather.get("wind_direction_forecast")
    if not isinstance(forecast, Mapping) or forecast.get("status") != "ok":
        return False, None, None
    hours = _forecast_hours(weather)
    uncertainties = [
        value
        for item in hours
        if (value := _number(item.get("direction_uncertainty_deg"))) is not None
    ]
    max_uncertainty = max(uncertainties) if uncertainties else None
    resultant = _number(forecast.get("resultant_length"))
    # Either a concentrated circular forecast or a consistently narrow hourly
    # uncertainty range is sufficient evidence of a stable direction.
    stable = (
        resultant is not None and resultant >= 0.7
    ) or (
        max_uncertainty is not None and max_uncertainty <= 45.0
    )
    return stable, resultant, max_uncertainty


def _has_sensitive_targets(spatial: Mapping[str, Any]) -> bool:
    """Check if sensitive targets are present in spatial result."""
    by_type = spatial.get("sensitive_poi_by_type")
    if isinstance(by_type, Mapping):
        sensitive_keys = {
            "school", "schools", "healthcare", "hospital", "hospitals",
            "kindergarten", "clinic",
        }
        if any(
            str(key).lower() in sensitive_keys and (_number(value) or 0) > 0
            for key, value in by_type.items()
        ):
            return True
    pois = spatial.get("sensitive_pois")
    if isinstance(pois, list):
        for poi in pois:
            if not isinstance(poi, Mapping):
                continue
            category = str(
                poi.get("type") or poi.get("category") or poi.get("poi_type") or ""
            ).lower()
            if any(token in category for token in ("school", "hospital", "health", "clinic")):
                return True
    # SpatialAgent's list is already filtered to sensitive POIs.  Older output
    # versions may provide only the aggregate count without per-type details.
    count = _number(spatial.get("sensitive_poi_count"))
    return count is not None and count > 0


def _prediction_window(
    weather: Mapping[str, Any],
    prediction: Mapping[str, Any],
) -> str:
    """Determine prediction window from forecast hours."""
    leads = [
        int(value)
        for item in _forecast_hours(weather)
        if (value := _number(item.get("lead_hour"))) is not None and value > 0
    ]
    if not leads:
        steps = prediction.get("steps")
        if isinstance(steps, list):
            leads = [
                int(value)
                for item in steps
                if isinstance(item, Mapping)
                and (value := _number(item.get("lead_hour"))) is not None
                and value > 0
            ]
    return f"未来 1–{max(leads)} 小时" if leads else "未来时段（预测信息有限）"


class PredictionFusion:
    """Fuse existing Agent results into future risk signals.
    
    This class translates existing wind, path, spatial, traffic and air quality
    evidence into a screening-level future environmental risk view without
    running prediction models.
    
    Risk scoring logic:
    - Base: 10 points
    - Low wind + stable direction: +25 points
    - Path available + low uncertainty: +15 points
    - Sensitive targets present: +20 points
    - High traffic pressure: +20 points
    - Current pollution elevated: +10 points
    
    Risk levels:
    - high: >=65 points (with path available)
    - medium: >=35 points (with path available)
    - low: <35 points (with path available)
    - limited: path not available
    """

    LOW_WIND_THRESHOLD_MS = 1.5
    HIGH_TRAFFIC_THRESHOLD = 0.7
    LOW_DIRECTION_UNCERTAINTY_DEG = 45.0

    def assess_from_agent_results(
        self,
        agent_results: Mapping[str, Any],
        *,
        pollution_path_prediction: Mapping[str, Any] | None = None,
    ) -> FutureRiskAssessment:
        """Assess future risk from Agent results envelope.
        
        Args:
            agent_results: Dictionary of Agent results (AgentName -> envelope)
            pollution_path_prediction: Optional explicit prediction override
        
        Returns:
            FutureRiskAssessment with risk level and supporting evidence
        """
        air = _result(agent_results.get("AirQualityAgent"))
        weather = _result(agent_results.get("MeteorologyAgent"))
        traffic = _result(agent_results.get("TrafficAgent"))
        spatial = _result(agent_results.get("SpatialAgent"))
        prediction = pollution_path_prediction
        if not isinstance(prediction, Mapping):
            spatial_prediction = spatial.get("pollution_path_prediction")
            weather_prediction = weather.get("pollution_path_prediction")
            prediction = (
                spatial_prediction
                if isinstance(spatial_prediction, Mapping)
                else weather_prediction
                if isinstance(weather_prediction, Mapping)
                else {}
            )
        return self.assess(
            air_quality_result=air,
            meteorology_result=weather,
            traffic_result=traffic,
            spatial_result=spatial,
            pollution_path_prediction=prediction,
        )

    def assess(
        self,
        *,
        air_quality_result: Mapping[str, Any] | None,
        meteorology_result: Mapping[str, Any] | None,
        traffic_result: Mapping[str, Any] | None,
        spatial_result: Mapping[str, Any] | None,
        pollution_path_prediction: Mapping[str, Any] | None,
    ) -> FutureRiskAssessment:
        """Assess future risk from individual Agent results.
        
        Args:
            air_quality_result: AirQualityAgent result
            meteorology_result: MeteorologyAgent result
            traffic_result: TrafficAgent result
            spatial_result: SpatialAgent result
            pollution_path_prediction: Pollution path prediction
        
        Returns:
            FutureRiskAssessment with risk level and evidence
        """
        air = _result(air_quality_result)
        weather = _result(meteorology_result)
        traffic = _result(traffic_result)
        spatial = _result(spatial_result)
        prediction = (
            pollution_path_prediction
            if isinstance(pollution_path_prediction, Mapping)
            else {}
        )

        score = 10.0
        factors: list[str] = []
        evidence: list[dict[str, Any]] = []
        actions: list[str] = []
        limitations: list[str] = [
            "这是未经业务校准的启发式规则筛查分，不是官方风险等级、发生概率或医学健康风险",
            "本评估仅描述风险趋势和可能影响，不预测未来污染物浓度",
            "风驱动路径属于筛查级结果，未完整模拟地形、化学反应和垂直混合",
        ]

        wind_speed = _number(weather.get("mean_wind_speed"))
        stable, resultant, max_uncertainty = _stable_direction(weather)
        low_wind = wind_speed is not None and wind_speed < self.LOW_WIND_THRESHOLD_MS
        if low_wind and stable:
            score += 25.0
            factors.append("低风速条件可能降低污染扩散能力")
            evidence.append({
                "type": "diffusion_condition",
                "signal": "low_wind_stable_direction",
                "mean_wind_speed_ms": wind_speed,
                "direction_resultant_length": resultant,
                "max_direction_uncertainty_deg": max_uncertainty,
            })
            actions.append("建议加强低风速时段的环境监测，并关注污染滞留趋势")
        elif wind_speed is None:
            limitations.append("缺少可用于扩散条件判断的风速结果")

        path_available = _path_exists(prediction)
        low_path_uncertainty = (
            max_uncertainty is not None
            and max_uncertainty <= self.LOW_DIRECTION_UNCERTAINTY_DEG
        )
        if path_available and low_path_uncertainty:
            score += 15.0
            factors.append("污染输送方向较明确")
            evidence.append({
                "type": "transport_path",
                "signal": "available_with_lower_direction_uncertainty",
                "max_direction_uncertainty_deg": max_uncertainty,
            })
            actions.append("建议优先关注预测输送方向及其不确定性走廊覆盖区域")
        elif not path_available:
            reason = prediction.get("reason")
            limitations.append(
                f"污染输送路径不可用：{reason}"
                if isinstance(reason, str) and reason.strip()
                else "污染输送路径不可用，影响方向存在较大不确定性"
            )
        else:
            limitations.append("污染输送路径方向不确定性较高，仅可用于趋势参考")

        if _has_sensitive_targets(spatial):
            score += 20.0
            factors.append("预测影响区域包含敏感目标")
            evidence.append({
                "type": "sensitive_targets",
                "signal": "downwind_sensitive_pois_present",
                "count": _number(spatial.get("sensitive_poi_count")),
                "by_type": dict(spatial.get("sensitive_poi_by_type") or {}),
            })
            actions.append("建议对可能受影响的学校、医院等敏感目标加强提示与巡查")
        elif not spatial:
            limitations.append("缺少空间分析结果，无法核查下风向敏感目标")

        traffic_score = _number(traffic.get("traffic_pressure_score"))
        traffic_level = str(
            traffic.get("traffic_pressure_level") or traffic.get("traffic_pressure") or ""
        ).lower()
        high_traffic = (
            traffic_score is not None and traffic_score >= self.HIGH_TRAFFIC_THRESHOLD
        ) or traffic_level in {"high", "very_high"}
        if high_traffic:
            score += 20.0
            factors.append("交通活动可能增强局地污染风险")
            evidence.append({
                "type": "traffic_pressure",
                "signal": "elevated_relative_pressure",
                "score": traffic_score,
                "level": traffic_level or None,
                "semantics": "relative_risk_score_not_absolute_traffic_flow",
            })
            actions.append("建议在高交通压力时段评估分流、公交替代或短时交通管理措施")
        elif not traffic:
            limitations.append("缺少交通压力结果，无法评估交通叠加风险")

        pollution_status = str(air.get("pollution_status") or "").lower()
        anomaly_score = _number(air.get("anomaly_score"))
        if pollution_status in {"moderate", "abnormal", "high", "very_high"} or (
            anomaly_score is not None and anomaly_score >= 0.7
        ):
            score += 10.0
            factors.append("当前空气质量异常信号可能抬高后续时段风险")
            evidence.append({
                "type": "current_air_quality",
                "signal": "elevated_current_condition",
                "pollution_status": pollution_status or None,
                "anomaly_score": anomaly_score,
            })
        elif not air:
            limitations.append("缺少当前空气质量结果，风险基线信息有限")

        score = round(min(100.0, score), 1)
        if not path_available:
            risk_level: FutureRiskLevel = "limited"
        elif score >= 65.0:
            risk_level = "high"
        elif score >= 35.0:
            risk_level = "medium"
        else:
            risk_level = "low"

        if not actions:
            actions.append("建议持续监测风场、空间影响和交通压力变化后再调整措施")
        affected_area = spatial.get("affected_area")
        if not isinstance(affected_area, str) or not affected_area.strip():
            affected_area = (
                "沿预测输送路径及不确定性走廊的可能影响区域"
                if path_available
                else "暂无法确定"
            )

        return FutureRiskAssessment(
            risk_level=risk_level,
            risk_score=score,
            prediction_window=_prediction_window(weather, prediction),
            affected_area=affected_area,
            risk_factors=tuple(dict.fromkeys(factors)),
            evidence=tuple(evidence),
            recommended_actions=tuple(dict.fromkeys(actions)),
            limitations=tuple(dict.fromkeys(limitations)),
        )


__all__ = ["FutureRiskAssessment", "FutureRiskLevel", "PredictionFusion"]
