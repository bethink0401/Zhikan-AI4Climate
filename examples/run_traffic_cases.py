# -*- coding: utf-8 -*-
"""一键：ETL（案例站）→ 跑 Mong Kok / Causeway Bay / Central 三个样例。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.traffic_agent import TrafficAgent  # noqa: E402
from data_process.traffic import run_etl  # noqa: E402

ZIP_DEFAULT = Path(r"c:\Users\mistakez\Downloads\ATC_TRAFFIC_DATA.zip")
DB = ROOT / "data" / "traffic.db"

CASE_STATIONS = ["3006", "4204", "3015", "1029", "2214", "1030", "2207", "2208", "2209"]

TASKS = [
    {
        "task_id": "HK_MK_2023_0800",
        "station_name": "Mong Kok Roadside",
        "station_id": "mk_roadside",
        "start_time": "2023-08-15 08:00:00",
        "end_time": "2023-08-15 11:00:00",
        "lat": 22.3193,
        "lon": 114.1694,
    },
    {
        "task_id": "HK_CB_2023_0800",
        "station_name": "Causeway Bay Roadside",
        "station_id": "cb_roadside",
        "start_time": "2023-08-15 08:00:00",
        "end_time": "2023-08-15 11:00:00",
        "lat": 22.2800,
        "lon": 114.1850,
    },
    {
        "task_id": "HK_CT_2023_0800",
        "station_name": "Central Roadside",
        "station_id": "ct_roadside",
        "start_time": "2023-08-15 08:00:00",
        "end_time": "2023-08-15 11:00:00",
        "lat": 22.2819,
        "lon": 114.1580,
    },
]


def main():
    if not ZIP_DEFAULT.exists():
        raise SystemExit(f"找不到压缩包: {ZIP_DEFAULT}")

    print("=== ETL 导入案例站 (2023) ===")
    run_etl(ZIP_DEFAULT, DB, years=[2023], station_ids=CASE_STATIONS)

    agent = TrafficAgent(db_path=DB)
    outputs = []
    print("\n=== Traffic Agent 样例 ===")
    for task in TASKS:
        result = agent.run(task)
        outputs.append(result)
        print(
            f"- {task['station_name']}: status={result['status']} "
            f"pressure={result.get('result', {}).get('traffic_pressure')} "
            f"score={result.get('result', {}).get('traffic_pressure_score')}"
        )

    out = ROOT / "examples" / "traffic_agent_case_outputs.json"
    out.write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[saved] {out}")


if __name__ == "__main__":
    main()
