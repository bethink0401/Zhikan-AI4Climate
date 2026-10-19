# -*- coding: utf-8 -*-
"""V9.5 Experiment (innovation-1): evidence-signature rule for source judgement.

Fixes the two structural defects found by the V9.4 diagnosis:
  * the priority chain let a passing traffic gate *override* local/regional
    evidence (traffic recall up but local recall collapsed to 0.06);
  * every threshold fell through to "uncertain", so the *scores* barely
    mattered and systematic errors lived in the threshold/priority stage.

New rule (``score_argmax``): each mechanism has a **signature score** built the
same way (positive evidence only, normalised to [0,1], no station-constant
term), the judgement is the argmax, and it is only issued when
``max >= min_strength`` and ``max - second >= dominance_margin``; otherwise the
event is reported as "insufficient evidence". The dominance margin replaces the
anomaly score as the uncertainty signal (V9.4 showed anomaly is uncorrelated
with being right).

Pre-registered split (protocol.json: v9_5_preregistration):
  - dev (calibration) synthetic seeds: 20260412, 20260413, 20260414
  - final (unseen)   synthetic seeds: 20260601, 20260602, 20260603 (V9.6)
    (20260501-03 were consumed during V9.6 development and are no longer blind)
  - the final seeds are NOT touched until the rule parameters are frozen

Everything is automatic; no human labels anywhere. All four required metrics are
reported per rule: Macro-F1, per-class recall, abstention rate, and the
error rate among confidently-issued judgements (1 - accuracy on covered).

Usage: /usr/bin/python3 tests/benchmarks/v9/governance_rule_v95.py
"""
from __future__ import annotations

import itertools
import json
import random
import sqlite3 as _sq
import statistics
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from common import save_result  # noqa: E402
from metrics import macro_f1  # noqa: E402
import governance_synthetic as gs  # noqa: E402

DEV_SEEDS = (20260412, 20260413, 20260414)
FINAL_SEEDS = (20260601, 20260602, 20260603)

SIGNATURE_TEXT = {"traffic": "traffic_dominant",
                  "local": "local_accumulation",
                  "regional": "regional_transport"}


def _max_upwind_alignment(spatial_result: dict) -> float | None:
    """与 SourceAgent 同口径：上风向道路的最大对齐度。"""
    result = spatial_result.get("result") or {}
    roads = result.get("upwind_roads") or []
    if not roads:
        return None
    return max(float(road.get("upwind_alignment") or 0.0) for road in roads)


def capture(seed: int) -> list[dict]:
    """跑一次完整管线，记录每个事件的组件得分与真值（不在此处判定）。"""
    import tools.air_quality_tools as aqt
    import agents.air_quality_agent as _aqa
    from agents.air_quality_agent import AirQualityAgent
    from agents.source_agent import SourceAgent
    from agents.spatial_agent import SpatialAgent
    from agents.traffic_agent import TrafficAgent

    rng = random.Random(seed)
    world = gs.build_world(rng)
    tmp = Path(tempfile.mkdtemp(prefix=f"v95rule_{seed}_"))
    db_path = tmp / "synthetic_air.db"
    gs.build_db(world, db_path)

    target_db = Path(db_path).resolve()

    def _open_db(_path=target_db):
        # V9.6 数据契约：任何一次连接指向别的库都立即失败。
        resolved = Path(_path).resolve()
        if resolved != target_db:
            raise RuntimeError(
                f"数据契约被破坏：应连 {target_db}，实际连 {resolved}")
        conn = _sq.connect(str(resolved))
        conn.row_factory = _sq.Row
        return conn

    real_open_db, real_default_db = aqt.open_db, aqt.DEFAULT_DB
    aqt.open_db = _open_db
    aqt.DEFAULT_DB = target_db
    aqt._P90_CACHE_PATH = tmp / "p90.json"
    _aqa.open_db = _open_db
    _aqa.DEFAULT_DB = target_db

    air_agent, traffic_agent = AirQualityAgent(db_path), TrafficAgent()
    spatial_agent, source_agent = SpatialAgent(), SourceAgent()
    gen_rng = random.Random(seed + 1)
    rows = []
    for entry in world["truth"]:
        station_name = entry["target"] or gs.STATIONS[entry["index"] % 3]["display"]
        task = {"task_id": f"V95R_{seed}_{entry['date']}_{entry['start_hour']}",
                "station_name": station_name,
                "start_time": entry["times"][0], "end_time": entry["times"][-1],
                "point_in_time": True}
        air = air_agent.run(dict(task))
        meteo = gs.synthetic_meteo_result(gen_rng, entry)
        traffic = gs.synthetic_traffic_result(gen_rng, entry)
        spatial = spatial_agent.run(dict(task), meteo)
        out = source_agent.run({"task_id": task["task_id"], "station_name": station_name},
                               air_result=air, meteo_result=meteo,
                               traffic_result=traffic, spatial_result=spatial)
        res = out["result"]
        attribution = res["source_attribution"]
        air_res = air.get("result") or {}
        meteo_res = meteo.get("result") or {}
        # 规则 B 需要的可观测量（PIT 窗 NO2 分位）：与 governance_synthetic 同口径
        import datetime as _dt
        db_name = station_name.replace(" Roadside", "").upper()
        series = world["series"][db_name]
        t0 = _dt.datetime.fromisoformat(entry["times"][0])
        pit_hi = t0.replace(day=1, hour=0, minute=0)
        pit_lo = pit_hi - _dt.timedelta(days=365)
        window_vals = [v for t, v in series.items()
                       if pit_hi > t >= pit_lo]
        window_3h = [v for t, v in series.items()
                     if t0 <= t < t0 + _dt.timedelta(hours=3)]
        mean_no2 = statistics.mean(v[0] for v in window_3h) if window_3h else None
        no2_pct = (statistics.mean(1.0 if v[0] < mean_no2 else 0.0 for v in window_vals)
                   if window_vals and mean_no2 is not None else None)
        rows.append({
            "seed": seed, "date": entry["date"], "start_hour": entry["start_hour"],
            "label": entry["label"],
            "anomaly": air_res.get("anomaly_score"),
            "signature": attribution.get("signature_scores") or {},
            "signature_available": attribution.get("signature_available") or {},
            "legacy": {"traffic": attribution.get("traffic_source"),
                       "local": attribution.get("local_accumulation"),
                       "regional": attribution.get("regional_transport")},
            "legacy_available": {"traffic": attribution.get("traffic_available"),
                                 "local": attribution.get("local_available"),
                                 "regional": attribution.get("regional_available")},
            "sync_above": (air_res.get("multi_station_sync") or {}).get("stations_above_p90"),
            "sync_checked": (air_res.get("multi_station_sync") or {}).get("stations_checked"),
            "nox_pct": air_res.get("nox_year_percentile"),
            "no2_signal": air_res.get("traffic_pollutant_signal_score"),
            "no2_pct": no2_pct,
            "wind_speed": meteo_res.get("mean_wind_speed"),
            "traffic_pressure": (traffic.get("result") or {}).get("traffic_pressure_score"),
            # V9.6：原始组件（离线扫描判定规则时重算签名用，全部为可观测量）
            "in_pit": True,
            "station": station_name,
            "static_wind": meteo_res.get("static_wind_score"),
            "wind_shift": meteo_res.get("wind_shift_score"),
            "mean_wind": meteo_res.get("mean_wind_speed"),
            "upwind_score": _max_upwind_alignment(spatial),
            "contrast_mean_ratio": (air_res.get("spatial_contrast") or {}).get("mean_ratio"),
            "contrast_cv": (air_res.get("spatial_contrast") or {}).get("cv"),
            "contrast_station_ratio": (air_res.get("spatial_contrast") or {}).get("station_ratio"),
        })

    aqt.open_db, aqt.DEFAULT_DB = real_open_db, real_default_db
    _aqa.open_db, _aqa.DEFAULT_DB = real_open_db, real_default_db
    return rows


def predict_argmax(row: dict, min_strength: float, margin: float,
                   traffic_nox_min: float = 0.8,
                   gate_min: float = 0.45) -> str:
    anomaly = row.get("anomaly")
    if anomaly is None or anomaly < gate_min:
        return "uncertain"
    # 交通可用性：NOx 年内分位门控 OR 旧日内外形信号分（阈值 0.8）。
    # 离线重算以便把门控阈值一并纳入开发集标定。
    nox_pct = row.get("nox_pct")
    traffic_ok = nox_pct is not None and nox_pct >= traffic_nox_min
    available = dict(row.get("signature_available") or {})
    available["traffic"] = traffic_ok
    scores = {k: float(v) for k, v in (row.get("signature") or {}).items()
              if available.get(k)}
    if not scores:
        return "uncertain"
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    top_name, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top >= min_strength and (top - second) >= margin:
        return SIGNATURE_TEXT[top_name]
    return "uncertain"


def predict_legacy(row: dict, traffic_th: float, local_th: float,
                   regional_th: float, gate_min: float = 0.45) -> str:
    if row.get("anomaly") is None or row["anomaly"] < gate_min:
        return "uncertain"
    legacy = row.get("legacy") or {}
    allowed = row.get("legacy_available") or {}
    traffic = float(legacy.get("traffic") or 0.0)
    local = float(legacy.get("local") or 0.0)
    regional = float(legacy.get("regional") or 0.0)
    if allowed.get("regional") and regional >= regional_th:
        return "regional_transport"
    if allowed.get("traffic") and traffic >= traffic_th:
        return "traffic_dominant"
    if allowed.get("local") and local >= local_th:
        return "local_accumulation"
    return "uncertain"


def predict_b(row: dict, threshold: float = 0.55) -> str:
    """公平版规则 B：只用可观测量（交通信号 + 静风 + 同步），不读标签。

    与 governance_synthetic 的 B 同构：区域通道用与 C 同一个可观测同步证据，
    交通/积聚通道用交通压力、NOx/NO2 年内分位与风速；不再读真值标签。
    """
    nox_pct = row.get("nox_pct")
    no2_pct = row.get("no2_pct")
    pressure = row.get("traffic_pressure")
    wind = row.get("wind_speed")
    if None in (nox_pct, no2_pct, pressure, wind):
        return "uncertain"
    sync_norm = max(0.0, min(1.0, float(row.get("sync_above") or 0.0) / 8.0))
    scores = {
        "traffic_dominant": 0.6 * nox_pct + 0.4 * pressure,
        "local_accumulation": 0.6 * max(0.0, min(1.0, (2.5 - wind) / 2.5))
                              + 0.4 * no2_pct,
        "regional_transport": 0.7 * sync_norm + 0.3 * min(1.0, wind / 5.0),
    }
    best = max(scores, key=scores.get)
    return best if scores[best] >= threshold else "uncertain"


def evaluate(rows: list[dict], predictor, *args) -> dict:
    truths = [r["label"] for r in rows]
    preds = [predictor(r, *args) for r in rows]
    covered = [(t, p) for t, p in zip(truths, preds) if p != "uncertain"]
    correct = sum(1 for t, p in covered if t == p)
    recall = {}
    for lab in sorted(set(truths)):
        subset = [p for t, p in zip(truths, preds) if t == lab]
        recall[lab] = round(sum(1 for p in subset if p == lab) / max(1, len(subset)), 4)
    return {"macro_f1": round(macro_f1(truths, preds), 4),
            "per_class_recall": recall,
            "abstention_rate": round(sum(1 for p in preds if p == "uncertain")
                                     / len(preds), 4),
            "coverage": round(len(covered) / len(preds), 4),
            "error_on_covered": round(1 - correct / max(1, len(covered)), 4),
            "n": len(preds)}


def main() -> int:
    print("捕获开发集种子 ...", flush=True)
    dev_rows = []
    for seed in DEV_SEEDS:
        rows = capture(seed)
        dev_rows.extend(rows)
        print(f"  seed {seed}: {len(rows)} 事件", flush=True)

    # ── 开发集标定：min_strength × dominance_margin（不碰最终种子）──
    grid_min = [0.35, 0.40, 0.45, 0.50, 0.55, 0.60]
    grid_margin = [0.0, 0.03, 0.05, 0.08, 0.12, 0.16, 0.20]
    grid_nox = [0.55, 0.65, 0.75, 0.85]
    search = []
    for ms, mg, nx in itertools.product(grid_min, grid_margin, grid_nox):
        metrics = evaluate(dev_rows, predict_argmax, ms, mg, nx)
        search.append({"min_strength": ms, "dominance_margin": mg,
                       "traffic_nox_pct_min": nx,
                       "dev_macro_f1": metrics["macro_f1"],
                       "dev_coverage": metrics["coverage"],
                       "dev_error_on_covered": metrics["error_on_covered"],
                       "dev_abstention": metrics["abstention_rate"]})
    # 目标：dev Macro-F1 最优；并列时覆盖率更高者优先（少弃判）
    best = max(search, key=lambda r: (r["dev_macro_f1"], r["dev_coverage"]))
    min_strength, margin = best["min_strength"], best["dominance_margin"]
    nox_min = best["traffic_nox_pct_min"]
    print(f"开发集标定冻结：min_strength={min_strength} margin={margin} "
          f"traffic_nox_pct_min={nox_min} "
          f"(dev F1={best['dev_macro_f1']}, 覆盖率={best['dev_coverage']})", flush=True)

    # ── 冻结参数写入 config（供系统默认规则使用）──
    rules_path = BASE / "config" / "pollution_rules.json"
    config = json.loads(rules_path.read_text(encoding="utf-8"))
    config["source_judgement"]["min_strength"] = min_strength
    config["source_judgement"]["dominance_margin"] = margin
    config["source_judgement"]["traffic_nox_pct_min"] = nox_min
    config["source_judgement"]["_v95_calibrated"] = (
        f"2026-10-14 开发集(种子 {list(DEV_SEEDS)})网格搜索冻结；最终种子 "
        f"{list(FINAL_SEEDS)} 未参与")
    rules_path.write_text(json.dumps(config, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    # ── 最终（未见）种子评估：同一批事件、多规则同场对比 ──
    print("捕获最终（未见）种子 ...", flush=True)
    final_rows = []
    for seed in FINAL_SEEDS:
        rows = capture(seed)
        final_rows.extend(rows)
        print(f"  seed {seed}: {len(rows)} 事件", flush=True)

    # V9.6：把捕获结果落盘，后续规则变体全部离线扫描（避免重复重跑链路）
    import csv as _csv
    for tag, data in (("dev", dev_rows), ("blind", final_rows)):
        dest = HERE / "results" / f"v96_synthetic_{tag}_components.csv"
        flat = []
        for row in data:
            item = {}
            for key, value in row.items():
                if isinstance(value, dict):
                    for sub, sub_value in value.items():
                        item[f"{key}_{sub}"] = sub_value
                else:
                    item[key] = value
            flat.append(item)
        keys = sorted({k for item in flat for k in item})
        with dest.open("w", newline="", encoding="utf-8") as handle:
            writer = _csv.DictWriter(handle, fieldnames=keys)
            writer.writeheader()
            writer.writerows(flat)
        print(f"  captured -> {dest.name}（{len(flat)} 行）", flush=True)

    results = {
        "C_v95_signature_argmax": evaluate(final_rows, predict_argmax, min_strength,
                                           margin, nox_min),
        "C_v95_on_dev": evaluate(dev_rows, predict_argmax, min_strength, margin, nox_min),
        "C_legacy_chain": evaluate(final_rows, predict_legacy, 0.45, 0.45, 0.65),
        "B_fair_no_leak": evaluate(final_rows, predict_b, 0.55),
        "A_single_source_stub": evaluate(
            final_rows, lambda r: ("traffic_dominant" if (r.get("nox_pct") or 0) >= 0.8
                                   else "uncertain")),
    }

    payload = {
        "design": {
            "note": "V9.5 创新一：证据签名 argmax + 支配边际，替换优先级判定链；"
                    "开发集标定（种子 %s），最终种子 %s 未见；规则 B 修复标签泄漏后"
                    "只用可观测量。四指标并报：Macro-F1 / 分类召回 / 弃判率 / "
                    "被覆盖样本错误率。" % (list(DEV_SEEDS), list(FINAL_SEEDS)),
            "dev_seeds": list(DEV_SEEDS),
            "final_seeds_unseen": list(FINAL_SEEDS),
            "frozen": {"min_strength": min_strength, "dominance_margin": margin,
                       "traffic_nox_pct_min": nox_min},
            "leak_fix": "规则 B 原用 entry['label'] 构造同步特征（泄漏）；现改用与 "
                        "C 相同的可观测同步证据",
        },
        "dev_grid_search": search,
        "results_final_seeds": results,
        "label_distribution_final": dict(Counter(r["label"] for r in final_rows)),
    }
    path = save_result("v95_governance_rule", payload)
    print(f"\nsaved -> {path}")
    print(f"=== 最终（未见）种子 {list(FINAL_SEEDS)}，{len(final_rows)} 事件 ===")
    for name, m in results.items():
        if not name.endswith("on_dev"):
            print(f"  {name:<26} F1={m['macro_f1']} 弃判={m['abstention_rate']} "
                  f"覆盖错误率={m['error_on_covered']}")
            print(f"      召回={m['per_class_recall']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
