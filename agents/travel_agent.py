# -*- coding: utf-8 -*-
"""TravelAgent —— 出行建议与路线推荐。

输入：起点、终点、时间窗口、用户出行习惯画像。
结合：真实路网（香港道路中心线）、真实气象（MeteorologyAgent 输出）、
      污染事件信号（AirQualityAgent 输出或显式传入的风险值）。
输出：按该用户习惯排序的路线方案、出发时段建议、装备提醒与风险提示。

职责边界
--------
本 Agent 产出的是**筛查级、可解释**的比较结果：它把气象与污染条件按用户
敏感度换算成路线扣分项，用于出行前快速比选；不等同于实时路况导航，
也不代表官方交通或健康通告。公共交通部分使用由车站点位近似重建的轨道
网络，已在输出的 assumptions 中显式声明。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spatial_agent.tools.spatial_tools import haversine_km  # noqa: E402
from tools.pollution_field import (  # noqa: E402
    avoidance_multipliers,
    build_field_from_sources,
    load_pollution_rules,
)
from tools.parcel_lifetime import build_lifetime_model, parcel_initial_conditions  # noqa: E402
from tools.route_tools import (  # noqa: E402
    build_rail_links,
    get_road_graph,
    load_rail_stations,
    make_rail_segment,
    merge_route_options,
    nearest_rail_station,
    path_exposure,
    search_rail_route,
    search_route_alternatives,
    stations_by_name,
    straight_line_km,
)
from tools.travel_advice_tools import (  # noqa: E402
    MODE_LABELS,
    build_departure_advice,
    build_gear_reminders,
    build_pollution_advice,
    build_route_reason,
    classify_weather,
    effective_speed,
    evaluate_option,
    extract_weather_summary,
    overall_condition,
    personalized_severity,
    rail_minutes,
    resolve_profile,
    road_minutes,
    select_recommendation,
    time_model_config,
    wind_note,
)
from tools.travel_advice_tools import load_travel_config  # noqa: E402

TZ = ZoneInfo("Asia/Hong_Kong")

#: 每个出行方式最多给出的备选路线数（常规与避污各取这些条后合并）。
ROUTE_ALTERNATIVES_PER_MODE = 2

#: 未提供污染信号时，空气质量因子按"无可用信息"处理。
DEFAULT_POLLUTION_RISK = None

#: 出行建议中最多返回的方案数。要保证"最脏"的基准路线也在列表里，
#: 否则无法和避污路线做对比。
MAX_ROUTE_OPTIONS = 12


def _now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def _hour_of(time_text: Any) -> int | None:
    """从气象逐小时序列的时间串里取出小时，用于判断是否高峰时段。"""
    if not isinstance(time_text, str) or not time_text.strip():
        return None
    text = time_text.strip().replace("T", " ")
    for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len(pattern) + 2].strip(), pattern).hour
        except ValueError:
            continue
    return None


def _short_time(time_text: Any) -> str:
    """把 '2023-08-15 09:00' 压成 '09:00' 用于展示。"""
    if not isinstance(time_text, str):
        return str(time_text)
    text = time_text.strip().replace("T", " ")
    return text.split(" ")[-1][:5] if " " in text else text[:5]


def _normalise_point(value: Any, label: str) -> dict[str, Any]:
    """Validate and normalise an origin/destination specification."""
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是包含 name/lat/lon 的对象")
    if value.get("lat") is None or value.get("lon") is None:
        raise ValueError(f"{label} 缺少 lat 或 lon")
    lat, lon = float(value["lat"]), float(value["lon"])
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise ValueError(f"{label} 坐标超出合法经纬度范围")
    return {
        "name": str(value.get("name") or label),
        "lat": round(lat, 6),
        "lon": round(lon, 6),
    }


def _parse_hour(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, pattern)
            return parsed.replace(tzinfo=TZ)
        except ValueError:
            continue
    return None


def _normalise_sources(task: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect pollution source specifications from the task.

    Accepts a ``pollution_sources`` list (multiple events), a single
    ``pollution_source`` object, or a bare station_id/lat/lon on the task.
    """
    items: list[dict[str, Any]] = []
    listed = task.get("pollution_sources")
    if isinstance(listed, list):
        items.extend(item for item in listed if isinstance(item, dict))

    single = task.get("pollution_source")
    if isinstance(single, dict):
        items.append(single)
    elif single is None and task.get("lat") is not None and task.get("lon") is not None:
        items.append(
            {
                "lat": task.get("lat"),
                "lon": task.get("lon"),
                "station_id": task.get("station_id"),
                "name": task.get("station_name"),
            }
        )
    return [
        dict(item)
        for item in items
        if item.get("lat") is not None and item.get("lon") is not None
    ]


def validate_task(task: dict[str, Any]) -> list[str]:
    """Return a list of validation errors. Empty list means the task is usable."""
    errors: list[str] = []
    if not isinstance(task, dict):
        return ["task 必须是对象"]
    if not task.get("task_id"):
        errors.append("缺少 task_id")
    for label, key in (("origin", "origin"), ("destination", "destination")):
        try:
            _normalise_point(task.get(key), label)
        except ValueError as exc:
            errors.append(str(exc))

    start = _parse_hour(task.get("start_time"))
    end = _parse_hour(task.get("end_time"))
    if start is None:
        errors.append("start_time 无法解析为合法时间")
    if end is None:
        errors.append("end_time 无法解析为合法时间")
    if start is not None and end is not None and end <= start:
        errors.append("end_time 必须晚于 start_time")
    return errors


class TravelAgent:
    """结合气象条件与用户出行习惯，输出路线方案与出行建议。"""

    agent_name = "TravelAgent"

    def __init__(self, config_path: str | Path | None = None):
        self.config = load_travel_config(config_path) if config_path else load_travel_config()
        self._road_graph = None
        self._rail_stations = None
        self._rail_links = None

    # ── 惰性加载的重资产 ────────────────────────────────────────────────
    @property
    def road_graph(self):
        if self._road_graph is None:
            self._road_graph = get_road_graph()
        return self._road_graph

    @property
    def rail_stations(self) -> list[dict[str, Any]]:
        if self._rail_stations is None:
            self._rail_stations = load_rail_stations()
        return self._rail_stations

    @property
    def rail_links(self) -> dict[str, list[tuple[str, float]]]:
        if self._rail_links is None:
            self._rail_links = build_rail_links(self.rail_stations)
        return self._rail_links

    # ── 路线候选 ────────────────────────────────────────────────────────
    def _road_route_options(
        self,
        mode: str,
        origin: tuple[float, float],
        destination: tuple[float, float],
        *,
        edge_exposure: list[float] | None = None,
        avoidance: list[float] | None = None,
        high_threshold: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Route candidates for one mode: the plain options plus pollution-aware ones.

        The pollution-aware search uses a super-linear exposure penalty, so it can
        return a genuinely different — longer but much cleaner — street rather
        than merely re-ranking the same geometry.
        """
        normal = search_route_alternatives(
            self.road_graph,
            origin,
            destination,
            mode=mode,
            max_routes=ROUTE_ALTERNATIVES_PER_MODE,
            edge_exposure=edge_exposure,
            high_threshold=high_threshold,
        )
        if not avoidance:
            return normal

        clean = search_route_alternatives(
            self.road_graph,
            origin,
            destination,
            mode=mode,
            max_routes=ROUTE_ALTERNATIVES_PER_MODE,
            edge_exposure=edge_exposure,
            edge_multiplier=avoidance,
            high_threshold=high_threshold,
        )
        return merge_route_options(normal, clean)

    def _walk_link(
        self,
        from_xy: tuple[float, float],
        to_station: dict[str, Any],
        *,
        edge_exposure: list[float] | None = None,
        high_threshold: float = 0.5,
    ) -> dict[str, Any] | None:
        """Walk route between a point and a station.

        When the traveller already stands at the station the road search returns
        an empty path, so a zero-length link is built instead of dropping the
        whole public-transport option.
        """
        routes = self._road_route_options(
            "walk",
            from_xy,
            (to_station["lon"], to_station["lat"]),
            edge_exposure=edge_exposure,
            high_threshold=high_threshold,
        )
        if routes:
            return routes[0]

        distance = straight_line_km(from_xy, (to_station["lon"], to_station["lat"]))
        if distance > 0.08:
            return None
        return {
            "mode": "walk",
            "alternative_index": 0,
            "edge_indices": [],
            "distance_km": round(distance, 3),
            "geometry": [[from_xy[0], from_xy[1]], [to_station["lon"], to_station["lat"]]],
            "road_names": [],
            "junctions": 0,
            "exposure": path_exposure(
                self.road_graph, [], edge_exposure=edge_exposure, high_threshold=high_threshold
            ),
        }

    def _transit_option(
        self,
        origin: dict[str, Any],
        destination: dict[str, Any],
        *,
        edge_exposure: list[float] | None = None,
        field: Any = None,
        high_threshold: float = 0.5,
    ) -> dict[str, Any] | None:
        """Build a 步行接驳 + 轨道 + 步行接驳 option, or None when unavailable."""
        max_access_km = float(time_model_config(self.config)["max_transit_access_km"])
        boarding, access_km = nearest_rail_station(
            self.rail_stations, origin["lon"], origin["lat"]
        )
        alighting, egress_km = nearest_rail_station(
            self.rail_stations, destination["lon"], destination["lat"]
        )
        if boarding is None or alighting is None:
            return None
        if boarding["name"] == alighting["name"]:
            return None
        if access_km > max_access_km or egress_km > max_access_km:
            return None

        sequence = search_rail_route(self.rail_links, boarding["name"], alighting["name"])
        if not sequence or len(sequence) < 2:
            return None
        ordered = stations_by_name(self.rail_stations, sequence)
        if len(ordered) < 2:
            return None

        rail = make_rail_segment(ordered)

        access = self._walk_link(
            (origin["lon"], origin["lat"]), boarding,
            edge_exposure=edge_exposure, high_threshold=high_threshold,
        )
        egress = self._walk_link(
            (destination["lon"], destination["lat"]), alighting,
            edge_exposure=edge_exposure, high_threshold=high_threshold,
        )
        if access is None or egress is None:
            return None

        # 暴露度只统计步行接驳段：轨道区间基本隔绝，不在受监测道路上。
        combined = path_exposure(
            self.road_graph,
            access["edge_indices"] + egress["edge_indices"],
            edge_exposure=edge_exposure,
            high_threshold=high_threshold,
        )
        # 候车与进出站仍在户外。这里只**记录**候车暴露，不再折算成"等效里程"
        # 混进路线剂量——吸入当量由分段模型计算（见 tools/travel_advice_tools.py），
        # 候车段有自己的呼吸速率与穿透系数，混进主剂量会重复计数。
        # 上下车站取暴露较高的一侧：从干净区坐进污染区，下车出站同样要暴露。
        wait_index = 0.0
        if field is not None and edge_exposure is not None:
            wait_index = max(
                float(field.index_at(boarding["lon"], boarding["lat"])),
                float(field.index_at(alighting["lon"], alighting["lat"])),
            )

        rail_metrics = time_model_config(self.config)["rail"]

        geometry = list(access["geometry"]) + list(rail["geometry"])[1:] + list(egress["geometry"])[1:]

        return {
            "mode": "transit",
            "alternative_index": 0,
            "distance_km": round(access["distance_km"] + rail["distance_km"] + egress["distance_km"], 3),
            "geometry": geometry,
            "road_names": [station["name"] for station in ordered],
            "junctions": access["junctions"] + egress["junctions"],
            "exposure": combined,
            "transit_detail": {
                "boarding_station": boarding["name"],
                "alighting_station": alighting["name"],
                "stations": [station["name"] for station in ordered],
                "stop_count": len(ordered) - 1,
                "rail_distance_km": rail["distance_km"],
                "access_distance_km": access["distance_km"],
                "egress_distance_km": egress["distance_km"],
                "access_junctions": access["junctions"],
                "egress_junctions": egress["junctions"],
                "access_mean_index": float(access["exposure"].get("mean_index") or 0.0),
                "egress_mean_index": float(egress["exposure"].get("mean_index") or 0.0),
                "access_edge_indices": list(access["edge_indices"]),
                "egress_edge_indices": list(egress["edge_indices"]),
                "station_points": [
                    [float(boarding["lon"]), float(boarding["lat"])],
                    [float(alighting["lon"]), float(alighting["lat"])],
                ],
                "access_road_names": access["road_names"][:4],
                "egress_road_names": egress["road_names"][:4],
                "wait_index": round(wait_index, 4),
                "platform_access_minutes": float(rail_metrics["platform_access_minutes"]),
                "wait_minutes": float(rail_metrics["wait_minutes"]),
            },
        }

    def _base_speed(
        self, mode: str, profile: dict[str, Any], scaled: dict[str, Any]
    ) -> dict[str, Any]:
        """Planning speed for one mode, after the profile's own pace and the weather."""
        base = float(self.config["mode_speeds"][mode])
        if mode == "walk":
            # 同一段路，跑步爱好者步速快于平均，长者与带娃家长更慢。
            base *= float(profile.get("walk_speed_factor", 1.0))
        return effective_speed(base, scaled, self.config)

    # ── 分段与耗时 ──────────────────────────────────────────────────────
    def _connector(self, field: Any, point: dict[str, Any], snap_km: float) -> dict[str, Any] | None:
        """起终点到路网节点的直线接驳段。

        吸附距离此前只被记进 origin_snap_km 就丢掉了：既不进里程、不进耗时、
        也不进吸入当量。用户在地图上自选点位时这段可能有几百米，不能不计。
        """
        if snap_km is None or snap_km <= 0.01:
            return None
        point_xy = (float(point["lon"]), float(point["lat"]))
        mean_index = (
            float(field.index_at(point_xy[0], point_xy[1])) if field is not None else 0.0
        )
        return {
            "distance_km": round(float(snap_km), 3),
            "mean_index": mean_index,
            "points": [point_xy],
            "label": f"{point.get('name') or '起终点'}接驳",
        }

    def _segments_for(
        self,
        mode: str,
        route: dict[str, Any],
        *,
        speed_kmh: float,
        hour: int | None,
        connectors: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
        """把一个候选方案拆成分段，算出各段耗时，并留下可重采样的引用。

        分段是吸入当量与户外等效时间的计算单位。公共交通不是"整段都在户外"：
        它只有接驳与候车段暴露，区间内基本隔绝，因此四种方式的吸入量才可比。

        第三个返回值 ``refs`` 记录每段由哪些路段或点位组成。换一个暴露场
        （不同风速、不同出发时刻）时可以直接重采样，不必重跑路径搜索。
        """
        segments: list[dict[str, Any]] = []
        refs: list[dict[str, Any]] = []
        timeline: dict[str, Any] = {"connector_minutes": 0.0}

        for connector in connectors:
            minutes = float(connector["distance_km"]) / max(speed_kmh, 0.5) * 60.0
            segments.append(
                {
                    "kind": "walk",
                    "minutes": minutes,
                    "mean_index": float(connector.get("mean_index") or 0.0),
                }
            )
            refs.append(
                {
                    "kind": "walk",
                    "minutes": minutes,
                    "edges": [],
                    "points": connector.get("points") or [],
                }
            )
            timeline["connector_minutes"] += minutes

        if mode == "transit":
            detail = route["transit_detail"]
            access = road_minutes(
                mode="walk", edges=None, distance_km=detail["access_distance_km"],
                base_speed_kmh=speed_kmh, junctions=detail["access_junctions"],
                hour=hour, config=self.config,
            )
            egress = road_minutes(
                mode="walk", edges=None, distance_km=detail["egress_distance_km"],
                base_speed_kmh=speed_kmh, junctions=detail["egress_junctions"],
                hour=hour, config=self.config,
            )
            rail = rail_minutes(detail["rail_distance_km"], detail["stop_count"], self.config)
            wait_minutes = float(detail["platform_access_minutes"]) + float(detail["wait_minutes"])
            segments.extend(
                [
                    {"kind": "walk", "minutes": access["minutes"], "mean_index": detail["access_mean_index"]},
                    {"kind": "walk", "minutes": egress["minutes"], "mean_index": detail["egress_mean_index"]},
                    {"kind": "transit_wait", "minutes": wait_minutes, "mean_index": detail["wait_index"]},
                    {"kind": "transit_rail", "minutes": rail["minutes"], "mean_index": 0.0},
                ]
            )
            timeline.update(
                {
                    "access": access,
                    "egress": egress,
                    "rail": rail,
                    "wait_minutes": round(wait_minutes, 2),
                }
            )
            refs.extend(
                [
                    {"kind": "walk", "minutes": access["minutes"],
                     "edges": detail["access_edge_indices"], "points": []},
                    {"kind": "walk", "minutes": egress["minutes"],
                     "edges": detail["egress_edge_indices"], "points": []},
                    {"kind": "transit_wait", "minutes": wait_minutes, "edges": [],
                     "points": detail["station_points"]},
                    {"kind": "transit_rail", "minutes": rail["minutes"],
                     "edges": [], "points": []},
                ]
            )
        else:
            edges = [self.road_graph.edges[index] for index in route.get("edge_indices") or []]
            main = road_minutes(
                mode=mode, edges=edges, distance_km=route["distance_km"],
                base_speed_kmh=speed_kmh, junctions=route["junctions"],
                hour=hour, config=self.config,
            )
            segments.append(
                {
                    "kind": mode,
                    "minutes": main["minutes"],
                    "mean_index": float(route["exposure"].get("mean_index") or 0.0),
                }
            )
            refs.append(
                {
                    "kind": mode,
                    "minutes": main["minutes"],
                    "edges": list(route.get("edge_indices") or []),
                    "points": [],
                }
            )
            timeline["main"] = main

        timeline["total_minutes"] = round(
            sum(float(segment["minutes"]) for segment in segments), 2
        )
        return segments, timeline, refs

    def _resample_refs(
        self, refs: list[dict[str, Any]], field: Any
    ) -> list[dict[str, Any]]:
        """换一个暴露场后，按引用重新给每个分段取平均暴露指数。"""
        segments: list[dict[str, Any]] = []
        for ref in refs:
            edge_indices = ref.get("edges") or []
            points = ref.get("points") or []
            if field is None:
                mean_index = 0.0
            elif edge_indices:
                values = [
                    field.index_at(edge["mid_lon"], edge["mid_lat"])
                    for edge in (
                        self.road_graph.edges[index] for index in edge_indices
                    )
                ]
                mean_index = sum(values) / len(values) if values else 0.0
            elif points:
                # 候车段取上下车站中暴露较高的一侧。
                mean_index = max(
                    float(field.index_at(point[0], point[1])) for point in points
                )
            else:
                mean_index = 0.0
            segments.append(
                {
                    "kind": ref["kind"],
                    "minutes": float(ref["minutes"]),
                    "mean_index": mean_index,
                }
            )
        return segments

    def _field_for(
        self,
        corridor_edges: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        *,
        station_strength: float | None,
        corridor_strength: float | None,
        wind_from: float | None,
        wind_speed: float | None,
        focus: list[tuple[float, float]],
        rules: dict[str, Any],
        grid_focus_margin_km: float | None = None,
        lifetime_model: dict[str, Any] | None = None,
    ) -> Any:
        """只建暴露场，不做全路网逐路段采样。

        换风速、换出发时刻时用得到：走廊源集合只依赖污染源位置，与风速无关，
        因此可以复用，省掉每小时重扫 2 万条路段的开销。
        """
        usable = [
            source
            for source in sources
            if source.get("lat") is not None and source.get("lon") is not None
        ]
        if not usable or wind_speed is None or wind_from is None:
            return None
        return build_field_from_sources(
            stations=usable,
            station_strength=station_strength,
            corridor_edges=corridor_edges,
            corridor_strength=corridor_strength,
            wind_from_direction=wind_from,
            wind_speed_ms=wind_speed,
            focus_points=focus,
            rules=rules,
            grid_focus_margin_km=grid_focus_margin_km,
            lifetime_model=lifetime_model,
        )

    def _departure_dose_profile(
        self,
        *,
        hourly: list[dict[str, Any]],
        start_dt: datetime | None,
        limit: int,
        options: list[dict[str, Any]],
        corridor_edges: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        station_strength: float | None,
        corridor_strength: float | None,
        focus: list[tuple[float, float]],
        rules: dict[str, Any],
        grid_focus_margin_km: float | None,
        profile: dict[str, Any],
        scaled: dict[str, float],
        initial_conditions: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """出发时段 × 吸入当量曲线。

        逐小时风场是气象 Agent 已经返回的字段，不需要额外请求。做法是按时段里
        每一小时的风速风向重建暴露场，再把候选方案的分段重新采样，得到
        "如果这个时刻出发，会吸入多少"。

        这是把"建议几点走"从定性变成定量的关键：原来的出发建议只看降雨与温湿，
        完全没有污染项，答不了"晚走半小时能少吸多少"。
        """
        usable = [
            {"time": str(hour.get("time")), "wind_speed": hour.get("wind_speed"),
             "wind_direction": hour.get("wind_direction"),
             "precipitation": hour.get("precipitation"),
             "temperature": hour.get("temperature"), "humidity": hour.get("humidity"),
             "pressure_hpa": hour.get("pressure_hpa")}
            for hour in hourly
            if hour.get("wind_speed") is not None and hour.get("wind_direction") is not None
        ]
        if not usable or not options or not corridor_edges:
            return None

        if start_dt is not None:
            start_text = start_dt.strftime("%Y-%m-%d %H:%M:%S")
            usable = [hour for hour in usable if hour["time"] >= start_text] or usable
        usable = usable[: max(int(limit), 1)]

        points: list[dict[str, Any]] = []
        for hour in usable:
            field = self._field_for(
                corridor_edges, sources,
                station_strength=station_strength,
                corridor_strength=corridor_strength,
                wind_from=float(hour["wind_direction"]),
                wind_speed=float(hour["wind_speed"]),
                focus=focus,
                rules=rules,
                grid_focus_margin_km=grid_focus_margin_km,
                lifetime_model=build_lifetime_model(hour, initial_conditions),
            )
            if field is None:
                continue
            scored: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
            for option in options:
                minutes = float(option["duration_minutes"])
                refs = option["_refs"]
                speed_kmh = float(option["_speed_kmh"])
                # 驾车耗时随时段拥堵变化：晚走可能更堵，这一点也要算进去。
                if option["mode"] == "drive":
                    main_ref = next(
                        (ref for ref in refs if ref["kind"] == "drive"), None
                    )
                    if main_ref:
                        edges = [
                            self.road_graph.edges[index] for index in main_ref["edges"]
                        ]
                        recomputed = road_minutes(
                            mode="drive", edges=edges,
                            distance_km=option["road_distance_km"],
                            base_speed_kmh=speed_kmh,
                            junctions=option["junctions"],
                            hour=_hour_of(hour["time"]),
                            config=self.config,
                        )
                        minutes = minutes - float(main_ref["minutes"]) + float(
                            recomputed["minutes"]
                        )
                segments = self._resample_refs(refs, field)
                evaluation = evaluate_option(
                    segments=segments,
                    minutes=minutes,
                    distance_km=float(option["distance_km"]),
                    mode=option["mode"],
                    profile=profile,
                    scaled=scaled,
                    config=self.config,
                )
                scored.append((evaluation["utility_minutes"], option, evaluation))
            if not scored:
                continue
            scored.sort(key=lambda item: item[0])
            _, best_option, best_evaluation = scored[0]
            points.append(
                {
                    "time": hour["time"],
                    "wind_speed_ms": round(float(hour["wind_speed"]), 2),
                    "wind_direction": round(float(hour["wind_direction"]), 1),
                    "precipitation": hour.get("precipitation"),
                    "recommended_route_id": best_option["route_id"],
                    "recommended_mode": best_option["mode_label"],
                    "intake_index": best_evaluation["intake_detail"]["intake_index"],
                    "utility_minutes": best_evaluation["utility_minutes"],
                    "duration_minutes": round(minutes, 1),
                }
            )

        if len(points) < 2:
            return None

        baseline = points[0]
        # 两个口径都给出：吸入当量最低的时段（回答"几点走更干净"）
        # 与统一效用最低的时段（回答"几点走综合最优"），两者未必一致。
        best = min(points, key=lambda item: item["intake_index"])
        worst = max(points, key=lambda item: item["intake_index"])
        best_utility = min(points, key=lambda item: item["utility_minutes"])
        reduction = (
            (worst["intake_index"] - best["intake_index"]) / worst["intake_index"] * 100.0
            if worst["intake_index"] > 0
            else 0.0
        )
        mode_switches = [
            point["recommended_route_id"] for point in points
        ]
        note = (
            f"未来 {len(points)} 小时内，{_short_time(best['time'])} 出发吸入当量最低"
            f"（{best['intake_index']:.2f} EU），{_short_time(worst['time'])} 最高"
            f"（{worst['intake_index']:.2f} EU），相差 {reduction:.0f}%。"
        )
        if len(set(mode_switches)) > 1:
            note += "不同出发时刻的推荐方式并不相同，说明出发时间会改变最优选择。"
        else:
            note += "各时刻的推荐方式一致，出发时间的差别主要体现在吸入量上。"
        if best_utility["time"] != best["time"]:
            note += (
                f"按统一效用衡量，{_short_time(best_utility['time'])} 出发最优"
                f"（效用 {best_utility['utility_minutes']:.2f} 分钟），"
                f"与按吸入当量衡量的最优时段（{_short_time(best['time'])}）不同。"
            )

        return {
            "available": True,
            "unit": "EU",
            "note": note,
            "baseline_window": baseline["time"],
            "best_window": best["time"],
            "best_utility_window": best_utility["time"],
            "best_utility_minutes": round(float(best_utility["utility_minutes"]), 2),
            "best_utility_route_id": best_utility["recommended_route_id"],
            "best_utility_mode": best_utility["recommended_mode"],
            "best_utility_matches_best_window": best_utility["time"] == best["time"],
            "worst_window": worst["time"],
            "intake_range": {
                "min": best["intake_index"],
                "max": worst["intake_index"],
                "reduction_pct": round(reduction, 1),
            },
            "recommendation_changes_with_time": len(set(mode_switches)) > 1,
            "points": points,
        }

    def _uncertainty_report(
        self,
        *,
        options: list[dict[str, Any]],
        recommended_id: str,
        corridor_edges: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        station_strength: float | None,
        corridor_strength: float | None,
        base_wind_speed: float,
        base_wind_from: float,
        focus: list[tuple[float, float]],
        rules: dict[str, Any],
        grid_focus_margin_km: float | None,
        profile: dict[str, Any],
        scaled: dict[str, float],
        settings: dict[str, Any],
        lifetime_model: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """对风速、风向与画像交换率做蒙特卡洛，给出结论的稳健性。

        旧的 confidence 只反映"缺了几个输入"，与结论稳不稳健无关。这里实际扰动
        输入重建暴露场并重算候选集效用，统计推荐方案在扰动下仍是第一的比例，
        把"筛查级"这个自我声明变成可量化的数字。
        """
        samples = int(settings.get("samples") or 0)
        if samples <= 0 or not options or not corridor_edges:
            return None

        speed_sigma = float(settings.get("wind_speed_rel_sigma", 0.15))
        direction_sigma = float(settings.get("wind_direction_sigma_deg", 20.0))
        exchange_sigma = float(settings.get("exchange_rel_sigma", 0.2))
        base_exchange = float(profile.get("pollution_exchange_minutes", 2.0))

        # 固定种子：同一份输入必须给出同一份稳健性结果，否则不可复现。
        rng = random.Random(20260815)
        picks: dict[str, int] = {}
        intake_samples: dict[str, list[float]] = {option["route_id"]: [] for option in options}
        used = 0

        for _ in range(samples):
            wind_speed = max(base_wind_speed * (1.0 + rng.gauss(0.0, speed_sigma)), 0.1)
            wind_from = (base_wind_from + rng.gauss(0.0, direction_sigma)) % 360.0
            field = self._field_for(
                corridor_edges, sources,
                station_strength=station_strength,
                corridor_strength=corridor_strength,
                wind_from=wind_from,
                wind_speed=wind_speed,
                focus=focus,
                rules=rules,
                grid_focus_margin_km=grid_focus_margin_km,
                lifetime_model=lifetime_model,
            )
            if field is None:
                continue
            perturbed_profile = dict(profile)
            perturbed_profile["pollution_exchange_minutes"] = max(
                base_exchange * (1.0 + rng.gauss(0.0, exchange_sigma)), 0.0
            )
            scored: list[tuple[float, str]] = []
            for option in options:
                segments = self._resample_refs(option["_refs"], field)
                evaluation = evaluate_option(
                    segments=segments,
                    minutes=float(option["duration_minutes"]),
                    distance_km=float(option["distance_km"]),
                    mode=option["mode"],
                    profile=perturbed_profile,
                    scaled=scaled,
                    config=self.config,
                )
                scored.append((evaluation["utility_minutes"], option["route_id"]))
                intake_samples[option["route_id"]].append(
                    evaluation["intake_detail"]["intake_index"]
                )
            scored.sort(key=lambda item: item[0])
            picks[scored[0][1]] = picks.get(scored[0][1], 0) + 1
            used += 1

        if used == 0:
            return None

        def quantile(values: list[float], fraction: float) -> float:
            if not values:
                return 0.0
            ordered = sorted(values)
            index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
            return round(ordered[index], 4)

        recommended_share = picks.get(recommended_id, 0) / used
        if recommended_share >= 0.9:
            level, label = "high", "稳健"
        elif recommended_share >= 0.6:
            level, label = "medium", "基本稳健"
        else:
            level, label = "low", "结论对输入敏感"

        distribution = sorted(
            (
                {
                    "route_id": route_id,
                    "mode_label": next(
                        option["mode_label"] for option in options
                        if option["route_id"] == route_id
                    ),
                    "share": round(count / used, 3),
                }
                for route_id, count in picks.items()
            ),
            key=lambda item: -item["share"],
        )

        intake_intervals = {
            route_id: {
                "p05": quantile(values, 0.05),
                "p50": quantile(values, 0.5),
                "p95": quantile(values, 0.95),
            }
            for route_id, values in intake_samples.items()
            if values
        }

        return {
            "available": True,
            "samples": used,
            "perturbations": {
                "wind_speed_rel_sigma": speed_sigma,
                "wind_direction_sigma_deg": direction_sigma,
                "exchange_rel_sigma": exchange_sigma,
            },
            "recommended_route_id": recommended_id,
            "recommended_share": round(recommended_share, 3),
            "level": level,
            "level_label": label,
            "top_pick_distribution": distribution,
            "intake_intervals": intake_intervals,
            "note": (
                f"在风速 ±{speed_sigma * 100:.0f}%、风向 ±{direction_sigma:.0f}°、"
                f"吸入交换率 ±{exchange_sigma * 100:.0f}% 的 {used} 次扰动中，"
                f"推荐方案仍为第一的比例为 {recommended_share * 100:.0f}%（{label}）。"
            ),
        }

    def _build_exposure(
        self,
        sources: list[dict[str, Any]],
        *,
        station_strength: float | None,
        corridor_strength: float | None,
        wind_from: float | None,
        wind_speed: float | None,
        focus: list[tuple[float, float]],
        rules: dict[str, Any],
        grid_focus_margin_km: float | None = None,
        lifetime_model: dict[str, Any] | None = None,
    ) -> tuple[Any, Any, list[float] | None, list[float] | None]:
        """Build the exposure field and per-edge exposure for this trip.

        Returns ``(corridor_edges, field, edge_exposure, avoidance)``; every item
        after the first is None when there is no usable source location.
        """
        graph = self.road_graph
        usable = [
            source
            for source in sources
            if source.get("lat") is not None and source.get("lon") is not None
        ]
        if not usable:
            return [], None, None, None

        scope_km = float(rules["source_strength"]["corridor_scope_km"])
        corridor_edges = [
            edge
            for edge in graph.edges
            if any(
                haversine_km(source["lon"], source["lat"], edge["mid_lon"], edge["mid_lat"])
                <= scope_km
                for source in usable
            )
        ]
        field = build_field_from_sources(
            stations=usable,
            station_strength=station_strength,
            corridor_edges=corridor_edges,
            corridor_strength=corridor_strength,
            wind_from_direction=wind_from,
            wind_speed_ms=wind_speed,
            focus_points=focus,
            rules=rules,
            grid_focus_margin_km=grid_focus_margin_km,
            lifetime_model=lifetime_model,
        )
        edge_exposure = field.index_for_edges(graph.edges)
        avoidance = avoidance_multipliers(edge_exposure, rules)
        return corridor_edges, field, edge_exposure, avoidance

    # ── 主流程 ──────────────────────────────────────────────────────────
    def run(
        self,
        task: dict[str, Any],
        meteorology_result: dict[str, Any] | None = None,
        air_quality_result: dict[str, Any] | None = None,
        traffic_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        try:
            errors = validate_task(task)
            if errors:
                raise ValueError("；".join(errors))

            origin = _normalise_point(task.get("origin"), "origin")
            destination = _normalise_point(task.get("destination"), "destination")
            profile = resolve_profile(self.config, task.get("travel_profile"))
            thresholds = self.config["weather_thresholds"]

            # ① 气象：优先用调用方传入的结果，否则实时获取。
            if meteorology_result is None:
                from agents.meteorology_agent import run as meteorology_run

                meteorology_result = meteorology_run(
                    {
                        "task_id": f"{task.get('task_id')}_meteo",
                        "lat": origin["lat"],
                        "lon": origin["lon"],
                        "start_time": task.get("start_time"),
                        "end_time": task.get("end_time"),
                    }
                )
            weather = extract_weather_summary(meteorology_result)
            air_domain = (air_quality_result or {}).get("result") or {}
            initial_conditions = air_domain.get("parcel_initial_conditions") or parcel_initial_conditions(
                air_domain.get("hourly_series") or [])
            lifetime_model = build_lifetime_model(
                {"temperature": weather.get("mean_temperature"),
                 "humidity": weather.get("mean_humidity"),
                 "pressure_hpa": weather.get("mean_pressure_hpa")},
                initial_conditions,
            )
            if not weather.get("available"):
                warnings.append("未取得可用气象数据，出行影响因子按缺省处理。")

            # ② 污染信号：显式风险值 > AirQualityAgent 异常分 > 无。
            pollution_risk = task.get("air_quality_risk", DEFAULT_POLLUTION_RISK)
            if pollution_risk is None and isinstance(air_quality_result, dict):
                air_result = air_quality_result.get("result") or {}
                if isinstance(air_result, dict):
                    pollution_risk = air_result.get("anomaly_score")
            if pollution_risk is not None:
                try:
                    pollution_risk = float(pollution_risk)
                except (TypeError, ValueError):
                    pollution_risk = None
                    warnings.append("air_quality_risk 无法解析为数值，已忽略。")

            # ③ 个性化影响因子与出行条件。
            severity = classify_weather(weather, thresholds, pollution_risk=pollution_risk)
            scaled = personalized_severity(severity, profile, self.config)
            condition = overall_condition(scaled, self.config)

            # ④ 污染源与暴露场：把站点异常与交通走廊一起建成可比较的暴露场。
            #    污染避让完全由暴露场驱动，不再使用"是否下风向""是否受监测道路"
            #    这类粗粒度启发式——它们在数据上覆盖过广，且会与暴露场重复计数。
            sources = _normalise_sources(task)

            # 交通压力分：显式传入 > TrafficAgent 输出 > 配置缺省。
            traffic_strength = task.get("traffic_pressure_score")
            if traffic_strength is None and isinstance(traffic_result, dict):
                traffic_strength = (traffic_result.get("result") or {}).get(
                    "traffic_pressure_score"
                )

            origin_xy = (origin["lon"], origin["lat"])
            destination_xy = (destination["lon"], destination["lat"])

            pollution_rules = load_pollution_rules()
            high_threshold = float(
                pollution_rules["avoidance"]["high_exposure_threshold"]
            )

            # 网格范围模式：请求带 exposure_grid_extent="focus_corridor" 时，
            # 网格外接框只取起终点走廊（± 聚焦半径），场值仍由全部源贡献。
            # 用于污染源散布全港的实时规划——不收紧的话源一多网格就外扩成全港，
            # 每次重建（含蒙特卡洛逐样本）都要算几十万个格点。
            grid_focus_margin_km = None
            if task.get("exposure_grid_extent") == "focus_corridor":
                grid_focus_margin_km = float(
                    pollution_rules["grid"].get("focus_margin_km", 1.5)
                )

            # 风速与风向缺一不可。缺少时就**不建场**，而不是退回静风假设：
            # 「测得静风」和「没测到风速」是两件完全不同的事，后者按静风处理
            # 会给出一个全域饱和、没有梯度的假场，看起来正常但绕行无从选择。
            wind_speed = weather.get("mean_wind_speed")
            wind_from = weather.get("dominant_wind_direction")
            field = None
            edge_exposure = None
            avoidance = None
            corridor_edges: list[dict[str, Any]] = []
            if wind_speed is None or wind_from is None:
                warnings.append(
                    "缺少风速或风向，无法判断污染物输送方向，未建立暴露场"
                    "（不以静风假设替代）。路线仅按气象条件比选。"
                )
            else:
                corridor_edges, field, edge_exposure, avoidance = self._build_exposure(
                    sources,
                    station_strength=pollution_risk,
                    corridor_strength=traffic_strength,
                    wind_from=wind_from,
                    wind_speed=wind_speed,
                    focus=[origin_xy, destination_xy],
                    rules=pollution_rules,
                    grid_focus_margin_km=grid_focus_margin_km,
                    lifetime_model=lifetime_model,
                )
                if field is None:
                    warnings.append(
                        "缺少污染源位置，未建立暴露场，路线仅按气象条件比选。"
                    )

            # ⑤ 生成候选路线：常规（最快）+ 强避污（最净）+ 折中（避污乘数减半）。
            # 三类一起进入比选，保证"时间—暴露"前沿的两端与中点都有真实候选，
            # 而不是先只生成常规路线再事后排序。
            candidates: list[tuple[str, dict[str, Any]]] = []
            for mode in ("walk", "drive"):
                for route in self._road_route_options(
                    mode, origin_xy, destination_xy,
                    edge_exposure=edge_exposure, avoidance=avoidance,
                    high_threshold=high_threshold,
                ):
                    candidates.append((mode, route))
                if avoidance is not None:
                    balanced = [1.0 + (a - 1.0) * 0.5 for a in avoidance]
                    for route in self._road_route_options(
                        mode, origin_xy, destination_xy,
                        edge_exposure=edge_exposure, avoidance=balanced,
                        high_threshold=high_threshold,
                    ):
                        candidates.append((mode, route))

            # 去重：不同搜索可能返回同一条几何路线（route_id 相同、指标一致）
            seen: set[tuple] = set()
            deduped: list[tuple[str, dict[str, Any]]] = []
            for mode, route in candidates:
                key = (mode, route.get("alternative_index"),
                       round(route.get("distance_km", 0.0), 3))
                if key in seen:
                    continue
                seen.add(key)
                deduped.append((mode, route))
            candidates = deduped

            transit = self._transit_option(
                origin, destination,
                edge_exposure=edge_exposure, field=field,
                high_threshold=high_threshold,
            )
            if transit:
                candidates.append(("transit", transit))
            else:
                warnings.append(
                    "起点或终点附近没有可用的轨道交通接驳，未生成公共交通方案。"
                )

            if not candidates:
                raise ValueError("未能在路网上找到任何可行路线，请检查起终点坐标")

            # ⑥ 计算耗时与吸入当量：每种方式拆成分段后逐段计算。
            #    步行/驾车用天气折减后的速度，并计入路口等待与道路等级；
            #    公共交通按接驳/候车/区间/接驳四段合成。
            hour = None
            start_dt = _parse_hour(task.get("start_time"))
            if start_dt is not None:
                hour = start_dt.hour

            speed_cache: dict[str, dict[str, Any]] = {}

            def speed_for(mode_name: str) -> float:
                if mode_name not in speed_cache:
                    speed_cache[mode_name] = self._base_speed(mode_name, profile, scaled)
                return float(speed_cache[mode_name]["effective_speed_kmh"])

            # 起终点吸附后的直线接驳段：此前被记进 origin_snap_km 后就丢掉了，
            # 既不进里程也不进耗时与吸入当量。用户自选点位时这段可能有几百米。
            _, origin_snap_km = self.road_graph.nearest_node(origin_xy[0], origin_xy[1])
            _, destination_snap_km = self.road_graph.nearest_node(
                destination_xy[0], destination_xy[1]
            )
            origin_connector = self._connector(field, origin, origin_snap_km)
            destination_connector = self._connector(field, destination, destination_snap_km)
            connectors = [item for item in (origin_connector, destination_connector) if item]
            connector_km = sum(float(item["distance_km"]) for item in connectors)

            options: list[dict[str, Any]] = []
            for mode, route in candidates:
                speed_kmh = speed_for("walk") if mode == "transit" else speed_for(mode)
                segments, timeline, refs = self._segments_for(
                    mode,
                    route,
                    speed_kmh=speed_kmh,
                    hour=hour,
                    connectors=connectors,
                )
                minutes = float(timeline["total_minutes"])
                total_distance = round(route["distance_km"] + connector_km, 3)
                evaluation = evaluate_option(
                    segments=segments,
                    minutes=minutes,
                    distance_km=total_distance,
                    mode=mode,
                    profile=profile,
                    scaled=scaled,
                    config=self.config,
                )
                options.append(
                    {
                        "route_id": f"{mode}_{route.get('alternative_index', 0)}",
                        "mode": mode,
                        "mode_label": MODE_LABELS.get(mode, mode),
                        "alternative_index": route.get("alternative_index", 0),
                        "distance_km": total_distance,
                        "road_distance_km": route["distance_km"],
                        "connector_km": round(connector_km, 3),
                        "duration_minutes": round(minutes, 1),
                        "score": evaluation["score"],
                        "utility_minutes": evaluation["utility_minutes"],
                        "utility_detail": evaluation["utility_detail"],
                        "intake_index": evaluation["intake_detail"]["intake_index"],
                        "intake_detail": evaluation["intake_detail"],
                        "exchange_minutes_per_eu": evaluation["exchange_minutes_per_eu"],
                        "timeline": timeline,
                        "road_names": route["road_names"],
                        "geometry": route["geometry"],
                        "junctions": route["junctions"],
                        "exposure": route["exposure"],
                        "transit_detail": route.get("transit_detail"),
                        "_evaluation": evaluation,
                        "_refs": refs,
                        "_speed_kmh": speed_kmh,
                    }
                )

            # 画像硬约束：allowed_modes 之外的出行方式不进入候选（如长者不自驾）。
            # 与 preferred_modes（软偏好，走 mode_constant_minutes）不同，
            # 这里的限制不可被效用评分推翻。
            allowed_modes = profile.get("allowed_modes")
            if allowed_modes:
                allowed = {str(m) for m in allowed_modes}
                dropped = [o for o in options if o["mode"] not in allowed]
                if dropped and len(options) > len(dropped):
                    options = [o for o in options if o["mode"] in allowed]
                    warnings.append(
                        "画像方式硬约束已生效：已排除 "
                        + "、".join(sorted({o["mode_label"] for o in dropped}))
                        + " 候选。"
                    )

            # 用户显式约束（暴露上限 / 时间预算）：先于一切效用优化满足。
            # 无可行方案时不静默失败——降级为"与约束归一化距离最近的方案"并明确警告。
            user_constraints = task.get("user_constraints") or {}
            max_intake = user_constraints.get("max_intake")
            max_duration = user_constraints.get("max_duration_minutes")

            def _within_constraints(option: dict[str, Any]) -> bool:
                if max_intake is not None and option["intake_index"] > float(max_intake):
                    return False
                if max_duration is not None and option["duration_minutes"] > float(max_duration):
                    return False
                return True

            if max_intake is not None or max_duration is not None:
                feasible = [o for o in options if _within_constraints(o)]
                if feasible:
                    options = feasible
                    warnings.append(
                        f"用户约束已生效：暴露上限 {max_intake}、时间上限 {max_duration} 分钟，"
                        f"候选剩 {len(feasible)} 条。"
                    )
                else:
                    span_t = max(o["duration_minutes"] for o in options) or 1.0
                    span_e = max(o["intake_index"] for o in options) or 1.0

                    def _violation(option: dict[str, Any]) -> float:
                        t_violation = (max(0.0, option["duration_minutes"] - float(max_duration))
                                       / span_t) if max_duration is not None else 0.0
                        e_violation = (max(0.0, option["intake_index"] - float(max_intake))
                                       / span_e) if max_intake is not None else 0.0
                        return t_violation + e_violation

                    closest = min(options, key=_violation)
                    options = [closest]
                    warnings.append(
                        "无完全满足用户约束的方案：已推荐与约束归一化距离最小的方案，"
                        "请放宽暴露上限或时间预算。"
                    )

            # 排序依据是效用（等效分钟）升序：越低越好。全部为绝对量，
            # 与"这次还比了哪几条"无关，因此同一方案在不同候选集下排序不变。
            options.sort(key=lambda item: (item["utility_minutes"], item["duration_minutes"]))
            options = options[:MAX_ROUTE_OPTIONS]

            # Pareto 非支配筛选：在（耗时，吸入）两个维度上都被其他候选严格
            # 压过的方案不能成为推荐——推荐一个支配劣选无法自洽。全部候选
            # 仍然透明列出并标记 pareto_dominated，供前端展示与审计。
            def _is_pareto_dominated(option: dict[str, Any]) -> bool:
                for other in options:
                    if other is option:
                        continue
                    if (other["duration_minutes"] <= option["duration_minutes"]
                            and other["intake_index"] <= option["intake_index"]
                            and (other["duration_minutes"] < option["duration_minutes"]
                                 or other["intake_index"] < option["intake_index"])):
                        return True
                return False

            for option in options:
                option["pareto_dominated"] = _is_pareto_dominated(option)

            # 推荐：Pareto 非支配筛选先行，再按配置的决策规则在前沿上取舍
            # （V9.5：加权和 → 归一化 + 画像驱动的加权 Tchebycheff，默认开启；
            # 规则与权重依据集中在 config.decision，可切回 weighted_sum）。
            recommended = select_recommendation(options, profile, self.config)
            for rank, option in enumerate(options, start=1):
                option["rank"] = rank
                option["recommended"] = option is recommended
            fastest_minutes = min(option["duration_minutes"] for option in options)
            for option in options:
                option["reason"] = build_route_reason(
                    option, option["mode"], profile, option["_evaluation"], option["recommended"]
                )

            # 推荐理由里补一句与次优方案的直接对比，并**把权衡量化**：
            # 只写"领先 X 分"没有信息量，要写出用多少时间换多少吸入。
            comparison_note = None
            rivals = [o for o in options[1:] if o["mode"] != recommended["mode"]]
            rival = rivals[0] if rivals else (options[1] if len(options) > 1 else None)
            if rival is not None:
                delta_time = recommended["duration_minutes"] - rival["duration_minutes"]
                delta_intake = recommended["intake_index"] - rival["intake_index"]
                comparison_note = (
                    f"与同样可行的{rival['mode_label']}方案相比，等效时间少 "
                    f"{rival['utility_minutes'] - recommended['utility_minutes']:.0f} 分钟："
                    f"在途时间{'多' if delta_time >= 0 else '少'} {abs(delta_time):.0f} 分钟，"
                    f"吸入当量{'高' if delta_intake >= 0 else '低'} {abs(delta_intake):.2f} EU。"
                )

            # ⑧ 出发时段、装备与避污建议。
            departure = build_departure_advice(weather, profile, self.config)
            gear = build_gear_reminders(weather, scaled, recommended["mode"])
            field_summary = field.summary() if field is not None else {}
            pollution_advice = build_pollution_advice(
                field_summary=field_summary,
                route_options=options,
                recommended=recommended,
                origin=origin,
                config=pollution_rules,
            )

            # ⑨ 出发时段 × 吸入当量：用逐小时风场重建暴露场，把"几点走"量化。
            departure_curve_cfg = self.config.get("departure_curve") or {}
            departure_dose = self._departure_dose_profile(
                hourly=weather.get("hourly_series") or [],
                start_dt=start_dt,
                limit=int(departure_curve_cfg.get("max_points", 8)),
                options=options,
                corridor_edges=corridor_edges,
                sources=sources,
                station_strength=pollution_risk,
                corridor_strength=traffic_strength,
                focus=[origin_xy, destination_xy],
                rules=pollution_rules,
                grid_focus_margin_km=grid_focus_margin_km,
                profile=profile,
                scaled=scaled,
                initial_conditions=initial_conditions,
            )

            # ⑩ 结论稳健性：扰动风速、风向与画像交换率，重建场并重算候选集。
            uncertainty_cfg = self.config.get("uncertainty") or {}
            uncertainty = self._uncertainty_report(
                options=options,
                recommended_id=recommended["route_id"],
                corridor_edges=corridor_edges,
                sources=sources,
                station_strength=pollution_risk,
                corridor_strength=traffic_strength,
                base_wind_speed=float(wind_speed) if wind_speed is not None else 0.0,
                base_wind_from=float(wind_from) if wind_from is not None else 0.0,
                focus=[origin_xy, destination_xy],
                rules=pollution_rules,
                grid_focus_margin_km=grid_focus_margin_km,
                profile=profile,
                scaled=scaled,
                settings=uncertainty_cfg,
                lifetime_model=lifetime_model,
            )

            mode_best: dict[str, dict[str, Any]] = {}
            for option in options:
                mode_best.setdefault(option["mode"], option)

            headline = (
                f"建议采用{recommended['mode_label']}方案"
                f"（{recommended['distance_km']:.2f} km，约 {recommended['duration_minutes']:.0f} 分钟；"
                f"等效时间 {recommended['utility_minutes']:.0f} 分钟，"
                f"吸入当量 {recommended['intake_index']:.2f} EU）"
            )

            evidence = [
                f"出行条件评分 {condition['score']:.1f}（{condition['level_label']}），"
                f"主导影响因子：{'、'.join(condition['dominant_factors']) or '无'}。",
                f"共比选 {len(options)} 条路线，覆盖 {len(mode_best)} 种出行方式；"
                f"排序依据为等效时间（分钟）升序，是绝对量，与候选集大小无关。",
                f"推荐方案在途 {recommended['duration_minutes']:.0f} 分钟"
                f"（最快 {fastest_minutes:.0f} 分钟），其中天气加成 "
                f"{recommended['utility_detail']['weather_added_minutes']:.0f} 分钟、"
                f"吸入折算 {recommended['utility_detail']['pollution_added_minutes']:.0f} 分钟"
                f"（该画像 1 EU 约等于 {recommended['exchange_minutes_per_eu']:.1f} 分钟）。",
                wind_note(weather),
            ]
            if field is not None:
                evidence.append(
                    f"暴露场由 {field_summary.get('source_count', 0)} 个源建立"
                    f"（监测站 {len(field_summary.get('station_sources', []))} 个 + "
                    f"走廊采样点 {field_summary.get('corridor_source_count', 0)} 个）；"
                    f"推荐方案峰值暴露 "
                    f"{float(recommended['exposure'].get('peak_index') or 0):.2f}，"
                    f"高暴露里程 "
                    f"{float(recommended['exposure'].get('high_exposure_km') or 0):.2f} km。"
                )
            if departure_dose:
                evidence.append(departure_dose["note"])
            if uncertainty:
                evidence.append(uncertainty["note"])
            for note in pollution_advice.get("notes", [])[:2]:
                evidence.append(note)

            assumptions = [
                "路线基于香港道路中心线数据集规划，未叠加实时交通流量与临时封路。",
                "轨道交通网络由车站点位按邻近关系重建，站序为筛查级近似，非官方运营线路图。",
                "步行与驾车速度按画像基准速度叠加天气折减、路口等待与时段拥堵，非个人实测速度。",
                "吸入当量按分段呼吸速率与车厢穿透系数折算，为相对口径，不等同于实际吸入剂量。",
            ]
            assumptions.extend(field.assumptions() if field is not None else [])

            missing_components = sum(
                (not weather.get("available"), pollution_risk is None, field is None)
            )

            # 内部字段（可重采样引用、速度、评估明细）不进输出契约。
            # transit_detail 里用于重采样的路段索引与站点坐标同样属于内部数据，
            # 一个公共交通方案会带上几百个整数，必须剥掉。
            private_transit_keys = {
                "access_edge_indices",
                "egress_edge_indices",
                "station_points",
            }

            def _public_transit_detail(detail: Any) -> Any:
                if not isinstance(detail, dict):
                    return detail
                return {
                    key: value
                    for key, value in detail.items()
                    if key not in private_transit_keys
                }

            public_options = [
                {
                    **{
                        key: value
                        for key, value in option.items()
                        if not key.startswith("_")
                    },
                    "transit_detail": _public_transit_detail(option.get("transit_detail")),
                }
                for option in options
            ]

            result = {
                "origin": origin,
                "destination": destination,
                "straight_line_km": round(straight_line_km(origin_xy, destination_xy), 3),
                "start_time": task.get("start_time"),
                "end_time": task.get("end_time"),
                "travel_profile": {
                    "profile_id": profile.get("profile_id"),
                    "allowed_modes": profile.get("allowed_modes"),
                    "hard_constraints": [
                        "allowed_modes（方式硬约束）",
                        "max_walk_km（单段步行上限）",
                        "max_trip_minutes（单程时间上限）",
                        "user_constraints（暴露上限/时间预算，传入时）",
                    ],
                    "soft_preferences": [
                        "preferred_modes（经 mode_constant_minutes 进入效用，可被权衡推翻）",
                        "pollution_exchange_minutes（污染厌恶强度）",
                    ],
                    "profile_name": profile.get("profile_name"),
                    "description": profile.get("description"),
                    "preferred_modes": profile.get("preferred_modes"),
                    "mode_constant_minutes": profile.get("mode_constant_minutes"),
                    "pollution_exchange_minutes": profile.get("pollution_exchange_minutes"),
                    "weather_time_multiplier": profile.get("weather_time_multiplier"),
                    "max_walk_km": profile.get("max_walk_km"),
                    "max_trip_minutes": profile.get("max_trip_minutes"),
                    "time_flexibility_minutes": profile.get("time_flexibility_minutes"),
                    "walk_speed_factor": profile.get("walk_speed_factor"),
                    "sensitivities": profile.get("sensitivities"),
                },
                "weather_summary": {
                    key: value for key, value in weather.items() if key != "hourly_series"
                },
                "weather_severity": severity,
                "personalized_severity": scaled,
                "travel_condition": condition,
                "route_options": public_options,
                "recommendation": {
                    "route_id": recommended["route_id"],
                    "mode": recommended["mode"],
                    "mode_label": recommended["mode_label"],
                    "headline": headline,
                    "utility_minutes": recommended["utility_minutes"],
                    "intake_index": recommended["intake_index"],
                    "reasons": [
                        item
                        for item in (
                            recommended["reason"],
                            comparison_note,
                            f"出行条件评分 {condition['score']:.1f}（{condition['level_label']}）",
                        )
                        if item
                    ],
                    "alternative_by_mode": {
                        mode: {
                            "route_id": option["route_id"],
                            "mode_label": option["mode_label"],
                            "distance_km": option["distance_km"],
                            "duration_minutes": option["duration_minutes"],
                            "score": option["score"],
                            "utility_minutes": option["utility_minutes"],
                            "intake_index": option["intake_index"],
                        }
                        for mode, option in mode_best.items()
                    },
                },
                "departure_advice": departure,
                "departure_dose_curve": departure_dose,
                "uncertainty": uncertainty,
                "gear_reminders": gear,
                "pollution_field": {
                    **field_summary,
                    "grid": field.sample_points() if field is not None else [],
                }
                if field is not None
                else None,
                "pollution_advice": pollution_advice,
                "assumptions": assumptions,
            }

            # 置信度优先采用实测的结论稳健性；拿不到时才退回"输入完整度"。
            if uncertainty:
                confidence = round(uncertainty["recommended_share"], 2)
                confidence_basis = "蒙特卡洛扰动下推荐方案仍为第一的比例"
            else:
                confidence = round(max(0.4, 1.0 - 0.15 * missing_components), 2)
                confidence_basis = "输入完整度（未能计算结论稳健性）"

            return {
                "task_id": task.get("task_id"),
                "agent_name": self.agent_name,
                "status": "warning" if warnings else "success",
                "result": result,
                "evidence": evidence,
                "confidence": confidence,
                "data_quality": {
                    "missing_rate": round(missing_components / 3.0, 2),
                    "confidence_basis": confidence_basis,
                    "meteorology_source": (meteorology_result or {}).get("data_quality", {}).get("source")
                    if isinstance(meteorology_result, dict) else None,
                    "pollution_risk_used": pollution_risk,
                    "note": "；".join(warnings) if warnings else "气象、路网与污染信号均可用",
                },
                "warnings": warnings,
                "created_at": _now(),
            }

        except Exception as exc:  # noqa: BLE001 - Agent 契约要求任何失败都返回结构化结果
            return {
                "task_id": task.get("task_id") if isinstance(task, dict) else None,
                "agent_name": self.agent_name,
                "status": "error",
                "result": {},
                "evidence": [],
                "confidence": 0.0,
                "data_quality": {"missing_rate": 1.0, "note": str(exc)},
                "warnings": [str(exc)],
                "created_at": _now(),
            }


def run(task: dict[str, Any]) -> dict[str, Any]:
    """便捷入口，供 Coordinator 直接调用。"""
    return TravelAgent().run(task)


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 TravelAgent")
    parser.add_argument(
        "--input",
        type=Path,
        default=ROOT / "examples" / "travel_case_request.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    result = TravelAgent().run(
        payload,
        meteorology_result=payload.get("meteorology_result"),
        air_quality_result=payload.get("air_quality_result"),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
