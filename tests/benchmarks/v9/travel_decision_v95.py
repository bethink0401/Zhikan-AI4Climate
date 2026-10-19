# -*- coding: utf-8 -*-
"""V9.5 Experiment (innovation-3): profile-driven Tchebycheff decisioner benchmark.

Question answered: does replacing the final linear-utility selection with a
normalised, profile-driven weighted Tchebycheff change the recommendation in a
way that is measurably better than a *tuned* weighted sum / epsilon-constraint /
standard Tchebycheff — on identical candidates and identical hard constraints?

Pre-registered (see protocol.json: v9_5_preregistration):
  - dev (tuning) date: 2024-08-14
  - blind date:        2024-09-18
  - all previously-seen travel dates are regression only, never called "blind"

Arms (identical feasible set, identical cap):
  A  shortest time
  B  lowest exposure
  C  weighted sum, lambda tuned on dev
  E  epsilon-constraint, eps tuned on dev
  F  standard (unweighted-profile) Tchebycheff, w tuned on dev
  D  system decisioner with rule=weighted_sum   ("old 智瞰")
  G  system decisioner with rule=tchebycheff    ("new 智瞰", the default now)

Metrics: single-point normalised hypervolume (unified ref point), decision
regret against the profile-feasible optimum (non-negative by construction),
preference satisfaction (rubric v1/v2), infeasible-pick rate, and OD x slot
clustered paired bootstrap CIs for the key comparisons (incl. F-D).

Online/offline consistency: the system is also run *online* with the cap applied
for both rules and compared with the offline arm picks.

Conclusions limited to "optimisation performance under pre-registered profiles
and constraints"; no real-user claims.

Usage: /usr/bin/python3 tests/benchmarks/v9/travel_decision_v95.py
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
from travel_multiopt import hypervolume_norm, pick_weighted  # noqa: E402
from travel_differentiation import (  # noqa: E402
    nonconvex_frontier_unit_test, pick_epsilon, pick_tchebycheff)
from tools.travel_advice_tools import load_travel_config, resolve_profile, \
    select_recommendation  # noqa: E402

CAP_RATIO = 1.25
DEV_DATE = "2024-08-14"
BLIND_DATE = "2024-09-18"
ARMS = ("A_shortest", "B_lowest", "C_weighted_tuned", "E_epsilon_tuned",
        "F_tchebycheff_std", "D_system_weighted_sum", "G_system_tchebycheff")


def annotate_pareto(cands: list[dict]) -> None:
    for option in cands:
        option["pareto_dominated"] = any(
            other is not option
            and other["duration_minutes"] <= option["duration_minutes"]
            and other["intake_index"] <= option["intake_index"]
            and (other["duration_minutes"] < option["duration_minutes"]
                 or other["intake_index"] < option["intake_index"])
            for other in cands)


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.travel_agent import TravelAgent
    from bench_travel_multigoal import STATION, _compact, satisfies_preference
    from bench_travel_tradeoff import CASES, PROFILES, SLOTS

    RUBRIC = json.loads((HERE.parent / "results" / "travel_multigoal.json")
                        .read_text(encoding="utf-8"))["design"]["preference_rubric"]
    travel_config = load_travel_config()

    def profile_dict(profile_id: str) -> dict:
        return resolve_profile(travel_config, profile_id)

    travel = TravelAgent()
    air_agent = AirQualityAgent()

    def run_system(task: dict, meteo: dict, air: dict, rule: str | None) -> dict:
        original = dict(travel.config.get("decision") or {})
        if rule is not None:
            travel.config["decision"] = {**original, "rule": rule}
        try:
            return travel.run(dict(task), meteorology_result=meteo,
                              air_quality_result=air)
        finally:
            travel.config["decision"] = original

    def collect(date: str) -> list[dict]:
        conditions = {}
        for slot, (h0, h1) in SLOTS.items():
            start, end = f"{date} {h0}", f"{date} {h1}"
            meteo = meteorology_run({"task_id": f"V95_{slot}_m",
                                     "station_id": STATION["station_id"],
                                     "lat": STATION["lat"], "lon": STATION["lon"],
                                     "start_time": start, "end_time": end})
            air = air_agent.run({"task_id": f"V95_{slot}_a",
                                 "station_name": STATION["station_name"],
                                 "start_time": start, "end_time": end})
            conditions[slot] = {"meteo": meteo, "air": air, "start": start, "end": end,
                                "anomaly": (air.get("result") or {}).get("anomaly_score")}
        units = []
        for slot in SLOTS:
            cond = conditions[slot]
            for case in CASES:
                for profile in PROFILES:
                    task = {"task_id": f"V95_{date}_{case['case_id']}_{profile}_{slot}",
                            "origin": case["origin"], "destination": case["destination"],
                            "start_time": cond["start"], "end_time": cond["end"],
                            "travel_profile": profile,
                            "pollution_source": case["pollution_source"],
                            "air_quality_risk": cond["anomaly"]}
                    out = run_system(task, cond["meteo"], cond["air"], None)
                    cands = [_compact(o) for o in (out.get("result") or {})
                             .get("route_options") or []]
                    cands = [c for c in cands
                             if isinstance(c["duration_minutes"], float)
                             and isinstance(c["intake_index"], float)]
                    if len(cands) < 2:
                        continue
                    cap = CAP_RATIO * min(c["intake_index"] for c in cands)
                    # 在线一致性：同一候选集下，两条规则各自在线跑一次（同 cap）
                    online = {}
                    for rule in ("weighted_sum", "tchebycheff"):
                        out_online = run_system(
                            {**task, "user_constraints": {"max_intake": round(cap, 4)}},
                            cond["meteo"], cond["air"], rule)
                        pick = next((o for o in (out_online.get("result") or {})
                                     .get("route_options") or [] if o.get("recommended")),
                                    None)
                        online[rule] = pick.get("route_id") if pick else None
                    units.append({"case": case["case_id"], "profile": profile,
                                  "slot": slot, "candidates": cands,
                                  "online_picks": online})
        return units

    def feasible(unit: dict) -> list[dict]:
        cap = CAP_RATIO * min(c["intake_index"] for c in unit["candidates"])
        return [c for c in unit["candidates"] if c["intake_index"] <= cap]

    dev_units = collect(DEV_DATE)
    print(f"开发日单元 {len(dev_units)}", flush=True)
    dev_feas = [(u, feasible(u)) for u in dev_units if feasible(u)]

    lam_grid = [5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 60.0]
    lam_star = max(lam_grid, key=lambda lam: statistics.mean(
        hypervolume_norm(pick_weighted(f, lam), f) for _, f in dev_feas))
    eps_grid = [1.05, 1.10, 1.15, 1.25, 1.40]
    eps_star = max(eps_grid, key=lambda r: statistics.mean(
        hypervolume_norm(pick_epsilon(f, r * min(c["intake_index"] for c in f)), f)
        for _, f in dev_feas))
    w_grid = [i / 20.0 for i in range(1, 20)]
    w_star = max(w_grid, key=lambda w: statistics.mean(
        hypervolume_norm(pick_tchebycheff(f, w, 1.0 - w), f) for _, f in dev_feas))
    print(f"开发集调优: λ*={lam_star} ε*={eps_star} w*={w_star:.2f}", flush=True)

    blind_units = collect(BLIND_DATE)
    print(f"盲测日单元 {len(blind_units)}", flush=True)

    rows = []
    consistency = {"G": [0, 0], "D": [0, 0]}  # [一致数, 总数]
    for unit in blind_units:
        f = feasible(unit)
        if not f:
            continue
        annotate_pareto(f)
        profile = profile_dict(unit["profile"])
        t_min = min(c["duration_minutes"] for c in f)
        e_min = min(c["intake_index"] for c in f)
        cap = CAP_RATIO * e_min

        picks = {
            "A_shortest": min(f, key=lambda c: (c["duration_minutes"], c["intake_index"])),
            "B_lowest": min(f, key=lambda c: (c["intake_index"], c["duration_minutes"])),
            "C_weighted_tuned": pick_weighted(f, lam_star),
            "E_epsilon_tuned": pick_epsilon(f, eps_star * e_min),
            "F_tchebycheff_std": pick_tchebycheff(f, w_star, 1.0 - w_star),
            "D_system_weighted_sum": select_recommendation(
                f, profile, {"decision": {"rule": "weighted_sum"}}),
            "G_system_tchebycheff": select_recommendation(f, profile, travel_config),
        }
        # 在线一致性
        for arm, rule in (("G", "tchebycheff"), ("D", "weighted_sum")):
            online_id = unit["online_picks"].get(rule)
            consistency[arm][1] += 1
            if online_id is not None and online_id == picks[
                    "G_system_tchebycheff" if arm == "G" else "D_system_weighted_sum"]["route_id"]:
                consistency[arm][0] += 1

        # 画像可行集（时间/EU 双比率上限），后悔值参照它
        rule_spec = RUBRIC[unit["profile"]]
        tr, er = rule_spec.get("max_time_ratio"), rule_spec.get("max_intake_ratio")
        time_cap = tr * t_min if tr is not None else math.inf
        eu_cap = er * e_min if er is not None else math.inf
        pool = [c for c in f if c["duration_minutes"] <= time_cap
                and c["intake_index"] <= eu_cap]
        best_t = min(c["duration_minutes"] for c in pool) if pool else None
        best_e = min(c["intake_index"] for c in pool) if pool else None

        row = {"case": unit["case"], "profile": unit["profile"], "slot": unit["slot"],
               "frontier_size": sum(1 for c in f if not c["pareto_dominated"])}
        for name, pick in picks.items():
            in_profile = (pick["duration_minutes"] <= time_cap
                          and pick["intake_index"] <= eu_cap)
            row[name] = {
                "route_id": pick["route_id"], "mode": pick["mode"],
                "duration_minutes": pick["duration_minutes"],
                "intake_index": pick["intake_index"],
                "hv": hypervolume_norm(pick, f),
                "feasible_pick": in_profile,
                "regret_time": (round(pick["duration_minutes"] - best_t, 3)
                                if (in_profile and best_t is not None) else None),
                "regret_eu": (round(pick["intake_index"] - best_e, 4)
                              if (in_profile and best_e is not None) else None),
                "pref_v1": satisfies_preference(pick, unit["profile"], t_min, e_min),
            }
        row["G_differs_from_D"] = (picks["G_system_tchebycheff"]["route_id"]
                                   != picks["D_system_weighted_sum"]["route_id"])
        row["G_differs_from_A"] = (picks["G_system_tchebycheff"]["route_id"]
                                   != picks["A_shortest"]["route_id"])
        rows.append(row)

    def mean(values, digits=4):
        vals = [v for v in values if v is not None]
        return round(statistics.mean(vals), digits) if vals else None

    summary = {arm: {
        "mean_hv": mean([r[arm]["hv"] for r in rows]),
        "mean_regret_time": mean([r[arm]["regret_time"] for r in rows], 3),
        "mean_regret_eu": mean([r[arm]["regret_eu"] for r in rows], 4),
        "infeasible_pick_rate": round(statistics.mean(
            1.0 if not r[arm]["feasible_pick"] else 0.0 for r in rows), 4),
        "regret_n": sum(1 for r in rows if r[arm]["regret_time"] is not None),
        "pref_v1": mean([1.0 if r[arm]["pref_v1"] else 0.0 for r in rows]),
    } for arm in ARMS}

    clusters: dict[tuple, list] = {}
    for r in rows:
        clusters.setdefault((r["case"], r["slot"]), []).append(r)
    rng = random.Random(42)

    def cluster_diff(x_arm: str, y_arm: str, key: str) -> dict:
        pairs = [u for u in clusters.values()
                 if all(e[x_arm][key] is not None and e[y_arm][key] is not None for e in u)]
        values = [statistics.mean(e[x_arm][key] for e in unit)
                  - statistics.mean(e[y_arm][key] for e in unit) for unit in pairs]
        if not values:
            return {}
        samples = sorted(statistics.mean(values[rng.randrange(len(values))]
                                         for _ in range(len(values)))
                         for _ in range(10000))
        return {"mean_diff": round(statistics.mean(values), 4),
                "ci95": [round(samples[250], 4), round(samples[9750], 4)],
                "n_clusters": len(pairs)}

    comparisons = {}
    for other in ("D_system_weighted_sum", "F_tchebycheff_std",
                  "C_weighted_tuned", "E_epsilon_tuned"):
        comparisons[f"G_vs_{other}_hv"] = cluster_diff(
            "G_system_tchebycheff", other, "hv")
    comparisons["F_vs_D_hv"] = cluster_diff("F_tchebycheff_std",
                                            "D_system_weighted_sum", "hv")
    comparisons["G_vs_D_regret_time"] = cluster_diff(
        "G_system_tchebycheff", "D_system_weighted_sum", "regret_time")

    payload = {
        "design": {
            "note": "V9.5 创新三：把系统最终选择从线性效用换成归一化+画像驱动的加权 "
                    "Tchebycheff（保留 Pareto 筛选与硬约束）；同候选同约束下与调优加权和、"
                    "ε-约束、标准 Tchebycheff 对比。结论限定『预设画像与约束下的优化表现』",
            "cap_ratio": CAP_RATIO,
            "dev_date": DEV_DATE, "blind_date": BLIND_DATE,
            "preregistration": "tests/benchmarks/v9/protocol.json: v9_5_preregistration",
            "dev_tuned": {"lambda_star": lam_star, "eps_ratio_star": eps_star,
                          "w_star": round(w_star, 3)},
            "cluster_unit": "OD×时段",
            "decisioner_config": travel_config.get("decision"),
        },
        "summary": summary,
        "online_offline_consistency": {
            "G_tchebycheff": {"matched": consistency["G"][0], "total": consistency["G"][1]},
            "D_weighted_sum": {"matched": consistency["D"][0], "total": consistency["D"][1]},
        },
        "comparisons_cluster_bootstrap": comparisons,
        "nonconvex_frontier_unit_test": nonconvex_frontier_unit_test(),
        "rows": rows,
    }
    path = save_result("v95_travel_decision", payload)
    print(f"\nsaved -> {path}")
    print(f"=== 七臂对比（盲测日 {BLIND_DATE}，{len(rows)} 单元）===")
    for arm in ARMS:
        s = summary[arm]
        print(f"  {arm:<24} HV={s['mean_hv']} 时间后悔={s['mean_regret_time']}"
              f"(n={s['regret_n']}) 不可行率={s['infeasible_pick_rate']} pref_v1={s['pref_v1']}")
    print("\n在线一致性:", json.dumps(payload["online_offline_consistency"], ensure_ascii=False))
    print("聚类 CI:", json.dumps(comparisons, ensure_ascii=False, indent=1))
    print("G 与 D 选择不同比例:", round(statistics.mean(
        1.0 if r["G_differs_from_D"] else 0.0 for r in rows), 4))
    print("非凸单元测试:", json.dumps(
        payload["nonconvex_frontier_unit_test"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
