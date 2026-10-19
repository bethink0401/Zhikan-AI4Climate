"""Benchmark V8: 2x2 ablation — candidate generation x decision maker.

Per the V8 plan the contribution of (a) richer route candidates and (b) the
personalised multi-objective decision must be separated, not confounded:

    A = base candidates     x base decision  (single-objective, lowest EU
                                               within the exposure cap)
    B = base candidates     x 智瞰 decision   (utility ordering + Pareto screen)
    C = enhanced candidates x base decision
    D = enhanced candidates x 智瞰 decision   (the full system)

Candidate sets (both derived from ONE agent run per case, so the comparison
is exact and not confounded by upstream stochasticity):
    base     = per road mode, the fastest and the lowest-EU route + transit
               options — mirrors the pre-V7 generation structure;
    enhanced = the full V7 candidate set (fastest + balanced + strongest
               avoidance + transit).

Blind date 2023-11-29 (Wednesday) — never used in any earlier round.
Constraint rule unchanged from V6: exposure cap = 1.25 x (best EU), fixed a
priori, identical for every arm.

Preference satisfaction is scored against BOTH the original rubric and the
independent rubric v2 introduced in V7. Cluster bootstrap (resampling the 12
OD x slot units) gives CIs for arm differences, avoiding the pseudo-replication
of treating the 5 profiles of one OD as independent observations.

Usage: /usr/bin/python3 tests/benchmarks/bench_travel_v8.py
"""
from __future__ import annotations

import json
import random
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

DATE_BLIND = "2023-11-29"   # 周三，V8 全新盲测日（任何历史轮次未见过）
CAP_RATIO = 1.25            # 暴露上限 = 候选集最优 EU 的 1.25 倍（先验固定）

#: 独立细则 v2（与 V7 相同，跑前固定）
RUBRIC_V2 = {
    "commuter": {"max_time_ratio": 1.15, "max_intake_ratio": 1.30, "modes": None},
    "parent_with_child": {"max_time_ratio": 1.60, "max_intake_ratio": 1.10, "modes": None},
    "elderly": {"max_time_ratio": 1.40, "max_intake_ratio": 1.20,
                "modes": ["transit", "walk"]},
    "runner": {"max_time_ratio": None, "max_intake_ratio": 1.10,
               "modes": ["walk", "transit"]},
    "tourist": {"max_time_ratio": 1.50, "max_intake_ratio": 1.50, "modes": None},
}


def base_candidate_set(candidates: list[dict]) -> list[dict]:
    """基础候选集：每种地面方式保留最快与最净两条 + 全部轨道候选。

    对应 V6 之前的生成结构（常规搜索 + 最强避污搜索），其选择恰好是
    全集里各方式的"时间/暴露"两端；折中候选（V7 新增）被剔除。
    """
    kept: dict[tuple[str, str], dict] = {}
    modes = sorted({c["mode"] for c in candidates})
    for mode in modes:
        subset = [c for c in candidates if c["mode"] == mode]
        if not subset:
            continue
        kept[(mode, "fastest")] = min(subset, key=lambda c: c["duration_minutes"])
        kept[(mode, "cleanest")] = min(subset, key=lambda c: c["intake_index"])
    return list(kept.values())


def decision_base(candidates: list[dict], cap: float) -> dict | None:
    """基础决策器：暴露上限内的单目标规则（最净优先，并列取最快）。"""
    feasible = [c for c in candidates if c["intake_index"] <= cap]
    if not feasible:
        return None
    return min(feasible, key=lambda c: (c["intake_index"], c["duration_minutes"]))


def decision_zhikan(candidates: list[dict], cap: float) -> dict | None:
    """智瞰决策器：约束内 Pareto 非支配筛选 + 效用最优（与系统内部一致）。"""
    feasible = [c for c in candidates if c["intake_index"] <= cap]
    if not feasible:
        return None

    def dominated(option: dict) -> bool:
        for other in feasible:
            if other is option:
                continue
            if (other["duration_minutes"] <= option["duration_minutes"]
                    and other["intake_index"] <= option["intake_index"]
                    and (other["duration_minutes"] < option["duration_minutes"]
                         or other["intake_index"] < option["intake_index"])):
                return True
        return False

    frontier = [c for c in feasible if not dominated(c)]
    return min(frontier, key=lambda c: c["utility_minutes"])


def is_nondominated_in(pick: dict, feasible: list[dict]) -> bool:
    for other in feasible:
        if other is pick:
            continue
        if (other["duration_minutes"] <= pick["duration_minutes"]
                and other["intake_index"] <= pick["intake_index"]
                and (other["duration_minutes"] < pick["duration_minutes"]
                     or other["intake_index"] < pick["intake_index"])):
            return False
    return True


def cluster_bootstrap_diff(pairs: list[tuple[float, float]], reps: int = 10000,
                           seed: int = 42) -> dict:
    """聚类 bootstrap：pairs = (arm_x, arm_y) 按聚类单位（OD×slot）配对。

    每个聚类单位含 5 个画像的指标均值；重采样聚类单位而非个体案例，
    避免同一 OD 的多个案例被当作独立观测。
    """
    if not pairs:
        return {}
    rng = random.Random(seed)
    n = len(pairs)
    diffs = [x - y for x, y in pairs]
    samples = []
    for _ in range(reps):
        chosen = [pairs[rng.randrange(n)] for _ in range(n)]
        samples.append(statistics.mean(x - y for x, y in chosen))
    samples.sort()
    return {"mean_diff": round(statistics.mean(diffs), 4),
            "ci95": [round(samples[int(0.025 * reps)], 4),
                     round(samples[int(0.975 * reps)], 4)],
            "n_clusters": n}


def main() -> int:
    from agents.air_quality_agent import AirQualityAgent
    from agents.meteorology_agent import run as meteorology_run
    from agents.travel_agent import TravelAgent

    travel = TravelAgent()
    air_agent = AirQualityAgent()

    rubric_source = json.loads(
        (Path(__file__).resolve().parent / "results" / "travel_multigoal.json")
        .read_text(encoding="utf-8"))
    rubric_v1 = rubric_source["design"]["preference_rubric"]

    conditions = {}
    for slot, (h0, h1) in SLOTS.items():
        start, end = f"{DATE_BLIND} {h0}", f"{DATE_BLIND} {h1}"
        meteo = meteorology_run({"task_id": f"V8_{slot}_meteo",
                                 "station_id": STATION["station_id"],
                                 "lat": STATION["lat"], "lon": STATION["lon"],
                                 "start_time": start, "end_time": end})
        air = air_agent.run({"task_id": f"V8_{slot}_air",
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
                    "task_id": f"V8_{case['case_id']}_{profile}_{slot}",
                    "origin": case["origin"], "destination": case["destination"],
                    "start_time": cond["start"], "end_time": cond["end"],
                    "travel_profile": profile,
                    "pollution_source": case["pollution_source"],
                    "air_quality_risk": cond["anomaly"],
                }
                t0 = _time.perf_counter()
                out = travel.run(dict(task), meteorology_result=cond["meteo"],
                                 air_quality_result=cond["air"])
                run_ms = (_time.perf_counter() - t0) * 1000.0
                enhanced = [_compact(o) for o in (out.get("result") or {})
                            .get("route_options") or []]
                enhanced = [c for c in enhanced
                            if isinstance(c["duration_minutes"], float)
                            and isinstance(c["intake_index"], float)]
                if not enhanced:
                    continue
                base = base_candidate_set(enhanced)
                cap = CAP_RATIO * min(c["intake_index"] for c in enhanced)
                # 系统以同 cap 的 user_constraints 在线运行（V9-P0 修复：V8 原版
                # 拿无约束系统推荐对比带 cap 离线决策，3/60 假性不一致）
                out_c = travel.run({**task,
                                    "user_constraints": {"max_intake": round(cap, 4)}},
                                   meteorology_result=cond["meteo"],
                                   air_quality_result=cond["air"])
                enhanced_c = [_compact(o) for o in (out_c.get("result") or {})
                              .get("route_options") or []]
                system_pick = next((c for c in enhanced_c if c.get("recommended")), None)

                t_min = min(c["duration_minutes"] for c in enhanced)
                e_min = min(c["intake_index"] for c in enhanced)
                rubric_v2 = RUBRIC_V2[profile]

                def pref_flags(pick: dict) -> dict:
                    v2_ok = True
                    if rubric_v2.get("modes") and pick.get("mode") not in rubric_v2["modes"]:
                        v2_ok = False
                    if rubric_v2.get("max_time_ratio") and pick["duration_minutes"] > \
                            rubric_v2["max_time_ratio"] * t_min:
                        v2_ok = False
                    if rubric_v2.get("max_intake_ratio") and pick["intake_index"] > \
                            rubric_v2["max_intake_ratio"] * e_min:
                        v2_ok = False
                    return {"pref_v1": satisfies_preference(pick, profile, t_min, e_min),
                            "pref_v2": v2_ok}

                def entry_for(pick: dict | None, cand_set: list[dict]) -> dict | None:
                    if pick is None:
                        return {"status": "infeasible"}
                    feasible = [c for c in cand_set if c["intake_index"] <= cap]
                    flags = pref_flags(pick)
                    return {
                        "mode": pick.get("mode"), "route_id": pick.get("route_id"),
                        "duration_minutes": round(pick["duration_minutes"], 2),
                        "intake_index": round(pick["intake_index"], 4),
                        "utility_minutes": round(pick.get("utility_minutes") or 0.0, 2),
                        "within_cap": pick["intake_index"] <= cap,
                        "nondominated": is_nondominated_in(pick, feasible) if feasible else False,
                        **flags,
                    }

                arms = {
                    "A_base_cand_base_dec": decision_base(base, cap),
                    "B_base_cand_zhikan_dec": decision_zhikan(base, cap),
                    "C_full_cand_base_dec": decision_base(enhanced, cap),
                    "D_full_cand_zhikan_dec": decision_zhikan(enhanced, cap),
                }
                row = {
                    "case": case["case_id"], "profile": profile, "slot": slot,
                    "cap": round(cap, 4),
                    "n_candidates_enhanced": len(enhanced),
                    "n_candidates_base": len(base),
                    "run_ms": round(run_ms, 1),
                }
                for name, pick in arms.items():
                    row[name] = entry_for(pick, base if name.startswith(("A", "B"))
                                          else enhanced)
                # D 臂与系统真实推荐的一致性校验
                row["D_matches_system"] = (
                    system_pick is not None and arms["D_full_cand_zhikan_dec"] is not None
                    and arms["D_full_cand_zhikan_dec"].get("route_id") == system_pick.get("route_id")
                    and arms["D_full_cand_zhikan_dec"].get("mode") == system_pick.get("mode")
                )  # V9-P0 修复后：系统在线接收同 cap 约束，口径对齐
                rows.append(row)
                d_entry = row["D_full_cand_zhikan_dec"]
                print(f"  {slot[:7]} {case['case_id'][:22]:<22} {profile:<17} "
                      f"base={row['n_candidates_base']:>2} full={row['n_candidates_enhanced']:>2} "
                      f"D: {d_entry.get('mode', '-'):>7} t={d_entry.get('duration_minutes', '-')}",
                      flush=True)

    # ── 汇总 ────────────────────────────────────────────────────────────────
    ARM_KEYS = ("A_base_cand_base_dec", "B_base_cand_zhikan_dec",
                "C_full_cand_base_dec", "D_full_cand_zhikan_dec")

    def summarise(key: str) -> dict:
        valid = [r[key] for r in rows
                 if r[key] and r[key].get("duration_minutes") is not None]
        pref_v1 = [1.0 if e.get("pref_v1") else 0.0 for e in valid]
        pref_v2 = [1.0 if e.get("pref_v2") else 0.0 for e in valid]
        return {
            "n": len(rows),
            "strict_feasible": len([e for e in valid if e.get("within_cap")]),
            "infeasible": len(rows) - len(valid),
            "median_time_min": round(statistics.median(e["duration_minutes"] for e in valid), 2)
                               if valid else None,
            "mean_eu": round(statistics.mean(e["intake_index"] for e in valid), 4)
                       if valid else None,
            "pref_satisfaction_v1": round(statistics.mean(pref_v1), 4) if valid else None,
            "pref_satisfaction_v2": round(statistics.mean(pref_v2), 4) if valid else None,
            "nondominated_rate": round(statistics.mean(
                1.0 if e.get("nondominated") else 0.0 for e in valid), 4) if valid else None,
        }

    summary = {key: summarise(key) for key in ARM_KEYS}
    summary["D_matches_system_rate"] = (
        f"{sum(1 for r in rows if r['D_matches_system'])}/{len(rows)}")

    # 聚类 bootstrap（聚类单位 = OD×slot，共 12 个）
    clusters: dict[tuple, list[dict]] = {}
    for r in rows:
        clusters.setdefault((r["case"], r["slot"]), []).append(r)

    def cluster_metric(key: str, metric: str) -> list[float]:
        values = []
        for unit in clusters.values():
            entries = [u[key] for u in unit
                       if u[key] and u[key].get("duration_minutes") is not None]
            if metric == "eu":
                values.append(statistics.mean(e["intake_index"] for e in entries))
            elif metric == "time":
                values.append(statistics.mean(e["duration_minutes"] for e in entries))
            elif metric == "pref_v1":
                values.append(statistics.mean(1.0 if e.get("pref_v1") else 0.0
                                              for e in entries))
            elif metric == "pref_v2":
                values.append(statistics.mean(1.0 if e.get("pref_v2") else 0.0
                                              for e in entries))
        return values

    comparisons = {}
    for x, y, label in (
            ("D_full_cand_zhikan_dec", "B_base_cand_zhikan_dec", "candidates_effect"),
            ("D_full_cand_zhikan_dec", "C_full_cand_base_dec", "decision_effect"),
            ("D_full_cand_zhikan_dec", "A_base_cand_base_dec", "total_effect"),
            ("D_full_cand_zhikan_dec", "B_base_cand_zhikan_dec", "D_vs_B"),
    ):
        for metric in ("eu", "time", "pref_v1", "pref_v2"):
            pairs = list(zip(cluster_metric(x, metric), cluster_metric(y, metric)))
            comparisons[f"{label}/{metric}"] = cluster_bootstrap_diff(pairs)

    payload = {
        "design": {
            "note": "V8 2×2 消融：基础候选（各方式最快+最净+轨道）×增强候选（V7 全集含折中）"
                    "×基础决策（约束内单目标最净）×智瞰决策（约束内 Pareto+效用）。"
                    "四臂全部从同一次系统运行的候选集离线派生，上游完全一致；"
                    "D 臂与系统真实推荐的一致性单独校验。"
                    "【V9-P0 修订】系统推荐校验改为以同 cap 的 user_constraints 在线运行后"
                    "对比——原版拿无约束系统推荐对比带 cap 离线决策，产生 3/60 假性不一致；"
                    "修复后重放 60/60 一致（tests/benchmarks/v9/replay_v8.py）。",
            "blind_date": DATE_BLIND, "cap_ratio": CAP_RATIO,
            "cluster_unit": "case x slot（12 个聚类单位，每单位 5 画像）",
            "rubric_v2": RUBRIC_V2,
        },
        "summary": summary,
        "comparisons_cluster_bootstrap": comparisons,
        "rows": rows,
    }
    path = save_result("travel_v8", payload)
    print(f"\nsaved -> {path}")
    print("\n=== V8 2×2 消融（盲测日 2023-11-29）===")
    for name, s in summary.items():
        if isinstance(s, str):
            print(f"  {name}: {s}")
            continue
        print(f"  {name}: 可行={s['strict_feasible']}/{s['n']} "
              f"t={s['median_time_min']} EU={s['mean_eu']} "
              f"pref_v1={s['pref_satisfaction_v1']} pref_v2={s['pref_satisfaction_v2']} "
              f"nondom={s['nondominated_rate']}")
    print("\n聚类 bootstrap 差值（D 相对）:")
    for label, v in comparisons.items():
        print(f"  {label}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
