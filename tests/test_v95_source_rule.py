# -*- coding: utf-8 -*-
"""V9.5 SourceAgent 证据签名 argmax 规则回归测试。

锁定的行为（全部纯逻辑，无网络）：
  1. 三类证据签名同构可比，且**不含站点常数项**（交通签名不再被路边站属性抬高）；
  2. argmax 规则：交通证据明显强 → 判交通；积聚证据明显强 → 判积聚；
     两者接近（差值 < 支配边际）→ 判"证据不足"，不再让交通吞并积聚；
  3. 污染门控未过 → 一律 Insufficient evidence；
  4. judgement_rule 可切回 legacy_priority_chain 做消融对照。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.source_agent import SourceAgent, load_source_rules  # noqa: E402


def _air(anomaly=0.8, signal=0.9, nox_pct=0.9, sync_above=0, sync_checked=18,
         station_ratio=None):
    return {"agent_name": "AirQualityAgent", "status": "success",
            "result": {"spatial_contrast": {
                           "stations_checked": 18, "mean_ratio": 1.0, "cv": 0.05,
                           "station_ratio": station_ratio},
                       "anomaly_score": anomaly,
                       "traffic_pollutant_signal": "moderate",
                       "traffic_pollutant_signal_score": signal,
                       "nox_year_percentile": nox_pct,
                       "traffic_hour_clim_ratio": 1.1,
                       "roadside_station": True,
                       "multi_station_sync": {"stations_checked": sync_checked,
                                              "stations_above_p90": sync_above}}}


def _meteo(static_wind=0.2, wind=3.0, shift=0.1):
    return {"agent_name": "MeteorologyAgent", "status": "success",
            "result": {"static_wind_score": static_wind, "wind_shift_score": shift,
                       "rain_removal_score": 0.5, "mean_wind_speed": wind,
                       "total_precipitation": 0.0, "dominant_wind_direction": 90,
                       "upwind_direction": "E", "downwind_direction": "W"}}


def _traffic(score=0.9):
    return {"agent_name": "TrafficAgent", "status": "success",
            "result": {"traffic_pressure": "high", "traffic_pressure_score": score,
                       "road_links": ["Nathan Road"]}}


def _spatial(alignment=0.9):
    return {"agent_name": "SpatialAgent", "status": "success",
            "result": {"upwind_roads": [{"road_name": "Nathan Road",
                                         "upwind_alignment": alignment}],
                       "spatial_confidence": "high"}}


def _run(air, meteo, traffic, spatial, rules=None) -> dict:
    agent = SourceAgent()
    out = agent.run({"task_id": "t", "station_name": "Mong Kok Roadside"},
                    air_result=air, meteo_result=meteo,
                    traffic_result=traffic, spatial_result=spatial, rules=rules)
    return out["result"]


def test_signature_scores_present_and_comparable() -> None:
    res = _run(_air(), _meteo(), _traffic(), _spatial())
    sig = res["source_attribution"]["signature_scores"]
    assert set(sig) == {"traffic", "local", "regional"}
    assert all(0.0 <= v <= 1.0 for v in sig.values())


def test_traffic_signature_excludes_station_constant() -> None:
    """交通签名不得包含路边站常数项：站点属性不是事件签名。"""
    rules = load_source_rules()
    weights = rules["signature_weights"]["traffic"]
    assert "roadside" not in weights, "交通签名不得含 roadside 常数项"
    # 压力/上风向都为 0 时，交通签名必须为 0（旧实现会被 roadside 抬到 ≥0.25）
    res = _run(_air(), _meteo(), _traffic(score=0.0), _spatial(alignment=0.0))
    assert res["source_attribution"]["signature_scores"]["traffic"] == 0.0


def test_argmax_prefers_traffic_when_traffic_evidence_dominates() -> None:
    res = _run(_air(anomaly=0.9, nox_pct=0.95), _meteo(static_wind=0.05, wind=6.0),
               _traffic(score=0.95), _spatial(alignment=0.95))
    assert res["main_judgement"] == "Traffic source dominant"


def test_argmax_prefers_local_when_local_evidence_dominates() -> None:
    # 交通门控关闭（nox_pct 低）+ 强静风 → 积聚
    res = _run(_air(anomaly=0.9, nox_pct=0.1, signal=0.2),
               _meteo(static_wind=0.95, wind=0.4), _traffic(score=0.3), _spatial(0.1))
    assert res["main_judgement"] == "Local accumulation dominant"


def test_ambiguous_evidence_abstains() -> None:
    """交通与积聚证据接近（差值 < 支配边际）时判"证据不足"，
    不再让交通无条件吞并积聚。

    用显式 rules 覆盖标定值，使断言不随开发集标定结果漂移。
    """
    rules = dict(load_source_rules())
    rules["judgement_rule"] = "score_argmax"
    rules["min_strength"] = 0.4
    rules["dominance_margin"] = 0.06
    res = _run(_air(anomaly=0.7, nox_pct=0.9), _meteo(static_wind=0.95, wind=0.6),
               _traffic(score=0.8), _spatial(alignment=0.8), rules=rules)
    sig = res["source_attribution"]["signature_scores"]
    assert abs(sig["traffic"] - sig["local"]) < 0.06, "测试前提：两类签名接近"
    assert res["main_judgement"] == "Mixed / uncertain sources"


def test_gate_blocks_judgement() -> None:
    res = _run(_air(anomaly=0.1), _meteo(), _traffic(), _spatial())
    assert res["main_judgement"] == "Insufficient evidence"


def test_legacy_rule_still_selectable() -> None:
    rules = dict(load_source_rules())
    rules["judgement_rule"] = "legacy_priority_chain"
    agent = SourceAgent()
    out = agent.run({"task_id": "t", "station_name": "Mong Kok Roadside"},
                    air_result=_air(anomaly=0.9, nox_pct=0.95),
                    meteo_result=_meteo(static_wind=0.8, wind=0.6),
                    traffic_result=_traffic(score=0.9), spatial_result=_spatial(),
                    rules=rules)
    # 旧链下交通得分 ≥ 阈值即判交通类（无支配边际约束）——这正是对照要暴露的行为：
    # 积聚证据同样强时只是加了个后缀，判定仍然归到交通类。
    assert "Traffic" in out["result"]["main_judgement"]
