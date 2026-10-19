"""V6: robustness of the propagation comparison to the reference model.

The v5 conclusions (stable-wind advantage, calm deficit) were measured against
one reference advection-diffusion model with K = 50 m²/s. To show the ranking
is not an artefact of that single choice, this script recomputes the reference
labels with K ∈ {30, 50, 100} m²/s (the C algorithm and B baseline are frozen)
and reports per-category C−B IoU with paired bootstrap CIs.

Usage: /usr/bin/python3 tests/benchmarks/bench_propagation_robustness.py
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result  # noqa: E402

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

from bench_propagation_baselines import (  # noqa: E402
    CENTER,
    GRID_X,
    GRID_Y,
    N_CELL,
    REF_DT_S,
    REF_NORM_THRESHOLD,
    REF_SOURCE_SIGMA_M,
    dynamic_combined_field,
    iou,
    km_to_lonlat,
    make_scenarios,
    region_fixed_sector,
    vector_mean_wind,
)

GRID_X = GRID_X
GRID_Y = GRID_Y
REF_DECAY_PER_S = math.log(2.0) / (3.0 * 3600.0)
CATEGORIES = ["stable", "veering", "cross_zero", "calm", "gust"]


def reference_field_with_k(hours: list[tuple[float, float]], k_m2s: float) -> np.ndarray:
    """与主基准相同的参考模型，扩散系数 K 参数化。"""
    xs_step = None
    # 与主基准一致的网格（0.1 km 格距、6×6 km 域）
    from bench_propagation_baselines import GRID_X as GX, GRID_Y as GY, XS
    dx = (XS[1] - XS[0]) * 1000.0
    field = np.zeros(GX.shape, dtype=float)
    source_term = np.exp(-(GX ** 2 + GY ** 2) / (2.0 * (REF_SOURCE_SIGMA_M / 1000.0) ** 2))
    steps_per_hour = int(3600.0 / REF_DT_S)
    decay = math.exp(-REF_DECAY_PER_S * REF_DT_S)

    for wind_from, speed in hours:
        rad = math.radians(wind_from)
        u, v = -speed * math.sin(rad), -speed * math.cos(rad)
        for _ in range(steps_per_hour):
            dx_c = (field[1:-1, 2:] - field[1:-1, :-2]) / 2.0
            dy_c = (field[2:, 1:-1] - field[:-2, 1:-1]) / 2.0
            lap = (field[1:-1, 2:] + field[1:-1, :-2] +
                   field[2:, 1:-1] + field[:-2, 1:-1] - 4.0 * field[1:-1, 1:-1])
            inner = (
                field[1:-1, 1:-1]
                - REF_DT_S * (u * dx_c / dx + v * dy_c / dx)
                + REF_DT_S * k_m2s * lap / (dx * dx)
                + REF_DT_S * source_term[1:-1, 1:-1]
            ) * decay
            field[1:-1, 1:-1] = np.maximum(inner, 0.0)
            field[0, :] = field[1, :]
            field[-1, :] = field[-2, :]
            field[:, 0] = field[:, 1]
            field[:, -1] = field[:, -2]
    peak = field.max()
    return field / peak if peak > 0 else field


def main() -> int:
    base = json.loads(
        (Path(__file__).resolve().parent / "results" / "propagation_baselines.json")
        .read_text(encoding="utf-8"))
    params = base["design"]["calibration"]
    c_threshold = params["C_threshold"]

    scenarios = make_scenarios()
    test = [s for s in scenarios if s["split"] == "test"]

    # C 的组合场与阈值无关，逐场景只算一次
    combined = {s["scenario_id"]: dynamic_combined_field(s["hours"]) for s in test}
    c_regions = {sid: combined[sid] >= c_threshold for sid in combined}
    mean_wind = {s["scenario_id"]: vector_mean_wind(s["hours"]) for s in test}

    out = {}
    for k_m2s in (30.0, 50.0, 100.0):
        per_category = {}
        for category in CATEGORIES:
            subset = [s for s in test if s["category"] == category]
            diffs, c_ious, b_ious = [], [], []
            for s in subset:
                ref = reference_field_with_k(s["hours"], k_m2s) >= REF_NORM_THRESHOLD
                c_iou = iou(c_regions[s["scenario_id"]], ref)
                mean_dir, mean_speed = mean_wind[s["scenario_id"]]
                b_region = region_fixed_sector(mean_dir, mean_speed,
                                               params["B_l0_km"], params["B_k_km_per_ms"])
                b_iou = iou(b_region, ref)
                c_ious.append(c_iou)
                b_ious.append(b_iou)
                diffs.append(c_iou - b_iou)
            rng = __import__("random").Random(42)
            samples = sorted(diffs[rng.randrange(len(diffs))] for _ in range(10000))
            per_category[category] = {
                "n": len(subset),
                "mean_iou_C": round(statistics.mean(c_ious), 4),
                "mean_iou_B": round(statistics.mean(b_ious), 4),
                "diff_ci95": [round(samples[int(0.025 * 10000)], 4),
                              round(samples[int(0.975 * 10000)], 4)],
            }
        out[f"K={k_m2s:.0f}"] = per_category
        print(f"K={k_m2s:.0f} m²/s:", flush=True)
        for category, s in per_category.items():
            print(f"  {category:<11} C={s['mean_iou_C']:.3f} B={s['mean_iou_B']:.3f} "
                  f"CI {s['diff_ci95']}", flush=True)

    payload = {
        "design": {"note": "参考模型扩散系数敏感性：C 算法与 B 基线冻结，仅参考场 K 变化。",
                   "c_threshold": c_threshold},
        "robustness": out,
    }
    path = save_result("propagation_robustness", payload)
    print(f"saved -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
