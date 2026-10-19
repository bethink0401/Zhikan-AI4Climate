# -*- coding: utf-8 -*-
"""
Source Agent 接入桩（供第9天联调）

此处提供可运行的最小实现：
接收 TrafficAgent（及可选的其他 Agent）标准 JSON，按分工表建议公式
计算交通源可能性，证明 Traffic 输出可被下游消费。

交通源可能性 ≈ 0.35×NO2异常 + 0.25×交通压力 + 0.20×上风向匹配
              + 0.10×低风速 + 0.10×路边站增强
缺失上游时使用中性默认值，并在 data_quality 中说明。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


ROADSIDE_STATIONS = {
    "Mong Kok Roadside",
    "Causeway Bay Roadside",
    "Central Roadside",
}


class SourceAgentStub:
    name = "SourceAgent"

    def run(
        self,
        task: dict[str, Any],
        air_result: Optional[dict[str, Any]] = None,
        meteo_result: Optional[dict[str, Any]] = None,
        traffic_result: Optional[dict[str, Any]] = None,
        spatial_result: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        created_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        notes = []

        # --- 各分量（0-1）---
        no2_score = 0.5
        if air_result and air_result.get("status") == "success":
            ar = air_result.get("result") or {}
            no2_score = float(ar.get("anomaly_score") or ar.get("traffic_pollutant_signal_score") or 0.5)
            if ar.get("traffic_pollutant_signal") == "strong":
                no2_score = max(no2_score, 0.8)
        else:
            notes.append("AirQuality 未接入，NO2异常分量使用默认0.5")

        traffic_score = 0.5
        if traffic_result and traffic_result.get("status") == "success":
            tr = traffic_result.get("result") or {}
            traffic_score = float(tr.get("traffic_pressure_score") or 0.5)
        else:
            notes.append("Traffic 结果缺失，交通压力分量使用默认0.5")

        upwind_score = 0.5
        if spatial_result and spatial_result.get("status") == "success":
            sr = spatial_result.get("result") or {}
            roads = sr.get("upwind_roads") or []
            if roads:
                upwind_score = float(roads[0].get("upwind_alignment") or 0.5)
            conf = sr.get("spatial_confidence")
            if conf == "high":
                upwind_score = max(upwind_score, 0.7)
        else:
            notes.append("Spatial 未接入，上风向分量使用默认0.5")

        static_wind = 0.5
        if meteo_result and meteo_result.get("status") == "success":
            mr = meteo_result.get("result") or {}
            static_wind = float(mr.get("static_wind_score") or 0.5)
        else:
            notes.append("Meteorology 未接入，低风速分量使用默认0.5")

        station_name = task.get("station_name") or ""
        roadside = 0.85 if station_name in ROADSIDE_STATIONS else 0.4

        traffic_source = (
            0.35 * no2_score
            + 0.25 * traffic_score
            + 0.20 * upwind_score
            + 0.10 * static_wind
            + 0.10 * roadside
        )
        traffic_source = max(0.0, min(1.0, traffic_source))

        local_acc = min(1.0, 0.4 * static_wind + 0.3 * traffic_score + 0.2)
        regional = max(0.0, 0.55 - 0.25 * traffic_score)
        dust = 0.25
        photo = 0.20

        if traffic_source >= 0.65 and local_acc >= 0.55:
            judgement = "Traffic source with local accumulation"
            risk = "high"
        elif traffic_source >= 0.55:
            judgement = "Traffic source dominant"
            risk = "high" if traffic_source >= 0.7 else "medium"
        else:
            judgement = "Mixed / uncertain sources"
            risk = "medium"

        evidence = []
        if traffic_result and traffic_result.get("evidence"):
            evidence.extend(traffic_result["evidence"][:2])
        evidence.append(
            f"Traffic-source possibility={traffic_source:.2f} "
            f"(0.35*NO2 + 0.25*traffic_pressure + 0.20*upwind + 0.10*static_wind + 0.10*roadside)."
        )
        if station_name in ROADSIDE_STATIONS:
            evidence.append("Roadside station signal enhances traffic-source weight.")

        confidence = 0.55 + (0.15 if traffic_result and traffic_result.get("status") == "success" else 0)
        if air_result and air_result.get("status") == "success":
            confidence += 0.1

        return {
            "agent_name": self.name,
            "status": "success",
            "result": {
                "source_attribution": {
                    "traffic_source": round(traffic_source, 4),
                    "local_accumulation": round(local_acc, 4),
                    "regional_transport": round(regional, 4),
                    "dust_source": dust,
                    "photochemical_source": photo,
                },
                "main_judgement": judgement,
                "risk_level": risk,
                "component_scores": {
                    "no2_anomaly": round(no2_score, 4),
                    "traffic_pressure": round(traffic_score, 4),
                    "upwind_match": round(upwind_score, 4),
                    "static_wind": round(static_wind, 4),
                    "roadside_boost": roadside,
                },
            },
            "evidence": evidence,
            "confidence": round(min(0.9, confidence), 2),
            "data_quality": {
                "missing_rate": round(len(notes) / 4.0, 2),
                "note": "；".join(notes) if notes else "上游完整；本桩仅用于第9天联调演示",
            },
            "created_at": created_at,
            "meta": {"task_id": task.get("task_id"), "stub": True},
        }
