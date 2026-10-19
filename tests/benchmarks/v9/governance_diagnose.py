"""V9.4 Experiment: innovation-1 fault diagnosis, gate ablation and selective
classification on the synthetic known-source world.

Answers the V9.4 questions directly:
  1. Stage tracing — for every event, where is the label lost? (feature
     extraction → gate → score thresholds / priority → abstention)
  2. Gate ablation — disable each gate separately (NOx gate, sync weight,
     abstention) and measure per-class recall, to locate the main error source.
  3. Selective classification — coverage-matched comparison: accuracy and
     confident-misjudgement rate at equal coverage, rejection cost.
  4. Multi-seed paired C−B CI (clustered by event date).

Upstream agent results are computed ONCE per event and reused for every
configuration (throttle grid + source_agent rule overrides), so all
comparisons share identical inputs.

Usage: /usr/bin/python3 tests/benchmarks/v9/governance_diagnose.py
"""
from __future__ import annotations

import csv
import json
import random
import sqlite3
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

from common import save_result  # noqa: E402
from metrics import clustered_macro_f1_diff, clustered_paired_diff, macro_f1  # noqa: E402
import governance_synthetic as gs  # noqa: E402

SEEDS = (20260412, 20260413, 20260414)

#: 门控消融配置（其余阈值保持 V8 冻结值）
ABLATIONS = {
    "full": {},
    "no_nox_gate": {"traffic_nox_pct_min": 0.0, "traffic_signal_min": 0.0},
    "no_sync_weight": {"regional_weights": {"sync": 0.0, "high_wind": 0.25,
                                            "wind_stability": 0.15, "weak_local": 0.2}},
    "no_abstention": {"gate_anomaly_min": 0.0},
    "no_abstention_no_nox_gate": {"gate_anomaly_min": 0.0,
                                  "traffic_nox_pct_min": 0.0,
                                  "traffic_signal_min": 0.0},
}


def judge_label(judgement: str) -> str:
    lowered = (judgement or "").lower()
    if "traffic" in lowered:
        return "traffic_dominant"
    if "regional" in lowered:
        return "regional_transport"
    if "accumulation" in lowered or "local" in lowered:
        return "local_accumulation"
    return "uncertain"


def main() -> int:
    import sqlite3 as _sq

    import tools.air_quality_tools as aqt
    import agents.air_quality_agent as _aqa
    from agents.air_quality_agent import AirQualityAgent
    from agents.source_agent import SourceAgent, load_source_rules
    from agents.spatial_agent import SpatialAgent
    from agents.traffic_agent import TrafficAgent

    all_rows = []
    stage_rows = []

    for seed in SEEDS:
        rng = random.Random(seed)
        world = gs.build_world(rng)
        tmp = Path(__import__("tempfile").mkdtemp(prefix=f"diag_{seed}_"))
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

        real_open_db = aqt.open_db
        real_default_db = aqt.DEFAULT_DB
        aqt.open_db = _open_db
        aqt.DEFAULT_DB = target_db    # agent 显式传参 open_db(DEFAULT_DB)
        _aqa.open_db = _open_db
        _aqa.DEFAULT_DB = target_db
        aqt._P90_CACHE_PATH = tmp / "p90.json"

        air_agent = AirQualityAgent(db_path)
        traffic_agent = TrafficAgent()
        spatial_agent = SpatialAgent()
        source_agent = SourceAgent()

        gen_rng = random.Random(seed + 1)
        for entry in world["truth"]:
            station_name = entry["target"] or gs.STATIONS[entry["index"] % 3]["display"]
            task = {"task_id": f"DIAG_{seed}_{entry['date']}_{entry['start_hour']}",
                    "station_name": station_name,
                    "start_time": entry["times"][0], "end_time": entry["times"][-1],
                    "point_in_time": True}
            air = air_agent.run(dict(task))
            meteo = gs.synthetic_meteo_result(gen_rng, entry)
            traffic = gs.synthetic_traffic_result(gen_rng, entry)
            spatial = spatial_agent.run(dict(task), meteo)

            # 同一上游 → 多个门控配置离线复用
            for config_name, override in ABLATIONS.items():
                rules = None
                if override:
                    rules = dict(load_source_rules())
                    for key, value in override.items():
                        if key == "regional_weights":
                            rules[key] = value
                        else:
                            rules[key] = value
                out = source_agent.run({"task_id": task["task_id"],
                                       "station_name": station_name},
                                       air_result=air, meteo_result=meteo,
                                       traffic_result=traffic, spatial_result=spatial,
                                       rules=rules)
                res = out["result"]
                judgement = res.get("main_judgement") or ""
                pred = judge_label(judgement)
                all_rows.append({"seed": seed, "config": config_name,
                                 "date": entry["date"], "label": entry["label"],
                                 "station": station_name, "pred": pred,
                                 "judgement": judgement,
                                 "confidence": out.get("confidence")})

            # 逐阶段追踪（full 配置）
            air_res = air.get("result") or {}
            att = None
            out_full = source_agent.run({"task_id": task["task_id"],
                                        "station_name": station_name},
                                        air_result=air, meteo_result=meteo,
                                        traffic_result=traffic, spatial_result=spatial)
            res = out_full["result"]
            att = res["source_attribution"]
            judgement = res.get("main_judgement") or ""
            pred = judge_label(judgement)
            rules = load_source_rules()
            anomaly = air_res.get("anomaly_score")
            nox_pct = air_res.get("nox_year_percentile")
            sync = (air_res.get("multi_station_sync") or {})
            stage = "judged"
            if pred == "uncertain":
                if judgement == "Insufficient evidence":
                    stage = "gate_lost"
                else:
                    stage = "threshold_lost"
            stage_rows.append({
                "seed": seed, "date": entry["date"], "label": entry["label"],
                "pred": pred, "stage": stage, "judgement": judgement,
                "anomaly": anomaly, "nox_pct": nox_pct,
                "sync_above": sync.get("stations_above_p90"),
                "sync_checked": sync.get("stations_checked"),
                "traffic_score": att.get("traffic_source"),
                "local_score": att.get("local_accumulation"),
                "regional_score": att.get("regional_transport"),
                "traffic_avail": att.get("traffic_available"),
                "local_avail": att.get("local_available"),
                "regional_avail": att.get("regional_available"),
                "gate_min": rules.get("gate_anomaly_min"),
                "traffic_th": rules.get("traffic_threshold"),
                "local_th": rules.get("local_threshold"),
                "regional_th": rules.get("regional_threshold"),
            })

        aqt.open_db = real_open_db
        aqt.DEFAULT_DB = real_default_db
        _aqa.open_db = real_open_db
        _aqa.DEFAULT_DB = real_default_db

    # ── 汇总：消融 ─────────────────────────────────────────────────────────
    truths_by_seed = {}
    for r in all_rows:
        truths_by_seed.setdefault((r["seed"], r["date"], r["label"]), r["label"])

    ablation_summary = {}
    for config_name in ABLATIONS:
        rows = [r for r in all_rows if r["config"] == config_name]
        truths = [r["label"] for r in rows]
        preds = [r["pred"] for r in rows]
        per_class = {lab: round(sum(1 for r in rows if r["label"] == lab
                                    and r["pred"] == lab)
                                / max(1, sum(1 for r in rows if r["label"] == lab)), 4)
                     for lab in sorted(set(truths))}
        ablation_summary[config_name] = {
            "macro_f1": round(macro_f1(truths, preds), 4),
            "per_class_recall": per_class,
            "abstention_rate": round(sum(1 for p in preds if p == "uncertain")
                                     / len(preds), 4),
            "n": len(rows)}

    # C−B 配对 CI：直接引用 governance_synthetic 的公平版结果（V9.5）。
    # 原实现在此重复复算 B，且 B 的特征直接读真值标签（traffic_score=0.75 if
    # label==...，wind=0.8 if label in ...）——标签泄漏，V9.3/V9.4 的"B≫C"
    # 因此虚高。修复后 B 只用可观测量，权威数字以 governance_synthetic 为准，
    # 这里只做引用，不再二次实现以免再次引入口径/泄漏分叉。
    cb_ci = {}
    cb_ci_hit_rate_legacy = None
    synthetic_path = BASE / "tests" / "benchmarks" / "results" / "v9_governance_synthetic.json"
    if synthetic_path.exists():
        synthetic = json.loads(synthetic_path.read_text(encoding="utf-8"))
        cb_ci = synthetic.get("C_minus_B_paired_ci_pooled") or {}
        cb_ci["source"] = "v9_governance_synthetic.json (fair B, no label leak)"

    # ── 选择性分类（覆盖率—正确率曲线，full 配置 by 门控阈值滑窗） ──
    # 用事件级 anomaly 作为拒判分数：anomaly 越高越有把握
    stage_by_event = {}
    for r in stage_rows:
        stage_by_event[(r["seed"], r["date"], r["label"])] = r
    curve = []
    for percent in (0, 10, 20, 30, 40, 50):
        kept = []
        for key, r in stage_by_event.items():
            if r["anomaly"] is None:
                continue
            kept.append(r)
        if percent:
            thr = sorted(r["anomaly"] for r in kept)[int(percent / 100 * len(kept))]
            kept = [r for r in kept if r["anomaly"] > thr]
        covered = [r for r in kept if r["pred"] != "uncertain"]
        correct = sum(1 for r in covered if r["pred"] == r["label"])
        curve.append({"reject_percent": percent,
                      "coverage": round(len(covered) / max(1, len(kept)), 4),
                      "accuracy_on_covered": round(correct / max(1, len(covered)), 4),
                      "confident_wrong_rate": round(
                          1 - correct / max(1, len(covered)), 4)})

    # ── 导出 ────────────────────────────────────────────────────────────────
    with (HERE / "results" / "v94_stage_trace.csv").open("w", newline="",
                                                         encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(stage_rows[0].keys()))
        writer.writeheader()
        writer.writerows(stage_rows)
    with (HERE / "results" / "v94_ablation_cases.csv").open("w", newline="",
                                                             encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(all_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_rows)

    stage_dist = {}
    for r in stage_rows:
        stage_dist.setdefault(r["label"], {})
        stage_dist[r["label"]][r["stage"]] = stage_dist[r["label"]].get(r["stage"], 0) + 1

    payload = {
        "design": {
            "note": "V9.4 创新一诊断：同一上游多门控配置离线复用；逐阶段追踪定位"
                    "标签损失环节；选择性分类以 anomaly 为拒判分数；多种子配对 CI",
            "seeds": list(SEEDS), "ablations": list(ABLATIONS),
        },
        "ablation_summary": ablation_summary,
        "stage_distribution": stage_dist,
        "selective_classification": curve,
        "C_minus_B_paired_ci": cb_ci,
        "C_minus_B_paired_ci_hit_rate_legacy": cb_ci_hit_rate_legacy,
        "stage_rows_n": len(stage_rows),
    }
    path = save_result("v94_governance_diagnose", payload)
    print(f"\nsaved -> {path}")
    print("=== 门控消融（3 种子合并）===")
    for name, s in ablation_summary.items():
        print(f"  {name:<26} F1={s['macro_f1']} 弃判={s['abstention_rate']} "
              f"召回={s['per_class_recall']}")
    print("\n逐阶段损失分布（按真值类别）:")
    for lab, dist in stage_dist.items():
        print(f"  {lab:<22} {dist}")
    print("\n选择性分类:", curve)
    print("C−B 配对 CI:", cb_ci)
    return 0


if __name__ == "__main__":
    sys.exit(main())
