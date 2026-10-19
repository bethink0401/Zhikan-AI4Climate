# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path

conn = sqlite3.connect(str(Path(__file__).resolve().parents[1] / "data" / "traffic.db"))
for sid in ["1029", "1030", "3006", "2214", "2207"]:
    print(
        sid,
        "mix",
        conn.execute(
            "SELECT year, COUNT(*) FROM traffic_vehicle_mix WHERE atc_station_id=? GROUP BY year",
            (sid,),
        ).fetchall(),
        "meta",
        conn.execute(
            "SELECT year, road_link FROM traffic_station_meta WHERE atc_station_id=?",
            (sid,),
        ).fetchall()[:3],
    )
print(
    "like 1029 links",
    conn.execute(
        "SELECT DISTINCT atc_station_id, road_link FROM traffic_station_meta "
        "WHERE road_link LIKE '%HENNESSY%' OR atc_station_id IN ('1029','1030')"
    ).fetchall()[:20],
)
print(
    "mix hennessy",
    conn.execute(
        "SELECT DISTINCT year, atc_station_id, road_link FROM traffic_vehicle_mix "
        "WHERE road_link LIKE '%HENNESSY%' OR road_link LIKE '%CONNAUGHT%C%'"
    ).fetchall()[:20],
)
conn.close()
