"""Validate model decisions and structured context without text-triggered rules."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Mapping
from api.stations import lookup_station_coordinate


UserGoal = Literal[
    "analysis", "explanation", "visualization", "governance_advice",
    "public_travel_advice", "health_protection", "report", "capability_intro", "unknown",
]
Audience = Literal["citizen", "manager", "unknown"]

_ALLOWED_CONTEXT_FIELDS = (
    "task_id", "station_id", "station_name", "start_time", "end_time", "lat", "lon",
)
_SCOPE_FIELDS = ("station_id", "station_name", "start_time", "end_time", "lat", "lon")

# Actual EPD station names exposed by the target business service. These aliases
# are selectors only; environmental facts still come exclusively from the
# trusted CoordinatorMin endpoint.
_EPD_STATION_ALIASES = {
    "深水埗": "SHAM SHUI PO", "sham shui po": "SHAM SHUI PO",
    "中西区": "CENTRAL/WESTERN", "central/western": "CENTRAL/WESTERN",
    "东区": "EASTERN", "eastern": "EASTERN", "葵涌": "KWAI CHUNG", "kwai chung": "KWAI CHUNG",
    "观塘": "KWUN TONG", "kwun tong": "KWUN TONG", "北区": "NORTH", "north": "NORTH",
    "沙田": "SHATIN", "shatin": "SHATIN", "南区": "SOUTHERN", "southern": "SOUTHERN",
    "大埔": "TAI PO", "tai po": "TAI PO", "塔门": "TAP MUN", "tap mun": "TAP MUN",
    "将军澳": "TSEUNG KWAN O", "tseung kwan o": "TSEUNG KWAN O",
    "荃湾": "TSUEN WAN", "tsuen wan": "TSUEN WAN", "屯门": "TUEN MUN", "tuen mun": "TUEN MUN",
    "东涌": "TUNG CHUNG", "tung chung": "TUNG CHUNG", "元朗": "YUEN LONG", "yuen long": "YUEN LONG",
}


@dataclass(frozen=True, slots=True)
class GoalResolution:
    user_goal: UserGoal
    audience: Audience
    reason: str
    prefers_reuse: bool


class UserGoalResolver:
    """Validate a model-selected goal; never infer intent from user tokens."""

    def resolve(self, text: str, *, has_results: bool,
                model_plan: Mapping[str, Any] | None = None, **_unused: Any) -> GoalResolution:
        plan = model_plan or {}
        goal = plan.get("goal")
        allowed = {"analysis", "explanation", "visualization", "governance_advice",
                   "public_travel_advice", "health_protection", "report", "capability_intro", "unknown"}
        if not isinstance(goal, str) or goal not in allowed:
            raise ValueError("A valid model-selected goal is required")
        audience = "manager" if goal == "governance_advice" else (
            "citizen" if goal in {"public_travel_advice", "health_protection"} else "unknown")
        return GoalResolution(goal, audience, "model_selected_goal", has_results)


def requests_recommendation(text: str, user_goal: str) -> bool:
    """The goal is already selected by the model."""
    return user_goal in {"public_travel_advice", "health_protection", "governance_advice"}


@dataclass(frozen=True, slots=True)
class ContextResolution:
    task: dict[str, Any]
    updated_fields: tuple[str, ...]
    scope_changed: bool
    missing_fields: tuple[str, ...]
    errors: tuple[str, ...]


class TaskContextResolver:
    """Accept task selectors only; client evidence/results are always discarded."""

    def __init__(self, station_mapping_path: Path) -> None:
        self._aliases = self._load_aliases(station_mapping_path)

    @staticmethod
    def _load_aliases(path: Path) -> dict[str, str]:
        if not path.is_file():
            return dict(_EPD_STATION_ALIASES)
        value = json.loads(path.read_text(encoding="utf-8"))
        aliases: dict[str, str] = {}
        for canonical in (value.get("air_quality_to_atc") or {}):
            aliases[canonical.lower()] = canonical
            aliases[canonical.replace(" Roadside", "").lower()] = canonical
        for alias, canonical in (value.get("aliases") or {}).items():
            aliases[str(alias).lower()] = str(canonical)
        aliases.update(_EPD_STATION_ALIASES)
        return aliases

    def _canonical_station(self, value: Any) -> str:
        text = str(value or "").strip()
        canonical = self._aliases.get(text.lower(), text)
        return " ".join(
            canonical.upper().replace(" ROADSIDE", "").replace("_", " ").split()
        )

    def _same_station(self, left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        left_name = self._canonical_station(left.get("station_name"))
        right_name = self._canonical_station(right.get("station_name"))
        if left_name and right_name:
            return left_name == right_name
        left_id = self._canonical_station(left.get("station_id"))
        right_id = self._canonical_station(right.get("station_id"))
        return bool(left_id and right_id and left_id == right_id)

    def _scope_updates(
        self, before: Mapping[str, Any], task: Mapping[str, Any]
    ) -> tuple[str, ...]:
        updated: list[str] = []
        if not self._same_station(before, task):
            updated.append("station_name")
        for key in ("start_time", "end_time"):
            if task.get(key) != before.get(key):
                updated.append(key)
        if not before.get("station_name") and not task.get("station_name"):
            for key in ("lat", "lon"):
                if task.get(key) != before.get(key):
                    updated.append(key)
        return tuple(updated)

    def resolve(
        self,
        user_input: str,
        request_context: Mapping[str, Any] | None,
        previous: Mapping[str, Any] | None,
        *, model_updates: Mapping[str, Any] | None = None,
    ) -> ContextResolution:
        task = {key: value for key, value in (previous or {}).items() if key in _ALLOWED_CONTEXT_FIELDS}
        before = dict(task)
        errors: list[str] = []
        explicit = request_context if isinstance(request_context, Mapping) else {}
        explicit_station_name = explicit.get("station_name")
        if explicit_station_name is not None:
            normalized_station_name = str(explicit_station_name).strip()
            if normalized_station_name and not self._same_station(
                before, {**before, "station_name": normalized_station_name}
            ):
                for dependent in ("station_id", "lat", "lon"):
                    if dependent not in explicit:
                        task.pop(dependent, None)
        if (
            "station_id" in explicit
            and "station_name" not in explicit
            and str(explicit.get("station_id") or "").strip() != str(before.get("station_id") or "")
        ):
            errors.append("station_id change requires station_name")
        for key in _ALLOWED_CONTEXT_FIELDS:
            if key not in explicit:
                continue
            value = explicit[key]
            if key in {"lat", "lon"}:
                try:
                    value = float(value) if value is not None else None
                except (TypeError, ValueError):
                    errors.append(f"invalid {key}")
                    continue
            elif value is not None:
                value = str(value).strip()
            task[key] = value

        updates = {key: value for key, value in (model_updates or {}).items()
                   if key in {"station_name", "start_time", "end_time"}}
        if "station_name" in updates:
            selected = str(updates["station_name"] or "").strip()
            station = self._aliases.get(selected.lower())
            if selected and station is None:
                errors.append("model selected an unknown station")
            else:
                task["station_name"] = station
                if not self._same_station(before, {**before, "station_name": station}):
                    for key in ("station_id", "lat", "lon"):
                        task.pop(key, None)
        for key in ("start_time", "end_time"):
            if key in updates:
                task[key] = updates[key]
        if task.get("lat") is None or task.get("lon") is None:
            coordinate = lookup_station_coordinate(str(task.get("station_name") or ""),
                                                   str(task.get("station_id") or ""))
            if coordinate:
                task["lat"], task["lon"] = coordinate
        self._validate(task, errors)
        updated = self._scope_updates(before, task)
        missing = tuple(
            key for key in ("station_name", "start_time", "end_time")
            if not task.get(key)
        )
        return ContextResolution(
            task=task,
            updated_fields=updated,
            scope_changed=bool(previous) and bool(updated),
            missing_fields=missing,
            errors=tuple(errors),
        )

    def station_catalog(self) -> list[str]:
        return sorted(set(self._aliases.values()))

    @staticmethod
    def _validate(task: dict[str, Any], errors: list[str]) -> None:
        for key in ("start_time", "end_time"):
            value = task.get(key)
            if not value:
                continue
            try:
                datetime.strptime(str(value), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                errors.append(f"invalid {key}")
        if task.get("start_time") and task.get("end_time"):
            try:
                start = datetime.strptime(str(task["start_time"]), "%Y-%m-%d %H:%M:%S")
                end = datetime.strptime(str(task["end_time"]), "%Y-%m-%d %H:%M:%S")
                if end < start:
                    errors.append("invalid time range")
            except ValueError:
                pass
        lat, lon = task.get("lat"), task.get("lon")
        if lat is not None and not -90 <= float(lat) <= 90:
            errors.append("invalid lat")
        if lon is not None and not -180 <= float(lon) <= 180:
            errors.append("invalid lon")


@dataclass(frozen=True, slots=True)
class ContinuationDecision:
    action: Literal["run", "need_params", "reuse", "fallback"]
    reason: str
    missing_fields: tuple[str, ...] = ()
    invalidate_results: bool = False


class ContinuationResolver:
    """Apply data preconditions to the model's execution decision."""

    def resolve(self, goal: GoalResolution, context: ContextResolution, *,
                has_results: bool, user_input: str = "", model_action: str = "none",
                **_unused: Any) -> ContinuationDecision:
        if model_action == "none":
            return ContinuationDecision("fallback", "model_skipped_analysis", invalidate_results=context.scope_changed)
        if context.errors or context.missing_fields:
            return ContinuationDecision("need_params", "invalid_or_missing_parameters",
                                        context.missing_fields, context.scope_changed)
        if model_action == "reuse" and has_results and not context.scope_changed:
            return ContinuationDecision("reuse", "model_reused_analysis")
        if model_action in {"run", "reuse"}:
            return ContinuationDecision("run", "model_requested_analysis",
                                        invalidate_results=context.scope_changed)
        raise ValueError("Unsupported model execution decision")
