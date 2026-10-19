# -*- coding: utf-8 -*-
"""
Source Agent — 污染来源可能性判断智能体

功能：
1. 融合空气质量、气象、交通和空间匹配结果（优先使用上游Agent结果）
2. 判断污染更可能来自：交通源、局地积聚、区域传输
3. 输出证据链

判定原则（2026-10-13 重标定）：
- **缺测不再默认插补**：缺少关键证据的来源类别一律不允许判定（宁可"证据不足"）
- NO2/异常分只做"污染确实升高"的门控，不直接作为交通源证据——
  旧版把异常分当交通证据用，导致所有窗口都判交通源
- 交通源证据 = 交通压力 + 上风向道路匹配 + 路边站属性
- 区域传输证据 = 高风速 + 风向稳定 + 局地交通证据不足
- 判定阈值集中在 config/pollution_rules.json 的 source_judgement 块
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]

RULES_PATH = ROOT / "config" / "pollution_rules.json"

ROADSIDE_STATIONS = {
    "Mong Kok Roadside",
    "Causeway Bay Roadside",
    "Central Roadside",
}


def load_source_rules() -> dict[str, Any]:
    """判定阈值与权重（集中在 config，便于开发集重标定后冻结）。"""
    with RULES_PATH.open(encoding="utf-8") as handle:
        return json.load(handle).get("source_judgement") or {}


class SourceAgent:
    """污染来源智能体"""
    name = "SourceAgent"

    # ── 上游证据提取（缺测返回 None，不做默认插补） ────────────────────────

    def _extract_air_scores(self, air_result: Optional[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
        """空气质量证据：异常分只做门控，不再充当交通源证据。"""
        notes: list[str] = []
        anomaly = None
        no2_score = None
        traffic_signal = "unknown"
        roadside_station = False

        if air_result and air_result.get("status") == "success":
            result = air_result.get("result", {})
            anomaly = result.get("anomaly_score")
            no2_score = result.get("traffic_pollutant_signal_score")
            traffic_signal = result.get("traffic_pollutant_signal") or "weak"
            roadside_station = bool(result.get("roadside_station", False))
            if anomaly is None:
                notes.append("AirQualityAgent 未给出异常分，无法做污染门控")
        else:
            notes.append("AirQualityAgent结果缺失，无法判断污染是否升高")

        sync = None
        clim_ratio = None
        nox_pct = None
        spatial_contrast = None
        if air_result and air_result.get("status") == "success":
            air_payload = air_result.get("result") or {}
            sync = air_payload.get("multi_station_sync")
            clim_ratio = air_payload.get("traffic_hour_clim_ratio")
            nox_pct = air_payload.get("nox_year_percentile")
            spatial_contrast = air_payload.get("spatial_contrast")

        return {
            "anomaly_score": anomaly,
            "no2_score": no2_score,
            "traffic_signal": traffic_signal,
            "roadside_station": roadside_station,
            "multi_station_sync": sync,
            "spatial_contrast": spatial_contrast,
            "traffic_hour_clim_ratio": clim_ratio,
            "nox_year_percentile": nox_pct,
            "notes": notes,
        }, notes

    def _extract_traffic_scores(self, traffic_result: Optional[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
        notes: list[str] = []
        traffic_score: Optional[float] = None
        traffic_pressure = "unknown"
        road_links: list[str] = []

        if traffic_result and traffic_result.get("status") == "success":
            result = traffic_result.get("result", {})
            traffic_score = result.get("traffic_pressure_score")
            traffic_pressure = result.get("traffic_pressure") or "unknown"
            road_links = result.get("road_links", [])
        else:
            notes.append("TrafficAgent结果缺失，无法判定交通源")

        return {
            "traffic_score": traffic_score,
            "traffic_pressure": traffic_pressure,
            "road_links": road_links,
            "notes": notes,
        }, notes

    def _extract_meteorology_scores(self, meteo_result: Optional[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
        notes: list[str] = []
        static_wind = None
        wind_shift = None
        rain_removal = None
        mean_wind_speed = None
        total_precipitation = None
        wind_direction = None
        upwind_direction = None
        downwind_direction = None

        if meteo_result and meteo_result.get("status") == "success":
            result = meteo_result.get("result", {})
            static_wind = result.get("static_wind_score")
            wind_shift = result.get("wind_shift_score")
            rain_removal = result.get("rain_removal_score")
            mean_wind_speed = result.get("mean_wind_speed")
            total_precipitation = result.get("total_precipitation")
            wind_direction = result.get("dominant_wind_direction")
            upwind_direction = result.get("upwind_direction")
            downwind_direction = result.get("downwind_direction")
        else:
            notes.append("MeteorologyAgent结果缺失，无法评估积聚/传输条件")

        return {
            "static_wind_score": static_wind,
            "wind_shift_score": wind_shift,
            "rain_removal_score": rain_removal,
            "mean_wind_speed": mean_wind_speed,
            "total_precipitation": total_precipitation,
            "wind_direction": wind_direction,
            "upwind_direction": upwind_direction,
            "downwind_direction": downwind_direction,
            "notes": notes,
        }, notes

    def _extract_spatial_scores(self, spatial_result: Optional[dict[str, Any]]) -> tuple[dict[str, Any], list[str]]:
        notes: list[str] = []
        upwind_score: Optional[float] = None
        road_links: list[str] = []
        upwind_roads_count = 0
        spatial_confidence = "low"

        if spatial_result and spatial_result.get("status") == "success":
            result = spatial_result.get("result", {})
            upwind_roads = result.get("upwind_roads") or []
            upwind_roads_count = len(upwind_roads)
            if upwind_roads:
                max_alignment = 0.0
                max_road_name = ""
                for road in upwind_roads:
                    alignment = float(road.get("upwind_alignment") or 0.0)
                    if alignment > max_alignment:
                        max_alignment = alignment
                        max_road_name = (road.get("road_name")
                                         or road.get("road_link") or "")
                upwind_score = max_alignment
                if max_road_name:
                    road_links = [max_road_name]
                spatial_confidence = (result.get("spatial_confidence")
                                      or "medium")
            else:
                notes.append("SpatialAgent未提供upwind_roads数据")
        else:
            notes.append("SpatialAgent结果缺失，无法做上风向道路匹配")

        return {
            "upwind_score": upwind_score,
            "road_links": road_links,
            "upwind_roads_count": upwind_roads_count,
            "spatial_confidence": spatial_confidence,
            "notes": notes,
        }, notes

    # ── 来源评分（证据缺失的类别不允许判定） ────────────────────────────────

    def _compute_source_attribution(
        self,
        air_scores: dict[str, Any],
        traffic_scores: dict[str, Any],
        meteorology_scores: dict[str, Any],
        spatial_scores: dict[str, Any],
        station_name: str,
    ) -> dict[str, Any]:
        rules = self._rules or load_source_rules()

        anomaly = air_scores.get("anomaly_score")
        traffic_score = traffic_scores.get("traffic_score")
        upwind_score = spatial_scores.get("upwind_score")
        static_wind = meteorology_scores.get("static_wind_score")
        mean_wind = meteorology_scores.get("mean_wind_speed")
        wind_shift = meteorology_scores.get("wind_shift_score")

        # 交通源 = 交通压力 + 上风向道路匹配 + 路边站属性；缺任一证据按 0 计并标记。
        # 额外要求 EPD 日内交通信号达标：异常分只说明"污染升高"，日内高峰信号才
        # 是污染来自交通的证据（旧版把异常分当交通证据，导致所有窗口都判交通源）。
        # V9.5 交通门控：只用 NOx 年内分位（车辆尾气专有证据）。旧版把 EPD
        # 日内 NO2 形态分作为 OR 备用通道——但 NO2 日内峰对交通与**局地积聚**
        # 都会触发（两者都抬高 NO2），不具交通特异性，正是"交通证据一过门控
        # 就吞并积聚"的直接原因。NO2 形态分降级为纯证据，不再参与门控。
        no2_signal = air_scores.get("no2_score")
        nox_pct = air_scores.get("nox_year_percentile")
        nox_pct_min = float(rules.get("traffic_nox_pct_min", 9e9))
        traffic_signal_ok = nox_pct is not None and nox_pct >= nox_pct_min
        roadside_boost = 1.0 if station_name in ROADSIDE_STATIONS else 0.4
        traffic_available = traffic_score is not None and traffic_signal_ok
        traffic_source = (
            float(rules["traffic_weights"]["pressure"]) * (traffic_score or 0.0)
            + float(rules["traffic_weights"]["upwind"]) * (upwind_score or 0.0)
            + float(rules["traffic_weights"]["roadside"]) * roadside_boost
        )
        traffic_source = max(0.0, min(1.0, traffic_source))
        if not traffic_available:
            traffic_source = 0.0  # 无交通数据时不给交通源任何分数

        # 局地积聚 = 静风 + 污染升高；缺风无法判定积聚
        local_available = static_wind is not None
        local_accumulation = (
            float(rules["local_weights"]["static_wind"]) * (static_wind or 0.0)
            + float(rules["local_weights"]["anomaly"]) * (anomaly or 0.0)
        )
        local_accumulation = max(0.0, min(1.0, local_accumulation))
        if not local_available:
            local_accumulation = 0.0

        # 区域传输 = 多站同步升高 + 高风速 + 风向稳定 + 局地交通证据不足。
        # 同步证据是主项：多数站点同期超自身 p90 才是"区域性"事件的直接观测，
        # 风速/风向只是传输条件，局地交通弱只是排除项。缺风无法判定传输。
        sync = air_scores.get("multi_station_sync") or {}
        sync_count = float(sync.get("stations_above_p90") or 0.0)
        sync_norm = max(0.0, min(1.0, sync_count / 8.0))  # ≥8 站同步 = 显著
        regional_available = mean_wind is not None
        high_wind = max(0.0, min(1.0, (mean_wind or 0.0) / 5.0))
        wind_stability = (1.0 - wind_shift) if wind_shift is not None else 0.0
        weak_local_traffic = 1.0 - traffic_source if traffic_available else 0.5
        regional_transport = (
            float(rules["regional_weights"]["sync"]) * sync_norm
            + float(rules["regional_weights"]["high_wind"]) * high_wind
            + float(rules["regional_weights"]["wind_stability"]) * wind_stability
            + float(rules["regional_weights"]["weak_local"]) * weak_local_traffic
        )
        regional_transport = max(0.0, min(1.0, regional_transport))
        if not regional_available:
            regional_transport = 0.0

        # ── V9.5 证据签名：三类同构、只含正向证据、各自归一化到 [0,1] ──────
        # 与上面的"加权和得分"不同，签名不含任何站点常数项，也不含"某类证据弱"
        # 这类排除项——它们回答的是同一个问题："这一类机制的特征出现了多少"，
        # 因此可以直接横向比较大小。交通签名去掉了路边站常数（站点属性会污染
        # 事件签名，让每个路边站窗口都像交通）；区域签名去掉了 weak_local
        # 排除项（判定改成 argmax 后，排除项只会把分数整体压低）。
        sig_weights = rules.get("signature_weights") or {}
        tw = sig_weights.get("traffic") or {}
        lw = sig_weights.get("local") or {}
        rw = sig_weights.get("regional") or {}
        sig_traffic = (float(tw.get("pressure", 0.6)) * (traffic_score or 0.0)
                       + float(tw.get("upwind", 0.4)) * (upwind_score or 0.0))
        sig_traffic = max(0.0, min(1.0, sig_traffic)) if traffic_available else 0.0
        # 局地积聚签名：静风 + 污染异常 + **本站自身抬升比**（局部聚焦证据）。
        # 本站抬升比区分"单站升高"（积聚）与"全站均匀升高"（区域）——前者
        # 本站比值高，后者各站相近（此时由区域签名胜出）。
        station_ratio = (air_scores.get("spatial_contrast") or {}).get("station_ratio")
        own_elev = (max(0.0, min(1.0, (float(station_ratio) - 1.0) / 0.5))
                    if station_ratio is not None else 0.0)
        sig_local = (float(lw.get("static_wind", 0.4)) * (static_wind or 0.0)
                     + float(lw.get("anomaly", 0.25)) * (anomaly or 0.0)
                     + float(lw.get("station_elevation", 0.35)) * own_elev)
        sig_local = max(0.0, min(1.0, sig_local)) if local_available else 0.0
        # 区域证据 = 空间对比度（主项，V9.5）：大范围均匀抬升 → 高抬升幅度 +
        # 低空间离散；交通/积聚是集中抬升 → 高离散。仅凭"超 p90 站数"无法区分
        # 区域与交通（两者都可能全站超标）。同步站数/风速/风向稳定保留为次要项。
        contrast = air_scores.get("spatial_contrast") or {}
        mean_ratio = contrast.get("mean_ratio")
        cv = contrast.get("cv")
        elev_norm = (max(0.0, min(1.0, (float(mean_ratio) - 1.0) / 0.5))
                     if mean_ratio is not None else 0.0)
        uniform_norm = (max(0.0, min(1.0, 1.0 - float(cv) / 0.25))
                        if cv is not None else 0.0)
        sig_regional = (float(rw.get("elevation", 0.5)) * elev_norm
                        + float(rw.get("uniformity", 0.3)) * uniform_norm
                        + float(rw.get("sync", 0.1)) * sync_norm
                        + float(rw.get("high_wind", 0.05)) * high_wind
                        + float(rw.get("wind_stability", 0.05)) * wind_stability)
        sig_regional = max(0.0, min(1.0, sig_regional)) if regional_available else 0.0

        # 保留旧字段兼容下游（decision/report 只读 traffic_source 数值）
        anomaly_value = anomaly or 0.0
        dust_source = min(1.0, 0.5 * anomaly_value
                          + 0.2 * (traffic_score or 0.0))
        photochemical_source = min(1.0, 0.6 * anomaly_value)

        return {
            "traffic_source": round(traffic_source, 4),
            "local_accumulation": round(local_accumulation, 4),
            "regional_transport": round(regional_transport, 4),
            "dust_source": round(dust_source, 4),
            "photochemical_source": round(photochemical_source, 4),
            "traffic_available": traffic_available,
            "traffic_signal_ok": traffic_signal_ok,
            "local_available": local_available,
            "regional_available": regional_available,
            "signature_scores": {
                "traffic": round(sig_traffic, 4),
                "local": round(sig_local, 4),
                "regional": round(sig_regional, 4),
            },
            "signature_available": {
                "traffic": traffic_available,
                "local": local_available,
                "regional": regional_available,
            },
        }

    def _generate_judgement(
        self,
        source_attribution: dict[str, Any],
        air_scores: dict[str, Any],
        meteorology_scores: dict[str, Any],
    ) -> tuple[str, str]:
        """生成主要判断和风险等级。门控不过 → 一律"证据不足/不确定"。

        V9.5 默认规则 ``score_argmax``：在三类**证据签名**（同构、可比）上取
        最大值，但要求 (1) 最大值达到 min_strength，(2) 与次大值的差距达到
        dominance_margin，否则判"证据不足"。这直接修掉了旧优先级链的两个
        结构缺陷：交通证据一旦通过门控就把积聚/区域"吞并"；以及判定完全由
        硬阈值串决定、得分高低反而无关。支配边际同时充当不确定性信号——
        V9.4 诊断发现 anomaly 与"判对与否"不相关，不能用来度量把握程度。

        ``judgement_rule=legacy_priority_chain`` 保留旧逻辑，供消融对照。
        """
        rules = self._rules or load_source_rules()
        gate_min = float(rules["gate_anomaly_min"])

        anomaly = air_scores.get("anomaly_score")
        if anomaly is None or anomaly < gate_min:
            # 明确输出"证据不足"：污染未达研判门控，不做来源归属
            return "Insufficient evidence", "low"

        traffic = source_attribution.get("traffic_source", 0.0)
        local = source_attribution.get("local_accumulation", 0.0)
        regional = source_attribution.get("regional_transport", 0.0)
        traffic_allowed = source_attribution.get("traffic_available", False)
        local_allowed = source_attribution.get("local_available", False)
        regional_allowed = source_attribution.get("regional_available", False)

        rule = str(rules.get("judgement_rule") or "legacy_priority_chain")
        signature_text = {
            "traffic": "Traffic source dominant",
            "local": "Local accumulation dominant",
            "regional": "Regional transport dominant",
        }

        if rule == "score_argmax":
            scores = source_attribution.get("signature_scores") or {}
            allowed = source_attribution.get("signature_available") or {}
            candidates = {k: float(v) for k, v in scores.items() if allowed.get(k)}
            if not candidates:
                main_judgement = "Mixed / uncertain sources"
            else:
                ranked = sorted(candidates.items(), key=lambda kv: (-kv[1], kv[0]))
                top_name, top = ranked[0]
                second = ranked[1][1] if len(ranked) > 1 else 0.0
                min_strength = float(rules.get("min_strength", 0.0))
                margin = float(rules.get("dominance_margin", 0.0))
                if top >= min_strength and (top - second) >= margin:
                    main_judgement = signature_text[top_name]
                else:
                    main_judgement = "Mixed / uncertain sources"
        else:
            # 旧优先级链（消融对照用）：区域 → 交通(+积聚) → 交通 → 积聚
            traffic_th = float(rules["traffic_threshold"])
            local_th = float(rules["local_threshold"])
            regional_th = float(rules["regional_threshold"])
            if regional_allowed and regional >= regional_th:
                main_judgement = "Regional transport dominant"
            elif traffic_allowed and local_allowed and traffic >= traffic_th and local >= local_th:
                main_judgement = "Traffic source with local accumulation"
            elif traffic_allowed and traffic >= traffic_th:
                main_judgement = "Traffic source dominant"
            elif local_allowed and local >= local_th:
                main_judgement = "Local accumulation dominant"
            else:
                main_judgement = "Mixed / uncertain sources"

        scores = [s for s, ok in ((traffic, traffic_allowed), (local, local_allowed),
                                  (regional, regional_allowed)) if ok]
        top = max(scores) if scores else 0.0
        if top >= float(rules["risk_high"]):
            risk_level = "high"
        elif top >= float(rules["risk_medium"]):
            risk_level = "medium"
        else:
            risk_level = "low"
        return main_judgement, risk_level

    def _generate_evidence(
        self,
        task: dict[str, Any],
        air_scores: dict[str, Any],
        traffic_scores: dict[str, Any],
        meteorology_scores: dict[str, Any],
        spatial_scores: dict[str, Any],
        source_attribution: dict[str, Any],
    ) -> list[str]:
        """证据链：每条都带数据来源与数值，缺测也如实记录。"""
        evidence: list[str] = []
        station_name = task.get("station_name", "")

        # 1. 门控
        anomaly = air_scores.get("anomaly_score")
        if anomaly is not None:
            evidence.append(
                f"污染门控：{station_name} 异常分 {anomaly:.2f}（EPD 逐小时数据）。"
            )
        else:
            evidence.append("污染门控：EPD 数据缺失，无法判断污染是否升高。")

        # 2. 交通源证据
        traffic_score = traffic_scores.get("traffic_score")
        if traffic_score is not None:
            evidence.append(
                f"交通源可能性={source_attribution.get('traffic_source', 0):.2f} "
                f"（交通压力 {traffic_score:.2f}，ATC 车种数据）。"
            )
        else:
            evidence.append("交通源：ATC 交通数据缺失，本窗口不判定交通源。")

        no2_signal = air_scores.get("no2_score")
        if no2_signal is not None:
            evidence.append(
                f"日内交通信号分 {no2_signal:.2f}（EPD NO2/NOx 高峰形态），"
                f"阈值 {self._rules_or_load().get('traffic_signal_min', 0)}。"
            )
        nox_pct = air_scores.get("nox_year_percentile")
        if nox_pct is not None:
            evidence.append(
                f"NOx 年内分位 {nox_pct:.2f}（窗口均值相对本站当年分布，"
                f"门控 {self._rules_or_load().get('traffic_nox_pct_min', 'N/A')}）。"
            )
        clim_ratio = air_scores.get("traffic_hour_clim_ratio")
        if clim_ratio is not None:
            evidence.append(
                f"交通气候态比值 {clim_ratio:.2f}（窗口 NO2 / 本站当年 11-15 时基线，"
                f"阈值 {self._rules_or_load().get('traffic_clim_ratio_min', 'N/A')}）。"
            )
        if air_scores.get("traffic_signal") == "strong":
            evidence.append(f"NO2/NOx 在 {station_name} 高峰时段显著上升（EPD）。")

        # 3. 气象证据
        mean_ws = meteorology_scores.get("mean_wind_speed")
        if mean_ws is not None:
            evidence.append(
                f"风速 {mean_ws:.2f} m/s（Open-Meteo），"
                + ("扩散条件差，利于局地积聚。" if mean_ws < 1.5 else "扩散条件可。")
            )
        else:
            evidence.append("气象数据缺失，无法评估积聚/传输条件。")

        wind_dir = meteorology_scores.get("wind_direction")
        if wind_dir is not None:
            evidence.append(
                f"主导风向 {wind_dir:.0f}°，"
                f"上风向 {meteorology_scores.get('upwind_direction', 'N/A')}。"
            )

        total_precip = meteorology_scores.get("total_precipitation")
        if total_precip is not None and total_precip < 1.0:
            evidence.append(
                f"降水量 {total_precip:.2f} mm，湿清除弱，利于污染物累积。"
            )

        # 3.5 多站同步证据
        sync = air_scores.get("multi_station_sync") or {}
        if sync.get("stations_checked"):
            evidence.append(
                f"多站同步：全站 {sync['stations_checked']} 站中 "
                f"{sync['stations_above_p90']} 站窗口均值超自身 p90"
                + (f"（{', '.join(s['station'] for s in sync.get('above_p90_stations', [])[:3])}）。"
                   if sync.get("above_p90_stations") else "。")
            )

        # 4. 空间匹配证据
        road_links = [r for r in (spatial_scores.get("road_links") or []) if r]
        if road_links:
            evidence.append(
                f"上风向匹配道路：{', '.join(road_links[:3])}（路网+POI 空间匹配）。"
            )
        else:
            evidence.append("上风向道路匹配为空，无空间侧交通证据。")

        contrast = air_scores.get("spatial_contrast") or {}
        if contrast.get("mean_ratio") is not None:
            evidence.append(
                f"空间对比度：全站窗口均值/各自 p90 = {contrast['mean_ratio']:.2f}"
                f"（离散度 {contrast['cv']:.2f}）——均匀抬升指向区域传输，"
                f"集中抬升指向交通/积聚。"
            )

        # 5. 证据签名对照（V9.5）：三类同构、可直接比较；给出支配边际与判定门槛
        signature = source_attribution.get("signature_scores") or {}
        if signature:
            names = {"traffic": "交通", "local": "局地积聚", "regional": "区域传输"}
            parts = [f"{names.get(k, k)} {v:.2f}" for k, v in signature.items()]
            evidence.append(
                "证据签名（同构可比）：" + "、".join(parts)
                + f"；判定门槛 {self._rules_or_load().get('min_strength', 'N/A')}，"
                f"支配边际 {self._rules_or_load().get('dominance_margin', 'N/A')}。"
            )

        return evidence

    def _rules_or_load(self) -> dict[str, Any]:
        return self._rules or load_source_rules()

    def run(
        self,
        task: dict[str, Any],
        air_result: Optional[dict[str, Any]] = None,
        meteo_result: Optional[dict[str, Any]] = None,
        traffic_result: Optional[dict[str, Any]] = None,
        spatial_result: Optional[dict[str, Any]] = None,
        rules: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """运行污染来源分析。``rules`` 供基准脚本在开发集上做阈值搜索。"""
        created_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        station_name = task.get("station_name", "")
        self._rules = rules  # None 时读 config 文件
        all_notes: list[str] = []

        air_scores, air_notes = self._extract_air_scores(air_result)
        traffic_scores, traffic_notes = self._extract_traffic_scores(traffic_result)
        meteorology_scores, meteo_notes = self._extract_meteorology_scores(meteo_result)
        spatial_scores, spatial_notes = self._extract_spatial_scores(spatial_result)
        all_notes.extend(air_notes + traffic_notes + meteo_notes + spatial_notes)

        source_attribution = self._compute_source_attribution(
            air_scores, traffic_scores, meteorology_scores, spatial_scores, station_name
        )
        main_judgement, risk_level = self._generate_judgement(
            source_attribution, air_scores, meteorology_scores
        )
        evidence = self._generate_evidence(
            task, air_scores, traffic_scores, meteorology_scores, spatial_scores,
            source_attribution
        )

        # 置信度按"证据到齐程度"计算，缺关键证据时压低
        present = sum(1.0 for ok in (
            air_result and air_result.get("status") == "success",
            meteo_result and meteo_result.get("status") == "success",
            traffic_result and traffic_result.get("status") == "success",
            spatial_result and spatial_result.get("status") == "success",
        ) if ok)
        confidence = 0.25 + 0.65 * present / 4.0

        result = {
            "agent_name": self.name,
            "status": "success",
            "result": {
                "source_attribution": source_attribution,
                "main_judgement": main_judgement,
                "risk_level": risk_level,
                "component_scores": {
                    "no2_anomaly": air_scores.get("no2_score") or 0,
                    "traffic_pressure": traffic_scores.get("traffic_score") or 0,
                    "upwind_match": spatial_scores.get("upwind_score") or 0,
                    "static_wind": meteorology_scores.get("static_wind_score") or 0,
                    "roadside_boost": 1.0 if station_name in ROADSIDE_STATIONS else 0.4,
                    "anomaly_score": air_scores.get("anomaly_score") or 0,
                },
                "meteorology_summary": {
                    "wind_direction": meteorology_scores.get("wind_direction"),
                    "mean_wind_speed": meteorology_scores.get("mean_wind_speed"),
                    "rain_removal_score": meteorology_scores.get("rain_removal_score"),
                    "total_precipitation": meteorology_scores.get("total_precipitation"),
                },
                "spatial_summary": {
                    "spatial_confidence": spatial_scores.get("spatial_confidence"),
                    "upwind_roads_count": spatial_scores.get("upwind_roads_count", 0),
                },
            },
            "evidence": evidence,
            "confidence": round(confidence, 2),
            "data_quality": {
                "missing_rate": round(len(all_notes) / 4.0, 2),
                "note": "；".join(all_notes) if all_notes else "上游数据完整",
            },
            "created_at": created_at,
            "meta": {
                "task_id": task.get("task_id"),
                "station_name": station_name,
            },
        }
        return result


def main():
    """示例运行"""
    task = {
        "task_id": "HK_MK_2023_0800",
        "station_name": "Mong Kok Roadside",
        "station_id": "mk_roadside",
        "start_time": "2023-08-15 08:00:00",
        "end_time": "2023-08-15 11:00:00",
        "lat": 22.3193,
        "lon": 114.1694,
    }

    air_result = {
        "agent_name": "AirQualityAgent",
        "status": "success",
        "result": {
            "pollution_status": "abnormal",
            "main_pollutants": ["NO2", "PM2.5"],
            "aqhi_level": "high",
            "traffic_pollutant_signal": "strong",
            "traffic_pollutant_signal_score": 0.85,
            "anomaly_score": 0.75,
            "roadside_station": True,
        },
    }

    traffic_result = {
        "agent_name": "TrafficAgent",
        "status": "success",
        "result": {
            "traffic_pressure": "high",
            "traffic_pressure_score": 0.78,
            "dominant_vehicle_types": ["Taxi", "Private Car"],
            "road_links": ["King's Road", "Nathan Road"],
        },
    }

    meteo_result = {
        "agent_name": "MeteorologyAgent",
        "status": "success",
        "result": {
            "weather_risk": 0.65,
            "risk_type": "poor_dispersion",
            "dispersion_condition": "poor",
            "static_wind_score": 0.72,
            "wind_shift_score": 0.35,
            "mean_wind_speed": 1.2,
            "dominant_wind_direction": 135,
            "upwind_direction": "SSE",
            "downwind_direction": "NNW",
            "total_precipitation": 0.5,
        },
    }

    spatial_result = {
        "agent_name": "SpatialAgent",
        "status": "success",
        "result": {
            "upwind_roads": [
                {"road_name": "Nathan Road", "distance_to_station_km": 0.5, "upwind_alignment": 0.85},
                {"road_name": "King's Road", "distance_to_station_km": 0.8, "upwind_alignment": 0.65},
            ],
            "affected_area": "downwind buffer within 1-3 km",
            "sensitive_poi_count": 8,
            "spatial_confidence": "high",
        },
    }

    agent = SourceAgent()
    result = agent.run(task, air_result, meteo_result, traffic_result, spatial_result)

    import json as _json
    print(_json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
