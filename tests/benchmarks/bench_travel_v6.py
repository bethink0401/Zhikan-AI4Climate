"""Benchmark V6: explicit constraint-first optimisation, all methods under the
same constraints.

Per the V6 plan: the system now supports user constraints (exposure cap /
time budget) as hard constraints satisfied BEFORE utility optimisation, with
explicit degradation when nothing fits. This benchmark:

  1. fixes the constraint rule a priori: exposure cap = 1.25 × (best EU in
     the candidate set) — same for every method and every case;
  2. runs the pre-registered trade-off scenarios on a fresh blind date
     (2023-10-25, never seen in any round);
  3. compares, under the SAME constraints:
       A_c  fastest constraint-feasible candidate
       B_c  lowest-intake constraint-feasible candidate
       C    the system (constraints passed in-task, Pareto + utility)
  4. also reports how often the UNCONSTRAINED A/B picks would have violated
     the cap (why constraints matter).

Metrics: strict constraint satisfaction, median/mean time and EU, preference
satisfaction, dominance — no method is declared better for being faster or
cleaner alone.

Usage: /usr/bin/python3 tests/benchmarks/bench_travel_v6.py
"""
from __future__ import annotations

import json
import statistics
import sys
import time as _time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

from common import save_result  # noqa: E402
from bench_travel_multigoal import (  # noqa: E402
    STATION,
    _compact,
    satisfies_preference,
)
from bench_travel_tradeoff import CASES, PROFILES, SLOTS  # noqa: E402

DATE_BLIND = "2023-10-25"   # 周三，V6 全新盲测日
CAP_RATIO = 1.25            # 暴露上限 = 候选集最优 EU 的 1.25 倍（先验固定）


def pick_constrained(candidates: list[dict], rule: str) -> dict | None:
    """基线的同约束选择：可行集中单目标最优；无可行则 None。"""
    feasible = [c for c in candidates if c["intake_index"] <= CAP_RATIO * min(
        c["intake_index"] for c in candidates)]
    if not feasible:
        return None
    if rule == "time":
        return min(feasible, key=lambda c: (c["duration_minutes"], c["intake_index"]))
    return min(feasible, key=lambda c: (c["intake_index"], c["duration_minutes"]))


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.travel_agent import TravelAgent

    travel = TravelAgent()
    air_agent = AirQualityAgent()

    conditions = {}
    for slot, (h0, h1) in SLOTS.items():
        start, end = f"{DATE_BLIND} {h0}", f"{DATE_BLIND} {h1}"
        meteo = meteorology_run({"task_id": f"V6_{slot}_meteo",
                                 "station_id": STATION["station_id"],
                                 "lat": STATION["lat"], "lon": STATION["lon"],
                                 "start_time": start, "end_time": end})
        air = air_agent.run({"task_id": f"V6_{slot}_air",
                             "station_name": STATION["station_name"],
                             "start_time": start, "end_time": end})
        mres = meteo.get("result") or {}
        ares = air.get("result") or {}
        conditions[slot] = {"start": start, "end": end, "meteo": meteo, "air": air,
                            "anomaly": ares.get("anomaly_score")}
        print(f"[{slot}] 异常分 {conditions[slot]['anomaly']}", flush=True)

    rows = []
    for slot in SLOTS:
        cond = conditions[slot]
        for case in CASES:
            for profile in PROFILES:
                base_task = {
                    "task_id": f"V6_{case['case_id']}_{profile}_{slot}",
                    "origin": case["origin"], "destination": case["destination"],
                    "start_time": cond["start"], "end_time": cond["end"],
                    "travel_profile": profile,
                    "pollution_source": case["pollution_source"],
                    "air_quality_risk": cond["anomaly"],
                }
                # 无约束运行：拿全量候选集（用于定义 cap 与基线可行集）
                out_full = travel.run(dict(base_task),
                                      meteorology_result=cond["meteo"],
                                      air_quality_result=cond["air"])
                full = [_compact(o) for o in (out_full.get("result") or {})
                        .get("route_options") or []]
                full = [c for c in full
                        if isinstance(c["duration_minutes"], float)
                        and isinstance(c["intake_index"], float)]
                if not full:
                    continue
                cap = CAP_RATIO * min(c["intake_index"] for c in full)

                # 同约束基线（从全量候选的可行子集选择）
                a_c = pick_constrained(full, "time")
                b_c = pick_constrained(full, "intake")

                # 系统：约束传入任务（真实代码路径）
                t0 = _time.perf_counter()
                constrained_task = {**base_task,
                                    "user_constraints": {"max_intake": round(cap, 4)}}
                out_c = travel.run(constrained_task,
                                   meteorology_result=cond["meteo"],
                                   air_quality_result=cond["air"])
                run_ms = (_time.perf_counter() - t0) * 1000.0
                c_options = [_compact(o) for o in (out_c.get("result") or {})
                             .get("route_options") or []]
                c_pick = next((o for o in c_options if o.get("recommended")), None)
                c_degraded = any("无完全满足用户约束" in w
                                 for w in (out_c.get("warnings") or []))

                # 无约束基线（v4 口径）：检验其选择是否会违反 cap
                a_unc = min(full, key=lambda c: (c["duration_minutes"], c["intake_index"]))
                b_unc = min(full, key=lambda c: (c["intake_index"], c["duration_minutes"]))

                rubric_source = json.loads(
                    (Path(__file__).resolve().parent / "results" / "travel_multigoal.json")
                    .read_text(encoding="utf-8"))
                rubric = rubric_source["design"]["preference_rubric"]
                t_min = min(c["duration_minutes"] for c in full)
                e_min = min(c["intake_index"] for c in full)

                def entry_for(pick: dict | None, constrained: bool) -> dict | None:
                    if pick is None:
                        return {"status": "infeasible"}
                    return {
                        "mode": pick.get("mode"), "route_id": pick.get("route_id"),
                        "duration_minutes": round(pick["duration_minutes"], 2),
                        "intake_index": round(pick["intake_index"], 4),
                        "pref_satisfied": satisfies_preference(pick, profile, t_min, e_min),
                        "within_cap": pick["intake_index"] <= cap,
                        "constrained": constrained,
                    }

                rows.append({
                    "case": case["case_id"], "profile": profile, "slot": slot,
                    "cap": round(cap, 4), "n_candidates": len(full),
                    "A_constrained": entry_for(a_c, True),
                    "B_constrained": entry_for(b_c, True),
                    "C_system": entry_for(c_pick, True) if c_pick is not None
                                else {"status": "degraded"},
                    "C_degraded": c_degraded,
                    "A_unconstrained_violates_cap": a_unc["intake_index"] > cap,
                    "B_unconstrained_violates_cap": b_unc["intake_index"] > cap,
                    "run_ms": round(run_ms, 1),
                })
                c_entry = rows[-1]["C_system"]
                print(f"  {slot[:7]} {case['case_id'][:24]:<24} {profile:<17} "
                      f"cap={rows[-1]['cap']:.3f} -> C: {c_entry.get('mode', '-'):>7} "
                      f"t={c_entry.get('duration_minutes', '-')} "
                      f"EU={c_entry.get('intake_index', '-')}", flush=True)

    # ── 汇总 ────────────────────────────────────────────────────────────────
    def summarise(entries: list[dict | None]) -> dict:
        valid = [e for e in entries if e and e.get("duration_minutes") is not None]
        pref = [1.0 if e.get("pref_satisfied") else 0.0
                for e in valid if e.get("pref_satisfied") is not None]
        return {
            "n": len(entries),
            "strict_feasible": len([e for e in valid if e.get("within_cap")]),
            "degraded": len([e for e in entries if e and e.get("status") == "degraded"]),
            "median_time_min": round(statistics.median(e["duration_minutes"] for e in valid), 2)
                               if valid else None,
            "mean_eu": round(statistics.mean(e["intake_index"] for e in valid), 4)
                       if valid else None,
            "pref_satisfaction": round(statistics.mean(pref), 4) if pref else None,
        }

    summary = {
        "A_constrained": summarise([r["A_constrained"] for r in rows]),
        "B_constrained": summarise([r["B_constrained"] for r in rows]),
        "C_system": summarise([r["C_system"] for r in rows]),
        "unconstrained_violation_rate": {
            "A": f"{sum(1 for r in rows if r['A_unconstrained_violates_cap'])}/{len(rows)}",
            "B": f"{sum(1 for r in rows if r['B_unconstrained_violates_cap'])}/{len(rows)}",
        },
    }

    payload = {
        "design": {
            "note": "V6：显式约束优先。暴露上限 = 候选集最优 EU × 1.25（先验固定，三方法相同）；"
                    "系统按真实代码路径接收约束；基线从可行子集单目标选择；"
                    "无约束基线的违约率单独报告。",
            "blind_date": DATE_BLIND, "cap_ratio": CAP_RATIO,
            "cases": [c["case_id"] for c in CASES], "profiles": PROFILES,
        },
        "summary": summary,
        "rows": rows,
    }
    path = save_result("travel_v6", payload)
    print(f"\nsaved -> {path}")
    print("\n=== V6 同约束对比（盲测日 2023-10-25）===")
    for name, s in summary.items():
        print(f"  {name}: {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
