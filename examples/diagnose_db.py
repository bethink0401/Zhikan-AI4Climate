# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

conn = sqlite3.connect(str(Path(__file__).resolve().parents[1] / "data" / "traffic.db"))
print("mix_total", conn.execute("SELECT COUNT(*) FROM traffic_vehicle_mix").fetchone())
print(
    "mix_by_year",
    conn.execute(
        "SELECT year, COUNT(*), COUNT(DISTINCT atc_station_id) "
        "FROM traffic_vehicle_mix GROUP BY year ORDER BY year"
    ).fetchall(),
)
print(
    "null_or_empty_sid",
    conn.execute(
        "SELECT year, COUNT(*) FROM traffic_vehicle_mix "
        "WHERE atc_station_id IS NULL OR atc_station_id='' GROUP BY year"
    ).fetchall(),
)
print(
    "s3006",
    conn.execute(
        "SELECT year, COUNT(*) FROM traffic_vehicle_mix "
        "WHERE atc_station_id='3006' GROUP BY year ORDER BY year"
    ).fetchall(),
)
print(
    "meta_by_year",
    conn.execute(
        "SELECT year, COUNT(*) FROM traffic_station_meta GROUP BY year ORDER BY year"
    ).fetchall(),
)
print("meta_total", conn.execute("SELECT COUNT(*) FROM traffic_station_meta").fetchone())
# why distinct < stations: check if same id different links
print(
    "2023 id samples",
    conn.execute(
        "SELECT atc_station_id, COUNT(DISTINCT road_link), COUNT(*) "
        "FROM traffic_vehicle_mix WHERE year=2023 "
        "GROUP BY atc_station_id HAVING COUNT(DISTINCT road_link)>1 LIMIT 10"
    ).fetchall(),
)
print(
    "2023 unique links",
    conn.execute(
        "SELECT COUNT(DISTINCT road_link) FROM traffic_vehicle_mix WHERE year=2023"
    ).fetchone(),
)
conn.close()
