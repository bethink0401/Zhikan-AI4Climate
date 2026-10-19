# -*- coding: utf-8 -*-
"""
Coordinator 最小流程（第9天联调）

按分工表调用顺序：
Air → Meteo → Traffic → Spatial → Source → Decision → Report

当前实际接入所有真实Agent。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.air_quality_agent import AirQualityAgent  # noqa: E402
from agents.meteorology_agent import run as meteo_run  # noqa: E402
from agents.spatial_agent import SpatialAgent  # noqa: E402
from agents.source_agent import SourceAgent  # noqa: E402
from agents.traffic_agent import TrafficAgent  # noqa: E402
from agents.decison_agent import run as decision_run  # noqa: E402
from agents.report_agent import ReportAgent  # noqa: E402


class CoordinatorMin:
    def __init__(self, db_path: Optional[Path] = None):
        self.air = AirQualityAgent(db_path=db_path) if db_path else AirQualityAgent()
        self.spatial = SpatialAgent()
        self.traffic = TrafficAgent(db_path=db_path) if db_path else TrafficAgent()
        self.source = SourceAgent()
        self.report = ReportAgent()

    def analyze(self, task: dict[str, Any]) -> dict[str, Any]:
        # 1: Air Quality — 真实实现
        air_result = self.air.run(task)

        # 2: Meteorology — 真实实现
        meteo_result = meteo_run(task)

        # 3: Traffic — 真实实现
        traffic_result = self.traffic.run(task)

        # 4: Spatial — 真实实现，传递气象结果
        spatial_result = self.spatial.run(task, meteo_result)

        # 5: Source — 真实实现
        source_result = self.source.run(
            task,
            air_result=air_result,
            meteo_result=meteo_result,
            traffic_result=traffic_result,
            spatial_result=spatial_result,
        )

        # 6: Decision — 真实实现
        decision_result = decision_run(
            source_result,
            spatial_result,
            traffic_result,
            meteo_result,
            task,
            air_result,
        )

        # 7: Report — 真实实现
        report_result = self.report.run({
            "task": task,
            "air_quality": air_result,
            "meteorology": meteo_result,
            "traffic": traffic_result,
            "spatial": spatial_result,
            "source": source_result,
            "decision": decision_result,
        })

        # Pipeline状态
        pipeline_status = {
            "air_quality_ready": air_result.get("status") == "success",
            "meteo_ready": meteo_result.get("status") in {"success", "warning"},
            "traffic_ready": traffic_result.get("status") == "success",
            "spatial_ready": spatial_result.get("status") in {"success", "warning"},
            "source_ready": source_result.get("status") == "success",
            "decision_ready": decision_result.get("status") == "success",
            "report_ready": report_result.get("status") == "success",
            "coordinator_min_ok": (
                air_result.get("status") == "success"
                and meteo_result.get("status") in {"success", "warning"}
                and traffic_result.get("status") == "success"
                and spatial_result.get("status") in {"success", "warning"}
                and source_result.get("status") == "success"
                and decision_result.get("status") == "success"
                and report_result.get("status") == "success"
            ),
        }

        return {
            "task": task,
            "air_quality": air_result,
            "meteorology": meteo_result,
            "traffic": traffic_result,
            "spatial": spatial_result,
            "source": source_result,
            "decision": decision_result,
            "report": report_result,
            "pipeline_status": pipeline_status,
        }


def demo_task() -> dict[str, Any]:
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
    parser = argparse.ArgumentParser(description="Coordinator 最小联调（第9天）")
    parser.add_argument("--task-json", default="")
    args = parser.parse_args()
    task = json.loads(Path(args.task_json).read_text(encoding="utf-8")) if args.task_json else demo_task()

    out = CoordinatorMin().analyze(task)
    path = ROOT / "examples" / "day9_coordinator_output.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("pipeline:", json.dumps(out["pipeline_status"], ensure_ascii=False))
    print("air:", out["air_quality"]["status"], (out["air_quality"].get("result") or {}).get("pollution_status"))
    print("traffic:", out["traffic"]["status"], (out["traffic"].get("result") or {}).get("traffic_pressure"))
    print("source:", out["source"]["status"], (out["source"].get("result") or {}).get("main_judgement"))
    print("traffic_source_score:", (out["source"].get("result") or {}).get("source_attribution", {}).get("traffic_source"))
    print("decision:", out["decision"]["status"], (out["decision"].get("result") or {}).get("decision_level"))
    print("report:", out["report"]["status"], len((out["report"].get("result") or {}).get("sections", [])), "sections")
    print(f"[saved] {path}")


if __name__ == "__main__":
    main()
