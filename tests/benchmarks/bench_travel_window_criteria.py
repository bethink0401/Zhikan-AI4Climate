"""Benchmark: departure-window criteria — dose-optimal vs utility-optimal.

The departure curve re-scores every candidate option at each hour with the same
composite utility (including time-varying driving congestion), so each hour
already has a utility-optimal plan. This script asks a sharper question: is the
hour that minimises **inhaled dose** the same as the hour that minimises
**composite utility**? The two are reported separately so the "route x mode x
time" optimisation claim can be checked against data instead of prose.

Real hourly meteorology (Open-Meteo archive) and real station air quality are
used; the pollution source is fixed.

Usage: /usr/bin/python3 tests/benchmarks/bench_travel_window_criteria.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

WINDOW = ("2023-08-15 08:00:00", "2023-08-15 16:00:00")
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


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.travel_agent import TravelAgent

    travel = TravelAgent()
    meteo = meteorology_run({"task_id": "WINDOW_meteo", "station_id": STATION["station_id"],
                             "lat": STATION["lat"], "lon": STATION["lon"],
                             "start_time": WINDOW[0], "end_time": WINDOW[1]})
    air = AirQualityAgent().run({"task_id": "WINDOW_air", "station_name": STATION["station_name"],
                                 "start_time": WINDOW[0], "end_time": WINDOW[1]})
    risk = (air.get("result") or {}).get("anomaly_score")

    rows = []
    for case in CASES:
        for profile in PROFILES:
            out = travel.run(
                {"task_id": f"WINDOW_{case['case_id']}_{profile}",
                 "origin": case["origin"], "destination": case["destination"],
                 "start_time": WINDOW[0], "end_time": WINDOW[1],
                 "travel_profile": profile, "pollution_source": POLLUTION_SOURCE,
                 "air_quality_risk": risk},
                meteorology_result=meteo, air_quality_result=air)
            curve = (out.get("result") or {}).get("departure_dose_curve") or {}
            if not curve.get("available"):
                print(f"  {case['case_id'][:12]:<12} {profile:<18} 曲线不可用", flush=True)
                continue
            rows.append({
                "case": case["case_id"], "profile": profile,
                "points": len(curve.get("points") or []),
                "dose_optimal_window": curve.get("best_window"),
                "dose_optimal_eu": (curve.get("intake_range") or {}).get("min"),
                "utility_optimal_window": curve.get("best_utility_window"),
                "utility_optimal_minutes": curve.get("best_utility_minutes"),
                "utility_optimal_mode": curve.get("best_utility_mode"),
                "criteria_agree": curve.get("best_utility_matches_best_window"),
                "recommendation_changes_with_time": curve.get("recommendation_changes_with_time"),
            })
            print(f"  {case['case_id'][:12]:<12} {profile:<18} "
                  f"剂量最优 {curve.get('best_window')} vs 效用最优 {curve.get('best_utility_window')} | "
                  f"一致={curve.get('best_utility_matches_best_window')}", flush=True)

    agree = sum(1 for r in rows if r["criteria_agree"])
    switched = sum(1 for r in rows if r["recommendation_changes_with_time"])
    payload = {
        "design": {"window": WINDOW, "cases": [c["case_id"] for c in CASES],
                   "profiles": PROFILES,
                   "note": "每个时段均以统一效用对候选路线×方式重新评分（含随小时拥堵变化的驾车耗时）；"
                           "本实验对比『吸入当量最小』与『统一效用最小』两种口径选出的出发时段"},
        "combinations": len(rows),
        "criteria_agree_rate": f"{agree}/{len(rows)}",
        "recommendation_changes_with_time_rate": f"{switched}/{len(rows)}",
        "rows": rows,
    }
    path = save_result("travel_window_criteria", payload)
    print(f"\nsaved -> {path}")
    print(f"两种口径选出的时段一致: {payload['criteria_agree_rate']}")
    print(f"不同时段推荐方案发生变化: {payload['recommendation_changes_with_time_rate']}")
    for r in rows:
        if not r["criteria_agree"]:
            print(f"  不一致: {r['case']} / {r['profile']} | 剂量最优 {r['dose_optimal_window']}"
                  f" vs 效用最优 {r['utility_optimal_window']}（{r['utility_optimal_mode']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
