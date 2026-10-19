# -*- coding: utf-8 -*-
"""对照分工表第1-9天做严格自检。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

checks = []


def ok(name, passed, detail=""):
    checks.append((name, bool(passed), detail))
    mark = "PASS" if passed else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))


# --- 文件交付 ---
required_files = [
    "agents/traffic_agent.py",
    "tools/traffic_tools.py",
    "data_process/traffic.py",
    "data_process/schema_traffic.sql",
    "docs/TrafficAgent_接口说明.md",
    "data/station_mapping.json",
    "data/traffic.db",
]
for f in required_files:
    p = ROOT / f
    ok(f"文件存在: {f}", p.exists(), str(p) if p.exists() else "缺失")

# --- 数据表与 2018-2024 ---
db = ROOT / "data" / "traffic.db"
conn = sqlite3.connect(str(db))
years = [r[0] for r in conn.execute(
    "SELECT DISTINCT year FROM traffic_vehicle_mix ORDER BY year"
).fetchall()]
ok("年份覆盖 2018-2024", years == list(range(2018, 2025)), f"实际={years}")

for y in range(2018, 2025):
    n_mix, n_st = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT atc_station_id) FROM traffic_vehicle_mix WHERE year=?",
        (y,),
    ).fetchone()
    ok(f"{y} 年 vehicle_mix 非空", n_mix > 0, f"rows={n_mix}, stations={n_st}")

# 表字段
cols = {r[1] for r in conn.execute("PRAGMA table_info(traffic_vehicle_mix)").fetchall()}
need = {"year", "region_or_station", "day_type", "time_slot", "vehicle_type", "proportion", "occupancy", "source_file"}
ok("traffic_vehicle_mix 核心字段齐全", need <= cols, f"缺={need-cols}")

vol_n = conn.execute("SELECT COUNT(*) FROM traffic_volume").fetchone()[0]
ok("traffic_volume（如有）", True, f"当前行数={vol_n}（分工表为可选）")

# 案例站七年（映射到有 OCC 的主 ATC）
for sid, label in (("3006", "MongKok/Nathan"), ("2214", "CausewayBay/CausewayRd"), ("2207", "Central/Connaught")):
    ys = [r[0] for r in conn.execute(
        "SELECT DISTINCT year FROM traffic_vehicle_mix WHERE atc_station_id=? ORDER BY year",
        (sid,),
    ).fetchall()]
    ok(f"案例 ATC {sid} ({label}) 覆盖七年", ys == list(range(2018, 2025)), f"实际={ys}")

# 原映射 1029/1030 无 OCC（数据源限制，不算 ETL 失败）
for sid in ("1029", "1030"):
    n = conn.execute(
        "SELECT COUNT(*) FROM traffic_vehicle_mix WHERE atc_station_id=?", (sid,)
    ).fetchone()[0]
    meta_n = conn.execute(
        "SELECT COUNT(*) FROM traffic_station_meta WHERE atc_station_id=?", (sid,)
    ).fetchone()[0]
    ok(
        f"说明: ATC {sid} 无 OCC（meta={meta_n}, mix={n}）",
        meta_n > 0 and n == 0,
        "已改用有 OCC 的周边主站 2214/2207",
    )

# Pro/Ocp 样例
row = conn.execute(
    "SELECT proportion, occupancy FROM traffic_vehicle_mix "
    "WHERE atc_station_id='3006' AND time_slot='0800-0900' AND vehicle_type='Taxi' AND year=2023"
).fetchone()
ok("Pro/Ocp 已解析入库", row is not None and row[0] is not None, str(row))

conn.close()

# --- Agent 功能与标准 JSON ---
from agents.traffic_agent import TrafficAgent

task = {
    "task_id": "HK_MK_2023_0800",
    "station_name": "Mong Kok Roadside",
    "station_id": "mk",
    "start_time": "2023-08-15 08:00:00",
    "end_time": "2023-08-15 11:00:00",
    "lat": 22.3193,
    "lon": 114.1694,
}
result = TrafficAgent().run(task)
ok("Agent 可独立运行", result.get("agent_name") == "TrafficAgent")
ok("统一输出含 status/result/evidence/confidence/data_quality/created_at",
   all(k in result for k in ("status", "result", "evidence", "confidence", "data_quality", "created_at")))
r = result.get("result") or {}
need_result = {
    "traffic_pressure", "traffic_pressure_score", "dominant_vehicle_types",
    "heavy_vehicle_signal", "bus_ratio", "goods_vehicle_ratio",
}
ok("标准 result 字段齐全", need_result <= set(r), f"缺={need_result-set(r)}")
ok("压力指数已计算", isinstance(r.get("traffic_pressure_score"), (int, float)) and r.get("traffic_pressure_score", 0) > 0,
   str(r.get("traffic_pressure_score")))
ok("车型结构已输出", bool(r.get("dominant_vehicle_types")), str(r.get("dominant_vehicle_types")))
ok("重点车型判断", bool(r.get("vehicle_focus") or r.get("heavy_vehicle_signal")),
   str(r.get("vehicle_focus")))

# 权重实现
from tools.traffic_tools import VEHICLE_RISK_WEIGHTS
ok("含文档权重 私家车1/出租1.2/小巴1.5/轻货2/中重货4/巴士3",
   VEHICLE_RISK_WEIGHTS.get("Private Car") == 1.0
   and VEHICLE_RISK_WEIGHTS.get("Taxi") == 1.2
   and VEHICLE_RISK_WEIGHTS.get("PLB") == 1.5
   and VEHICLE_RISK_WEIGHTS.get("Goods Vehicle Light") == 2.0
   and VEHICLE_RISK_WEIGHTS.get("Goods Vehicle M&H") == 4.0
   and VEHICLE_RISK_WEIGHTS.get("Franchised Bus DD") == 3.0)

# ETL 能力：函数是否存在
from data_process import traffic as etl
ok("压缩包自动导入函数", hasattr(etl, "extract_atc_archive") and hasattr(etl, "run_etl"))
ok("宽表转长表函数", hasattr(etl, "parse_occ_long"))

# 第9天：接入 Source / 总流程（Traffic 侧最小联调）
coord_py = ROOT / "agents" / "coordinator_min.py"
source_stub = ROOT / "agents" / "source_agent_stub.py"
ok("第9天：Coordinator 最小流程文件", coord_py.exists())
ok("第9天：Source 接入桩文件", source_stub.exists())
if coord_py.exists():
    from agents.coordinator_min import CoordinatorMin

    pipe = CoordinatorMin().analyze(task)
    ok(
        "第9天：总流程可跑且 Traffic→Source 已接通",
        pipe.get("pipeline_status", {}).get("coordinator_min_ok")
        and pipe.get("pipeline_status", {}).get("source_consumed_traffic")
        and pipe.get("traffic", {}).get("status") == "success"
        and pipe.get("source", {}).get("status") == "success",
        str(pipe.get("pipeline_status")),
    )
    ok(
        "第9天：完整7个真实Agent（非占位）",
        False,
        "Air/Meteo/Spatial/Decision/Report 仍为占位，待组员合入",
    )

# 接口说明
doc = (ROOT / "docs" / "TrafficAgent_接口说明.md").read_text(encoding="utf-8")
ok("接口说明含输入输出示例", "统一输入" in doc and "统一输出" in doc)

passed = sum(1 for _, p, _ in checks if p)
failed = sum(1 for _, p, _ in checks if not p)
print(f"\n合计 PASS={passed} FAIL={failed}")
(ROOT / "examples" / "day1_9_audit.json").write_text(
    json.dumps([{"name": n, "pass": p, "detail": d} for n, p, d in checks], ensure_ascii=False, indent=2),
    encoding="utf-8",
)
