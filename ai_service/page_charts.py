"""Small, evidence-backed charts for page-specific questions."""
from __future__ import annotations

from .route_labels import route_display_name

import logging
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Protocol
from .chart_relevance import FOCI, relevant_metrics


logger = logging.getLogger(__name__)
REMOVED_CHART_METRICS = frozenset({"weather.propagation.distance_km"})


class PageChartModel(Protocol):
    def plan_page_charts(self, *, question: str,
                         catalog: list[dict[str, Any]]) -> dict[str, Any]: ...


def _chart(title: str, kind: str, dimension: str, categories: list[str],
           series: list[dict[str, Any]], metric: str, unit: str,
           evidence: Mapping[str, Any], source_agent: str = "TravelAgent") -> dict[str, Any]:
    return {
        "id": f"page-{metric}", "type": kind, "title": title,
        "metrics": [metric], "dimension": dimension,
        "categories": categories, "series": series, "warnings": [],
        "transform": {"time_filter": "all", "bucket_hours": 1,
                      "start_hour": 0, "end_hour": 24},
        "provenance": {"provider": evidence.get("source"),
                       "timezone": "Asia/Hong_Kong",
                       "task": {"station_name": evidence.get("label")},
                       "metrics": [{"key": metric, "unit": unit,
                                    "field_path": metric, "source_agent": source_agent}]},
    }


def _model_charts(question: str, candidates: list[tuple[dict[str, Any], tuple[str, ...]]],
                  model: PageChartModel | None) -> list[dict[str, Any]] | None:
    planner = getattr(model, "plan_page_charts", None)
    if not callable(planner) or not candidates:
        return None
    by_metric = {chart["metrics"][0]: (chart, types) for chart, types in candidates}
    catalog = [{"key": key, "title": chart["title"], "unit": chart["series"][0]["unit"],
                "dimension": chart["dimension"], "allowed_types": list(types),
                "categories": chart["categories"][:12]}
               for key, (chart, types) in by_metric.items()]
    try:
        raw = planner(question=question, catalog=catalog)
    except Exception:
        logger.warning("model page chart planning failed; skipping charts", exc_info=True)
        return None
    focus = raw.get("focus") if isinstance(raw, Mapping) else None
    if not isinstance(focus, str) or focus not in FOCI:
        return None
    items = raw.get("charts") if isinstance(raw, Mapping) else None
    if not isinstance(items, list):
        return None
    charts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items[:2]:
        if not isinstance(item, Mapping):
            continue
        key = item.get("metric")
        if not isinstance(key, str) or key not in by_metric or key in seen:
            continue
        if not relevant_metrics(focus, [key]) or not isinstance(item.get("reason"), str) or not item["reason"].strip():
            continue
        source, allowed = by_metric[key]
        kind = item.get("type")
        if kind not in allowed:
            continue
        chart = deepcopy(source)
        chart["type"] = kind
        chart["provenance"]["selection"] = {"method": "model", "focus": focus, "relevance_validated": True}
        if isinstance(raw.get("_model_request"), Mapping):
            chart["provenance"]["selection"]["model_request"] = dict(raw["_model_request"])
        if isinstance(item.get("reason"), str) and item["reason"].strip():
            chart["provenance"]["selection"]["reason"] = item["reason"].strip()[:300]
        charts.append(chart)
        seen.add(key)
    if items and not charts:
        return None
    return charts


def _travel_candidates(evidence: Mapping[str, Any]) -> list[tuple[dict[str, Any], tuple[str, ...]]]:
    candidates: list[tuple[dict[str, Any], tuple[str, ...]]] = []
    routes = [item for item in evidence.get("route_options") or [] if isinstance(item, Mapping)][:8]
    if routes:
        labels = [route.get("display_name") or route_display_name(str(route.get("route_id") or "")) or f"{route.get('mode_label') or '路线'} · 方案{index + 1}"
                  for index, route in enumerate(routes)]
        for field, title, unit in (
                ("duration_minutes", "候选路线所需时间对比", "分钟"),
                ("intake_index", "候选路线吸入当量对比", "EU"),
                ("score", "候选路线综合评分对比", "分")):
            values = [route.get(field) for route in routes]
            if any(isinstance(value, (int, float)) for value in values):
                candidates.append((_chart(title, "bar", "category", labels,
                    [{"name": title, "unit": unit, "data": values}],
                    f"travel.routes.{field}", unit, evidence), ("bar",)))
    curve = evidence.get("departure_dose_curve") or {}
    points = curve.get("points") or [] if isinstance(curve, Mapping) else []
    if points:
        candidates.append((_chart("不同出发时段的吸入当量", "line", "time",
            [str(point.get("time", ""))[11:16] for point in points],
            [{"name": "吸入当量", "unit": "EU",
              "data": [point.get("intake_index") for point in points]}],
            "travel.departure.intake_index", "EU", evidence), ("line", "area", "bar")))
    uncertainty = evidence.get("uncertainty") or {}
    if isinstance(uncertainty, Mapping):
        picks = [item for item in uncertainty.get("top_pick_distribution") or []
                 if isinstance(item, Mapping) and isinstance(item.get("share"), (int, float))]
        if picks:
            candidates.append((_chart("路线推荐稳健性", "bar", "category",
                [route_display_name(str(item.get("route_id") or "")) or str(item.get("mode_label") or "路线") for item in picks],
                [{"name": "扰动后入选比例", "unit": "比例",
                  "data": [item["share"] for item in picks]}],
                "travel.uncertainty.top_pick_share", "比例", evidence),
                ("bar", "pie", "donut")))
    severity = evidence.get("personalized_severity") or evidence.get("weather_severity") or {}
    if isinstance(severity, Mapping):
        labels = {"rain": "降雨", "heat": "高温", "cold": "低温", "humidity": "湿度",
                  "wind": "风", "air_quality": "空气质量"}
        factors = [(label, severity.get(key)) for key, label in labels.items()
                   if isinstance(severity.get(key), (int, float))]
        if factors:
            candidates.append((_chart("出行条件影响因素", "bar", "category",
                [label for label, _ in factors],
                [{"name": "影响评分", "unit": "评分", "data": [value for _, value in factors]}],
                "travel.conditions.severity", "评分", evidence), ("bar",)))
    return candidates


def travel_charts(question: str, evidence: Mapping[str, Any],
                  model: PageChartModel | None = None) -> list[dict[str, Any]]:
    return _model_charts(question, _travel_candidates(evidence), model) or []


def _environment_candidates(canonical: Mapping[str, Any]) -> list[tuple[dict[str, Any], tuple[str, ...]]]:
    agents = canonical.get("agent_results") or {}
    task = canonical.get("task") or {}
    evidence = {"source": "canonical_agent_results",
                "label": task.get("station_name") or task.get("station_id") or "当前站点"}
    weather = ((agents.get("MeteorologyAgent") or {}).get("result") or {})
    spatial = ((agents.get("SpatialAgent") or {}).get("result") or {})
    candidates: list[tuple[dict[str, Any], tuple[str, ...]]] = []
    steps = (weather.get("pollution_path_prediction") or {}).get("steps") or []
    survival_chart = _parcel_survival_chart(steps, evidence)
    if survival_chart:
        candidates.append((survival_chart, ("line", "area")))
    fields = [
        ("静风积聚", "static_wind_score"), ("风向变化", "wind_shift_score"),
        ("湿度", "humidity_risk_score"), ("降雨清除不足", "insufficient_rain_removal_score"),
    ]
    factors = [(label, weather.get(key)) for label, key in fields
               if isinstance(weather.get(key), (int, float))]
    if factors:
        candidates.append((_chart("气象风险因素对比", "bar", "category",
            [label for label, _ in factors],
            [{"name": "风险评分", "unit": "评分", "data": [value for _, value in factors]}],
            "weather.risk_factors", "评分", evidence, "MeteorologyAgent"), ("bar",)))
    roads = [road for road in spatial.get("upwind_roads") or []
             if isinstance(road, Mapping) and isinstance(road.get("match_score"), (int, float))][:10]
    if roads:
        candidates.append((_chart("上风向道路匹配评分", "bar", "category",
            [str(road.get("road_name_zh") or road.get("road_name") or "未命名道路") for road in roads],
            [{"name": "匹配评分", "unit": "评分",
              "data": [road["match_score"] for road in roads]}],
            "restriction.upwind_roads.match_score", "评分", evidence, "SpatialAgent"), ("bar",)))
    labels = {"school": "学校", "medical": "医疗", "healthcare": "医疗",
              "transport": "轨道交通", "rail_transit": "轨道交通",
              "community": "社区服务", "community_service": "社区服务",
              "community_centre": "社区中心"}
    counts = spatial.get("sensitive_poi_by_type") or {}
    if isinstance(counts, Mapping):
        pairs = [(labels.get(str(key), str(key)), value) for key, value in counts.items()
                 if isinstance(value, (int, float))]
        if pairs:
            candidates.append((_chart("敏感目标类别分布", "bar", "category",
                [label for label, _ in pairs],
                [{"name": "目标数量", "unit": "处", "data": [value for _, value in pairs]}],
                "restriction.sensitive_pois.by_type", "处", evidence, "SpatialAgent"),
                ("bar", "pie", "donut")))
    exposure = spatial.get("propagation_exposure") or {}
    forecast_counts = exposure.get("sensitive_poi_by_type") or {}
    if exposure.get("status") == "ok" and isinstance(forecast_counts, Mapping):
        forecast_pairs = [(labels.get(str(key), str(key)), value) for key, value in forecast_counts.items()
                          if isinstance(value, (int, float))]
        if forecast_pairs:
            chart = _chart("所选时段传播范围内敏感目标", "bar", "category",
                [label for label, _ in forecast_pairs],
                [{"name": "覆盖目标数量", "unit": "处", "data": [value for _, value in forecast_pairs]}],
                "weather.propagation.sensitive_pois.by_type", "处", evidence, "SpatialAgent")
            chart["warnings"].append("按模型传播范围筛选；空间覆盖不表示已观测到污染或健康风险。")
            candidates.append((chart, ("bar", "pie", "donut")))
    return candidates


def _parcel_survival_chart(steps: list[dict[str, Any]], evidence: Mapping[str, Any]) -> dict[str, Any] | None:
    labels = {"pm25": "PM2.5", "pm10": "PM10", "no2": "二氧化氮",
              "o3": "臭氧", "so2": "二氧化硫", "co": "一氧化碳"}
    names = list(dict.fromkeys(name for step in steps
                             for name in (step.get("remaining_by_pollutant") or {})))
    if not names:
        return None
    chart = _chart("各污染物气团剩余比例", "line", "time",
        [f"第{step.get('lead_hour')}小时" for step in steps],
        [{"name": labels.get(name, name), "unit": "%", "data": [
            round(value * 100, 2) if isinstance(value := (
                (step.get("remaining_by_pollutant") or {}).get(name) or {}).get("remaining_fraction"), (int, float))
            else None for step in steps]} for name in names],
        "weather.propagation.parcel_survival", "%", evidence, "MeteorologyAgent")
    return chart


def environment_page_charts(question: str, canonical: Mapping[str, Any],
                            page: str | None,
                            model: PageChartModel | None = None) -> list[dict[str, Any]]:
    """Explain weather/restriction page data that is absent from the generic catalog."""
    return _model_charts(question, _environment_candidates(canonical), model) or []


def environment_chart_candidates(canonical: Mapping[str, Any]) -> list[tuple[dict[str, Any], tuple[str, ...]]]:
    """Data-only candidates for the single combined chart selection call."""
    return _environment_candidates(canonical)
