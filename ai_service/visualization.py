"""LangGraph-free, evidence-backed visualization planning for phase 3A.

This module deliberately stops at semantic specs and a bounded data bundle.  It
never emits ECharts/MapLibre code and never treats a catalog entry as proof that
the current business result actually contains that data.
"""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Literal, Mapping
from zoneinfo import ZoneInfo

from ai_compat.contracts import ViewCommand, VisualizationSpec
from ai_compat.visualization_catalog import VisualizationDataCatalog, VisualizationMetric


PlannerStatus = Literal["planned", "no_match", "invalid", "reset"]
VisualizationPlanAction = Literal["show", "update", "reset", "none"]

HK = ZoneInfo("Asia/Hong_Kong")
_CHART_TYPES = ("line", "area", "bar", "radar", "pie", "donut", "stacked_bar", "scatter")
_MAP_TYPES = ("geojson", "path", "layered")
_COMPOSITE_WEATHER_METRICS = (
    "meteorology.hourly.temperature",
    "meteorology.hourly.humidity",
    "meteorology.hourly.wind_speed",
    "meteorology.hourly.precipitation",
)

_LABELS = {
    "meteorology.hourly.wind_speed": "风速",
    "meteorology.hourly.wind_direction": "风向",
    "meteorology.hourly.temperature": "温度",
    "meteorology.hourly.humidity": "湿度",
    "meteorology.hourly.precipitation": "降水量",
    "meteorology.hourly.cloud_cover": "云量",
    "air_quality.hourly.no2": "NO₂",
    "air_quality.hourly.pm25": "PM₂.₅",
    "air_quality.hourly.aqhi": "AQHI",
    "air_quality.aqhi_average": "平均 AQHI",
    "air_quality.pollutant_profile": "污染物概况",
    "traffic.vehicle_composition": "车型组成",
    "traffic.pressure_score": "交通压力筛查评分",
    "source.screening_scores": "污染来源筛查评分",
    "spatial.affected_area": "潜在影响区域",
    "spatial.upwind_roads": "上风向道路",
    "spatial.sensitive_pois": "敏感目标",
    "spatial.pollution_path": "污染输送筛查路径",
}
_SINGLE_METRIC_TITLES = {
    "air_quality.pollutant_profile": "污染物浓度对比图",
    "air_quality.aqhi_average": "AQHI 独立指标图",
    "traffic.vehicle_composition": "车辆类型构成图",
    "source.screening_scores": "污染来源筛查评分图",
}


def empty_visualization_context() -> dict[str, Any]:
    return {
        "last_spec": None,
        "last_view_id": None,
        "active_chart_type": None,
        "active_metrics": [],
        "active_data_source": None,
        "view_mode": None,
        "revision": 0,
    }


class VisualizationDataUnavailable(ValueError):
    """The catalog or current canonical result cannot supply a requested metric."""


@dataclass(frozen=True, slots=True)
class VisualizationPlanResult:
    status: PlannerStatus
    action: VisualizationPlanAction
    view_id: str | None
    spec: VisualizationSpec | None
    command: ViewCommand | None
    data: dict[str, Any] | None
    warnings: tuple[str, ...]
    reason: str

    @property
    def normalized_spec(self) -> VisualizationSpec | None:
        return self.spec if self.status == "planned" else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "action": self.action,
            "view_id": self.view_id,
            "spec": self.spec.to_dict() if self.spec else None,
            "view_command": self.command.to_dict() if self.command else None,
            "data": self.data,
            "warnings": list(self.warnings),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class VisualizationValidationResult:
    """Validation result compatible with the source validator's shape."""

    valid: bool
    normalized_spec: VisualizationSpec | None
    warnings: tuple[str, ...]
    reason: str | None
    data: dict[str, Any] | None = None

    @property
    def spec(self) -> VisualizationSpec | None:
        return self.normalized_spec

    def __getitem__(self, key: str) -> Any:
        values = {
            "valid": self.valid,
            "spec": self.normalized_spec,
            "normalized_spec": self.normalized_spec,
            "warnings": self.warnings,
            "reason": self.reason,
            "data": self.data,
        }
        return values[key]

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "normalized_spec": self.normalized_spec.to_dict() if self.normalized_spec else None,
            "warnings": list(self.warnings),
            "reason": self.reason,
            "data": self.data,
        }


def _parse_local_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        result = datetime.fromisoformat(text)
    except ValueError:
        return None
    if result.tzinfo is not None:
        return result.astimezone(HK).replace(tzinfo=None)
    return result


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


class CanonicalVisualizationDataProvider:
    """Read-only extractor from the phase-1 canonical result envelope."""

    def __init__(self, catalog: VisualizationDataCatalog | None = None) -> None:
        self.catalog = catalog or VisualizationDataCatalog.load()

    @staticmethod
    def _envelope(canonical: Mapping[str, Any], agent: str) -> Mapping[str, Any]:
        agents = canonical.get("agent_results")
        envelope = agents.get(agent) if isinstance(agents, Mapping) else None
        if not isinstance(envelope, Mapping):
            raise VisualizationDataUnavailable(f"{agent} canonical result is absent")
        if envelope.get("status") not in {"available", "partial"}:
            raise VisualizationDataUnavailable(f"{agent} status is {envelope.get('status')!r}")
        result = envelope.get("result")
        if not isinstance(result, Mapping):
            raise VisualizationDataUnavailable(f"{agent}.result is unavailable")
        return result

    @staticmethod
    def _task(canonical: Mapping[str, Any]) -> Mapping[str, Any]:
        task = canonical.get("task")
        return task if isinstance(task, Mapping) else {}

    def _hourly(self, result: Mapping[str, Any], field: str, task: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows = result.get("hourly_series")
        if not isinstance(rows, list):
            raise VisualizationDataUnavailable("hourly_series is not present in the canonical result")
        start = _parse_local_time(task.get("start_time"))
        end = _parse_local_time(task.get("end_time"))
        values: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping) or not _is_number(row.get(field)):
                continue
            timestamp = row.get("time")
            parsed = _parse_local_time(timestamp)
            if parsed is None:
                continue
            if start is not None and parsed < start:
                continue
            if end is not None and parsed > end:
                continue
            values.append({"time": timestamp, "value": row[field]})
        if not values:
            raise VisualizationDataUnavailable(f"hourly_series contains no usable {field} values in the requested range")
        return values

    def extract(self, metric_key: str, canonical: Mapping[str, Any]) -> dict[str, Any]:
        metric = self.catalog.require(metric_key)
        if metric.availability == "unavailable":
            raise VisualizationDataUnavailable(metric.unavailable_reason or f"{metric_key} is unavailable")
        if not isinstance(canonical, Mapping):
            raise VisualizationDataUnavailable("canonical agent results are unavailable")
        task = self._task(canonical)
        if metric_key.startswith("meteorology.hourly."):
            field = metric_key.rsplit(".", 1)[1]
            result = self._envelope(canonical, "MeteorologyAgent")
            values = self._hourly(result, field, task)
            return self._bundle(metric, values)
        if metric_key.startswith("air_quality.hourly."):
            field = metric_key.rsplit(".", 1)[1]
            result = self._envelope(canonical, "AirQualityAgent")
            values = self._hourly(result, field, task)
            return self._bundle(metric, values)
        if metric_key == "air_quality.pollutant_profile":
            result = self._envelope(canonical, "AirQualityAgent")
            profile = result.get("pollutant_details")
            if not isinstance(profile, Mapping) or not profile:
                raise VisualizationDataUnavailable("pollutant_details is unavailable")
            values = [
                {
                    "category": str(name),
                    "avg": item.get("avg"),
                    "max": item.get("max"),
                    "min": item.get("min"),
                    "unit": "mg/m³" if str(name).lower() == "co" else "μg/m³",
                }
                for name, item in profile.items()
                if isinstance(item, Mapping) and any(_is_number(item.get(key)) for key in ("avg", "max", "min"))
            ]
            if not values:
                raise VisualizationDataUnavailable("pollutant_details contains no numeric values")
            return self._bundle(metric, values)
        if metric_key == "air_quality.aqhi_average":
            result = self._envelope(canonical, "AirQualityAgent")
            value = result.get("avg_aqhi")
            if not _is_number(value):
                raise VisualizationDataUnavailable("avg_aqhi is unavailable for the current analysis window")
            return self._bundle(metric, [{
                "category": "avg_aqhi",
                "value": value,
                "level": result.get("aqhi_level"),
            }])
        if metric_key == "traffic.vehicle_composition":
            result = self._envelope(canonical, "TrafficAgent")
            values = self._mapping_values(result.get("proportions_pct"), percent=True)
            return self._bundle(metric, values)
        if metric_key == "traffic.pressure_score":
            result = self._envelope(canonical, "TrafficAgent")
            value = result.get("traffic_pressure_score")
            if not _is_number(value):
                raise VisualizationDataUnavailable("traffic_pressure_score is unavailable")
            return self._bundle(metric, [{"category": "traffic_pressure_score", "value": value}])
        if metric_key == "source.screening_scores":
            result = self._envelope(canonical, "SourceAgent")
            values = self._mapping_values(result.get("source_attribution"))
            return self._bundle(metric, values)
        if metric_key.startswith("spatial."):
            result = self._envelope(canonical, "SpatialAgent")
            source_field = {
                "spatial.affected_area": "affected_area_geojson",
                "spatial.upwind_roads": "upwind_roads",
                "spatial.sensitive_pois": "sensitive_pois",
            }.get(metric_key)
            if metric_key in {"spatial.pollution_path", "spatial.pollution_path_uncertainty"}:
                prediction = result.get("pollution_path_prediction")
                if not isinstance(prediction, Mapping):
                    raise VisualizationDataUnavailable("pollution_path_prediction is unavailable")
                source_field = "path_geojson" if metric_key == "spatial.pollution_path" else "uncertainty_corridor_geojson"
                value = prediction.get(source_field)
            else:
                value = result.get(source_field or "")
            if value is None or value == [] or value == {}:
                raise VisualizationDataUnavailable(f"{metric.field_path} is unavailable")
            return self._bundle(metric, [{"value": deepcopy(value)}])
        raise VisualizationDataUnavailable(f"no read-only provider is registered for {metric_key}")

    @staticmethod
    def _mapping_values(value: Any, *, percent: bool = False) -> list[dict[str, Any]]:
        if not isinstance(value, Mapping):
            raise VisualizationDataUnavailable("mapping result is unavailable")
        output = [{"category": str(key), "value": item} for key, item in value.items() if _is_number(item)]
        if not output:
            raise VisualizationDataUnavailable("mapping result contains no numeric values")
        return output

    @staticmethod
    def _bundle(metric: VisualizationMetric, values: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "metric": metric.key,
            "unit": metric.unit,
            "field_path": metric.field_path,
            "source_agent": metric.source_agent,
            "semantic_type": metric.semantic_type,
            "values": values,
        }

    def bundle(self, metrics: tuple[str, ...], canonical: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "provider": "canonical_agent_results",
            "timezone": "Asia/Hong_Kong",
            "task": deepcopy(dict(self._task(canonical))),
            "metrics": {metric: self.extract(metric, canonical) for metric in metrics},
        }


class VisualizationValidator:
    """Semantic validator; it accepts no executable renderer configuration."""

    def __init__(
        self,
        catalog: VisualizationDataCatalog | None = None,
        data_provider: CanonicalVisualizationDataProvider | None = None,
        model: Any = None,
    ) -> None:
        self.model = model
        self.catalog = catalog or VisualizationDataCatalog.load()
        self.data_provider = data_provider or CanonicalVisualizationDataProvider(self.catalog)

    @staticmethod
    def _invalid(reason: str, warnings: list[str] | None = None) -> VisualizationValidationResult:
        return VisualizationValidationResult(False, None, tuple(warnings or ()), reason, None)

    def validate(
        self,
        spec: VisualizationSpec | Mapping[str, Any],
        canonical: Mapping[str, Any] | None = None,
    ) -> VisualizationValidationResult:
        if isinstance(spec, Mapping):
            spec = VisualizationPlanner._previous_spec(spec)
            if spec is None:
                return self._invalid("visualization spec mapping is invalid")
        warnings = list(spec.warnings)
        if spec.version != self.catalog.version:
            return self._invalid(f"unsupported VisualizationSpec version: {spec.version!r}", warnings)
        if spec.view_type not in {"chart", "map"} or not spec.title.strip():
            return self._invalid("view_type and title are required", warnings)
        if not spec.metrics or len(set(spec.metrics)) != len(spec.metrics):
            return self._invalid("metrics must be non-empty and unique", warnings)
        definitions: list[VisualizationMetric] = []
        for key in spec.metrics:
            try:
                metric = self.catalog.require(key)
            except KeyError:
                return self._invalid(f"unknown metric: {key!r}", warnings)
            if metric.availability == "unavailable":
                return self._invalid(metric.unavailable_reason or f"metric {key!r} is unavailable", warnings)
            if metric.data_source != spec.data_source:
                return self._invalid(f"metric {key!r} does not belong to {spec.data_source!r}", warnings)
            if metric.dimension != spec.dimension:
                return self._invalid(f"metric {key!r} requires dimension {metric.dimension!r}", warnings)
            definitions.append(metric)
        warnings.extend(self._semantic_warnings(definitions))
        grouping = spec.grouping if isinstance(spec.grouping, Mapping) else {}
        axes = grouping.get("y_axis_by_metric", grouping.get("yAxisByMetric", {}))
        axes = axes if isinstance(axes, Mapping) else {}
        axis_indices = grouping.get(
            "y_axis_index_by_metric", grouping.get("yAxisIndexByMetric", {})
        )
        axis_indices = axis_indices if isinstance(axis_indices, Mapping) else {}
        units = {metric.unit for metric in definitions if metric.unit}
        if len(units) > 1:
            active_axes: Mapping[str, Any]
            if axis_indices:
                if any(metric.key not in axis_indices for metric in definitions):
                    return self._invalid("different physical units require explicit y-axis index mapping", warnings)
                if any(
                    not isinstance(axis_indices[metric.key], int)
                    or isinstance(axis_indices[metric.key], bool)
                    or not 0 <= axis_indices[metric.key] <= 5
                    for metric in definitions
                ):
                    return self._invalid("y-axis indexes must be integers from 0 to 5", warnings)
                active_axes = axis_indices
            else:
                if any(metric.key not in axes for metric in definitions):
                    return self._invalid("different physical units require explicit y-axis mapping", warnings)
                if set(axes.values()) - {"left", "right"}:
                    return self._invalid("y-axis must be 'left' or 'right'", warnings)
                active_axes = axes
            units_by_axis: dict[str, set[str]] = {}
            for metric in definitions:
                units_by_axis.setdefault(str(active_axes[metric.key]), set()).add(metric.unit or "")
            if any(len(axis_units) > 1 for axis_units in units_by_axis.values()):
                return self._invalid("different physical units cannot share one y-axis", warnings)
        if spec.view_type == "chart":
            if spec.map_type is not None or spec.layers:
                return self._invalid("chart must not contain map fields", warnings)
            allowed = set(definitions[0].supported_view_types)
            for metric in definitions[1:]:
                allowed.intersection_update(metric.supported_view_types)
            chart_type = spec.chart_type
            if chart_type not in _CHART_TYPES:
                return self._invalid(f"unknown chart_type: {chart_type!r}", warnings)
            if chart_type not in allowed:
                replacement = self._preferred(definitions, allowed)
                if replacement is None:
                    return self._invalid(f"chart_type {chart_type!r} is incompatible with requested metrics", warnings)
                warnings.append(f"requested chart_type {chart_type!r} was safely replaced with {replacement!r}")
                chart_type = replacement
            normalized = replace(
                spec,
                chart_type=chart_type,
                map_type=None,
                layers=(),
                unit=next(iter(units)) if len(units) == 1 else None,
                warnings=tuple(dict.fromkeys(warnings)),
            )
        else:
            if spec.chart_type is not None or spec.map_type not in _MAP_TYPES:
                return self._invalid("map requires a valid map_type and no chart_type", warnings)
            if not spec.layers:
                return self._invalid("map requires at least one layer", warnings)
            for layer in spec.layers:
                if not isinstance(layer, Mapping) or not layer.get("id") or layer.get("metric") not in spec.metrics:
                    return self._invalid("map layers must reference spec metrics", warnings)
                if layer.get("layer_type") not in {"fill", "line", "circle", "symbol"}:
                    return self._invalid("unknown map layer_type", warnings)
            normalized = replace(spec, unit=None, chart_type=None, warnings=tuple(dict.fromkeys(warnings)))
        data = None
        if canonical is not None:
            try:
                data = self.data_provider.bundle(spec.metrics, canonical)
            except VisualizationDataUnavailable as exc:
                return self._invalid(str(exc), warnings)
        return VisualizationValidationResult(True, normalized, normalized.warnings, None, data)

    @staticmethod
    def _preferred(definitions: list[VisualizationMetric], allowed: set[str]) -> str | None:
        semantics = {metric.semantic_type for metric in definitions}
        if "independent_source_screening_score" in semantics and "radar" in allowed:
            return "radar"
        if "vehicle_composition" in semantics and "donut" in allowed:
            return "donut"
        if "window_aggregate_concentration_profile" in semantics and "bar" in allowed:
            return "bar"
        for candidate in ("line", "area", "bar", "radar", "donut", "stacked_bar"):
            if candidate in allowed:
                return candidate
        return None

    @staticmethod
    def _semantic_warnings(definitions: list[VisualizationMetric]) -> list[str]:
        warnings: list[str] = []
        semantics = {metric.semantic_type for metric in definitions}
        if "independent_source_screening_score" in semantics:
            warnings.append("来源数值是相互独立的筛查评分，不是污染贡献率，不要求合计为100%。")
        if "vehicle_composition" in semantics:
            warnings.append("车辆类型为 ATC 参考期车型估计比例，不是当前时段实测车辆计数或实时道路流量。")
        if "window_aggregate_concentration_profile" in semantics:
            warnings.append("污染物数值是当前分析时段的浓度统计，不是逐小时污染物趋势。")
        if "window_average_health_index" in semantics:
            warnings.append("AQHI 是当前分析时段平均指标，不是逐小时值、实时值或未来预测。")
        if semantics & {"traffic_pressure_score", "traffic_pressure_index"}:
            warnings.append("交通压力是统计/规则代理评分，不代表真实道路流量。")
        if semantics & {"screening_affected_area", "modelled_forward_advection_path", "modelled_path_uncertainty"}:
            warnings.append("空间结果是模型筛查几何，不代表实测污染浓度场或实测传播轨迹。")
        if "circular_direction" in semantics:
            warnings.append("风向是循环角度；前端不得按普通连续标量插值或求平均。")
        return warnings


class VisualizationPlanner:
    """Validate model-selected views over trusted business data."""

    def __init__(
        self,
        catalog: VisualizationDataCatalog | None = None,
        validator: VisualizationValidator | None = None,
        data_provider: CanonicalVisualizationDataProvider | None = None,
        model: Any = None,
    ) -> None:
        self.model = model
        self.catalog = catalog or VisualizationDataCatalog.load()
        self.data_provider = data_provider or CanonicalVisualizationDataProvider(self.catalog)
        self.validator = validator or VisualizationValidator(self.catalog, self.data_provider)

    def plan(self, text: str, *, canonical: Mapping[str, Any] | None = None,
             visualization_context: Mapping[str, Any] | None = None,
             task_context: Mapping[str, Any] | None = None,
             analysis_context: Mapping[str, Any] | None = None) -> VisualizationPlanResult:
        context = visualization_context or empty_visualization_context()
        planner = getattr(self.model, "plan_visualization", None)
        if not callable(planner):
            return VisualizationPlanResult("no_match", "none", None, None, None, None, (), "model_required")
        catalog = [{"key": metric.key, "dimension": metric.dimension,
                    "semantic_type": metric.semantic_type, "data_source": metric.data_source}
                   for metric in self.catalog.available()]
        try:
            raw = planner(question=text, catalog=catalog, previous=context.get("last_spec"))
            action = raw.get("action")
            if action == "reset":
                return self._reset(context)
            if action == "none":
                return VisualizationPlanResult("no_match", "none", None, None, None, None, (), "model_skipped_view")
            metrics = raw.get("metrics")
            if action not in {"show", "update"} or not isinstance(metrics, list) or not metrics:
                raise ValueError("Invalid model view decision")
            if any(not isinstance(key, str) for key in metrics):
                raise ValueError("Invalid model metric")
            definitions = [self.catalog.require(key) for key in metrics]
            if any(metric.data_source == "spatial" for metric in definitions):
                candidate = self._map_candidate(metrics, task_context or analysis_context)
            else:
                candidate = self._candidate_for_metrics(tuple(metrics), task_context or analysis_context,
                                                        chart_type=raw.get("chart_type"))
            return self._validate_plan(candidate, canonical, action=action,
                                       view_id=self._view_id(candidate), reason="model_selected_view")
        except (ValueError, KeyError, TypeError):
            return VisualizationPlanResult("invalid", "none", None, None, None, None, (), "invalid_model_view")

    def _validate_plan(
        self,
        candidate: VisualizationSpec,
        canonical: Mapping[str, Any] | None,
        *,
        action: VisualizationPlanAction,
        view_id: str,
        reason: str,
    ) -> VisualizationPlanResult:
        result = self.validator.validate(candidate, canonical)
        if not result["valid"]:
            return VisualizationPlanResult("invalid", "none", None, None, None, None, tuple(result["warnings"]), str(result["reason"]))
        normalized = result["spec"]
        assert isinstance(normalized, VisualizationSpec)
        command = ViewCommand(action=action, view_id=view_id, spec=normalized, reason=reason)  # type: ignore[arg-type]
        return VisualizationPlanResult("planned", action, view_id, normalized, command, result["data"], tuple(result["warnings"]), reason)

    def _reset(self, context: Mapping[str, Any]) -> VisualizationPlanResult:
        command = ViewCommand(action="reset", view_id="default-map", spec=None, reason="return_to_default_map")
        return VisualizationPlanResult("reset", "reset", "default-map", None, command, None, (), "return_to_default_map")

    @staticmethod
    def update_context(
        current: Mapping[str, Any] | None,
        result: VisualizationPlanResult,
    ) -> dict[str, Any]:
        """Pure helper: only a valid plan may replace persisted view state."""
        existing = dict(current or empty_visualization_context())
        if result.status == "reset":
            return {**empty_visualization_context(), "revision": int(existing.get("revision", 0) or 0) + 1}
        if result.status != "planned" or result.spec is None:
            return existing
        return {
            "last_spec": result.spec.to_dict(),
            "last_view_id": result.view_id,
            "active_chart_type": result.spec.chart_type,
            "active_metrics": list(result.spec.metrics),
            "active_data_source": result.spec.data_source,
            "view_mode": result.spec.view_type,
            "revision": int(existing.get("revision", 0) or 0) + 1,
            "data_provider": "canonical_agent_results",
        }

    def _candidate_for_metrics(
        self,
        metrics: tuple[str, ...],
        task_context: Mapping[str, Any] | None,
        *,
        chart_type: str | None,
    ) -> VisualizationSpec:
        definitions = [self.catalog.require(key) for key in metrics]
        source = definitions[0].data_source
        dimension = definitions[0].dimension
        labels = "、".join(_LABELS.get(key, key) for key in metrics)
        grouping: dict[str, Any] = {"field": dimension, "mode": "series"}
        units = [metric.unit for metric in definitions]
        distinct = list(dict.fromkeys(unit for unit in units if unit))
        if len(distinct) > 1:
            if len(distinct) == 2:
                grouping["y_axis_by_metric"] = {
                    metric.key: "left" if metric.unit == distinct[0] else "right"
                    for metric in definitions
                }
            else:
                axis_by_unit = {unit: index for index, unit in enumerate(distinct)}
                grouping["y_axis_index_by_metric"] = {
                    metric.key: axis_by_unit[metric.unit]
                    for metric in definitions
                    if metric.unit in axis_by_unit
                }
        if "meteorology.hourly.precipitation" in metrics:
            grouping["series_type_by_metric"] = {
                metric: ("bar" if metric == "meteorology.hourly.precipitation" else "line")
                for metric in metrics
            }
        if chart_type is None:
            if source == "source":
                chart_type = "radar"
            elif source == "traffic" and "vehicle_composition" in {metric.semantic_type for metric in definitions}:
                chart_type = "donut"
            elif source == "air_quality":
                chart_type = "bar"
            else:
                chart_type = "line"
        title = (
            _SINGLE_METRIC_TITLES.get(metrics[0])
            if len(metrics) == 1
            else None
        ) or (f"{labels}时段趋势" if dimension == "time" else f"{labels}可视化")
        return VisualizationSpec(
            view_type="chart",
            title=title,
            data_source=source,  # type: ignore[arg-type]
            metrics=metrics,
            dimension=dimension,  # type: ignore[arg-type]
            filters=self._filters(task_context),
            grouping=grouping,
            unit=None,
            warnings=(),
            chart_type=chart_type,
            map_type=None,
            layers=(),
        )

    def _map_candidate(self, metrics: list[str], task_context: Mapping[str, Any] | None) -> VisualizationSpec:
        layers = []
        for metric in metrics:
            layer_type = "fill" if metric.endswith("affected_area") else "line" if "roads" in metric or "path" in metric else "circle"
            layers.append({"id": metric.replace(".", "-"), "metric": metric, "layer_type": layer_type, "visible": True})
        return VisualizationSpec(
            view_type="map",
            title="空间影响筛查地图",
            data_source="spatial",
            metrics=tuple(metrics),
            dimension="space",
            filters=self._filters(task_context),
            grouping=None,
            unit=None,
            warnings=(),
            chart_type=None,
            map_type="layered",
            layers=tuple(layers),
        )

    def _available_map_metrics(self, canonical: Mapping[str, Any] | None) -> list[str]:
        if canonical is None:
            return []
        selected: list[str] = []
        for key in ("spatial.affected_area", "spatial.upwind_roads", "spatial.sensitive_pois", "spatial.pollution_path"):
            try:
                self.data_provider.extract(key, canonical)
            except (KeyError, VisualizationDataUnavailable):
                continue
            selected.append(key)
        return selected

    def _available_hourly_weather_metrics(
        self, canonical: Mapping[str, Any] | None
    ) -> list[str]:
        if canonical is None:
            return []
        selected: list[str] = []
        for key in _COMPOSITE_WEATHER_METRICS:
            try:
                self.data_provider.extract(key, canonical)
            except (KeyError, VisualizationDataUnavailable):
                continue
            selected.append(key)
        return selected

    @staticmethod
    def _filters(task_context: Mapping[str, Any] | None) -> dict[str, Any]:
        context = task_context or {}
        output = {key: deepcopy(context[key]) for key in ("station_id", "station_name", "start_time", "end_time", "lat", "lon") if context.get(key) is not None}
        output["timezone"] = "Asia/Hong_Kong"
        return output

    @staticmethod
    def _view_id(spec: VisualizationSpec) -> str:
        return f"{spec.data_source}-{'-'.join(spec.metrics)}"

    @staticmethod
    def _previous_spec(value: Any) -> VisualizationSpec | None:
        if isinstance(value, VisualizationSpec):
            return value
        if not isinstance(value, Mapping):
            return None
        try:
            return VisualizationSpec(
                version=str(value.get("version", "1.0")),
                view_type=str(value.get("view_type", value.get("viewType", ""))),
                title=str(value.get("title", "")),
                data_source=str(value.get("data_source", value.get("dataSource", ""))),
                metrics=tuple(str(item) for item in value.get("metrics", ())),
                dimension=str(value.get("dimension", "")),
                filters=dict(value.get("filters", {})),
                grouping=dict(value["grouping"]) if isinstance(value.get("grouping"), Mapping) else None,
                unit=value.get("unit"),
                warnings=tuple(str(item) for item in value.get("warnings", ())),
                chart_type=value.get("chart_type", value.get("chartType")),
                map_type=value.get("map_type", value.get("mapType")),
                layers=tuple(
                    {
                        "id": item.get("id"),
                        "metric": item.get("metric"),
                        "layer_type": item.get("layer_type", item.get("layerType")),
                        "visible": item.get("visible", True),
                    }
                    for item in value.get("layers", ())
                    if isinstance(item, Mapping)
                ),
            )
        except (TypeError, ValueError):
            return None


__all__ = [
    "CanonicalVisualizationDataProvider",
    "VisualizationDataUnavailable",
    "VisualizationPlanResult",
    "VisualizationPlanner",
    "VisualizationValidationResult",
    "VisualizationValidator",
    "empty_visualization_context",
]
