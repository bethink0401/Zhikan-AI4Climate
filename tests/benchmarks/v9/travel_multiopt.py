"""V9.2 Experiment: innovation-3 multi-objective optimisation benchmark.

Four decision rules on IDENTICAL candidate sets, hard constraints and data
(all derived from one agent run per case; conclusions are limited to
"optimisation performance under pre-registered profiles and constraints" —
no real-user claims):

    A  shortest time           min (duration, EU)
    B  lowest exposure         min (EU, duration)
    C  traditional weighted    min (duration + LAMBDA * EU), LAMBDA = 20 min/EU
                               (fixed, pre-registered)
    D  智瞰                    Pareto non-dominated screen + utility
                               (config exchange rate, weather & profile terms)

Metrics (per the V9.2 plan):
  - normalised single-point hypervolume with a UNIFIED per-case reference
    point (worst feasible duration/EU);
  - time cost at the same EU cap (pick time minus fastest feasible time);
  - EU at the same time budget (1.15 x fastest; pick EU minus best EU in
    budget);
  - pre-registered synthetic preference satisfaction (rubric v1 + v2);
  - stability: pick-flip rate under lambda in {10, 40} (C) and exchange rate
    in {4, 16} (D, utility recomputed linearly), cap in {1.15x, 1.35x} (D);
  - cluster bootstrap (OD x slot) CIs on arm differences.

Blind date 2024-04-12 (Friday) — never used in any round.

Usage: /usr/bin/python3 tests/benchmarks/v9/travel_multiopt.py
"""
from __future__ import annotations

import json
import random
import statistics
import sys
import time as _time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(HERE.parent))

from common import save_result  # noqa: E402
from bench_travel_multigoal import STATION, _compact, satisfies_preference  # noqa: E402
from bench_travel_tradeoff import CASES, PROFILES, SLOTS  # noqa: E402
from bench_travel_v8 import decision_zhikan  # noqa: E402

DATE_BLIND = "2024-04-12"   # 周五，V9.2 全新盲测日
CAP_RATIO = 1.25
LAMBDA = 20.0               # 传统加权和的固定权重（分钟/EU，预注册）
TIME_BUDGET_RATIO = 1.15    # "同时间预算"定义：1.15 × 最快可行


def pick_weighted(cands: list[dict], lam: float) -> dict | None:
    return min(cands, key=lambda c: c["duration_minutes"] + lam * c["intake_index"]) \
        if cands else None


def recompute_utility(option: dict, exchange: float) -> float:
    """按新交换率线性重算效用（pollution_added 与交换率成正比）。"""
    d = option
    base_pollution = d.get("pollution_added_minutes") or 0.0
    scale = exchange / 8.0
    return (d.get("outdoor_equivalent_minutes") or 0.0
            + d.get("weather_added_minutes") or 0.0
            + base_pollution * scale
            + d.get("mode_constant_minutes") or 0.0
            + d.get("overrun_minutes") or 0.0
            + d.get("walk_overrun_minutes") or 0.0)


def zhikan_with_exchange(cands: list[dict], exchange: float) -> dict | None:
    """智瞰决策在新交换率下的等价实现：可行集 Pareto 筛选 + 新效用最优。"""
    if not cands:
        return None

    def dominated(option: dict) -> bool:
        for other in cands:
            if other is option:
                continue
            if (other["duration_minutes"] <= option["duration_minutes"]
                    and other["intake_index"] <= option["intake_index"]
                    and (other["duration_minutes"] < option["duration_minutes"]
                         or other["intake_index"] < option["intake_index"])):
                return True
        return False

    frontier = [c for c in cands if not dominated(c)]
    return min(frontier, key=lambda c: recompute_utility(c, exchange))


def hypervolume_norm(pick: dict, feasible: list[dict]) -> float:
    """单点归一化 Hypervolume：统一参考点 = 可行集最差 (t, EU)。

    HV = ((ref_t − t)/(ref_t − min_t)) × ((ref_e − e)/(ref_e − min_e)) ∈ [0,1]，
    越接近 1 越靠近理想角。
    """
    ref_t = max(c["duration_minutes"] for c in feasible)
    ref_e = max(c["intake_index"] for c in feasible)
    min_t = min(c["duration_minutes"] for c in feasible)
    min_e = min(c["intake_index"] for c in feasible)
    span_t = max(ref_t - min_t, 1e-9)
    span_e = max(ref_e - min_e, 1e-9)
    return round(((ref_t - pick["duration_minutes"]) / span_t)
                 * ((ref_e - pick["intake_index"]) / span_e), 4)


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.travel_agent import TravelAgent

    travel = TravelAgent()
    air_agent = AirQualityAgent()

    rubric_source = json.loads(
        (HERE.parent / "results" / "travel_multigoal.json").read_text(encoding="utf-8"))
    rubric_v1 = rubric_source["design"]["preference_rubric"]
    RUBRIC_V2 = {
        "commuter": {"max_time_ratio": 1.15, "max_intake_ratio": 1.30, "modes": None},
        "parent_with_child": {"max_time_ratio": 1.60, "max_intake_ratio": 1.10, "modes": None},
        "elderly": {"max_time_ratio": 1.40, "max_intake_ratio": 1.20,
                    "modes": ["transit", "walk"]},
        "runner": {"max_time_ratio": None, "max_intake_ratio": 1.10,
                   "modes": ["walk", "transit"]},
        "tourist": {"max_time_ratio": 1.50, "max_intake_ratio": 1.50, "modes": None},
    }

    conditions = {}
    for slot, (h0, h1) in SLOTS.items():
        start, end = f"{DATE_BLIND} {h0}", f"{DATE_BLIND} {h1}"
        meteo = meteorology_run({"task_id": f"V92_{slot}_meteo",
                                 "station_id": STATION["station_id"],
                                 "lat": STATION["lat"], "lon": STATION["lon"],
                                 "start_time": start, "end_time": end})
        air = air_agent.run({"task_id": f"V92_{slot}_air",
                             "station_name": STATION["station_name"],
                             "start_time": start, "end_time": end})
        conditions[slot] = {"start": start, "end": end, "meteo": meteo, "air": air,
                            "anomaly": (air.get("result") or {}).get("anomaly_score")}
        print(f"[{slot}] 异常分 {conditions[slot]['anomaly']}", flush=True)

    rows = []
    for slot in SLOTS:
        cond = conditions[slot]
        for case in CASES:
            for profile in PROFILES:
                task = {
                    "task_id": f"V92_{case['case_id']}_{profile}_{slot}",
                    "origin": case["origin"], "destination": case["destination"],
                    "start_time": cond["start"], "end_time": cond["end"],
                    "travel_profile": profile,
                    "pollution_source": case["pollution_source"],
                    "air_quality_risk": cond["anomaly"],
                }
                out = travel.run(dict(task), meteorology_result=cond["meteo"],
                                 air_quality_result=cond["air"])
                cands = [_compact(o) for o in (out.get("result") or {})
                         .get("route_options") or []]
                cands = [c for c in cands
                         if isinstance(c["duration_minutes"], float)
                         and isinstance(c["intake_index"], float)]
                if len(cands) < 2:
                    continue
                cap = CAP_RATIO * min(c["intake_index"] for c in cands)
                feasible = [c for c in cands if c["intake_index"] <= cap]
                if not feasible:
                    continue

                picks = {
                    "A_shortest": min(feasible, key=lambda c: (c["duration_minutes"],
                                                               c["intake_index"])),
                    "B_lowest": min(feasible, key=lambda c: (c["intake_index"],
                                                             c["duration_minutes"])),
                    "C_weighted": pick_weighted(feasible, LAMBDA),
                    "D_zhikan": decision_zhikan(feasible, cap),
                }
                t_min = min(c["duration_minutes"] for c in feasible)
                e_min = min(c["intake_index"] for c in feasible)
                budget = TIME_BUDGET_RATIO * t_min
                budget_set = [c for c in feasible if c["duration_minutes"] <= budget]

                def flags(pick: dict) -> dict:
                    v2 = RUBRIC_V2[profile]
                    v2_ok = True
                    if v2.get("modes") and pick.get("mode") not in v2["modes"]:
                        v2_ok = False
                    if v2.get("max_time_ratio") and pick["duration_minutes"] > \
                            v2["max_time_ratio"] * t_min:
                        v2_ok = False
                    if v2.get("max_intake_ratio") and pick["intake_index"] > \
                            v2["max_intake_ratio"] * e_min:
                        v2_ok = False
                    dominated = any(
                        o is not pick
                        and o["duration_minutes"] <= pick["duration_minutes"]
                        and o["intake_index"] <= pick["intake_index"]
                        and (o["duration_minutes"] < pick["duration_minutes"]
                             or o["intake_index"] < pick["intake_index"])
                        for o in feasible)
                    return {"hv": hypervolume_norm(pick, feasible),
                            "time_gap": round(pick["duration_minutes"] - t_min, 3),
                            "eu_gap_budget": (round(pick["intake_index"]
                                                    - min(c["intake_index"] for c in budget_set), 4)
                                              if budget_set else None),
                            "pref_v1": satisfies_preference(pick, profile, t_min, e_min),
                            "pref_v2": v2_ok,
                            "nondominated": not dominated}

                row = {"case": case["case_id"], "profile": profile, "slot": slot,
                       "cap": round(cap, 4), "n_candidates": len(cands),
                       "n_feasible": len(feasible)}
                for name, pick in picks.items():
                    row[name] = {"mode": pick.get("mode"),
                                 "route_id": pick.get("route_id"),
                                 "duration_minutes": pick["duration_minutes"],
                                 "intake_index": pick["intake_index"],
                                 **flags(pick)}

                # 稳定性：参数扰动下的翻选率（逐臂）
                row["C_flip_lambda"] = {
                    str(lam): pick_weighted(feasible, lam)["route_id"]
                    != picks["C_weighted"]["route_id"]
                    for lam in (10.0, 40.0)}
                row["D_flip_exchange"] = {
                    str(x): zhikan_with_exchange(feasible, x)["route_id"]
                    != picks["D_zhikan"]["route_id"]
                    for x in (4.0, 16.0)}
                row["D_flip_cap"] = {
                    str(ratio): decision_zhikan(
                        [c for c in cands
                         if c["intake_index"] <= ratio * min(
                             c["intake_index"] for c in cands)], cap)["route_id"]
                    != picks["D_zhikan"]["route_id"]
                    for ratio in (1.15, 1.35)}
                rows.append(row)
                print(f"  {slot[:7]} {case['case_id'][:22]:<22} {profile:<17} "
                      f"HV A={row['A_shortest']['hv']:.2f} B={row['B_lowest']['hv']:.2f} "
                      f"C={row['C_weighted']['hv']:.2f} D={row['D_zhikan']['hv']:.2f}",
                      flush=True)

    # ── 汇总 ────────────────────────────────────────────────────────────────
    ARMS = ("A_shortest", "B_lowest", "C_weighted", "D_zhikan")

    def summarise(arm: str) -> dict:
        entries = [r[arm] for r in rows]
        return {
            "n": len(rows),
            "mean_hv": round(statistics.mean(e["hv"] for e in entries), 4),
            "mean_time_gap_min": round(statistics.mean(e["time_gap"] for e in entries), 3),
            "mean_eu_gap_budget": round(statistics.mean(
                e["eu_gap_budget"] for e in entries if e["eu_gap_budget"] is not None), 4),
            "nondominated_rate": round(statistics.mean(
                1.0 if e["nondominated"] else 0.0 for e in entries), 4),
            "pref_v1": round(statistics.mean(1.0 if e["pref_v1"] else 0.0
                                             for e in entries), 4),
            "pref_v2": round(statistics.mean(1.0 if e["pref_v2"] else 0.0
                                             for e in entries), 4),
        }

    summary = {arm: summarise(arm) for arm in ARMS}
    summary["stability"] = {
        "C_flip_rate_lambda10_40": {
            lam: round(statistics.mean(1.0 if r["C_flip_lambda"][lam] else 0.0
                                       for r in rows), 4)
            for lam in ("10.0", "40.0")},
        "D_flip_rate_exchange4_16": {
            x: round(statistics.mean(1.0 if r["D_flip_exchange"][x] else 0.0
                                     for r in rows), 4)
            for x in ("4.0", "16.0")},
        "D_flip_rate_cap1.15_1.35": {
            ratio: round(statistics.mean(1.0 if r["D_flip_cap"][ratio] else 0.0
                                         for r in rows), 4)
            for ratio in ("1.15", "1.35")},
    }

    # 聚类 bootstrap（OD×slot 12 簇）：D 相对 C/B 的 HV 与 pref 差
    clusters = {}
    for r in rows:
        clusters.setdefault((r["case"], r["slot"]), []).append(r)
    rng = random.Random(42)
    unit_ids = sorted(clusters)

    def cluster_diff(metric_fn, x_arm, y_arm, reps=10000) -> dict:
        values = []
        for unit in clusters.values():
            values.append(metric_fn(unit, x_arm) - metric_fn(unit, y_arm))
        point = statistics.mean(values)
        samples = []
        for _ in range(reps):
            chosen = [values[rng.randrange(len(values))]
                      for _ in range(len(values))]
            samples.append(statistics.mean(chosen))
        samples.sort()
        return {"mean_diff": round(point, 4),
                "ci95": [round(samples[int(0.025 * reps)], 4),
                         round(samples[int(0.975 * reps)], 4)],
                "n_clusters": len(unit_ids)}

    hv_fn = lambda unit, arm: statistics.mean(e[arm]["hv"] for e in unit)
    pref_fn = lambda unit, arm: statistics.mean(
        1.0 if e[arm]["pref_v1"] else 0.0 for e in unit)
    pref2_fn = lambda unit, arm: statistics.mean(
        1.0 if e[arm]["pref_v2"] else 0.0 for e in unit)
    comparisons = {
        "D_vs_C_hv": cluster_diff(hv_fn, "D_zhikan", "C_weighted"),
        "D_vs_B_hv": cluster_diff(hv_fn, "D_zhikan", "B_lowest"),
        "D_vs_C_pref_v1": cluster_diff(pref_fn, "D_zhikan", "C_weighted"),
        "D_vs_C_pref_v2": cluster_diff(pref2_fn, "D_zhikan", "C_weighted"),
    }

    payload = {
        "design": {
            "note": "V9.2 多目标基准：四臂同候选集/同 cap/同数据；结论限定"
                    "『预设画像与约束下的优化表现』，不涉及真实用户",
            "blind_date": DATE_BLIND, "cap_ratio": CAP_RATIO,
            "lambda_weighted": LAMBDA,
            "time_budget_ratio": TIME_BUDGET_RATIO,
            "hypervolume": "单点归一化 HV，参考点=可行集最差 (t, EU)，逐案统一",
        },
        "summary": summary,
        "comparisons_cluster_bootstrap": comparisons,
        "rows": rows,
    }
    path = save_result("v9_travel_multiopt", payload)
    print(f"\nsaved -> {path}")
    print("=== V9.2 多目标基准（盲测日 2024-04-12）===")
    for arm in ARMS:
        s = summary[arm]
        print(f"  {arm:<11} HV={s['mean_hv']} t_gap={s['mean_time_gap_min']} "
              f"EU_gap={s['mean_eu_gap_budget']} pref_v1={s['pref_v1']} "
              f"pref_v2={s['pref_v2']} nondom={s['nondominated_rate']}")
    print("稳定性:", json.dumps(summary["stability"], ensure_ascii=False))
    for k, v in comparisons.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
