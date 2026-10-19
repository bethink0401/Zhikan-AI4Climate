# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parents[1] / "data" / "traffic.db"
raw = Path(__file__).resolve().parents[1] / "data" / "raw_atc" / "ATC_TRAFFIC_DATA"
print("db_exists", db.exists())
if db.exists():
    conn = sqlite3.connect(str(db))
    print("mix_by_year", conn.execute(
        "SELECT year, COUNT(*), COUNT(DISTINCT atc_station_id) FROM traffic_vehicle_mix GROUP BY year ORDER BY year"
    ).fetchall())
    print("meta_by_year", conn.execute(
        "SELECT year, COUNT(*) FROM traffic_station_meta GROUP BY year ORDER BY year"
    ).fetchall())
    print("volume_count", conn.execute("SELECT COUNT(*) FROM traffic_volume").fetchone())
    conn.close()
if raw.exists():
    years = sorted(p.name for p in raw.iterdir() if p.is_dir() and p.name.isdigit())
    print("extracted_dirs", years)
    zips = sorted(p.name for p in raw.glob("*.zip"))
    print("year_zips_present", zips)
