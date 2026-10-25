"""
监测站点清单的统一加载。

三个来源合并去重：

1. ``spatial_agent/data/stations.csv`` —— 项目登记的路边站（权威，带中英文名）；
2. ``data/station_mapping.json`` —— 补站点与 ATC 路段编号的对应；
3. ``data/air_quality.db`` 的 ``air_quality_stations`` 表 —— EPD EPICDI 实际有
   数据的全部监测站（15 个一般站 + 3 个路边站，含坐标）。

合并后一共 18 个站：路边站在前（出行/溯源以它们为主），一般站在后。
以前站点接口只返回登记表里的 3 个路边站，用户问"只有 3 个吗"——
EPD 监测网络本来就有 18 个站，空气质量库里全都逐小时存了，没理由不给。
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
STATIONS_CSV = ROOT / "spatial_agent" / "data" / "stations.csv"
STATION_MAPPING_JSON = ROOT / "data" / "station_mapping.json"
AIR_QUALITY_DB = ROOT / "data" / "air_quality.db"
TRAFFIC_DB = ROOT / "data" / "traffic.db"
#: ``data_process/map_epd_to_atc.py`` 生成的 EPD 站 → ATC 站就近映射
#: （官方 ATC 站点坐标，2 km 内最近匹配）。
ATC_PROXIMITY_JSON = ROOT / "data" / "atc_proximity_mapping.json"

#: EPD 一般站的官方中文名（环保署监测网络的标准站名）。
#: 空气质量库里只存了英文名，站点下拉要对前 3 个路边站一视同仁地显示中文。
EPD_NAME_ZH: dict[str, str] = {
    "CENTRAL/WESTERN": "中西区",
    "EASTERN": "东区",
    "KWAI CHUNG": "葵涌",
    "KWUN TONG": "观塘",
    "NORTH": "北区",
    "SOUTHERN": "南区",
    "SHAM SHUI PO": "深水埗",
    "SHATIN": "沙田",
    "TUNG CHUNG": "东涌",
    "TSEUNG KWAN O": "将军澳",
    "TAP MUN": "塔门",
    "TUEN MUN": "屯门",
    "TAI PO": "大埔",
    "TSUEN WAN": "荃湾",
    "YUEN LONG": "元朗",
}


def _load_traffic_stations() -> list[dict[str, Any]]:
    """ATC 交通监测站（运输署自动交通统计站）。

    ``traffic_station_meta`` 里登记了每一年参与统计的全部 ATC 站
    （2018–2024 合计 115 个），带所属道路与道路类型，但**没有经纬度** ——
    它们是路段统计站，不是地理点。接入清单是为了"系统里每个数据接口的
    站点都可见"：站点接口按类型标注 traffic，地图与气象不使用它们。
    """
    if not TRAFFIC_DB.is_file():
        return []
    try:
        conn = sqlite3.connect(str(TRAFFIC_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT atc_station_id, MAX(year) AS latest_year,
                      MAX(road_link) AS road_link, MAX(road_type) AS road_type
               FROM traffic_station_meta
               GROUP BY atc_station_id"""
        ).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("读取 traffic_station_meta 失败：%s", exc)
        return []

    stations: list[dict[str, Any]] = []
    for row in rows:
        stations.append(
            {
                "station_id": f"atc_{row['atc_station_id']}",
                "station_name": f"ATC {row['atc_station_id']}",
                "station_name_zh": "",
                "station_type": "traffic",
                "lat": None,
                "lon": None,
                "address": f"{row['road_link'] or ''}（{row['road_type'] or ''}）最新统计年份 {row['latest_year']}",
                "atc_station_ids": [row["atc_station_id"]],
            }
        )
    return stations


def _load_registered() -> list[dict[str, Any]]:
    """读项目登记表（stations.csv），并补上 ATC 路段映射。"""
    stations: list[dict[str, Any]] = []
    if STATIONS_CSV.is_file():
        with STATIONS_CSV.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                stations.append(
                    {
                        "station_id": row.get("station_id") or "",
                        "station_name": row.get("station_name") or "",
                        "station_name_zh": row.get("station_name_zh") or "",
                        "station_type": row.get("station_type") or "roadside",
                        "lat": float(row["lat"]) if row.get("lat") else None,
                        "lon": float(row["lon"]) if row.get("lon") else None,
                        "address": row.get("address") or "",
                        "atc_station_ids": [],
                    }
                )

    if STATION_MAPPING_JSON.is_file():
        mapping = json.loads(STATION_MAPPING_JSON.read_text(encoding="utf-8"))
        by_name = {item["station_name"]: item for item in stations}
        for name, info in (mapping.get("air_quality_to_atc") or {}).items():
            target = by_name.get(name)
            if target is None:
                stations.append(
                    {
                        "station_id": name,
                        "station_name": name,
                        "station_name_zh": "",
                        "station_type": "roadside",
                        "lat": info.get("lat"),
                        "lon": info.get("lon"),
                        "address": info.get("note") or "",
                        "atc_station_ids": list(info.get("primary_atc_station_ids") or []),
                    }
                )
                continue
            target["atc_station_ids"] = list(info.get("primary_atc_station_ids") or [])
            target["nearby_atc_station_ids"] = list(info.get("nearby_atc_station_ids") or [])
    return stations


def _load_epd_stations() -> list[dict[str, Any]]:
    """读 EPD 空气质量库里的站点登记（18 个站，含一般站坐标）。"""
    if not AIR_QUALITY_DB.is_file():
        return []
    try:
        conn = sqlite3.connect(str(AIR_QUALITY_DB))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT station_id, station_name, station_type, lat, lon, district
               FROM air_quality_stations"""
        ).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        logger.warning("读取 air_quality_stations 失败：%s", exc)
        return []

    stations: list[dict[str, Any]] = []
    for row in rows:
        stations.append(
            {
                "station_id": row["station_id"],
                "station_name": row["station_name"],
                "station_name_zh": "",
                "station_type": row["station_type"] or "general",
                "lat": row["lat"],
                "lon": row["lon"],
                "address": row["district"] or "",
                "atc_station_ids": [],
            }
        )
    return stations


def _physical_key(name: str | None) -> str:
    """归一化站名，用来识别同一个物理站。

    登记表叫 ``Causeway Bay Roadside``，EPD 库叫 ``CAUSEWAY BAY`` ——
    同一个站两个名字，直接按原字符串去重会得到 6 个"路边站"（实际 3 个）。
    统一成大写、去掉 Roadside 后缀再比。
    """
    return (name or "").upper().replace(" ROADSIDE", "").replace("_", " ").strip()


def lookup_station_coordinate(station_name: str, station_id: str = "") -> tuple[float, float] | None:
    """Resolve both sides with the same station-name rules used by the catalog."""
    wanted = _physical_key(station_name or station_id)
    if not wanted:
        return None
    for station in _load_registered() + load_stations():
        names = {_physical_key(station.get(key)) for key in
                 ("station_name", "station_name_zh", "station_id") if station.get(key)}
        names.update(name.removesuffix("路边站") for name in tuple(names))
        if wanted in names and station.get("lat") is not None and station.get("lon") is not None:
            return float(station["lat"]), float(station["lon"])
    return None


def _load_atc_proximity() -> dict[str, dict[str, Any]]:
    """读就近映射（EPD 站名 → 主/补充 ATC 站编号）。"""
    if not ATC_PROXIMITY_JSON.is_file():
        return {}
    try:
        return json.loads(ATC_PROXIMITY_JSON.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("读取 atc_proximity_mapping.json 失败：%s", exc)
        return {}


def load_stations() -> list[dict[str, Any]]:
    """合并四个来源：EPD 空气质量站（18）+ 登记表 + ATC 交通站（115）。

    物理站去重后，空气质量相关站排前面（路边站最前），ATC 交通站排最后
    （无坐标，仅作清单展示）。
    """
    merged: dict[str, dict[str, Any]] = {}
    proximity = _load_atc_proximity()

    # EPD 库的站最全（含一般站），先入；登记表随后覆盖同名项（中文名/ATC 映射更全）
    for station in _load_epd_stations() + _load_registered():
        key = _physical_key(station.get("station_name"))
        if not key:
            continue
        existing = merged.get(key)
        if existing is None:
            existing = dict(station)
            merged[key] = existing
        else:
            for field, value in station.items():
                # 空值不覆盖已有值；列表合并
                if isinstance(value, list):
                    combined = list(dict.fromkeys([*(existing.get(field) or []), *value]))
                    existing[field] = combined
                elif value and not existing.get(field):
                    existing[field] = value

        # 手工映射没登记的站，用就近映射补 ATC 编号
        # （站点名在两个文件里的写法不同：EPD 库 "MONG KOK"、登记表
        #  "Mong Kok Roadside"，就近映射用的是 EPD 库的写法）
        info = proximity.get(station.get("station_name") or "")
        if info and not existing.get("atc_station_ids"):
            existing["atc_station_ids"] = [
                *(info.get("primary_atc_station_ids") or []),
                *(info.get("nearby_atc_station_ids") or []),
            ]
            if info.get("nearest_distance_m") is not None:
                existing["atc_nearest_distance_m"] = info["nearest_distance_m"]

        # EPD 一般站补官方中文名，让站点下拉与前 3 个路边站显示一致
        if not existing.get("station_name_zh"):
            existing["station_name_zh"] = EPD_NAME_ZH.get(
                (station.get("station_name") or "").upper(), ""
            )

    # ATC 交通站没有坐标，单独挂在尾部，不参与去重
    traffic = _load_traffic_stations()

    # 空气质量站：路边在前；交通站追加在最后
    air = sorted(
        merged.values(),
        key=lambda item: (0 if item.get("station_type") == "roadside" else 1,
                          item.get("station_name") or ""),
    )
    return air + traffic


def roadside_stations() -> list[dict[str, Any]]:
    """全部路边监测站。"""
    return [item for item in load_stations() if item.get("station_type") == "roadside"]


def epd_stations() -> list[dict[str, Any]]:
    """全部 EPD 空气质量监测站（路边 + 一般，共 18 个，均有坐标）。

    出行规划用它做**全站联合输入**：气象与空气质量对每个站各取一份。
    """
    return [
        item
        for item in load_stations()
        if item.get("station_type") in ("roadside", "general") and item.get("lat") is not None
    ]
