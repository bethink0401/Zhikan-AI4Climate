# -*- coding: utf-8 -*-
"""
Air Quality Agent — 空气质量异常识别

输入：全组统一 task JSON
输出：全组统一 Agent 结果 JSON
可独立运行：python agents/air_quality_agent.py
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

import tools.air_quality_tools as air_quality_tools  # noqa: E402
from tools.air_quality_tools import (  # noqa: E402
    DEFAULT_DB,
    query_midday_climatology,
    build_evidence,
    classify_aqhi_level,
    compute_anomaly_score,
    compute_roadside_boost,
    detect_traffic_signal,
    identify_main_pollutants,
    is_roadside_station,
    load_station_no2_p90,
    open_db,
    query_air_quality_data,
    query_multi_station_sync,
    query_spatial_contrast,
)


class AirQualityAgent:
    name = "AirQualityAgent"

    def __init__(self, db_path: Path | None = None):
        # 默认库路径必须**在调用时解析**，不能在定义时绑定：基准脚本切换合成库
        # 的标准做法是替换模块级 DEFAULT_DB，而 def __init__(db_path=DEFAULT_DB)
        # 会在导入时把真实库路径固化进默认值——替换不生效、agent 静默读真实库，
        # 合成世界的标签与真实观测被混在一起（V9.6 数据契约审计发现）。
        self.db_path = Path(db_path) if db_path is not None else None

    def run(self, task: dict[str, Any]) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        station_name = task.get("station_name") or ""
        start_time = task.get("start_time") or ""
        end_time = task.get("end_time") or start_time
        task_id = task.get("task_id", "")

        resolved_path = (self.db_path if self.db_path is not None
                         else Path(air_quality_tools.DEFAULT_DB))
        try:
            conn = open_db(resolved_path)
        except FileNotFoundError as e:
            return self._fail(str(e), created_at, missing_rate=1.0)

        rows = query_air_quality_data(conn, station_name, start_time, end_time)

        if not rows:
            conn.close()
            return {
                "agent_name": self.name,
                "status": "warning",
                "result": {
                    "pollution_status": "unknown",
                    "main_pollutants": [],
                    "aqhi_level": "unknown",
                    "traffic_pollutant_signal": "unknown",
                    "anomaly_score": 0.0,
                },
                "evidence": [
                    f"No air quality data for station={station_name}, period={start_time} ~ {end_time}."
                ],
                "confidence": 0.2,
                "data_quality": {
                    "missing_rate": 1.0,
                    "note": "数据缺失，请检查 ETL 是否导入对应站点/时段；勿直接报错中断上游。",
                },
                "created_at": created_at,
                "meta": {
                    "task_id": task_id,
                    "station_name": station_name,
                    "start_time": start_time,
                    "end_time": end_time,
                    "row_count": len(rows),
                },
            }

        aqhi_values = [r["aqhi"] for r in rows if r.get("aqhi") is not None]
        avg_aqhi = sum(aqhi_values) / len(aqhi_values) if aqhi_values else None
        aqhi_level = classify_aqhi_level(avg_aqhi)
        main_pollutants = identify_main_pollutants(rows)
        anomaly = compute_anomaly_score(rows)
        traffic_signal = detect_traffic_signal(rows, start_time, end_time)
        roadside_boost = compute_roadside_boost(station_name, traffic_signal["traffic_pollutant_signal"])
        # V8 年份鲁棒交通信号：窗口 NO2 均值相对站点当年午间（11-15 时）气候态的
        # 比值。峰值窗口该比值 >1 表示"比本站同时段典型水平更高"，基于全年数据，
        # 无窗口内 off-peak 小样本噪声，且随年份自动适配（诊断结论：V7 的离散
        # 三档信号分在 2022 全部落 0.75 < 0.8 阈值，致交通源禁判）。
        try:
            window_no2 = [r["no2"] for r in rows if r.get("no2") is not None]
            year = int(str(start_time)[:4])
        except (ValueError, TypeError, KeyError):
            window_no2, year = [], None
        midday_clim = None
        clim_ratio = None
        if window_no2 and year:
            midday_clim = query_midday_climatology(conn, station_name, year)
            if midday_clim:
                clim_ratio = (sum(window_no2) / len(window_no2)) / midday_clim
        # V8 交通门控主证据：窗口 NOx 均值的年内分位。分位是相对量，
        # 天然年份鲁棒（诊断：两年交通窗 13/13、20/20 全部 ≥0.75）。
        nox_pct = None
        point_in_time = bool(task.get("point_in_time"))
        try:
            window_nox = [r["nox"] for r in rows if r.get("nox") is not None]
            if window_nox and year:
                mean_nox = sum(window_nox) / len(window_nox)
                db_name = (station_name.replace(" Roadside", "")
                           .replace(" roadside", "").strip())
                # 注意：库中站点名是全大写（如 MONG KOK），而派生的 db_name 保留
                # 大小写（如 Mong Kok）——必须大小写不敏感匹配，否则查询恒为空、
                # NOx 门控静默失效（V9.4 诊断发现，真实与合成数据同样受影响）。
                if point_in_time:
                    pop = sorted(v for (v,) in conn.execute(
                        "SELECT nox FROM air_quality_hourly "
                        "WHERE UPPER(station_name)=UPPER(?) "
                        "AND nox IS NOT NULL AND time >= ? AND time < ?",
                        (db_name, f"{year}-01-01 00:00:00",
                         f"{str(start_time)[:7]} 00:00:00")))
                else:
                    pop = sorted(v for (v,) in conn.execute(
                        "SELECT nox FROM air_quality_hourly "
                        "WHERE UPPER(station_name)=UPPER(?) "
                        "AND nox IS NOT NULL AND time LIKE ?",
                        (db_name, f"{year}-%")))
                if pop:
                    nox_pct = sum(1.0 for v in pop if v < mean_nox) / len(pop)
        except (ValueError, TypeError, KeyError):
            nox_pct = None
        if is_roadside_station(station_name):
            anomaly = min(1.0, anomaly * 1.2 + 0.1)
        if aqhi_level in ("high", "very_high"):
            pollution_status = "abnormal"
        elif aqhi_level == "moderate" or anomaly >= 0.5:
            pollution_status = "moderate"
        else:
            pollution_status = "normal"
        confidence = 0.75
        if len(rows) >= 10:
            confidence = min(0.9, confidence + 0.05)
        if avg_aqhi is not None:
            confidence = min(0.9, confidence + 0.05)
        # 多站同步检测：区域传输证据（窗口内各站 NO2 均值 vs 各站自身 p90）。
        # 失败不阻断主流程，同步块置空并记录。
        try:
            year = int(str(start_time)[:4])
        except (ValueError, TypeError):
            year = None
        # V9.2 前向回测：task.point_in_time=true 时，p90 基线只用事件月之前
        # 的数据（严格早于事件），消除全年统计量的前视偏差。
        point_in_time = bool(task.get("point_in_time"))
        try:
            as_of = start_time if point_in_time else None
            p90_map = load_station_no2_p90(conn, year, as_of=as_of) if year else {}
            sync = query_multi_station_sync(conn, start_time, end_time, p90_map)
            # V9.5 区域证据：空间对比度（各站相对自身 p90 的抬升幅度与离散度）。
            # 仅"超 p90 站数"无法区分区域与交通——两者都可能全站超标；区域是
            # 均匀抬升（低离散），交通/积聚是集中抬升（高离散）。
            spatial_contrast = query_spatial_contrast(
                conn, start_time, end_time, p90_map, focus_station=station_name)
        except Exception as exc:  # noqa: BLE001 - 同步检测失败不阻断主流程
            sync = {"stations_checked": 0, "stations_above_p90": 0, "above_p90_stations": []}
            spatial_contrast = {"stations_checked": 0, "mean_ratio": None, "cv": None}
            note_sync = f"多站同步检测失败：{exc}"
        else:
            note_sync = ""

        evidence = build_evidence(station_name, aqhi_level, main_pollutants, traffic_signal, anomaly)
        note = "使用 EPD AQHI/多污染物小时数据；异常指数仅供风险判断。"
        note += note_sync
        if is_roadside_station(station_name):
            note += " 路边站已启用增强判断。"
        conn.close()
        return {
            "agent_name": self.name,
            "status": "success",
            "result": {
                "pollution_status": pollution_status,
                "main_pollutants": main_pollutants,
                "aqhi_level": aqhi_level,
                "avg_aqhi": round(avg_aqhi, 2) if avg_aqhi is not None else None,
                "traffic_pollutant_signal": traffic_signal["traffic_pollutant_signal"],
                "traffic_pollutant_signal_score": roadside_boost,
                "traffic_hour_clim_ratio": round(clim_ratio, 4) if clim_ratio else None,
                "nox_year_percentile": round(nox_pct, 4) if nox_pct is not None else None,
                "midday_climatology_no2": round(midday_clim, 2) if midday_clim else None,
                "anomaly_score": round(anomaly, 4),
                "roadside_station": is_roadside_station(station_name),
                "multi_station_sync": sync,
                "spatial_contrast": spatial_contrast,
                "pollutant_details": {
                    p: {
                        "avg": round(sum(r[p] for r in rows if r.get(p) is not None) /
                                     max(1, sum(1 for r in rows if r.get(p) is not None)), 2),
                        "max": round(max((r[p] for r in rows if r.get(p) is not None), default=0), 2),
                        "min": round(min((r[p] for r in rows if r.get(p) is not None), default=0), 2),
                    }
                    for p in ["no2", "nox", "pm25", "pm10", "o3", "so2", "co"]
                },
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
                "start_time": start_time,
                "end_time": end_time,
                "row_count": len(rows),
                "traffic_signal_details": traffic_signal,
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
    parser = argparse.ArgumentParser(description="Air Quality Agent")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--task-json", default="", help="任务 JSON 文件路径")
    parser.add_argument("--demo", action="store_true", help="运行 Mong Kok 早高峰示例")
    args = parser.parse_args()
    if args.task_json:
        task = json.loads(Path(args.task_json).read_text(encoding="utf-8"))
    else:
        task = demo_task_mong_kok()
    agent = AirQualityAgent(db_path=Path(args.db))
    result = agent.run(task)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    out = ROOT / "examples" / "air_quality_agent_last_output.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[saved] {out}")


if __name__ == "__main__":
    main()
