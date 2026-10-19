"""V9.4 Experiment: innovation-3 decision differentiation & strong baselines.

Answers "what can the 智瞰 Pareto+utility decision solve that a tuned weighted
sum cannot?" with four automation-only probes:

  1. Candidate-frontier diagnosis (from the V9.3 blind-day rows): how many
     non-dominated candidates per OD, how often the pick differs from the
     shortest-time arm.
  2. Strong baselines on identical candidates/constraints, tuned on the dev
     date: weighted sum (lambda*), epsilon-constraint (min time s.t. EU <= eps*),
     and weighted Tchebycheff (min max(w1*t_norm, w2*e_norm)).
  3. Decision regret: per-profile objective fixed a priori (rubric v1/v2 as
     the target), regret = objective(recommendation) − objective(best feasible
     for that objective), on the unseen cases.
  4. Non-convex-frontier unit test: synthetic candidate sets whose Pareto
     front is concave; boundary scalarisations (weighted sum AND the utility
     form, which is linear in (time, EU)) provably cannot reach non-convex
     frontier points — measured directly, per method.

Conclusions limited to "optimisation performance under pre-registered
profiles and constraints"; no real-user claims.

Usage: /usr/bin/python3 tests/benchmarks/v9/travel_differentiation.py
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

CAP_RATIO = 1.25


def pick_epsilon(cands: list[dict], eps: float) -> dict | None:
    """ε-约束法：时间最短且 EU ≤ ε。"""
    feasible = [c for c in cands if c["intake_index"] <= eps]
    return min(feasible, key=lambda c: (c["duration_minutes"], c["intake_index"])) \
        if feasible else None


def pick_tchebycheff(cands: list[dict], w_time: float, w_eu: float) -> dict | None:
    """加权 Tchebycheff：min max(w1·t_norm, w2·e_norm)。"""
    if not cands:
        return None
    t_min = min(c["duration_minutes"] for c in cands)
    t_max = max(c["duration_minutes"] for c in cands)
    e_min = min(c["intake_index"] for c in cands)
    e_max = max(c["intake_index"] for c in cands)
    span_t = max(t_max - t_min, 1e-9)
    span_e = max(e_max - e_min, 1e-9)

    def score(c: dict) -> float:
        return max(w_time * (c["duration_minutes"] - t_min) / span_t,
                   w_eu * (c["intake_index"] - e_min) / span_e)

    return min(cands, key=score)


def nonconvex_frontier_unit_test() -> dict:
    """非凸前沿单元测试：线性标量化能否到达前沿的非凸段。

    构造一个凹（非凸）Pareto 前沿：EU 固定档位、时间按凹函数下降。
    沿前沿扫描：加权和（含智瞰的效用式，都是 (t, EU) 的线性标量化）
    只能命中凸包上的点；Tchebycheff 可命中非凸点。直接测量命中率。
    """
    # 非凸前沿：e 随 t 超线性下降（斜率递增）→ Pareto 前沿含非凸段
    # e = 1 - (i/8)^2.2：一阶导递增，使下凸包严格小于前沿本身
    candidates = []
    for i in range(9):
        t = 10.0 + 3.0 * i
        e = 1.0 - (i / 8.0) ** 2.2
        candidates.append({"mode": "drive", "route_id": f"c{i}",
                           "duration_minutes": round(t, 2),
                           "intake_index": round(e, 4),
                           "utility_minutes": round(t + 20.0 * e, 4)})

    # 真 Pareto 集（全部互不支配，构造保证）
    def dominated(c: dict) -> bool:
        return any(o is not c
                   and o["duration_minutes"] <= c["duration_minutes"]
                   and o["intake_index"] <= c["intake_index"]
                   and (o["duration_minutes"] < c["duration_minutes"]
                        or o["intake_index"] < c["intake_index"])
                   for o in candidates)
    frontier = [c for c in candidates if not dominated(c)]

    def convex_hull_ids() -> set[str]:
        """前沿的凸包点（下凸包：斜率单调）。"""
        ordered = sorted(frontier, key=lambda c: c["duration_minutes"])
        hull = []
        for c in ordered:
            while len(hull) >= 2:
                a, b = hull[-2], hull[-1]
                cross = ((b["duration_minutes"] - a["duration_minutes"])
                         * (c["intake_index"] - a["intake_index"])
                         - (b["intake_index"] - a["intake_index"])
                         * (c["duration_minutes"] - a["duration_minutes"]))
                if cross <= 0:      # 非下凸 → 弹出中点
                    hull.pop()
                else:
                    break
            hull.append(c)
        return {c["route_id"] for c in hull} | {ordered[0]["route_id"],
                                                ordered[-1]["route_id"]}

    hull_ids = convex_hull_ids()
    non_convex_ids = {c["route_id"] for c in frontier} - hull_ids

    # 加权和扫描（大量权重）能命中的前沿点
    weighted_hits = set()
    for k in range(1, 400):
        lam = k * 0.25
        pick = pick_weighted(frontier, lam)
        weighted_hits.add(pick["route_id"])
    # Tchebycheff 扫描
    tcheby_hits = set()
    for k in range(1, 100):
        w = k / 100.0
        pick = pick_tchebycheff(frontier, w, 1.0 - w)
        tcheby_hits.add(pick["route_id"])

    return {
        "frontier_size": len(frontier),
        "convex_hull_size": len(hull_ids),
        "non_convex_points": sorted(non_convex_ids),
        "weighted_sum_hits": sorted(weighted_hits),
        "weighted_sum_reaches_non_convex": sorted(weighted_hits & non_convex_ids),
        "tchebycheff_hits": sorted(tcheby_hits),
        "tchebycheff_reaches_non_convex": sorted(tcheby_hits & non_convex_ids),
        "conclusion": "线性标量化（加权和与智瞰效用式同为 (t, EU) 线性组合）无法到达"
                      "非凸前沿点；Tchebycheff 可达——差异化能力存在于算法族选择，"
                      "而非 Pareto 筛选本身",
    }


def main() -> int:
    # 复用 V9.3 盲测日数据（候选集不落盘，仅选择结果；前沿诊断需要候选集，
    # 因此这里对盲测日重跑一次 agent 以获得候选）
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.travel_agent import TravelAgent
    from bench_travel_multigoal import STATION, _compact, satisfies_preference
    from bench_travel_tradeoff import CASES, PROFILES, SLOTS
    from bench_travel_v8 import decision_zhikan

    RUBRIC = json.loads((HERE.parent / "results" / "travel_multigoal.json")
                        .read_text(encoding="utf-8"))["design"]["preference_rubric"]

    travel = TravelAgent()
    air_agent = AirQualityAgent()

    def collect(date: str) -> list[dict]:
        conditions = {}
        for slot, (h0, h1) in SLOTS.items():
            start, end = f"{date} {h0}", f"{date} {h1}"
            meteo = meteorology_run({"task_id": f"DF_{slot}_m",
                                     "station_id": STATION["station_id"],
                                     "lat": STATION["lat"], "lon": STATION["lon"],
                                     "start_time": start, "end_time": end})
            air = air_agent.run({"task_id": f"DF_{slot}_a",
                                 "station_name": STATION["station_name"],
                                 "start_time": start, "end_time": end})
            conditions[slot] = {"meteo": meteo, "air": air, "start": start, "end": end,
                                "anomaly": (air.get("result") or {}).get("anomaly_score")}
        units = []
        for slot in SLOTS:
            cond = conditions[slot]
            for case in CASES:
                for profile in PROFILES:
                    task = {"task_id": f"DF_{date}_{case['case_id']}_{profile}_{slot}",
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

    dev_units = collect("2023-10-18")
    print(f"开发日单元 {len(dev_units)}", flush=True)

    def feasible(u: dict) -> list[dict]:
        cap = CAP_RATIO * min(c["intake_index"] for c in u["candidates"])
        return [c for c in u["candidates"] if c["intake_index"] <= cap]

    # 开发集调三个强基线参数（目标：平均 HV）
    dev_feas = [(u, feasible(u)) for u in dev_units if feasible(u)]
    lam_grid = [5.0, 10.0, 15.0, 20.0, 30.0, 40.0, 60.0]
    lam_star = max(lam_grid, key=lambda lam: statistics.mean(
        hypervolume_norm(pick_weighted(f, lam), f) for _, f in dev_feas))
    eps_ratio_grid = [1.05, 1.10, 1.15, 1.25, 1.40]
    eps_star = max(eps_ratio_grid, key=lambda r: statistics.mean(
        hypervolume_norm(pick_epsilon(f, r * min(c["intake_index"] for c in f)), f)
        for _, f in dev_feas))
    w_grid = [i / 20.0 for i in range(1, 20)]
    w_star = max(w_grid, key=lambda w: statistics.mean(
        hypervolume_norm(pick_tchebycheff(f, w, 1.0 - w), f) for _, f in dev_feas))
    print(f"开发集调优: λ*={lam_star} ε*={eps_star}（比率） w*={w_star:.2f}", flush=True)

    # 盲测日（新日期 2024-06-14）
    blind_units = collect("2024-06-14")
    print(f"盲测日单元 {len(blind_units)}", flush=True)

    rows = []
    for u in blind_units:
        f = feasible(u)
        if not f:
            continue
        t_min = min(c["duration_minutes"] for c in f)
        e_min = min(c["intake_index"] for c in f)
        cap = CAP_RATIO * e_min
        # 前沿规模
        order_t = sorted(f, key=lambda c: c["duration_minutes"])
        frontier, best_e = [], math.inf
        for c in order_t:
            if c["intake_index"] < best_e - 1e-12:
                frontier.append(c)
                best_e = c["intake_index"]
        picks = {
            "A_shortest": min(f, key=lambda c: (c["duration_minutes"], c["intake_index"])),
            "B_lowest": min(f, key=lambda c: (c["intake_index"], c["duration_minutes"])),
            "C_weighted_tuned": pick_weighted(f, lam_star),
            "E_epsilon_tuned": pick_epsilon(f, eps_star * e_min),
            "F_tchebycheff_tuned": pick_tchebycheff(f, w_star, 1.0 - w_star),
            "D_zhikan": decision_zhikan(f, cap),
        }

        def profile_caps(profile: str) -> tuple[float, float]:
            """画像细则给出的 (时间上限, 暴露上限)。跑者无时间约束 → inf。"""
            rule = RUBRIC[profile]
            tr = rule.get("max_time_ratio")
            er = rule.get("max_intake_ratio")
            return (tr * t_min if tr is not None else math.inf,
                    er * e_min if er is not None else math.inf)

        def regrets(pick: dict, profile: str) -> dict:
            """决策后悔值：相对**同一画像可行集**最优解的目标损失（恒非负）。

            V9.5 修正：旧实现里 best_t 只在"满足 EU 上限"的子集上取最小时间，
            而被选方案若 EU 超上限则可能时间更短 → 后悔为负（审查发现）。
            现在统一定义：参照集 F_p = 同时满足两个比率上限的画像可行集；
            最优时间/EU 均取自 F_p；被选方案不在 F_p 时后悔记为 None 并
            显式标记 feasible_pick=False，由汇总单独统计不可行率。
            """
            time_cap, eu_cap = profile_caps(profile)
            feasible_pool = [c for c in f
                             if c["duration_minutes"] <= time_cap
                             and c["intake_index"] <= eu_cap]
            in_profile = (pick["duration_minutes"] <= time_cap
                          and pick["intake_index"] <= eu_cap)
            if feasible_pool and in_profile:
                best_t = min(c["duration_minutes"] for c in feasible_pool)
                best_e = min(c["intake_index"] for c in feasible_pool)
                regret_time = round(pick["duration_minutes"] - best_t, 3)
                regret_eu = round(pick["intake_index"] - best_e, 4)
            else:
                regret_time = regret_eu = None
            return {"regret_time": regret_time, "regret_eu": regret_eu,
                    "feasible_pick": in_profile,
                    "pref_v1": satisfies_preference(pick, profile, t_min, e_min)}

        row = {"case": u["case"], "profile": u["profile"], "slot": u["slot"],
               "frontier_size": len(frontier), "n_feasible": len(f)}
        for name, pick in picks.items():
            row[name] = {"route_id": pick.get("route_id"),
                         "duration_minutes": pick["duration_minutes"],
                         "intake_index": pick["intake_index"],
                         "hv": hypervolume_norm(pick, f),
                         **regrets(pick, u["profile"])}
        row["D_differs_from_A"] = (picks["D_zhikan"]["route_id"]
                                   != picks["A_shortest"]["route_id"])
        rows.append(row)

    ARMS = ("A_shortest", "B_lowest", "C_weighted_tuned", "E_epsilon_tuned",
            "F_tchebycheff_tuned", "D_zhikan")
    def _mean(values: list, digits: int):
        vals = [v for v in values if v is not None]
        return round(statistics.mean(vals), digits) if vals else None

    summary = {arm: {
        "mean_hv": round(statistics.mean(r[arm]["hv"] for r in rows), 4),
        "mean_regret_time": _mean([r[arm]["regret_time"] for r in rows], 3),
        "mean_regret_eu": _mean([r[arm]["regret_eu"] for r in rows], 4),
        "infeasible_pick_rate": round(statistics.mean(
            1.0 if not r[arm]["feasible_pick"] else 0.0 for r in rows), 4),
        "regret_n": sum(1 for r in rows if r[arm]["regret_time"] is not None),
        "pref_v1": round(statistics.mean(
            1.0 if r[arm]["pref_v1"] else 0.0 for r in rows), 4),
    } for arm in ARMS}

    frontier_stats = {
        "mean_frontier_size": round(statistics.mean(r["frontier_size"] for r in rows), 2),
        "frontier_size_distribution": {
            k: sum(1 for r in rows if r["frontier_size"] == k)
            for k in sorted({r["frontier_size"] for r in rows})},
        "D_differs_from_A_rate": round(statistics.mean(
            1.0 if r["D_differs_from_A"] else 0.0 for r in rows), 4),
    }

    # 聚类 bootstrap（OD×slot）：D 相对三个强基线的 HV 差
    clusters = {}
    for r in rows:
        clusters.setdefault((r["case"], r["slot"]), []).append(r)
    rng = random.Random(42)

    def cluster_diff(x_arm: str, y_arm: str, key: str = "hv") -> dict:
        # 后悔类键可能为 None（不可行推荐）——配对时要求两侧都非 None
        pairs = [unit for unit in clusters.values()
                 if all(e[x_arm][key] is not None and e[y_arm][key] is not None
                        for e in unit)]
        values = [statistics.mean(e[x_arm][key] for e in unit)
                  - statistics.mean(e[y_arm][key] for e in unit)
                  for unit in pairs]
        samples = sorted(statistics.mean(values[rng.randrange(len(values))]
                                         for _ in range(len(values)))
                         for _ in range(10000))
        return {"mean_diff": round(statistics.mean(values), 4),
                "ci95": [round(samples[250], 4), round(samples[9750], 4)],
                "n_clusters": len(clusters)}

    comparisons = {
        f"D_vs_{arm}_{key}": cluster_diff("D_zhikan", arm, key)
        for arm in ("C_weighted_tuned", "E_epsilon_tuned", "F_tchebycheff_tuned")
        for key in ("hv",)
    }
    comparisons["D_vs_C_weighted_tuned_regret_time"] = cluster_diff(
        "D_zhikan", "C_weighted_tuned", "regret_time")

    payload = {
        "design": {
            "note": "V9.4 创新三差异化：同候选同约束下引入 ε-约束法与 Tchebycheff 强基线"
                    "（开发集按 HV 调优后冻结），新盲测日 2024-06-14；决策后悔值按预设"
                    "画像目标计算；非凸前沿单元测试检验标量化族的可达性边界",
            "cap_ratio": CAP_RATIO,
            "dev_tuned": {"lambda_star": lam_star, "eps_ratio_star": eps_star,
                          "w_star": round(w_star, 3)},
            "cluster_unit": "OD×时段",
        },
        "frontier_diagnosis": frontier_stats,
        "summary": summary,
        "comparisons_cluster_bootstrap": comparisons,
        "nonconvex_frontier_unit_test": nonconvex_frontier_unit_test(),
        "rows": rows,
    }
    path = save_result("v94_travel_differentiation", payload)
    print(f"\nsaved -> {path}")
    print("=== 前沿诊断 ===")
    print(json.dumps(frontier_stats, ensure_ascii=False))
    print("\n=== 六臂对比（盲测日 2024-06-14）===")
    for arm in ARMS:
        s = summary[arm]
        print(f"  {arm:<22} HV={s['mean_hv']} 时间后悔={s['mean_regret_time']}"
              f"(n={s['regret_n']}) EU后悔={s['mean_regret_eu']} "
              f"不可行率={s['infeasible_pick_rate']} pref_v1={s['pref_v1']}")
    print("\n聚类 CI:", json.dumps(comparisons, ensure_ascii=False))
    print("\n非凸前沿单元测试:",
          json.dumps(payload["nonconvex_frontier_unit_test"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
