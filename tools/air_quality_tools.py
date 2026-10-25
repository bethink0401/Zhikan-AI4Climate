# -*- coding: utf-8 -*-
"""
Air Quality Agent 工具函数：异常识别、交通源信号判断、路边站增强判断。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "air_quality.db"

ROADSIDE_STATIONS = {"Mong Kok Roadside", "Causeway Bay Roadside", "Central Roadside"}

AQHI_THRESHOLDS = {
    "low": 4.0,
    "moderate": 5.0,
    "high": 7.0,
    "very_high": 10.0,
}

POLLUTANT_THRESHOLDS = {
    "no2": {"normal": 80.0, "high": 120.0, "very_high": 200.0},
    "nox": {"normal": 100.0, "high": 150.0, "very_high": 250.0},
    "pm25": {"normal": 25.0, "high": 50.0, "very_high": 75.0},
    "pm10": {"normal": 50.0, "high": 100.0, "very_high": 150.0},
    "o3": {"normal": 100.0, "high": 160.0, "very_high": 200.0},
    "so2": {"normal": 20.0, "high": 40.0, "very_high": 80.0},
    "co": {"normal": 4.0, "high": 8.0, "very_high": 12.0},
}


def open_db(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    if not Path(db_path).exists():
        raise FileNotFoundError(f"空气库不存在，请先运行 ETL: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def classify_aqhi_level(aqhi: Optional[float]) -> str:
    if aqhi is None:
        return "unknown"
    if aqhi < AQHI_THRESHOLDS["low"]:
        return "low"
    elif aqhi < AQHI_THRESHOLDS["moderate"]:
        return "moderate"
    elif aqhi < AQHI_THRESHOLDS["high"]:
        return "high"
    else:
        return "very_high"


def classify_pollutant_level(pollutant: str, value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    thresholds = POLLUTANT_THRESHOLDS.get(pollutant)
    if not thresholds:
        return "unknown"
    if value < thresholds["normal"]:
        return "normal"
    elif value < thresholds["high"]:
        return "high"
    else:
        return "very_high"


def compute_anomaly_score(rows: list[dict[str, Any]]) -> float:
    """计算异常分数 (0-1)"""
    if not rows:
        return 0.0
    scores = []
    for row in rows:
        row_scores = []
        if row.get("aqhi") is not None:
            aqhi = row["aqhi"]
            if aqhi >= 7:
                row_scores.append(0.9)
            elif aqhi >= 5:
                row_scores.append(0.7)
            elif aqhi >= 4:
                row_scores.append(0.5)
            else:
                row_scores.append(0.2)
        for pollutant in ["no2", "nox", "pm25", "pm10", "o3"]:
            val = row.get(pollutant)
            if val is not None:
                level = classify_pollutant_level(pollutant, val)
                if level == "very_high":
                    row_scores.append(0.9)
                elif level == "high":
                    row_scores.append(0.7)
                else:
                    row_scores.append(0.3)
        if row_scores:
            scores.append(max(row_scores))
    return sum(scores) / len(scores) if scores else 0.0


def identify_main_pollutants(rows: list[dict[str, Any]]) -> list[str]:
    """识别主要污染物"""
    if not rows:
        return []
    pollutant_counts: dict[str, int] = {}
    for row in rows:
        for pollutant in ["no2", "nox", "pm25", "pm10", "o3", "so2", "co"]:
            val = row.get(pollutant)
            if val is not None:
                level = classify_pollutant_level(pollutant, val)
                if level in ("high", "very_high"):
                    pollutant_counts[pollutant.upper()] = pollutant_counts.get(pollutant.upper(), 0) + 1
    sorted_pollutants = sorted(pollutant_counts.items(), key=lambda x: x[1], reverse=True)
    return [p for p, _ in sorted_pollutants[:3]]


def detect_traffic_signal(rows: list[dict[str, Any]], start_time: str = "", end_time: str = "") -> dict[str, Any]:
    """检测交通源信号：NO2/NOx 在早晚高峰异常升高"""
    if not rows:
        return {"traffic_pollutant_signal": "unknown", "details": "No data available"}
    morning_peak = []
    evening_peak = []
    off_peak = []
    for row in rows:
        time_str = row.get("time", "")
        hour = 0
        if " " in time_str:
            try:
                hour = int(time_str.split(" ")[1].split(":")[0])
            except (ValueError, IndexError):
                pass
        if 7 <= hour <= 9:
            morning_peak.append(row)
        elif 17 <= hour <= 19:
            evening_peak.append(row)
        else:
            off_peak.append(row)
    def avg_pollutant(data, key):
        vals = [r[key] for r in data if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None
    morning_no2 = avg_pollutant(morning_peak, "no2")
    evening_no2 = avg_pollutant(evening_peak, "no2")
    off_no2 = avg_pollutant(off_peak, "no2")
    morning_nox = avg_pollutant(morning_peak, "nox")
    evening_nox = avg_pollutant(evening_peak, "nox")
    off_nox = avg_pollutant(off_peak, "nox")
    signal = "weak"
    details = []
    if morning_no2 and off_no2 and morning_no2 > off_no2 * 1.3:
        signal = "strong"
        details.append(f"Morning peak NO2 ({morning_no2:.1f}) significantly higher than off-peak ({off_no2:.1f})")
    if evening_no2 and off_no2 and evening_no2 > off_no2 * 1.3:
        signal = "strong"
        details.append(f"Evening peak NO2 ({evening_no2:.1f}) significantly higher than off-peak ({off_no2:.1f})")
    if morning_nox and off_nox and morning_nox > off_nox * 1.3:
        if signal != "strong":
            signal = "moderate"
        details.append(f"Morning peak NOx ({morning_nox:.1f}) elevated")
    if not details:
        details.append("No significant traffic-related pollutant elevation detected")
    return {
        "traffic_pollutant_signal": signal,
        "details": details,
        "morning_peak": {"no2": morning_no2, "nox": morning_nox},
        "evening_peak": {"no2": evening_no2, "nox": evening_nox},
        "off_peak": {"no2": off_no2, "nox": off_nox},
    }


def is_roadside_station(station_name: str) -> bool:
    name_upper = station_name.upper()
    for rs in ROADSIDE_STATIONS:
        if rs.upper() in name_upper or name_upper in rs.upper():
            return True
    return False


def compute_roadside_boost(station_name: str, traffic_signal: str) -> float:
    if not is_roadside_station(station_name):
        return 0.4
    if traffic_signal == "strong":
        return 0.95
    elif traffic_signal == "moderate":
        return 0.85
    return 0.75


def build_evidence(
    station_name: str,
    aqhi_level: str,
    main_pollutants: list[str],
    traffic_signal: dict[str, Any],
    anomaly_score: float,
) -> list[str]:
    evidence = []
    if aqhi_level in ("high", "very_high"):
        evidence.append(f"AQHI level is {aqhi_level} at {station_name}.")
    if main_pollutants:
        evidence.append(f"Main pollutants: {', '.join(main_pollutants)}.")
    if traffic_signal.get("traffic_pollutant_signal") == "strong":
        evidence.append("NO2 and NOx increased significantly during the selected period.")
    if is_roadside_station(station_name):
        evidence.append("Roadside station signal indicates possible traffic-related pollution.")
    if anomaly_score >= 0.7:
        evidence.append(f"Anomaly score is {anomaly_score:.2f}, indicating significant pollution.")
    elif anomaly_score >= 0.5:
        evidence.append(f"Anomaly score is {anomaly_score:.2f}, moderate pollution detected.")
    return evidence


def query_air_quality_data(
    conn: sqlite3.Connection,
    station_name: str,
    start_time: str,
    end_time: str,
) -> list[dict[str, Any]]:
    # 数据库中存储的是不带 "Roadside" 后缀的全大写站点名（如 "MONG KOK"）。
    # 去掉后缀后再做 LIKE 匹配，确保 "Mong Kok Roadside" → "MONG KOK" 能命中。
    search_name = station_name.replace(" Roadside", "").replace(" roadside", "").strip()
    sql = """
        SELECT station_id, station_name, station_type, time,
               aqhi, no2, nox, pm25, pm10, o3, so2, co
        FROM air_quality_hourly
        WHERE UPPER(station_name) LIKE UPPER(?)
          AND time >= ?
          AND time <= ?
        ORDER BY time
    """
    cursor = conn.execute(sql, (f"%{search_name}%", start_time, end_time))
    return [dict(row) for row in cursor.fetchall()]


# ── 多站同步检测（区域传输证据）─────────────────────────────────────────
#
# 区域传输的判据不是单站升高，而是"多数站点同期升高"。这里给每个站建立
# 自身的 NO2 p90 基线（全期小时值），窗口均值超过自身 p90 记为"该站同步
# 升高"。p90 结果缓存到 data/processed/station_no2_p90.json，跨进程复用。

_P90_CACHE_PATH = DEFAULT_DB.parent / "processed" / "station_no2_p90.json"


def load_station_no2_p90(conn, year: int, cache_path=None,
                         as_of: str | None = None) -> dict[str, float]:
    """各站当年逐小时 NO2 的 p90，带磁盘缓存（按年份分键）。

    基线必须与窗口同年份：香港 NO2 年际下降明显，混入早年数据会把 p90
    抬高、同步升高判不出来。缓存文件按年份区分。
    """
    import datetime as dt
    import json

    base = Path(cache_path) if cache_path else _P90_CACHE_PATH
    # V9.2 前向回测：as_of 给定时基线只取事件月之前的数据（严格早于事件），
    # 缓存按年+月粒度；None 时保持全年口径（历史回测框架）。
    if as_of:
        path = base.parent / f"station_no2_p90_{year}_pit_{str(as_of)[:7]}.json"
    else:
        path = base.parent / f"station_no2_p90_{year}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    if as_of:
        # 事件前数据：滚动 12 个月（事件月月初往前一年）至事件月月初，
        # 严格早于事件；跨年使用历史数据，符合实时系统的滚动基线实践。
        pit_hi = f"{str(as_of)[:7]}-01 00:00:00"
        pit_lo = (dt.datetime.fromisoformat(pit_hi) - dt.timedelta(days=365)
                  ).isoformat(sep=" ")
        stations = [r[0] for r in conn.execute(
            "SELECT DISTINCT station_name FROM air_quality_hourly "
            "WHERE no2 IS NOT NULL AND time >= ? AND time < ?",
            (pit_lo, pit_hi))]
    else:
        time_filter = f"{year}-%"
        stations = [r[0] for r in conn.execute(
            "SELECT DISTINCT station_name FROM air_quality_hourly "
            "WHERE no2 IS NOT NULL AND time LIKE ?", (f"{year}-%",))]
    p90_map: dict[str, float] = {}
    for name in stations:
        if as_of:
            values = sorted(v for (v,) in conn.execute(
                "SELECT no2 FROM air_quality_hourly WHERE station_name=? AND no2 IS NOT NULL "
                "AND time >= ? AND time < ?", (name, pit_lo, pit_hi)))
        else:
            values = sorted(v for (v,) in conn.execute(
                "SELECT no2 FROM air_quality_hourly WHERE station_name=? AND no2 IS NOT NULL "
                "AND time LIKE ?", (name, f"{year}-%")))
        if values:
            p90_map[name] = round(values[int(0.90 * len(values))], 2)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(p90_map, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return p90_map


def query_multi_station_sync(conn, start_time: str, end_time: str,
                             p90_map: dict[str, float]) -> dict[str, Any]:
    """窗口内各站 NO2 均值 vs 各站自身 p90，输出同步升高统计。"""
    rows = conn.execute(
        "SELECT station_name, AVG(no2) AS mean_no2, COUNT(*) AS n "
        "FROM air_quality_hourly WHERE no2 IS NOT NULL AND time >= ? AND time <= ? "
        "GROUP BY station_name",
        (start_time, end_time),
    ).fetchall()
    above = []
    checked = 0
    for name, mean_no2, n in rows:
        threshold = p90_map.get(name)
        if threshold is None or n < 2:
            continue
        checked += 1
        if mean_no2 >= threshold:
            above.append({"station": name, "mean_no2": round(mean_no2, 1),
                          "p90": threshold})
    above.sort(key=lambda x: -x["mean_no2"])
    return {
        "stations_checked": checked,
        "stations_above_p90": len(above),
        "above_p90_stations": above[:8],
    }


def query_spatial_contrast(conn, start_time: str, end_time: str,
                           p90_map: dict[str, float],
                           focus_station: str | None = None) -> dict[str, Any]:
    """窗口内各站 NO2 均值相对各自 p90 的空间对比度（V9.5 区域证据）。

    物理依据：区域传输是**大范围均匀抬升**——各站窗口均值相对自身基线
    同步升高、离散度低；交通/局地积聚是**单站或少数站集中升高**——站点间
    比值离散度大。因此同时看两个量：

      * ``mean_ratio``：全站窗口均值 / 各自 p90 的均值（抬升幅度）；
      * ``cv``：同一组比值的变异系数（空间离散度）。

    实测（合成世界，2026-10-14）：区域 mean_ratio 1.44–1.67 / cv≈0.05；
    交通 ≈1.0 / cv≈0.05；局地 0.78–0.88 / cv≈0.21；不确定 ≈1.0 / cv≈0.05。
    仅凭"超 p90 站数"无法区分区域与交通（两者都可能全站超标），空间对比度
    才是区域事件的判别证据。
    """
    rows = conn.execute(
        "SELECT station_name, AVG(no2) AS mean_no2, COUNT(*) AS n "
        "FROM air_quality_hourly WHERE no2 IS NOT NULL AND time >= ? AND time <= ? "
        "GROUP BY station_name",
        (start_time, end_time),
    ).fetchall()
    ratios = []
    station_ratio = None
    focus_key = (focus_station or "").replace(" Roadside", "").strip().upper()
    for name, mean_no2, n in rows:
        threshold = p90_map.get(name)
        if threshold is None or n < 2 or not threshold:
            continue
        ratio = float(mean_no2) / float(threshold)
        ratios.append(ratio)
        if focus_key and str(name).replace(" Roadside", "").strip().upper() == focus_key:
            station_ratio = ratio
    if len(ratios) < 3:
        return {"stations_checked": len(ratios), "mean_ratio": None, "cv": None,
                "station_ratio": round(station_ratio, 4) if station_ratio else None}
    mean_ratio = sum(ratios) / len(ratios)
    variance = sum((r - mean_ratio) ** 2 for r in ratios) / len(ratios)
    cv = (variance ** 0.5) / mean_ratio if mean_ratio else 0.0
    return {"stations_checked": len(ratios),
            "mean_ratio": round(mean_ratio, 4), "cv": round(cv, 4),
            "station_ratio": round(station_ratio, 4) if station_ratio is not None else None}


def query_midday_climatology(conn, station_name: str, year: int) -> float | None:
    """站点当年 11-15 时（车辆稀少的日内低谷）NO2 均值。

    用全年小时数据做基线，消除"3 小时窗口内 off-peak 只剩 1 小时"的
    小样本噪声；分年份计算，天然适配年际水平漂移。
    """
    search_name = station_name.replace(" Roadside", "").replace(" roadside", "").strip()
    row = conn.execute(
        "SELECT AVG(no2) FROM air_quality_hourly "
        "WHERE UPPER(station_name) LIKE UPPER(?) AND no2 IS NOT NULL "
        "AND time LIKE ? AND CAST(substr(time, 12, 2) AS INTEGER) BETWEEN 11 AND 15",
        (f"%{search_name}%", f"{year}-%"),
    ).fetchone()
    if row and row[0]:
        return float(row[0])
    return None
