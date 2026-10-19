"""Benchmark V8: external validity check with a Lagrangian particle reference.

The V7 two-scale calm model was validated against an Eulerian finite-difference
advection-diffusion reference. A favourable comparison against ONE reference
structure could be an artefact of shared assumptions, so this benchmark
re-generates the risk labels with a structurally DIFFERENT reference model:

    Lagrangian stochastic particle model — N particles released at the source
    each hour, advected by the hourly mean wind, perturbed by a random walk
    with sigma = sqrt(2*K*dt) (same K = 50 m²/s and decay constant as the
    Eulerian reference), deposited on the same 61x61 grid and summed over the
    3-hour window, normalised by the peak, thresholded at 0.25.

System parameters are the FROZEN V7 calibration (loaded from the saved
propagation_v7.json), thresholds re-matched to the particle reference's area
on the validation split only — no system parameter is touched.

Usage: /usr/bin/python3 tests/benchmarks/bench_propagation_lagrangian.py
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import time
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import save_result  # noqa: E402

# 复用 V7 场景与已冻结参数（导入即执行模块级常量定义，main 不会运行）
import bench_propagation_v7 as v7  # noqa: E402

PARTICLES_PER_HOUR = 6000
PARTICLE_DT_S = 15.0
LAGR_K_M2S = 50.0            # 与欧拉参考同物理常数、不同求解结构
LAGR_SOURCE_SIGMA_M = 150.0
SEED = 20260813


def lagrangian_reference_field(hours: list[tuple[float, float]],
                               rng_seed: int = SEED) -> np.ndarray:
    """拉格朗日粒子参考场：随机游走 + 平流，3 小时沉积累计。

    结构差异（与欧拉迎风差分对照）：无网格数值扩散、以随机游走显式
    表达湍流扩散、逐粒子沉积计数。
    """
    import random

    rng = np.random.default_rng(rng_seed)
    cell_km = v7.XS[1] - v7.XS[0]
    n = v7.N_CELL
    deposits = np.zeros((n, n), dtype=float)
    sigma_step_km = math.sqrt(2.0 * LAGR_K_M2S * PARTICLE_DT_S) / 1000.0
    sigma_src_km = LAGR_SOURCE_SIGMA_M / 1000.0
    steps_per_hour = int(3600.0 / PARTICLE_DT_S)
    decay_step = math.exp(-(math.log(2.0) / (3.0 * 3600.0)) * PARTICLE_DT_S)

    for wind_from, speed in hours:
        # 平流方向：风的来向 +180° 为输送方向（x 向东、y 向北）
        bearing = math.radians((wind_from + 180.0) % 360.0)
        vx = speed / 1000.0 * math.sin(bearing)   # km/s
        vy = speed / 1000.0 * math.cos(bearing)
        # 初始位置：源点高斯（R0=150 m）
        pos = rng.normal(0.0, sigma_src_km, size=(PARTICLES_PER_HOUR, 2))
        weight = 1.0
        for _ in range(steps_per_hour):
            pos[:, 0] += vx * PARTICLE_DT_S
            pos[:, 1] += vy * PARTICLE_DT_S
            pos += rng.normal(0.0, sigma_step_km, size=(PARTICLES_PER_HOUR, 2))
            weight *= decay_step
            # 沉积到网格（域内）
            ix = ((pos[:, 0] + v7.HALF_KM) / cell_km).astype(int)
            iy = ((pos[:, 1] + v7.HALF_KM) / cell_km).astype(int)
            valid = (ix >= 0) & (ix < n) & (iy >= 0) & (iy < n)
            np.add.at(deposits, (iy[valid], ix[valid]), weight)
        # 粒子每小时重新释放（连续排放的逐小时切片）
    peak = float(deposits.max())
    if peak <= 0:
        return deposits
    return deposits / peak


def main() -> int:
    params = None
    saved = json.loads((Path(__file__).resolve().parent / "results" /
                        "propagation_v7.json").read_text(encoding="utf-8"))
    params = saved["design"]["calibration"]
    print(f"载入 V7 冻结参数: {params}", flush=True)

    v7.POI_X, v7.POI_Y = v7.load_pois()
    scenarios = v7.make_scenarios()
    validation = [s for s in scenarios if s["split"] == "validation"]
    test = [s for s in scenarios if s["split"] == "test"]
    print(f"V7 全新场景 {len(scenarios)} 个（验证 {len(validation)} / 测试 {len(test)}）",
          flush=True)

    # C 阈值对粒子参考按验证集"同 B 面积预算"重新匹配（只动阈值、不动系统参数）
    b_area = statistics.mean(
        float(v7.region_fixed_sector(*v7.vector_mean_wind(s["hours"]),
                                     params["B_l0_km"], params["B_k_km_per_ms"]).sum())
        for s in validation)
    combined_val = {s["scenario_id"]: v7.dynamic_combined_field(s["hours"])
                    for s in validation}
    best_thr, best_gap = None, None
    for thr in v7.C_THRESHOLD_CANDIDATES:
        areas = [float((combined_val[s["scenario_id"]] >= thr).sum()) for s in validation]
        gap = abs(statistics.mean(areas) - b_area)
        if best_thr is None or gap < best_gap:
            best_thr, best_gap = thr, gap
    print(f"C 阈值对粒子参考重匹配: {best_thr}（面积差 {best_gap:.0f} 格）", flush=True)

    per_scenario = []
    t0 = time.perf_counter()
    for s in scenarios:
        ref = lagrangian_reference_field(s["hours"])
        ref_label = ref >= v7.REF_NORM_THRESHOLD
        mean_dir, mean_speed = v7.vector_mean_wind(s["hours"])

        region_a = v7.region_static_circle(params["A_radius_km"])
        region_b = v7.region_fixed_sector(mean_dir, mean_speed,
                                          params["B_l0_km"], params["B_k_km_per_ms"])
        region_c = v7.dynamic_combined_field(s["hours"]) >= best_thr

        entry = {"scenario_id": s["scenario_id"], "category": s["category"],
                 "split": s["split"], "methods": {}}
        for name, region in (("A", region_a), ("B", region_b), ("C", region_c)):
            precision, recall = v7.poi_pr(v7.poi_flags(region), v7.poi_flags(ref_label))
            entry["methods"][name] = {
                "iou": round(v7.iou(region, ref_label), 4),
                "area_km2": round(float(region.sum()) * 0.01, 3),
                "ref_area_km2": round(float(ref_label.sum()) * 0.01, 3),
                "poi_precision": None if precision is None else round(precision, 4),
                "poi_recall": None if recall is None else round(recall, 4),
            }
        per_scenario.append(entry)
        print(f"  {s['scenario_id']:<20} IoU A={entry['methods']['A']['iou']:.3f} "
              f"B={entry['methods']['B']['iou']:.3f} C={entry['methods']['C']['iou']:.3f}",
              flush=True)
    print(f"粒子参考总耗时 {time.perf_counter() - t0:.0f}s", flush=True)

    def summarise(split: str) -> dict:
        subset = [r for r in per_scenario if r["split"] == split]
        out = {"n": len(subset), "methods": {}}
        for method in ("A", "B", "C"):
            rows = [r["methods"][method] for r in subset]
            ious = [r["iou"] for r in rows]
            out["methods"][method] = {
                "mean_iou": round(statistics.mean(ious), 4),
                "mean_area_km2": round(statistics.mean(r["area_km2"] for r in rows), 3),
                "mean_poi_precision": round(statistics.mean(
                    r["poi_precision"] for r in rows if r["poi_precision"] is not None), 4),
                "mean_poi_recall": round(statistics.mean(
                    r["poi_recall"] for r in rows if r["poi_recall"] is not None), 4),
                "by_category": {c: round(statistics.mean(
                    [r["methods"][method]["iou"] for r in subset if r["category"] == c]), 4)
                    for c in v7.SCENARIO_CATEGORIES},
            }
        return out

    def paired_ci(x: list[float], y: list[float], reps: int = 10000) -> dict:
        import random
        rng = random.Random(42)
        n = len(x)
        diffs = [a - b for a, b in zip(x, y)]
        samples = sorted(statistics.mean(
            diffs[rng.randrange(n)] for _ in range(n)) for _ in range(reps))
        return {"mean_diff": round(statistics.mean(diffs), 4),
                "ci95": [round(samples[int(0.025 * reps)], 4),
                         round(samples[int(0.975 * reps)], 4)]}

    test_rows = [r for r in per_scenario if r["split"] == "test"]
    iou_by = {m: [r["methods"][m]["iou"] for r in test_rows] for m in ("A", "B", "C")}

    payload = {
        "design": {
            "note": "外部有效性验证：拉格朗日随机游走粒子参考（6000 粒/时、dt=15s、"
                    "K=50 m²/s、衰减常数同欧拉参考）替代欧拉差分参考生成风险标签；"
                    "系统参数 = V7 冻结值，C 阈值仅对粒子参考按验证集同面积预算重匹配。",
            "frozen_params": params, "c_threshold_rematched": best_thr,
        },
        "validation_summary": summarise("validation"),
        "test_summary": summarise("test"),
        "iou_diff_test": {"C_minus_B": paired_ci(iou_by["C"], iou_by["B"]),
                          "C_minus_A": paired_ci(iou_by["C"], iou_by["A"])},
        "per_scenario": per_scenario,
    }
    path = save_result("propagation_lagrangian", payload)
    print(f"\nsaved -> {path}")
    t = payload["test_summary"]["methods"]
    print("=== 粒子参考测试集 ===")
    for m in ("A", "B", "C"):
        print(f"  {m}: IoU={t[m]['mean_iou']} 面积={t[m]['mean_area_km2']}km² "
              f"分类别={t[m]['by_category']}")
    print(f"  C−B: {payload['iou_diff_test']['C_minus_B']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
