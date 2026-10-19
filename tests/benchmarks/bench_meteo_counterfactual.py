"""Benchmark: counterfactual wind field — change only direction / speed.

The real-data departure experiment changes wind, air quality and weather burden
together, so it cannot isolate the wind. This script holds everything else
fixed (same OD, same profile, same pollution source, same air quality, same
precipitation / temperature) and replaces **only** the wind field, then records
how far the change propagates:

  spatial side : upwind road matches, sensitive POIs in the sector
  travel side  : public-transport and driving intake / utility, final mode

Variants: real wind, +90 deg, +180 deg, calm (x0.3), strong (x2.0).

Usage: /usr/bin/python3 tests/benchmarks/bench_meteo_counterfactual.py
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

WINDOW = ("2023-08-15 08:00:00", "2023-08-15 11:00:00")
STATION = {"station_id": "mong_kok", "station_name": "Mong Kok Roadside",
           "lat": 22.3227, "lon": 114.1683}
ORIGIN = {"name": "旺角站", "lat": 22.3193, "lon": 114.1694}
DESTINATION = {"name": "太子站", "lat": 22.32441, "lon": 114.16828}
PROFILE = "commuter"
POLLUTION_SOURCE = {"station_id": "MK", "station_name": "Mong Kok Roadside",
                    "lat": 22.3193, "lon": 114.1694, "radius_km": 3.0}

VARIANTS = [
    {"tag": "real", "label": "真实风场", "dir_delta": 0.0, "speed_scale": 1.0},
    {"tag": "dir_plus_90", "label": "风向 +90°", "dir_delta": 90.0, "speed_scale": 1.0},
    {"tag": "dir_plus_180", "label": "风向 +180°", "dir_delta": 180.0, "speed_scale": 1.0},
    {"tag": "calm", "label": "风速 ×0.3（低风速分支）", "dir_delta": 0.0, "speed_scale": 0.3},
    {"tag": "strong", "label": "风速 ×2.0", "dir_delta": 0.0, "speed_scale": 2.0},
]


def retarget(meteo: dict, dir_delta: float, speed_scale: float) -> dict:
    """Copy a MeteorologyAgent response and shift only its wind field."""
    clone = copy.deepcopy(meteo)
    result = clone.get("result") if isinstance(clone.get("result"), dict) else clone
    if not isinstance(result, dict):
        return clone

    for key in ("dominant_wind_direction", "upwind_direction", "downwind_direction"):
        value = result.get(key)
        if isinstance(value, (int, float)):
            result[key] = round((float(value) + dir_delta) % 360.0, 2)
    if isinstance(result.get("mean_wind_speed"), (int, float)):
        result["mean_wind_speed"] = round(float(result["mean_wind_speed"]) * speed_scale, 3)

    for hour in result.get("hourly_series") or []:
        if not isinstance(hour, dict):
            continue
        if isinstance(hour.get("wind_direction"), (int, float)):
            hour["wind_direction"] = round((float(hour["wind_direction"]) + dir_delta) % 360.0, 2)
        if isinstance(hour.get("wind_speed"), (int, float)):
            hour["wind_speed"] = round(float(hour["wind_speed"]) * speed_scale, 3)
    return clone


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.spatial_agent import SpatialAgent
    from agents.travel_agent import TravelAgent

    base_meteo = meteorology_run({"task_id": "CF_meteo", "station_id": STATION["station_id"],
                                  "lat": STATION["lat"], "lon": STATION["lon"],
                                  "start_time": WINDOW[0], "end_time": WINDOW[1]})
    air = AirQualityAgent().run({"task_id": "CF_air", "station_name": STATION["station_name"],
                                 "start_time": WINDOW[0], "end_time": WINDOW[1]})
    risk = (air.get("result") or {}).get("anomaly_score")

    base_result = base_meteo.get("result") or {}
    print(f"真实风况: {base_result.get('mean_wind_speed')} m/s @ "
          f"{base_result.get('dominant_wind_direction')}° | 空气异常分 {risk}\n")

    spatial_agent, travel = SpatialAgent(), TravelAgent()
    rows = []
    for variant in VARIANTS:
        meteo = retarget(base_meteo, variant["dir_delta"], variant["speed_scale"])
        mres = meteo.get("result") or {}
        task = {"task_id": f"CF_{variant['tag']}", "station_name": STATION["station_name"],
                "station_id": STATION["station_id"], "lat": STATION["lat"], "lon": STATION["lon"],
                "start_time": WINDOW[0], "end_time": WINDOW[1]}

        spatial_out = spatial_agent.run(dict(task), meteo)
        sres = spatial_out.get("result") or {}
        upwind = sres.get("upwind_roads") or []
        poi_count = sres.get("sensitive_poi_count")

        travel_out = travel.run(
            {"task_id": f"CF_TRAVEL_{variant['tag']}", "origin": ORIGIN, "destination": DESTINATION,
             "start_time": WINDOW[0], "end_time": WINDOW[1], "travel_profile": PROFILE,
             "pollution_source": POLLUTION_SOURCE, "air_quality_risk": risk},
            meteorology_result=meteo, air_quality_result=air)
        tresult = travel_out.get("result") or {}
        options = tresult.get("route_options") or []
        rec = next((o for o in options if o.get("recommended")), None)

        def by_mode(mode: str) -> dict | None:
            """同一方式中吸入最低的候选，用于比较暴露水平。"""
            candidates = [o for o in options if o.get("mode") == mode]
            return min(candidates, key=lambda o: o.get("intake_index") or 9e9) if candidates else None

        transit, driving = by_mode("transit"), by_mode("drive")
        field = tresult.get("pollution_field") or {}

        rows.append({
            "tag": variant["tag"], "label": variant["label"],
            "wind_speed": mres.get("mean_wind_speed"),
            "wind_direction": mres.get("dominant_wind_direction"),
            "upwind_road_count": len(upwind),
            "upwind_best_alignment": round(max([float(r.get("upwind_alignment") or 0.0)
                                                for r in upwind] or [0.0]), 3),
            "sensitive_poi_count": poi_count,
            "spatial_confidence": sres.get("spatial_confidence"),
            "field_source_count": field.get("source_count"),
            "transit_intake": None if transit is None else round(transit.get("intake_index") or 0, 4),
            "transit_utility": None if transit is None else round(transit.get("utility_minutes") or 0, 2),
            "driving_intake": None if driving is None else round(driving.get("intake_index") or 0, 4),
            "driving_utility": None if driving is None else round(driving.get("utility_minutes") or 0, 2),
            "recommended_mode": None if rec is None else rec.get("mode_label") or rec.get("mode"),
            "recommended_intake": None if rec is None else round(rec.get("intake_index") or 0, 4),
            "recommended_utility": None if rec is None else round(rec.get("utility_minutes") or 0, 2),
            "affected_area": sres.get("affected_area"),
            "warnings": (tresult.get("assumptions") or [])[:3],
        })
        print(f"  {variant['label']:<20} 风 {mres.get('mean_wind_speed')} m/s @ "
              f"{mres.get('dominant_wind_direction')}° | 上风向道路 {len(upwind)} | "
              f"敏感点 {poi_count} | 公交 {rows[-1]['transit_intake']} | "
              f"驾车 {rows[-1]['driving_intake']} | 推荐 {rows[-1]['recommended_mode']}", flush=True)

    modes = {r["recommended_mode"] for r in rows}
    payload = {
        "design": {"window": WINDOW, "od": "mongkok_to_prince_edward", "profile": PROFILE,
                   "note": "只替换风场（风向偏移 / 风速倍率），其余输入（起终点、画像、污染源、空气质量、"
                           "降水与温度）保持完全一致；空间与出行两侧同时观察"},
        "variants": [v["tag"] for v in VARIANTS],
        "recommended_mode_changed": len(modes) > 1,
        "rows": rows,
    }
    path = save_result("meteo_counterfactual", payload)
    print(f"\nsaved -> {path}")
    print(f"推荐方案是否随风场改变: {'是' if len(modes) > 1 else '否'}（出现的方式：{sorted(modes)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
