"""Benchmark: multi-source agent governance vs single-source / rule baselines.

Experiment 1 of the comparison study, post-fix rerun. SourceAgent has been
reworked (no 0.5 default imputation; NO2/anomaly only gates "pollution is
elevated"; traffic source requires actual traffic + spatial evidence;
thresholds live in config/pollution_rules.json). This script:

  1. samples labelled windows and splits them dev / test (weekend
     weak-evidence negatives all go to test, never used for tuning);
  2. runs the agent chain ONCE per window and caches upstream results;
  3. grid-searches SourceAgent thresholds on **dev windows only**, freezes
     them into config/pollution_rules.json;
  4. evaluates the **test windows** once with the frozen thresholds.

NOTE on claims: labels are criteria-based annotations, so scores measure
**agreement with the annotation criteria**, not true source-identification
accuracy.

Usage: /usr/bin/python3 tests/benchmarks/bench_governance_baselines.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

DB_PATH = BASE / "data" / "air_quality.db"
RULES_PATH = BASE / "config" / "pollution_rules.json"
WIND_CACHE = Path(__file__).resolve().parent / "results" / "wind_openmeteo_2023_mongkok.json"

STATION_DB_NAME = "MONG KOK"
STATION_NAME = "Mong Kok Roadside"
STATION_ID = "mk_roadside"
LAT, LON = 22.3193, 114.1694
YEAR = 2023
WINDOW_HOURS = 3
SLOT_STARTS = [7, 12, 18]

CLASSES = ["traffic_dominant", "local_accumulation", "regional_transport", "uncertain"]
SAMPLE_PLAN = {"traffic_dominant": 20, "local_accumulation": 12,
               "regional_transport": 16, "uncertain": 16, "weekend_negative": 8}
#: 每类前 60% 窗口做开发集（阈值标定），其余留独立测试；周末负向全部进测试
DEV_FRACTION = 0.6

THRESHOLD_GRID = [0.45, 0.55, 0.65]
GATE_GRID = [0.45, 0.55]
SIGNAL_GRID = [0.6, 0.8]
NOXPCT_GRID = [0.7, 0.75, 0.8]


# ── 数据准备 ────────────────────────────────────────────────────────────

def load_annual_wind() -> dict[str, float]:
    """Open-Meteo Archive 全年逐小时风速（独立于 MeteorologyAgent 的取数路径）。"""
    if WIND_CACHE.exists():
        return json.loads(WIND_CACHE.read_text(encoding="utf-8"))
    import requests

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": LAT, "longitude": LON,
        "start_date": f"{YEAR}-01-01", "end_date": f"{YEAR}-12-31",
        "hourly": "wind_speed_10m", "timezone": "Asia/Hong_Kong",
    }
    response = requests.get(url, params=params, timeout=60)
    response.raise_for_status()
    hourly = response.json()["hourly"]
    mapping = {
        ts.replace("T", " ") + ":00" if len(ts) == 16 else ts.replace("T", " "): speed
        for ts, speed in zip(hourly["time"], hourly["wind_speed_10m"])
    }
    WIND_CACHE.parent.mkdir(parents=True, exist_ok=True)
    WIND_CACHE.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    return mapping


def percentile_of(value: float, population: list[float]) -> float:
    if not population:
        return 0.0
    below = sum(1 for v in population if v < value)
    return below / len(population)


def aqhi_to_risk(aqhi: float | None) -> str:
    if aqhi is None:
        return "unknown"
    if aqhi >= 7.0:
        return "high"
    if aqhi >= 4.0:
        return "medium"
    return "low"


# ── 标注（先于任何方法运行，标准固定） ────────────────────────────────────

def build_candidate_windows(conn: sqlite3.Connection, wind: dict[str, float]) -> list[dict]:
    """扫描全年候选窗口，按固定标准打标注。

    判据基线取**窗口均值的分位数**（不是逐小时分位数——3 小时均值几乎不可
    能超过逐小时 p85，那会让交通类永远选不出来）。区域传输的同步站判据用
    各站逐小时 p90（34 个站随机情况下同期超 p90 的期望约 3.4 个，取 ≥8 个
    才算显著同步升高）。
    """
    import datetime as dt

    station_hours = conn.execute(
        "SELECT time, no2, nox, aqhi FROM air_quality_hourly "
        "WHERE station_name = ? AND station_type = 'roadside' AND time LIKE ?",
        (STATION_DB_NAME, f"{YEAR}-%")).fetchall()
    nox_population = [row[2] for row in station_hours if row[2] is not None]
    no2_population = [row[1] for row in station_hours if row[1] is not None]
    general_p90 = {}
    for (name,) in conn.execute(
            "SELECT DISTINCT station_name FROM air_quality_hourly WHERE station_type='general'"):
        values = sorted(v for (v,) in conn.execute(
            "SELECT no2 FROM air_quality_hourly WHERE station_name=? AND station_type='general' "
            "AND time LIKE ? AND no2 IS NOT NULL", (name, f"{YEAR}-%")))
        if values:
            general_p90[name] = values[int(0.90 * len(values))]

    hours_index = {row[0]: row for row in station_hours}
    raw = []
    day = dt.date(YEAR, 1, 1)
    while day <= dt.date(YEAR, 12, 31):
        for start_hour in SLOT_STARTS:
            times = [f"{day.isoformat()} {h:02d}:00:00" for h in range(start_hour, start_hour + WINDOW_HOURS)]
            rows = [hours_index[t] for t in times if t in hours_index]
            if len(rows) < WINDOW_HOURS:
                continue
            no2 = [r[1] for r in rows if r[1] is not None]
            nox = [r[2] for r in rows if r[2] is not None]
            aqhi = [r[3] for r in rows if r[3] is not None]
            if not no2 or not nox:
                continue
            mean = lambda xs: statistics.mean(xs)  # noqa: E731
            winds = [wind.get(t) for t in times]
            winds = [w for w in winds if w is not None]
            sync_count = 0
            if winds:
                for name, threshold in general_p90.items():
                    values = [v for (v,) in conn.execute(
                        "SELECT no2 FROM air_quality_hourly WHERE station_name=? AND no2 IS NOT NULL "
                        "AND time IN (?,?,?)", (name, *times)) if v is not None]
                    if values and mean(values) >= threshold:
                        sync_count += 1
            raw.append({
                "date": day.isoformat(), "start_hour": start_hour,
                "start_time": times[0], "end_time": times[-1].replace(":00:00", ":59:59"),
                "is_weekday": day.weekday() < 5,
                "mean_no2": round(mean(no2), 2), "mean_nox": round(mean(nox), 2),
                "mean_aqhi": round(mean(aqhi), 2) if aqhi else None,
                "mean_wind": None if not winds else round(mean(winds), 2),
                "sync_count": sync_count,
            })
        day += dt.timedelta(days=1)

    # 窗口均值分位数（标注判据的唯一基线）
    nox_win_p70 = sorted(r["mean_nox"] for r in raw)[int(0.70 * len(raw))]
    no2_win_p70 = sorted(r["mean_no2"] for r in raw)[int(0.70 * len(raw))]
    no2_win_p60 = sorted(r["mean_no2"] for r in raw)[int(0.60 * len(raw))]

    candidates = []
    for r in raw:
        ratio = r["mean_no2"] / max(r["mean_nox"], 1e-6)
        # 固定标注标准（优先级：区域传输 > 局地积聚 > 交通源 > 不确定）
        if r["mean_wind"] is not None and r["sync_count"] >= 8 and r["mean_wind"] >= 3.0:
            label = "regional_transport"
        elif r["mean_wind"] is not None and r["mean_wind"] < 2.5 and r["mean_no2"] >= no2_win_p60:
            label = "local_accumulation"
        elif r["is_weekday"] and r["start_hour"] in (7, 18) and r["mean_nox"] >= nox_win_p70 and ratio >= 0.4:
            label = "traffic_dominant"
        else:
            label = "uncertain"
        r["label"] = label
        r["nox_pct"] = round(percentile_of(r["mean_nox"], nox_population), 3)
        r["no2_pct"] = round(percentile_of(r["mean_no2"], no2_population), 3)
        candidates.append(r)
    return candidates


def sample_windows(candidates: list[dict], seed: int = 42) -> list[dict]:
    """分层抽样：主样本按类别前 DEV_FRACTION 进开发集、其余进测试集；
    周末弱证据负向窗口全部进测试集，绝不参与阈值标定。"""
    import random

    rng = random.Random(seed)
    picked: list[dict] = []
    for label, count in SAMPLE_PLAN.items():
        if label == "weekend_negative":
            pool = [c for c in candidates if not c["is_weekday"]]
        else:
            pool = [c for c in candidates if c["label"] == label and c["is_weekday"]]
        rng.shuffle(pool)
        pool = pool[:count]
        pool.sort(key=lambda r: (r["date"], r["start_hour"]))
        cut = int(len(pool) * DEV_FRACTION)
        for index, item in enumerate(pool):
            row = dict(item)
            row["sample_group"] = label
            row["split"] = "dev" if (label != "weekend_negative" and index < cut) else "test"
            picked.append(row)
    picked.sort(key=lambda r: (r["date"], r["start_hour"]))
    return picked


# ── 方法 A：单源规则（只有 EPD 站点指标） ─────────────────────────────────

def method_a(window: dict, nox_population: list[float], no2_population: list[float]) -> tuple[dict, float]:
    t0 = time.perf_counter()
    nox_pct = percentile_of(window["mean_nox"], nox_population)
    no2_pct = percentile_of(window["mean_no2"], no2_population)
    # 结构性限制：只有单站污染物，无法输出区域传输
    if nox_pct >= 0.85 and window["mean_no2"] / max(window["mean_nox"], 1e-6) >= 0.4:
        label = "traffic_dominant"
    elif nox_pct >= 0.80:
        label = "traffic_dominant"
    elif no2_pct >= 0.80:
        label = "local_accumulation"
    else:
        label = "uncertain"
    evidence = [
        f"EPD {STATION_NAME} {window['start_time']}–{window['end_time']} "
        f"NO2={window['mean_no2']} NOx={window['mean_nox']}（分位 nox_p{round(nox_pct*100)}）",
        f"AQHI 均值 {window['mean_aqhi']}（EPD 逐小时数据）",
    ]
    advice = "加强该站周边巡查并关注下一小时监测" if label != "uncertain" else "继续常规监测"
    out = {"label": label, "risk_level": aqhi_to_risk(window["mean_aqhi"]),
           "advice": advice, "evidence": evidence, "datasets": ["EPD"],
           "missing_flags": [], "per_conclusion_provenance": False}
    return out, (time.perf_counter() - t0) * 1000.0


# ── 方法 B：多源固定规则（无智能体协同 / 无证据链） ────────────────────────

def method_b(window: dict, traffic_rows: list[dict], nox_population: list[float],
             no2_population: list[float]) -> tuple[dict, float]:
    t0 = time.perf_counter()
    nox_pct = percentile_of(window["mean_nox"], nox_population)
    no2_pct = percentile_of(window["mean_no2"], no2_population)
    wind = window["mean_wind"]

    # ATC 车辆结构（缺失时按常见做法默认 0.5 插补——这正是要检验的弱点）
    missing_flags = []
    if traffic_rows:
        heavy = statistics.mean(r["heavy_ratio"] for r in traffic_rows)
        heavy_norm = min(1.0, heavy / 0.3)
    else:
        heavy_norm = 0.5
        missing_flags.append("ATC 交通数据缺失，按默认值插补")
    roadside = 1.0 if "ROADSIDE" in STATION_DB_NAME else 0.4
    if wind is None:
        missing_flags.append("气象数据缺失，按默认值插补")
        low_wind = high_wind = 0.5
    else:
        low_wind = max(0.0, min(1.0, (2.5 - wind) / 2.5))
        high_wind = max(0.0, min(1.0, wind / 5.0))

    traffic_score = 0.45 * nox_pct + 0.35 * heavy_norm + 0.20 * roadside
    accumulation_score = 0.6 * low_wind + 0.4 * no2_pct
    sync_norm = min(1.0, window["sync_count"] / 5.0)
    regional_score = 0.7 * sync_norm + 0.3 * high_wind

    scores = {"traffic_dominant": traffic_score,
              "local_accumulation": accumulation_score,
              "regional_transport": regional_score}
    label = max(scores, key=scores.get)
    top_score = scores[label]
    if top_score < 0.55:
        label = "uncertain"
    risk_score = max(top_score, (window["mean_aqhi"] or 0) / 10.0)
    risk_level = "high" if risk_score >= 0.65 else "medium" if risk_score >= 0.45 else "low"
    advice = {
        "traffic_dominant": "对周边主干道实施临时交通疏导并加密路边监测",
        "local_accumulation": "提示敏感人群减少户外活动，关注风速变化",
        "regional_transport": "通报上风向区域并核对区域站点趋势",
        "uncertain": "维持常规监测，等待更多证据",
    }[label]
    evidence = [
        f"EPD {STATION_NAME} NOx 分位 {round(nox_pct*100)}%、NO2 分位 {round(no2_pct*100)}%",
        f"ATC 重型车比例归一 {round(heavy_norm, 2)}（{len(traffic_rows)} 条车种记录）",
        f"Open-Meteo 风速 {wind} m/s；区域同步升高站数 {window['sync_count']}",
    ]
    out = {"label": label, "risk_level": risk_level, "advice": advice,
           "evidence": evidence, "datasets": ["EPD", "ATC", "Open-Meteo"],
           "missing_flags": missing_flags, "per_conclusion_provenance": False}
    return out, (time.perf_counter() - t0) * 1000.0


# ── 方法 C：完整七智能体流水线 ───────────────────────────────────────────

def map_judgement(judgement: str) -> str:
    if not judgement:
        return "uncertain"
    lowered = judgement.lower()
    if "traffic" in lowered:
        return "traffic_dominant"
    if "regional" in lowered:
        return "regional_transport"
    if "accumulation" in lowered or "local" in lowered:
        return "local_accumulation"
    return "uncertain"


def run_chain(window: dict, agents: dict) -> tuple[dict, float, dict]:
    """跑一次七智能体链，缓存全部上游输出（阈值标定时只重跑 source）。"""
    t0 = time.perf_counter()
    full_task = {"task_id": f"GOV_{window['date']}_{window['start_hour']}",
                 "station_name": STATION_NAME, "station_id": STATION_ID,
                 "lat": LAT, "lon": LON,
                 "start_time": window["start_time"], "end_time": window["end_time"]}
    air = agents["air"].run(dict(full_task))
    meteo = agents["meteo"](dict(full_task))
    traffic = agents["traffic"].run(dict(full_task))
    spatial = agents["spatial"].run(dict(full_task), meteo)
    elapsed = (time.perf_counter() - t0) * 1000.0
    upstream = {"air": air, "meteo": meteo, "traffic": traffic, "spatial": spatial,
                "full_task": full_task}
    statuses = {name: agent_out.get("status") for name, agent_out in
                (("air", air), ("meteo", meteo), ("traffic", traffic), ("spatial", spatial))}
    return upstream, elapsed, statuses


def judge_source(upstream: dict, agents: dict,
                 rules: dict | None = None, label_only: bool = False) -> tuple[dict, float]:
    """用（可能被覆盖的）阈值对缓存的上游结果重新判定来源。

    ``label_only`` 供阈值网格搜索使用：只跑 source 拿标签，跳过 decision/report。
    """
    t0 = time.perf_counter()
    source = agents["source"].run({"task_id": upstream["full_task"]["task_id"],
                                   "station_name": STATION_NAME},
                                  air_result=upstream["air"],
                                  meteo_result=upstream["meteo"],
                                  traffic_result=upstream["traffic"],
                                  spatial_result=upstream["spatial"],
                                  rules=rules)
    if label_only:
        sres = source.get("result") or {}
        out = {"label": map_judgement(sres.get("main_judgement") or ""),
               "main_judgement": sres.get("main_judgement")}
        return out, (time.perf_counter() - t0) * 1000.0
    decision = agents["decision"](source, upstream["spatial"], upstream["traffic"],
                                  upstream["meteo"], dict(upstream["full_task"]),
                                  upstream["air"])
    report = agents["report"].run({"task": dict(upstream["full_task"]),
                                   "air_quality": upstream["air"],
                                   "meteorology": upstream["meteo"],
                                   "traffic": upstream["traffic"],
                                   "spatial": upstream["spatial"],
                                   "source": source, "decision": decision})
    elapsed = (time.perf_counter() - t0) * 1000.0

    sres = source.get("result") or {}
    label = map_judgement(sres.get("main_judgement") or "")
    missing_flags = []
    for name in ("air", "meteo", "traffic", "spatial"):
        if (upstream[name].get("status") or "success") != "success":
            missing_flags.append(f"{name} 状态异常")
    dquality = source.get("data_quality") or {}
    if dquality.get("missing_rate", 0) > 0:
        missing_flags.append(dquality.get("note") or "上游数据缺失")
    evidence = [e for e in (source.get("evidence") or []) if e]
    decision_res = decision.get("result") or {}
    advice = None
    if isinstance(decision_res, dict):
        actions = decision_res.get("recommended_actions") or decision_res.get("actions") or []
        if actions:
            advice = "; ".join(str(a) for a in actions[:3])
    if not advice and isinstance(report.get("result"), dict):
        advice = (report["result"].get("summary") or "").strip() or None
    out = {"label": label, "risk_level": sres.get("risk_level") or "unknown",
           "advice": advice, "evidence": evidence,
           "datasets": ["EPD", "ATC", "Open-Meteo", "路网/POI"],
           "missing_flags": missing_flags,
           "per_conclusion_provenance": True,
           "main_judgement": sres.get("main_judgement"),
           "confidence": source.get("confidence")}
    return out, elapsed


# ── 评价 ────────────────────────────────────────────────────────────────

def macro_f1(truths: list[str], preds: list[str]) -> float:
    f1s = []
    for cls in CLASSES:
        tp = sum(1 for t, p in zip(truths, preds) if t == cls and p == cls)
        fp = sum(1 for t, p in zip(truths, preds) if t != cls and p == cls)
        fn = sum(1 for t, p in zip(truths, preds) if t == cls and p != cls)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return statistics.mean(f1s)


SOURCE_KEYWORDS = re.compile(r"EPD|ATC|Open-Meteo|路网|POI|road|wind|NO2|NOx|AQHI", re.IGNORECASE)


def traceability_score(out: dict) -> float:
    """追溯率评分细则：全局溯源 0.5 + 逐结论可分解 0.5。"""
    score = 0.0
    if out.get("datasets") and out.get("evidence"):
        joined = " ".join(out["evidence"])
        has_named_source = any(k.lower() in joined.lower() for k in out["datasets"]) or \
            bool(SOURCE_KEYWORDS.search(joined))
        if has_named_source:
            score += 0.5
    if out.get("per_conclusion_provenance") and len(out.get("evidence") or []) >= 2:
        score += 0.5
    return round(score, 2)


def completion_ok(out: dict) -> bool:
    return (out.get("label") in CLASSES
            and out.get("risk_level") in ("low", "medium", "high")
            and bool(out.get("advice"))
            and len(out.get("evidence") or []) >= 2)


def summarise(rows: list[dict], note: str) -> dict:
    truths = [r["window"]["label"] for r in rows]
    summary = {"n": len(rows), "note": note, "methods": {}}
    for method in ("A", "B", "C"):
        preds = [r[method]["output"]["label"] for r in rows]
        timings = [r[method]["ms"] for r in rows]
        traces = [r[method]["traceability"] for r in rows]
        completions = [1.0 if r[method]["complete"] else 0.0 for r in rows]
        neg = [r for r in rows if r["window"]["sample_group"] == "weekend_negative"]
        neg_pred_traffic = sum(1 for r in neg if r[method]["output"]["label"] == "traffic_dominant")
        neg_flagged = sum(1 for r in neg if r[method]["output"].get("missing_flags"))
        confusion = {}
        for t, p in zip(truths, preds):
            confusion[f"{t}->{p}"] = confusion.get(f"{t}->{p}", 0) + 1
        summary["methods"][method] = {
            "macro_f1": round(macro_f1(truths, preds), 4),
            "median_ms": round(statistics.median(timings), 1),
            "mean_traceability": round(statistics.mean(traces), 3) if traces else None,
            "completion_rate": round(statistics.mean(completions), 3),
            "weak_evidence_pred_traffic": f"{neg_pred_traffic}/{len(neg)}",
            "weak_evidence_flagged": f"{neg_flagged}/{len(neg)}",
            "confusion": confusion,
        }
    return summary


def main() -> int:
    print("加载 2023 全年风速（Open-Meteo Archive，独立取数路径）...", flush=True)
    wind = load_annual_wind()
    conn = sqlite3.connect(DB_PATH)

    print("构建候选窗口与固定标准标注...", flush=True)
    nox_population = [v for (v,) in conn.execute(
        "SELECT nox FROM air_quality_hourly WHERE station_name=? AND station_type='roadside' "
        "AND time LIKE ? AND nox IS NOT NULL", (STATION_DB_NAME, f"{YEAR}-%"))]
    no2_population = [v for (v,) in conn.execute(
        "SELECT no2 FROM air_quality_hourly WHERE station_name=? AND station_type='roadside' "
        "AND time LIKE ? AND no2 IS NOT NULL", (STATION_DB_NAME, f"{YEAR}-%"))]
    candidates = build_candidate_windows(conn, wind)
    label_counts = {}
    for c in candidates:
        label_counts[c["label"]] = label_counts.get(c["label"], 0) + 1
    print(f"候选窗口 {len(candidates)} 个，标注分布 {label_counts}", flush=True)

    windows = sample_windows(candidates)
    dev_windows = [w for w in windows if w["split"] == "dev"]
    test_windows = [w for w in windows if w["split"] == "test"]
    print(f"抽样 {len(windows)} 个（开发 {len(dev_windows)} / 测试 {len(test_windows)}，"
          f"周末负向全部在测试集）", flush=True)

    sys.path.insert(0, str(BASE))
    from tools.traffic_tools import get_atc_station_ids, query_vehicle_mix, open_db as open_traffic_db

    traffic_conn = open_traffic_db()
    atc_ids = get_atc_station_ids(STATION_NAME, include_nearby=False)

    def traffic_rows_for(window: dict) -> list[dict]:
        slots = [f"{h:02d}:00" for h in range(window["start_hour"], window["start_hour"] + WINDOW_HOURS)]
        rows = query_vehicle_mix(traffic_conn, atc_ids, YEAR, slots)
        heavy_types = {"Goods Vehicle", "Franchised Bus", "Public Light Bus"}
        return [{"time_slot": r.get("time_slot"),
                 "heavy_ratio": r.get("proportion") if r.get("vehicle_type") in heavy_types else 0.0,
                 "vehicle_type": r.get("vehicle_type")} for r in rows]

    from agents.air_quality_agent import AirQualityAgent
    from agents import meteorology_agent as met
    from agents.spatial_agent import SpatialAgent
    from agents.source_agent import SourceAgent
    from agents.traffic_agent import TrafficAgent
    from agents.decison_agent import run as decision_run
    from agents.report_agent import ReportAgent
    agents = {"air": AirQualityAgent(), "meteo": met.run, "traffic": TrafficAgent(),
              "spatial": SpatialAgent(), "source": SourceAgent(),
              "decision": decision_run, "report": ReportAgent()}

    # ── 第 1 步：每个窗口跑一次链（A/B 同步评），缓存上游 ────────────────────
    results = []
    for index, window in enumerate(windows):
        b_rows = traffic_rows_for(window)
        out_a, ms_a = method_a(window, nox_population, no2_population)
        out_b, ms_b = method_b(window, b_rows, nox_population, no2_population)
        upstream, chain_ms, statuses = run_chain(window, agents)
        results.append({
            "window": {k: window[k] for k in ("date", "start_hour", "start_time", "end_time",
                                              "mean_no2", "mean_nox", "mean_aqhi", "mean_wind",
                                              "sync_count", "label", "sample_group", "split")},
            "A": {"output": out_a, "ms": round(ms_a, 2)},
            "B": {"output": out_b, "ms": round(ms_b, 2)},
            "_upstream": upstream, "_chain_ms": chain_ms, "_statuses": statuses,
        })
        print(f"  [{index+1}/{len(windows)}] {window['date']} {window['start_hour']:02d}时 "
              f"[{window['split']}] 标注={window['label']:<19} A={out_a['label']:<19} B={out_b['label']}",
              flush=True)
    conn.close()

    # ── 第 2 步：只在开发窗口上网格搜索 source 阈值 ──────────────────────────
    from agents.source_agent import load_source_rules

    base_rules = load_source_rules()
    dev_rows_cached = [r for r in results if r["window"]["split"] == "dev"]
    dev_truths = [r["window"]["label"] for r in dev_rows_cached]
    grid_results = []
    for traffic_th in THRESHOLD_GRID:
        for local_th in THRESHOLD_GRID:
            for regional_th in THRESHOLD_GRID:
                for gate_min in GATE_GRID:
                    for signal_min in SIGNAL_GRID:
                        for nox_pct_min in NOXPCT_GRID:
                            rules = dict(base_rules)
                            rules.update({"traffic_threshold": traffic_th,
                                          "local_threshold": local_th,
                                          "regional_threshold": regional_th,
                                          "gate_anomaly_min": gate_min,
                                          "traffic_signal_min": signal_min,
                                          "traffic_nox_pct_min": nox_pct_min})
                        preds = []
                        for row in dev_rows_cached:
                            out_c, _ = judge_source(row["_upstream"], agents,
                                                    rules=rules, label_only=True)
                            preds.append(out_c["label"])
                            grid_results.append({
                                "traffic_threshold": traffic_th, "local_threshold": local_th,
                                "regional_threshold": regional_th, "gate_anomaly_min": gate_min,
                                "traffic_signal_min": signal_min,
                                "traffic_nox_pct_min": nox_pct_min,
                                "dev_macro_f1": round(macro_f1(dev_truths, preds), 4),
                                "dev_pred_traffic": preds.count("traffic_dominant"),
                            })
    best = min(grid_results, key=lambda g: (-g["dev_macro_f1"], g["dev_pred_traffic"]))
    frozen_rules = dict(base_rules)
    frozen_rules.update({k: best[k] for k in ("traffic_threshold", "local_threshold",
                                              "regional_threshold", "gate_anomaly_min",
                                              "traffic_signal_min", "traffic_nox_pct_min")})
    # 冻结写回 config（之后系统与复测都使用该阈值）
    config = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    config["source_judgement"].update({
        "traffic_threshold": best["traffic_threshold"],
        "local_threshold": best["local_threshold"],
        "regional_threshold": best["regional_threshold"],
        "gate_anomaly_min": best["gate_anomaly_min"],
        "traffic_signal_min": best["traffic_signal_min"],
        "traffic_nox_pct_min": best["traffic_nox_pct_min"],
        "_calibrated": f"2026-10-13 开发窗口网格搜索冻结（dev_macro_f1={best['dev_macro_f1']}）",
    })
    RULES_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n阈值冻结（开发集 Macro-F1={best['dev_macro_f1']}）: "
          f"traffic={best['traffic_threshold']} local={best['local_threshold']} "
          f"regional={best['regional_threshold']} -> {RULES_PATH.name}", flush=True)

    # ── 第 3 步：用冻结阈值一次性评估全部窗口（测试窗口即独立复测） ────────────
    for row in results:
        out_c, ms_c = judge_source(row["_upstream"], agents, rules=frozen_rules)
        row["C"] = {"output": out_c, "ms": round(row["_chain_ms"] + ms_c, 2),
                    "agent_statuses": dict(row["_statuses"])}
        for method in ("A", "B", "C"):
            out = row[method]["output"]
            row[method]["traceability"] = traceability_score(out)
            row[method]["complete"] = completion_ok(out)
        del row["_upstream"]

    dev_results = [r for r in results if r["window"]["split"] == "dev"]
    test_results = [r for r in results if r["window"]["split"] == "test"]
    payload = {
        "design": {
            "annotation": "固定标准（先于方法运行）：区域传输=≥8 个一般站同步超各自 p90 且风≥3 m/s；"
                          "局地积聚=风<2.5 m/s 且 NO2≥窗口均值 p60；交通源=工作日高峰时段且 NOx≥窗口均值 p70 "
                          "且 NO2/NOx≥0.4；其余=不确定。标注为资料标准，F1 度量一致性而非真实溯源准确率。"
                          "周末窗口（ATC 仅 Mon-Fri）全部作为弱证据负向进入测试集。",
            "methods": {
                "A": "单源规则：仅 EPD 站点 NO2/NOx/AQHI 阈值（结构性无法输出区域传输）",
                "B": "多源固定规则：EPD+ATC+风速+区域同步，缺失输入按常见做法 0.5 默认插补；无智能体协同、无逐结论证据链",
                "C": "智瞰七智能体流水线（重构后）：缺测不插补、异常分仅做门控、交通源需真实交通+空间证据、"
                     "阈值在开发窗口标定后冻结",
            },
            "dev_test_split": f"每类前 {int(DEV_FRACTION*100)}% 开发 / 其余测试；周末负向全部测试",
            "frozen_thresholds": {k: frozen_rules[k] for k in
                                  ("gate_anomaly_min", "traffic_threshold", "local_threshold",
                                   "regional_threshold", "traffic_signal_min",
                                   "traffic_nox_pct_min")},
            "threshold_grid_top": sorted(grid_results,
                                         key=lambda g: (-g["dev_macro_f1"], g["dev_pred_traffic"]))[:10],
        },
        "summary_dev": summarise(dev_results, "开发窗口（阈值在此标定）"),
        "summary_test": summarise(test_results, "独立测试窗口（阈值冻结后一次性评估）"),
        "results": [{k: v for k, v in r.items() if not k.startswith("_")} for r in results],
    }
    path = save_result("governance_baselines", payload)
    print(f"\nsaved -> {path}")
    print("\n=== 独立测试集（阈值冻结）===")
    for method, s in payload["summary_test"]["methods"].items():
        print(f"  {method}: Macro-F1={s['macro_f1']} 中位耗时={s['median_ms']}ms "
              f"追溯率={s['mean_traceability']} 完整率={s['completion_rate']} "
              f"弱证据交通误判={s['weak_evidence_pred_traffic']} 缺失标记={s['weak_evidence_flagged']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
