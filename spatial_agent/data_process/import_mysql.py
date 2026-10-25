"""Create a separate MySQL database and import prepared Spatial Agent data.

The password is requested interactively and is never stored in this file.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import pymysql

IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


def batched(rows: list[dict[str, Any]], size: int = 500) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_schema(connection: Any) -> None:
    statements = [
        """
        CREATE TABLE station (
            station_id VARCHAR(16) PRIMARY KEY,
            station_name VARCHAR(128) NOT NULL,
            station_name_zh VARCHAR(128),
            station_type VARCHAR(32) NOT NULL,
            lat DECIMAL(10,7) NOT NULL,
            lon DECIMAL(10,7) NOT NULL,
            address VARCHAR(512),
            INDEX idx_station_lat_lon (lat, lon)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE road_network (
            road_id VARCHAR(64) PRIMARY KEY,
            object_id VARCHAR(64),
            street_code VARCHAR(32),
            road_name VARCHAR(255) NOT NULL,
            road_name_zh VARCHAR(255),
            road_type VARCHAR(64),
            route_number VARCHAR(64),
            travel_direction VARCHAR(32),
            near_stations VARCHAR(128),
            source_updated_at VARCHAR(64),
            geometry_geojson LONGTEXT NOT NULL,
            INDEX idx_road_street_code (street_code),
            INDEX idx_road_name (road_name),
            INDEX idx_road_name_zh (road_name_zh)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE poi_sensitive (
            poi_id VARCHAR(64) PRIMARY KEY,
            poi_type VARCHAR(64) NOT NULL,
            name VARCHAR(512) NOT NULL,
            name_en VARCHAR(512),
            name_zh VARCHAR(512),
            lat DECIMAL(10,7) NOT NULL,
            lon DECIMAL(10,7) NOT NULL,
            district VARCHAR(128),
            district_zh VARCHAR(128),
            region VARCHAR(64),
            address TEXT,
            address_zh TEXT,
            source_class VARCHAR(32),
            source_type VARCHAR(32),
            source_subcat VARCHAR(32),
            revision_date VARCHAR(32),
            INDEX idx_poi_type (poi_type),
            INDEX idx_poi_lat_lon (lat, lon),
            INDEX idx_poi_district (district)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE data_import_log (
            import_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            imported_at DATETIME NOT NULL,
            source_name VARCHAR(128) NOT NULL,
            source_path VARCHAR(1024) NOT NULL,
            source_sha256 CHAR(64) NOT NULL,
            imported_rows INT NOT NULL,
            details_json LONGTEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    ]
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


def import_data(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    package_dir: Path,
) -> dict[str, int]:
    if not IDENTIFIER.fullmatch(database):
        raise ValueError("Database name must contain only letters, digits and underscores")

    server = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=15,
        read_timeout=30,
        write_timeout=30,
    )
    try:
        with server.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s",
                (database,),
            )
            if cursor.fetchone()[0]:
                raise RuntimeError(
                    f"Database {database!r} already exists; choose another name to avoid mixing data"
                )
            cursor.execute(
                f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        server.close()

    connection = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=15,
        read_timeout=60,
        write_timeout=60,
    )
    stations_path = package_dir / "data" / "stations.csv"
    roads_path = package_dir / "data" / "processed" / "road_network.geojson"
    pois_path = package_dir / "data" / "processed" / "poi_sensitive.csv"
    poi_audit_path = package_dir / "data" / "processed" / "poi_cleaning_audit.json"
    road_audit_path = package_dir / "data" / "processed" / "road_import_audit.json"

    try:
        create_schema(connection)
        with stations_path.open(encoding="utf-8-sig", newline="") as handle:
            stations = list(csv.DictReader(handle))
        with roads_path.open(encoding="utf-8") as handle:
            road_features = json.load(handle)["features"]
        with pois_path.open(encoding="utf-8-sig", newline="") as handle:
            pois = list(csv.DictReader(handle))

        station_sql = """
            INSERT INTO station
            (station_id,station_name,station_name_zh,station_type,lat,lon,address)
            VALUES
            (%(station_id)s,%(station_name)s,%(station_name_zh)s,%(station_type)s,
             %(lat)s,%(lon)s,%(address)s)
        """
        road_sql = """
            INSERT INTO road_network
            (road_id,object_id,street_code,road_name,road_name_zh,road_type,
             route_number,travel_direction,near_stations,source_updated_at,geometry_geojson)
            VALUES
            (%(road_id)s,%(object_id)s,%(street_code)s,%(road_name)s,%(road_name_zh)s,
             %(road_type)s,%(route_number)s,%(travel_direction)s,%(near_stations)s,
             %(source_updated_at)s,%(geometry_geojson)s)
        """
        poi_sql = """
            INSERT INTO poi_sensitive
            (poi_id,poi_type,name,name_en,name_zh,lat,lon,district,district_zh,region,
             address,address_zh,source_class,source_type,source_subcat,revision_date)
            VALUES
            (%(poi_id)s,%(poi_type)s,%(name)s,%(name_en)s,%(name_zh)s,%(lat)s,%(lon)s,
             %(district)s,%(district_zh)s,%(region)s,%(address)s,%(address_zh)s,
             %(source_class)s,%(source_type)s,%(source_subcat)s,%(revision_date)s)
        """
        road_rows = []
        for feature in road_features:
            props = feature.get("properties", {})
            road_rows.append(
                {
                    "road_id": str(props.get("road_id") or props.get("object_id") or ""),
                    "object_id": str(props.get("object_id") or ""),
                    "street_code": str(props.get("street_code") or ""),
                    "road_name": props.get("road_name") or "Unnamed road",
                    "road_name_zh": props.get("road_name_zh") or "",
                    "road_type": props.get("road_type") or "road_centreline",
                    "route_number": str(props.get("route_number") or ""),
                    "travel_direction": str(props.get("travel_direction") or ""),
                    "near_stations": json.dumps(props.get("near_stations", []), ensure_ascii=False),
                    "source_updated_at": str(props.get("source_updated_at") or ""),
                    "geometry_geojson": json.dumps(
                        feature.get("geometry"), ensure_ascii=False, separators=(",", ":")
                    ),
                }
            )

        with connection.cursor() as cursor:
            cursor.executemany(station_sql, stations)
            for batch in batched(road_rows):
                cursor.executemany(road_sql, batch)
            for batch in batched(pois):
                cursor.executemany(poi_sql, batch)
            import_logs = [
                (
                    datetime.now(),
                    "station",
                    str(stations_path),
                    sha256(stations_path),
                    len(stations),
                    "{}",
                ),
                (
                    datetime.now(),
                    "road_network",
                    str(roads_path),
                    sha256(roads_path),
                    len(road_rows),
                    road_audit_path.read_text(encoding="utf-8"),
                ),
                (
                    datetime.now(),
                    "poi_sensitive",
                    str(pois_path),
                    sha256(pois_path),
                    len(pois),
                    poi_audit_path.read_text(encoding="utf-8"),
                ),
            ]
            cursor.executemany(
                """
                INSERT INTO data_import_log
                (imported_at,source_name,source_path,source_sha256,imported_rows,details_json)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                import_logs,
            )
        connection.commit()

        counts: dict[str, int] = {}
        with connection.cursor() as cursor:
            for table in ("station", "road_network", "poi_sensitive", "data_import_log"):
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                counts[table] = int(cursor.fetchone()[0])
        return counts
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def verify_database(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> dict[str, Any]:
    connection = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        connect_timeout=15,
        read_timeout=30,
    )
    try:
        result: dict[str, Any] = {"database": database, "counts": {}}
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION()")
            result["server_version"] = cursor.fetchone()[0]
            for table in ("station", "road_network", "poi_sensitive", "data_import_log"):
                cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                result["counts"][table] = int(cursor.fetchone()[0])
            cursor.execute(
                """
                SELECT COUNT(*) FROM road_network
                WHERE geometry_geojson IS NULL OR geometry_geojson = ''
                """
            )
            result["roads_missing_geometry"] = int(cursor.fetchone()[0])
            cursor.execute(
                """
                SELECT COUNT(*) FROM poi_sensitive
                WHERE lat IS NULL OR lon IS NULL
                """
            )
            result["pois_missing_coordinates"] = int(cursor.fetchone()[0])
        return result
    finally:
        connection.close()


def main() -> None:
    package_dir = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", required=True)
    parser.add_argument("--database", default="spatial_agent_20260723")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    password = getpass.getpass("MySQL password: ")
    if args.verify_only:
        print(
            json.dumps(
                verify_database(
                    args.host,
                    args.port,
                    args.user,
                    password,
                    args.database,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    counts = import_data(
        args.host,
        args.port,
        args.user,
        password,
        args.database,
        package_dir,
    )
    print(json.dumps({"database": args.database, "counts": counts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
