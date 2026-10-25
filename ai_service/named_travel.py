"""Resolve named trip requests against the business API's place directory."""

from __future__ import annotations

import re
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import requests


class PlaceResolutionError(ValueError):
    def __init__(self, message: str, *, role: str | None = None,
                 candidates: list[str] | None = None) -> None:
        super().__init__(message)
        self.role = role
        self.candidates = candidates or []


logger = logging.getLogger(__name__)


class TravelIntentModel(Protocol):
    def extract_trip(self, question: str) -> tuple[str, str] | None: ...


def _base(business_api_url: str) -> str:
    parsed = urlsplit(business_api_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Invalid business API URL")
    return f"{parsed.scheme}://{parsed.netloc}/api/travel"


def _place(client: requests.Session, base: str, name: str, role: str) -> dict[str, Any]:
    response = client.get(f"{base}/places", params={"q": name}, timeout=10)
    response.raise_for_status()
    places = response.json().get("places") or []
    if not places:
        raise PlaceResolutionError(f"没有找到{role}“{name}”。请换一个地点名称，或在出行页地图上选点。", role=role)
    # A station's short name often also prefixes clinics and monitoring sites.
    # Prefer the uniquely named rail stop, including its usual omitted "站" suffix.
    def station_key(value: str) -> str:
        value = value.strip().replace("港铁", "").replace("港鐵", "")
        value = value.replace("奥", "奧").replace("运", "運").replace("东", "東")
        return value.removesuffix("站")

    rail = [item for item in places if item.get("kind") == "rail"
            and station_key(str(item.get("name") or "")) == station_key(name)]
    if len(rail) == 1:
        return rail[0]
    exact = [item for item in places if item.get("match") == 0]
    candidates = exact or places
    if len(candidates) == 1:
        return candidates[0]
    first = candidates[0]
    same_place = all(abs(item["lat"] - first["lat"]) < 0.005
                     and abs(item["lon"] - first["lon"]) < 0.005 for item in candidates)
    if same_place:
        return first
    choices = [f"{item['name']}（{item.get('detail') or item['kind']}）"
               for item in candidates[:5]]
    raise PlaceResolutionError(
        f"{role}“{name}”对应多个地点：{'；'.join(choices)}。请选择其中一个地点。",
        role=role, candidates=choices)


def _time_window(page_context: Mapping[str, Any] | None,
                 request_context: Mapping[str, Any] | None) -> tuple[str, str]:
    page_context = page_context or {}
    request_context = request_context or {}
    date = str(page_context.get("depart_date") or "")
    start = str(page_context.get("depart_start") or "")
    end = str(page_context.get("depart_end") or "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) and re.fullmatch(r"\d{2}:\d{2}", start) and re.fullmatch(r"\d{2}:\d{2}", end):
        window = (f"{date} {start}:00", f"{date} {end}:00")
    elif request_context.get("start_time") and request_context.get("end_time"):
        window = (str(request_context["start_time"]), str(request_context["end_time"]))
    else:
        window = ("2023-08-15 08:00:00", "2023-08-15 11:00:00")
    try:
        if datetime.fromisoformat(window[0]) >= datetime.fromisoformat(window[1]):
            raise ValueError
    except ValueError as exc:
        raise PlaceResolutionError("所选出行时段无效，请先在页面上重新选择日期和时间。") from exc
    return window


def plan_named_trip(names: tuple[str, str], business_api_url: str,
                    page_context: Mapping[str, Any] | None,
                    request_context: Mapping[str, Any] | None,
                    *, http: requests.Session | None = None) -> str:
    client = http or requests.Session()
    base = _base(business_api_url)
    origin = _place(client, base, names[0], "起点")
    destination = _place(client, base, names[1], "终点")
    start_time, end_time = _time_window(page_context, request_context)
    profile = str((page_context or {}).get("profile_id") or "commuter")[:50]
    payload = {
        "task_id": f"TRAVEL_CHAT_{uuid4().hex[:12]}",
        "origin": {key: origin[key] for key in ("name", "lat", "lon")},
        "destination": {key: destination[key] for key in ("name", "lat", "lon")},
        "start_time": start_time, "end_time": end_time,
        "travel_profile": profile,
    }
    response = client.post(f"{base}/plan", json=payload, timeout=125)
    response.raise_for_status()
    value = response.json()
    evidence_id = value.get("evidence_id")
    if not isinstance(evidence_id, str) or not evidence_id:
        raise ValueError("路线规划未返回可核验的结果")
    return evidence_id


def replan_travel_evidence(evidence: Mapping[str, Any], profile_id: str,
                           business_api_url: str, *, http: requests.Session | None = None) -> str:
    """Recompute a trusted trip when its requested audience changes."""
    client = http or requests.Session()
    payload = {key: evidence.get(key) for key in
               ("origin", "destination", "start_time", "end_time")}
    if not all(payload.values()):
        raise PlaceResolutionError("请补充出行起点、终点和时段，以便按你的身份重新规划。")
    payload.update(task_id=f"TRAVEL_CHAT_{uuid4().hex[:12]}", travel_profile=profile_id)
    response = client.post(f"{_base(business_api_url)}/plan", json=payload, timeout=125)
    response.raise_for_status()
    evidence_id = response.json().get("evidence_id")
    if not isinstance(evidence_id, str) or not evidence_id:
        raise ValueError("路线规划未返回可核验的结果")
    return evidence_id
