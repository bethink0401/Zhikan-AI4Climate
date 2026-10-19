# -*- coding: utf-8 -*-
"""
Air Quality Data Process — 香港 EPD AQHI/多污染物数据 ETL

支持：
1. API 接入（EPD EPICDI 接口）
2. CSV 导入
3. 数据清洗（统一时间格式、站点名称、污染物字段）
4. 缺失值标记（不直接删除）
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "air_quality.db"
SCHEMA_SQL = Path(__file__).resolve().parent / "schema_air_quality.sql"

ROADSIDE_STATIONS = {
    "Mong Kok Roadside": {"is_roadside": 1, "district": "Kowloon"},
    "Causeway Bay Roadside": {"is_roadside": 1, "district": "Hong Kong Island"},
    "Central Roadside": {"is_roadside": 1, "district": "Hong Kong Island"},
}

EPD_STATIONS = {
    "Central": {"is_roadside": 0, "district": "Hong Kong Island", "lat": 22.2819, "lon": 114.1580},
    "Causeway Bay": {"is_roadside": 0, "district": "Hong Kong Island", "lat": 22.2800, "lon": 114.1850},
    "Mong Kok": {"is_roadside": 0, "district": "Kowloon", "lat": 22.3193, "lon": 114.1694},
    "Tung Chung": {"is_roadside": 0, "district": "Lantau", "lat": 22.2890, "lon": 113.9440},
    "Tap Mun": {"is_roadside": 0, "district": "New Territories", "lat": 22.4710, "lon": 114.2700},
    "Tsuen Wan": {"is_roadside": 0, "district": "New Territories", "lat": 22.3710, "lon": 114.1140},
    "Sham Shui Po": {"is_roadside": 0, "district": "Kowloon", "lat": 22.3310, "lon": 114.1630},
    "Kwun Tong": {"is_roadside": 0, "district": "Kowloon", "lat": 22.3130, "lon": 114.2260},
    "Tai Po": {"is_roadside": 0, "district": "New Territories", "lat": 22.4510, "lon": 114.1680},
    "Yuen Long": {"is_roadside": 0, "district": "New Territories", "lat": 22.4450, "lon": 114.0220},
    "North": {"is_roadside": 0, "district": "New Territories", "lat": 22.4960, "lon": 114.1370},
    "Hung Hom": {"is_roadside": 0, "district": "Kowloon", "lat": 22.3080, "lon": 114.1850},
}


def init_db(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    _init_station_meta(conn)
    return conn


def _init_station_meta(conn: sqlite3.Connection) -> None:
    existing = conn.execute("SELECT COUNT(*) FROM air_quality_stations").fetchone()[0]
    if existing > 0:
        return
    for name, info in {**ROADSIDE_STATIONS, **EPD_STATIONS}.items():
        station_id = name.lower().replace(" ", "_").replace("roadside", "")
        conn.execute(
            """INSERT OR REPLACE INTO air_quality_stations
               (station_id, station_name, station_type, lat, lon, district, is_roadside, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                station_id,
                name,
                "roadside" if info.get("is_roadside") else "general",
                info.get("lat"),
                info.get("lon"),
                info.get("district"),
                info.get("is_roadside", 0),
                "epd",
            ),
        )
    conn.commit()


def normalize_time(time_str: str) -> str:
    """统一时间格式为 YYYY-MM-DD HH:MM:SS"""
    time_str = time_str.strip()
    patterns = [
        (r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})", "%Y-%m-%d %H:%M"),
        (r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", "%Y-%m-%dT%H:%M"),
        (r"(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})", "%d/%m/%Y %H:%M"),
        (r"(\d{2})-(\d{2})-(\d{4})\s+(\d{2}):(\d{2})", "%d-%m-%Y %H:%M"),
    ]
    for pattern, fmt in patterns:
        m = re.match(pattern, time_str)
        if m:
            try:
                dt = datetime.strptime(time_str[:len(fmt.replace('%Y','2024').replace('%m','01').replace('%d','01').replace('%H','00').replace('%M','00'))], fmt)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
    if re.match(r"\d{4}-\d{2}-\d{2}", time_str):
        return time_str[:19] if len(time_str) >= 19 else time_str + " 00:00:00"
    return time_str


def normalize_station_name(name: str) -> str:
    """统一站点名称"""
    name = name.strip()
    alias_map = {
        "旺角": "Mong Kok",
        "旺角路边": "Mong Kok Roadside",
        "铜锣湾": "Causeway Bay",
        "铜锣湾路边": "Causeway Bay Roadside",
        "中环": "Central",
        "中环路边": "Central Roadside",
        "东涌": "Tung Chung",
        "塔门": "Tap Mun",
        "荃湾": "Tsuen Wan",
        "深水埗": "Sham Shui Po",
        "观塘": "Kwun Tong",
        "大埔": "Tai Po",
        "元朗": "Yuen Long",
        "北区": "North",
        "红磡": "Hung Hom",
    }
    if name in alias_map:
        return alias_map[name]
    for key, val in alias_map.items():
        if key in name or name in key:
            return val
    return name


def safe_float(value: Any) -> Optional[float]:
    """安全转换为浮点数，缺失值返回 None（标记但不删除）"""
    if value is None or value == "" or value == "N/A" or value == "-":
        return None
    try:
        v = float(value)
        if v < 0:
            return None
        return v
    except (TypeError, ValueError):
        return None


def import_csv(csv_path: Path, db_path: Path = DEFAULT_DB) -> int:
    """导入 CSV 格式的 AQHI 数据"""
    conn = init_db(db_path)
    count = 0
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            station_name = normalize_station_name(
                row.get("station_name") or row.get("Station") or row.get("station") or ""
            )
            time_str = normalize_time(
                row.get("time") or row.get("datetime") or row.get("date") or ""
            )
            if not station_name or not time_str:
                continue
            station_id = station_name.lower().replace(" ", "_").replace("roadside", "")
            station_type = "roadside" if "roadside" in station_name.lower() else "general"
            conn.execute(
                """INSERT OR REPLACE INTO air_quality_hourly
                   (station_id, station_name, station_type, time,
                    aqhi, no2, nox, pm25, pm10, o3, so2, co, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    station_id,
                    station_name,
                    station_type,
                    time_str,
                    safe_float(row.get("aqhi") or row.get("AQHI")),
                    safe_float(row.get("no2") or row.get("NO2") or row.get("nitrogen_dioxide")),
                    safe_float(row.get("nox") or row.get("NOx") or row.get("nitrogen_oxides")),
                    safe_float(row.get("pm25") or row.get("PM2.5") or row.get("pm2_5")),
                    safe_float(row.get("pm10") or row.get("PM10")),
                    safe_float(row.get("o3") or row.get("O3") or row.get("ozone")),
                    safe_float(row.get("so2") or row.get("SO2") or row.get("sulphur_dioxide")),
                    safe_float(row.get("co") or row.get("CO") or row.get("carbon_monoxide")),
                    "csv",
                ),
            )
            count += 1
    conn.commit()
    conn.close()
    return count


def import_json(json_path: Path, db_path: Path = DEFAULT_DB) -> int:
    """导入 JSON 格式的 AQHI 数据"""
    conn = init_db(db_path)
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("data") or data.get("records") or [data]
    count = 0
    for row in data:
        station_name = normalize_station_name(
            row.get("station_name") or row.get("Station") or ""
        )
        time_str = normalize_time(
            row.get("time") or row.get("datetime") or ""
        )
        if not station_name or not time_str:
            continue
        station_id = station_name.lower().replace(" ", "_").replace("roadside", "")
        station_type = "roadside" if "roadside" in station_name.lower() else "general"
        conn.execute(
            """INSERT OR REPLACE INTO air_quality_hourly
               (station_id, station_name, station_type, time,
                aqhi, no2, nox, pm25, pm10, o3, so2, co, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                station_id,
                station_name,
                station_type,
                time_str,
                safe_float(row.get("aqhi")),
                safe_float(row.get("no2")),
                safe_float(row.get("nox")),
                safe_float(row.get("pm25")),
                safe_float(row.get("pm10")),
                safe_float(row.get("o3")),
                safe_float(row.get("so2")),
                safe_float(row.get("co")),
                "json",
            ),
        )
        count += 1
    conn.commit()
    conn.close()
    return count


def query_air_quality(
    conn: sqlite3.Connection,
    station_name: str,
    start_time: str,
    end_time: str,
) -> list[dict[str, Any]]:
    """查询指定站点和时间段的空气质量数据"""
    sql = """
        SELECT station_id, station_name, station_type, time,
               aqhi, no2, nox, pm25, pm10, o3, so2, co
        FROM air_quality_hourly
        WHERE station_name LIKE ?
          AND time >= ?
          AND time <= ?
        ORDER BY time
    """
    cursor = conn.execute(sql, (f"%{station_name}%", start_time, end_time))
    return [dict(row) for row in cursor.fetchall()]


def get_station_info(conn: sqlite3.Connection, station_name: str) -> Optional[dict[str, Any]]:
    """获取站点信息"""
    sql = "SELECT * FROM air_quality_stations WHERE station_name LIKE ?"
    row = conn.execute(sql, (f"%{station_name}%",)).fetchone()
    return dict(row) if row else None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Air Quality Data ETL")
    parser.add_argument("--csv", default="", help="CSV 文件路径")
    parser.add_argument("--json", default="", help="JSON 文件路径")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    args = parser.parse_args()
    if args.csv:
        count = import_csv(Path(args.csv), Path(args.db))
        print(f"Imported {count} rows from CSV")
    elif args.json:
        count = import_json(Path(args.json), Path(args.db))
        print(f"Imported {count} rows from JSON")
    else:
        print("Please specify --csv or --json")


if __name__ == "__main__":
    main()
