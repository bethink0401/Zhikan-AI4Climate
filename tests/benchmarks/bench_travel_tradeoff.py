"""Benchmark v2: travel decisions on scenarios with REAL time-exposure tradeoffs.

The v1 experiment showed 85/90 cases where the fastest candidate was also the
cleanest (pollution source at the origin), leaving no room for multi-objective
selection. This pre-registered scenario set fixes that:

  * pollution source sits **mid-route** with a tight radius (1.2-1.5 km), so
    the direct corridor is exposed while bypass routes are clean but slower;
  * one cross-district case separates modes (rail bypasses the source area
    while driving crosses it);
  * two fresh dates that never participated in any tuning
    (dev reporting date 2023-10-18, independent test 2023-12-06);
  * exchange rates and all thresholds are ALREADY frozen from earlier rounds —
    nothing is calibrated here.

Usage: /usr/bin/python3 tests/benchmarks/bench_travel_tradeoff.py
"""
from __future__ import annotations

import statistics
import sys
import time as _time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

from common import save_result  # noqa: E402
from bench_travel_multigoal import (  # noqa: E402
    METHOD_NAMES,
    STATION,
    evaluate_row,
    summarise,
)

DATE_REPORT = "2023-10-18"   # 周三，新场景报告日（未参与任何调参）
DATE_TEST = "2023-12-06"     # 周四，独立测试日（未参与任何调参）

SLOTS = {
    "morning_07_09": ("07:00:00", "09:00:00"),
    "midday_12_14": ("12:00:00", "14:00:00"),
    "evening_18_20": ("18:00:00", "20:00:00"),
}

PROFILES = ["commuter", "parent_with_child", "elderly", "runner", "tourist"]

#: 预注册场景（跑之前固定，不因结果调整）：
#: 污染源放在 OD 连线中段、半径收紧到 1.2-1.5 km，制造"直达走廊高暴露 /
#: 绕行低暴露但更慢"的权衡；case 4 跨区且地铁绕开污染区、驾车穿越污染区。
CASES = [
    {"case_id": "jordan_to_prince_edward_midsource",
     "origin": {"name": "佐敦站", "lat": 22.3055, "lon": 114.1716},
     "destination": {"name": "太子站", "lat": 22.32441, "lon": 114.16828},
     "pollution_source": {"station_id": "YMT", "station_name": "Yau Ma Tei",
                          "lat": 22.31263, "lon": 114.17086, "radius_km": 1.2}},
    {"case_id": "prince_edward_to_jordan_midsource",
     "origin": {"name": "太子站", "lat": 22.32441, "lon": 114.16828},
     "destination": {"name": "佐敦站", "lat": 22.3055, "lon": 114.1716},
     "pollution_source": {"station_id": "MK", "station_name": "Mong Kok",
                          "lat": 22.3193, "lon": 114.1694, "radius_km": 1.2}},
    {"case_id": "mongkok_to_shamshuipo_midsource",
     "origin": {"name": "旺角站", "lat": 22.3193, "lon": 114.1694},
     "destination": {"name": "深水埗站", "lat": 22.3302, "lon": 114.1621},
     "pollution_source": {"station_id": "PW", "station_name": "Prince Edward West",
                          "lat": 22.3253, "lon": 114.1630, "radius_km": 1.2}},
    {"case_id": "shamshuipo_to_causewaybay_crossdistrict",
     "origin": {"name": "深水埗站", "lat": 22.3302, "lon": 114.1621},
     "destination": {"name": "铜锣湾站", "lat": 22.2801, "lon": 114.1850},
     "pollution_source": {"station_id": "MK", "station_name": "Mong Kok",
                          "lat": 22.3193, "lon": 114.1694, "radius_km": 1.5}},
]


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.travel_agent import TravelAgent

    travel = TravelAgent()
    air_agent = AirQualityAgent()

    conditions: dict[str, dict] = {}
    for date in (DATE_REPORT, DATE_TEST):
        for slot, (h0, h1) in SLOTS.items():
            start, end = f"{date} {h0}", f"{date} {h1}"
            meteo = meteorology_run({"task_id": f"TR_{date}_{slot}_meteo",
                                     "station_id": STATION["station_id"],
                                     "lat": STATION["lat"], "lon": STATION["lon"],
                                     "start_time": start, "end_time": end})
            air = air_agent.run({"task_id": f"TR_{date}_{slot}_air",
                                 "station_name": STATION["station_name"],
                                 "start_time": start, "end_time": end})
            mres = meteo.get("result") or {}
            ares = air.get("result") or {}
            key = f"{date}|{slot}"
            conditions[key] = {
                "start": start, "end": end, "meteo": meteo, "air": air,
                "wind_speed": mres.get("mean_wind_speed"),
                "wind_dir": mres.get("dominant_wind_direction"),
                "anomaly": ares.get("anomaly_score"),
            }
            print(f"[{key}] 风 {conditions[key]['wind_speed']} m/s @ "
                  f"{conditions[key]['wind_dir']}° | 异常分 {conditions[key]['anomaly']}", flush=True)

    all_rows = []
    for date in (DATE_REPORT, DATE_TEST):
        for slot in SLOTS:
            cond = conditions[f"{date}|{slot}"]
            for case in CASES:
                for profile in PROFILES:
                    task = {
                        "task_id": f"TR_{case['case_id']}_{profile}_{date}_{slot}",
                        "origin": case["origin"], "destination": case["destination"],
                        "start_time": cond["start"], "end_time": cond["end"],
                        "travel_profile": profile,
                        "pollution_source": case["pollution_source"],
                        "air_quality_risk": cond["anomaly"],
                    }
                    t0 = _time.perf_counter()
                    out = travel.run(task, meteorology_result=cond["meteo"],
                                     air_quality_result=cond["air"])
                    run_ms = (_time.perf_counter() - t0) * 1000.0
                    result = out.get("result") or {}
                    options = result.get("route_options") or []
                    row = evaluate_row(case, profile, slot, date, run_ms, options)
                    row["pollution_source"] = case["pollution_source"]["station_name"]
                    all_rows.append(row)
                    pick = (row.get("methods") or {}).get("C_multigoal") or {}
                    a = (row.get("methods") or {}).get("A_shortest_time") or {}
                    same = "同选" if (pick.get("route_id"), pick.get("mode")) == \
                        (a.get("route_id"), a.get("mode")) else "不同"
                    print(f"  {date} {slot[:7]} {case['case_id'][:24]:<24} {profile:<17} "
                          f"-> {pick.get('mode', '-'):>7} t={pick.get('duration_minutes', '-')} "
                          f"EU={pick.get('intake_index', '-')} [{same}]", flush=True)

    report_rows = [r for r in all_rows if r["date"] == DATE_REPORT]
    test_rows = [r for r in all_rows if r["date"] == DATE_TEST]
    report_summary = summarise(report_rows, f"新场景报告日 {DATE_REPORT}")
    test_summary = summarise(test_rows, f"独立测试日 {DATE_TEST}")

    scatter = []
    for row in test_rows:
        for name in METHOD_NAMES:
            entry = (row.get("methods") or {}).get(name) or {}
            if "duration_minutes" in entry:
                scatter.append({
                    "method": name, "case": row["case"], "profile": row["profile"],
                    "slot": row["slot"],
                    "duration_minutes": entry["duration_minutes"],
                    "intake_index": entry["intake_index"],
                })

    payload = {
        "design": {
            "note": "预注册权衡场景：污染源位于 OD 连线中段（半径 1.2-1.5 km），"
                    "直达走廊高暴露、绕行低暴露但更慢；case4 跨区且地铁绕开污染区。"
                    "交换率与全部阈值沿用此前轮次冻结值，本轮不调任何参数。",
            "dates": {"report": DATE_REPORT, "test_independent": DATE_TEST},
            "cases": [c["case_id"] for c in CASES],
            "profiles": PROFILES, "slots": list(SLOTS),
            "preference_rubric_note": "长者 allowed_modes 硬约束已在系统中生效"
                                      "（transit/walk 之外的方式不进入候选）。",
        },
        "conditions": {k: {kk: vv for kk, vv in v.items() if kk not in ("meteo", "air")}
                       for k, v in conditions.items()},
        "report_summary": report_summary,
        "test_summary": test_summary,
        "scatter_test": scatter,
        "rows": all_rows,
    }
    path = save_result("travel_tradeoff", payload)
    print(f"\nsaved -> {path}")
    print("\n=== 独立测试日汇总（2023-12-06）===")
    for name, s in test_summary["methods"].items():
        print(f"  {name}: 中位耗时={s['median_duration_min']}min 平均EU={s['mean_intake_EU']} "
              f"偏好={s['pref_satisfaction_rate']} 非支配={s['nondominated_rate']}")
    print(f"  A/B 同选率: {test_summary['ab_same_pick_rate']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
