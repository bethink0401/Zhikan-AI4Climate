"""V9.2 Experiment: innovation-2 two-scale ablation + parameter sweeps.

Pure automation, fresh seeds (zhikan-prop-v92-*), frozen system parameters
except the ablation knobs under test. Two parts:

  A. Two-scale calm ablation (4 variants, same frozen base config otherwise):
       full          — local R0 term + diffusion background (the shipped model)
       local_only    — background_term_scale = 0
       background_only — local_term_scale = 0
       legacy_single — diffusion_background k=0 (V6-era single-scale behaviour)
     Each variant gets its C threshold budget-matched to B on the validation
     split (same rule as V7/V8), then evaluated once on the test split.

  B. Parameter sweeps on the FULL variant (threshold re-matched per condition):
       background K in {20, 100} m²/s (default 50)
       calm reference hours in {1, 6} h (default 3)
       wind regimes: direction-random-walk sigma {2°, 8°} x speed {1.5, 3.0} m/s

Metrics: IoU, budget-matched Precision/Recall, runtime. Reference = the same
independent Eulerian advection-diffusion model as V7/V8 (labels fixed).

Usage: /usr/bin/python3 tests/benchmarks/v9/propagation_ablation.py
"""
from __future__ import annotations

import copy
import json
import math
import statistics
import sys
import time
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(HERE.parent))

from common import save_result  # noqa: E402
import bench_propagation_baselines as prop  # noqa: E402


def make_scenarios_v92(seed_prefix: str, per_category: int = 8,
                       categories: list[str] | None = None,
                       dir_sigma: float | None = None,
                       speed_level: float | None = None) -> list[dict]:
    """V9.2 全新种子场景；可选覆盖风向随机游走幅度与风速水平。"""
    import random
    import zlib

    categories = categories or prop.SCENARIO_CATEGORIES
    scenarios = []
    for category in categories:
        for index in range(per_category):
            seed = zlib.crc32(f"{seed_prefix}-{category}-{index}".encode())
            rng = random.Random(seed)
            hours = []
            if category == "stable":
                base_dir = rng.uniform(0.0, 360.0)
                base_speed = speed_level if speed_level else rng.uniform(2.0, 3.0)
                sigma = dir_sigma if dir_sigma is not None else 4.0
                direction = base_dir
                for _ in range(prop.REF_HOURS):
                    direction = (direction + rng.gauss(0.0, sigma)) % 360.0
                    hours.append((direction,
                                  max(0.5, base_speed + rng.gauss(0.0, 0.25))))
            elif category == "veering":
                base_dir = rng.uniform(0.0, 360.0)
                speed = speed_level if speed_level else rng.uniform(2.0, 4.0)
                step = rng.uniform(25.0, 40.0) * rng.choice((-1.0, 1.0))
                direction = base_dir
                for _ in range(prop.REF_HOURS):
                    direction = (direction + step) % 360.0
                    hours.append((direction, speed))
            elif category == "cross_zero":
                speed = speed_level if speed_level else rng.uniform(1.5, 3.0)
                flip_dir = rng.uniform(0.0, 360.0)
                for i in range(prop.REF_HOURS):
                    direction = (flip_dir + (180.0 if i >= 2 else 0.0)) % 360.0
                    hours.append((direction, speed))
            elif category == "calm":
                for i in range(prop.REF_HOURS):
                    hours.append((rng.uniform(0.0, 360.0),
                                  rng.uniform(0.1, 0.6)))
            else:  # gust
                base_dir = rng.uniform(0.0, 360.0)
                base_speed = speed_level if speed_level else rng.uniform(2.0, 3.0)
                for _ in range(prop.REF_HOURS):
                    hours.append((base_dir,
                                  max(0.5, base_speed + rng.gauss(0.0, 1.2))))
            split = "validation" if index < per_category // 2 else "test"
            scenarios.append({"scenario_id": f"{category}_{index}",
                              "category": category, "split": split,
                              "hours": hours})
    return scenarios


def ablation_rules(base_rules: dict, variant: str) -> dict:
    rules = copy.deepcopy(base_rules)
    if variant == "local_only":
        rules["calm"]["background_term_scale"] = 0.0
    elif variant == "background_only":
        rules["calm"]["local_term_scale"] = 0.0
    elif variant == "legacy_single":
        rules["calm"]["diffusion_background"] = {"k_m2s": 0.0, "hours": 3.0}
    return rules


def combined_for(rules: dict, hours) -> np.ndarray:
    from tools import pollution_field as pf
    pf._RULES_CACHE.clear()
    try:
        return prop.dynamic_combined_field(hours, rules)
    finally:
        pf._RULES_CACHE.clear()


def match_threshold(combined_val: dict, b_area: float) -> float:
    best_thr, best_gap = None, None
    for thr in prop.C_THRESHOLD_CANDIDATES:
        areas = [float((v >= thr).sum()) for v in combined_val.values()]
        gap = abs(statistics.mean(areas) - b_area)
        if best_thr is None or gap < best_gap:
            best_thr, best_gap = thr, gap
    return best_thr


def evaluate(variant_rules: dict, scenarios: list[dict], b_params: dict,
             label: str) -> dict:
    from tools import pollution_field as pf
    validation = [s for s in scenarios if s["split"] == "validation"]
    test = [s for s in scenarios if s["split"] == "test"]

    b_area = statistics.mean(
        float(prop.region_fixed_sector(*prop.vector_mean_wind(s["hours"]),
                                       b_params["B_l0_km"],
                                       b_params["B_k_km_per_ms"]).sum())
        for s in validation)
    combined_val = {s["scenario_id"]: combined_for(variant_rules, s["hours"])
                    for s in validation}
    threshold = match_threshold(combined_val, b_area)

    per = []
    for s in scenarios:
        t0 = time.perf_counter()
        ref = prop.reference_field(s["hours"])
        ref_label = ref >= prop.REF_NORM_THRESHOLD
        region = combined_for(variant_rules, s["hours"]) >= threshold
        ms = (time.perf_counter() - t0) * 1000.0
        region_b = prop.region_fixed_sector(
            *prop.vector_mean_wind(s["hours"]), b_params["B_l0_km"],
            b_params["B_k_km_per_ms"])
        precision, recall = prop.poi_pr(prop.poi_flags(region),
                                        prop.poi_flags(ref_label))
        per.append({
            "scenario_id": s["scenario_id"], "category": s["category"],
            "split": s["split"],
            "iou": round(prop.iou(region, ref_label), 4),
            "iou_B": round(prop.iou(region_b, ref_label), 4),
            "area_km2": round(float(region.sum()) * 0.01, 3),
            "precision": None if precision is None else round(precision, 4),
            "recall": None if recall is None else round(recall, 4),
            "ms": round(ms, 1),
        })

    def summarise(split):
        subset = [r for r in per if r["split"] == split]
        return {
            "n": len(subset),
            "mean_iou": round(statistics.mean(r["iou"] for r in subset), 4),
            "mean_iou_B": round(statistics.mean(r["iou_B"] for r in subset), 4),
            "mean_area_km2": round(statistics.mean(r["area_km2"] for r in subset), 3),
            "budget_precision": round(statistics.mean(
                r["precision"] for r in subset if r["precision"] is not None), 4),
            "budget_recall": round(statistics.mean(
                r["recall"] for r in subset if r["recall"] is not None), 4),
            "median_ms": round(statistics.median(r["ms"] for r in subset), 1),
            "by_category": {c: round(statistics.mean(
                [r["iou"] for r in subset if r["category"] == c]), 4)
                for c in prop.SCENARIO_CATEGORIES
                if any(r["category"] == c for r in subset)},
        }

    return {"variant": label, "threshold": threshold,
            "validation": summarise("validation"), "test": summarise("test"),
            "per_scenario": per}


def main() -> int:
    from tools.pollution_field import load_pollution_rules
    base_rules = load_pollution_rules()
    saved = json.loads((HERE.parent / "results" / "propagation_v7.json")
                       .read_text(encoding="utf-8"))
    b_params = {"B_l0_km": saved["design"]["calibration"]["B_l0_km"],
                "B_k_km_per_ms": saved["design"]["calibration"]["B_k_km_per_ms"]}
    prop.POI_X, prop.POI_Y = prop.load_pois()

    # ── A. 双尺度消融（全新 v92 种子，5 类 × 8 = 40 场景） ──
    scenarios = make_scenarios_v92("zhikan-prop-v92", per_category=8)
    print(f"消融场景 {len(scenarios)} 个（v92 全新种子）", flush=True)
    ablation = []
    for variant in ("full", "local_only", "background_only", "legacy_single"):
        rules = ablation_rules(base_rules, variant)
        result = evaluate(rules, scenarios, b_params, variant)
        ablation.append(result)
        t = result["test"]
        print(f"  {variant:<16} thr={result['threshold']:.2f} "
              f"IoU={t['mean_iou']} (B={t['mean_iou_B']}) "
              f"calm={t['by_category']['calm']} "
              f"P={t['budget_precision']} R={t['budget_recall']} "
              f"{t['median_ms']}ms", flush=True)

    # ── B. 参数扫描（full 变体，每条件独立场景、阈值重匹配） ──
    sweeps = {}
    for sweep_name, rules_override, sweep_kwargs in (
            ("K20", {"calm": {"diffusion_background": {"k_m2s": 20.0, "hours": 3.0}}}, {}),
            ("K100", {"calm": {"diffusion_background": {"k_m2s": 100.0, "hours": 3.0}}}, {}),
            ("hours1", {"calm": {"diffusion_background": {"k_m2s": 50.0, "hours": 1.0}}}, {}),
            ("hours6", {"calm": {"diffusion_background": {"k_m2s": 50.0, "hours": 6.0}}}, {}),
            ("dir_sigma2", {}, {"dir_sigma": 2.0}),
            ("dir_sigma8", {}, {"dir_sigma": 8.0}),
            ("slow_wind", {}, {"speed_level": 1.5}),
            ("fast_wind", {}, {"speed_level": 3.0}),
    ):
        rules = copy.deepcopy(base_rules)
        for section, patch in rules_override.items():
            rules[section].update(patch)
        sweep_scenarios = make_scenarios_v92(
            f"zhikan-prop-v92s-{sweep_name}", per_category=4,
            categories=["stable", "calm", "gust"], **sweep_kwargs)
        result = evaluate(rules, sweep_scenarios, b_params, sweep_name)
        sweeps[sweep_name] = result
        t = result["test"]
        print(f"  sweep {sweep_name:<12} thr={result['threshold']:.2f} "
              f"IoU={t['mean_iou']} (B={t['mean_iou_B']}) "
              f"calm={t['by_category']['calm']}", flush=True)

    payload = {
        "design": {
            "note": "V9.2：双尺度消融（4 变体，消融开关见 tools/pollution_field.py，"
                    "默认行为不变）+ 参数扫描；参考场与标签冻结（欧拉差分）；"
                    "每变体/条件的 C 阈值在验证集按同 B 面积预算重匹配后测试一次",
            "frozen_base": "V8 冻结配置",
            "scenarios": "v92 全新种子；扫描条件各自独立种子",
        },
        "ablation": ablation,
        "sweeps": sweeps,
    }
    path = save_result("v9_propagation_ablation", payload)
    print(f"\nsaved -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
