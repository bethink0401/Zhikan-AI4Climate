"""V9 Experiment 2: innovation-2 observational backtest (leave-one-station-out).

Framing (per the V9 protocol): this is a HISTORICAL-WEATHER-DRIVEN BACKTEST,
not a real forecast. The held-out station's own observations are used ONLY as
the verification label and are never fed to any method.

Design:
  - Source station: Mong Kok Roadside (2024 holdout year);
  - Held-out verification stations: Central & Causeway Bay roadside — each
    3-hour window of 2024 produces one (prediction, observation) pair per
    held-out station;
  - Prediction = "is the held-out station inside the method's risk region";
    Label = held-out station's window NO2 >= its own 2024 p90 (relative,
    year-scoped);
  - Methods (all V8-frozen parameters, zero re-tuning):
      A  static circle (frozen radius);
      B  fixed-wind sector (frozen L0/K, window vector-mean wind);
      C  two-scale dynamic field (frozen plume/calm params), threshold
         budget-matched on the V7 validation scenarios (not on 2024 data);
  - Continuous scores at the station point give threshold-free ROC-AUC;
  - Weather split: calm (window mean wind < 2 m/s) vs windy;
  - Clustered (by date) bootstrap CIs.

Usage: /usr/bin/python3 tests/benchmarks/v9/propagation_external.py
"""
from __future__ import annotations

import json
import math
import sqlite3
import statistics
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(HERE.parent))

from common import save_result  # noqa: E402
from metrics import clustered_paired_diff  # noqa: E402
import bench_propagation_baselines as prop  # noqa: E402

YEAR = 2024
SLOT_HOURS = (0, 4, 8, 12, 16, 20)
WINDOW_HOURS = 3
SOURCE = prop.CENTER  # Mong Kok
HELD_OUT = [
    {"db": "CENTRAL", "name": "Central Roadside", "lat": 22.2858, "lon": 114.1577},
    {"db": "CAUSEWAY BAY", "name": "Causeway Bay Roadside", "lat": 22.2801,
     "lon": 114.1850},
]
#: V8 冻结参数（propagation_v7.json 校准值；阈值预算匹配沿用其验证集结果）
FROZEN = {"A_radius_km": 1.0, "B_l0_km": 1.5, "B_k_km_per_ms": 0.6,
          "C_threshold": 0.06}


def station_xy(lat: float, lon: float) -> tuple[float, float]:
    """站点相对源站的 km 坐标（与传播基准同一投影）。"""
    return ((lon - SOURCE["lon"]) * prop.LON_SCALE,
            (lat - SOURCE["lat"]) * prop.LAT_SCALE)


def field_value_at(lon: float, lat: float, hours: list[tuple[float, float]]) -> float:
    """智瞰动态场在站点位置的逐小时最大值（解析路径，冻结参数）。"""
    from tools.pollution_field import PollutionField
    best = 0.0
    for wind_from, speed in hours:
        field = PollutionField(
            [{"name": SOURCE["name"], "lon": SOURCE["lon"], "lat": SOURCE["lat"],
              "strength": 0.8, "kind": "station"}],
            wind_from_direction=wind_from, wind_speed_ms=speed,
            focus_points=[(lon, lat)])
        best = max(best, field.index_at(lon, lat))
    return best


def soft_scores_at(station_key: str, distance_km: float, speed: float,
                   wind_from: float, params: dict) -> dict:
    """A/B 的连续软评分（与 V7 soft_region_scores 同构的单点版）。"""
    score_a = math.exp(-distance_km / params["A_radius_km"])
    transport = (wind_from + 180.0) % 360.0
    # 站点相对源站的方位角（北为 0、顺时针，x 东 y 北）
    x, y = _POINT_CACHE[station_key]
    bearing = (math.degrees(math.atan2(x, y)) + 360.0) % 360.0
    relative = abs((bearing - transport + 180.0) % 360.0 - 180.0)
    length = params["B_l0_km"] + params["B_k_km_per_ms"] * speed
    angular = math.exp(-(relative / prop.B_HALF_ANGLE_DEG) ** 2)
    score_b = angular * math.exp(-distance_km / length) + math.exp(-distance_km / 0.3)
    return {"A": score_a, "B": score_b}


_POINT_CACHE: dict[str, tuple[float, float]] = {}


def region_flags(station_key: str, speed: float, wind_from: float,
                 c_value: float, params: dict) -> dict:
    x, y = _POINT_CACHE[station_key]
    inside_a = math.hypot(x, y) <= params["A_radius_km"]
    transport = (wind_from + 180.0) % 360.0
    bearing = (math.degrees(math.atan2(x, y)) + 360.0) % 360.0
    relative = abs((bearing - transport + 180.0) % 360.0 - 180.0)
    length = params["B_l0_km"] + params["B_k_km_per_ms"] * speed
    inside_b = (relative <= prop.B_HALF_ANGLE_DEG) and (math.hypot(x, y) <= length)
    inside_c = c_value >= params["C_threshold"]
    return {"A": inside_a, "B": inside_b, "C": inside_c}


def roc_auc(scores: list[float], labels: list[bool]) -> float | None:
    pos = [s for s, l in zip(scores, labels) if l]
    neg = [s for s, l in zip(scores, labels) if not l]
    if not pos or not neg:
        return None
    wins = ties = 0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    return round((wins + 0.5 * ties) / (len(pos) * len(neg)), 4)


def pr_at_flag(flags: list[bool], labels: list[bool]) -> dict:
    tp = sum(1 for f, l in zip(flags, labels) if f and l)
    fp = sum(1 for f, l in zip(flags, labels) if f and not l)
    fn = sum(1 for f, l in zip(flags, labels) if not f and l)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    return {"precision": None if precision is None else round(precision, 4),
            "recall": None if recall is None else round(recall, 4),
            "flag_rate": round(sum(flags) / len(flags), 4) if flags else None}


def main() -> int:
    from tools.pollution_field import PollutionField  # noqa: F401 触发规则加载
    conn = sqlite3.connect(BASE / "data" / "air_quality.db")

    # 冻结参数从 V7 保存值读取（不硬编码，防口径漂移）
    saved = json.loads((HERE.parent / "results" / "propagation_v7.json")
                       .read_text(encoding="utf-8"))
    calib = saved["design"]["calibration"]
    params = {"A_radius_km": calib["A_radius_km"], "B_l0_km": calib["B_l0_km"],
              "B_k_km_per_ms": calib["B_k_km_per_ms"],
              "C_threshold": calib["C_threshold"]}
    print(f"V8 冻结参数: {params}", flush=True)

    # 2024 全年逐小时风（Open-Meteo Archive 历史再分析——回测口径）
    wind = gv6_wind()
    rows = []
    p90s = {}
    for held in HELD_OUT:
        values = sorted(v for (v,) in conn.execute(
            "SELECT no2 FROM air_quality_hourly WHERE station_name=? AND no2 IS NOT NULL "
            "AND time LIKE ?", (held["db"], f"{YEAR}-%")))
        p90s[held["db"]] = values[int(0.90 * len(values))]
    print(f"留出站 2024 自身 p90: {p90s}", flush=True)

    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(time, 1, 10) FROM air_quality_hourly "
        "WHERE time LIKE ? ORDER BY 1", (f"{YEAR}-%",))]
    t0 = time.perf_counter()
    n_windows = 0
    for date in dates:
        for hour in SLOT_HOURS:
            times = [f"{date} {h:02d}:00:00"
                     for h in range(hour, hour + WINDOW_HOURS)]
            hour_keys = [f"{date} {h:02d}:00:00"
                         for h in range(hour, hour + WINDOW_HOURS)]
            winds = [wind.get(k) for k in hour_keys]
            winds = [w for w in winds if w is not None]
            if not winds:
                continue
            # 风的矢量平均（与主基准 vector_mean_wind 同口径）
            mean_dir, mean_speed = vector_mean(winds)
            for held in HELD_OUT:
                obs = conn.execute(
                    "SELECT no2 FROM air_quality_hourly WHERE station_name=? "
                    "AND no2 IS NOT NULL AND time IN (?,?,?)",
                    (held["db"], *times)).fetchall()
                if len(obs) < WINDOW_HOURS:
                    continue
                mean_no2 = statistics.mean(r[0] for r in obs)
                label = mean_no2 >= p90s[held["db"]]
                _POINT_CACHE[held["name"]] = station_xy(held["lat"], held["lon"])
                distance = math.hypot(*_POINT_CACHE[held["name"]])
                # 点位回测不受 6 km 评估域限制：留出站 3.9/4.6 km 的距离
                # 本身就是方法需要面对的真实条件（A 半径 1 km 在该距离下
                # 召回为 0 是真实结果，如实保留）。
                c_value = field_value_at(held["lon"], held["lat"],
                                         list(zip([w[0] for w in winds],
                                                  [w[1] for w in winds])))
                soft = soft_scores_at(held["name"], distance, mean_speed,
                                      mean_dir, params)
                flags = region_flags(held["name"], mean_speed, mean_dir,
                                     c_value, params)
                rows.append({
                    "date": date, "hour": hour, "held_out": held["name"],
                    "wind": round(mean_speed, 2), "calm": mean_speed < 2.0,
                    "label": label, "distance_km": round(distance, 3),
                    "flag_A": flags["A"], "flag_B": flags["B"], "flag_C": flags["C"],
                    "score_A": round(soft["A"], 4), "score_B": round(soft["B"], 4),
                    "score_C": round(c_value, 6),
                    "mean_no2": round(mean_no2, 1),
                })
                n_windows += 1
    conn.close()
    print(f"有效 (窗口×留出站) 对: {n_windows}（耗时 {time.perf_counter()-t0:.0f}s）",
          flush=True)

    # ── 汇总 ────────────────────────────────────────────────────────────────
    summary = {}
    for method in ("A", "B", "C"):
        flags = [r[f"flag_{method}"] for r in rows]
        labels = [r["label"] for r in rows]
        scores = [r[f"score_{method}"] for r in rows]
        entry = {"point_metrics": pr_at_flag(flags, labels),
                 "roc_auc": roc_auc(scores, labels)}
        for name, subset in (("calm", [r for r in rows if r["calm"]]),
                             ("windy", [r for r in rows if not r["calm"]])):
            entry[name] = {
                "n": len(subset),
                "point_metrics": pr_at_flag([r[f"flag_{method}"] for r in subset],
                                            [r["label"] for r in subset]),
                "roc_auc": roc_auc([r[f"score_{method}"] for r in subset],
                                   [r["label"] for r in subset]),
            }
        summary[method] = entry

    # 聚类（按日期）配对 CI：C 相对 B 的逐对命中差（Jaccard 式 0/1 命中）
    def hit(r, m):
        return 1.0 if r[f"flag_{m}"] == r["label"] else 0.0
    ci = clustered_paired_diff([hit(r, "C") for r in rows],
                               [hit(r, "B") for r in rows],
                               [r["date"] for r in rows], reps=10000)

    # 天气分组 CSV
    with (HERE / "results" / "v9_propagation_by_weather.csv").open("w", newline="",
                                                                   encoding="utf-8") as h:
        writer = csv.writer(h)
        writer.writerow(["weather", "method", "n", "precision", "recall", "roc_auc"])
        for weather in ("calm", "windy"):
            for method in ("A", "B", "C"):
                e = summary[method][weather]
                writer.writerow([weather, method, e["n"],
                                 e["point_metrics"]["precision"],
                                 e["point_metrics"]["recall"], e["roc_auc"]])

    payload = {
        "design": {
            "framing": "历史气象驱动回测（Open-Meteo Archive 再分析），非真实预报",
            "leakage_control": "留出站观测仅作验证标签，未输入任何方法；所有方法用同一数据截止",
            "source_station": SOURCE["name"], "held_out": [h["name"] for h in HELD_OUT],
            "frozen_params": params,
            "cluster_unit": "date",
            "window": f"{WINDOW_HOURS}h 窗口，起始于 {SLOT_HOURS} 时",
        },
        "n_pairs": n_windows,
        "summary": summary,
        "clustered_ci_C_minus_B": ci,
    }
    path = save_result("v9_propagation", payload)
    print(f"\nsaved -> {path}")
    print("=== 留出站点观测回测（2024）===")
    for method in ("A", "B", "C"):
        s = summary[method]
        print(f"  {method}: P={s['point_metrics']['precision']} "
              f"R={s['point_metrics']['recall']} AUC={s['roc_auc']} | "
              f"calm AUC={s['calm']['roc_auc']} windy AUC={s['windy']['roc_auc']}")
    print(f"  C−B 命中差（按日期聚类）: {ci}")
    return 0


import csv  # noqa: E402


def gv6_wind() -> dict[str, tuple[float, float]]:
    """2024 源站逐小时风（风速+来向）。

    gv6 的缓存只含风速；传播回测需要风向驱动平流，这里独立取数并缓存。
    Open-Meteo Archive = 历史再分析，仅作回测口径。
    """
    import time

    import requests

    cache = HERE.parent / "results" / f"wind_openmeteo_{YEAR}_dir_{SOURCE['lat']:.4f}.json"
    if cache.exists():
        return {k: (v[0], v[1]) for k, v in
                json.loads(cache.read_text(encoding="utf-8")).items()}
    response = None
    for attempt in range(4):
        try:
            response = requests.get(
                "https://archive-api.open-meteo.com/v1/archive",
                params={"latitude": SOURCE["lat"], "longitude": SOURCE["lon"],
                        "start_date": f"{YEAR}-01-01", "end_date": f"{YEAR}-12-31",
                        "hourly": "wind_speed_10m,wind_direction_10m",
                        "timezone": "Asia/Hong_Kong"},
                timeout=60)
            response.raise_for_status()
            break
        except (requests.exceptions.RequestException, OSError) as exc:
            if attempt == 3:
                raise
            print(f"    风取数失败（{exc}），10 秒后重试 ({attempt + 1}/3)...", flush=True)
            time.sleep(10)
    hourly = response.json()["hourly"]
    mapping = {ts.replace("T", " ") + (":00" if len(ts) == 16 else ""):
               (speed, direction)
               for ts, speed, direction in zip(hourly["time"],
                                               hourly["wind_speed_10m"],
                                               hourly["wind_direction_10m"])}
    cache.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    return mapping


def vector_mean(winds: list[tuple[float, float]]) -> tuple[float, float]:
    """风的矢量平均（来向角 + 速率），与主基准同口径。"""
    xs = [math.sin(math.radians(d)) * v for d, v in winds]
    ys = [math.cos(math.radians(d)) * v for d, v in winds]
    speed = math.hypot(statistics.mean(xs), statistics.mean(ys))
    dir_from = (math.degrees(math.atan2(-statistics.mean(xs), -statistics.mean(ys)))
                + 360.0) % 360.0
    return dir_from, speed


if __name__ == "__main__":
    sys.exit(main())
