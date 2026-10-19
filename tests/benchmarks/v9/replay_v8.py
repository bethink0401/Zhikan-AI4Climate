"""V9-P0: replay the V8 2x2 ablation and locate the 3/60 offline-vs-online
recommendation mismatches field by field.

Protocol (per the V9 work order):
  1. Re-run the exact V8 blind date (2023-11-29) cases with identical inputs.
  2. For every case compare the ONLINE system recommendation (TravelAgent's
     `recommended` flag) against the OFFLINE D-arm decision (constraint filter
     -> Pareto non-dominated -> min utility) used in the ablation.
  3. For mismatches dump a readable field-level diff: candidate sets, ordering,
     tie-breaking, floating point, constraint application.
  4. Fix evaluator/logic defects only (no utility tuning); add a regression
     test; accept only when online == offline 60/60 under identical inputs.

Exports: results/v9_replay_diff.csv, results/v9_replay_trace.jsonl

Usage: /usr/bin/python3 tests/benchmarks/v9/replay_v8.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import statistics
import sys
import time as _time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(HERE.parent))

from common import save_result  # noqa: E402
from bench_travel_multigoal import (  # noqa: E402
    STATION,
    _compact,
)
from bench_travel_tradeoff import CASES, PROFILES, SLOTS  # noqa: E402
from bench_travel_v8 import decision_zhikan  # noqa: E402

DATE_BLIND = "2023-11-29"
CAP_RATIO = 1.25


def candidate_set_hash(candidates: list[dict]) -> str:
    payload = json.dumps(
        [[c.get("mode"), c.get("route_id"), round(c["duration_minutes"], 6),
          round(c["intake_index"], 6), round(c.get("utility_minutes") or 0.0, 6)]
         for c in candidates], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.travel_agent import TravelAgent

    travel = TravelAgent()
    air_agent = AirQualityAgent()

    conditions = {}
    for slot, (h0, h1) in SLOTS.items():
        start, end = f"{DATE_BLIND} {h0}", f"{DATE_BLIND} {h1}"
        meteo = meteorology_run({"task_id": f"V9R_{slot}_meteo",
                                 "station_id": STATION["station_id"],
                                 "lat": STATION["lat"], "lon": STATION["lon"],
                                 "start_time": start, "end_time": end})
        air = air_agent.run({"task_id": f"V9R_{slot}_air",
                             "station_name": STATION["station_name"],
                             "start_time": start, "end_time": end})
        conditions[slot] = {"start": start, "end": end, "meteo": meteo, "air": air,
                            "anomaly": (air.get("result") or {}).get("anomaly_score")}

    trace_rows = []
    diff_rows = []
    n_match = 0
    for slot in SLOTS:
        cond = conditions[slot]
        for case in CASES:
            for profile in PROFILES:
                task = {
                    "task_id": f"V9R_{case['case_id']}_{profile}_{slot}",
                    "origin": case["origin"], "destination": case["destination"],
                    "start_time": cond["start"], "end_time": cond["end"],
                    "travel_profile": profile,
                    "pollution_source": case["pollution_source"],
                    "air_quality_risk": cond["anomaly"],
                }
                out = travel.run(dict(task), meteorology_result=cond["meteo"],
                                 air_quality_result=cond["air"])
                enhanced = [_compact(o) for o in (out.get("result") or {})
                            .get("route_options") or []]
                enhanced = [c for c in enhanced
                            if isinstance(c["duration_minutes"], float)
                            and isinstance(c["intake_index"], float)]
                if not enhanced:
                    continue
                cap = CAP_RATIO * min(c["intake_index"] for c in enhanced)
                # 关键：V8 消融的评测缺陷是拿"带 cap 的离线 D 臂"对比"未传约束的
                # 系统推荐"。正确对齐 = 系统也以 user_constraints 接收同一 cap
                # （与 V6/V7 基准的在线口径一致），再比较两者推荐。
                out_c = travel.run({**task,
                                    "user_constraints": {"max_intake": round(cap, 4)}},
                                   meteorology_result=cond["meteo"],
                                   air_quality_result=cond["air"])
                enhanced_c = [_compact(o) for o in (out_c.get("result") or {})
                              .get("route_options") or []]
                enhanced_c = [c for c in enhanced_c
                              if isinstance(c["duration_minutes"], float)
                              and isinstance(c["intake_index"], float)]
                system_pick = next((c for c in enhanced_c if c.get("recommended")), None)
                offline_pick = decision_zhikan(enhanced, cap)

                match = bool(
                    system_pick is not None and offline_pick is not None
                    and system_pick.get("route_id") == offline_pick.get("route_id")
                    and system_pick.get("mode") == offline_pick.get("mode"))
                n_match += int(match)

                feasible = [c for c in enhanced if c["intake_index"] <= cap]
                trace = {
                    "task_id": task["task_id"], "case": case["case_id"],
                    "profile": profile, "slot": slot,
                    "inputs_hash": hashlib.sha256(json.dumps(
                        {k: task[k] for k in ("origin", "destination", "start_time",
                                              "end_time", "travel_profile",
                                              "pollution_source", "air_quality_risk")},
                        ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:12],
                    "config_hash": hashlib.sha256(
                        (BASE / "config" / "travel_profile.json").read_bytes()).hexdigest()[:12],
                    "candidate_set_hash": candidate_set_hash(enhanced),
                    "n_candidates": len(enhanced), "n_feasible": len(feasible),
                    "cap": round(cap, 4),
                    "system_pick": None if system_pick is None else {
                        "mode": system_pick.get("mode"),
                        "route_id": system_pick.get("route_id"),
                        "utility_minutes": system_pick.get("utility_minutes"),
                        "duration_minutes": system_pick["duration_minutes"],
                        "intake_index": system_pick["intake_index"]},
                    "offline_pick": None if offline_pick is None else {
                        "mode": offline_pick.get("mode"),
                        "route_id": offline_pick.get("route_id"),
                        "utility_minutes": offline_pick.get("utility_minutes"),
                        "duration_minutes": offline_pick["duration_minutes"],
                        "intake_index": offline_pick["intake_index"]},
                    "match": match,
                }
                if not match:
                    # 字段级差异：两候选项的全字段对比 + 可行集效用排序
                    def flat(c: dict) -> dict:
                        return {k: (round(v, 6) if isinstance(v, float) else v)
                                for k, v in c.items()
                                if k in ("mode", "route_id", "duration_minutes",
                                         "intake_index", "utility_minutes",
                                         "outdoor_equivalent_minutes",
                                         "weather_added_minutes",
                                         "pollution_added_minutes",
                                         "mode_constant_minutes",
                                         "overrun_minutes", "walk_overrun_minutes",
                                         "rank", "recommended")}
                    sorted_feasible = sorted(
                        feasible, key=lambda c: (c.get("utility_minutes") or 0.0,
                                                 c["duration_minutes"]))
                    trace["mismatch_analysis"] = {
                        "system_pick_full": flat(system_pick),
                        "offline_pick_full": flat(offline_pick),
                        "utility_gap_minutes": round(
                            (offline_pick.get("utility_minutes") or 0.0)
                            - (system_pick.get("utility_minutes") or 0.0), 6),
                        "nondominated_utilities_sorted": [
                            (c.get("route_id"), round(c.get("utility_minutes") or 0.0, 6),
                             c["duration_minutes"]) for c in sorted_feasible[:6]],
                        "offline_candidates_in_original_order": [
                            (c.get("route_id"), round(c.get("utility_minutes") or 0.0, 6))
                            for c in feasible[:6]],
                    }
                    diff_rows.append({
                        "task_id": task["task_id"], "case": case["case_id"],
                        "profile": profile, "slot": slot,
                        "system_route": system_pick.get("route_id"),
                        "offline_route": offline_pick.get("route_id") if offline_pick else None,
                        "system_utility": system_pick.get("utility_minutes"),
                        "offline_utility": (offline_pick.get("utility_minutes")
                                            if offline_pick else None),
                        "utility_gap": trace["mismatch_analysis"]["utility_gap_minutes"],
                        "n_feasible": len(feasible),
                    })
                trace_rows.append(trace)

    match_rate = f"{n_match}/{len(trace_rows)}"
    print(f"\n回放一致性: {match_rate}")
    for row in diff_rows:
        print(f"  不一致: {row['task_id']} system={row['system_route']} "
              f"({row['system_utility']}) offline={row['offline_route']} "
              f"({row['offline_utility']}) gap={row['utility_gap']}")

    with (HERE / "results" / "v9_replay_diff.csv").open("w", newline="",
                                                        encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "task_id", "case", "profile", "slot", "system_route", "offline_route",
            "system_utility", "offline_utility", "utility_gap", "n_feasible"])
        writer.writeheader()
        writer.writerows(diff_rows)
    with (HERE / "results" / "v9_replay_trace.jsonl").open("w",
                                                           encoding="utf-8") as handle:
        for row in trace_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    payload = {
        "design": {"blind_date": DATE_BLIND, "cap_ratio": CAP_RATIO,
                   "note": "V9-P0 回放：与 V8 完全相同的输入重跑，比较系统推荐与离线 D 臂决策"},
        "match": match_rate, "n_mismatch": len(diff_rows),
        "mismatches": diff_rows,
    }
    path = save_result("v9_replay", payload)
    print(f"saved -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
