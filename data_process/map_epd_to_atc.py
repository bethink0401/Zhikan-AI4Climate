# -*- coding: utf-8 -*-
"""
把 EPD 空气质量站就近映射到 ATC 交通统计站。

为什么需要这个脚本：
``data/station_mapping.json`` 是手工整理的，只登记了 3 个路边站的 ATC 对应。
其余 15 个 EPD 一般站没有车辆数据可用 —— 不是 ATC 库里没有数据
（2018-2024 全港 1694 个统计站都有），而是**从来没人把"哪个 EPD 站
附近有哪些 ATC 站"整理出来**。

数据源：运输署官方发布的交通统计调查站点位
``https://static.data.gov.hk/td/traffic-flow-census/ATC_STATION_PT.gml``
（CityGML，HK1980 网格坐标 EPSG:2326，单位米）。

做法：
1. 解析 GML，得到每个 ATC 站的编号与 HK1980 网格坐标；
2. HK1980 网格 → WGS84 经纬度（横轴墨卡托反算，参数为香港官方定义；
   HK80 与 WGS84 之间还有几米的椭球差异，对"就近匹配"完全无影响）；
3. 对 18 个 EPD 站，找 2 km 内最近的 ATC 站，取 1 个主站 + 至多 3 个补充站；
4. 写 ``data/atc_proximity_mapping.json``，供 tools/traffic_tools.py 与
   api/stations.py 读取。

用法：/usr/bin/python3 data_process/map_epd_to_atc.py
"""

from __future__ import annotations

import json
import math
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GML_PATH = Path("/tmp/ATC_STATION_PT.gml")
OUT_PATH = ROOT / "data" / "atc_proximity_mapping.json"
AIR_DB = ROOT / "data" / "air_quality.db"
TRAFFIC_DB = ROOT / "data" / "traffic.db"

#: 就近匹配半径（米）。EPD 一般站多在住宅/新区，ATC 站沿路布设，
#: 太近会一个都匹配不上，太远会把跨海对岸的路段算进来。
MATCH_RADIUS_M = 2000.0
#: 每个 EPD 站最多取多少个 ATC 站（1 主 + 3 补）。
MAX_PER_STATION = 4

# ── HK1980 网格 → WGS84 ────────────────────────────────────────────────
# EPSG:2326（Hong Kong 1980 Grid System）→ EPSG:4326（WGS84）。
# 用 pyproj 的权威实现：与地政总署公布的核对点
# （HK1980 方格 E 818097.267 / N 838477.970 ≈ 22.485771°N, 114.000300°E）
# 逐位一致。手写级数会差出两百多米，不要回头手写。
# 注意：本脚本是一次性数据准备工具，需要 pyproj（venv 里已装）；
# 运行时（/usr/bin/python3）只读生成的 JSON，不依赖 pyproj。

from pyproj import Transformer

_GRID_TO_WGS = Transformer.from_crs("EPSG:2326", "EPSG:4326", always_xy=True)


def hk1980_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    """HK1980 网格（米）→ WGS84（纬度, 经度）。"""
    lon, lat = _GRID_TO_WGS.transform(easting, northing)
    return lat, lon


def parse_gml() -> dict[str, dict]:
    """解析 CityGML，返回 {ATC编号: {lat, lon, feature_id}}。"""
    ns_core = "{http://www.opengis.net/citygml/2.0}"
    ns_gen = "{http://www.opengis.net/citygml/generics/2.0}"
    ns_gml = "{http://www.opengis.net/gml}"

    tree = ET.parse(str(GML_PATH))
    stations: dict[str, dict] = {}
    for member in tree.getroot().iter(f"{ns_core}cityObjectMember"):
        obj = member.find(f"{ns_gen}GenericCityObject")
        if obj is None:
            continue
        station_no = None
        feature_id = None
        for attr in obj.iter(f"{ns_gen}intAttribute"):
            name = attr.get("name")
            value = attr.find(f"{ns_gen}value")
            if value is None or value.text is None:
                continue
            if name == "ATC_STATION_NO":
                station_no = value.text.strip()
            elif name == "FEATUREID":
                feature_id = value.text.strip()
        pos = obj.find(f".//{ns_gml}pos")
        if not station_no or pos is None or not pos.text:
            continue
        easting, northing = (float(v) for v in pos.text.split()[:2])
        lat, lon = hk1980_to_wgs84(easting, northing)
        # 同一编号出现多次（双向/多灯位）取第一个即可，距离计算不受影响
        stations.setdefault(station_no, {"lat": lat, "lon": lon, "feature_id": feature_id})
    return stations


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点球面距离（米）。"""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def main() -> None:
    atc = parse_gml()
    print(f"GML 解析：{len(atc)} 个 ATC 站点")

    # 只保留 traffic.db 里真有车型数据的站
    conn = sqlite3.connect(str(TRAFFIC_DB))
    known = {row[0] for row in conn.execute("SELECT DISTINCT atc_station_id FROM traffic_vehicle_mix")}
    conn.close()
    usable = {k: v for k, v in atc.items() if k in known}
    print(f"其中 {len(usable)} 个在 traffic.db 里有车型数据")

    conn = sqlite3.connect(str(AIR_DB))
    conn.row_factory = sqlite3.Row
    epd_rows = conn.execute(
        "SELECT station_id, station_name, station_type, lat, lon FROM air_quality_stations"
    ).fetchall()
    conn.close()

    mapping: dict[str, dict] = {}
    for row in epd_rows:
        lat, lon = row["lat"], row["lon"]
        if lat is None or lon is None:
            print(f"  {row['station_name']}: 无坐标，跳过")
            continue
        ranked = sorted(
            ((no, haversine_m(lat, lon, v["lat"], v["lon"])) for no, v in usable.items()),
            key=lambda item: item[1],
        )
        within = [(no, dist) for no, dist in ranked if dist <= MATCH_RADIUS_M][:MAX_PER_STATION]
        if not within:
            mapping[row["station_name"]] = {
                "station_id": row["station_id"],
                "station_type": row["station_type"],
                "primary_atc_station_ids": [],
                "nearby_atc_station_ids": [],
                "note": f"{MATCH_RADIUS_M:.0f} m 内无 ATC 站",
            }
            print(f"  {row['station_name']}: {MATCH_RADIUS_M:.0f} m 内无 ATC 站（最近 {ranked[0][1]:.0f} m）")
            continue
        primary = [no for no, _ in within[:1]]
        nearby = [no for no, _ in within[1:]]
        mapping[row["station_name"]] = {
            "station_id": row["station_id"],
            "station_type": row["station_type"],
            "primary_atc_station_ids": primary,
            "nearby_atc_station_ids": nearby,
            "nearest_distance_m": round(within[0][1]),
            "note": "按官方 ATC 站点坐标就近匹配（data_process/map_epd_to_atc.py 生成）",
        }
        desc = ", ".join(f"{no}({dist:.0f}m)" for no, dist in within)
        print(f"  {row['station_name']}: {desc}")

    OUT_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[saved] {OUT_PATH}（{len(mapping)} 站）")


if __name__ == "__main__":
    main()
