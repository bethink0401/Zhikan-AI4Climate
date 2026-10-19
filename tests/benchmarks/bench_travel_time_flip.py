"""Benchmark: does the departure time change the recommendation?

The 16-run boundary experiment used one constant synthetic weather regime per
run, so the departure-time curve could not move the recommendation. This script
tests the departure-time layer with **real data only**:

  * for each departure slot on 2023-08-15 the script pulls the true hourly
    meteorology (Open-Meteo archive) and the true station air quality
    (EPD hourly records);
  * the travel planner is then run with that condition set;
  * if the recommended mode/route differs between slots, departure time is shown
    to participate in the decision — not merely in the dose numbers.

No synthetic weather is involved. Results feed section 5.2 of the proposal.

Usage: /usr/bin/python3 tests/benchmarks/bench_travel_time_flip.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

#: 真实日期上的三个出发时段（早/午/晚），全部使用该时段的真实观测
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

#: 污染源固定为旺角路边站，三时段一致，确保差异只来自时刻本身
POLLUTION_SOURCE = {"station_id": "MK", "station_name": "Mong Kok Roadside",
                    "lat": 22.3193, "lon": 114.1694, "radius_km": 3.0}
STATION = {"station_id": "mong_kok", "station_name": "Mong Kok Roadside",
           "lat": 22.3227, "lon": 114.1683}


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.travel_agent import TravelAgent

    travel = TravelAgent()
    air_agent = AirQualityAgent()

    conditions: dict[str, dict] = {}
    for slot, (start, end) in SLOTS.items():
        meteo = meteorology_run({"task_id": f"TIME_{slot}_meteo", "station_id": STATION["station_id"],
                                 "lat": STATION["lat"], "lon": STATION["lon"],
                                 "start_time": start, "end_time": end})
        air = air_agent.run({"task_id": f"TIME_{slot}_air", "station_name": STATION["station_name"],
                             "start_time": start, "end_time": end})
        mres = meteo.get("result") or {}
        ares = air.get("result") or {}
        conditions[slot] = {
            "start": start, "end": end, "meteo": meteo, "air": air,
            "wind_speed": mres.get("mean_wind_speed"),
            "wind_dir": mres.get("dominant_wind_direction"),
            "humidity": mres.get("mean_humidity"),
            "temperature": mres.get("mean_temperature"),
            "precipitation": mres.get("total_precipitation"),
            "anomaly": ares.get("anomaly_score"),
            "meteo_status": meteo.get("status"), "air_status": air.get("status"),
        }
        print(f"[{slot}] 风 {conditions[slot]['wind_speed']} m/s @ {conditions[slot]['wind_dir']}° | "
              f"湿度 {conditions[slot]['humidity']}% | 温 {conditions[slot]['temperature']}°C | "
              f"雨 {conditions[slot]['precipitation']} mm | 异常分 {conditions[slot]['anomaly']}", flush=True)

    rows = []
    for case in CASES:
        for profile in PROFILES:
            for slot in SLOTS:
                cond = conditions[slot]
                task = {
                    "task_id": f"TIMEFLIP_{case['case_id']}_{profile}_{slot}",
                    "origin": case["origin"], "destination": case["destination"],
                    "start_time": cond["start"], "end_time": cond["end"],
                    "travel_profile": profile,
                    "pollution_source": POLLUTION_SOURCE,
                    "air_quality_risk": cond["anomaly"],
                }
                out = travel.run(task, meteorology_result=cond["meteo"], air_quality_result=cond["air"])
                result = out.get("result") or {}
                options = result.get("route_options") or []
                rec = next((o for o in options if o.get("recommended")), None) or {}
                detail = rec.get("utility_detail") or {}
                rows.append({
                    "case": case["case_id"], "profile": profile, "slot": slot,
                    "wind_speed": cond["wind_speed"], "wind_dir": cond["wind_dir"],
                    "anomaly": cond["anomaly"], "status": out.get("status"),
                    "recommended_mode": rec.get("mode"),
                    "recommended_route": rec.get("route_id"),
                    "duration_minutes": rec.get("duration_minutes"),
                    "intake_index": rec.get("intake_index"),
                    "utility_minutes": rec.get("utility_minutes"),
                    "weather_added_minutes": detail.get("weather_added_minutes"),
                    "pollution_added_minutes": detail.get("pollution_added_minutes"),
                })
                print(f"  {case['case_id'][:12]:<12} {profile:<18} {slot:<15} -> {rec.get('mode')} "
                      f"(U={rec.get('utility_minutes')})", flush=True)

    # 每个（OD × 画像）组合：推荐是否随出发时段变化
    flips = []
    for case in CASES:
        for profile in PROFILES:
            group = [r for r in rows if r["case"] == case["case_id"] and r["profile"] == profile]
            modes = {r["slot"]: r["recommended_mode"] for r in group}
            routes = {r["slot"]: r["recommended_route"] for r in group}
            if len(set(modes.values())) > 1 or len(set(routes.values())) > 1:
                flips.append({"case": case["case_id"], "profile": profile, "modes": modes, "routes": routes})

    combos = len(CASES) * len(PROFILES)
    payload = {
        "design": {
            "date": "2023-08-15", "slots": list(SLOTS),
            "cases": [c["case_id"] for c in CASES], "profiles": PROFILES,
            "note": "真实数据：逐时段 Open-Meteo 历史气象 + EPD 逐小时空气质量；无合成天气",
        },
        "conditions": {k: {kk: vv for kk, vv in v.items() if kk not in ("meteo", "air")}
                       for k, v in conditions.items()},
        "rows": rows,
        "combinations": combos,
        "time_flip_combinations": len(flips),
        "time_flip_rate": f"{len(flips)}/{combos}",
        "flips": flips,
        "mode_by_slot": {
            f"{c['case_id']}|{p}": {r["slot"]: r["recommended_mode"]
                                    for r in rows if r["case"] == c["case_id"] and r["profile"] == p}
            for c in CASES for p in PROFILES},
    }
    path = save_result("travel_time_flip", payload)
    print(f"\nsaved -> {path}")
    print(f"时段导致的推荐变化: {payload['time_flip_rate']}")
    for f in flips:
        print(f"  {f['case']} / {f['profile']}: {f['modes']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
