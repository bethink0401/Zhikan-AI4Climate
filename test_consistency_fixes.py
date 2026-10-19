# -*- coding: utf-8 -*-
"""
一致性修复测试用例

测试内容：
1. SourceAgent上风向道路数量统计
2. ReportAgent气象结论一致性
3. ReportAgent使用真实AirQuality证据
4. 中文映射转换
5. 行动建议标点修复
6. 端到端集成测试
"""

import unittest
import json
from agents.source_agent import SourceAgent
from agents.report_agent import ReportAgent


class TestConsistencyFixes(unittest.TestCase):
    
    def setUp(self):
        """测试初始化"""
        self.source_agent = SourceAgent()
        self.report_agent = ReportAgent()
        
        # 基础task
        self.task = {
            "task_id": "HK_MK_2023_0800",
            "station_name": "Mong Kok Roadside",
            "station_id": "mk_roadside",
            "start_time": "2023-08-15 08:00:00",
            "end_time": "2023-08-15 11:00:00",
            "lat": 22.3193,
            "lon": 114.1694,
        }
    
    def test_1_upwind_roads_count(self):
        """测试1：真实上风向道路数量"""
        print("\n=== 测试1：上风向道路数量统计 ===")
        
        # 模拟SpatialAgent输出15条道路
        spatial_result = {
            "agent_name": "SpatialAgent",
            "status": "success",
            "result": {
                "upwind_roads": [
                    {"road_name": f"Road_{i}", "distance_to_station_km": 0.5 + i*0.1, "upwind_alignment": 0.8 - i*0.05}
                    for i in range(15)  # 15条道路
                ],
                "affected_area": "downwind sector within 3 km",
                "spatial_confidence": "high",
            }
        }
        
        # 运行SourceAgent
        source_result = self.source_agent.run(
            self.task,
            spatial_result=spatial_result
        )
        
        # 断言：上风向道路数量应为15，不是1
        upwind_roads_count = source_result["result"]["spatial_summary"]["upwind_roads_count"]
        print(f"上风向道路数量: {upwind_roads_count}")
        
        self.assertEqual(upwind_roads_count, 15, "上风向道路数量应为15")
        self.assertNotEqual(upwind_roads_count, 1, "不能再固定为1")
        
        print("✓ 测试1通过：上风向道路数量正确统计")
    
    def test_2_meteorology_consistency(self):
        """测试2：气象结论一致性"""
        print("\n=== 测试2：气象结论一致性 ===")
        
        # 模拟MeteorologyAgent输出
        meteo_input = {
            "agent_name": "MeteorologyAgent",
            "status": "success",
            "result": {
                "mean_wind_speed": 1.1925,
                "dispersion_condition": "一般",
                "risk_type": "high",
                "weather_risk": 0.7237
            }
        }
        
        # 模拟报告输入数据
        report_data = {
            "task": self.task,
            "meteorology": meteo_input,
            "air_quality": {"result": {}},
            "traffic": {"result": {}},
            "spatial": {"result": {}},
            "source": {"result": {}},
            "decision": {"result": {}},
        }
        
        # 运行ReportAgent
        report_result = self.report_agent.run(report_data)
        report_text = report_result["result"]["report_text"]
        
        print("气象章节内容摘录：")
        lines = report_text.split('\n')
        meteo_section = ""
        capture = False
        for line in lines:
            if "三、气象扩散条件分析" in line:
                capture = True
            elif capture and line.startswith("四、"):
                break
            elif capture:
                meteo_section += line + "\n"
        
        print(meteo_section.strip())
        
        # 断言：应包含MeteorologyAgent的正式结论
        self.assertIn("平均风速为1.19 m/s", report_text, "应使用mean_wind_speed")
        self.assertIn("扩散条件为\"一般\"", report_text, "应使用dispersion_condition")
        self.assertIn("综合气象风险等级为\"高\"", report_text, "应使用risk_type中文映射")
        
        # 断言：不应出现自行生成的覆盖结论
        self.assertNotIn("扩散条件极差", report_text, "不应覆盖正式结论")
        
        print("✓ 测试2通过：气象结论一致性正确")
    
    def test_3_real_air_evidence(self):
        """测试3：Air真实evidence"""
        print("\n=== 测试3：真实AirQuality证据 ===")
        
        # 模拟AirQualityAgent外层包装输入
        air_input = {
            "agent_name": "AirQualityAgent",
            "status": "success",
            "result": {
                "main_pollutants": ["CO", "NOX"],
                "anomaly_score": 0.94
            },
            "evidence": [
                "Main pollutants: CO, NOX.",
                "Anomaly score is 0.94, indicating significant pollution."
            ]
        }
        
        # 模拟报告输入数据
        report_data = {
            "task": self.task,
            "air_quality": air_input,  # 使用完整包装
            "meteorology": {"result": {}},
            "traffic": {"result": {}},
            "spatial": {"result": {}},
            "source": {"result": {}},
            "decision": {"result": {}},
        }
        
        # 运行ReportAgent
        report_result = self.report_agent.run(report_data)
        report_text = report_result["result"]["report_text"]
        
        print("空气质量章节内容摘录：")
        lines = report_text.split('\n')
        air_section = ""
        capture = False
        for line in lines:
            if "二、空气质量异常情况" in line:
                capture = True
            elif capture and line.startswith("三、"):
                break
            elif capture:
                air_section += line + "\n"
        
        print(air_section.strip())
        
        # 断言：应使用真实evidence
        self.assertTrue(
            "Main pollutants: CO, NOX" in report_text or "CO" in report_text,
            "应体现真实污染物CO、NOX"
        )
        self.assertTrue(
            "0.94" in report_text or "异常评分" in report_text,
            "应体现异常评分0.94"
        )
        
        # 断言：不应使用固定文本
        self.assertNotIn("实时监测数据触发告警阈值", report_text, "不应使用固定文本")
        
        print("✓ 测试3通过：真实AirQuality证据使用正确")
    
    def test_4_chinese_mapping(self):
        """测试4：中文映射"""
        print("\n=== 测试4：中文映射转换 ===")
        
        # 模拟各Agent输出
        source_input = {
            "result": {
                "main_judgement": "Traffic source with local accumulation"
            }
        }
        
        spatial_input = {
            "result": {
                "affected_area": "downwind sector within 3 km"
            }
        }
        
        decision_input = {
            "result": {
                "decision_level": "high",
                "actions": ["建议A", "建议B"]
            }
        }
        
        # 模拟报告输入数据
        report_data = {
            "task": self.task,
            "air_quality": {"result": {}},
            "meteorology": {"result": {}},
            "traffic": {"result": {}},
            "spatial": spatial_input,
            "source": source_input,
            "decision": decision_input,
        }
        
        # 运行ReportAgent
        report_result = self.report_agent.run(report_data)
        report_text = report_result["result"]["report_text"]
        
        print("中文映射检查：")
        
        # 断言：应包含中文映射
        self.assertIn("交通源影响并伴随局地积聚", report_text, "source判断应使用中文")
        self.assertIn("下风向3 km影响扇区", report_text, "空间区域应使用中文")
        self.assertIn("高等级响应", report_text, "决策等级应使用中文")
        
        # 断言：报告正文不得直接出现英文枚举
        self.assertNotIn("Traffic source with local accumulation", report_text, "不应出现英文枚举")
        self.assertNotIn("downwind sector within 3 km", report_text, "不应出现英文枚举")
        
        print("✓ 测试4通过：中文映射转换正确")
    
    def test_5_punctuation_fix(self):
        """测试5：标点修复"""
        print("\n=== 测试5：行动建议标点修复 ===")
        
        # 模拟DecisionAgent输出（带重复标点）
        decision_input = {
            "result": {
                "decision_level": "high",
                "actions": [
                    "建议早高峰重点道路实施短时分流引导。",
                    "建议增加公交运力以替代私家车出行。",
                    "建议发布健康防护提醒。"
                ]
            }
        }
        
        # 模拟报告输入数据
        report_data = {
            "task": self.task,
            "air_quality": {"result": {}},
            "meteorology": {"result": {}},
            "traffic": {"result": {}},
            "spatial": {"result": {}},
            "source": {"result": {}},
            "decision": decision_input,
        }
        
        # 运行ReportAgent
        report_result = self.report_agent.run(report_data)
        report_text = report_result["result"]["report_text"]
        
        print("决策章节内容摘录：")
        lines = report_text.split('\n')
        decision_section = ""
        capture = False
        for line in lines:
            if "七、治理决策与复盘建议" in line:
                capture = True
            elif capture:
                decision_section += line + "\n"
        
        print(decision_section.strip())
        
        # 断言：不存在重复标点
        self.assertNotIn("。；", report_text, "不应出现句号分号连用")
        self.assertNotIn("。。", report_text, "不应出现双句号")
        
        # 断言：应该是正确的分号分隔格式
        self.assertTrue(
            "建议早高峰重点道路实施短时分流引导；建议增加公交运力以替代私家车出行；建议发布健康防护提醒。" in report_text,
            "应使用正确的分号分隔格式"
        )
        
        print("✓ 测试5通过：标点修复正确")
    
    def test_6_end_to_end(self):
        """测试6：完整端到端"""
        print("\n=== 测试6：端到端集成测试 ===")
        
        # 模拟完整链路输入
        air_result = {
            "agent_name": "AirQualityAgent",
            "status": "success",
            "result": {
                "main_pollutants": ["CO", "NOX"],
                "anomaly_score": 0.94,
                "traffic_pollutant_signal_score": 0.85,
                "roadside_station": True
            },
            "evidence": ["Main pollutants: CO, NOX", "Anomaly score is 0.94"]
        }
        
        meteo_result = {
            "agent_name": "MeteorologyAgent", 
            "status": "success",
            "result": {
                "mean_wind_speed": 1.1925,
                "dispersion_condition": "一般",
                "risk_type": "high",
                "weather_risk": 0.7237,
                "static_wind_score": 0.72
            }
        }
        
        traffic_result = {
            "agent_name": "TrafficAgent",
            "status": "success",
            "result": {
                "traffic_pressure_score": 0.78,
                "traffic_pressure": "high"
            }
        }
        
        spatial_result = {
            "agent_name": "SpatialAgent",
            "status": "success", 
            "result": {
                "upwind_roads": [
                    {"road_name": f"Road_{i}", "upwind_alignment": 0.8 - i*0.03}
                    for i in range(15)
                ],
                "affected_area": "downwind sector within 3 km",
                "spatial_confidence": "high"
            }
        }
        
        # 运行SourceAgent
        source_result = self.source_agent.run(
            self.task, air_result, meteo_result, traffic_result, spatial_result
        )
        
        # 模拟DecisionAgent结果
        decision_result = {
            "agent_name": "DecisionAgent",
            "status": "success",
            "result": {
                "decision_level": "high",
                "actions": ["建议交通分流", "建议健康防护"]
            }
        }
        
        # 运行ReportAgent
        report_data = {
            "task": self.task,
            "air_quality": air_result,
            "meteorology": meteo_result,
            "traffic": traffic_result,
            "spatial": spatial_result,
            "source": source_result,
            "decision": decision_result
        }
        
        report_result = self.report_agent.run(report_data)
        
        # 综合检查
        print("端到端检查结果：")
        print(f"SourceAgent状态: {source_result['status']}")
        print(f"ReportAgent状态: {report_result['status']}")
        print(f"上风向道路数量: {source_result['result']['spatial_summary']['upwind_roads_count']}")
        print(f"报告sections数量: {len(report_result['result']['sections'])}")
        
        # 断言综合结果
        self.assertEqual(source_result["status"], "success", "SourceAgent应成功")
        self.assertEqual(report_result["status"], "success", "ReportAgent应成功") 
        self.assertEqual(source_result["result"]["spatial_summary"]["upwind_roads_count"], 15, "道路数量应为15")
        self.assertEqual(len(report_result["result"]["sections"]), 7, "报告应有7节")
        
        print("✓ 测试6通过：端到端集成测试成功")


def main():
    """运行测试"""
    print("开始运行一致性修复测试...")
    
    # 创建测试套件
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestConsistencyFixes)
    
    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # 输出总结
    print(f"\n测试总结:")
    print(f"运行测试: {result.testsRun}")
    print(f"失败: {len(result.failures)}")
    print(f"错误: {len(result.errors)}")
    
    if result.failures:
        print("\n失败详情:")
        for test, traceback in result.failures:
            print(f"- {test}: {traceback}")
    
    if result.errors:
        print("\n错误详情:")
        for test, traceback in result.errors:
            print(f"- {test}: {traceback}")
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)