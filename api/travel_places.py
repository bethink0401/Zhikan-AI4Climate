"""Server-owned place candidates for travel controls and named chat routes."""

from __future__ import annotations

import json
import re
import sqlite3
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
_CHAR_MAP = str.maketrans({
    "奥": "奧", "运": "運", "维": "維", "铁": "鐵", "车": "車",
    "门": "門", "湾": "灣", "广": "廣", "场": "場", "东": "東",
    "头": "頭", "黄": "黃", "医": "醫", "学": "學", "区": "區",
    "华": "華", "龙": "龍", "环": "環", "楼": "樓",
    "会": "會", "码": "碼", "马": "馬", "长": "長",
    "乐": "樂", "树": "樹", "阳": "陽", "关": "關", "济": "濟",
})


def _key(value: str) -> str:
    value = value.strip().lower().translate(_CHAR_MAP)
    for prefix in ("香港鐵路", "香港铁路", "港鐵", "港铁"):
        value = value.replace(prefix, "")
    return "".join(value.split())


@lru_cache(maxsize=1)
def _places() -> tuple[dict[str, Any], ...]:
    places: dict[str, dict[str, Any]] = {}

    def add(place_id: str, name: str, lat: Any, lon: Any,
            kind: str, detail: str = "", alias: str = "") -> None:
        try:
            latitude, longitude = float(lat), float(lon)
        except (TypeError, ValueError):
            return
        if not name or not 22.0 <= latitude <= 22.7 or not 113.8 <= longitude <= 114.6:
            return
        places[place_id] = {"id": place_id, "name": name, "lat": latitude,
                            "lon": longitude, "kind": kind, "detail": detail,
                            "alias": alias}

    case_path = ROOT / "examples" / "travel_case_output.json"
    if case_path.is_file():
        case_data = json.loads(case_path.read_text(encoding="utf-8"))
        for scenario in case_data.get("scenarios") or []:
            for case in scenario.get("cases") or []:
                for key in ("origin", "destination"):
                    point = case.get(key) or {}
                    name = str(point.get("name") or "")
                    add(f"case:{name}", name, point.get("lat"), point.get("lon"), "case", "案例地点")

    from tools.route_tools import load_rail_stations
    for station in load_rail_stations():
        add(f"rail:{station['station_id']}", station["name"], station["lat"],
            station["lon"], "rail", "港铁站")

    from api.stations import epd_stations
    for station in epd_stations():
        add(f"station:{station['station_id']}",
            station.get("station_name_zh") or station["station_name"],
            station.get("lat"), station.get("lon"), "monitor", "空气质量监测站",
            station.get("station_name") or "")

    db_path = ROOT / "spatial_agent" / "data" / "processed" / "spatial.db"
    if db_path.is_file():
        with sqlite3.connect(str(db_path)) as conn:
            for row in conn.execute(
                "SELECT poi_id, name, name_zh, lat, lon, district_zh, poi_type "
                "FROM poi_sensitive WHERE poi_type != 'rail_transit'"
            ):
                poi_id, name_en, name_zh, lat, lon, district, kind = row
                add(f"poi:{poi_id}", name_zh or name_en, lat, lon,
                    "poi", district or kind or "地标", name_en or "")
    return tuple(places.values())


def search_places(query: str, limit: int = 10) -> list[dict[str, Any]]:
    match = re.fullmatch(r"\s*(.*?)\s*[（(]\s*(.*?)\s*[）)]\s*", query)
    needle = _key(match.group(1) if match else query)
    district = _key(match.group(2)) if match else ""
    if len(needle) < 2:
        return []
    ranked = []
    for place in _places():
        names = (_key(place["name"]), _key(place["alias"]))
        if needle in names:
            rank = 0
        elif any(name.startswith(needle) for name in names):
            rank = 1
        elif any(needle in name for name in names):
            rank = 2
        elif len(needle) >= 3 and max(
            (SequenceMatcher(None, needle, name).ratio() for name in names if name),
            default=0.0,
        ) >= 0.78:
            rank = 3
        else:
            continue
        detail = _key(place["detail"])
        if district and district not in detail and detail not in district:
            continue
        ranked.append((rank, {"case": 0, "rail": 1, "monitor": 2, "poi": 3}[place["kind"]],
                       len(place["name"]), place))
    ranked.sort(key=lambda item: item[:3])
    return [{**{key: value for key, value in place.items() if key != "alias"}, "match": rank}
            for rank, _, _, place in ranked[:max(1, min(limit, 20))]]
