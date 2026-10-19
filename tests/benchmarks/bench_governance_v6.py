"""Benchmark V6: governance evaluation on genuinely new independent samples.

The v5 test windows (Mong Kok / 2023) have been used for analysis, so per the
freeze protocol this script evaluates the **frozen** system on samples the
system has never seen in any form:

  * year 2022 (different year),
  * all three roadside stations (Mong Kok / Central / Causeway Bay),
  * same fixed annotation criteria, same frozen thresholds and weights,
  * no calibration of any kind happens here.

Outputs per-station and pooled Macro-F1 with paired bootstrap CIs for C−B.

Usage: /usr/bin/python3 tests/benchmarks/bench_governance_v6.py
"""
from __future__ import annotations

import json
import random
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result  # noqa: E402

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

DB_PATH = BASE / "data" / "air_quality.db"
WIND_DIR = Path(__file__).resolve().parent / "results"

YEAR = 2022
WINDOW_HOURS = 3
SLOT_STARTS = [7, 12, 18]
CLASSES = ["traffic_dominant", "local_accumulation", "regional_transport", "uncertain"]
SAMPLE_PLAN = {"traffic_dominant": 5, "local_accumulation": 2,
               "regional_transport": 4, "uncertain": 4, "weekend_negative": 3}

STATIONS = [
    {"db_name": "MONG KOK", "name": "Mong Kok Roadside", "id": "mk_roadside",
     "lat": 22.3193, "lon": 114.1694},
    {"db_name": "CENTRAL", "name": "Central Roadside", "id": "central_roadside",
     "lat": 22.2858, "lon": 114.1577},
    {"db_name": "CAUSEWAY BAY", "name": "Causeway Bay Roadside", "id": "cwb_roadside",
     "lat": 22.2801, "lon": 114.1850},
]


def load_annual_wind(year: int, lat: float, lon: float) -> dict[str, float]:
    cache = WIND_DIR / f"wind_openmeteo_{year}_{lat:.4f}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    import time

    import requests

    response = None
    for attempt in range(4):
        try:
            response = requests.get(
                "https://archive-api.open-meteo.com/v1/archive",
                params={"latitude": lat, "longitude": lon,
                        "start_date": f"{year}-01-01", "end_date": f"{year}-12-31",
                        "hourly": "wind_speed_10m", "timezone": "Asia/Hong_Kong"},
                timeout=60)
            response.raise_for_status()
            break
        except (requests.exceptions.RequestException, OSError) as exc:
            if attempt == 3:
                raise
            print(f"    风速取数失败（{exc}），10 秒后重试 ({attempt + 1}/3)...", flush=True)
            time.sleep(10)
    hourly = response.json()["hourly"]
    mapping = {ts.replace("T", " ") + (":00" if len(ts) == 16 else "")
               for ts in hourly["time"]}
    mapping = {ts.replace("T", " ") + (":00" if len(ts) == 16 else ""): speed
               for ts, speed in zip(hourly["time"], hourly["wind_speed_10m"])}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    return mapping


def build_windows(conn: sqlite3.Connection, station_db: str, year: int,
                  wind: dict[str, float]) -> list[dict]:
    """与主基准相同的固定标注标准（站点/年份参数化）。"""
    import datetime as dt

    station_hours = conn.execute(
        "SELECT time, no2, nox, aqhi FROM air_quality_hourly "
        "WHERE station_name = ? AND station_type = 'roadside' AND time LIKE ?",
        (station_db, f"{year}-%")).fetchall()
    no2_population = [r[1] for r in station_hours if r[1] is not None]
    nox_population = [r[2] for r in station_hours if r[2] is not None]
    general_p90 = {}
    for (name,) in conn.execute(
            "SELECT DISTINCT station_name FROM air_quality_hourly "
            "WHERE station_type='general' AND time LIKE ?", (f"{year}-%",)):
        values = sorted(v for (v,) in conn.execute(
            "SELECT no2 FROM air_quality_hourly WHERE station_name=? AND no2 IS NOT NULL "
            "AND time LIKE ?", (name, f"{year}-%")))
        if values:
            general_p90[name] = values[int(0.90 * len(values))]

    hours_index = {r[0]: r for r in station_hours}
    raw = []
    day = dt.date(year, 1, 1)
    while day <= dt.date(year, 12, 31):
        for start_hour in SLOT_STARTS:
            times = [f"{day.isoformat()} {h:02d}:00:00"
                     for h in range(start_hour, start_hour + WINDOW_HOURS)]
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
                        "SELECT no2 FROM air_quality_hourly WHERE station_name=? "
                        "AND no2 IS NOT NULL AND time IN (?,?,?)", (name, *times))
                        if v is not None]
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

    nox_win_p70 = sorted(r["mean_nox"] for r in raw)[int(0.70 * len(raw))]
    no2_win_p60 = sorted(r["mean_no2"] for r in raw)[int(0.60 * len(raw))]
    out = []
    for r in raw:
        ratio = r["mean_no2"] / max(r["mean_nox"], 1e-6)
        if r["mean_wind"] is not None and r["sync_count"] >= 8 and r["mean_wind"] >= 3.0:
            label = "regional_transport"
        elif r["mean_wind"] is not None and r["mean_wind"] < 2.5 and r["mean_no2"] >= no2_win_p60:
            label = "local_accumulation"
        elif r["is_weekday"] and r["start_hour"] in (7, 18) and r["mean_nox"] >= nox_win_p70 and ratio >= 0.4:
            label = "traffic_dominant"
        else:
            label = "uncertain"
        r["label"] = label
        out.append(r)
    return out


def method_b_v6(window: dict, station: dict, nox_population: list[float],
                no2_population: list[float], traffic_conn, atc_ids: list[str],
                year: int, wind: dict[str, float]) -> str:
    """多源固定规则基线（与主基准 method_b 同构，站点参数化）。"""
    from tools.traffic_tools import query_vehicle_mix
    nox_pct = statistics.mean([1.0 if v < window["mean_nox"] else 0.0
                               for v in nox_population])
    no2_pct = statistics.mean([1.0 if v < window["mean_no2"] else 0.0
                               for v in no2_population])
    slots = [f"{h:02d}:00" for h in range(window["start_hour"], window["start_hour"] + WINDOW_HOURS)]
    rows = query_vehicle_mix(traffic_conn, atc_ids, year, slots)
    heavy_types = {"Goods Vehicle", "Franchised Bus", "Public Light Bus"}
    if rows:
        heavy = statistics.mean(r.get("proportion") or 0.0 for r in rows
                                if r.get("vehicle_type") in heavy_types)
        heavy_norm = min(1.0, heavy / 0.3)
    else:
        heavy_norm = 0.5
    roadside = 1.0 if "ROADSIDE" in station["db_name"] else 0.4
    wind_speed = window["mean_wind"]
    if wind_speed is None:
        low_wind = high_wind = 0.5
    else:
        low_wind = max(0.0, min(1.0, (2.5 - wind_speed) / 2.5))
        high_wind = max(0.0, min(1.0, wind_speed / 5.0))

    traffic_score = 0.45 * nox_pct + 0.35 * heavy_norm + 0.20 * roadside
    accumulation_score = 0.6 * low_wind + 0.4 * no2_pct
    sync_norm = min(1.0, window["sync_count"] / 8.0)
    regional_score = 0.7 * sync_norm + 0.3 * high_wind

    scores = {"traffic_dominant": traffic_score,
              "local_accumulation": accumulation_score,
              "regional_transport": regional_score}
    label = max(scores, key=scores.get)
    if scores[label] < 0.55:
        label = "uncertain"
    return label


def annotate_v2(window: dict, general_p75: dict[str, float], conn,
                nox_population: list[float], no2_population: list[float],
                aqhi_mean: float | None) -> str:
    """独立标注 v2：与主标准不同的特征集与阈值（事先固定）。

    差异设计：区域传输用 ≥25% 一般站超各自 p75 且风 ≥2.5；交通源用
    NOx 主导（NO2/NOx < 0.5，新鲜排放特征）而非 NO2/NOx ≥ 0.4；
    积聚用 AQHI ≥ 4 且低风；污染门控用 NO2 > 站点 p60。
    """
    times = [f"{window['date']} {h:02d}:00:00"
             for h in range(window["start_hour"], window["start_hour"] + WINDOW_HOURS)]
    sync_share = 0.0
    checked = 0
    above = 0
    for name, threshold in general_p75.items():
        values = [v for (v,) in conn.execute(
            "SELECT no2 FROM air_quality_hourly WHERE station_name=? AND no2 IS NOT NULL "
            "AND time IN (?,?,?)", (name, *times)) if v is not None]
        if not values:
            continue
        checked += 1
        if statistics.mean(values) >= threshold:
            above += 1
    if checked:
        sync_share = above / checked

    elevated = window["mean_no2"] > no2_population[int(0.60 * len(no2_population))]
    if not elevated:
        return "uncertain"
    if sync_share >= 0.25 and (window["mean_wind"] or 0) >= 2.5:
        return "regional_transport"
    ratio = window["mean_no2"] / max(window["mean_nox"], 1e-6)
    if window["is_weekday"] and window["start_hour"] in (7, 12, 18) \
            and nox_population and window["mean_nox"] > nox_population[int(0.75 * len(nox_population))] \
            and ratio < 0.5:
        return "traffic_dominant"
    if (window["mean_wind"] or 9) < 2.0 and (aqhi_mean or 0) >= 4.0:
        return "local_accumulation"
    return "uncertain"


def annotate_v3(window: dict, conn, station_db: str, year: int,
                general_o3_p70: dict[str, float]) -> str:
    """独立标注 v3：完全不同的污染物通道与判据结构（跑前固定）。

    物理依据：区域传输事件在香江典型表现为 NO2 与 O3/PM 同升（区域
    光化学烟羽）；局地交通排放 NOx 新鲜（NO2/NOx 低）且滴定 O3；
    局地积聚 = 低风 + NO2 高但 O3 被滴定。
    """
    times = [f"{window['date']} {h:02d}:00:00"
             for h in range(window["start_hour"], window["start_hour"] + WINDOW_HOURS)]
    rows = conn.execute(
        "SELECT no2, nox, o3, pm25 FROM air_quality_hourly "
        "WHERE station_name=? AND time IN (?,?,?)",
        (station_db, *times)).fetchall()
    o3_vals = [r[2] for r in rows if r[2] is not None]
    pm_vals = [r[3] for r in rows if r[3] is not None]
    no2_mean = window["mean_no2"]
    nox_mean = max(window["mean_nox"], 1e-6)
    o3_mean = statistics.mean(o3_vals) if o3_vals else None

    # 门控：NO2 高于本站当年 p70（相对量，年份鲁棒）
    station_no2 = sorted(v for (v,) in conn.execute(
        "SELECT no2 FROM air_quality_hourly WHERE station_name=? AND no2 IS NOT NULL "
        "AND time LIKE ?", (station_db, f"{year}-%")))
    if not station_no2 or no2_mean <= station_no2[int(0.70 * len(station_no2))]:
        return "uncertain"

    o3_above = (o3_mean is not None and any(
        o3_mean >= t for t in general_o3_p70.values()))
    no2_no3_ratio = no2_mean / nox_mean

    # 区域传输：O3/PM 同升（区域烟羽）+ 同步（复用窗口 sync_count）或大风
    if o3_above and (window["sync_count"] >= 6 or (window["mean_wind"] or 0) >= 3.0):
        return "regional_transport"
    # 交通：新鲜排放（NO2/NOx < 0.45）+ 工作日高峰 + NOx 分位高
    nox_pop = sorted(v for (v,) in conn.execute(
        "SELECT nox FROM air_quality_hourly WHERE station_name=? AND nox IS NOT NULL "
        "AND time LIKE ?", (station_db, f"{year}-%")))
    nox_pct = statistics.mean([1.0 if v < nox_mean else 0.0 for v in nox_pop]) if nox_pop else 0.0
    if (window["is_weekday"] and window["start_hour"] in (7, 18)
            and nox_pct >= 0.75 and no2_no3_ratio < 0.45):
        return "traffic_dominant"
    # 局地积聚：低风 + O3 被滴定（低于一般站 p50 中位）
    o3_mid = statistics.mean(list(general_o3_p70.values())) / 1.15 if general_o3_p70 else 999
    if (window["mean_wind"] or 9) < 2.0 and (o3_mean is None or o3_mean < o3_mid):
        return "local_accumulation"
    return "uncertain"


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


def main() -> int:
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

    conn = sqlite3.connect(DB_PATH)
    all_rows = []
    for station in STATIONS:
        print(f"=== {station['name']} / {YEAR} ===", flush=True)
        wind = load_annual_wind(YEAR, station["lat"], station["lon"])
        candidates = build_windows(conn, station["db_name"], YEAR, wind)
        counts = {}
        for c in candidates:
            counts[c["label"]] = counts.get(c["label"], 0) + 1
        print(f"  候选 {len(candidates)} 个，标注分布 {counts}", flush=True)

        rng = random.Random(2022)
        picked = []
        for label, count in SAMPLE_PLAN.items():
            if label == "weekend_negative":
                pool = [c for c in candidates if not c["is_weekday"]]
            else:
                pool = [c for c in candidates if c["label"] == label and c["is_weekday"]]
            rng.shuffle(pool)
            for item in pool[:count]:
                row = dict(item)
                row["sample_group"] = label
                row["station"] = station["name"]
                picked.append(row)

        no2_population = sorted(v for (v,) in conn.execute(
            "SELECT no2 FROM air_quality_hourly WHERE station_name=? AND station_type='roadside' "
            "AND time LIKE ? AND no2 IS NOT NULL", (station["db_name"], f"{YEAR}-%")))
        nox_population = sorted(v for (v,) in conn.execute(
            "SELECT nox FROM air_quality_hourly WHERE station_name=? AND station_type='roadside' "
            "AND time LIKE ? AND nox IS NOT NULL", (station["db_name"], f"{YEAR}-%")))
        general_o3_p70 = {}
        for (name,) in conn.execute(
                "SELECT DISTINCT station_name FROM air_quality_hourly "
                "WHERE station_type='general' AND time LIKE ?", (f"{YEAR}-%",)):
            o3v = sorted(v for (v,) in conn.execute(
                "SELECT o3 FROM air_quality_hourly WHERE station_name=? AND o3 IS NOT NULL "
                "AND time LIKE ?", (name, f"{YEAR}-%")))
            if o3v:
                general_o3_p70[name] = o3v[int(0.70 * len(o3v))]
        general_p75 = {}
        for (name,) in conn.execute(
                "SELECT DISTINCT station_name FROM air_quality_hourly "
                "WHERE station_type='general' AND time LIKE ?", (f"{YEAR}-%",)):
            values = sorted(v for (v,) in conn.execute(
                "SELECT no2 FROM air_quality_hourly WHERE station_name=? AND no2 IS NOT NULL "
                "AND time LIKE ?", (name, f"{YEAR}-%")))
            if values:
                general_p75[name] = values[int(0.75 * len(values))]

        from tools.traffic_tools import get_atc_station_ids, query_vehicle_mix, open_db as open_traffic_db
        traffic_conn = open_traffic_db()
        atc_ids = get_atc_station_ids(station["name"], include_nearby=False)

        for index, window in enumerate(picked):
            full_task = {"task_id": f"GV6_{station['id']}_{window['date']}_{window['start_hour']}",
                         "station_name": station["name"], "station_id": station["id"],
                         "lat": station["lat"], "lon": station["lon"],
                         "start_time": window["start_time"], "end_time": window["end_time"]}
            air = agents["air"].run(dict(full_task))
            meteo = agents["meteo"](dict(full_task))
            traffic = agents["traffic"].run(dict(full_task))
            spatial = agents["spatial"].run(dict(full_task), meteo)
            source = agents["source"].run({"task_id": full_task["task_id"],
                                           "station_name": station["name"]},
                                          air_result=air, meteo_result=meteo,
                                          traffic_result=traffic, spatial_result=spatial)
            sres = source.get("result") or {}
            judgement = (sres.get("main_judgement") or "").lower()
            if "traffic" in judgement:
                c_label = "traffic_dominant"
            elif "regional" in judgement:
                c_label = "regional_transport"
            elif "accumulation" in judgement or "local" in judgement:
                c_label = "local_accumulation"
            else:
                c_label = "uncertain"

            # A/B 基线（同 v5 口径）
            nox_pct = statistics.mean([1.0 if v < window["mean_nox"] else 0.0
                                       for v in nox_population])
            no2_pct = statistics.mean([1.0 if v < window["mean_no2"] else 0.0
                                       for v in no2_population])
            if nox_pct >= 0.80:
                a_label = "traffic_dominant"
            elif no2_pct >= 0.80:
                a_label = "local_accumulation"
            else:
                a_label = "uncertain"

            b_label = method_b_v6(window, station, nox_population, no2_population,
                                  traffic_conn, atc_ids, YEAR, wind)
            label_v2 = annotate_v2(window, general_p75, conn,
                                   nox_population, no2_population,
                                   window["mean_aqhi"])
            label_v3 = annotate_v3(window, conn, station["db_name"], YEAR,
                                   general_o3_p70)
            all_rows.append({
                "station": station["name"], "date": window["date"],
                "start_hour": window["start_hour"], "label": window["label"],
                "sample_group": window.get("sample_group"),
                "A": a_label, "B": b_label, "C": c_label,
                "label_v2": label_v2, "label_v3": label_v3,
                "source_judgement": sres.get("main_judgement"),
                "confidence": source.get("confidence"),
            })
            print(f"  [{index+1}/{len(picked)}] {window['date']} {window['start_hour']:02d}时 "
                  f"标注={window['label']:<19} A={a_label:<19} C={c_label}", flush=True)

    conn.close()

    truths = [r["label"] for r in all_rows]
    preds = {m: [r[m] for r in all_rows] for m in ("A", "B", "C")}
    point = {m: round(macro_f1(truths, preds[m]), 4) for m in ("A", "B", "C")}
    truths_v2 = [r["label_v2"] for r in all_rows]
    point_v2 = {m: round(macro_f1(truths_v2, preds[m]), 4) for m in ("A", "B", "C")}
    truths_v3 = [r["label_v3"] for r in all_rows]
    point_v3 = {m: round(macro_f1(truths_v3, preds[m]), 4) for m in ("A", "B", "C")}

    rng = random.Random(42)
    n = len(all_rows)
    diff_samples = []
    for _ in range(10000):
        idx = [rng.randrange(n) for _ in range(n)]
        t = [truths[i] for i in idx]
        pb = [preds["B"][i] for i in idx]
        pc = [preds["C"][i] for i in idx]
        diff_samples.append(macro_f1(t, pc) - macro_f1(t, pb))
    diff_sorted = sorted(diff_samples)
    ci = [round(diff_sorted[int(0.025 * 10000)], 4), round(diff_sorted[int(0.975 * 10000)], 4)]

    by_station = {}
    for station in STATIONS:
        subset = [r for r in all_rows if r["station"] == station["name"]]
        by_station[station["name"]] = {
            "n": len(subset),
            "A": round(macro_f1([r["label"] for r in subset],
                                [r["A"] for r in subset]), 4),
            "B": round(macro_f1([r["label"] for r in subset],
                                [r["B"] for r in subset]), 4),
            "C": round(macro_f1([r["label"] for r in subset],
                                [r["C"] for r in subset]), 4),
        }

    payload = {
        "design": {
            "note": "全新独立样本：2022 年 + 三个路边站，冻结系统零调参评估。"
                    "标注标准与主基准完全一致。",
            "year": YEAR, "stations": [s["name"] for s in STATIONS],
            "sample_plan": SAMPLE_PLAN,
        },
        "point_f1": point,
        "point_f1_vs_independent_annotation_v2": point_v2,
        "point_f1_vs_independent_annotation_v3": point_v3,
        "diff_C_minus_A_ci": {"mean": round(statistics.mean(diff_samples), 4), "ci95": ci},
        "by_station": by_station,
        "label_distribution": {
            group: sum(1 for r in all_rows if r["sample_group"] == group)
            for group in SAMPLE_PLAN},
        "results": all_rows,
    }
    path = save_result("governance_v6", payload)
    print(f"\nsaved -> {path}")
    print(f"\n=== 全新独立样本（{YEAR}，3 站，n={len(all_rows)}）===")
    print(f"  Macro-F1 vs 主标注: A={point['A']} B={point['B']} C={point['C']}")
    print(f"  Macro-F1 vs 独立标注v2: A={point_v2['A']} B={point_v2['B']} C={point_v2['C']}")
    print(f"  Macro-F1 vs 独立标注v3(O3通道): A={point_v3['A']} B={point_v3['B']} C={point_v3['C']}")
    print(f"  C−B 差值 CI: {ci}")
    print(f"  分站点: {by_station}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
