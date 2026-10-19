# -*- coding: utf-8 -*-
"""
EPD 真实数据导入脚本
处理 EPD 下载的 CSV 格式（宽表，按污染物分列）
"""

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "air_quality.db"
SCHEMA_SQL = ROOT / "data_process" / "schema_air_quality.sql"

POLLUTANT_MAP = {
    "Sulphur Dioxide": "so2",
    "Fine Suspended Particulates": "pm25",
    "Nitrogen Dioxide": "no2",
    "Nitrogen Oxides": "nox",
    "Ozone": "o3",
    "Respirable Suspended Particulates": "pm10",
    "Carbon Monoxide": "co",
}

ROADSIDE_STATIONS = {"MONG KOK", "CAUSEWAY BAY", "CENTRAL"}


def init_db(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return conn


def import_epd_csv(csv_path: Path, db_path: Path = DEFAULT_DB) -> int:
    conn = init_db(db_path)
    count = 0
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        lines = f.readlines()
    data_start = 0
    for i, line in enumerate(lines):
        if line.startswith("DATE,"):
            data_start = i
            break
    if data_start == 0:
        print("Error: Cannot find header row")
        return 0
    header = lines[data_start].strip().split(",")
    station_names = header[3:]
    for line in lines[data_start + 1:]:
        parts = line.strip().split(",")
        if len(parts) < 4:
            continue
        date_str = parts[0]
        hour_str = parts[1]
        pollutant_name = parts[2]
        values = parts[3:]
        db_col = POLLUTANT_MAP.get(pollutant_name)
        if not db_col:
            continue
        try:
            month, day, year = date_str.split("/")
            time_str = f"{year}-{month.zfill(2)}-{day.zfill(2)} {hour_str.zfill(2)}:00:00"
        except:
            continue
        for j, station_name in enumerate(station_names):
            if j >= len(values):
                break
            val_str = values[j]
            if val_str in ("N.A.", "", "-", "N.A"):
                val = None
            else:
                try:
                    val = float(val_str)
                except:
                    val = None
            station_id = station_name.lower().replace(" ", "_")
            station_type = "roadside" if station_name.upper() in ROADSIDE_STATIONS else "general"
            existing = conn.execute(
                "SELECT id FROM air_quality_hourly WHERE station_id=? AND time=?",
                (station_id, time_str)
            ).fetchone()
            if existing:
                conn.execute(
                    f"UPDATE air_quality_hourly SET {db_col}=? WHERE id=?",
                    (val, existing[0])
                )
            else:
                conn.execute(
                    """INSERT INTO air_quality_hourly
                       (station_id, station_name, station_type, time, {col}, source)
                       VALUES (?, ?, ?, ?, ?, ?)""".format(col=db_col),
                    (station_id, station_name, station_type, time_str, val, "epd")
                )
            count += 1
    conn.commit()
    conn.close()
    return count


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python import_epd_real.py <csv_file>")
        sys.exit(1)
    csv_file = sys.argv[1]
    n = import_epd_csv(Path(csv_file))
    print(f"Imported {n} records")
