# -*- coding: utf-8 -*-
"""
Traffic Agent — 交通结构与交通污染压力分析

输入：全组统一 task JSON
输出：全组统一 Agent 结果 JSON
可独立运行：python agents/traffic_agent.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.traffic_tools import (  # noqa: E402
    DEFAULT_DB,
    build_evidence,
    estimate_year_from_task,
    get_atc_station_ids,
    open_db,
    parse_time_range,
    peak_pressure_over_slots,
    query_vehicle_mix,
    resolve_station_name,
    summarize_vehicle_structure,
)


def _window_covers_non_weekday(start_time: str, end_time: str) -> bool:
    """窗口内是否包含周六/周日（解析失败时返回 False，不阻断原流程）。"""
    import datetime as dt

    try:
        start = dt.datetime.fromisoformat(str(start_time).strip())
        end = dt.datetime.fromisoformat(str(end_time).strip())
    except (ValueError, TypeError):
        return False
    if end < start:
        start, end = end, start
    day = start.date()
    while day <= end.date():
        if day.weekday() >= 5:
            return True
        day += dt.timedelta(days=1)
    return False



class TrafficAgent:
    name = "TrafficAgent"

    def __init__(self, db_path: Path = DEFAULT_DB):
        self.db_path = Path(db_path)

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        station_name = resolve_station_name(task.get("station_name") or "")
        start_time = task.get("start_time") or ""
        end_time = task.get("end_time") or start_time
        task_id = task.get("task_id", "")

        try:
            conn = open_db(self.db_path)
        except FileNotFoundError as e:
            return self._fail(str(e), created_at, missing_rate=1.0)

        years = [
            r[0]
            for r in conn.execute("SELECT DISTINCT year FROM traffic_vehicle_mix ORDER BY year").fetchall()
        ]
        if not years:
            conn.close()
            return self._fail("traffic_vehicle_mix 为空，请先运行 data_process/traffic.py", created_at, 1.0)

        year = estimate_year_from_task(start_time, years)
        atc_ids = get_atc_station_ids(station_name, include_nearby=False)
        if not atc_ids:
            # 允许直接传 ATC 站号
            sid = str(task.get("station_id") or "").strip()
            if sid.isdigit():
                atc_ids = [sid]
            else:
                conn.close()
                return self._fail(
                    f"无法映射站点到 ATC：{station_name}",
                    created_at,
                    1.0,
                    status="warning",
                )

        time_slots = parse_time_range(start_time, end_time)

        # ATC 车型结构数据仅覆盖 Mon-Fri（day_type 固定）。窗口含周六/周日时，
        # 库里查到的"数据"其实是工作日均值——不能当作当日证据使用，如实降级。
        if _window_covers_non_weekday(start_time, end_time):
            return {
                "agent_name": self.name,
                "status": "warning",
                "result": {
                    "traffic_pressure": "unknown",
                    "traffic_pressure_score": 0.0,
                    "dominant_vehicle_types": [],
                    "heavy_vehicle_signal": "unknown",
                    "bus_ratio": 0.0,
                    "goods_vehicle_ratio": 0.0,
                },
                "evidence": [
                    f"任务窗口 {start_time} ~ {end_time} 含周六/周日，"
                    f"ATC 车型结构数据仅覆盖 Mon-Fri，本窗口无当日交通证据。"
                ],
                "confidence": 0.2,
                "data_quality": {
                    "missing_rate": 1.0,
                    "note": "窗口含非工作日，ATC 数据不覆盖；上游不得据此判定交通源。",
                },
                "created_at": created_at,
                "meta": {
                    "task_id": task_id,
                    "station_name": station_name,
                    "year_used": year,
                    "atc_station_ids": atc_ids,
                    "time_slots": time_slots,
                },
            }

        rows = query_vehicle_mix(conn, atc_ids, year, time_slots)

        # 主站无数据时扩展到 nearby
        used_nearby = False
        if not rows:
            nearby = get_atc_station_ids(station_name, include_nearby=True)
            rows = query_vehicle_mix(conn, nearby, year, time_slots)
            used_nearby = bool(rows)
            atc_ids = nearby if rows else atc_ids

        road_links = sorted({r["road_link"] for r in rows if r.get("road_link")})
        conn.close()

        if not rows:
            return {
                "agent_name": self.name,
                "status": "warning",
                "result": {
                    "traffic_pressure": "unknown",
                    "traffic_pressure_score": 0.0,
                    "dominant_vehicle_types": [],
                    "heavy_vehicle_signal": "unknown",
                    "bus_ratio": 0.0,
                    "goods_vehicle_ratio": 0.0,
                },
                "evidence": [
                    f"No ATC vehicle-mix rows for station={station_name}, year={year}, slots={time_slots}."
                ],
                "confidence": 0.2,
                "data_quality": {
                    "missing_rate": 1.0,
                    "note": "数据缺失，请检查 ETL 是否导入对应站点/年份；勿直接报错中断上游。",
                },
                "created_at": created_at,
                "meta": {
                    "task_id": task_id,
                    "year_used": year,
                    "atc_station_ids": atc_ids,
                    "time_slots": time_slots,
                },
            }

        pressure, proportions, peak_slot = peak_pressure_over_slots(rows)
        structure = summarize_vehicle_structure(proportions)
        evidence = build_evidence(
            station_name,
            time_slots,
            structure,
            pressure,
            road_links,
        )
        if peak_slot:
            evidence.insert(
                1,
                f"Peak traffic-pressure hour within window: {peak_slot}.",
            )

        # 简单置信度：有主站数据更高
        confidence = 0.78 if not used_nearby else 0.68
        if len(time_slots) >= 2:
            confidence = min(0.9, confidence + 0.04)

        note = f"使用 ATC {year} 年 Mon-Fri 车型结构；压力指数仅供风险判断。"
        if used_nearby:
            note += " 主站无匹配时已扩展至周边 ATC 站。"
        if year != int(start_time[:4]):
            note += f" 请求年份与库内不一致，已回退到 {year}。"

        return {
            "agent_name": self.name,
            "status": "success",
            "result": {
                "traffic_pressure": pressure["traffic_pressure"],
                "traffic_pressure_score": pressure["traffic_pressure_score"],
                "traffic_pressure_index": pressure["traffic_pressure_index"],
                "traffic_pressure_level": pressure["traffic_pressure_level"],
                "dominant_vehicle_types": structure["dominant_vehicle_types"],
                "heavy_vehicle_signal": structure["heavy_vehicle_signal"],
                "bus_ratio": structure["bus_ratio"],
                "goods_vehicle_ratio": structure["goods_vehicle_ratio"],
                "taxi_ratio": structure["taxi_ratio"],
                "private_car_ratio": structure["private_car_ratio"],
                "vehicle_focus": structure["vehicle_focus"],
                "proportions_pct": structure["proportions_pct"],
                "road_links": road_links,
            },
            "evidence": evidence,
            "confidence": round(confidence, 2),
            "data_quality": {
                "missing_rate": 0.0,
                "note": note,
            },
            "created_at": created_at,
            "meta": {
                "task_id": task_id,
                "station_name": station_name,
                "year_used": year,
                "atc_station_ids": atc_ids,
                "time_slots": time_slots,
                "peak_time_slot": peak_slot,
                "row_count": len(rows),
            },
        }

    def _fail(
        self,
        note: str,
        created_at: str,
        missing_rate: float,
        status: str = "warning",
    ) -> dict[str, Any]:
        return {
            "agent_name": self.name,
            "status": status,
            "result": {},
            "evidence": [note],
            "confidence": 0.1,
            "data_quality": {"missing_rate": missing_rate, "note": note},
            "created_at": created_at,
        }


def demo_task_mong_kok() -> dict[str, Any]:
    return {
        "task_id": "HK_MK_2023_0800",
        "station_name": "Mong Kok Roadside",
        "station_id": "mk_roadside",
        "start_time": "2023-08-15 08:00:00",
        "end_time": "2023-08-15 11:00:00",
        "lat": 22.3193,
        "lon": 114.1694,
    }


def main():
    parser = argparse.ArgumentParser(description="Traffic Agent")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--task-json", default="", help="任务 JSON 文件路径")
    parser.add_argument("--demo", action="store_true", help="运行 Mong Kok 早高峰示例")
    args = parser.parse_args()

    if args.task_json:
        task = json.loads(Path(args.task_json).read_text(encoding="utf-8"))
    else:
        task = demo_task_mong_kok()

    agent = TrafficAgent(db_path=Path(args.db))
    result = agent.run(task)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    out = ROOT / "examples" / "traffic_agent_last_output.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[saved] {out}")


if __name__ == "__main__":
    main()
