"""V5 final-round analysis: fair indicators, confidence intervals, frozen.

No algorithm changes happen in V5 — this is an evaluation-layer pass over the
already-collected independent test results:

  创新三 (travel_v4.json, blind date 2023-11-15):
    - constrained comparison (the fair questions):
        a) under the same EU cap, who gets closer to the best feasible time?
        b) under the same time budget, who satisfies more user preferences
           and gets closer to the best feasible EU?
        c) infeasible / dominated recommendation counts under the same hard
           constraints;
    - fixed statistics: full-sample mean differences and paired bootstrap CIs
      (the v3/v4 "median vs A" figures mixed differing-cases-only medians with
      full-sample means — both 口径 are now reported explicitly).

  创新一 (governance_baselines.json):
    - paired bootstrap CI for the C−B Macro-F1 difference on test windows;

  创新二 (propagation_baselines.json):
    - per-category paired bootstrap CI for C−B IoU (test scenarios);
    - physical plausibility notes for the recalibrated plume parameters.

Usage: /usr/bin/python3 tests/benchmarks/v5_analysis.py
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result  # noqa: E402

RESULTS = Path(__file__).resolve().parent / "results"

EU_CAPS = [("EU ≤ 1.10×e_min", 1.10), ("EU ≤ 1.25×e_min", 1.25), ("EU ≤ 1.50×e_min", 1.50)]
TIME_BUDGETS = [("t ≤ 1.10×t_min", 1.10), ("t ≤ 1.25×t_min", 1.25), ("t ≤ 1.50×t_min", 1.50)]
BOOT_REPS = 10000
METHODS = ("A_shortest_time", "B_lowest_intake", "C_multigoal")


def bootstrap_ci(values: list[float], reps: int = BOOT_REPS, seed: int = 42) -> dict:
    rng = random.Random(seed)
    n = len(values)
    if n == 0:
        return {}
    stats = sorted(statistics.mean(values[rng.randrange(n)] for _ in range(n))
                   for _ in range(reps))
    return {"mean": round(statistics.mean(values), 4),
            "ci95": [round(stats[int(0.025 * reps)], 4), round(stats[int(0.975 * reps)], 4)],
            "n": n}


def paired_diff_ci(xs: list[float], ys: list[float], reps: int = BOOT_REPS, seed: int = 42) -> dict:
    diffs = [x - y for x, y in zip(xs, ys)]
    out = bootstrap_ci(diffs, reps, seed)
    return out


def macro_f1_from(truths: list[str], preds: list[str]) -> float:
    classes = ["traffic_dominant", "local_accumulation", "regional_transport", "uncertain"]
    f1s = []
    for cls in classes:
        tp = sum(1 for t, p in zip(truths, preds) if t == cls and p == cls)
        fp = sum(1 for t, p in zip(truths, preds) if t != cls and p == cls)
        fn = sum(1 for t, p in zip(truths, preds) if t == cls and p != cls)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return statistics.mean(f1s)


# ── 创新三：约束条件下的公平比较 ────────────────────────────────────────

def travel_constrained(rows: list[dict]) -> dict:
    eu_caps = {name: {} for name in METHODS}
    time_budgets = {name: {} for name in METHODS}

    for cap_label, _ in EU_CAPS:
        for name in METHODS:
            eu_caps[name][cap_label] = {"feasible": 0, "infeasible": 0, "gaps": []}
    for budget_label, _ in TIME_BUDGETS:
        for name in METHODS:
            time_budgets[name][budget_label] = {
                "feasible": 0, "infeasible": 0, "eu_gaps": [], "pref_ok": 0, "n_pref": 0}

    for row in rows:
        candidates = row.get("candidates") or []
        methods_map = row.get("methods") or {}
        if not candidates:
            continue
        e_min = min(c["intake_index"] for c in candidates)
        t_min = min(c["duration_minutes"] for c in candidates)
        by_key = {(c["route_id"], c["mode"]): c for c in candidates}

        # (a) 相同 EU 上限：推荐是否可行、与该上限下最优可行时间的差距
        for cap_label, cap_ratio in EU_CAPS:
            cap = cap_ratio * e_min
            feasible_pool = [c for c in candidates if c["intake_index"] <= cap]
            if not feasible_pool:
                continue
            t_opt = min(c["duration_minutes"] for c in feasible_pool)
            for name in METHODS:
                entry = methods_map.get(name) or {}
                pick = by_key.get((entry.get("route_id"), entry.get("mode")))
                bucket = eu_caps[name][cap_label]
                if pick is None or pick["intake_index"] > cap:
                    bucket["infeasible"] += 1
                else:
                    bucket["feasible"] += 1
                    bucket["gaps"].append((pick["duration_minutes"] - t_opt) / t_opt)

        # (b) 相同时间预算：可行率、可行时的偏好满足与 EU 差距
        for budget_label, budget_ratio in TIME_BUDGETS:
            budget = budget_ratio * t_min
            feasible_pool = [c for c in candidates if c["duration_minutes"] <= budget]
            if not feasible_pool:
                continue
            e_opt = min(c["intake_index"] for c in feasible_pool)
            for name in METHODS:
                entry = methods_map.get(name) or {}
                pick = by_key.get((entry.get("route_id"), entry.get("mode")))
                bucket = time_budgets[name][budget_label]
                if pick is None or pick["duration_minutes"] > budget:
                    bucket["infeasible"] += 1
                    continue
                bucket["feasible"] += 1
                bucket["eu_gaps"].append((pick["intake_index"] - e_opt) / e_opt)
                if entry.get("pref_satisfied") is not None:
                    bucket["n_pref"] += 1
                    bucket["pref_ok"] += 1 if entry["pref_satisfied"] else 0

    def summarise_buckets(buckets: dict) -> dict:
        out = {}
        for label, bucket in buckets.items():
            entry = {
                "feasible": bucket["feasible"], "infeasible": bucket["infeasible"],
                "feasible_rate": round(bucket["feasible"] /
                                       max(1, bucket["feasible"] + bucket["infeasible"]), 3),
            }
            if bucket.get("gaps"):
                entry["median_time_gap_vs_opt"] = round(statistics.median(bucket["gaps"]), 4)
                entry["mean_time_gap_vs_opt"] = round(statistics.mean(bucket["gaps"]), 4)
            if bucket.get("eu_gaps"):
                entry["median_eu_gap_vs_opt"] = round(statistics.median(bucket["eu_gaps"]), 4)
            if bucket.get("n_pref"):
                entry["pref_satisfaction_feasible"] = round(bucket["pref_ok"] / bucket["n_pref"], 3)
            out[label] = entry
        return out

    return {"eu_cap_comparison": {name: summarise_buckets(eu_caps[name]) for name in METHODS},
            "time_budget_comparison": {name: summarise_buckets(time_budgets[name]) for name in METHODS}}


def travel_fixed_stats(rows: list[dict]) -> dict:
    """修正口径：全样本均值差 + 全样本中位比率 + 配对 bootstrap CI。"""
    out = {}
    for name in ("B_lowest_intake", "C_multigoal"):
        times_m, eus_m, time_ratios, eu_ratios = [], [], [], []
        times_a, eus_a = [], []
        for row in rows:
            a = (row.get("methods") or {}).get("A_shortest_time") or {}
            m = (row.get("methods") or {}).get(name) or {}
            if "duration_minutes" not in a or "duration_minutes" not in m:
                continue
            times_a.append(a["duration_minutes"])
            eus_a.append(a["intake_index"])
            times_m.append(m["duration_minutes"])
            eus_m.append(m["intake_index"])
            time_ratios.append(m["duration_minutes"] / a["duration_minutes"])
            eu_ratios.append(m["intake_index"] / a["intake_index"])
        out[name] = {
            "n": len(times_a),
            "mean_time_vs_A_pct": round((statistics.mean(times_m) / statistics.mean(times_a) - 1) * 100, 2),
            "mean_eu_vs_A_pct": round((statistics.mean(eus_m) / statistics.mean(eus_a) - 1) * 100, 2),
            "median_time_ratio_vs_A": round(statistics.median(time_ratios), 4),
            "median_eu_ratio_vs_A": round(statistics.median(eu_ratios), 4),
            "eu_ratio_vs_A_ci": paired_diff_ci(eu_ratios, [1.0] * len(eu_ratios)),
            "eu_diff_pairwise_ci": paired_diff_ci(eus_m, eus_a),
        }
    return out


def feasibility_counts(rows: list[dict]) -> dict:
    """同一套硬约束下：支配推荐数、偏好违反数。"""
    out = {}
    for name in METHODS:
        dominated = pref_viol = total = 0
        for row in rows:
            entry = (row.get("methods") or {}).get(name) or {}
            if "duration_minutes" not in entry:
                continue
            total += 1
            if not entry.get("nondominated", True):
                dominated += 1
            if entry.get("pref_satisfied") is False:
                pref_viol += 1
        out[name] = {"total": total, "dominated_recommendations": dominated,
                     "preference_violations": pref_viol}
    return out


# ── 创新一：F1 差异置信区间 ─────────────────────────────────────────────

def governance_bootstrap(data: dict) -> dict:
    test_rows = [r for r in data["results"] if r["window"]["split"] == "test"]
    truths = [r["window"]["label"] for r in test_rows]
    preds = {m: [r[m]["output"]["label"] for r in test_rows] for m in ("A", "B", "C")}
    point = {m: round(macro_f1_from(truths, preds[m]), 4) for m in ("A", "B", "C")}

    rng = random.Random(42)
    n = len(test_rows)
    f1_samples = {m: [] for m in ("A", "B", "C")}
    diff_samples = []
    for _ in range(BOOT_REPS):
        idx = [rng.randrange(n) for _ in range(n)]
        t = [truths[i] for i in idx]
        p = {m: [preds[m][i] for i in idx] for m in ("A", "B", "C")}
        f1s = {m: macro_f1_from(t, p[m]) for m in ("A", "B", "C")}
        for m in ("A", "B", "C"):
            f1_samples[m].append(f1s[m])
        diff_samples.append(f1s["C"] - f1s["B"])

    def ci(values):
        s = sorted(values)
        return [round(s[int(0.025 * BOOT_REPS)], 4), round(s[int(0.975 * BOOT_REPS)], 4)]

    return {"point_f1": point,
            "f1_ci": {m: ci(f1_samples[m]) for m in ("A", "B", "C")},
            "diff_C_minus_B": {"mean": round(statistics.mean(diff_samples), 4),
                               "ci95": ci(diff_samples)}}


# ── 创新二：分类别 IoU CI + 物理合理性 ──────────────────────────────────

def propagation_category_ci(data: dict) -> dict:
    test_rows = [r for r in data["per_scenario"] if r["split"] == "test"]
    out = {}
    for category in ("stable", "veering", "cross_zero", "calm", "gust"):
        subset = [r for r in test_rows if r["category"] == category]
        ious = {m: [r["methods"][m]["iou"] for r in subset] for m in ("A", "B", "C")}
        out[category] = {
            "n": len(subset),
            "mean_iou": {m: round(statistics.mean(ious[m]), 4) for m in ("A", "B", "C")},
            "diff_C_minus_B_ci": paired_diff_ci(ious["C"], ious["B"]),
        }
    out["physical_plausibility"] = {
        "decay_half_life_hours": 8.0,
        "note": "NO2 大气寿命为数小时量级，8h 衰减半衰期处于合理区间（v2 的 2h 会把烟羽过度衰减，"
                "导致暴露场面积被系统性压缩）；横向扩散系数 0.09 与初始 sigma 0.15 km 沿用原标定；"
                "静风积聚半径经网格重标定回到 0.35 km，说明静风场景落后是结构差异而非参数失谐。",
    }
    return out


def main() -> int:
    travel = json.loads((RESULTS / "travel_v4.json").read_text(encoding="utf-8"))
    blind_date = travel["design"]["dates"]["blind_independent"]
    blind_rows = [r for r in travel["rows"] if r.get("date") == blind_date]
    governance = json.loads((RESULTS / "governance_baselines.json").read_text(encoding="utf-8"))
    propagation = json.loads((RESULTS / "propagation_baselines.json").read_text(encoding="utf-8"))

    print("=== 创新三：约束条件下的公平比较（盲测日） ===", flush=True)
    constrained = travel_constrained(blind_rows)
    for cap_label, _ in EU_CAPS:
        for name in METHODS:
            b = constrained["eu_cap_comparison"][name][cap_label]
            print(f"  [{cap_label}] {name[:1]}: 可行 {b['feasible']}/{b['feasible'] + b['infeasible']} "
                  f"时间差距中位 {b.get('median_time_gap_vs_opt', '-')}", flush=True)
    fixed = travel_fixed_stats(blind_rows)
    print("\n=== 修正口径（全样本） ===", flush=True)
    for name, s in fixed.items():
        print(f"  {name[:1]} vs A: 均值EU差 {s['mean_eu_vs_A_pct']}%（CI "
              f"{s['eu_diff_pairwise_ci']['ci95']}）| 均值时间差 {s['mean_time_vs_A_pct']}% | "
              f"全样本EU比率中位 {s['median_eu_ratio_vs_A']}", flush=True)
    feasibility = feasibility_counts(blind_rows)
    print(f"\n  支配推荐/偏好违反: {feasibility}", flush=True)

    print("\n=== 创新一：F1 配对 bootstrap ===", flush=True)
    gov_ci = governance_bootstrap(governance)
    print(f"  point: {gov_ci['point_f1']}", flush=True)
    print(f"  C−B 差值: {gov_ci['diff_C_minus_B']}", flush=True)

    print("\n=== 创新二：分类别 IoU CI ===", flush=True)
    prop_ci = propagation_category_ci(propagation)
    for category, s in prop_ci.items():
        if category == "physical_plausibility":
            continue
        print(f"  {category}: C−B 差值 CI {s['diff_C_minus_B_ci']['ci95']} "
              f"(C {s['mean_iou']['C']} vs B {s['mean_iou']['B']})", flush=True)

    payload = {
        "travel_constrained": constrained,
        "travel_fixed_stats": fixed,
        "travel_feasibility_counts": feasibility,
        "governance_bootstrap": gov_ci,
        "propagation_category_ci": prop_ci,
        "note": "V5 为评价层分析：算法自 V4 冻结后未再修改，V4 盲测样本继续用于评价合法；"
                "全部置信区间为 10000 次配对 bootstrap。",
    }
    path = save_result("v5_analysis", payload)
    print(f"\nsaved -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
