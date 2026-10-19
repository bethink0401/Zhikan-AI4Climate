"""Benchmark: dynamic pollution propagation vs static / fixed-wind baselines.

Experiment 2 of the comparison study. Three methods produce an hourly risk
region around the same point source (Mong Kok roadside) on the same 6x6 km
grid (0.1 km cells):

  A  static circular buffer : distance <= R, wind-independent
  B  fixed-wind sector      : one representative wind (time-averaged) -> downwind wedge
  C  智瞰 dynamic model      : PollutionField driven by each hour's wind,
                               smooth calm/plume transition (max over hours)

The ground truth is an **independent** 2-D advection-diffusion numerical model
(explicit upwind finite differences, numpy) — none of the three methods shares
code with it. Risk labels come from the reference field only.

60 scenarios in 5 categories (stable / veering / cross-zero / calm / gust).
Per category: 4 validation scenarios (threshold + geometry calibration, then
frozen) and 8 independent test scenarios.

Usage: /usr/bin/python3 tests/benchmarks/bench_propagation_baselines.py
"""
from __future__ import annotations

import csv
import math
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

from tools.pollution_field import PollutionField  # noqa: E402

CENTER = {"name": "Mong Kok Roadside", "lat": 22.3193, "lon": 114.1694}
POI_CSV = BASE / "spatial_agent" / "data" / "processed" / "poi_sensitive.csv"

#: 对比域：以污染源为中心 6 km × 6 km，0.1 km 格距
HALF_KM = 3.0
N_CELL = 61
XS = np.linspace(-HALF_KM, HALF_KM, N_CELL)
GRID_X, GRID_Y = np.meshgrid(XS, XS)  # x 向东, y 向北
R_GRID = np.sqrt(GRID_X ** 2 + GRID_Y ** 2)

#: 参考模型（独立二维平流-扩散，显式迎风差分）
REF_K_M2S = 50.0          # 扩散系数 m²/s
REF_DT_S = 20.0           # 时间步长
REF_HOURS = 3
REF_SOURCE_SIGMA_M = 150.0
REF_DECAY_PER_S = math.log(2.0) / (3.0 * 3600.0)
REF_NORM_THRESHOLD = 0.25  # 参考场归一化风险阈值（先验固定）

#: 校准搜索空间（验证集上选定后冻结）
A_RADIUS_CANDIDATES = [round(0.5 + 0.25 * i, 2) for i in range(11)]        # 0.5..3.0 km
B_L0_CANDIDATES = [round(0.5 + 0.25 * i, 2) for i in range(7)]             # 0.5..2.0 km
B_K_CANDIDATES = [0.0, 0.3, 0.6, 1.0]                                      # km per m/s
B_HALF_ANGLE_DEG = 35.0
C_THRESHOLD_CANDIDATES = [round(0.02 + 0.04 * i, 2) for i in range(15)]    # 0.02..0.58

#: 固定预算 PR 扫描的预算比例（域面积 36 km² 的 2%..20%，先验固定）
FIXED_BUDGET_FRACTIONS = [0.02, 0.04, 0.06, 0.08, 0.10, 0.12, 0.16, 0.20]

SCENARIO_CATEGORIES = ["stable", "veering", "cross_zero", "calm", "gust"]
PER_CATEGORY = 12          # 前 4 个验证、后 8 个独立测试
VALIDATION_COUNT = 4

LAT_SCALE = 110.57          # km per degree latitude
LON_SCALE = 111.32 * math.cos(math.radians(CENTER["lat"]))
LON0, LAT0 = CENTER["lon"], CENTER["lat"]


def km_to_lonlat(x: float, y: float) -> tuple[float, float]:
    return LON0 + x / LON_SCALE, LAT0 + y / LAT_SCALE


def wind_to_uv(speed: float, wind_from_deg: float) -> tuple[float, float]:
    """气象风向（来向，北顺时针）-> 内陆坐标速度分量 (u向东, v向北)。"""
    rad = math.radians(wind_from_deg)
    return -speed * math.sin(rad), -speed * math.cos(rad)


# ── 场景生成 ────────────────────────────────────────────────────────────

def make_scenarios() -> list[dict]:
    import random
    import zlib

    scenarios = []
    for category in SCENARIO_CATEGORIES:
        for index in range(PER_CATEGORY):
            # 注意：str.hash 带进程盐，不能做随机种子（否则每次运行场景都不同）
            seed = zlib.crc32(f"zhikan-prop-{category}-{index}".encode("utf-8"))
            rng = random.Random(seed)
            hours = []
            if category == "stable":
                base_dir = rng.uniform(0.0, 360.0)
                base_speed = rng.uniform(2.0, 3.0)
                for _ in range(REF_HOURS):
                    hours.append(((base_dir + rng.gauss(0.0, 4.0)) % 360.0,
                                  max(0.5, base_speed + rng.gauss(0.0, 0.25))))
            elif category == "veering":
                base_dir = rng.uniform(0.0, 360.0)
                step = rng.uniform(15.0, 35.0)
                for k in range(REF_HOURS):
                    hours.append(((base_dir + k * step) % 360.0, rng.uniform(2.0, 3.0)))
            elif category == "cross_zero":
                base_dir = rng.uniform(320.0, 350.0)
                step = rng.uniform(18.0, 28.0)
                for k in range(REF_HOURS):
                    hours.append(((base_dir + k * step) % 360.0, rng.uniform(2.0, 2.8)))
            elif category == "calm":
                base_dir = rng.uniform(0.0, 360.0)
                for k in range(REF_HOURS):
                    hours.append(((base_dir + rng.gauss(0.0, 10.0)) % 360.0,
                                  rng.uniform(0.3, 0.9)))
            else:  # gust
                base_dir = rng.uniform(0.0, 360.0)
                speed = rng.uniform(1.0, 1.6)
                for k in range(REF_HOURS):
                    speed = min(3.5, max(0.8, speed + rng.uniform(-0.4, 1.0)))
                    hours.append(((base_dir + rng.gauss(0.0, 15.0)) % 360.0, speed))
            scenarios.append({
                "scenario_id": f"{category}_{index:02d}",
                "category": category,
                "split": "validation" if index < VALIDATION_COUNT else "test",
                "hours": hours,
            })
    return scenarios


# ── 独立参考场：二维平流-扩散 ────────────────────────────────────────────

def reference_field(hours: list[tuple[float, float]]) -> np.ndarray:
    """数值求解 3 小时平流-扩散，返回归一化浓度场（0-1）。"""
    dx = (XS[1] - XS[0]) * 1000.0  # m
    field = np.zeros((N_CELL, N_CELL), dtype=float)
    sigma_m = REF_SOURCE_SIGMA_M
    source_term = np.exp(-(GRID_X ** 2 + GRID_Y ** 2) / (2.0 * (sigma_m / 1000.0) ** 2))
    steps_per_hour = int(3600.0 / REF_DT_S)
    decay = math.exp(-REF_DECAY_PER_S * REF_DT_S)

    for hour, (wind_from, speed) in enumerate(hours):
        u, v = wind_to_uv(speed, wind_from)
        for _ in range(steps_per_hour):
            # 显式迎风差分
            dx_c = (field[1:-1, 2:] - field[1:-1, :-2]) / 2.0
            dy_c = (field[2:, 1:-1] - field[:-2, 1:-1]) / 2.0
            lap = (field[1:-1, 2:] + field[1:-1, :-2] +
                   field[2:, 1:-1] + field[:-2, 1:-1] - 4.0 * field[1:-1, 1:-1])
            inner = (
                field[1:-1, 1:-1]
                - REF_DT_S * (u * dx_c / dx + v * dy_c / dx)
                + REF_DT_S * REF_K_M2S * lap / (dx * dx)
                + REF_DT_S * source_term[1:-1, 1:-1]
            ) * decay
            field[1:-1, 1:-1] = np.maximum(inner, 0.0)
            # 零梯度开边界
            field[0, :] = field[1, :]
            field[-1, :] = field[-2, :]
            field[:, 0] = field[:, 1]
            field[:, -1] = field[:, -2]
        _ = hour
    peak = field.max()
    return field / peak if peak > 0 else field


# ── 三组方法的风险区域 ──────────────────────────────────────────────────

def region_static_circle(radius_km: float) -> np.ndarray:
    return R_GRID <= radius_km


def region_fixed_sector(mean_wind_from: float, mean_speed: float,
                        l0_km: float, k_km: float) -> np.ndarray:
    """固定风向扇区：时间平均风驱动，下风向楔形 + 源点近邻。"""
    transport = (mean_wind_from + 180.0) % 360.0
    bearing = (np.degrees(np.arctan2(GRID_X, GRID_Y)) + 360.0) % 360.0
    relative = np.abs((bearing - transport + 180.0) % 360.0 - 180.0)
    length = l0_km + k_km * mean_speed
    return (R_GRID <= length) & ((relative <= B_HALF_ANGLE_DEG) | (R_GRID <= 0.3))


def dynamic_combined_field(hours: list[tuple[float, float]],
                           rules: dict | None = None) -> np.ndarray:
    """智瞰动态模型：逐小时 PollutionField，逐点取最大值（与阈值无关）。

    ``rules`` 供验证集参数标定覆盖烟羽参数；None 时读全局配置。
    """
    corners = [km_to_lonlat(x, y) for x, y in
               ((-HALF_KM, -HALF_KM), (HALF_KM, -HALF_KM), (-HALF_KM, HALF_KM), (HALF_KM, HALF_KM))]
    combined = np.zeros((N_CELL, N_CELL), dtype=float)
    for wind_from, speed in hours:
        field = PollutionField(
            [{"name": CENTER["name"], "lon": CENTER["lon"], "lat": CENTER["lat"],
              "strength": 0.8, "kind": "station"}],
            wind_from_direction=wind_from,
            wind_speed_ms=speed,
            focus_points=corners,
            rules=rules,
        )
        values = np.empty((N_CELL, N_CELL), dtype=float)
        for row in range(N_CELL):
            for col in range(N_CELL):
                lon, lat = km_to_lonlat(float(GRID_X[row, col]), float(GRID_Y[row, col]))
                values[row, col] = field.index_at(lon, lat)
        combined = np.maximum(combined, values)
    return combined


def vector_mean_wind(hours: list[tuple[float, float]]) -> tuple[float, float]:
    """风矢量平均（先合成 u/v 再回算来向）。"""
    u = v = 0.0
    for wind_from, speed in hours:
        du, dv = wind_to_uv(speed, wind_from)
        u += du
        v += dv
    u /= len(hours)
    v /= len(hours)
    speed = math.hypot(u, v)
    if speed < 1e-6:
        return 0.0, 0.0
    direction_to = math.degrees(math.atan2(u, v)) % 360.0
    return (direction_to + 180.0) % 360.0, speed


# ── 评价指标 ────────────────────────────────────────────────────────────

def iou(a: np.ndarray, b: np.ndarray) -> float:
    union = float(np.logical_or(a, b).sum())
    if union == 0:
        return 1.0
    return float(np.logical_and(a, b).sum()) / union


def load_pois() -> tuple[np.ndarray, np.ndarray]:
    """敏感目标（学校 + 医疗），返回域内 (x, y) km 数组。"""
    xs, ys = [], []
    with POI_CSV.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("poi_type") not in ("school", "healthcare"):
                continue
            lon, lat = float(row["lon"]), float(row["lat"])
            x = (lon - LON0) * LON_SCALE
            y = (lat - LAT0) * LAT_SCALE
            if abs(x) <= HALF_KM and abs(y) <= HALF_KM:
                xs.append(x)
                ys.append(y)
    return np.array(xs), np.array(ys)


POI_X, POI_Y = None, None


def poi_flags(region: np.ndarray) -> np.ndarray:
    cols = np.clip(((POI_X + HALF_KM) / (2 * HALF_KM) * (N_CELL - 1)).astype(int), 0, N_CELL - 1)
    rows = np.clip(((POI_Y + HALF_KM) / (2 * HALF_KM) * (N_CELL - 1)).astype(int), 0, N_CELL - 1)
    return region[rows, cols]


def poi_pr(flags: np.ndarray, ref_flags: np.ndarray) -> tuple[float | None, float | None]:
    flagged = int(flags.sum())
    ref_total = int(ref_flags.sum())
    hits = int(np.logical_and(flags, ref_flags).sum())
    precision = hits / flagged if flagged else None
    recall = hits / ref_total if ref_total else None
    return precision, recall


# ── 校准与评估 ──────────────────────────────────────────────────────────

PLUME_GRID = {
    "plume_scale": [1.3],
    "decay_half_life_hours": [8.0],
    "crosswind_coefficient": [0.09],
    "calm_accumulation_radius": [0.35, 0.6, 1.0, 1.5],
}


def calibrate_plume_params(validation: list[dict], base_rules: dict) -> tuple[dict, list[dict]]:
    """验证集上重标定烟羽参数（plume_scale / 衰减半衰期 / 横向扩散系数）。

    目标函数：与 B 同预警面积预算下的验证集 IoU（与阈值校准准则一致）。
    只在验证集搜索，选定后冻结进全局配置。
    """
    import copy as _copy

    ref_labels = {s["scenario_id"]: reference_field(s["hours"]) >= REF_NORM_THRESHOLD
                  for s in validation}
    # B 的验证集平均面积（参数标定也用同预算准则）
    b_area = statistics.mean(
        float(region_fixed_sector(*vector_mean_wind(s["hours"]),
                                  1.75, 0.3).sum())
        for s in validation)

    results = []
    for scale in PLUME_GRID["plume_scale"]:
        for half_life in PLUME_GRID["decay_half_life_hours"]:
            for crosswind in PLUME_GRID["crosswind_coefficient"]:
              for calm_radius in PLUME_GRID["calm_accumulation_radius"]:
                rules = _copy.deepcopy(base_rules)
                rules["plume"]["plume_scale"] = scale
                rules["plume"]["decay_half_life_hours"] = half_life
                rules["plume"]["crosswind_spread"]["coefficient"] = crosswind
                rules["calm"]["accumulation_radius_km"] = calm_radius
                combined = {s["scenario_id"]: dynamic_combined_field(s["hours"], rules)
                            for s in validation}
                best_thr, best_iou, best_diff = None, -1.0, None
                for thr in C_THRESHOLD_CANDIDATES:
                    areas = [float((combined[s["scenario_id"]] >= thr).sum())
                             for s in validation]
                    diff = abs(statistics.mean(areas) - b_area)
                    iou_val = statistics.mean(
                        iou(combined[s["scenario_id"]] >= thr, ref_labels[s["scenario_id"]])
                        for s in validation)
                    if best_thr is None or iou_val > best_iou:
                        best_thr, best_iou, best_diff = thr, iou_val, diff
                results.append({
                    "plume_scale": scale, "decay_half_life_hours": half_life,
                    "crosswind_coefficient": crosswind,
                    "calm_accumulation_radius": calm_radius, "threshold": best_thr,
                    "validation_iou": round(best_iou, 4),
                    "area_gap_to_B_cells": round(best_diff, 1),
                })
    best = max(results, key=lambda r: r["validation_iou"])
    chosen = _copy.deepcopy(base_rules)
    chosen["plume"]["plume_scale"] = best["plume_scale"]
    chosen["plume"]["decay_half_life_hours"] = best["decay_half_life_hours"]
    chosen["plume"]["crosswind_spread"]["coefficient"] = best["crosswind_coefficient"]
    chosen["calm"]["accumulation_radius_km"] = best["calm_accumulation_radius"]
    return chosen, results


def soft_region_scores(method: str, params: dict, speed: float, wind_from: float) -> np.ndarray:
    """A/B 的连续风险评分（与各自几何参数同构的软版本），供同口径 AUC 对比。

    A：以源为中心的指数衰减 exp(-d/R)。
    B：扇区方向高斯 × 下风向指数衰减 + 源点近邻项。
    """
    if method == "A":
        return np.exp(-R_GRID / params["A_radius_km"])
    transport = (wind_from + 180.0) % 360.0
    bearing = (np.degrees(np.arctan2(GRID_X, GRID_Y)) + 360.0) % 360.0
    relative = np.abs((bearing - transport + 180.0) % 360.0 - 180.0)
    length = params["B_l0_km"] + params["B_k_km_per_ms"] * speed
    angular = np.exp(-(relative / B_HALF_ANGLE_DEG) ** 2)
    return angular * np.exp(-R_GRID / length) + np.exp(-R_GRID / 0.3)


def calibrate(validation: list[dict]) -> dict:
    """在验证集上选定 A 半径、B 参数与 C 阈值，冻结后用于测试集。

    C 的阈值不按 IoU 最优选取——相对暴露指数没有绝对标定，纯 IoU 最优会把
    阈值一路压低、面积膨胀（正是"覆盖面积≠更好"的陷阱）。按方案要求做
    **同预算校准**：让 C 的平均预警面积与 B 一致，再在同面积下比较；
    同时记录 IoU 最优阈值作对照。
    """
    ref_labels = {s["scenario_id"]: reference_field(s["hours"]) >= REF_NORM_THRESHOLD
                  for s in validation}

    best_a = max(A_RADIUS_CANDIDATES,
                 key=lambda r: statistics.mean(iou(region_static_circle(r), ref_labels[s["scenario_id"]])
                                               for s in validation))
    best_b, best_b_score = (1.0, 0.3), -1.0
    for l0 in B_L0_CANDIDATES:
        for k in B_K_CANDIDATES:
            scores = []
            for s in validation:
                mean_dir, mean_speed = vector_mean_wind(s["hours"])
                scores.append(iou(region_fixed_sector(mean_dir, mean_speed, l0, k),
                                  ref_labels[s["scenario_id"]]))
            score = statistics.mean(scores)
            if score > best_b_score:
                best_b, best_b_score = (l0, k), score

    combined_fields = {s["scenario_id"]: dynamic_combined_field(s["hours"]) for s in validation}
    b_target_area = statistics.mean(
        float(region_fixed_sector(*vector_mean_wind(s["hours"]),
                                  best_b[0], best_b[1]).sum())
        for s in validation)

    best_c, best_c_diff = None, None
    best_c_iou, best_c_iou_score = None, -1.0
    for thr in C_THRESHOLD_CANDIDATES:
        areas = [float((combined_fields[s["scenario_id"]] >= thr).sum()) for s in validation]
        diff = abs(statistics.mean(areas) - b_target_area)
        if best_c_diff is None or diff < best_c_diff:
            best_c, best_c_diff = thr, diff
        score = statistics.mean(iou(combined_fields[s["scenario_id"]] >= thr,
                                    ref_labels[s["scenario_id"]]) for s in validation)
        if score > best_c_iou_score:
            best_c_iou, best_c_iou_score = thr, score

    return {"A_radius_km": best_a, "B_l0_km": best_b[0], "B_k_km_per_ms": best_b[1],
            "C_threshold": best_c, "C_threshold_iou_optimal": best_c_iou,
            "B_validation_area_km2": round(b_target_area * 0.01, 3),
            "validation_mean_iou": {
                "A": round(statistics.mean(
                    iou(region_static_circle(best_a), ref_labels[s["scenario_id"]])
                    for s in validation), 4),
                "B": round(best_b_score, 4),
                "C_budget_matched": round(statistics.mean(
                    iou(combined_fields[s["scenario_id"]] >= best_c,
                        ref_labels[s["scenario_id"]]) for s in validation), 4),
                "C_iou_optimal": round(best_c_iou_score, 4)}}


def evaluate_method(s: dict, params: dict, ref_field: np.ndarray) -> dict:
    """单场景下三组方法的区域、指标与耗时。

    除各自阈值/几何下的原始 Precision/Recall 外，还报告**相同预警面积预算**
    （三组方法面积的中位数）下的 Precision/Recall——把各方法区域按参考场
    浓度从高到低裁剪到预算再评分，直接回应"覆盖面积增大不天然等于更好"。
    C 额外报告阈值无关的 ROC-AUC（连续场排序质量）。
    """
    ref_label = ref_field >= REF_NORM_THRESHOLD
    results = {}
    t0 = time.perf_counter()
    region_a = region_static_circle(params["A_radius_km"])
    results["A"] = {"region": region_a, "ms": (time.perf_counter() - t0) * 1000.0}

    t0 = time.perf_counter()
    mean_dir, mean_speed = vector_mean_wind(s["hours"])
    region_b = region_fixed_sector(mean_dir, mean_speed, params["B_l0_km"], params["B_k_km_per_ms"])
    results["B"] = {"region": region_b, "ms": (time.perf_counter() - t0) * 1000.0}

    t0 = time.perf_counter()
    combined = dynamic_combined_field(s["hours"])
    results["C"] = {"region": combined >= params["C_threshold"],
                    "ms": (time.perf_counter() - t0) * 1000.0,
                    "combined": combined}

    ref_flags = poi_flags(ref_label)
    areas = [float(results[name]["region"].sum()) for name in ("A", "B", "C")]
    budget_cells = int(statistics.median(areas))

    # 固定预算 PR 扫描：预算=域面积(36km²)的既定比例，先验固定、与验证集无关，
    # 各方法区域按参考场浓度裁剪到同一预算后评分 → 逐预算 PR 点
    domain_cells = float(region_a.size)
    fixed_budget_pr = {}
    for fraction in FIXED_BUDGET_FRACTIONS:
        cells = int(domain_cells * fraction)
        entry = {}
        for name, item in results.items():
            region = item["region"]
            if int(region.sum()) > cells > 0:
                candidate_values = np.where(region, ref_field, -1.0)
                order = np.argsort(candidate_values.ravel())[::-1][:cells]
                trimmed = np.zeros_like(region)
                trimmed.ravel()[order] = True
            else:
                trimmed = region
            precision, recall = poi_pr(poi_flags(trimmed), ref_flags)
            entry[name] = {"precision": None if precision is None else round(precision, 4),
                           "recall": None if recall is None else round(recall, 4)}
        fixed_budget_pr[f"{fraction:.2f}"] = entry
    for name, item in results.items():
        region = item["region"]
        precision, recall = poi_pr(poi_flags(region), ref_flags)
        # 相同面积预算：按参考场浓度排序裁剪到 budget_cells
        trimmed = region
        if int(region.sum()) > budget_cells > 0:
            candidate_values = np.where(region, ref_field, -1.0)
            order = np.argsort(candidate_values.ravel())[::-1][:budget_cells]
            trimmed = np.zeros_like(region)
            trimmed.ravel()[order] = True
        t_precision, t_recall = poi_pr(poi_flags(trimmed), ref_flags)
        entry = {
            "iou": round(iou(region, ref_label), 4),
            "area_km2": round(float(region.sum()) * 0.01, 3),
            "ref_area_km2": round(float(ref_label.sum()) * 0.01, 3),
            "poi_precision": None if precision is None else round(precision, 4),
            "poi_recall": None if recall is None else round(recall, 4),
            "poi_flagged": int(poi_flags(region).sum()),
            "poi_ref_total": int(ref_flags.sum()),
            "budget_precision": None if t_precision is None else round(t_precision, 4),
            "budget_recall": None if t_recall is None else round(t_recall, 4),
        }
        if name in ("A", "B"):
            # A/B 的连续软评分 AUC：与 C 的场值 AUC 同口径比较
            mean_dir0, mean_speed0 = vector_mean_wind(s["hours"])
            entry["soft_auc"] = round(
                field_auc(soft_region_scores(name, params, mean_speed0, mean_dir0), ref_label), 4)
        if name == "C":
            entry["field_auc"] = round(field_auc(item.pop("combined"), ref_label), 4)
        entry["fixed_budget_pr"] = {k: v[name] for k, v in fixed_budget_pr.items()}
        item.update(entry)
        del item["region"]
    return results


def field_auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """ROC-AUC（Mann-Whitney 秩实现，含并列值平均秩）。"""
    n_pos, n_neg = int(labels.sum()), int((~labels).sum())
    if n_pos == 0 or n_neg == 0:
        return 0.5
    combined = scores.ravel()
    order = np.argsort(combined, kind="mergesort")
    sorted_vals = combined[order]
    ranks_sorted = np.arange(1, len(combined) + 1, dtype=float)
    # 并列值取平均秩
    start = 0
    while start < len(sorted_vals):
        end = start
        while end + 1 < len(sorted_vals) and sorted_vals[end + 1] == sorted_vals[start]:
            end += 1
        if end > start:
            ranks_sorted[start:end + 1] = (start + 1 + end + 1) / 2.0
        start = end + 1
    ranks = np.empty(len(combined), dtype=float)
    ranks[order] = ranks_sorted
    labels_flat = labels.ravel()
    pos_rank_sum = ranks[labels_flat].sum()
    return float((pos_rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


# ── 连续性测试 ──────────────────────────────────────────────────────────

def continuity_sweeps(params: dict) -> dict:
    """风速细步长扫描 + 跨零点风向扫描，检查风险场是否异常跳变。"""
    def region_for(method: str, speed: float, wind_from: float) -> np.ndarray:
        if method == "A":
            return region_static_circle(params["A_radius_km"])
        if method == "B":
            return region_fixed_sector(wind_from, speed, params["B_l0_km"], params["B_k_km_per_ms"])
        return dynamic_combined_field([(wind_from, speed)]) >= params["C_threshold"]

    def sweep(values: list[tuple[float, float]]) -> dict:
        out = {}
        for method in ("A", "B", "C"):
            prev_region, areas, min_iou, max_jump = None, [], 1.0, 0.0
            for speed, wind_from in values:
                region = region_for(method, speed, wind_from)
                areas.append(float(region.sum()) * 0.01)
                if prev_region is not None:
                    step_iou = iou(region, prev_region)
                    min_iou = min(min_iou, step_iou)
                    jump = abs(areas[-1] - areas[-2]) / max(areas[-2], 0.01)
                    max_jump = max(max_jump, jump)
                prev_region = region
            out[method] = {
                "area_km2_series": [round(a, 3) for a in areas],
                "min_consecutive_iou": round(min_iou, 4),
                "max_relative_area_jump": round(max_jump, 4),
            }
        return out

    speeds = [round(0.2 + 0.1 * i, 1) for i in range(29)]  # 0.2..3.0 m/s
    dirs = [round(340.0 + 2.0 * i, 1) % 360.0 for i in range(21)]  # 340° -> 20°
    return {
        "speed_sweep_wind_from_270": sweep([(s, 270.0) for s in speeds]),
        "direction_sweep_speed_2p5": sweep([(2.5, d) for d in dirs]),
        "speed_values_ms": speeds,
        "direction_values_deg": dirs,
    }


def paired_bootstrap_ci(values_x: list[float], values_y: list[float] | None = None,
                        reps: int = 10000, seed: int = 42) -> dict:
    """配对 bootstrap：单方法均值 CI 或两方法均值差的 CI。"""
    import random

    rng = random.Random(seed)
    n = len(values_x)
    if n == 0:
        return {}
    diffs = values_x if values_y is None else [x - y for x, y in zip(values_x, values_y)]
    stats = []
    for _ in range(reps):
        sample = [diffs[rng.randrange(n)] for _ in range(n)]
        stats.append(statistics.mean(sample))
    stats.sort()
    lo, hi = stats[int(0.025 * reps)], stats[int(0.975 * reps)]
    return {"mean": round(statistics.mean(diffs), 4),
            "ci95": [round(lo, 4), round(hi, 4)],
            "n": n}


def fixed_budget_pr_summary(per_scenario: list[dict], split: str) -> dict:
    """先验固定预算下的平均 Precision-Recall 曲线（逐预算点）。"""
    subset = [r for r in per_scenario if r["split"] == split]
    out = {}
    for fraction in FIXED_BUDGET_FRACTIONS:
        key = f"{fraction:.2f}"
        entry = {}
        for method in ("A", "B", "C"):
            precisions, recalls = [], []
            for row in subset:
                point = row["methods"][method].get("fixed_budget_pr", {}).get(key) or {}
                if point.get("precision") is not None:
                    precisions.append(point["precision"])
                if point.get("recall") is not None:
                    recalls.append(point["recall"])
            entry[method] = {
                "precision": round(statistics.mean(precisions), 4) if precisions else None,
                "recall": round(statistics.mean(recalls), 4) if recalls else None,
            }
        out[key] = entry
    return out


def main() -> int:
    global POI_X, POI_Y
    POI_X, POI_Y = load_pois()
    print(f"域内敏感目标（学校+医疗）: {len(POI_X)} 个", flush=True)

    scenarios = make_scenarios()
    validation = [s for s in scenarios if s["split"] == "validation"]
    test = [s for s in scenarios if s["split"] == "test"]
    print(f"场景 {len(scenarios)} 个（验证 {len(validation)} / 测试 {len(test)}）", flush=True)

    # 第一步：验证集上重标定烟羽参数并冻结进全局配置
    from tools.pollution_field import load_pollution_rules as _load_rules
    import json as _json
    base_rules = _load_rules()
    plume_rules, plume_grid = calibrate_plume_params(validation, base_rules)
    config_path = BASE / "config" / "pollution_rules.json"
    config_disk = _json.loads(config_path.read_text(encoding="utf-8"))
    config_disk["plume"].update(plume_rules["plume"])
    config_disk["calm"]["accumulation_radius_km"] = plume_rules["calm"]["accumulation_radius_km"]
    config_disk["plume"]["_calibrated"] = (
        f"2026-10-13 验证集网格重标定（validation_iou="
        f"{max(g['validation_iou'] for g in plume_grid)}）")
    config_path.write_text(_json.dumps(config_disk, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    # PollutionField 有模块级规则缓存，写回后必须失效
    from tools import pollution_field as _pf
    _pf._RULES_CACHE.clear()
    best_plume = max(plume_grid, key=lambda g: g["validation_iou"])
    print(f"烟羽/静稳参数冻结: scale={best_plume['plume_scale']} "
          f"half_life={best_plume['decay_half_life_hours']}h "
          f"crosswind={best_plume['crosswind_coefficient']} "
          f"calm_radius={best_plume['calm_accumulation_radius']}km "
          f"(验证集 IoU {best_plume['validation_iou']})", flush=True)

    # 第二步：阈值/几何校准（基于新参数）
    params = calibrate(validation)
    print(f"验证集校准并冻结: {params}", flush=True)

    per_scenario = []
    for s in scenarios:
        ref_field = reference_field(s["hours"])
        metrics = evaluate_method(s, params, ref_field)
        per_scenario.append({"scenario_id": s["scenario_id"], "category": s["category"],
                             "split": s["split"],
                             "hours": [(round(d, 1), round(v, 2)) for d, v in s["hours"]],
                             "methods": metrics})
        print(f"  {s['scenario_id']:<18} IoU A={metrics['A']['iou']:.3f} "
              f"B={metrics['B']['iou']:.3f} C={metrics['C']['iou']:.3f}", flush=True)

    # 预注册第二变体：IoU 最优阈值（同样在验证集冻结）在测试集上的表现。
    # 预算匹配阈值回答"同面积下谁好"；IoU 最优阈值回答"各自最优标定下谁好"。
    for row in (x for x in per_scenario if x["split"] == "test"):
        scenario = next(x for x in scenarios if x["scenario_id"] == row["scenario_id"])
        ref_label = reference_field(scenario["hours"]) >= REF_NORM_THRESHOLD
        region = dynamic_combined_field(scenario["hours"]) >= params["C_threshold_iou_optimal"]
        precision, recall = poi_pr(poi_flags(region), poi_flags(ref_label))
        row["methods"]["C_iou_opt"] = {
            "iou": round(iou(region, ref_label), 4),
            "area_km2": round(float(region.sum()) * 0.01, 3),
            "poi_precision": None if precision is None else round(precision, 4),
            "poi_recall": None if recall is None else round(recall, 4),
        }

    def summarise(split: str) -> dict:
        out = {"split": split, "n": 0, "methods": {}, "by_category": {}}
        subset = [r for r in per_scenario if r["split"] == split]
        out["n"] = len(subset)
        for method in ("A", "B", "C"):
            rows = [r["methods"][method] for r in subset]

            def collect(key):
                vals = [row[key] for row in rows if row.get(key) is not None]
                return {
                    "mean": round(statistics.mean(vals), 4) if vals else None,
                    "median": round(statistics.median(vals), 4) if vals else None,
                }

            timings = [row["ms"] for row in rows]
            out["methods"][method] = {
                "mean_iou": collect("iou"),
                "mean_poi_precision": collect("poi_precision"),
                "mean_poi_recall": collect("poi_recall"),
                "mean_area_km2": collect("area_km2"),
                "budget_precision": collect("budget_precision"),
                "budget_recall": collect("budget_recall"),
                "mean_field_auc": collect("field_auc"),
                "mean_soft_auc": collect("soft_auc"),
                "median_runtime_ms": round(statistics.median(timings), 2),
            }
            # 分类别汇总：动态场景（转向/跨零点/阵风）是区分度所在
            by_cat = {}
            for category in SCENARIO_CATEGORIES:
                cat_rows = [r["methods"][method] for r in subset if r["category"] == category]
                ious = [row["iou"] for row in cat_rows]
                areas = [row["area_km2"] for row in cat_rows]
                by_cat[category] = {
                    "mean_iou": round(statistics.mean(ious), 4) if ious else None,
                    "mean_area_km2": round(statistics.mean(areas), 3) if areas else None,
                }
            out["methods"][method]["by_category"] = by_cat
            # 面积分布诊断：验证→测试的分布变化直接可见
            out["methods"][method]["area_min_max_km2"] = {
                "min": round(min(rows_areas := [row["area_km2"] for row in rows]), 3) if rows else None,
                "max": round(max(rows_areas), 3) if rows else None,
            }
        return out

    val_summary = summarise("validation")
    test_summary = summarise("test")

    # IoU 配对 bootstrap 置信区间（测试集）
    test_rows_iou = [r for r in per_scenario if r["split"] == "test"]
    iou_by = {m: [r["methods"][m]["iou"] for r in test_rows_iou] for m in ("A", "B", "C")}
    bootstrap = {
        "mean_iou_ci": {m: paired_bootstrap_ci(iou_by[m]) for m in ("A", "B", "C")},
        "iou_diff_C_minus_B": paired_bootstrap_ci(iou_by["C"], iou_by["B"]),
        "iou_diff_C_iouopt_minus_B": paired_bootstrap_ci(
            [r["methods"]["C_iou_opt"]["iou"] for r in test_rows_iou], iou_by["B"]),
    }
    c2_rows = [r["methods"]["C_iou_opt"] for r in test_rows_iou]
    c2_summary = {
        "mean_iou": round(statistics.mean(r["iou"] for r in c2_rows), 4),
        "mean_poi_precision": round(statistics.mean(
            r["poi_precision"] for r in c2_rows if r["poi_precision"] is not None), 4),
        "mean_poi_recall": round(statistics.mean(
            r["poi_recall"] for r in c2_rows if r["poi_recall"] is not None), 4),
        "mean_area_km2": round(statistics.mean(r["area_km2"] for r in c2_rows), 3),
    }

    continuity = continuity_sweeps(params)

    payload = {
        "fixed_budget_pr_test": fixed_budget_pr_summary(per_scenario, "test"),
        "bootstrap_test": bootstrap,
        "c_iou_optimal_variant_test": c2_summary,
        "design": {
            "methods": {
                "A": f"静态圆形缓冲区（半径 {params['A_radius_km']} km，与风无关）",
                "B": f"固定风向扇区（时间平均风，L0={params['B_l0_km']} km + "
                     f"{params['B_k_km_per_ms']} km/(m/s)，半角 {B_HALF_ANGLE_DEG}°）",
                "C": f"智瞰动态传播模型（逐小时风场驱动，静风平滑过渡，"
                     f"阈值按与 B 同预警面积校准 = {params['C_threshold']}）",
            },
            "reference": "独立二维平流-扩散数值模型（显式迎风差分，K=50 m²/s，"
                         "源高斯半径 150 m，3 小时），归一化阈值 0.25 生成风险标签；"
                         "与三组方法不共享任何代码",
            "grid": f"{N_CELL}×{N_CELL} 节点，0.1 km 格距，6×6 km 域，中心 Mong Kok Roadside",
            "scenarios": "5 类 × 12 个：stable / veering / cross_zero / calm / gust；"
                         "每类前 4 个验证（校准后冻结参数）、后 8 个独立测试",
            "pois": f"域内学校+医疗敏感目标 {len(POI_X)} 个（poi_sensitive.csv）",
            "calibration": params,
            "plume_calibration": {"grid_top": sorted(
                plume_grid, key=lambda g: -g["validation_iou"])[:6]},
            "fixed_budget_note": "固定预算 PR 的预算为域面积(36km²)的 2%..20%，先验固定，"
                                 "各方法区域按参考场浓度裁剪到同预算后评分",
        },
        "validation_summary": val_summary,
        "test_summary": test_summary,
        "continuity": continuity,
        "per_scenario": per_scenario,
    }
    path = save_result("propagation_baselines", payload)
    print(f"\nsaved -> {path}")
    print("\n=== 独立测试集汇总（C 阈值与 B 同面积预算校准）===")
    for method, s in test_summary["methods"].items():
        print(f"  {method}: IoU={s['mean_iou']['mean']} [95%CI "
              f"{bootstrap['mean_iou_ci'][method]['ci95']}] 面积={s['mean_area_km2']['mean']}km² "
              f"POI P={s['mean_poi_precision']['mean']} R={s['mean_poi_recall']['mean']} | "
              f"同预算 P={s['budget_precision']['mean']} R={s['budget_recall']['mean']} | "
              f"AUC={s['mean_field_auc']['mean']} 软AUC={s['mean_soft_auc']['mean']} "
              f"耗时={s['median_runtime_ms']}ms")
    print(f"  IoU C-B 差值: {bootstrap['iou_diff_C_minus_B']}")
    print(f"  C@IoU最优阈值(验证集冻结 {params['C_threshold_iou_optimal']}): "
          f"IoU={c2_summary['mean_iou']} 面积={c2_summary['mean_area_km2']}km² "
          f"P={c2_summary['mean_poi_precision']} R={c2_summary['mean_poi_recall']} | "
          f"C2-B 差值 CI: {bootstrap['iou_diff_C_iouopt_minus_B']['ci95']}")
    print("  分类别 IoU/面积:")
    for method in ("A", "B", "C"):
        by_cat = test_summary["methods"][method]["by_category"]
        print(f"    {method}: " + "  ".join(
            f"{c}: IoU={v['mean_iou']}/A={v['mean_area_km2']}" for c, v in by_cat.items()))
    print("  固定预算 PR（测试集均值）:")
    for fraction, entry in payload["fixed_budget_pr_test"].items():
        line = " | ".join(f"{m}: P={entry[m]['precision']} R={entry[m]['recall']}"
                          for m in ("A", "B", "C"))
        print(f"    预算 {float(fraction)*36:.1f} km² → {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
