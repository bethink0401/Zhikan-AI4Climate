"""V9.7 回归：离线规则扫描与在线判定链必须逐例等价。

背景：V9.6 的离线扫描漏掉了系统的异常分门控，并且并列决胜顺序也与在线不同，
导致"离线说能判、在线其实弃判"这类静默分叉（正是 V9.6/V9.7 两处统计矛盾的
温床）。本测试用同一组组件数值分别喂给在线 SourceAgent 与离线的
governance_sweep.predict，要求两者给出**同一个类别**。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V9 = ROOT / "tests" / "benchmarks" / "v9"
BENCH = ROOT / "tests" / "benchmarks"
for path in (str(ROOT), str(V9), str(BENCH)):
    if path not in sys.path:
        sys.path.insert(0, path)

import governance_sweep as gs  # noqa: E402
from agents.source_agent import SourceAgent  # noqa: E402

LABEL_OF = {
    "traffic": "traffic_dominant",
    "local": "local_accumulation",
    "regional": "regional_transport",
}

CONFIG = {"gate_anomaly_min": 0.45, "traffic_nox_pct_min": 0.55,
          "min_strength": 0.35, "margin": 0.05,
          "local_station_term": "none", "traffic_gate_uses_no2_signal": False,
          "regional_variant": "R1_sync"}


def _agent(anomaly: float, nox_pct: float | None, pressure: float | None,
           upwind: float | None, static_wind: float | None, wind_shift: float | None,
           mean_wind: float | None, sync_above: int | None,
           station_ratio: float | None, mean_ratio: float | None) -> str:
    roads = [] if upwind is None else [{"road_name": "R", "upwind_alignment": upwind}]
    air = {"status": "success", "result": {
        "anomaly_score": anomaly, "nox_year_percentile": nox_pct,
        "traffic_pollutant_signal_score": 0.2,
        "multi_station_sync": {"stations_checked": 18,
                               "stations_above_p90": sync_above,
                               "above_p90_stations": []},
        "spatial_contrast": {"stations_checked": 18, "mean_ratio": mean_ratio,
                             "cv": 0.1, "station_ratio": station_ratio}}}
    meteo = {"status": "success", "result": {
        "static_wind_score": static_wind, "wind_shift_score": wind_shift,
        "mean_wind_speed": mean_wind}}
    traffic = {"status": "success", "result": {"traffic_pressure_score": pressure}}
    spatial = {"status": "success", "result": {"upwind_roads": roads}}
    out = SourceAgent().run({"task_id": "t", "station_name": "Mong Kok Roadside"},
                            air_result=air, meteo_result=meteo,
                            traffic_result=traffic, spatial_result=spatial)
    return out["result"]["main_judgement"]


def _offline(row: dict) -> str:
    return gs.predict(row, CONFIG)


CASES = [
    # (说明, 参与判定与已弃用的全部组件)
    ("交通强", dict(anomaly=0.8, nox_pct=0.95, pressure=0.9, upwind=0.8,
                  static_wind=0.2, wind_shift=0.5, mean_wind=3.0, sync_above=1,
                  station_ratio=1.0, mean_ratio=1.0)),
    ("局地强", dict(anomaly=1.0, nox_pct=0.3, pressure=0.2, upwind=0.1,
                  static_wind=0.95, wind_shift=0.4, mean_wind=1.0, sync_above=0,
                  station_ratio=1.0, mean_ratio=1.0)),
    ("区域强", dict(anomaly=0.7, nox_pct=0.4, pressure=0.1, upwind=0.1,
                  static_wind=0.3, wind_shift=0.1, mean_wind=4.5, sync_above=16,
                  station_ratio=1.5, mean_ratio=1.4)),
    ("歧义（应弃判）", dict(anomaly=0.7, nox_pct=0.9, pressure=0.8, upwind=0.8,
                      static_wind=0.95, wind_shift=0.5, mean_wind=3.0, sync_above=0,
                      station_ratio=1.0, mean_ratio=1.0)),
    ("异常分未过门控", dict(anomaly=0.30, nox_pct=0.95, pressure=0.9, upwind=0.9,
                      static_wind=0.9, wind_shift=0.1, mean_wind=4.0, sync_above=18,
                      station_ratio=1.6, mean_ratio=1.5)),
    ("缺气象（局地/区域不可用）", dict(anomaly=0.9, nox_pct=0.95, pressure=0.9,
                             upwind=0.9, static_wind=None, wind_shift=None,
                             mean_wind=None, sync_above=None,
                             station_ratio=None, mean_ratio=None)),
    ("缺 NOx 门控（交通不可用）", dict(anomaly=0.9, nox_pct=None, pressure=0.9,
                              upwind=0.9, static_wind=0.9, wind_shift=0.2,
                              mean_wind=1.2, sync_above=2,
                              station_ratio=1.0, mean_ratio=1.0)),
]


def test_offline_prediction_matches_online_judgement() -> None:
    mismatches = []
    for name, values in CASES:
        online = _agent(**values)
        row = {
            "anomaly": values["anomaly"], "nox_pct": values["nox_pct"],
            "no2_signal": 0.2, "traffic_pressure": values["pressure"],
            "upwind_score": values["upwind"], "static_wind": values["static_wind"],
            "wind_shift": values["wind_shift"], "mean_wind": values["mean_wind"],
            "sync_above": values["sync_above"],
            "contrast_station_ratio": values["station_ratio"],
            "contrast_mean_ratio": values["mean_ratio"], "contrast_cv": 0.1,
            "traffic_status": "success",
            "label": "uncertain",
        }
        offline = _offline(row)
        online_label = ("uncertain" if "Insufficient" in online or "uncertain" in online
                        else next((v for k, v in LABEL_OF.items() if k in online.lower()),
                                  "uncertain"))
        if online_label != offline:
            mismatches.append((name, online, offline))
    assert not mismatches, f"在线/离线判定不一致：{mismatches}"
