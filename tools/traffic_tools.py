# -*- coding: utf-8 -*-
"""
Traffic Agent 工具函数：车型结构、交通污染压力指数、重点车型、时段映射。
压力指数用于风险判断，不等同于精确排放贡献率。
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "traffic.db"
MAPPING_PATH = ROOT / "data" / "station_mapping.json"
#: ``data_process/map_epd_to_atc.py`` 按官方 ATC 站点坐标生成的就近映射。
#: 手工整理的 station_mapping.json 只登记了 3 个路边站，其余 EPD 一般站的
#: 车辆数据靠这份生成文件补齐（2 km 内最近的 ATC 统计站）。
PROXIMITY_PATH = ROOT / "data" / "atc_proximity_mapping.json"

# 分工表建议权重
VEHICLE_RISK_WEIGHTS = {
    "Private Car": 1.0,
    "Taxi": 1.2,
    "Private LB": 1.5,
    "PLB": 1.5,
    "Goods Vehicle Light": 2.0,
    "Goods Vehicle M&H": 4.0,
    "Non Franchised Bus": 3.0,
    "Franchised Bus SD": 3.0,
    "Franchised Bus DD": 3.0,
    "Motor Cycle": 0.5,
}

BUS_TYPES = {"Non Franchised Bus", "Franchised Bus SD", "Franchised Bus DD"}
GOODS_TYPES = {"Goods Vehicle Light", "Goods Vehicle M&H"}
LIGHT_BUS_TYPES = {"Private LB", "PLB"}


def load_station_mapping() -> dict[str, Any]:
    mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
    # 就近映射作为兜底：手工整理的条目优先（它们是按 OCC 数据有无从
    # 候选里挑过的），生成的条目只补空缺。
    if PROXIMITY_PATH.is_file():
        proximity = json.loads(PROXIMITY_PATH.read_text(encoding="utf-8"))
        curated = mapping.setdefault("air_quality_to_atc", {})
        for name, info in proximity.items():
            if name not in curated:
                curated[name] = {
                    "primary_atc_station_ids": info.get("primary_atc_station_ids") or [],
                    "nearby_atc_station_ids": info.get("nearby_atc_station_ids") or [],
                    "note": info.get("note") or "就近匹配生成",
                }
    return mapping


def resolve_station_name(name: str) -> str:
    mapping = load_station_mapping()
    if name in mapping["air_quality_to_atc"]:
        return name
    alias = mapping.get("aliases", {}).get(name)
    if alias:
        return alias
    # 模糊包含
    for k in mapping["air_quality_to_atc"]:
        if name.lower() in k.lower() or k.lower() in name.lower():
            return k
    return name


def get_atc_station_ids(station_name: str, include_nearby: bool = True) -> list[str]:
    mapping = load_station_mapping()
    key = resolve_station_name(station_name)
    info = mapping["air_quality_to_atc"].get(key)
    if not info:
        return []
    ids = list(info.get("primary_atc_station_ids", []))
    if include_nearby:
        for x in info.get("nearby_atc_station_ids", []):
            if x not in ids:
                ids.append(x)
    return ids


def hour_to_time_slot(hour: int) -> str:
    h = int(hour) % 24
    return f"{h:02d}00-{(h + 1) % 24:02d}00"


def parse_time_range(start_time: str, end_time: str) -> list[str]:
    """根据起止时间生成 ATC 小时时段列表（左闭右开）。"""
    start = datetime.strptime(start_time[:19], "%Y-%m-%d %H:%M:%S")
    end = datetime.strptime(end_time[:19], "%Y-%m-%d %H:%M:%S")
    if end <= start:
        return [hour_to_time_slot(start.hour)]
    slots = []
    cur = start.replace(minute=0, second=0, microsecond=0)
    while cur < end:
        slots.append(hour_to_time_slot(cur.hour))
        # 下一小时
        nh = cur.hour + 1
        if nh >= 24:
            break
        cur = cur.replace(hour=nh)
    return slots or [hour_to_time_slot(start.hour)]


def open_db(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    if not Path(db_path).exists():
        raise FileNotFoundError(f"交通库不存在，请先运行 ETL: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def query_vehicle_mix(
    conn: sqlite3.Connection,
    atc_station_ids: list[str],
    year: int,
    time_slots: list[str],
    day_type: str = "Mon-Fri",
) -> list[dict[str, Any]]:
    if not atc_station_ids or not time_slots:
        return []
    placeholders_s = ",".join("?" * len(atc_station_ids))
    placeholders_t = ",".join("?" * len(time_slots))
    sql = f"""
        SELECT year, atc_station_id, road_link, day_type, time_slot,
               vehicle_type, proportion, occupancy, source_file
        FROM traffic_vehicle_mix
        WHERE year = ?
          AND atc_station_id IN ({placeholders_s})
          AND time_slot IN ({placeholders_t})
          AND day_type = ?
          AND time_slot != '16hours'
    """
    params = [year, *atc_station_ids, *time_slots, day_type]
    cur = conn.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def aggregate_proportions(rows: list[dict[str, Any]]) -> dict[str, float]:
    """多站多时段：对同一车型的 proportion(%) 取平均。"""
    buckets: dict[str, list[float]] = {}
    for r in rows:
        vt = r["vehicle_type"]
        pro = r.get("proportion")
        if pro is None:
            continue
        buckets.setdefault(vt, []).append(float(pro))
    return {k: sum(v) / len(v) for k, v in buckets.items() if v}


def proportions_by_slot(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """按 time_slot 聚合车型比例（多站同槽取平均）。"""
    tmp: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        slot = r.get("time_slot") or ""
        vt = r["vehicle_type"]
        pro = r.get("proportion")
        if pro is None or not slot:
            continue
        tmp.setdefault(slot, {}).setdefault(vt, []).append(float(pro))
    out: dict[str, dict[str, float]] = {}
    for slot, mp in tmp.items():
        out[slot] = {k: sum(v) / len(v) for k, v in mp.items()}
    return out


def peak_pressure_over_slots(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, float], str]:
    """
    风险判断取窗口内压力最高的小时段（避免多时段平均冲淡早高峰），
    同时返回该时段车型比例，便于解释。
    """
    by_slot = proportions_by_slot(rows)
    if not by_slot:
        empty = compute_traffic_pressure({})
        return empty, {}, ""
    best_slot = ""
    best_pressure: dict[str, Any] = {}
    best_props: dict[str, float] = {}
    best_score = -1.0
    for slot, props in by_slot.items():
        p = compute_traffic_pressure(props)
        if p["traffic_pressure_score"] > best_score:
            best_score = p["traffic_pressure_score"]
            best_slot = slot
            best_pressure = p
            best_props = props
    return best_pressure, best_props, best_slot


def compute_traffic_pressure(proportions_pct: dict[str, float]) -> dict[str, Any]:
    """
    压力指数 = Σ (比例/100 × 权重)
    归一化分数 = min(1, 指数 / 2.8)
    （典型城区车队指数约 1.2–2.5；除以 2.8 使高峰交通源场景更容易进入 high）
    """
    raw = 0.0
    detail = {}
    for vt, pct in proportions_pct.items():
        w = VEHICLE_RISK_WEIGHTS.get(vt, 1.0)
        contrib = (float(pct) / 100.0) * w
        detail[vt] = {"proportion_pct": round(float(pct), 2), "weight": w, "contrib": round(contrib, 4)}
        raw += contrib

    score = max(0.0, min(1.0, raw / 2.8))
    if score >= 0.85:
        level = "very_high"
    elif score >= 0.60:
        level = "high"
    elif score >= 0.40:
        level = "medium"
    else:
        level = "low"

    return {
        "traffic_pressure_index": round(raw, 4),
        "traffic_pressure_score": round(score, 4),
        "traffic_pressure": "high" if level in ("high", "very_high") else level,
        "traffic_pressure_level": level,
        "weight_detail": detail,
    }


def summarize_vehicle_structure(proportions_pct: dict[str, float]) -> dict[str, Any]:
    items = sorted(proportions_pct.items(), key=lambda x: x[1], reverse=True)
    dominant = [k for k, _ in items[:3]]

    bus_ratio = sum(proportions_pct.get(t, 0.0) for t in BUS_TYPES) / 100.0
    goods_ratio = sum(proportions_pct.get(t, 0.0) for t in GOODS_TYPES) / 100.0
    taxi_ratio = proportions_pct.get("Taxi", 0.0) / 100.0
    private_ratio = proportions_pct.get("Private Car", 0.0) / 100.0
    plb_ratio = sum(proportions_pct.get(t, 0.0) for t in LIGHT_BUS_TYPES) / 100.0

    heavy = goods_ratio + bus_ratio
    if heavy >= 0.35:
        heavy_signal = "high"
    elif heavy >= 0.20:
        heavy_signal = "medium"
    else:
        heavy_signal = "low"

    focus = []
    if bus_ratio >= 0.10:
        focus.append("Bus")
    if goods_ratio >= 0.10:
        focus.append("Goods Vehicle")
    if taxi_ratio >= 0.15:
        focus.append("Taxi")
    if private_ratio >= 0.25:
        focus.append("Private Car")
    if plb_ratio >= 0.05:
        focus.append("PLB")

    return {
        "dominant_vehicle_types": dominant,
        "bus_ratio": round(bus_ratio, 4),
        "goods_vehicle_ratio": round(goods_ratio, 4),
        "taxi_ratio": round(taxi_ratio, 4),
        "private_car_ratio": round(private_ratio, 4),
        "plb_ratio": round(plb_ratio, 4),
        "heavy_vehicle_signal": heavy_signal,
        "vehicle_focus": focus,
        "proportions_pct": {k: round(v, 2) for k, v in items},
    }


def build_evidence(
    station_name: str,
    time_slots: list[str],
    structure: dict[str, Any],
    pressure: dict[str, Any],
    road_links: list[str],
) -> list[str]:
    evidence = []
    slot_txt = ", ".join(time_slots)
    if any(s.startswith("07") or s.startswith("08") or s.startswith("09") for s in time_slots):
        evidence.append(
            f"Morning peak period ({slot_txt}) near {station_name} shows elevated traffic-related vehicle mix."
        )
    else:
        evidence.append(f"Selected period ({slot_txt}) vehicle composition analyzed for {station_name}.")

    dom = ", ".join(structure.get("dominant_vehicle_types", [])[:3])
    if dom:
        evidence.append(f"Dominant vehicle types: {dom}.")

    if structure.get("bus_ratio", 0) >= 0.1 or structure.get("goods_vehicle_ratio", 0) >= 0.1:
        evidence.append(
            "Bus and goods vehicle proportions increase traffic-related NOx risk."
        )
    if structure.get("taxi_ratio", 0) >= 0.15:
        evidence.append("Taxi proportion is relatively high during the selected window.")

    evidence.append(
        f"Traffic pollution pressure score={pressure['traffic_pressure_score']} "
        f"(level={pressure.get('traffic_pressure_level')}), for risk screening only."
    )
    if road_links:
        evidence.append("ATC links used: " + "; ".join(road_links[:3]))
    return evidence


def estimate_year_from_task(start_time: str, available_years: list[int]) -> int:
    y = int(start_time[:4])
    if y in available_years:
        return y
    if available_years:
        # 选最接近的已入库年份
        return min(available_years, key=lambda a: abs(a - y))
    return y
