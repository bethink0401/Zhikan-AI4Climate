# -*- coding: utf-8 -*-
"""V9.6 Experiment (innovation-3): profile-weight semantics + infeasibility taxonomy.

Answers three review questions:
  1. Does the profile-driven weight formula behave monotonically with the
     intended semantics? (fixed / legacy formula / V9.6 formula compared on the
     dev date, then verified on a NEW pre-registered blind date)
  2. Is the new decisioner's remaining infeasible-pick rate a *system hard
     constraint* violation or merely the evaluator's ratio rubric? The two are
     now measured and reported separately (a system violation fails the run).
  3. New 智瞰 (V9.6 weights) vs standard tuned Tchebycheff on HV, regret,
     constraint satisfaction and profile objectives.

Pre-registered (protocol.json: v9_6_preregistration):
  dev (tuning) date 2024-11-13, blind date 2024-10-16.
  Previously-used dates are regression only.

Usage: /usr/bin/python3 tests/benchmarks/v9/travel_decision_v96.py
"""
from __future__ import annotations

import json
import math
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from common import save_result  # noqa: E402
from contracts import ContractViolation, require_no_all_null  # noqa: E402
from travel_multiopt import hypervolume_norm, pick_weighted  # noqa: E402
from travel_differentiation import (  # noqa: E402
    nonconvex_frontier_unit_test, pick_epsilon, pick_tchebycheff)
from tools.travel_advice_tools import (  # noqa: E402
    load_travel_config, resolve_profile, select_recommendation)

CAP_RATIO = 1.25
DEV_DATE = "2024-11-13"
BLIND_DATE = "2024-10-16"
ARMS = ("A_shortest", "B_lowest", "C_weighted_tuned", "E_epsilon_tuned",
        "F_tchebycheff_std", "D_old_zhikan",
        "G_fixed_weights", "G_legacy_formula", "G_v96_weights")


def annotate_pareto(cands: list[dict]) -> None:
    for option in cands:
        option["pareto_dominated"] = any(
            other is not option
            and other["duration_minutes"] <= option["duration_minutes"]
            and other["intake_index"] <= option["intake_index"]
            and (other["duration_minutes"] < option["duration_minutes"]
                 or other["intake_index"] < option["intake_index"])
            for other in cands)


def tchebycheff_scores(options: list[dict], w_time: float, w_intake: float) -> dict:
    times = [o["duration_minutes"] for o in options]
    intakes = [o["intake_index"] for o in options]
    t_min, t_max = min(times), max(times)
    e_min, e_max = min(intakes), max(intakes)
    span_t = max(t_max - t_min, 1e-9)
    span_e = max(e_max - e_min, 1e-9)
    return {o["route_id"]: max(w_time * (o["duration_minutes"] - t_min) / span_t,
                               w_intake * (o["intake_index"] - e_min) / span_e)
            for o in options}


def pick_with_weights(cands: list[dict], w_time: float, w_intake: float) -> dict:
    """按给定权重在非支配集上取 Tchebycheff 最优（供权重方案对照）。"""
    pool = [c for c in cands if not c.get("pareto_dominated")] or cands
    scores = tchebycheff_scores(pool, w_time, w_intake)
    return min(pool, key=lambda o: (scores[o["route_id"]], o["utility_minutes"]))


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.travel_agent import TravelAgent
    from bench_travel_multigoal import STATION, _compact, satisfies_preference
    from bench_travel_tradeoff import CASES, PROFILES, SLOTS

    rubric = json.loads((HERE.parent / "results" / "travel_multigoal.json")
                        .read_text(encoding="utf-8"))["design"]["preference_rubric"]
    travel_config = load_travel_config()

    def profile_dict(pid: str) -> dict:
        return resolve_profile(travel_config, pid)

    travel = TravelAgent()
    air_agent = AirQualityAgent()

    def run_system(task: dict, meteo: dict, air: dict, rule=None, basis=None) -> dict:
        saved = dict(travel.config.get("decision") or {})
        if rule is not None or basis is not None:
            travel.config["decision"] = {**saved, **({"rule": rule} if rule else {}),
                                         **({"weight_basis": basis} if basis else {})}
        try:
            return travel.run(dict(task), meteorology_result=meteo, air_quality_result=air)
        finally:
            travel.config["decision"] = saved

    def collect(date: str) -> list[dict]:
        conditions = {}
        for slot, (h0, h1) in SLOTS.items():
            start, end = f"{date} {h0}", f"{date} {h1}"
            meteo = meteorology_run({"task_id": f"V96_{slot}_m",
                                     "station_id": STATION["station_id"],
                                     "lat": STATION["lat"], "lon": STATION["lon"],
                                     "start_time": start, "end_time": end})
            air = air_agent.run({"task_id": f"V96_{slot}_a",
                                 "station_name": STATION["station_name"],
                                 "start_time": start, "end_time": end})
            conditions[slot] = {"meteo": meteo, "air": air, "start": start,
                                "end": end, "anomaly": (air.get("result") or {}).get("anomaly_score")}
        units = []
        for slot in SLOTS:
            cond = conditions[slot]
            for case in CASES:
                for profile in PROFILES:
                    task = {"task_id": f"V96_{date}_{case['case_id']}_{profile}_{slot}",
                            "origin": case["origin"], "destination": case["destination"],
                            "start_time": cond["start"], "end_time": cond["end"],
                            "travel_profile": profile,
                            "pollution_source": case["pollution_source"],
                            "air_quality_risk": cond["anomaly"]}
                    out = run_system(task, cond["meteo"], cond["air"])
                    cands = [_compact(o) for o in (out.get("result") or {})
                             .get("route_options") or []]
                    cands = [c for c in cands
                             if isinstance(c["duration_minutes"], float)
                             and isinstance(c["intake_index"], float)]
                    if len(cands) < 2:
                        continue
                    cap = CAP_RATIO * min(c["intake_index"] for c in cands)
                    online = run_system(
                        {**task, "user_constraints": {"max_intake": round(cap, 4)}},
                        cond["meteo"], cond["air"])
                    pick = next((o for o in (online.get("result") or {})
                                 .get("route_options") or [] if o.get("recommended")), None)
                    units.append({"case": case["case_id"], "profile": profile, "slot": slot,
                                  "candidates": cands,
                                  "online_route_id": pick.get("route_id") if pick else None,
                                  "online_intake": pick.get("intake_index") if pick else None,
                                  "cap": cap,
                                  "allowed_modes": profile_dict(profile).get("allowed_modes")})
        return units

    dev_units = collect(DEV_DATE)
    print(f"开发日单元 {len(dev_units)}", flush=True)
    dev_feas = [(u, [c for c in u["candidates"] if c["intake_index"] <= u["cap"]])
                for u in dev_units]
    dev_feas = [(u, f) for u, f in dev_feas if f]

    lam_star = max([5.0, 10.0, 20.0, 30.0, 40.0, 60.0], key=lambda lam: statistics.mean(
        hypervolume_norm(pick_weighted(f, lam), f) for _, f in dev_feas))
    eps_star = max([1.05, 1.10, 1.15, 1.25, 1.40], key=lambda r: statistics.mean(
        hypervolume_norm(pick_epsilon(f, r * min(c["intake_index"] for c in f)), f)
        for _, f in dev_feas))
    w_star = max([i / 20.0 for i in range(1, 20)], key=lambda w: statistics.mean(
        hypervolume_norm(pick_tchebycheff(f, w, 1.0 - w), f) for _, f in dev_feas))
    print(f"开发集调优: λ*={lam_star} ε*={eps_star} w*={w_star:.2f}", flush=True)

    # 三种权重方案在开发集上的 HV（只比较，不择优后回填）
    basis_hv = {}
    for basis in ("fixed", "time_pressure_legacy", "time_pressure"):
        vals = []
        for unit, f in dev_feas:
            annotate_pareto(f)
            w_t, w_i = _weights(unit["profile"], travel_config, basis)
            vals.append(hypervolume_norm(pick_with_weights(f, w_t, w_i), f))
        basis_hv[basis] = round(statistics.mean(vals), 4)
    print("开发集权重方案 HV:", basis_hv, flush=True)

    blind_units = collect(BLIND_DATE)
    print(f"盲测日单元 {len(blind_units)}", flush=True)

    rows = []
    contract_violations = 0
    for unit in blind_units:
        f = [c for c in unit["candidates"] if c["intake_index"] <= unit["cap"]]
        if not f:
            continue
        annotate_pareto(f)
        pid = unit["profile"]
        profile = profile_dict(pid)
        t_min = min(c["duration_minutes"] for c in f)
        e_min = min(c["intake_index"] for c in f)
        picks = {
            "A_shortest": min(f, key=lambda c: (c["duration_minutes"], c["intake_index"])),
            "B_lowest": min(f, key=lambda c: (c["intake_index"], c["duration_minutes"])),
            "C_weighted_tuned": pick_weighted(f, lam_star),
            "E_epsilon_tuned": pick_epsilon(f, eps_star * e_min),
            "F_tchebycheff_std": pick_tchebycheff(f, w_star, 1.0 - w_star),
            "D_old_zhikan": select_recommendation(
                f, profile, {"decision": {"rule": "weighted_sum"}}),
        }
        for arm, basis in (("G_fixed_weights", "fixed"),
                           ("G_legacy_formula", "time_pressure_legacy"),
                           ("G_v96_weights", "time_pressure")):
            w_t, w_i = _weights(pid, travel_config, basis)
            picks[arm] = pick_with_weights(f, w_t, w_i)

        spec = rubric[pid]
        time_ratio, intake_ratio = spec.get("max_time_ratio"), spec.get("max_intake_ratio")
        rubric_time_cap = time_ratio * t_min if time_ratio is not None else math.inf
        rubric_eu_cap = intake_ratio * e_min if intake_ratio is not None else math.inf
        pool = [c for c in f if c["duration_minutes"] <= rubric_time_cap
                and c["intake_index"] <= rubric_eu_cap]
        best_t = min(c["duration_minutes"] for c in pool) if pool else None
        best_e = min(c["intake_index"] for c in pool) if pool else None

        row = {"case": unit["case"], "profile": pid, "slot": unit["slot"],
               "frontier_size": sum(1 for c in f if not c["pareto_dominated"])}
        for arm, pick in picks.items():
            # 两类不可行分开度量：
            #   系统硬约束 = 本次运行的 user_constraints（EU ≤ cap）+ 画像 allowed_modes
            #   评测比率上限 = 评测细则的 max_time_ratio / max_intake_ratio
            allowed = unit["allowed_modes"]
            system_bad = (pick["intake_index"] > unit["cap"] + 1e-9
                          or (allowed is not None and pick.get("mode") not in allowed))
            rubric_bad = not (pick["duration_minutes"] <= rubric_time_cap
                              and pick["intake_index"] <= rubric_eu_cap)
            if system_bad:
                contract_violations += 1
            row[arm] = {
                "route_id": pick["route_id"], "mode": pick["mode"],
                "duration_minutes": pick["duration_minutes"],
                "intake_index": pick["intake_index"],
                "hv": hypervolume_norm(pick, f),
                "system_violation": bool(system_bad),
                "rubric_ratio_violation": bool(rubric_bad),
                "regret_time": (round(pick["duration_minutes"] - best_t, 3)
                                if best_t is not None else None),
                "regret_eu": (round(pick["intake_index"] - best_e, 4)
                              if best_e is not None else None),
                "pref_v1": satisfies_preference(pick, pid, t_min, e_min),
            }
        rows.append(row)

    if contract_violations:
        raise ContractViolation(
            f"存在 {contract_violations} 例系统硬约束违规（user_constraints 或 allowed_modes）")

    require_no_all_null(rows, tuple(f"{a}" for a in ARMS), context="travel v96 rows")

    def mean(values, digits=4):
        vals = [v for v in values if v is not None]
        return round(statistics.mean(vals), digits) if vals else None

    summary = {arm: {
        "mean_hv": mean([r[arm]["hv"] for r in rows]),
        "mean_regret_time": mean([r[arm]["regret_time"] for r in rows], 3),
        "mean_regret_eu": mean([r[arm]["regret_eu"] for r in rows], 4),
        "system_violation_rate": round(statistics.mean(
            1.0 if r[arm]["system_violation"] else 0.0 for r in rows), 4),
        "rubric_ratio_violation_rate": round(statistics.mean(
            1.0 if r[arm]["rubric_ratio_violation"] else 0.0 for r in rows), 4),
        "pref_v1": mean([1.0 if r[arm]["pref_v1"] else 0.0 for r in rows]),
    } for arm in ARMS}

    clusters: dict[tuple, list] = {}
    for r in rows:
        clusters.setdefault((r["case"], r["slot"]), []).append(r)
    rng = random.Random(42)

    def cluster_diff(x_arm, y_arm, key="hv"):
        pairs = [u for u in clusters.values()
                 if all(e[x_arm][key] is not None and e[y_arm][key] is not None for e in u)]
        values = [statistics.mean(e[x_arm][key] for e in u)
                  - statistics.mean(e[y_arm][key] for e in u) for u in pairs]
        if not values:
            return {}
        samples = sorted(statistics.mean(values[rng.randrange(len(values))]
                                         for _ in range(len(values)))
                         for _ in range(10000))
        return {"mean_diff": round(statistics.mean(values), 4),
                "ci95": [round(samples[250], 4), round(samples[9750], 4)],
                "n_clusters": len(pairs)}

    comparisons = {}
    for other in ("D_old_zhikan", "F_tchebycheff_std", "C_weighted_tuned",
                  "E_epsilon_tuned", "G_fixed_weights", "G_legacy_formula"):
        comparisons[f"G_v96_vs_{other}_hv"] = cluster_diff("G_v96_weights", other)
    comparisons["F_vs_D_hv"] = cluster_diff("F_tchebycheff_std", "D_old_zhikan")
    comparisons["G_v96_vs_D_regret_time"] = cluster_diff(
        "G_v96_weights", "D_old_zhikan", "regret_time")

    consistency = {"matched": 0, "total": 0}
    for r, unit in zip(rows, [u for u in blind_units
                              if [c for c in u["candidates"] if c["intake_index"] <= u["cap"]]]):
        consistency["total"] += 1
        if unit["online_route_id"] == r["G_v96_weights"]["route_id"]:
            consistency["matched"] += 1

    payload = {
        "design": {
            "note": "V9.6 创新三：画像权重语义修正（单调性）+ 不可行率定性（系统硬约束 vs "
                    "评测比率上限）+ 新智瞰 vs 标准 Tchebycheff。结论限定『预设画像与约束下的"
                    "优化表现』",
            "dev_date": DEV_DATE, "blind_date": BLIND_DATE,
            "preregistration": "tests/benchmarks/v9/protocol.json: v9_6_preregistration",
            "dev_tuned": {"lambda_star": lam_star, "eps_ratio_star": eps_star,
                          "w_star": round(w_star, 3)},
            "dev_weight_basis_hv": basis_hv,
            "cluster_unit": "OD×时段",
            "decisioner_config": travel_config.get("decision"),
        },
        "summary": summary,
        "online_offline_consistency": consistency,
        "comparisons_cluster_bootstrap": comparisons,
        "nonconvex_frontier_unit_test": nonconvex_frontier_unit_test(),
        "rows": rows,
    }
    path = save_result("v96_travel_decision", payload)
    print(f"\nsaved -> {path}")
    print(f"=== 九臂对比（盲测日 {BLIND_DATE}，{len(rows)} 单元）===")
    for arm in ARMS:
        s = summary[arm]
        print(f"  {arm:<20} HV={s['mean_hv']} 时间后悔={s['mean_regret_time']} "
              f"系统违规={s['system_violation_rate']} 比率超限={s['rubric_ratio_violation_rate']} "
              f"pref_v1={s['pref_v1']}")
    print("在线一致性:", consistency)
    print("聚类 CI:", json.dumps(comparisons, ensure_ascii=False, indent=1))
    return 0


def _weights(profile_id: str, config: dict, basis: str) -> tuple[float, float]:
    from tools.travel_advice_tools import resolve_decision_weights
    override = {"decision": {**(config.get("decision") or {}), "weight_basis": basis}}
    return resolve_decision_weights(resolve_profile(config, profile_id), override)


if __name__ == "__main__":
    sys.exit(main())
