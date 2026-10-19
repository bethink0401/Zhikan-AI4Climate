"""V9.3 Experiment: innovation-3 strong baseline & perturbation loss.

Per the V9.3 plan, the fixed lambda=20 weighted sum is replaced by a
DEV-SET-TUNED weighted sum, and stability is scored by ACTUAL OPTIMISATION
LOSS under parameter perturbation (not merely pick-flip rates):

  1. Dev date 2023-10-18 (used only for tuning): grid-search lambda to
     maximise mean normalised hypervolume -> lambda* (frozen).
  2. Blind date 2024-05-10 (never used): arms on identical candidate sets,
     hard constraints and data:
       A shortest time | B lowest exposure | C_fixed (lambda=20)
       C_tuned (lambda*) | D 智瞰 (Pareto + utility, config exchange)
  3. Perturbation loss per adaptive arm:
       C_tuned: lambda * {0.5, 2}   D: exchange * {0.5, 2}
     loss = HV(unperturbed pick) − HV(perturbed pick), clipped at 0,
     averaged; also preference-violation delta. This distinguishes "stable
     because robustly optimal" from "stable because insensitive".
  4. Per-profile / per-slot breakdown; cluster bootstrap (OD x slot).

Conclusions limited to "optimisation performance under pre-registered
profiles and constraints".

Usage: /usr/bin/python3 tests/benchmarks/v9/travel_strongbase.py
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
sys.path.insert(0, str(HERE))

from common import save_result  # noqa: E402
from bench_travel_multigoal import STATION, _compact, satisfies_preference  # noqa: E402
from bench_travel_tradeoff import CASES, PROFILES, SLOTS  # noqa: E402
from travel_multiopt import (  # noqa: E402
    hypervolume_norm,
    pick_weighted,
    recompute_utility,
    zhikan_with_exchange,
)
from bench_travel_v8 import decision_zhikan  # noqa: E402

DATE_DEV = "2023-10-18"     # 历史开发日（仅用于 λ 调优）
DATE_BLIND = "2024-05-10"   # 周五，V9.3 全新盲测日
CAP_RATIO = 1.25
LAMBDA_FIXED = 20.0
LAMBDA_GRID = [5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 60.0]
RUBRIC_V2 = {
    "commuter": {"max_time_ratio": 1.15, "max_intake_ratio": 1.30, "modes": None},
    "parent_with_child": {"max_time_ratio": 1.60, "max_intake_ratio": 1.10, "modes": None},
    "elderly": {"max_time_ratio": 1.40, "max_intake_ratio": 1.20,
                "modes": ["transit", "walk"]},
    "runner": {"max_time_ratio": None, "max_intake_ratio": 1.10,
               "modes": ["walk", "transit"]},
    "tourist": {"max_time_ratio": 1.50, "max_intake_ratio": 1.50, "modes": None},
}


def collect_cases(travel, air_agent, date: str) -> list[dict]:
    """跑一遍全部案例，返回候选集与画像上下文（供调参与盲测复用）。"""
    from agents.meteorology_agent import run as meteorology_run
    conditions = {}
    for slot, (h0, h1) in SLOTS.items():
        start, end = f"{date} {h0}", f"{date} {h1}"
        meteo = meteorology_run({"task_id": f"SB_{slot}_meteo",
                                 "station_id": STATION["station_id"],
                                 "lat": STATION["lat"], "lon": STATION["lon"],
                                 "start_time": start, "end_time": end})
        air = air_agent.run({"task_id": f"SB_{slot}_air",
                             "station_name": STATION["station_name"],
                             "start_time": start, "end_time": end})
        conditions[slot] = {"start": start, "end": end, "meteo": meteo, "air": air,
                            "anomaly": (air.get("result") or {}).get("anomaly_score")}
    units = []
    for slot in SLOTS:
        cond = conditions[slot]
        for case in CASES:
            for profile in PROFILES:
                task = {"task_id": f"SB_{date}_{case['case_id']}_{profile}_{slot}",
                        "origin": case["origin"], "destination": case["destination"],
                        "start_time": cond["start"], "end_time": cond["end"],
                        "travel_profile": profile,
                        "pollution_source": case["pollution_source"],
                        "air_quality_risk": cond["anomaly"]}
                out = travel.run(dict(task), meteorology_result=cond["meteo"],
                                 air_quality_result=cond["air"])
                cands = [_compact(o) for o in (out.get("result") or {})
                         .get("route_options") or []]
                cands = [c for c in cands
                         if isinstance(c["duration_minutes"], float)
                         and isinstance(c["intake_index"], float)]
                if len(cands) < 2:
                    continue
                units.append({"case": case["case_id"], "profile": profile,
                              "slot": slot, "candidates": cands})
    return units


def feasible_set(cands: list[dict]) -> list[dict]:
    cap = CAP_RATIO * min(c["intake_index"] for c in cands)
    return [c for c in cands if c["intake_index"] <= cap]


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.travel_agent import TravelAgent

    travel = TravelAgent()
    air_agent = AirQualityAgent()

    # ── 第一步：开发日调 λ*（目标 = 平均归一化 HV） ──
    print("=== 开发日调参（2023-10-18）===", flush=True)
    dev_units = collect_cases(travel, air_agent, DATE_DEV)
    dev_feasible = [u | {"feasible": feasible_set(u["candidates"])}
                    for u in dev_units]
    dev_feasible = [u for u in dev_feasible if u["feasible"]]
    trials = []
    for lam in LAMBDA_GRID:
        hvs = [hypervolume_norm(pick_weighted(u["feasible"], lam), u["feasible"])
               for u in dev_feasible]
        trials.append({"lambda": lam, "mean_hv": round(statistics.mean(hvs), 4)})
    best = max(trials, key=lambda t: t["mean_hv"])
    lambda_star = best["lambda"]
    print(f"λ 网格: {trials}\nλ* = {lambda_star}（开发集 HV {best['mean_hv']}，已冻结）",
          flush=True)

    # ── 第二步：盲测日评估 ──
    print("=== 盲测日（2024-05-10）===", flush=True)
    units = collect_cases(travel, air_agent, DATE_BLIND)
    rows = []
    for u in units:
        feasible = feasible_set(u["candidates"])
        if not feasible:
            continue
        cap = CAP_RATIO * min(c["intake_index"] for c in u["candidates"])
        t_min = min(c["duration_minutes"] for c in feasible)
        e_min = min(c["intake_index"] for c in feasible)
        profile = u["profile"]
        picks = {
            "A_shortest": min(feasible, key=lambda c: (c["duration_minutes"],
                                                       c["intake_index"])),
            "B_lowest": min(feasible, key=lambda c: (c["intake_index"],
                                                     c["duration_minutes"])),
            "C_fixed20": pick_weighted(feasible, LAMBDA_FIXED),
            "C_tuned": pick_weighted(feasible, lambda_star),
            "D_zhikan": decision_zhikan(feasible, cap),
        }

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
            return {"hv": hypervolume_norm(pick, feasible),
                    "pref_v1": satisfies_preference(pick, profile, t_min, e_min),
                    "pref_v2": v2_ok}

        row = {"case": u["case"], "profile": profile, "slot": u["slot"],
               "n_feasible": len(feasible)}
        for name, pick in picks.items():
            row[name] = {"route_id": pick.get("route_id"),
                         "duration_minutes": pick["duration_minutes"],
                         "intake_index": pick["intake_index"], **flags(pick)}

        # 扰动后的实际优化损失（HV 损失取正部）
        def perturb_loss(pick: dict, perturbed_picks: list[dict]) -> dict:
            base_hv = hypervolume_norm(pick, feasible)
            losses = [max(0.0, base_hv - hypervolume_norm(p, feasible))
                      for p in perturbed_picks if p is not None]
            return {"mean_hv_loss": round(statistics.mean(losses), 4) if losses else None,
                    "max_hv_loss": round(max(losses), 4) if losses else None}

        row["C_tuned_perturb"] = perturb_loss(
            picks["C_tuned"], [pick_weighted(feasible, lambda_star * f)
                               for f in (0.5, 2.0)])
        row["D_perturb"] = perturb_loss(
            picks["D_zhikan"], [zhikan_with_exchange(feasible, 8.0 * f)
                                for f in (0.5, 2.0)])
        # 扰动后偏好违反变化
        def pref_v2_violations(pick: dict) -> bool:
            v2 = RUBRIC_V2[profile]
            ok = True
            if v2.get("modes") and pick.get("mode") not in v2["modes"]:
                ok = False
            if v2.get("max_time_ratio") and pick["duration_minutes"] > \
                    v2["max_time_ratio"] * t_min:
                ok = False
            if v2.get("max_intake_ratio") and pick["intake_index"] > \
                    v2["max_intake_ratio"] * e_min:
                ok = False
            return ok
        row["C_tuned_perturb_pref_loss"] = statistics.mean(
            0.0 if pref_v2_violations(p) else 1.0
            for p in (pick_weighted(feasible, lambda_star * f) for f in (0.5, 2.0))
        ) if picks["C_tuned"] else None
        row["D_perturb_pref_loss"] = statistics.mean(
            0.0 if pref_v2_violations(p) else 1.0
            for p in (zhikan_with_exchange(feasible, 8.0 * f) for f in (0.5, 2.0))
        ) if picks["D_zhikan"] else None
        rows.append(row)

    # ── 汇总 ────────────────────────────────────────────────────────────────
    ARMS = ("A_shortest", "B_lowest", "C_fixed20", "C_tuned", "D_zhikan")

    def summarise(arm: str) -> dict:
        entries = [r[arm] for r in rows]
        return {"n": len(rows),
                "mean_hv": round(statistics.mean(e["hv"] for e in entries), 4),
                "pref_v1": round(statistics.mean(1.0 if e["pref_v1"] else 0.0
                                                 for e in entries), 4),
                "pref_v2": round(statistics.mean(1.0 if e["pref_v2"] else 0.0
                                                 for e in entries), 4)}

    summary = {arm: summarise(arm) for arm in ARMS}
    summary["perturbation_loss"] = {
        "C_tuned": {"mean_hv_loss": round(statistics.mean(
            r["C_tuned_perturb"]["mean_hv_loss"] for r in rows), 4),
            "pref_v2_violation_rate_under_perturb": round(statistics.mean(
                r["C_tuned_perturb_pref_loss"] for r in rows), 4)},
        "D": {"mean_hv_loss": round(statistics.mean(
            r["D_perturb"]["mean_hv_loss"] for r in rows), 4),
            "pref_v2_violation_rate_under_perturb": round(statistics.mean(
                r["D_perturb_pref_loss"] for r in rows), 4)},
    }

    # 分画像 HV
    by_profile = {}
    for profile in PROFILES:
        subset = [r for r in rows if r["profile"] == profile]
        by_profile[profile] = {arm: round(statistics.mean(
            r[arm]["hv"] for r in subset), 4) for arm in ARMS}

    # 聚类 bootstrap（OD×slot）
    clusters = {}
    for r in rows:
        clusters.setdefault((r["case"], r["slot"]), []).append(r)
    rng = random.Random(42)

    def cluster_diff(metric_fn, x_arm, y_arm, reps=10000) -> dict:
        values = [metric_fn(unit, x_arm) - metric_fn(unit, y_arm)
                  for unit in clusters.values()]
        samples = sorted(statistics.mean(
            values[rng.randrange(len(values))] for _ in range(len(values)))
            for _ in range(reps))
        return {"mean_diff": round(statistics.mean(values), 4),
                "ci95": [round(samples[int(0.025 * reps)], 4),
                         round(samples[int(0.975 * reps)], 4)],
                "n_clusters": len(clusters)}

    hv_fn = lambda unit, arm: statistics.mean(e[arm]["hv"] for e in unit)
    pref_fn = lambda unit, arm: statistics.mean(
        1.0 if e[arm]["pref_v2"] else 0.0 for e in unit)
    comparisons = {
        "D_vs_C_tuned_hv": cluster_diff(hv_fn, "D_zhikan", "C_tuned"),
        "D_vs_C_tuned_pref_v2": cluster_diff(pref_fn, "D_zhikan", "C_tuned"),
    }

    payload = {
        "design": {
            "note": "V9.3 强基线：λ 在开发集按 HV 调优后冻结（区别于 V9.2 的固定 λ=20）；"
                    "稳定性以扰动后实际 HV/偏好损失度量，而非仅翻选率；"
                    "结论限定『预设画像与约束下的优化表现』",
            "dev_date": DATE_DEV, "blind_date": DATE_BLIND,
            "lambda_grid": LAMBDA_GRID, "lambda_star": lambda_star,
            "cluster_unit": "OD×时段",
        },
        "lambda_trials": trials,
        "summary": summary,
        "by_profile_hv": by_profile,
        "comparisons_cluster_bootstrap": comparisons,
        "rows": rows,
    }
    path = save_result("v9_travel_strongbase", payload)
    print(f"\nsaved -> {path}")
    print("=== V9.3 强基线对比（盲测日 2024-05-10）===")
    for arm in ARMS:
        s = summary[arm]
        print(f"  {arm:<11} HV={s['mean_hv']} pref_v1={s['pref_v1']} "
              f"pref_v2={s['pref_v2']}")
    print("扰动损失:", json.dumps(summary["perturbation_loss"], ensure_ascii=False))
    print("分画像 HV:", json.dumps(by_profile, ensure_ascii=False))
    for k, v in comparisons.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
