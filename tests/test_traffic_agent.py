# -*- coding: utf-8 -*-
"""Traffic Agent 基础自检。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.traffic_tools import compute_traffic_pressure, hour_to_time_slot, parse_time_range


def test_time_slots():
    assert hour_to_time_slot(8) == "0800-0900"
    slots = parse_time_range("2023-08-15 08:00:00", "2023-08-15 11:00:00")
    assert slots == ["0800-0900", "0900-1000", "1000-1100"]


def test_pressure_weights():
    props = {
        "Private Car": 20.0,
        "Taxi": 16.0,
        "Goods Vehicle Light": 16.0,
        "Franchised Bus DD": 40.0,
    }
    p = compute_traffic_pressure(props)
    assert p["traffic_pressure_score"] > 0.5
    assert p["traffic_pressure"] in ("medium", "high")


def test_agent_if_db_ready():
    db = ROOT / "data" / "traffic.db"
    if not db.exists():
        print("skip agent: no db")
        return
    from agents.traffic_agent import TrafficAgent, demo_task_mong_kok

    r = TrafficAgent(db).run(demo_task_mong_kok())
    assert r["agent_name"] == "TrafficAgent"
    assert r["status"] in ("success", "warning")
    print("agent status", r["status"], r.get("result", {}).get("traffic_pressure"))


if __name__ == "__main__":
    test_time_slots()
    test_pressure_weights()
    test_agent_if_db_ready()
    print("all tests passed")
