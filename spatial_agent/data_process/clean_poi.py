"""Clean iGeoCom POIs and extract sensitive facilities.

Input coordinates use Hong Kong 1980 Grid (EPSG:2326). Output CSV and GeoJSON
use WGS84 (EPSG:4326). The source file is never modified.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from pyproj import Transformer
except ImportError as exc:  # pragma: no cover - installation guidance
    raise SystemExit("pyproj is required: pip install 'pyproj>=3.6,<4'") from exc

SENSITIVE_RULES: dict[str, set[str] | None] = {
    "SCH": None,  # kindergarten, primary/secondary schools, colleges, training institutes
    "HNC": None,  # hospitals, clinics, elderly homes, rehabilitation centres
    "TRS": {"MTA", "RSN", "LRA", "HSA", "HSN", "PTA"},  # rail/MTR/light rail access and stations
    "COM": {"CMC"},  # community centres/halls and elderly community centres
    "CUF": {"SSR"},  # social/community service facilities
}

TYPE_LABELS = {
    "SCH": "school",
    "HNC": "healthcare",
    "TRS": "rail_transit",
    "COM": "community_centre",
    "CUF": "community_service",
}

OUTPUT_FIELDS = [
    "poi_id",
    "poi_type",
    "name",
    "name_en",
    "name_zh",
    "lat",
    "lon",
    "district",
    "district_zh",
    "region",
    "address",
    "address_zh",
    "source_class",
    "source_type",
    "source_subcat",
    "revision_date",
]


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\ufeff", "").strip().split())


def classify_sensitive(row: dict[str, str]) -> str | None:
    class_code = clean_text(row.get("CLASS")).upper()
    type_code = clean_text(row.get("TYPE")).upper()
    allowed_types = SENSITIVE_RULES.get(class_code)
    if class_code not in SENSITIVE_RULES:
        return None
    if allowed_types is not None and type_code not in allowed_types:
        return None
    return TYPE_LABELS[class_code]


def clean_poi(input_csv: Path, output_csv: Path, output_geojson: Path, audit_json: Path) -> dict[str, Any]:
    transformer = Transformer.from_crs("EPSG:2326", "EPSG:4326", always_xy=True)
    seen_ids: set[str] = set()
    seen_facilities: set[tuple[str, str, float, float]] = set()
    cleaned: list[dict[str, Any]] = []
    stats: Counter[str] = Counter()

    with input_csv.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"GEONAMEID", "CLASS", "TYPE", "EASTING", "NORTHING"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing required columns: {sorted(missing)}")
        for row in reader:
            stats["source_rows"] += 1
            poi_type = classify_sensitive(row)
            if poi_type is None:
                stats["non_sensitive_rows"] += 1
                continue
            poi_id = clean_text(row.get("GEONAMEID"))
            if not poi_id or poi_id in seen_ids:
                stats["duplicate_or_missing_id"] += 1
                continue
            try:
                easting = float(clean_text(row.get("EASTING")))
                northing = float(clean_text(row.get("NORTHING")))
                lon, lat = transformer.transform(easting, northing)
            except (TypeError, ValueError):
                stats["invalid_coordinates"] += 1
                continue
            if not (113.8 <= lon <= 114.5 and 22.1 <= lat <= 22.6):
                stats["coordinates_outside_hong_kong"] += 1
                continue

            name_en = clean_text(row.get("ENGLISHNAME"))
            name_zh = clean_text(row.get("CHINESENAME"))
            facility_key = (
                poi_type,
                (name_zh or name_en).casefold(),
                round(easting, 1),
                round(northing, 1),
            )
            if facility_key in seen_facilities:
                stats["duplicate_facility"] += 1
                continue
            record = {
                "poi_id": poi_id,
                "poi_type": poi_type,
                "name": name_en or name_zh or f"POI {poi_id}",
                "name_en": name_en,
                "name_zh": name_zh,
                "lat": round(lat, 7),
                "lon": round(lon, 7),
                "district": clean_text(row.get("E_DISTRICT")),
                "district_zh": clean_text(row.get("C_DISTRICT")),
                "region": clean_text(row.get("E_REGION")),
                "address": clean_text(row.get("E_ADDRESS")),
                "address_zh": clean_text(row.get("C_ADDRESS")),
                "source_class": clean_text(row.get("CLASS")).upper(),
                "source_type": clean_text(row.get("TYPE")).upper(),
                "source_subcat": clean_text(row.get("SUBCAT")).upper(),
                "revision_date": clean_text(row.get("REV_DATE")),
            }
            seen_ids.add(poi_id)
            seen_facilities.add(facility_key)
            cleaned.append(record)
            stats[f"kept_{poi_type}"] += 1

    cleaned.sort(key=lambda item: (item["poi_type"], item["district"], item["name"], item["poi_id"]))
    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(cleaned)

    features = [
        {
            "type": "Feature",
            "id": record["poi_id"],
            "geometry": {"type": "Point", "coordinates": [record["lon"], record["lat"]]},
            "properties": {key: value for key, value in record.items() if key not in {"lon", "lat"}},
        }
        for record in cleaned
    ]
    geojson = {
        "type": "FeatureCollection",
        "name": "poi_sensitive",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    output_geojson.write_text(json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    stats["kept_rows"] = len(cleaned)
    audit = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_file": str(input_csv),
        "source_crs": "EPSG:2326",
        "output_crs": "EPSG:4326",
        "classification_rules": {
            key: sorted(value) if value is not None else "all types" for key, value in SENSITIVE_RULES.items()
        },
        "statistics": dict(sorted(stats.items())),
    }
    audit_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    return audit


def main() -> None:
    package_dir = Path(__file__).resolve().parents[1]
    repo_root = package_dir.parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=repo_root / "task" / "data" / "POI" / "iGeoCOM_POI.csv")
    parser.add_argument("--output-dir", type=Path, default=package_dir / "data" / "processed")
    args = parser.parse_args()
    audit = clean_poi(
        args.input,
        args.output_dir / "poi_sensitive.csv",
        args.output_dir / "poi_sensitive.geojson",
        args.output_dir / "poi_cleaning_audit.json",
    )
    print(json.dumps(audit["statistics"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
