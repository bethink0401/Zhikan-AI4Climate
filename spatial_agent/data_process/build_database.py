"""Build a portable SQLite database from prepared Spatial Agent datasets."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path


def build_database(package_dir: Path, output: Path) -> dict[str, int]:
    schema = (package_dir / "data_process" / "schema.sql").read_text(encoding="utf-8")
    stations_path = package_dir / "data" / "stations.csv"
    roads_path = package_dir / "data" / "processed" / "road_network.geojson"
    pois_path = package_dir / "data" / "processed" / "poi_sensitive.csv"
    output.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(output) as connection:
        connection.executescript(schema)
        connection.execute("DELETE FROM station")
        connection.execute("DELETE FROM road_network")
        connection.execute("DELETE FROM poi_sensitive")

        with stations_path.open(encoding="utf-8-sig", newline="") as handle:
            stations = list(csv.DictReader(handle))
        connection.executemany(
            "INSERT INTO station VALUES (:station_id,:station_name,:station_name_zh,:station_type,:lat,:lon,:address)",
            stations,
        )

        roads = json.loads(roads_path.read_text(encoding="utf-8"))["features"]
        road_rows = []
        for feature in roads:
            props = feature.get("properties", {})
            road_rows.append(
                {
                    "road_id": str(props.get("road_id") or props.get("object_id")),
                    "object_id": str(props.get("object_id") or ""),
                    "street_code": str(props.get("street_code") or ""),
                    "road_name": props.get("road_name") or "Unnamed road",
                    "road_name_zh": props.get("road_name_zh") or "",
                    "road_type": props.get("road_type") or "road_centreline",
                    "route_number": props.get("route_number") or "",
                    "travel_direction": props.get("travel_direction") or "",
                    "near_stations": json.dumps(props.get("near_stations", []), ensure_ascii=False),
                    "geometry": json.dumps(feature.get("geometry"), ensure_ascii=False, separators=(",", ":")),
                }
            )
        connection.executemany(
            """INSERT OR REPLACE INTO road_network
            (road_id,object_id,street_code,road_name,road_name_zh,road_type,route_number,travel_direction,near_stations,geometry)
            VALUES (:road_id,:object_id,:street_code,:road_name,:road_name_zh,:road_type,:route_number,:travel_direction,:near_stations,:geometry)""",
            road_rows,
        )

        with pois_path.open(encoding="utf-8-sig", newline="") as handle:
            pois = list(csv.DictReader(handle))
        connection.executemany(
            """INSERT INTO poi_sensitive
            (poi_id,poi_type,name,name_en,name_zh,lat,lon,district,district_zh,address,address_zh)
            VALUES (:poi_id,:poi_type,:name,:name_en,:name_zh,:lat,:lon,:district,:district_zh,:address,:address_zh)""",
            pois,
        )
        connection.commit()
    return {"station": len(stations), "road_network": len(road_rows), "poi_sensitive": len(pois)}


def main() -> None:
    package_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=package_dir / "data" / "processed" / "spatial.db")
    args = parser.parse_args()
    print(json.dumps(build_database(package_dir, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
