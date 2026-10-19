"""V9.3 Experiment: innovation-2 mechanism ablation matrix.

Isolates the contribution of each mechanism under ONE unified protocol
(fresh v93-seed scenarios, per-arm threshold budget-matched on validation,
single evaluation on test):

    arm1  fixed_wind_orig    : single vector-mean wind + V6 original params
                               (plume_scale 0.55, decay 2h, crosswind 0.09)
    arm2  dynamic_orig       : hourly dynamic wind + V6 original params
    arm3  dynamic_new       : hourly dynamic wind + V8 recalibrated params
                               (plume 1.3, decay 8h), single-scale calm
    arm4  full_twoscale      : hourly dynamic wind + V8 params + two-scale
                               calm background (= the shipped model)

Attribution: arm2 − arm1 = hourly dynamic wind; arm3 − arm2 = decay/plume
recalibration; arm4 − arm3 = two-scale calm background.

Usage: /usr/bin/python3 tests/benchmarks/v9/propagation_mechanism.py
"""
from __future__ import annotations

import copy
import json
import statistics
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(HERE.parent))

from common import save_result  # noqa: E402
import bench_propagation_baselines as prop  # noqa: E402
from propagation_ablation import (  # noqa: E402
    combined_for,
    make_scenarios_v92,
    match_threshold,
)

V6_ORIGINAL = {"plume_scale": 0.55, "decay_half_life_hours": 2.0,
               "crosswind_coefficient": 0.09}


def arm_rules(base_rules: dict, arm: str) -> dict:
    rules = copy.deepcopy(base_rules)
    if arm in ("fixed_wind_orig", "dynamic_orig"):
        rules["plume"]["plume_scale"] = V6_ORIGINAL["plume_scale"]
        rules["plume"]["decay_half_life_hours"] = V6_ORIGINAL["decay_half_life_hours"]
        rules["plume"]["crosswind_spread"]["coefficient"] = \
            V6_ORIGINAL["crosswind_coefficient"]
        rules["calm"]["diffusion_background"] = {"k_m2s": 0.0, "hours": 3.0}
    if arm in ("dynamic_new", "full_twoscale"):
        # 保持 V8 冻结的烟羽参数（base_rules 即 V8 冻结值），无需修改；
        # arm3 用单尺度 calm（k=0），arm4 保留双尺度。
        if arm == "dynamic_new":
            rules["calm"]["diffusion_background"] = {"k_m2s": 0.0, "hours": 3.0}
    return rules


def combined_arm(arm: str, rules: dict, hours) -> "np.ndarray":
    if arm == "fixed_wind_orig":
        # 固定风：窗口矢量平均风作为单一小时输入
        mean_dir, mean_speed = prop.vector_mean_wind(hours)
        return combined_for(rules, [(mean_dir, mean_speed)])
    return combined_for(rules, hours)


def main() -> int:
    saved = json.loads((HERE.parent / "results" / "propagation_v7.json")
                       .read_text(encoding="utf-8"))
    b_params = {"B_l0_km": saved["design"]["calibration"]["B_l0_km"],
                "B_k_km_per_ms": saved["design"]["calibration"]["B_k_km_per_ms"]}
    base_rules = __import__("tools.pollution_field", fromlist=["x"]).load_pollution_rules()
    prop.POI_X, prop.POI_Y = prop.load_pois()

    scenarios = make_scenarios_v92("zhikan-prop-v93", per_category=8)
    print(f"v93 场景 {len(scenarios)} 个（全新种子）", flush=True)

    results = []
    for arm in ("fixed_wind_orig", "dynamic_orig", "dynamic_new", "full_twoscale"):
        rules = arm_rules(base_rules, arm)
        validation = [s for s in scenarios if s["split"] == "validation"]
        b_area = statistics.mean(
            float(prop.region_fixed_sector(*prop.vector_mean_wind(s["hours"]),
                                           b_params["B_l0_km"],
                                           b_params["B_k_km_per_ms"]).sum())
            for s in validation)
        combined_val = {s["scenario_id"]: combined_arm(arm, rules, s["hours"])
                        for s in validation}
        threshold = match_threshold(combined_val, b_area)

        per = []
        for s in scenarios:
            ref_label = prop.reference_field(s["hours"]) >= prop.REF_NORM_THRESHOLD
            region = combined_arm(arm, rules, s["hours"]) >= threshold
            per.append({"scenario_id": s["scenario_id"], "category": s["category"],
                        "split": s["split"],
                        "iou": round(prop.iou(region, ref_label), 4)})

        def summarise(split):
            subset = [r for r in per if r["split"] == split]
            return {"n": len(subset),
                    "mean_iou": round(statistics.mean(r["iou"] for r in subset), 4),
                    "by_category": {c: round(statistics.mean(
                        [r["iou"] for r in subset if r["category"] == c]), 4)
                        for c in prop.SCENARIO_CATEGORIES
                        if any(r["category"] == c for r in subset)}}

        entry = {"arm": arm, "threshold": threshold,
                 "validation": summarise("validation"), "test": summarise("test"),
                 "per_scenario": per}
        results.append(entry)
        t = entry["test"]
        print(f"  {arm:<17} thr={threshold:.2f} IoU={t['mean_iou']} "
              f"calm={t['by_category'].get('calm')} "
              f"stable={t['by_category'].get('stable')}", flush=True)

    # 归因（测试集）+ 按场景配对 bootstrap CI（场景为独立单位）
    import random

    def arm_iou_map(arm: str) -> dict:
        entry = next(r for r in results if r["arm"] == arm)
        return {row["scenario_id"]: row["iou"] for row in entry["per_scenario"]
                if row["split"] == "test"}

    def paired_ci(arm_x: str, arm_y: str, reps: int = 10000) -> dict:
        x_map, y_map = arm_iou_map(arm_x), arm_iou_map(arm_y)
        keys = sorted(set(x_map) & set(y_map))
        diffs = [x_map[k] - y_map[k] for k in keys]
        rng = random.Random(42)
        samples = sorted(statistics.mean(
            diffs[rng.randrange(len(diffs))] for _ in range(len(diffs)))
            for _ in range(reps))
        return {"mean_diff": round(statistics.mean(diffs), 4),
                "ci95": [round(samples[int(0.025 * reps)], 4),
                         round(samples[int(0.975 * reps)], 4)],
                "n_scenarios": len(diffs)}

    by_arm = {r["arm"]: r["test"]["mean_iou"] for r in results}
    attribution = {
        "dynamic_wind_arm2_minus_arm1": paired_ci("dynamic_orig", "fixed_wind_orig"),
        "recalibration_arm3_minus_arm2": paired_ci("dynamic_new", "dynamic_orig"),
        "twoscale_arm4_minus_arm3": paired_ci("full_twoscale", "dynamic_new"),
        "point_estimates": {
            "arm1_fixed_wind_orig": by_arm["fixed_wind_orig"],
            "arm2_dynamic_orig": by_arm["dynamic_orig"],
            "arm3_dynamic_new": by_arm["dynamic_new"],
            "arm4_full_twoscale": by_arm["full_twoscale"]},
    }
    # 分类别增量（每类 IoU 差）
    by_category = {}
    for category in prop.SCENARIO_CATEGORIES:
        def cat_iou(arm: str) -> float:
            entry = next(r for r in results if r["arm"] == arm)
            vals = [row["iou"] for row in entry["per_scenario"]
                    if row["split"] == "test" and row["category"] == category]
            return round(statistics.mean(vals), 4) if vals else None
        by_category[category] = {
            "arm1": cat_iou("fixed_wind_orig"), "arm2": cat_iou("dynamic_orig"),
            "arm3": cat_iou("dynamic_new"), "arm4": cat_iou("full_twoscale")}
    attribution["by_category"] = by_category

    payload = {
        "design": {
            "note": "V9.3 机制消融矩阵：统一场景/阈值规则下分离动态风场、衰减标定、"
                    "双尺度结构各自贡献；V6 原参数 = plume 0.55 / decay 2h / crosswind 0.09",
            "v6_original_params": V6_ORIGINAL,
            "attribution_test_iou": attribution,
        },
        "arms": results,
    }
    path = save_result("v9_propagation_mechanism", payload)
    print(f"\nsaved -> {path}")
    print("归因（测试集 IoU 差）:", json.dumps(attribution, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
