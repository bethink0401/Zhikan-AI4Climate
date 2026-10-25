"""Resolve browser page selectors to server-owned, compact travel evidence."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import requests
from .route_labels import route_display_name


def _travel_base(business_api_url: str) -> str:
    parsed = urlsplit(business_api_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Invalid business API URL")
    return f"{parsed.scheme}://{parsed.netloc}/api/travel"


def _select(items: list[Any], key: str, value: str) -> Mapping[str, Any]:
    for item in items:
        if isinstance(item, Mapping) and item.get(key) == value:
            return item
    raise ValueError("当前页面选项在服务端证据中不存在，请刷新页面后重试")


def _compact(result: Mapping[str, Any], *, label: str, source: str,
             selected_route_id: str = "") -> dict[str, Any]:
    routes = []
    for route in result.get("route_options") or []:
        if not isinstance(route, Mapping):
            continue
        routes.append({key: route.get(key) for key in (
            "route_id", "mode_label", "distance_km", "duration_minutes",
            "score", "intake_index", "rank", "recommended", "reason",
            "road_names", "junctions", "utility_minutes", "utility_detail",
            "intake_detail", "exposure", "transit_detail", "timeline",
        )})
        routes[-1]["display_name"] = route_display_name(str(route.get("route_id") or ""))
    if selected_route_id and not any(r["route_id"] == selected_route_id for r in routes):
        raise ValueError("当前选中的路线在服务端证据中不存在")
    advice = result.get("pollution_advice") or {}
    recommendation = result.get("recommendation") or {}
    recommended_id = recommendation.get("route_id") if isinstance(recommendation, Mapping) else None
    recommended_route = next((route for route in result.get("route_options") or []
                              if isinstance(route, Mapping) and route.get("route_id") == recommended_id), None)
    if not recommended_route:
        recommended_route = next((route for route in result.get("route_options") or []
                                  if isinstance(route, Mapping) and route.get("recommended")), None)
    return {
        "source": source, "page": "travel", "label": label,
        "origin": result.get("origin"), "destination": result.get("destination"),
        "straight_line_km": result.get("straight_line_km"),
        "start_time": result.get("start_time"), "end_time": result.get("end_time"),
        "travel_profile": result.get("travel_profile"),
        "travel_condition": result.get("travel_condition"),
        "weather_severity": result.get("weather_severity"),
        "personalized_severity": result.get("personalized_severity"),
        "weather_summary": result.get("weather_summary"),
        "selected_route_id": selected_route_id or None,
        "route_options": routes[:24],
        "recommendation": recommendation,
        "departure_advice": result.get("departure_advice"),
        "gear_reminders": result.get("gear_reminders"),
        "pollution_advice": {key: advice.get(key) for key in
            ("available", "headline", "hotspot", "avoidance", "cleanest_route_id",
             "cleanest_route_is_recommended", "notes")},
        "uncertainty": result.get("uncertainty"),
        "assumptions": result.get("assumptions"),
        "pollution_field": {key: value for key, value in
                            (result.get("pollution_field") or {}).items() if key != "grid"},
        "departure_dose_curve": result.get("departure_dose_curve"),
        "_route_map": {
            "title": "推荐路线地图",
            "routes": [dict(recommended_route)] if recommended_route else [],
            "origin": result.get("origin"), "destination": result.get("destination"),
            "selected_route_id": recommended_route.get("route_id") if recommended_route else "",
        } if recommended_route else None,
    }


def resolve_travel_evidence(page_context: Mapping[str, Any], business_api_url: str,
                            *, http: requests.Session | None = None) -> dict[str, Any]:
    """Accept only selectors; fetch actual values from the trusted business API."""
    if page_context.get("page") != "travel":
        raise ValueError("Invalid travel page")
    client = http or requests.Session()
    base = _travel_base(business_api_url)
    mode = page_context.get("mode")
    route_id = str(page_context.get("route_id") or "")[:100]
    if mode in {"case", "default"}:
        response = client.get(f"{base}/case", timeout=8)
        response.raise_for_status()
        data = response.json()
        if mode == "default":
            first_scenario = (data.get("scenarios") or [None])[0]
            first_case = (first_scenario.get("cases") or [None])[0] if isinstance(first_scenario, Mapping) else None
            if not isinstance(first_case, Mapping):
                raise ValueError("服务端没有可用的出行案例")
            profile = str(page_context.get("profile_id") or ("commuter" if "commuter" in (first_case.get("runs") or {}) else next(iter(first_case.get("runs") or {}), "")))
            selectors = [str(first_scenario["scenario_id"]), str(first_case["case_id"]), profile]
        else:
            selectors = [str(page_context.get(key) or "") for key in
                         ("scenario_id", "case_id", "profile_id")]
        if any(not value or len(value) > 100 for value in selectors):
            raise ValueError("请先选择出行场景、行程和画像")
        scenario = _select(data.get("scenarios") or [], "scenario_id", selectors[0])
        case = _select(scenario.get("cases") or [], "case_id", selectors[1])
        run = (case.get("runs") or {}).get(selectors[2])
        if not isinstance(run, Mapping) or not isinstance(run.get("result"), Mapping):
            raise ValueError("当前画像在服务端案例中不存在")
        label = f"{scenario.get('label')} · {case.get('label')}"
        if mode == "default":
            label = f"示例行程：{label}"
        compact = _compact(run["result"], label=label,
                        source="server_case_default" if mode == "default" else "server_case",
                        selected_route_id=route_id)
        compact["scenario"] = {key: scenario.get(key) for key in
                               ("scenario_id", "label", "note", "air_quality_risk",
                                "traffic_pressure_score")}
        compact["case"] = {key: case.get(key) for key in
                           ("case_id", "label", "direction_note")}
        compact["data_quality"] = run.get("data_quality")
        compact["agent_evidence"] = run.get("evidence")
        return compact
    if mode == "live":
        evidence_id = page_context.get("evidence_id")
        if not isinstance(evidence_id, str) or len(evidence_id) > 100 or not evidence_id:
            raise ValueError("请先完成实时路线规划")
        if any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in evidence_id):
            raise ValueError("Invalid evidence selector")
        response = client.get(f"{base}/plan-evidence/{evidence_id}", timeout=8)
        if response.status_code == 404:
            raise ValueError("实时规划证据已过期，请重新规划路线")
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, Mapping) or not isinstance(data.get("result"), Mapping):
            raise ValueError("实时规划证据不可用")
        origin = data["result"].get("origin") or {}
        destination = data["result"].get("destination") or {}
        origin_name = origin.get("name") if isinstance(origin, Mapping) else None
        destination_name = destination.get("name") if isinstance(destination, Mapping) else None
        label = (f"{origin_name} → {destination_name}"
                 if origin_name and destination_name else "当前实时路线规划")
        compact = _compact(data["result"], label=label, source="server_live_plan",
                           selected_route_id=route_id)
        compact["data_quality"] = data.get("data_quality")
        compact["agent_evidence"] = data.get("evidence")
        return compact
    raise ValueError("出行请求缺少有效规划模式；请明确输入“从起点到终点”，或刷新页面后重试。")
