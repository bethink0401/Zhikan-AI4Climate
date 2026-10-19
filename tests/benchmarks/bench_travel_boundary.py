"""Benchmark: travel boundary scenarios + three-baseline comparison.

Goal 1 — find a case where the recommendation actually FLIPS: the stored
20-run case set always recommends driving, so the pollution/weather terms
never change the winner. This script probes short inner-city trips inside
a high-exposure area, across profiles and two synthetic weather regimes
(withheld by construction: no network calls), and reports whether the
recommended mode/route changes with profile and weather.

Goal 2 — three-baseline comparison for every run:
  shortest-time route / lowest-exposure route / composite-utility route,
each with duration, intake and utility, so it is visible that the
recommendation is a trade-off rather than a single-criterion pick.

Usage: /usr/bin/python3 tests/benchmarks/bench_travel_boundary.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

# 短途 / 高暴露：旺角路边站走廊内，步行耗时与驾车接近，留给方式选择空间
CASES = [
    {"case_id": "mongkok_to_prince_edward", "origin": {"name": "旺角站", "lat": 22.3193, "lon": 114.1694},
     "destination": {"name": "太子站", "lat": 22.32441, "lon": 114.16828}},
    {"case_id": "cwb_to_wanchai", "origin": {"name": "铜锣湾站", "lat": 22.2801, "lon": 114.1850},
     "destination": {"name": "湾仔站", "lat": 22.27758, "lon": 114.17334}},
]
PROFILES = ["commuter", "runner", "elderly", "parent_with_child"]

POLLUTION_SOURCE = {"station_id": "MK", "station_name": "Mong Kok Roadside",
                    "lat": 22.3193, "lon": 114.1694, "radius_km": 3.0}

WINDOW = ("2023-08-15 08:00:00", "2023-08-15 11:00:00")


def synthetic_meteo(mode: str) -> dict:
    """Controlled weather: 'mild' vs 'heavy_rain'; no network involved."""
    hours = [f"2023-08-15 0{h}:00:00" for h in (8, 9, 10)]
    if mode == "mild":
        precip = [0.0, 0.0, 0.0]
        result = {"mean_temperature": 26.5, "mean_humidity": 68.0,
                  "total_precipitation": 0.0, "mean_wind_speed": 2.6,
                  "dominant_wind_direction": 250.0, "dominant_wind_direction_label": "WSW",
                  "downwind_direction": 70.0, "upwind_direction": 250.0,
                  "weather_risk": 0.18, "risk_type": "low", "dispersion_condition": "有利扩散"}
    else:
        precip = [5.2, 6.4, 3.1]
        result = {"mean_temperature": 29.8, "mean_humidity": 93.0,
                  "total_precipitation": 14.7, "mean_wind_speed": 1.1,
                  "dominant_wind_direction": 205.0, "dominant_wind_direction_label": "SSW",
                  "downwind_direction": 25.0, "upwind_direction": 205.0,
                  "weather_risk": 0.78, "risk_type": "high", "dispersion_condition": "一般"}
    result["hourly_series"] = [
        {"time": t, "temperature": result["mean_temperature"], "humidity": result["mean_humidity"],
         "precipitation": p, "wind_speed": result["mean_wind_speed"], "wind_direction": 250.0}
        for t, p in zip(hours, precip)
    ]
    return {"agent_name": "MeteorologyAgent", "status": "success", "result": result}


def option_row(opt: dict) -> dict:
    detail = opt.get("utility_detail") or {}
    return {
        "route_id": opt.get("route_id"), "mode": opt.get("mode"),
        "duration_minutes": opt.get("duration_minutes"), "intake_index": opt.get("intake_index"),
        "utility_minutes": opt.get("utility_minutes"),
        "pollution_added_minutes": detail.get("pollution_added_minutes"),
        "weather_added_minutes": detail.get("weather_added_minutes"),
    }


def main() -> int:
    from agents.travel_agent import TravelAgent

    agent = TravelAgent()
    rows = []
    flips = 0
    for case in CASES:
        for profile in PROFILES:
            for weather in ("mild", "heavy_rain"):
                task = {
                    "task_id": f"BOUND_{case['case_id']}_{profile}_{weather}",
                    "origin": case["origin"], "destination": case["destination"],
                    "start_time": WINDOW[0], "end_time": WINDOW[1],
                    "travel_profile": profile,
                    "pollution_source": POLLUTION_SOURCE,
                    "air_quality_risk": 0.94, "traffic_pressure_score": 0.74,
                }
                out = agent.run(task, meteorology_result=synthetic_meteo(weather))
                result = out.get("result") or {}
                options = [o for o in (result.get("route_options") or [])
                           if o.get("utility_minutes") is not None]
                if len(options) < 2:
                    continue
                rec = next((o for o in options if o.get("recommended")),
                           min(options, key=lambda o: o["utility_minutes"]))
                base_time = min(options, key=lambda o: o.get("duration_minutes") or 1e9)
                base_expo = min(options, key=lambda o: o.get("intake_index") or 1e9)

                def no_term(term):
                    return min(options, key=lambda o: o["utility_minutes"]
                               - (o.get("utility_detail") or {}).get(term, 0.0))
                p_flip = no_term("pollution_added_minutes")["route_id"] != rec["route_id"]
                w_flip = no_term("weather_added_minutes")["route_id"] != rec["route_id"]
                flips += int(p_flip or w_flip)

                # 单目标基线相对推荐的效用遗憾（正值 = 基线比推荐差多少分钟）
                regret_time = round(base_time["utility_minutes"] - rec["utility_minutes"], 3)
                regret_expo = round(base_expo["utility_minutes"] - rec["utility_minutes"], 3)
                # Pareto：是否存在同时在时长与吸入上都不劣于推荐、且至少一项严格更优的候选
                dominated = [o for o in options
                             if (o.get("duration_minutes") or 1e9) <= (rec.get("duration_minutes") or 1e9)
                             and (o.get("intake_index") or 1e9) <= (rec.get("intake_index") or 1e9)
                             and ((o.get("duration_minutes") or 0) < (rec.get("duration_minutes") or 0)
                                  or (o.get("intake_index") or 0) < (rec.get("intake_index") or 0))]
                curve = result.get("departure_dose_curve") or {}
                rng = curve.get("intake_range") or {}
                rows.append({
                    "case": case["case_id"], "profile": profile, "weather": weather,
                    "recommended": option_row(rec),
                    "shortest_time": option_row(base_time),
                    "lowest_exposure": option_row(base_expo),
                    "utility_regret_vs_time_baseline": regret_time,
                    "utility_regret_vs_exposure_baseline": regret_expo,
                    "pareto_dominated_by": [o.get("route_id") for o in dominated],
                    "rec_on_pareto_front": not dominated,
                    "n_options": len(options),
                    "departure_curve": {
                        "intake_reduction_pct": rng.get("reduction_pct"),
                        "recommendation_changes_with_time": curve.get("recommendation_changes_with_time"),
                        "best_window": curve.get("best_window"),
                        "worst_window": curve.get("worst_window"),
                    },
                    "rec_equals_shortest_time": base_time["route_id"] == rec["route_id"],
                    "rec_equals_lowest_exposure": base_expo["route_id"] == rec["route_id"],
                    "removing_pollution_flips": p_flip,
                    "removing_weather_flips": w_flip,
                    "rec_mode_label": rec.get("mode_label"),
                })

    import statistics as _st
    regrets_t = [r["utility_regret_vs_time_baseline"] for r in rows]
    regrets_e = [r["utility_regret_vs_exposure_baseline"] for r in rows]
    utility_wins = sum(1 for r in rows
                       if r["utility_regret_vs_time_baseline"] >= 0
                       and r["utility_regret_vs_exposure_baseline"] >= 0)
    payload = {
        "summary": {
            "utility_wins_vs_both_baselines": f"{utility_wins}/{len(rows)}",
            "mean_regret_vs_time_baseline_min": round(_st.mean(regrets_t), 3),
            "mean_regret_vs_exposure_baseline_min": round(_st.mean(regrets_e), 3),
            "max_regret_vs_time_baseline_min": round(max(regrets_t), 3),
            "max_regret_vs_exposure_baseline_min": round(max(regrets_e), 3),
            "rec_on_pareto_front": f"{sum(1 for r in rows if r['rec_on_pareto_front'])}/{len(rows)}",
            "mean_candidate_count": round(_st.mean([r["n_options"] for r in rows]), 1),
            "time_flip_runs": sum(1 for r in rows
                                  if r["departure_curve"].get("recommendation_changes_with_time")),
            "departure_reduction_pct_values": [r["departure_curve"].get("intake_reduction_pct") for r in rows],
        },
        "design": {
            "cases": [c["case_id"] for c in CASES],
            "profiles": PROFILES,
            "weather_regimes": ["mild", "heavy_rain"],
            "note": "合成气象按构造提供，无网络调用；污染源固定在旺角路边站 3km",
        },
        "runs": len(rows),
        "runs_with_any_term_flip": flips,
        "mode_distribution": {m: sum(1 for r in rows if r["recommended"]["mode"] == m)
                              for m in {r["recommended"]["mode"] for r in rows}},
        "rows": rows,
    }
    path = save_result("travel_boundary", payload)
    print(f"saved -> {path}")
    print(f"runs={len(rows)}  term-removal flips={flips}")
    print("recommended mode distribution:", payload["mode_distribution"])
    for r in rows:
        rec, st, le = r["recommended"], r["shortest_time"], r["lowest_exposure"]
        print(f"  {r['case'][:22]:22s} {r['profile']:17s} {r['weather']:10s} "
              f"-> {rec['mode']:8s} rec={rec['utility_minutes']:7.2f} "
              f"(time-opt {st['utility_minutes']:7.2f}, expo-opt {le['utility_minutes']:7.2f}) "
              f"={r['rec_equals_shortest_time'] and 'time' or ''}{r['rec_equals_lowest_exposure'] and 'expo' or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
