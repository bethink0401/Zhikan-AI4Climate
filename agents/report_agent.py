import json
import os
from datetime import datetime, timezone
from typing import Dict, Any, List


class ReportAgent:
    def __init__(self):
        # Source判断中文映射
        self.SOURCE_LABELS = {
            "Traffic source with local accumulation": "交通源影响并伴随局地积聚",
            "Traffic source dominant": "交通源占主导",
            "Regional transport dominant": "区域传输占主导",
            "Dust source dominant": "道路扬尘源占主导",
            "Photochemical pollution dominant": "光化学污染占主导",
            "Mixed / uncertain sources": "混合来源或来源暂不确定",
            "Insufficient evidence": "证据不足，暂不做来源归属",
        }
        
        # 决策等级中文映射
        self.DECISION_LABELS = {
            "low": "低",
            "medium": "中",
            "high": "高",
            "very_high": "很高",
        }
        
        # 气象风险等级中文映射
        self.RISK_LABELS = {
            "low": "低",
            "medium": "中", 
            "high": "高",
            "very_high": "很高",
            "unknown": "未知",
        }
        
        # 车型中文映射
        self.VEHICLE_LABELS = {
            "Bus": "公交车",
            "Goods Vehicle": "货运车辆",
            "Taxi": "出租车",
            "Private Car": "私家车",
            "Franchised Bus DD": "双层专营公交车",
            "Franchised Bus SD": "单层专营公交车",
            "Non Franchised Bus": "非专营巴士",
            "Goods Vehicle Light": "轻型货车",
            "Goods Vehicle M&H": "中重型货车",
            "Motor Cycle": "摩托车",
            "Private LB": "私家小巴",
            "PLB": "公共小巴",
        }
    def _affected_area_desc(self, value: str) -> str:
        """空间影响区域中文转换"""
        mappings = {
            "downwind sector within 3 km": "下风向3 km影响扇区",
            "station buffer within 3 km": "站点周边3 km缓冲区域",
            "downwind buffer within 1-3 km": "下风向1-3 km缓冲区域",
        }
        return mappings.get(value, value or "站点周边影响区域")
    
    def _air_evidence_desc(self, air: dict, evidence: list) -> str:
        """空气质量证据中文转换"""
        parts = []
        pollutants = air.get("main_pollutants") or []
        if pollutants:
            parts.append(f"主要污染物为{'、'.join(pollutants)}")
        
        if air.get("roadside_station"):
            parts.append("路边监测站信号显示可能存在交通相关污染影响")
        
        anomaly_score = air.get("anomaly_score")
        if anomaly_score is not None:
            try:
                score = float(anomaly_score)
                parts.append(f"异常评分为{score:.2f}，表明污染异常较明显")
            except (TypeError, ValueError):
                pass
        
        if parts:
            return "；".join(parts)
        
        # 如果结构化字段不可用，再处理原始evidence
        translated = []
        for item in evidence or []:
            text = str(item).strip()
            if text.startswith("Main pollutants:"):
                value = text.replace("Main pollutants:", "").strip().rstrip(".")
                translated.append(f"主要污染物为{value}")
            elif "Roadside station signal" in text:
                translated.append("路边监测站信号显示可能存在交通相关污染影响")
            elif text.startswith("Anomaly score is"):
                translated.append("污染异常评分较高")
        
        if translated:
            return "；".join(translated)
        
        return "空气质量模块已完成多污染物异常分析"

    def _unwrap(self, value: Any) -> Dict[str, Any]:
        """解包Agent输出，同时兼容完整包装结果和内部result字典"""
        if not isinstance(value, dict):
            return {}
        inner = value.get("result")
        return inner if isinstance(inner, dict) else value
    
    def run(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """生成污染事件溯源分析报告
        
        Args:
            data: 包含task和各Agent结果的字典
        
        Returns:
            标准Agent输出格式，包含七节报告
        """
        warnings = []
        created_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        
        # 提取task和各Agent包装结果
        task = data.get("task") or {}
        air_wrapper = data.get("air_quality") or {}
        meteo_wrapper = data.get("meteorology") or {}
        traffic_wrapper = data.get("traffic") or {}
        spatial_wrapper = data.get("spatial") or {}
        source_wrapper = data.get("source") or {}
        decision_wrapper = data.get("decision") or {}
        
        # 解包得到内部result
        air = self._unwrap(air_wrapper)
        meteo = self._unwrap(meteo_wrapper)
        traffic = self._unwrap(traffic_wrapper)
        spatial = self._unwrap(spatial_wrapper)
        source = self._unwrap(source_wrapper)
        decision = self._unwrap(decision_wrapper)
        
        # 提取站点名称（优先使用task）
        station = task.get("station_name") or self._extract_station(decision, spatial)
        start_time = task.get("start_time", "早高峰时段")
        end_time = task.get("end_time", "")
        
        pollutants = air.get("main_pollutants") or []
        
        # 气象风速（优先使用mean_wind_speed）
        wind_speed = meteo.get("mean_wind_speed")
        if wind_speed is None:
            wind_speed = meteo.get("latest_wind_speed")
        
        # 读取MeteorologyAgent的正式结论
        dispersion_condition = meteo.get("dispersion_condition")
        risk_type = meteo.get("risk_type")
        weather_risk = meteo.get("weather_risk")
        
        pollution_desc = self._pollution_desc(pollutants)
        
        # 交通源可能性（不是贡献占比）
        traffic_source = source.get("source_attribution", {}).get("traffic_source", 0)
        source_desc = self._source_desc(traffic_source, source.get("main_judgement", ""))
        
        review_items = self._review_desc(pollutants)
        
        # 生成七节报告
        section_1 = self._section_event_overview(
            station, start_time, pollution_desc, air
        )
        
        section_2 = self._section_air_quality(
            air, air_wrapper, pollution_desc
        )
        
        section_3 = self._section_meteorology(
            wind_speed, dispersion_condition, risk_type, weather_risk, meteo
        )
        
        section_4 = self._section_traffic(
            traffic
        )
        
        section_5 = self._section_spatial(
            spatial
        )
        
        section_6 = self._section_source(
            source_desc, source
        )
        
        section_7 = self._section_decision(
            decision, review_items
        )
        
        # 组装完整报告文本
        report_text = f"""
一、事件概况
{section_1}

二、空气质量异常情况
{section_2}

三、气象扩散条件分析
{section_3}

四、交通污染压力分析
{section_4}

五、空间影响范围分析
{section_5}

六、污染源归因分析
{section_6}

七、治理决策与复盘建议
{section_7}
        """.strip()
        
        # 结构化sections
        sections = [
            {"section_id": "event_overview", "title": "一、事件概况", "content": section_1},
            {"section_id": "air_quality", "title": "二、空气质量异常情况", "content": section_2},
            {"section_id": "meteorology", "title": "三、气象扩散条件分析", "content": section_3},
            {"section_id": "traffic", "title": "四、交通污染压力分析", "content": section_4},
            {"section_id": "spatial", "title": "五、空间影响范围分析", "content": section_5},
            {"section_id": "source", "title": "六、污染源归因分析", "content": section_6},
            {"section_id": "decision", "title": "七、治理决策与复盘建议", "content": section_7},
        ]
        
        # 计算置信度
        confidence = 0.88
        if wind_speed is None:
            confidence -= 0.10
        if not source.get("source_attribution"):
            confidence -= 0.10
        if not spatial.get("upwind_roads"):
            confidence -= 0.08
        if not decision.get("actions"):
            confidence -= 0.05
        confidence = round(max(0.60, min(0.98, confidence)), 2)
        
        # 计算缺失率
        missing_count = 0
        total_count = 6
        if not air:
            missing_count += 1
        if not meteo:
            missing_count += 1
        if not traffic:
            missing_count += 1
        if not spatial:
            missing_count += 1
        if not source:
            missing_count += 1
        if not decision:
            missing_count += 1
        missing_rate = round(missing_count / total_count, 2)
        
        # 生成summary（基于真实数据）
        summary = self._generate_summary(source, meteo, decision)
        
        # 确定状态
        status = "success" if missing_rate < 0.3 else "warning"
        
        return {
            "agent_name": "ReportAgent",
            "status": status,
            "result": {
                "report_title": f"{station}路边站污染事件溯源分析报告",
                "report_text": report_text,
                "summary": summary,
                "decision_summary": decision.get("actions", ["建议启动交通分流预案"])[0] if decision.get("actions") else "建议启动交通分流预案",
                "sections": sections,
            },
            "confidence": confidence,
            "evidence": [
                "综合分析空气质量、气象、交通、空间和污染源数据",
                f"基于{len(sections)}节结构化报告",
            ],
            "data_quality": {
                "missing_rate": missing_rate,
                "note": "；".join(warnings) if warnings else "上游数据完整"
            },
            "warnings": warnings,
            "created_at": created_at,
        }
    
    def _section_event_overview(self, station: str, start_time: str, pollution_desc: str, air: dict) -> str:
        """一、事件概况"""
        aqhi_level = air.get("aqhi_level")
        avg_aqhi = air.get("avg_aqhi")
        
        if aqhi_level == "unknown" or avg_aqhi is None:
            aqhi_text = "本时段AQHI数据不可用，空气质量判断基于多污染物小时数据及异常评分"
        else:
            aqhi_text = f"AQHI等级达【{aqhi_level}】级"
        
        return f"系统于{start_time}监测到{station}区域空气质量异常，{pollution_desc}，{aqhi_text}。"
    
    def _section_air_quality(self, air: dict, air_wrapper: dict, pollution_desc: str) -> str:
        """二、空气质量异常情况"""
        # 提取真实证据
        air_evidence = []
        if isinstance(air_wrapper, dict):
            wrapper_evidence = air_wrapper.get("evidence")
            if isinstance(wrapper_evidence, list):
                air_evidence = [
                    str(item)
                    for item in wrapper_evidence
                    if item
                ]
        
        # 使用中文转换的证据
        evidence_text = self._air_evidence_desc(air, air_evidence)
        
        return f"监测数据显示，{evidence_text}。其中{pollution_desc}，需立即启动溯源研判。"
    
    def _section_meteorology(self, wind_speed: float, dispersion_condition: str, risk_type: str, weather_risk: float, meteo: dict) -> str:
        """三、气象扩散条件分析"""
        # 风速描述
        if wind_speed is None:
            wind_text = "目标时段平均风速数据不可用"
        else:
            wind_text = f"目标时段平均风速为{wind_speed:.2f} m/s"
        
        # 扩散条件描述（优先使用正式结论）
        if dispersion_condition:
            dispersion_text = f"气象模块判定扩散条件为\"{dispersion_condition}\""
        else:
            # 兼容回退：如果缺失dispersion_condition，使用旧逻辑
            if wind_speed is not None:
                dispersion_text = f"扩散条件{self._wind_desc_simple(wind_speed)}"
            else:
                dispersion_text = "气象模块未提供明确的扩散条件等级"
        
        # 风险等级中文标签
        risk_type_label = self.RISK_LABELS.get(risk_type, risk_type or "未知")
        
        if weather_risk is not None:
            risk_text = f"综合气象风险等级为\"{risk_type_label}\"（风险分数{weather_risk:.2f}）"
        else:
            risk_text = f"综合气象风险等级为\"{risk_type_label}\""
        
        return (
            f"{wind_text}，{dispersion_text}。"
            f"结合低风速、高湿度、风向变化及降雨清除不足等因素，"
            f"{risk_text}。"
        )
    
    def _section_traffic(self, traffic: dict) -> str:
        """四、交通污染压力分析"""
        # 交通等级转换
        traffic_level_raw = (
            traffic.get("traffic_pressure_level")
            or traffic.get("traffic_pressure")
            or "unknown"
        )
        traffic_level_label = self.RISK_LABELS.get(
            str(traffic_level_raw).lower(),
            str(traffic_level_raw)
        )
        
        traffic_pressure_score = traffic.get("traffic_pressure_score")
        
        if traffic_pressure_score is not None:
            pressure_text = f"交通污染压力等级为\"{traffic_level_label}\"（压力分数{traffic_pressure_score:.2f}）"
        else:
            pressure_text = f"交通污染压力等级为\"{traffic_level_label}\""
        
        # 车型中文转换
        vehicle_values = (
            traffic.get("vehicle_focus")
            or traffic.get("dominant_vehicle_types")
            or []
        )
        vehicle_labels = [
            self.VEHICLE_LABELS.get(str(item), str(item))
            for item in vehicle_values
        ]
        vehicle_text_cn = "、".join(vehicle_labels[:3]) if vehicle_labels else "暂无明确重点车型"
        
        if vehicle_labels:
            vehicle_text = f"，重点车型为{vehicle_text_cn}"
        else:
            vehicle_text = ""
        
        return f"{pressure_text}{vehicle_text}。早高峰时段交通流量大，机动车排放对路边站空气质量影响显著。"
    
    def _section_spatial(self, spatial: dict) -> str:
        """五、空间影响范围分析"""
        affected_area_raw = spatial.get("affected_area", "站点周边区域")
        affected_area = self._affected_area_desc(affected_area_raw)
        poi_count = spatial.get("sensitive_poi_count", 0)
        poi_by_type = spatial.get("sensitive_poi_by_type", {})
        upwind_roads = spatial.get("upwind_roads", [])
        
        # 提取道路名称（优先使用中文名，过滤无效道路）
        road_names = []
        for r in upwind_roads[:5]:  # 只展示前5条
            if not isinstance(r, dict):
                continue
            name = r.get("road_name_zh") or r.get("road_name")
            if not name:
                continue
            name_str = str(name).strip()
            if name_str.lower() == "unnamed road":
                continue
            road_names.append(name_str)
        
        if road_names:
            road_str = "、".join(road_names)
        else:
            road_str = "上风向主要道路"
        
        # POI类型描述
        poi_desc = ""
        if poi_by_type:
            types = []
            if poi_by_type.get("school"):
                types.append(f"学校{poi_by_type['school']}个")
            if poi_by_type.get("healthcare"):
                types.append(f"医疗机构{poi_by_type['healthcare']}个")
            if types:
                poi_desc = f"（包括{'、'.join(types[:2])}）"
        
        return f"空间分析生成{affected_area}。识别到上风向道路：{road_str}等，污染输送作用明显。影响区域内敏感点共{poi_count}个{poi_desc}，需重点防护。"
    
    def _section_source(self, source_desc: str, source: dict) -> str:
        """六、污染源归因分析"""
        main_judgement_raw = source.get("main_judgement", "交通与局地积累复合型")
        main_judgement_cn = self.SOURCE_LABELS.get(main_judgement_raw, main_judgement_raw)
        return f"污染源快速溯源结论：{source_desc}。综合判定为「{main_judgement_cn}」事件。本结果属于风险筛查和溯源研判结果，不代表已确认排放贡献率。"
    
    def _section_decision(self, decision: dict, review_items: str) -> str:
        """七、治理决策与复盘建议"""
        decision_level_raw = decision.get("decision_level", "高")
        decision_level_cn = self.DECISION_LABELS.get(decision_level_raw, decision_level_raw)
        actions = decision.get("actions", [])
        
        # 使用中文转换的影响区域
        suggested_area_raw = decision.get("suggested_area", "监测区域")
        suggested_area = self._affected_area_desc(suggested_area_raw)
        
        # 清洗actions避免重复标点
        clean_actions = []
        for action in actions:
            text = str(action).strip().rstrip("。；;，, ")
            if text:
                clean_actions.append(text)
        
        if clean_actions:
            action_str = "；".join(clean_actions) + "。"
        else:
            action_str = "建议加强实时监测与交通疏导。"
        
        return f"基于当前污染水平，建议执行{decision_level_cn}等级响应：{action_str}重点管控区域为{suggested_area}。后续复盘指标：{review_items}，并同步校验当日气象预报偏差对扩散趋势的影响。"
    
    def _generate_summary(self, source: dict, meteo: dict, decision: dict) -> str:
        """生成摘要（基于真实数据）"""
        main_judgement_raw = source.get("main_judgement", "")
        main_judgement_cn = self.SOURCE_LABELS.get(main_judgement_raw, main_judgement_raw)
        traffic_source = source.get("source_attribution", {}).get("traffic_source", 0)
        risk_type_raw = meteo.get("risk_type", "")
        risk_type_cn = self.RISK_LABELS.get(risk_type_raw, risk_type_raw)
        
        # 交通源可能性描述
        if traffic_source >= 0.7:
            traffic_desc = f"交通源可能性评分为{traffic_source:.2f}"
        elif traffic_source >= 0.5:
            traffic_desc = f"交通源可能性评分为{traffic_source:.2f}"
        else:
            traffic_desc = f"交通源可能性评分为{traffic_source:.2f}"
        
        # 气象条件描述（使用风险措辞）
        if risk_type_raw in ("high", "very_high"):
            meteo_desc = "低风速、高湿度及降雨清除不足可能增加污染物积聚风险"
        else:
            meteo_desc = "气象扩散条件可能对污染物传输产生影响"
        
        return f"本次事件判断为{main_judgement_cn}，{traffic_desc}。{meteo_desc}，建议结合交通与气象监测结果采取相应管控措施。"
    
    def _extract_station(self, decision: dict, spatial: dict) -> str:
        """从decision或spatial提取站点名称（回退方案）"""
        name = decision.get("suggested_area", "路边站")
        if "Mong Kok" in name:
            return "旺角"
        if "Causeway Bay" in name:
            return "铜锣湾"
        if "Central" in name:
            return "中环"
        # 避免把道路名当站点名
        if "Portland" not in name and "Road" not in name and "Street" not in name:
            return name.split(" ")[0]
        return "监测区域"

    def _wind_desc_simple(self, speed: float) -> str:
        """简化风速描述，用于兼容回退"""
        if speed < 1.5:
            return "较差"
        if speed < 3.0:
            return "一般"
        return "较好"

    def _wind_desc(self, speed: float) -> str:
        if speed < 1.5:
            return f"目标时段平均风速{speed:.2f} m/s，属静稳天气，扩散条件极差，污染物易在近地面累积"
        if speed < 3.0:
            return f"目标时段平均风速{speed:.2f} m/s，扩散条件一般，污染物存在局地滞留风险"
        return f"目标时段平均风速{speed:.2f} m/s，扩散条件较好，有利于污染物稀释"

    def _pollution_desc(self, pollutants: List[str]) -> str:
        # 统一大写进行判断
        normalized = {str(x).upper() for x in pollutants}
        
        if "NO2" in normalized and "NOX" in normalized:
            return "氮氧化物（NO2/NOX）出现同步显著攀升"
        if "PM2.5" in normalized or "PM25" in normalized:
            return "细颗粒物（PM2.5）浓度快速积累"
        if "O3" in normalized:
            return "臭氧（O3）浓度异常升高，需警惕光化学反应"
        return f"主要污染物为{'、'.join(pollutants)}" if pollutants else "多项污染物指标异常"

    def _source_desc(self, traffic_source: float, judgement: str) -> str:
        """交通源可能性描述（不是贡献占比）"""
        percentage = traffic_source * 100
        return f"交通源可能性约为{percentage:.0f}%（规则筛查评分）"

    def _review_desc(self, pollutants: List[str]) -> str:
        normalized = {str(x).upper() for x in pollutants}
        
        if "NO2" in normalized or "NOX" in normalized:
            return "重点复盘早高峰NO2峰值时段与周边道路车流量的滞后相关性"
        if "PM2.5" in normalized or "PM25" in normalized:
            return "重点复盘静稳天气持续时长及二次气溶胶生成速率"
        if "O3" in normalized:
            return "重点复盘日照强度及前体物NOx/VOCs的协同变化"
        return "综合复盘污染积累阶段的气象场变化及排放源活动水平"


if __name__ == "__main__":
    file_path = "mock_input.json"   # 这里改成你的现有文件名
    if not os.path.exists(file_path):
        print(f" 找不到 {file_path}，请确认文件在当前目录下。")
    else:
        with open(file_path, "r", encoding="utf-8") as f:
            test_data = json.load(f)

        agent = ReportAgent()
        result = agent.run(test_data)
        print("=" * 60)
        print("报告标题：", result["result"]["report_title"])
        print("\n完整报告：")
        print(result["result"]["report_text"])
        print("\n置信度：", result["confidence"])