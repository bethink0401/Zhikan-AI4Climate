"""Field-aware adapter from CoordinatorMin output to canonical AgentResults."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .contracts import (
    AgentResultEnvelope,
    CanonicalAgentResults,
    DataUnavailable,
    Evidence,
)


class CoordinatorResultValidationError(ValueError):
    """Raised when the value is not a safe CoordinatorMin response envelope."""


DOMAIN_AGENT = {
    "air_quality": "AirQualityAgent",
    "meteorology": "MeteorologyAgent",
    "traffic": "TrafficAgent",
    "spatial": "SpatialAgent",
    "source": "SourceAgent",
    "decision": "DecisionAgent",
    "report": "ReportAgent",
}

TASK_FIELDS = ("task_id", "station_name", "station_id", "start_time", "end_time", "lat", "lon")

RESULT_FIELDS = {
    "air_quality": (
        "pollution_status", "main_pollutants", "aqhi_level", "avg_aqhi",
        "traffic_pollutant_signal", "traffic_pollutant_signal_score",
        "traffic_hour_clim_ratio", "nox_year_percentile", "midday_climatology_no2",
        "anomaly_score", "roadside_station", "multi_station_sync",
        "spatial_contrast", "pollutant_details", "hourly_series", "parcel_initial_conditions", "parcel_start_conditions",
    ),
    "meteorology": (
        "task_id", "station_id", "station_name", "start_time", "end_time", "lat", "lon",
        "weather_risk", "risk_type", "dispersion_condition", "static_wind_score",
        "wind_shift_score", "humidity_risk_score", "rain_removal_score",
        "insufficient_rain_removal_score", "mean_wind_speed",
        "dominant_wind_direction", "dominant_wind_direction_label", "upwind_direction",
        "downwind_direction", "mean_humidity", "total_precipitation",
        "mean_temperature", "mean_pressure_hpa", "mean_cloud_cover", "weather_code_daily",
        "dominant_weather_code_daily", "weather_code_daily_series", "hourly_count",
        "hourly_series", "wind_direction_forecast", "pollution_path_prediction",
        "wind_speed", "wind_direction", "latest_wind_speed", "latest_wind_direction",
        "upwind", "downwind",
    ),
    "traffic": (
        "traffic_pressure", "traffic_pressure_score", "traffic_pressure_index",
        "traffic_pressure_level", "dominant_vehicle_types", "heavy_vehicle_signal",
        "bus_ratio", "goods_vehicle_ratio", "taxi_ratio", "private_car_ratio",
        "vehicle_focus", "proportions_pct", "road_links",
    ),
    "spatial": (
        "upwind_roads", "nearby_road_count", "nearby_road_segment_count",
        "affected_area", "affected_area_geojson", "sensitive_poi_count",
        "sensitive_poi_by_type", "sensitive_pois", "wind_direction_interpreted_as",
        "station", "analysis_radius_km", "wind_direction", "wind_speed",
        "spatial_confidence", "pollution_path_prediction", "propagation_exposure", "directional_exposure",
    ),
    "source": (
        "source_attribution", "main_judgement", "risk_level", "component_scores",
        "meteorology_summary", "spatial_summary",
    ),
    "decision": (
        "decision_level", "suggested_area", "suggested_time", "suggested_roads",
        "vehicle_focus", "actions", "review_indicators",
    ),
    "report": (
        "report_title", "report_text", "summary", "decision_summary", "sections",
    ),
}

UNITS = {
    "air_quality": {
        "result.hourly_series[].no2": "μg/m³",
        "result.hourly_series[].pm25": "μg/m³",
        "result.hourly_series[].aqhi": "AQHI index",
        "result.avg_aqhi": "AQHI index",
        "result.pollutant_details.no2.*": "μg/m³",
        "result.pollutant_details.nox.*": "μg/m³",
        "result.pollutant_details.pm25.*": "μg/m³",
        "result.pollutant_details.pm10.*": "μg/m³",
        "result.pollutant_details.o3.*": "μg/m³",
        "result.pollutant_details.so2.*": "μg/m³",
        "result.pollutant_details.co.*": "mg/m³",
    },
    "meteorology": {
        "result.hourly_series[].wind_speed": "m/s",
        "result.hourly_series[].wind_direction": "degree (meteorological wind-from)",
        "result.hourly_series[].humidity": "%",
        "result.hourly_series[].precipitation": "mm",
        "result.hourly_series[].temperature": "°C",
        "result.hourly_series[].pressure_hpa": "hPa",
        "result.hourly_series[].cloud_cover": "%",
        "result.mean_wind_speed": "m/s",
        "result.dominant_wind_direction": "degree (meteorological wind-from)",
        "result.mean_humidity": "%",
        "result.total_precipitation": "mm",
        "result.mean_temperature": "°C",
        "result.mean_pressure_hpa": "hPa",
        "result.mean_cloud_cover": "%",
    },
    "traffic": {
        "result.proportions_pct.*": "%",
        "result.traffic_pressure_score": "dimensionless screening score",
        "result.traffic_pressure_index": "dimensionless screening index",
    },
    "spatial": {
        "result.analysis_radius_km": "km",
        "result.wind_direction": "degree (meteorological wind-from)",
        "result.wind_speed": "m/s",
        "result.upwind_roads[].distance_to_station_km": "km",
        "result.sensitive_pois[].distance_to_station_km": "km",
    },
    "source": {
        "result.source_attribution.*": "dimensionless independent screening score",
        "result.component_scores.*": "dimensionless model component score",
    },
}

LIMITATIONS = {
    "air_quality": (
        "Hourly pollutant values are returned only when source records exist in the selected window.",
        "A null avg_aqhi or aqhi_level='unknown' means AQHI is unavailable, not zero.",
    ),
    "meteorology": (
        "pollution_path_prediction is a screening-level forward advection model, not a measured pollution trajectory or concentration forecast.",
        "Parcel survival coefficients are reference-matched to the existing exposure half-life, not empirically fitted for this model; missing inputs and persistence assumptions must be disclosed.",
    ),
    "traffic": (
        "Traffic pressure is derived from ATC vehicle composition and rule weights; it is not observed real-time road flow.",
    ),
    "spatial": (
        "Affected areas and paths are modelled screening geometry, not a measured pollution concentration field.",
    ),
    "source": (
        "Source scores are independent screening likelihoods and must not be interpreted as pollution contribution rates or normalized shares.",
    ),
    "decision": (
        "Recommendations inherit upstream data and rule-model limitations.",
    ),
    "report": (
        "Narrative text is generated from upstream Agent results and is not an independent observation source.",
    ),
}


def _copy_known(value: Mapping[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {name: deepcopy(value[name]) for name in names if name in value}


def _canonical_status(source_status: Any, result: Mapping[str, Any]) -> str:
    status = str(source_status or "").lower()
    if status == "success":
        return "available" if result else "unavailable"
    if status == "warning":
        return "partial" if result else "unavailable"
    if status == "error":
        return "error"
    return "unavailable"


class CoordinatorMinResultAdapter:
    """Adapt without mutating, resolving stations, or deriving absent measurements."""

    def adapt(self, payload: Mapping[str, Any]) -> CanonicalAgentResults:
        if not isinstance(payload, Mapping):
            raise CoordinatorResultValidationError("CoordinatorMin response must be a mapping")
        task_value = payload.get("task")
        if not isinstance(task_value, Mapping):
            raise CoordinatorResultValidationError("CoordinatorMin response.task must be a mapping")
        task = _copy_known(task_value, TASK_FIELDS)
        if not task.get("task_id"):
            raise CoordinatorResultValidationError("CoordinatorMin response.task.task_id is required")

        agent_results: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        for domain, canonical_name in DOMAIN_AGENT.items():
            envelope = self._adapt_domain(domain, canonical_name, payload.get(domain), task)
            agent_results[canonical_name] = envelope.to_dict()
            if envelope.status in {"unavailable", "error"}:
                warnings.append(f"{domain}: {envelope.status}")

        pipeline_value = payload.get("pipeline_status")
        pipeline = {
            str(key): bool(value)
            for key, value in pipeline_value.items()
        } if isinstance(pipeline_value, Mapping) else {}
        return CanonicalAgentResults(
            task=task,
            agent_results=agent_results,
            pipeline_status=pipeline,
            warnings=tuple(warnings),
        )

    def _adapt_domain(
        self,
        domain: str,
        canonical_name: str,
        value: Any,
        task: Mapping[str, Any],
    ) -> AgentResultEnvelope:
        if value is None:
            return self._unavailable(domain, canonical_name, "domain is absent from CoordinatorMin response")
        if not isinstance(value, Mapping):
            raise CoordinatorResultValidationError(f"CoordinatorMin response.{domain} must be a mapping")

        raw_result = value.get("result")
        if raw_result is None:
            raw_result = {}
        if not isinstance(raw_result, Mapping):
            raise CoordinatorResultValidationError(f"CoordinatorMin response.{domain}.result must be a mapping")
        result = _copy_known(raw_result, RESULT_FIELDS[domain])
        status = _canonical_status(value.get("status"), result)
        source_agent = str(value.get("agent_name") or canonical_name)
        data_quality = deepcopy(value.get("data_quality")) if isinstance(value.get("data_quality"), Mapping) else {}
        meta = deepcopy(value.get("meta")) if isinstance(value.get("meta"), Mapping) else {}
        evidence = self._evidence(domain, value.get("evidence"), task)
        confidence = value.get("confidence")
        confidence = float(confidence) if isinstance(confidence, (int, float)) else None
        provenance = {
            "task_id": task.get("task_id"),
            "station_id": task.get("station_id"),
            "station_name": task.get("station_name"),
            "start_time": task.get("start_time"),
            "end_time": task.get("end_time"),
            "created_at": value.get("created_at"),
            "source_meta": meta,
            "source_note": data_quality.get("note"),
        }
        unavailable = None
        if status in {"unavailable", "error"}:
            unavailable = DataUnavailable(
                domain=domain,
                reason=str(data_quality.get("note") or f"source status is {value.get('status')!r}"),
            )
        return AgentResultEnvelope(
            domain=domain,
            source_agent=source_agent,
            status=status,  # type: ignore[arg-type]
            source_status=str(value.get("status")) if value.get("status") is not None else None,
            result=result,
            evidence=evidence,
            confidence=confidence,
            data_quality=data_quality,
            units=deepcopy(UNITS.get(domain, {})),
            provenance=provenance,
            limitations=LIMITATIONS[domain],
            unavailable=unavailable,
        )

    @staticmethod
    def _evidence(
        domain: str,
        values: Any,
        task: Mapping[str, Any],
    ) -> tuple[Evidence, ...]:
        if not isinstance(values, (list, tuple)):
            return ()
        evidence: list[Evidence] = []
        for index, item in enumerate(values):
            if not isinstance(item, (str, int, float, bool)):
                continue
            evidence.append(Evidence(
                evidence_id=f"{domain}:{index + 1}",
                domain=domain,
                statement=str(item),
                source_path=f"{domain}.evidence[{index}]",
                evidence_type="agent_evidence",
                station_id=str(task["station_id"]) if task.get("station_id") is not None else None,
                station_name=str(task["station_name"]) if task.get("station_name") is not None else None,
                start_time=str(task["start_time"]) if task.get("start_time") is not None else None,
                end_time=str(task["end_time"]) if task.get("end_time") is not None else None,
            ))
        return tuple(evidence)

    @staticmethod
    def _unavailable(domain: str, canonical_name: str, reason: str) -> AgentResultEnvelope:
        unavailable = DataUnavailable(domain=domain, reason=reason)
        return AgentResultEnvelope(
            domain=domain,
            source_agent=canonical_name,
            status="unavailable",
            source_status=None,
            result={},
            limitations=LIMITATIONS[domain],
            unavailable=unavailable,
        )
