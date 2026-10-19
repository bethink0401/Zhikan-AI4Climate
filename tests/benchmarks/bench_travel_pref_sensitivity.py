"""Sensitivity: how much flipping comes from the user's mode-preference prior?

``mode_constant_minutes`` is an explicit **user mode-preference prior** (walking
for a runner, transit for an elderly traveller) — it is neither an environmental
nor a health benefit. This script re-runs two scenario sets with those priors
zeroed, so that every flip can be attributed either to environment or to
preference:

  A. departure-time set: real hourly meteorology + real station air quality on
     2023-08-15; 2 OD x 4 profiles x 3 slots = 24 runs per configuration;
  B. boundary set: controlled weather (mild / heavy rain); 2 OD x 4 profiles x
     2 regimes = 16 runs per configuration.

The zeroed configuration is written to a temporary file for the second agent
instance, so no project file is modified.

Usage: /usr/bin/python3 tests/benchmarks/bench_travel_pref_sensitivity.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

SLOTS = {
    "morning_07_09": ("2023-08-15 07:00:00", "2023-08-15 09:00:00"),
    "midday_12_14": ("2023-08-15 12:00:00", "2023-08-15 14:00:00"),
    "evening_18_20": ("2023-08-15 18:00:00", "2023-08-15 20:00:00"),
}
CASES = [
    {"case_id": "mongkok_to_prince_edward",
     "origin": {"name": "旺角站", "lat": 22.3193, "lon": 114.1694},
     "destination": {"name": "太子站", "lat": 22.32441, "lon": 114.16828}},
    {"case_id": "cwb_to_wanchai",
     "origin": {"name": "铜锣湾站", "lat": 22.2801, "lon": 114.1850},
     "destination": {"name": "湾仔站", "lat": 22.27758, "lon": 114.17334}},
]
PROFILES = ["commuter", "runner", "elderly", "parent_with_child"]
POLLUTION_SOURCE = {"station_id": "MK", "station_name": "Mong Kok Roadside",
                    "lat": 22.3193, "lon": 114.1694, "radius_km": 3.0}
STATION = {"station_id": "mong_kok", "station_name": "Mong Kok Roadside",
           "lat": 22.3227, "lon": 114.1683}
BOUNDARY_WEATHER = ("mild", "heavy_rain")
BOUNDARY_WINDOW = ("2023-08-15 08:00:00", "2023-08-15 11:00:00")


def zero_pref_config() -> Path:
    """mode_constant_minutes 全部置零的临时配置。"""
    config = json.loads((BASE / "config" / "travel_profile.json").read_text(encoding="utf-8"))
    for profile in config.get("profiles", []):
        constants = profile.get("mode_constant_minutes") or {}
        profile["mode_constant_minutes"] = {key: 0.0 for key in constants}
    path = Path(tempfile.mkdtemp()) / "travel_profile_zero_pref.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path


def synthetic_meteo(regime: str) -> dict:
    """边界集使用的受控天气（与 bench_travel_boundary.py 一致）。"""
    hours = [f"2023-08-15 0{h}:00:00" for h in (8, 9, 10)]
    if regime == "mild":
        precip = [0.0, 0.0, 0.0]
        result = {"mean_temperature": 26.5, "mean_humidity": 68.0, "total_precipitation": 0.0,
                  "mean_wind_speed": 2.6, "dominant_wind_direction": 250.0,
                  "dominant_wind_direction_label": "WSW", "downwind_direction": 70.0,
                  "upwind_direction": 250.0, "weather_risk": 0.18, "risk_type": "low",
                  "dispersion_condition": "有利扩散"}
    else:
        precip = [5.2, 6.4, 3.1]
        result = {"mean_temperature": 29.8, "mean_humidity": 93.0, "total_precipitation": 14.7,
                  "mean_wind_speed": 1.1, "dominant_wind_direction": 205.0,
                  "dominant_wind_direction_label": "SSW", "downwind_direction": 25.0,
                  "upwind_direction": 205.0, "weather_risk": 0.78, "risk_type": "high",
                  "dispersion_condition": "一般"}
    result["hourly_series"] = [
        {"time": t, "temperature": result["mean_temperature"], "humidity": result["mean_humidity"],
         "precipitation": p, "wind_speed": result["mean_wind_speed"], "wind_direction": 250.0}
        for t, p in zip(hours, precip)
    ]
    return {"agent_name": "MeteorologyAgent", "status": "success", "result": result}


def read_rec(out: dict) -> dict:
    options = (out.get("result") or {}).get("route_options") or []
    rec = next((o for o in options if o.get("recommended")), None) or {}
    return {"mode": rec.get("mode"), "route": rec.get("route_id"),
            "utility": rec.get("utility_minutes"), "intake": rec.get("intake_index"),
            "duration": rec.get("duration_minutes")}


def group_flips(rows: list[dict], condition_key: str) -> dict:
    """按（OD × 画像）分组，看推荐是否随条件变化。rows 必须来自同一配置。"""
    groups: dict = {}
    for row in rows:
        groups.setdefault(f"{row['case']}|{row['profile']}", {})[row[condition_key]] = row["mode"]
    flips = {key: value for key, value in groups.items() if len(set(value.values())) > 1}
    return {"groups": groups, "flips": flips, "rate": f"{len(flips)}/{len(groups)}"}


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.travel_agent import TravelAgent

    zero_path = zero_pref_config()
    agents = {"default": TravelAgent(), "zero_pref": TravelAgent(zero_path)}
    air_agent = AirQualityAgent()

    # ── A. 出发时刻集（真实条件）──────────────────────────────────
    conditions: dict = {}
    for slot, (start, end) in SLOTS.items():
        meteo = meteorology_run({"task_id": f"PS_{slot}", "station_id": STATION["station_id"],
                                 "lat": STATION["lat"], "lon": STATION["lon"],
                                 "start_time": start, "end_time": end})
        air = air_agent.run({"task_id": f"PS_{slot}_air", "station_name": STATION["station_name"],
                             "start_time": start, "end_time": end})
        conditions[slot] = {"start": start, "end": end, "meteo": meteo, "air": air}

    rows_a: list[dict] = []
    for tag, agent in agents.items():
        for case in CASES:
            for profile in PROFILES:
                for slot in SLOTS:
                    cond = conditions[slot]
                    task = {"task_id": f"PSA_{case['case_id']}_{profile}_{slot}_{tag}",
                            "origin": case["origin"], "destination": case["destination"],
                            "start_time": cond["start"], "end_time": cond["end"],
                            "travel_profile": profile,
                            "pollution_source": POLLUTION_SOURCE,
                            "air_quality_risk": (cond["air"].get("result") or {}).get("anomaly_score")}
                    out = agent.run(task, meteorology_result=cond["meteo"], air_quality_result=cond["air"])
                    rec = read_rec(out)
                    rows_a.append({"config": tag, "case": case["case_id"], "profile": profile,
                                   "slot": slot, **rec})
                    print(f"[A:{tag}] {case['case_id'][:12]:<12} {profile:<18} {slot:<15} -> {rec['mode']}",
                          flush=True)

    # ── B. 边界集（受控天气）──────────────────────────────────────
    rows_b: list[dict] = []
    for tag, agent in agents.items():
        for case in CASES:
            for profile in PROFILES:
                for regime in BOUNDARY_WEATHER:
                    task = {"task_id": f"PSB_{case['case_id']}_{profile}_{regime}_{tag}",
                            "origin": case["origin"], "destination": case["destination"],
                            "start_time": BOUNDARY_WINDOW[0], "end_time": BOUNDARY_WINDOW[1],
                            "travel_profile": profile, "pollution_source": POLLUTION_SOURCE,
                            "air_quality_risk": 0.94, "traffic_pressure_score": 0.74}
                    out = agent.run(task, meteorology_result=synthetic_meteo(regime))
                    rec = read_rec(out)
                    rows_b.append({"config": tag, "case": case["case_id"], "profile": profile,
                                   "weather": regime, **rec})
                    print(f"[B:{tag}] {case['case_id'][:12]:<12} {profile:<18} {regime:<11} -> {rec['mode']}",
                          flush=True)

    per_config: dict = {}
    detail: dict = {}
    for tag in agents:
        depart = group_flips([r for r in rows_a if r["config"] == tag], "slot")
        boundary = group_flips([r for r in rows_b if r["config"] == tag], "weather")
        per_config[tag] = {"departure_time_flips": depart["rate"],
                           "boundary_weather_flips": boundary["rate"]}
        detail[tag] = {"departure_time": depart, "boundary": boundary}
    payload = {
        "design": {
            "note": "mode_constant_minutes 置零（用户显式方式偏好先验）后重跑同一批场景",
            "sets": {"A": "出发时刻集（真实气象+真实空气质量，24 次/配置）",
                     "B": "边界集（受控天气，16 次/配置）"},
        },
        "per_config": per_config,
        "detail": detail,
        "rows_departure_time": rows_a,
        "rows_boundary": rows_b,
    }
    path = save_result("travel_pref_sensitivity", payload)
    print(f"\nsaved -> {path}")
    print("出发时刻翻转（默认配置 / 置零）:", per_config["default"]["departure_time_flips"],
          "/", per_config["zero_pref"]["departure_time_flips"])
    print("边界天气翻转（默认配置 / 置零）:", per_config["default"]["boundary_weather_flips"],
          "/", per_config["zero_pref"]["boundary_weather_flips"])
    print("时刻集翻转详情（默认）:", json.dumps(detail["default"]["departure_time"]["flips"], ensure_ascii=False))
    print("时刻集翻转详情（置零）:", json.dumps(detail["zero_pref"]["departure_time"]["flips"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
