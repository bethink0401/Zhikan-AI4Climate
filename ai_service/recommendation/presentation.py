"""Build a stable, user-facing recommendation presentation.

This module does not score risk or run business agents.  It translates an
existing RecommendationResult and trusted canonical results into UI sections.
"""

from __future__ import annotations

from typing import Any, Mapping

from .governance import parse_governance_answer


_TITLES = {
    "public_travel_advice": "居民出行建议",
    "health_protection": "健康防护建议",
    "governance_advice": "城市治理建议",
}


def _result(agent_results: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    envelope = agent_results.get(name)
    if not isinstance(envelope, Mapping):
        return {}
    result = envelope.get("result")
    return result if isinstance(result, Mapping) else envelope


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _display_number(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:.3f}".rstrip("0").rstrip(".")


class RecommendationPresentationBuilder:
    """Create intent-specific display sections from already trusted data."""

    def build(
        self,
        *,
        recommendation: Mapping[str, Any],
        agent_results: Mapping[str, Any],
        context: Mapping[str, Any],
        explanation: str,
        knowledge: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        recommendation_type = str(recommendation.get("recommendation_type") or "")
        try:
            generated = parse_governance_answer(explanation)
            direct_advice = generated["direct_advice"]
            safe_explanation = generated["explanation"]
        except (ValueError, TypeError):
            direct_advice = []
            safe_explanation = f"{_TITLES.get(recommendation_type, '环境建议')}暂未生成，请稍后重试。"
        evidence = self._evidence(recommendation_type, recommendation, agent_results, context)
        professional = self._professional_knowledge(knowledge)
        payload: dict[str, Any] = {
            "version": "1.0",
            "recommendation_type": recommendation_type,
            "title": _TITLES.get(recommendation_type, "环境建议"),
            "direct_advice": direct_advice,
            "explanation": safe_explanation,
            "evidence": evidence,
            "scope": {
                key: context.get(key)
                for key in ("station_id", "station_name", "start_time", "end_time")
                if context.get(key) is not None
            },
        }
        if professional is not None:
            payload["professional_knowledge"] = professional
        payload["markdown_fallback"] = self._markdown_fallback(payload)
        return payload

    def _evidence(
        self,
        recommendation_type: str,
        recommendation: Mapping[str, Any],
        agent_results: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        start, end = context.get("start_time"), context.get("end_time")
        time_basis = f"{start} 至 {end}" if start and end else "所选历史时段"

        air = _result(agent_results, "AirQualityAgent")
        aqhi = _number(air.get("avg_aqhi"))
        if aqhi is not None:
            items.append({
                "category": "observed", "label": "AQHI 时段平均值",
                "value": _display_number(aqhi), "unit": "AQHI 指数",
                "source": "AirQualityAgent / EPD 历史数据", "time_basis": time_basis,
                "note": "历史时段指标，不代表当前实时健康风险。",
            })
        details = air.get("pollutant_details")
        main_pollutants = [str(value).lower() for value in (air.get("main_pollutants") or [])]
        if isinstance(details, Mapping):
            selected = main_pollutants[:2] or list(details.keys())[:2]
            for pollutant in selected:
                detail = details.get(pollutant)
                avg = _number(detail.get("avg")) if isinstance(detail, Mapping) else None
                if avg is None:
                    continue
                unit = "mg/m³" if pollutant == "co" else "μg/m³"
                items.append({
                    "category": "observed", "label": f"{pollutant.upper()} 时段平均浓度",
                    "value": _display_number(avg), "unit": unit,
                    "source": "AirQualityAgent / EPD 历史监测", "time_basis": time_basis,
                    "note": "时段聚合值，不是逐小时趋势或未来浓度。",
                })

        weather = _result(agent_results, "MeteorologyAgent")
        wind = _number(weather.get("mean_wind_speed"))
        if wind is not None:
            items.append({
                "category": "observed", "label": "平均风速", "value": _display_number(wind),
                "unit": "m/s", "source": "MeteorologyAgent", "time_basis": time_basis,
                "note": "用于解释所选历史时段的扩散条件。",
            })

        traffic = _result(agent_results, "TrafficAgent")
        if recommendation_type in {"public_travel_advice", "governance_advice"}:
            pressure = _number(traffic.get("traffic_pressure_score"))
            if pressure is not None:
                items.append({
                    "category": "screening", "label": "交通压力代理评分",
                    "value": _display_number(pressure), "unit": "相对评分",
                    "source": "TrafficAgent", "time_basis": time_basis,
                    "note": "基于参考期交通结构的筛查指标，不是真实道路流量或车辆计数。",
                })
            proportions = traffic.get("proportions_pct")
            if isinstance(proportions, Mapping) and proportions:
                top = sorted(
                    ((str(key), value) for key, value in proportions.items() if _number(value) is not None),
                    key=lambda item: float(item[1]), reverse=True,
                )[:3]
                if top:
                    summary = "、".join(f"{name} {_display_number(float(value))}%" for name, value in top)
                    items.append({
                        "category": "estimated", "label": "参考期主要车型构成", "value": summary,
                        "unit": "%", "source": "TrafficAgent / ATC 参考期统计",
                        "time_basis": "参考期车型结构", "note": "不是选定小时的实测车流量。",
                    })

        source = _result(agent_results, "SourceAgent")
        if recommendation_type == "governance_advice" and source:
            judgement = source.get("main_judgement")
            if isinstance(judgement, str) and judgement.strip():
                items.append({
                    "category": "screening", "label": "污染来源筛查判断", "value": judgement,
                    "source": "SourceAgent", "time_basis": time_basis,
                    "note": "属于筛查判断，来源评分不是污染贡献率。",
                })

        future = recommendation.get("future_risk_assessment")
        if (
            recommendation_type == "governance_advice"
            and isinstance(future, Mapping)
            and _number(future.get("risk_score")) is not None
        ):
            items.append({
                "category": "screening", "label": "未经校准的历史规则筛查分",
                "value": _display_number(float(future["risk_score"])), "unit": "/100",
                "source": "PredictionFusion 启发式规则", "time_basis": time_basis,
                "note": "不是官方风险等级、发生概率、医学风险或未来实测污染浓度。",
            })

        limitation_labels = {
            "route_specific_exposure_unavailable": "缺少路线级个人暴露测量，不能保证某条道路更安全。",
            "aqhi_unavailable": "所选时段 AQHI 数据不可用。",
            "traffic_pressure_unavailable": "交通压力代理指标不可用。",
            "wind_speed_unavailable": "风速数据不可用。",
            "air_quality_unavailable": "空气质量数据不足。",
            "future_meteorology_unavailable": "未使用未来气象数据。",
            "dispersion_path_unavailable": "传播路径数据不可用。",
        }
        for limitation in recommendation.get("limitations") or []:
            if (
                str(limitation) == "route_specific_exposure_unavailable"
                and recommendation_type != "public_travel_advice"
            ):
                continue
            text = limitation_labels.get(str(limitation), str(limitation))
            items.append({
                "category": "limitation", "label": "适用边界", "value": text,
                "source": "RecommendationComposer", "time_basis": time_basis,
            })
        return items

    @staticmethod
    def _professional_knowledge(knowledge: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(knowledge, Mapping) or not knowledge.get("used"):
            return None
        hits = [item for item in (knowledge.get("hits") or []) if isinstance(item, Mapping)]
        sources = [item for item in (knowledge.get("sources") or []) if isinstance(item, Mapping)]
        excerpts = [str(item.get("excerpt") or item.get("text") or "").strip() for item in hits]
        excerpts = [item for item in excerpts if item][:2]
        if not excerpts and not sources:
            return None
        return {"summary": "\n\n".join(excerpts), "sources": sources}

    @staticmethod
    def _markdown_fallback(payload: Mapping[str, Any]) -> str:
        lines = [f"## {payload.get('title')}"]
        for item in payload.get("direct_advice") or []:
            lines.append(f"- **{item.get('title')}**：{item.get('detail')}")
        heading = "为什么这样建议" if payload.get("direct_advice") else "生成状态"
        lines.extend(("", f"### {heading}", str(payload.get("explanation") or "")))
        professional = payload.get("professional_knowledge")
        if isinstance(professional, Mapping) and professional.get("summary"):
            lines.extend(("", "### 专业知识补充", str(professional["summary"])))
        return "\n".join(lines)


__all__ = ["RecommendationPresentationBuilder"]
