"""Spatial Agent for station-road-wind-sensitive-POI matching."""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..tools.spatial_tools import analyze_spatial_context, load_geojson

PACKAGE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_ROADS = PACKAGE_DIR / "data" / "processed" / "road_network.geojson"
DEFAULT_POIS = PACKAGE_DIR / "data" / "processed" / "poi_sensitive.geojson"

STATIONS: dict[str, dict[str, Any]] = {
    "MK": {
        "station_id": "MK",
        "station_name": "Mong Kok Roadside",
        "aliases": ["Mong Kok", "旺角", "旺角路边站", "旺角路邊站"],
        "lat": 22.3227,
        "lon": 114.1683,
        "station_type": "roadside",
        "address": "Junction of Nathan Road and Lai Chi Kok Road, Mong Kok, Kowloon",
    },
    "CB": {
        "station_id": "CB",
        "station_name": "Causeway Bay Roadside",
        "aliases": ["Causeway Bay", "铜锣湾", "銅鑼灣", "铜锣湾路边站", "銅鑼灣路邊站"],
        "lat": 22.2802,
        "lon": 114.1851,
        "station_type": "roadside",
        "address": "1 Yee Wo Street pedestrian area, Causeway Bay, Hong Kong",
    },
    "CL": {
        "station_id": "CL",
        "station_name": "Central Roadside",
        "aliases": ["Central", "中环", "中環", "中环路边站", "中環路邊站"],
        "lat": 22.2818,
        "lon": 114.1581,
        "station_type": "roadside",
        "address": "Chater Road roadside area, Central, Hong Kong",
    },
}


def _normalise(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _resolve_station(task: dict[str, Any]) -> dict[str, Any]:
    station_id = _normalise(task.get("station_id")).upper()
    if station_id in STATIONS:
        station = dict(STATIONS[station_id])
    else:
        requested = _normalise(task.get("station_name") or task.get("station"))
        station = {}
        for candidate in STATIONS.values():
            names = [candidate["station_name"], *candidate.get("aliases", [])]
            if requested and requested in {_normalise(name) for name in names}:
                station = dict(candidate)
                break
        if not station:
            if task.get("lat") is None or task.get("lon") is None:
                raise ValueError("Unknown station: provide station_id/name or both lat and lon")
            station = {
                "station_id": str(task.get("station_id") or "CUSTOM"),
                "station_name": str(task.get("station_name") or task.get("station") or "Custom station"),
                "lat": float(task["lat"]),
                "lon": float(task["lon"]),
                "station_type": str(task.get("station_type") or "unknown"),
            }

    # Explicit coordinates may refine a known station when the coordinator supplies them.
    if task.get("lat") is not None and task.get("lon") is not None:
        station["lat"] = float(task["lat"])
        station["lon"] = float(task["lon"])
    if not (-90 <= float(station["lat"]) <= 90 and -180 <= float(station["lon"]) <= 180):
        raise ValueError("Station coordinates are outside valid latitude/longitude bounds")
    return station


def _get_valid_wind_direction(data: dict[str, Any]) -> tuple[float | None, str | None]:
    """
    从气象结果中提取有效风向值。
    
    优先级顺序：
    1. dominant_wind_direction — 时间段主导风向（正式契约字段）
    2. latest_wind_direction — 末小时风向
    3. wind_direction — 旧字段（兼容）
    
    注意：0° 是合法北风，不能用简单的 or 链判断。
    """
    for key in ("dominant_wind_direction", "latest_wind_direction", "wind_direction"):
        value = data.get(key)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and 0.0 <= value < 360.0:
            return value, key
    return None, None


def _extract_weather(meteorology_result: dict[str, Any] | None) -> tuple[float | None, float | None, list[str], str | None]:
    """
    从 MeteorologyAgent 输出中提取风向和风速。
    
    Returns:
        (direction, speed, warnings, direction_source)
    """
    warnings: list[str] = []
    direction_source: str | None = None
    payload = meteorology_result or {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    
    # 提取风向（优先 dominant_wind_direction）
    direction, direction_source = _get_valid_wind_direction(result)
    
    if direction is None:
        warnings.append("wind_direction is missing or invalid; a circular affected area is used")
    
    # 提取风速（优先 mean_wind_speed）
    speed = result.get("mean_wind_speed") or result.get("latest_wind_speed") or result.get("wind_speed")

    try:
        speed = float(speed)
        if not math.isfinite(speed) or speed < 0:
            raise ValueError
    except (TypeError, ValueError):
        speed = None
        warnings.append("wind_speed is missing or invalid")
    if speed is not None and speed < 0.5:
        warnings.append("wind_speed is below 0.5 m/s; directional dispersion is treated as unreliable")
    
    return direction, speed, warnings, direction_source


class SpatialAgent:
    """Load prepared data and produce the team's standard Agent JSON."""

    agent_name = "SpatialAgent"

    def __init__(self, roads_path: str | Path = DEFAULT_ROADS, pois_path: str | Path = DEFAULT_POIS):
        self.roads_path = Path(roads_path)
        self.pois_path = Path(pois_path)
        self._roads: dict[str, Any] | None = None
        self._pois: dict[str, Any] | None = None

    @property
    def roads(self) -> dict[str, Any]:
        if self._roads is None:
            self._roads = load_geojson(self.roads_path)
        return self._roads

    @property
    def pois(self) -> dict[str, Any]:
        if self._pois is None:
            self._pois = load_geojson(self.pois_path)
        return self._pois

    def run(
        self,
        task: dict[str, Any],
        meteorology_result: dict[str, Any] | None = None,
        *,
        radius_km: float = 3.0,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        try:
            radius_km = float(radius_km)
            if not 1.0 <= radius_km <= 3.0:
                raise ValueError("radius_km must be between 1 and 3 km")
            station = _resolve_station(task)
            direction, speed, weather_warnings, direction_source = _extract_weather(meteorology_result)
            warnings.extend(weather_warnings)
            result = analyze_spatial_context(
                station=station,
                wind_direction=direction,
                wind_speed=speed,
                roads=self.roads,
                pois=self.pois,
                radius_km=radius_km,
            )
            result.update(
                {
                    "station": station,
                    "analysis_radius_km": radius_km,
                    "wind_direction": direction,
                    "wind_speed": speed,
                }
            )
            # 由 MeteorologyAgent 生成的逐小时平流路径，和本模块的静态
            # 下风向扇区并列返回，供前端区分“影响范围”与“预测轨迹”。
            result_weather = (
                meteorology_result.get("result", meteorology_result)
                if isinstance(meteorology_result, dict) else {}
            )
            if isinstance(result_weather, dict) and "pollution_path_prediction" in result_weather:
                result["pollution_path_prediction"] = result_weather["pollution_path_prediction"]

            road_coverage = result["nearby_road_count"] > 0
            poi_coverage = result["sensitive_poi_count"] > 0
            wind_coverage = direction is not None and speed is not None
            coverage = sum((road_coverage, poi_coverage, wind_coverage)) / 3.0
            confidence = round(0.55 + 0.4 * coverage, 2)
            if confidence >= 0.85:
                spatial_confidence = "high"
            elif confidence >= 0.7:
                spatial_confidence = "medium"
            else:
                spatial_confidence = "low"
            result["spatial_confidence"] = spatial_confidence

            evidence = [
                f"在站点 {radius_km:g} km 范围内匹配到 {result['nearby_road_count']} 条道路中心线。",
                f"识别到 {len(result['upwind_roads'])} 条上风向候选道路。",
                f"潜在影响区内识别到 {result['sensitive_poi_count']} 个敏感 POI。",
            ]
            missing_components = sum((not road_coverage, not poi_coverage, not wind_coverage))
            
            # 构建 meta 字段记录风向来源
            meta: dict[str, Any] = {}
            if direction is not None and direction_source is not None:
                meta["wind_direction_used"] = direction
                meta["wind_direction_source"] = direction_source
            
            response = {
                "task_id": task.get("task_id"),
                "agent_name": self.agent_name,
                "status": "warning" if warnings else "success",
                "result": result,
                "evidence": evidence,
                "confidence": confidence,
                "data_quality": {
                    "missing_rate": round(missing_components / 3.0, 2),
                    "note": "；".join(warnings) if warnings else "道路、敏感 POI 与气象输入可用",
                },
                "warnings": warnings,
                "created_at": datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds"),
            }
            if meta:
                response["meta"] = meta
            return response
        except Exception as exc:
            return {
                "task_id": task.get("task_id"),
                "agent_name": self.agent_name,
                "status": "error",
                "result": {},
                "evidence": [],
                "confidence": 0.0,
                "data_quality": {"missing_rate": 1.0, "note": str(exc)},
                "warnings": [str(exc)],
                "created_at": datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds"),
            }


def main() -> None:
    package_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run the Spatial Agent with a JSON request")
    parser.add_argument("--input", type=Path, default=package_dir / "examples" / "request.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    task = payload.get("task") if isinstance(payload.get("task"), dict) else payload
    meteorology = payload.get("meteorology_result")
    radius = payload.get("radius_km", task.get("radius_km", 3.0))
    result = SpatialAgent().run(task, meteorology, radius_km=radius)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
