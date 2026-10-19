"""V9.2 Experiment: innovation-1 point-in-time (forward-chaining) backtest.

Per the V9.2 plan: all baselines and the system use ONLY data available
before each event (no full-year statistics), removing the look-ahead bias
identified in the V9.1 audit. Rule labels remain the full-year proxy labels
(documented limitation: no true source ground truth exists).

Design:
  - Year 2024, three roadside stations, same sampling plan as the V9 holdout
    (seed 2024 -> identical windows, so full-year vs PIT is a paired
    comparison);
  - System runs with task.point_in_time=true: multi-station p90 baselines and
    the NOx-year-percentile gate use only data strictly before the event
    month; thresholds stay frozen from V8;
  - A / B / B+ baseline features recomputed with PIT populations;
  - Labels: full-year rule labels (proxy) — the comparison is therefore
    "agreement with proxy labels under real-time data availability";
  - Unified coverage reporting (abstention rate) alongside Macro-F1.

Usage: /usr/bin/python3 tests/benchmarks/v9/governance_pit.py
"""
from __future__ import annotations

import csv
import json
import random
import sqlite3
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(HERE.parent))

from common import save_result  # noqa: E402
from metrics import clustered_paired_diff, confusion, macro_f1  # noqa: E402
import bench_governance_v6 as gv6  # noqa: E402
from governance_holdout import SAMPLE_PLAN, method_bplus  # noqa: E402

YEAR = 2024


def pit_pit_filter(year: int, as_of: str) -> tuple[str, str]:
    """PIT 数据窗口：滚动 12 个月至事件月月初（严格早于事件）。"""
    import datetime as dt
    pit_hi = f"{str(as_of)[:7]}-01 00:00:00"
    pit_lo = (dt.datetime.fromisoformat(pit_hi) - dt.timedelta(days=365)
              ).isoformat(sep=" ")
    return pit_lo, pit_hi


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents import meteorology_agent as met
    from agents.spatial_agent import SpatialAgent
    from agents.source_agent import SourceAgent
    from agents.decison_agent import run as decision_run
    from agents.report_agent import ReportAgent
    from agents.traffic_agent import TrafficAgent

    conn = sqlite3.connect(gv6.DB_PATH)
    agents = {"air": AirQualityAgent(), "meteo": met.run, "traffic": TrafficAgent(),
              "spatial": SpatialAgent(), "source": SourceAgent(),
              "decision": decision_run, "report": ReportAgent()}

    all_rows = []
    for station in gv6.STATIONS:
        print(f"=== {station['name']} / {YEAR}（PIT 前向回测） ===", flush=True)
        wind = gv6.load_annual_wind(YEAR, station["lat"], station["lon"])
        candidates = gv6.build_windows(conn, station["db_name"], YEAR, wind)
        rng = random.Random(2024)   # 与 V9 holdout 相同种子 → 同一批窗口
        picked = []
        for label, count in SAMPLE_PLAN.items():
            if label == "weekend_negative":
                pool = [c for c in candidates if not c["is_weekday"]]
            else:
                pool = [c for c in candidates if c["label"] == label and c["is_weekday"]]
            rng.shuffle(pool)
            for item in pool[:count]:
                row = dict(item)
                row["sample_group"] = label
                row["station"] = station["name"]
                picked.append(row)
        print(f"  抽样 {len(picked)} 窗（与 V9 holdout 同批）", flush=True)

        for index, window in enumerate(picked):
            start = window["start_time"]
            pit_lo, pit_hi = pit_pit_filter(YEAR, start)
            full_task = {"task_id": f"VPIT_{station['id']}_{window['date']}_{window['start_hour']}",
                         "station_name": station["name"], "station_id": station["id"],
                         "lat": station["lat"], "lon": station["lon"],
                         "start_time": window["start_time"],
                         "end_time": window["end_time"],
                         "point_in_time": True}
            air = agents["air"].run(dict(full_task))
            meteo = agents["meteo"](dict(full_task))
            traffic = agents["traffic"].run(dict(full_task))
            spatial = agents["spatial"].run(dict(full_task), meteo)
            source = agents["source"].run({"task_id": full_task["task_id"],
                                           "station_name": station["name"]},
                                          air_result=air, meteo_result=meteo,
                                          traffic_result=traffic, spatial_result=spatial)
            sres = source.get("result") or {}
            judgement = (sres.get("main_judgement") or "").lower()
            if "traffic" in judgement:
                c_label = "traffic_dominant"
            elif "regional" in judgement:
                c_label = "regional_transport"
            elif "accumulation" in judgement or "local" in judgement:
                c_label = "local_accumulation"
            else:
                c_label = "uncertain"

            # A/B/B+ 的分位特征全部改用 PIT 总体（与系统同数据权限）
            nox_pop = sorted(v for (v,) in conn.execute(
                "SELECT nox FROM air_quality_hourly WHERE station_name=? "
                "AND nox IS NOT NULL AND time >= ? AND time < ?",
                (station["db_name"], pit_lo, pit_hi)))
            no2_pop = sorted(v for (v,) in conn.execute(
                "SELECT no2 FROM air_quality_hourly WHERE station_name=? "
                "AND no2 IS NOT NULL AND time >= ? AND time < ?",
                (station["db_name"], pit_lo, pit_hi)))
            nox_pct = statistics.mean([1.0 if v < window["mean_nox"] else 0.0
                                       for v in nox_pop]) if nox_pop else 0.0
            no2_pct = statistics.mean([1.0 if v < window["mean_no2"] else 0.0
                                       for v in no2_pop]) if no2_pop else 0.0
            if nox_pct >= 0.80:
                a_label = "traffic_dominant"
            elif no2_pct >= 0.80:
                a_label = "local_accumulation"
            else:
                a_label = "uncertain"

            from tools.traffic_tools import query_vehicle_mix
            b_label = gv6.method_b_v6(window, station, nox_pop, no2_pop,
                                      _traffic_conn(), _atc_ids(station), YEAR, wind)
            bplus_label = method_bplus(window, station, nox_pop, no2_pop, conn)

            all_rows.append({
                "station": station["name"], "date": window["date"],
                "start_hour": window["start_hour"], "label": window["label"],
                "sample_group": window.get("sample_group"),
                "A": a_label, "B": b_label, "Bplus": bplus_label, "C": c_label,
                "source_judgement": sres.get("main_judgement"),
            })
            if (index + 1) % 20 == 0:
                print(f"  [{index+1}/{len(picked)}]", flush=True)

    conn.close()
    _close_traffic_conn()

    truths = [r["label"] for r in all_rows]
    methods = ("A", "B", "Bplus", "C")
    point = {m: round(macro_f1(truths, [r[m] for r in all_rows]), 4) for m in methods}
    clusters = [r["date"] for r in all_rows]

    # Macro-F1 差的聚类 bootstrap（V9.1 修正后的正确口径）
    units = {}
    for i, c in enumerate(clusters):
        units.setdefault(c, []).append(i)
    unit_ids = sorted(units)
    rng = random.Random(42)

    def f1_diff_ci(m1, m2, reps=10000):
        samples = []
        for _ in range(reps):
            chosen = sorted(i for u in (unit_ids[rng.randrange(len(unit_ids))]
                                        for _ in range(len(unit_ids)))
                            for i in units[u])
            if chosen:
                t = [truths[i] for i in chosen]
                samples.append(macro_f1(t, [all_rows[i][m1] for i in chosen])
                               - macro_f1(t, [all_rows[i][m2] for i in chosen]))
        samples.sort()
        point_diff = macro_f1(truths, [r[m1] for r in all_rows]) \
                     - macro_f1(truths, [r[m2] for r in all_rows])
        return {"metric": "macro_f1_diff", "mean_diff": round(point_diff, 4),
                "ci95": [round(samples[int(0.025 * reps)], 4),
                         round(samples[int(0.975 * reps)], 4)],
                "n": len(all_rows), "n_clusters": len(unit_ids)}

    ci = {m: f1_diff_ci(m, "B") for m in ("C", "Bplus")}

    # 弃判覆盖率
    abstention = {m: round(sum(1 for r in all_rows if r[m] == "uncertain")
                           / len(all_rows), 4) for m in methods}
    weak = [r for r in all_rows if r["sample_group"] == "weekend_negative"]
    weak_traffic = {m: sum(1 for r in weak if r[m] == "traffic_dominant")
                    for m in methods}

    payload = {
        "design": {
            "note": "V9.2 前向回测：系统 task.point_in_time=true（多站 p90 基线与 NOx "
                    "分位门控只用事件月之前数据）；A/B/B+ 特征同 PIT 总体；标注仍为"
                    "全年代理标签（proxy，如实声明）；阈值 = V8 冻结值零调参",
            "pit_definition": "基线数据窗口 = 当年年初至事件月月初（严格早于事件日）",
            "sample_seed": 2024,
        },
        "point_f1_main_labels": point,
        "clustered_ci_vs_B": ci,
        "abstention_rate": abstention,
        "weak_evidence_traffic_misfire": weak_traffic,
        "confusion": {m: confusion(truths, [r[m] for r in all_rows])
                      for m in methods},
        "label_distribution": dict(__import__("collections").Counter(
            r["label"] for r in all_rows)),
    }
    path = save_result("v9_governance_pit", payload)
    print(f"\nsaved -> {path}")
    print("=== PIT 前向回测（2024，代理标签）===")
    for m in methods:
        print(f"  {m}: F1={point[m]} 弃判率={abstention[m]} 弱证据误判={weak_traffic[m]}/{len(weak)}")
    for m in ("C", "Bplus"):
        print(f"  {m}−B: {ci[m]}")

    with (HERE / "results" / "v9_governance_pit_cases.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)
    return 0


_TRAFFIC = {}


def _traffic_conn():
    if "conn" not in _TRAFFIC:
        from tools.traffic_tools import open_db as open_traffic_db
        _TRAFFIC["conn"] = open_traffic_db()
    return _TRAFFIC["conn"]


def _atc_ids(station):
    if station["name"] not in _TRAFFIC:
        from tools.traffic_tools import get_atc_station_ids
        _TRAFFIC[station["name"]] = get_atc_station_ids(station["name"],
                                                        include_nearby=False)
    return _TRAFFIC[station["name"]]


def _close_traffic_conn():
    if "conn" in _TRAFFIC:
        _TRAFFIC["conn"].close()


if __name__ == "__main__":
    sys.exit(main())
