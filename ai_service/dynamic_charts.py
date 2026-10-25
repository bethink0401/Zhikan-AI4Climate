"""Validated, model-planned conversation charts over canonical business data.

The model selects keys and presentation only. Every plotted number is extracted
from the trusted result and every transform is performed here.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ai_compat.visualization_catalog import VisualizationDataCatalog
from .visualization import CanonicalVisualizationDataProvider, VisualizationDataUnavailable
from .page_charts import environment_chart_candidates, REMOVED_CHART_METRICS
from .chart_relevance import FOCI, metric_meaning, relevant_metrics

logger = logging.getLogger(__name__)
HK = ZoneInfo("Asia/Hong_Kong")


class ChartPlanModel(Protocol):
    def plan_charts(self, *, question: str, catalog: list[dict[str, Any]],
                    previous: list[dict[str, Any]]) -> dict[str, Any]: ...


class DynamicChartPlanner:
    def __init__(self, model: ChartPlanModel | None = None) -> None:
        self.model = model
        self.catalog = VisualizationDataCatalog.load()
        self.provider = CanonicalVisualizationDataProvider(self.catalog)

    def plan(self, question: str, canonical: Mapping[str, Any] | None,
             previous: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        if not canonical or self.model is None:
            return []
        available: dict[str, dict[str, Any]] = {}
        for metric in self.catalog.available():
            if metric.data_source == "spatial":
                continue  # Existing map renderer handles spatial features.
            try:
                bundle = self.provider.extract(metric.key, canonical)
            except (VisualizationDataUnavailable, KeyError):
                continue
            if bundle["values"]:
                available[metric.key] = bundle
        page_candidates = {chart["metrics"][0]: (chart, types)
                           for chart, types in environment_chart_candidates(canonical)
                           if chart["metrics"][0] not in REMOVED_CHART_METRICS}
        if not available and not page_candidates:
            return []
        catalog_prompt = [{
            "key": key, "unit": bundle["unit"],
            "dimension": self.catalog.require(key).dimension,
            "semantic_type": bundle["semantic_type"],
            "source": bundle["source_agent"],
        } for key, bundle in available.items()]
        catalog_prompt.extend({
            "key": key, "title": chart["title"], "unit": chart["series"][0]["unit"],
            "dimension": chart["dimension"], "allowed_types": list(types),
            "categories": chart["categories"][:12], "source": chart["provenance"]["metrics"][0]["source_agent"],
            "supports_time_transform": False,
        } for key, (chart, types) in page_candidates.items())
        for entry in catalog_prompt:
            entry["meaning"] = metric_meaning(entry["key"])
        prior = [chart for chart in list(previous or [])[-10:]
                 if not REMOVED_CHART_METRICS.intersection(chart.get("metrics") or [])]
        prior_prompt = [{key: chart.get(key) for key in
                         ("id", "title", "type", "metrics", "transform")}
                        for chart in prior]
        raw: dict[str, Any] = {"charts": []}
        if self.model:
            try:
                raw = self.model.plan_charts(question=question, catalog=catalog_prompt,
                                             previous=prior_prompt)
            except Exception:
                logger.exception("dynamic chart planning failed")
        focus = raw.get("focus") if isinstance(raw, Mapping) else None
        if not isinstance(focus, str) or focus not in FOCI:
            logger.warning("chart selection rejected: missing or invalid semantic focus")
            return []
        items = raw.get("charts") if isinstance(raw, Mapping) else None
        if not isinstance(items, list):
            items = []
        output: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_metrics: set[tuple[str, ...]] = set()
        for item in items[:2]:
            if not isinstance(item, Mapping):
                continue
            try:
                existing = next((chart for chart in prior if chart.get("id") == item.get("id")), {})
                keys = item.get("metrics", existing.get("metrics"))
                reason = item.get("reason")
                if (not isinstance(keys, list) or not all(isinstance(key, str) for key in keys)
                        or not relevant_metrics(focus, keys) or not isinstance(reason, str) or not reason.strip()):
                    logger.info("chart rejected: focus=%s metrics=%s reason=semantic_mismatch", focus, keys)
                    continue
                if isinstance(keys, list) and len(keys) == 1 and isinstance(keys[0], str) and keys[0] in page_candidates:
                    source, allowed = page_candidates[keys[0]]
                    if item.get("type", existing.get("type")) not in allowed:
                        continue
                    if any(item.get(key, default) != default for key, default in (
                            ("time_filter", "all"), ("bucket_hours", 1), ("start_hour", 0), ("end_hour", 24))):
                        continue
                    chart = deepcopy(source)
                    chart["type"] = item.get("type", existing.get("type"))
                    title = item.get("title")
                    if isinstance(title, str) and title.strip():
                        chart["title"] = title.strip()[:80]
                else:
                    chart = self._build(item, available, prior + output, canonical)
            except (ValueError, TypeError, KeyError):
                continue
            metric_signature = tuple(sorted(chart["metrics"]))
            if chart["id"] in seen_ids or metric_signature in seen_metrics:
                continue
            seen_ids.add(chart["id"])
            seen_metrics.add(metric_signature)
            chart["provenance"]["selection"] = {"method": "model", "focus": focus,
                                                  "relevance_validated": True}
            if isinstance(raw.get("_model_request"), Mapping):
                chart["provenance"]["selection"]["model_request"] = dict(raw["_model_request"])
            if isinstance(item.get("reason"), str) and item["reason"].strip():
                chart["provenance"]["selection"]["reason"] = item["reason"].strip()[:300]
            output.append(chart)
        logger.info("combined chart selection: count=%s metrics=%s", len(output),
                    [chart["metrics"] for chart in output])
        return output

    def _build(self, item: Mapping[str, Any], available: Mapping[str, dict[str, Any]],
               previous: list[dict[str, Any]], canonical: Mapping[str, Any]) -> dict[str, Any]:
        raw_id = item.get("id", "new")
        existing = next((chart for chart in previous if chart.get("id") == raw_id), None)
        previous_transform = existing.get("transform", {}) if existing else {}
        chart_type = item.get("type", existing.get("type") if existing else None)
        if chart_type not in {"line", "area", "bar", "scatter", "pie", "donut"}:
            raise ValueError("unsupported chart type")
        keys = item.get("metrics", existing.get("metrics") if existing else None)
        if not isinstance(keys, list) or not 1 <= len(keys) <= 5 or any(
            not isinstance(key, str) or key not in available for key in keys
        ) or len(set(keys)) != len(keys):
            raise ValueError("invalid metrics")
        dimensions = {self.catalog.require(key).dimension for key in keys}
        if len(dimensions) != 1:
            raise ValueError("incompatible dimensions")
        dimension = dimensions.pop()
        if chart_type == "scatter" and (dimension != "time" or len(keys) != 2):
            raise ValueError("scatter requires two time series")
        if chart_type in {"pie", "donut"} and (dimension == "time" or len(keys) != 1):
            raise ValueError("pie requires a category metric")
        if chart_type in {"pie", "donut"} and keys[0] != "traffic.vehicle_composition":
            raise ValueError("only composition shares support part-to-whole charts")
        if dimension == "time" and chart_type in {"pie", "donut"}:
            raise ValueError("time series is not a part-to-whole chart")
        time_filter = item.get("time_filter", previous_transform.get("time_filter", "all"))
        if time_filter not in {"all", "morning", "afternoon"}:
            raise ValueError("unsupported time filter")
        bucket = item.get("bucket_hours", previous_transform.get("bucket_hours", 1))
        if isinstance(bucket, bool) or not isinstance(bucket, int) or bucket not in {1, 2, 3, 4, 6, 12, 24}:
            raise ValueError("unsupported aggregation")
        if bucket != 1 and any(available[key]["semantic_type"] == "circular_direction" for key in keys):
            raise ValueError("circular wind direction cannot be arithmetically averaged")
        start_hour = item.get("start_hour", previous_transform.get("start_hour", 0))
        end_hour = item.get("end_hour", previous_transform.get("end_hour", 24))
        if (isinstance(start_hour, bool) or isinstance(end_hour, bool)
                or not isinstance(start_hour, int) or not isinstance(end_hour, int)
                or not 0 <= start_hour < end_hour <= 24):
            raise ValueError("invalid hour range")
        if dimension != "time" and (time_filter != "all" or bucket != 1
                                    or start_hour != 0 or end_hour != 24):
            raise ValueError("time transform on non-time data")
        prior_ids = {chart.get("id") for chart in previous}
        chart_id = raw_id if isinstance(raw_id, str) and raw_id in prior_ids else ""
        if not chart_id:
            existing_numbers = [int(str(chart.get("id", "")).split("-")[-1])
                                for chart in previous
                                if str(chart.get("id", "")).startswith("chart-")
                                and str(chart.get("id", "")).split("-")[-1].isdigit()]
            chart_id = f"chart-{max(existing_numbers, default=0) + 1}"
        title = item.get("title", existing.get("title") if existing else None)
        title = title.strip()[:80] if isinstance(title, str) else ""
        if not title:
            title = "数据图表"
        bundles = [available[key] for key in keys]
        warnings: list[str] = []
        if "meteorology.hourly.wind_direction" in keys:
            warnings.append("风向是循环角度，图中未做连续插值或算术平均。")
        if "traffic.vehicle_composition" in keys:
            warnings.append("车型比例来自 ATC 参考期估计，不代表当前时段的实测道路流量。")
        if dimension == "time":
            series, categories = self._time_data(bundles, time_filter, bucket,
                                                 start_hour, end_hour)
            if chart_type == "scatter":
                left, right = series
                points = [[x, y] for x, y in zip(left["data"], right["data"])
                          if isinstance(x, (int, float)) and isinstance(y, (int, float))]
                if not points:
                    raise ValueError("no aligned scatter pairs")
                series = [{"name": f"{left['name']} / {right['name']}", "data": points}]
                categories = []
        else:
            if len(bundles) != 1:
                raise ValueError("multi-source categories require an explicit join")
            bundle = bundles[0]
            values = bundle["values"]
            categories = [str(row.get("category", "")) for row in values]
            numbers = [self._numeric(row.get("value", row.get("avg"))) for row in values]
            if not any(value is not None for value in numbers):
                raise ValueError("no numeric category data")
            display_unit = bundle["unit"]
            if keys[0] == "air_quality.pollutant_profile":
                micro = [number if row.get("unit") == "μg/m³" else None
                         for row, number in zip(values, numbers)]
                co = [number if row.get("unit") == "mg/m³" else None
                      for row, number in zip(values, numbers)]
                series = [{"name": "其他污染物", "unit": "μg/m³", "data": micro}]
                if any(value is not None for value in co):
                    series.append({"name": "一氧化碳（CO）", "unit": "mg/m³", "data": co})
                    warnings.append("CO 与其他污染物单位不同，分别使用左右刻度；柱高不可跨刻度直接比较。")
            else:
                series = [{"name": keys[0], "unit": display_unit, "data": numbers}]
            if bundle["semantic_type"] == "independent_source_screening_score":
                if chart_type in {"pie", "donut"}:
                    raise ValueError("screening scores are not shares")
                warnings.append("来源评分为独立筛查评分，不代表污染贡献率。")
        task = canonical.get("task") if isinstance(canonical.get("task"), Mapping) else {}
        return {
            "id": chart_id, "type": chart_type, "title": title,
            "metrics": keys, "dimension": dimension, "categories": categories,
            "series": series, "warnings": warnings,
            "transform": {"time_filter": time_filter, "bucket_hours": bucket,
                          "start_hour": start_hour, "end_hour": end_hour},
            "provenance": {
                "provider": "canonical_agent_results", "timezone": "Asia/Hong_Kong",
                "task": {key: task.get(key) for key in
                         ("station_id", "station_name", "start_time", "end_time")
                         if key in task},
                "metrics": [{"key": bundle["metric"], "unit": bundle["unit"],
                             "field_path": bundle["field_path"],
                             "source_agent": bundle["source_agent"]}
                            for bundle in bundles],
            },
        }

    @staticmethod
    def _numeric(value: Any) -> float | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            return float(value)
        return None

    @staticmethod
    def _local_time(value: Any) -> datetime:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=HK) if stamp.tzinfo is None else stamp.astimezone(HK)

    def _time_data(self, bundles: list[dict[str, Any]], time_filter: str,
                   bucket: int, start_hour: int, end_hour: int) -> tuple[list[dict[str, Any]], list[str]]:
        mapped: list[dict[str, float]] = []
        for bundle in bundles:
            values: dict[str, float] = {}
            for row in bundle["values"]:
                try:
                    timestamp = self._local_time(row["time"])
                except (KeyError, TypeError, ValueError):
                    continue
                if time_filter == "morning" and timestamp.hour >= 12:
                    continue
                if time_filter == "afternoon" and timestamp.hour < 12:
                    continue
                if not start_hour <= timestamp.hour < end_hour:
                    continue
                number = self._numeric(row.get("value"))
                if number is None:
                    continue
                if bucket > 1:
                    timestamp = timestamp.replace(hour=(timestamp.hour // bucket) * bucket,
                                                  minute=0, second=0, microsecond=0)
                label = timestamp.strftime("%Y-%m-%d %H:%M")
                values[label] = number
            mapped.append(values)
        categories = sorted(set().union(*(set(values) for values in mapped)))[:500]
        if not categories:
            raise ValueError("no time data")
        series: list[dict[str, Any]] = []
        for bundle, values in zip(bundles, mapped):
            if bucket > 1:
                groups: dict[str, list[float]] = defaultdict(list)
                for row in bundle["values"]:
                    try:
                        stamp = self._local_time(row["time"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if time_filter == "morning" and stamp.hour >= 12:
                        continue
                    if time_filter == "afternoon" and stamp.hour < 12:
                        continue
                    if not start_hour <= stamp.hour < end_hour:
                        continue
                    number = self._numeric(row.get("value"))
                    if number is not None:
                        label = stamp.replace(hour=(stamp.hour // bucket) * bucket,
                                              minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
                        groups[label].append(number)
                values = {label: sum(nums) / len(nums) for label, nums in groups.items()}
            series.append({"name": bundle["metric"], "unit": bundle["unit"],
                           "data": [values.get(label) for label in categories]})
        if not any(any(value is not None for value in row["data"]) for row in series):
            raise ValueError("empty time series")
        return series, categories
