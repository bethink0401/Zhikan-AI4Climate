"""V9.6：判定规则离线扫描（跨数据域可迁移性检验）。

为什么不直接改代码重跑：一次完整链路 ~13 分钟，规则变体多、盲试代价高。
本脚本读取 governance_capture.py / governance_rule_v95.py 落盘的**原始组件**，
用显式权重重算三类签名与判定，因此：

* 规则改动完全可复现、可审计（没有隐藏状态）；
* 同一批观测量可同时评估"合成世界"与"真实世界"，直接检验**跨域可迁移性**。

三个区域判据（本轮的核心问题）：
  R1 sync        : 沿用 V8/V9.4 的多站同步（超 p90 站数）——真实数据上唯一
                   被验证过有效的区域证据；
  R2 abs_contrast: V9.5 引入的**绝对**对比度映射 (mean_ratio-1)/0.5——合成世界
                   判别力强，但两域 mean_ratio 量级不同（合成 ≈1.6、真实 ≈1.0），
                   阈值不可迁移；
  R3 net_relative: V9.6 提出的**尺度无关**形式——本站抬升比 / 网络平均抬升比。
                   区域事件"本站与全网同涨"→ 比值≈1；交通/局地"本站单独涨"→ 比值>1。
                   因为只用到比值，天然不受两域绝对水平差异影响。

用法：
    /usr/bin/python3 tests/benchmarks/v9/governance_sweep.py
"""

from __future__ import annotations

import csv
import itertools
import json
import math
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
for path in (str(BASE), str(HERE), str(HERE.parent)):
    if path not in sys.path:
        sys.path.insert(0, path)

from contracts import require_stats_consistent  # noqa: E402
from metrics import clustered_macro_f1_diff, macro_f1  # noqa: E402

LABELS = ("traffic_dominant", "local_accumulation",
          "regional_transport", "uncertain")


# ── 数据装载 ────────────────────────────────────────────────────────────────

def _num(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flag(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "yes")


def _real_paths() -> list[Path]:
    """真实窗组件文件：优先用合并文件，否则合并三份按站捕获的结果。"""
    combined = HERE / "results" / "v96_real_components.csv"
    if combined.exists():
        return [combined]
    parts = sorted((HERE / "results").glob("v96_real_components_*Roadside.csv"))
    if not parts:
        raise FileNotFoundError("未找到真实窗组件文件（先跑 governance_capture.py）")
    return parts


def load_real(mode: str) -> list[dict]:
    """真实 2024 留出窗；mode ∈ {full, pit}。"""
    rows = []
    raw_rows = []
    for path in _real_paths():
        raw_rows.extend(csv.DictReader(path.open(encoding="utf-8")))
    for raw in raw_rows:
        rows.append({
            "domain": "real", "mode": mode, "station": raw["station"],
            "date": raw["date"], "start_hour": int(raw["start_hour"]),
            "label": raw["label"],
            "anomaly": _num(raw[f"{mode}_anomaly"]),
            "nox_pct": _num(raw[f"{mode}_nox_pct"]),
            "no2_signal": _num(raw[f"{mode}_no2_signal"]),
            "sync_above": _num(raw[f"{mode}_sync_above"]),
            "sync_checked": _num(raw[f"{mode}_sync_checked"]),
            "static_wind": _num(raw[f"{mode}_static_wind"]),
            "wind_shift": _num(raw[f"{mode}_wind_shift"]),
            "mean_wind": _num(raw[f"{mode}_mean_wind"]),
            "traffic_pressure": _num(raw[f"{mode}_traffic_pressure"]),
            "traffic_status": raw.get(f"{mode}_traffic_status"),
            "upwind_score": _num(raw[f"{mode}_upwind_score"]),
            "contrast_mean_ratio": _num(raw[f"{mode}_contrast_mean_ratio"]),
            "contrast_cv": _num(raw[f"{mode}_contrast_cv"]),
            "contrast_station_ratio": _num(raw[f"{mode}_contrast_station_ratio"]),
        })
    return rows


def load_synthetic(tag: str) -> list[dict]:
    path = HERE / "results" / f"v96_synthetic_{tag}_components.csv"
    rows = []
    for raw in csv.DictReader(path.open(encoding="utf-8")):
        rows.append({
            "domain": "synthetic", "mode": "pit", "station": raw.get("station") or "",
            "date": raw["date"], "start_hour": int(raw["start_hour"]),
            "label": raw["label"],
            "anomaly": _num(raw["anomaly"]),
            "nox_pct": _num(raw["nox_pct"]),
            "no2_signal": _num(raw["no2_signal"]),
            "sync_above": _num(raw["sync_above"]),
            "sync_checked": _num(raw["sync_checked"]),
            "static_wind": _num(raw["static_wind"]),
            "wind_shift": _num(raw["wind_shift"]),
            "mean_wind": _num(raw["mean_wind"]),
            "traffic_pressure": _num(raw["traffic_pressure"]),
            # 合成世界由 synthetic_traffic_result 提供，状态恒为 success
            "traffic_status": "success",
            "upwind_score": _num(raw["upwind_score"]),
            "contrast_mean_ratio": _num(raw["contrast_mean_ratio"]),
            "contrast_cv": _num(raw["contrast_cv"]),
            "contrast_station_ratio": _num(raw["contrast_station_ratio"]),
        })
    return rows


# ── 规则族 ──────────────────────────────────────────────────────────────────

RULE_VARIANTS = {
    # V8/V9.4 形式：多站同步为主项
    "R1_sync": {"sync": 0.6, "high_wind": 0.25, "wind_stability": 0.15},
    # V9.5 形式：绝对对比度为主项（合成强、真实不可迁移）
    "R2_abs_contrast": {"elevation": 0.5, "uniformity": 0.3, "sync": 0.1,
                        "high_wind": 0.05, "wind_stability": 0.05},
    # V9.6：尺度无关的网络相对抬升
    "R3_net_relative": {"tracking": 0.7, "sync": 0.2, "high_wind": 0.1},
    # V9.6：混合（同步 + 网络相对）
    "R4_hybrid": {"sync": 0.4, "tracking": 0.4, "high_wind": 0.2},
}

LOCAL_STATION_TERM = {"none": 0.0, "abs": 0.35, "net_relative": 0.35}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _sync_norm(row: dict) -> float:
    return _clamp(float(row["sync_above"] or 0.0) / 8.0)


def _high_wind(row: dict) -> float:
    return _clamp(float(row["mean_wind"] or 0.0) / 5.0)


def _wind_stability(row: dict) -> float:
    shift = row["wind_shift"]
    return _clamp(1.0 - float(shift)) if shift is not None else 0.0


def _net_relative(row: dict) -> float | None:
    """本站抬升比 / 网络平均抬升比（尺度无关）。"""
    station = row["contrast_station_ratio"]
    network = row["contrast_mean_ratio"]
    if station is None or network in (None, 0):
        return None
    return float(station) / float(network)


def _tracking(row: dict) -> float:
    """本站与全网同步的程度 ∈ [0,1]：≈1 表示同涨（区域），偏离则低。"""
    ratio = _net_relative(row)
    if ratio is None:
        return 0.0
    return _clamp(1.0 - abs(ratio - 1.0) / 0.30)


def _excess(row: dict) -> float:
    """本站相对全网的**超额**抬升 ∈ [0,1]：局地/交通的特征。"""
    ratio = _net_relative(row)
    if ratio is None:
        return 0.0
    return _clamp((ratio - 1.0) / 0.40)


def signatures(row: dict, cfg: dict) -> tuple[dict, dict]:
    """用配置重算三类签名与可用性（与 SourceAgent 同构）。"""
    nox_min = cfg["traffic_nox_pct_min"]
    nox_pct = row["nox_pct"]
    # 与 SourceAgent._extract_traffic_scores 一致：TrafficAgent 未成功时
    # traffic_score 视为缺失（交通不可用）——离线此前只看数值、漏了这一条。
    traffic_present = row.get("traffic_status") == "success"
    traffic_ok = (traffic_present and row["traffic_pressure"] is not None
                  and nox_pct is not None and nox_pct >= nox_min)
    if cfg.get("traffic_gate_uses_no2_signal"):
        traffic_ok = traffic_ok or (row["no2_signal"] is not None
                                    and row["no2_signal"] >= 0.8)
    sig_traffic = (_clamp(0.6 * float(row["traffic_pressure"] or 0.0)
                          + 0.4 * float(row["upwind_score"] or 0.0))
                   if traffic_ok else 0.0)

    static_wind = row["static_wind"]
    local_ok = static_wind is not None
    own_term = LOCAL_STATION_TERM[cfg.get("local_station_term", "none")]
    if own_term and cfg["local_station_term"] == "abs":
        station_ratio = row["contrast_station_ratio"]
        own_value = (_clamp((float(station_ratio) - 1.0) / 0.5)
                     if station_ratio is not None else 0.0)
    elif own_term:
        own_value = _excess(row)
    else:
        own_value = 0.0
    sig_local = (0.6 * float(static_wind or 0.0)
                 + 0.4 * float(row["anomaly"] or 0.0)
                 + own_term * own_value) if local_ok else 0.0
    sig_local = _clamp(sig_local)

    regional_ok = row["mean_wind"] is not None
    variant = cfg["regional_variant"]
    weights = RULE_VARIANTS[variant]
    if variant == "R2_abs_contrast":
        mean_ratio = row["contrast_mean_ratio"]
        cv = row["contrast_cv"]
        features = {
            "elevation": (_clamp((float(mean_ratio) - 1.0) / 0.5)
                          if mean_ratio is not None else 0.0),
            "uniformity": (_clamp(1.0 - float(cv) / 0.25)
                           if cv is not None else 0.0),
        }
    else:
        features = {}
    features.update({"sync": _sync_norm(row), "high_wind": _high_wind(row),
                     "wind_stability": _wind_stability(row),
                     "tracking": _tracking(row)})
    sig_regional = sum(float(weight) * features.get(name, 0.0)
                       for name, weight in weights.items())
    sig_regional = _clamp(sig_regional) if regional_ok else 0.0

    # SourceAgent 输出的 signature_scores 是 round(x, 4)，判定用的就是该值；
    # 离线必须同样舍入，否则 0.8702−0.8202 这类"刀锋样本"会跨在阈值两侧。
    scores = {"traffic": round(sig_traffic, 4), "local": round(sig_local, 4),
              "regional": round(sig_regional, 4)}
    available = {"traffic": traffic_ok, "local": local_ok, "regional": regional_ok}
    return scores, available


def predict(row: dict, cfg: dict) -> str:
    # 与 SourceAgent._generate_judgement 完全一致：先过异常分门控
    # （污染未达门控一律"证据不足"）。此前离线扫描漏掉这一步，导致离线
    # 比在线更"敢判"，两端口径不一致（V9.7 审计发现）。
    anomaly = row["anomaly"]
    if anomaly is None or anomaly < cfg.get("gate_anomaly_min", 0.45):
        return "uncertain"
    scores, available = signatures(row, cfg)
    live = {name: value for name, value in scores.items() if available[name]}
    if not live:
        return "uncertain"
    # 与在线一致：并列时按类名字典序决胜（local < regional < traffic）
    ranked = sorted(live.items(), key=lambda kv: (-kv[1], kv[0]))
    top_name, top_value = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_value < cfg["min_strength"] or (top_value - second) < cfg["margin"]:
        return "uncertain"
    return {"traffic": "traffic_dominant", "local": "local_accumulation",
            "regional": "regional_transport"}[top_name]


def evaluate(rows: list[dict], cfg: dict) -> dict:
    truths = [r["label"] for r in rows]
    preds = [predict(r, cfg) for r in rows]
    covered = [(t, p) for t, p in zip(truths, preds) if p != "uncertain"]
    recall = {}
    for label in LABELS:
        pool = [t for t in truths if t == label]
        recall[label] = (round(sum(1 for t, p in zip(truths, preds)
                                   if t == label and p == label) / len(pool), 4)
                         if pool else None)
    stations = sorted({r.get("station") or "" for r in rows})
    by_station = {}
    for station in stations:
        subset = [i for i, r in enumerate(rows) if (r.get("station") or "") == station]
        if not subset:
            continue
        by_station[station] = round(macro_f1([truths[i] for i in subset],
                                             [preds[i] for i in subset]), 4)
    return {
        "n": len(rows),
        "macro_f1": round(macro_f1(truths, preds), 4),
        "by_station": by_station,
        "per_class_recall": recall,
        "abstention_rate": round(sum(1 for p in preds if p == "uncertain") / len(preds), 4),
        "coverage": round(len(covered) / len(preds), 4),
        "error_on_covered": (round(sum(1 for t, p in covered if t != p) / len(covered), 4)
                             if covered else None),
        "pred_distribution": {label: preds.count(label) for label in LABELS},
    }


# ── 主流程 ─────────────────────────────────────────────────────────────────

def main() -> int:
    real_full = load_real("full")
    real_pit = load_real("pit")
    syn_dev = load_synthetic("dev")
    syn_blind = load_synthetic("blind")
    print(f"真实窗 {len(real_full)}（full/pit 各一份）| "
          f"合成开发 {len(syn_dev)} | 合成盲测 {len(syn_blind)}", flush=True)

    # ① 三个区域判据的跨域对照：同一批观测量、只换区域判据
    print("\n=== ① 区域判据跨域对照（其余参数固定 V9.5 冻结值）===")
    base = {"traffic_nox_pct_min": 0.55, "min_strength": 0.40, "margin": 0.12,
            "local_station_term": "none", "traffic_gate_uses_no2_signal": False,
            "gate_anomaly_min": 0.45}
    cross = {}
    for variant in RULE_VARIANTS:
        cfg = {**base, "regional_variant": variant}
        cross[variant] = {
            "synthetic_dev": evaluate(syn_dev, cfg),
            "synthetic_blind": evaluate(syn_blind, cfg),
            "real_full": evaluate(real_full, cfg),
            "real_pit": evaluate(real_pit, cfg),
        }
        print(f"\n  {variant}")
        for name, metrics in cross[variant].items():
            print(f"    {name:<16} F1={metrics['macro_f1']} "
                  f"区域召回={metrics['per_class_recall']['regional_transport']} "
                  f"预测分布={metrics['pred_distribution']}")

    # ② 在合成开发集上标定（区域判据 + 阈值 + 局地项），再跨域报告
    print("\n=== ② 开发集标定（合成 dev）→ 跨域报告 ===")
    grid = []
    for variant, local_term, ms, mg in itertools.product(
            RULE_VARIANTS, ("none", "net_relative"),
            (0.30, 0.35, 0.40, 0.45, 0.50), (0.03, 0.05, 0.08, 0.12, 0.16)):
        cfg = {**base, "regional_variant": variant, "local_station_term": local_term,
               "min_strength": ms, "margin": mg}
        metrics = evaluate(syn_dev, cfg)
        grid.append({"cfg": cfg, "dev": metrics})
    best = max(grid, key=lambda item: (item["dev"]["macro_f1"], item["dev"]["coverage"]))
    cfg = best["cfg"]
    print(f"  冻结: {json.dumps(cfg, ensure_ascii=False)}")
    print(f"  dev F1={best['dev']['macro_f1']}")
    report = {}
    for name, rows in (("synthetic_dev", syn_dev), ("synthetic_blind", syn_blind),
                       ("real_full", real_full), ("real_pit", real_pit)):
        report[name] = evaluate(rows, cfg)
        print(f"  {name:<16} F1={report[name]['macro_f1']} "
              f"召回={report[name]['per_class_recall']} "
              f"弃判={report[name]['abstention_rate']}")

    # 配对 CI（按日期聚类）
    for name, rows in (("real_full", real_full), ("real_pit", real_pit),
                       ("synthetic_blind", syn_blind)):
        truths = [r["label"] for r in rows]
        preds = [predict(r, cfg) for r in rows]
        baseline_variant = dict(cfg, regional_variant="R1_sync")
        base_preds = [predict(r, baseline_variant) for r in rows]
        ci = clustered_macro_f1_diff(truths, preds, base_preds,
                                     [r["date"] for r in rows], reps=10000)
        # 明确记录两侧配置与配对样本，CI 不再可能被误读
        ci["arm_config"] = dict(cfg)
        ci["baseline_config"] = dict(baseline_variant)
        ci["arm_point_f1"] = round(macro_f1(truths, preds), 4)
        ci["baseline_point_f1"] = round(macro_f1(truths, base_preds), 4)
        ci["baseline_label"] = baseline_variant["regional_variant"]
        ci["paired_unit"] = "date"
        report[f"{name}_vs_R1_ci"] = ci
        # 数据契约：CI 的 mean_diff 必须等于同口径的点估计差
        require_stats_consistent(
            macro_f1(truths, preds) - macro_f1(truths, base_preds),
            ci["mean_diff"], context=f"{name} C−B 口径")
        print(f"  {name} 相对 R1（同步）: {ci['mean_diff']} CI {ci['ci95']}")

    payload = {"design": {"note": "离线规则扫描：同一批观测量、只换判定规则",
                          "variants": list(RULE_VARIANTS),
                          "base_config": base},
               "cross_domain_variants": cross,
               "frozen_config": cfg,
               "report": report}
    dest = HERE / "results" / "v96_rule_sweep.json"
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsaved -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
