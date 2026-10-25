# -*- coding: utf-8 -*-
"""出行建议与路线推荐模块 —— 具象化案例演示。

案例设计
--------
【案例一】奥运站 → 九龙寨城公园（向东北，跨油麻地—九龙城）
    从港铁奥运站出发前往九龙寨城公园（终点吸附到最近路段），
    展示默认演示行程；暴露场由全部 EPD 监测站联合建立。

【案例二】九龙寨城公园 → 奥运站（反向）
    同一走廊的反向行程，用来观察推荐是否会随行程方向改变。

每个案例都在两组真实气象下、针对五种出行习惯画像分别给出推荐，
并允许更换起终点（坐标即参数）。

数据真实性
----------
* 路网：spatial_agent/data/processed/road_network.geojson（香港道路中心线 12833 段）
* 轨道站：spatial_agent/data/processed/poi_sensitive.geojson（rail_transit，去重后 175 站）
* 气象：MeteorologyAgent 实时从 Open-Meteo Archive API 拉取，非硬编码
* 污染 / 交通：与实时 `/api/travel/plan` 同口径 —— 全部 EPD 监测站联合，
  异常分与交通压力分由 AirQualityAgent / TrafficAgent 对各站现场计算后取平均，
  案例里没有手填的风险值。

运行：
    python examples/run_travel_case.py
输出：
    examples/travel_case_output.json  结构化完整结果
    examples/travel_case_report.md    可读案例报告
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.travel_agent import TravelAgent  # noqa: E402
from api.stations import epd_stations  # noqa: E402
from api.travel_api import (  # noqa: E402
    _combined_air_quality,
    _combined_meteorology,
    _combined_traffic,
)
from tools.travel_advice_tools import list_profiles, load_travel_config  # noqa: E402

TZ = ZoneInfo("Asia/Hong_Kong")

OUTPUT_JSON = ROOT / "examples" / "travel_case_output.json"
OUTPUT_REPORT = ROOT / "examples" / "travel_case_report.md"
#: 前端案例页的离线快照。内容是 OUTPUT_JSON 的原样副本，
#: 只在后端不可达时兜底；正常情况下前端走后端 /api/travel/case 读 OUTPUT_JSON。
OUTPUT_FRONTEND_SNAPSHOT = ROOT / "frontend" / "public" / "travel-case.snapshot.json"

#: 污染输入：与实时 `/api/travel/plan` 同口径 —— 全部 EPD 监测站联合
#: （3 路边 + 15 一般），由 api.travel_api 的合并函数现场取真实数据。
EPD_SOURCES = [
    {
        "station_id": item["station_id"],
        "station_name": item["station_name"],
        "lat": item["lat"],
        "lon": item["lon"],
    }
    for item in epd_stations()
]

#: 出行案例。起终点完全由坐标决定，换一对坐标就是换一个行程。
CASES = [
    {
        "case_id": "olympic_to_walled_city_park",
        "label": "案例一｜奥运站 → 九龙寨城公园",
        "direction_note": "向东北，自西九龙经油麻地、九龙城方向前往九龙寨城公园，终点吸附到最近路段。",
        "origin": {"name": "奥运站", "lat": 22.30405, "lon": 114.16259},
        "destination": {"name": "九龙寨城公园", "lat": 22.33224, "lon": 114.19030},
    },
    {
        "case_id": "walled_city_park_to_olympic",
        "label": "案例二｜九龙寨城公园 → 奥运站",
        "direction_note": "反向行程，用于观察推荐是否会随行程方向改变。",
        "origin": {"name": "九龙寨城公园", "lat": 22.33224, "lon": 114.19030},
        "destination": {"name": "奥运站", "lat": 22.30405, "lon": 114.16259},
    },
]

#: 两组对照场景：恶劣与良好，用于观察推荐如何随气象翻转。
SCENARIOS = [
    {
        "scenario_id": "severe_morning_peak",
        "label": "场景 A｜8 月 15 日早高峰（高湿静风 + 空气质量高风险）",
        "start_time": "2023-08-15 08:00:00",
        "end_time": "2023-08-15 11:00:00",
        "air_quality_risk": 0.94,
        "traffic_pressure_score": 0.7363,
        "note": "沿用项目已验证的旺角路边站早高峰污染事件时段。",
    },
    {
        "scenario_id": "clear_autumn_morning",
        "label": "场景 B｜11 月 20 日早间（干燥凉爽 + 空气质量良好）",
        "start_time": "2023-11-20 08:00:00",
        "end_time": "2023-11-20 11:00:00",
        "air_quality_risk": 0.25,
        "traffic_pressure_score": 0.35,
        "note": "同一批路线的对照场景，用于验证推荐是否会随气象与空气质量改变。",
    },
]

PROFILE_ORDER = ["commuter", "parent_with_child", "elderly", "runner", "tourist"]


def _short_time(time_text: str) -> str:
    """把 '2023-08-15 09:00:00+08:00' 压成 '09:00'。"""
    text = str(time_text).replace("T", " ")
    return text.split(" ")[-1][:5]


def _profile_meta(config: dict) -> dict[str, dict]:
    return {profile["profile_id"]: profile for profile in list_profiles(config)}


def run_case() -> dict:
    """运行 案例 × 场景 × 画像，返回结构化案例结果。

    与实时 `/api/travel/plan` 同口径：气象 / 空气质量 / 交通全部由
    api.travel_api 的合并函数对 **全部 EPD 监测站** 现场取真实数据，
    案例里不再出现手填的场景风险值。
    """
    config = load_travel_config()
    agent = TravelAgent()

    scenarios_out = []
    for scenario in SCENARIOS:
        task_window = {
            "task_id": f"TRAVEL_{scenario['scenario_id']}",
            "start_time": scenario["start_time"],
            "end_time": scenario["end_time"],
        }
        # 18 站联合输入：气象（Open-Meteo 矢量平均）、空气（EPD 库）、交通（ATC 库）
        meteorology_service = _combined_meteorology(task_window)
        air_quality_result = _combined_air_quality(task_window)
        traffic_result = _combined_traffic(task_window)
        # 场景元数据用真实联合值（下游摘要 / 报告直接展示）
        scenario["air_quality_risk"] = (air_quality_result.get("result") or {}).get(
            "anomaly_score"
        )
        scenario["traffic_pressure_score"] = (traffic_result.get("result") or {}).get(
            "traffic_pressure_score"
        )

        cases_out = []
        for case in CASES:
            runs: dict[str, dict] = {}
            for profile_id in PROFILE_ORDER:
                task = {
                    "task_id": f"TRAVEL_{case['case_id']}_{scenario['scenario_id']}_{profile_id}",
                    "origin": case["origin"],
                    "destination": case["destination"],
                    "start_time": scenario["start_time"],
                    "end_time": scenario["end_time"],
                    "travel_profile": profile_id,
                    "pollution_sources": EPD_SOURCES,
                }
                runs[profile_id] = agent.run(
                    task,
                    meteorology_result=meteorology_service,
                    air_quality_result=air_quality_result,
                    traffic_result=traffic_result,
                )
            cases_out.append(
                {
                    "case_id": case["case_id"],
                    "label": case["label"],
                    "direction_note": case["direction_note"],
                    "origin": case["origin"],
                    "destination": case["destination"],
                    "runs": runs,
                }
            )

        scenarios_out.append(
            {
                "scenario_id": scenario["scenario_id"],
                "label": scenario["label"],
                "note": scenario["note"],
                "start_time": scenario["start_time"],
                "end_time": scenario["end_time"],
                "air_quality_risk": scenario["air_quality_risk"],
                "traffic_pressure_score": scenario["traffic_pressure_score"],
                "meteorology": meteorology_service,
                "cases": cases_out,
            }
        )

    return {
        "case_id": "travel_case_set",
        "title": "出行建议与路线推荐｜具象化案例演示",
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "pollution_sources": EPD_SOURCES,
        "pollution_source": {
            "station_name": f"全部 {len(EPD_SOURCES)} 个 EPD 监测站（联合输入）",
            "station_count": len(EPD_SOURCES),
            "lat": None,
            "lon": None,
        },
        "cases": CASES,
        "scenarios": scenarios_out,
    }


# ── 控制台摘要 ──────────────────────────────────────────────────────────────

def print_summary(output: dict) -> None:
    profiles = _profile_meta(load_travel_config())
    print("=" * 104)
    print(output["title"])
    source = output["pollution_source"]
    print(f"污染输入：{source['station_name']}")
    print("=" * 104)

    for scenario in output["scenarios"]:
        meteorology = scenario["meteorology"]["result"]
        print()
        print(f"■ {scenario['label']}")
        print(
            f"  气象：湿度 {meteorology.get('mean_humidity')}% ｜ 气温 {meteorology.get('mean_temperature')}°C ｜ "
            f"风速 {meteorology.get('mean_wind_speed')} m/s ｜ 降雨 {meteorology.get('total_precipitation')} mm ｜ "
            f"主导风向 {meteorology.get('dominant_wind_direction_label')}"
            f"（下风向 {meteorology.get('downwind_direction')}°）"
        )
        print(f"  污染风险 {scenario['air_quality_risk']} ｜ 交通压力 {scenario['traffic_pressure_score']}")

        for case in scenario["cases"]:
            print()
            print(f"  ── {case['label']}　{case['direction_note']}")
            print(
                f"     {'画像':<10}{'条件分':>7}  {'推荐方式':<9}{'里程':>8}{'耗时':>7}"
                f"{'等效时间':>9}{'吸入EU':>8}   说明"
            )
            for profile_id, run in case["runs"].items():
                if run["status"] == "error":
                    print(f"     {profile_id:<10}{'ERROR':>7}  {run['data_quality']['note']}")
                    continue
                result = run["result"]
                recommendation = result["recommendation"]
                best = next(
                    (o for o in result["route_options"] if o["recommended"]), None
                )
                print(
                    f"     {profiles[profile_id]['profile_name']:<10}"
                    f"{result['travel_condition']['score']:>7.1f}  "
                    f"{recommendation['mode_label']:<9}"
                    f"{(best or {}).get('distance_km', 0):>7.2f}km"
                    f"{(best or {}).get('duration_minutes', 0):>6.0f}m"
                    f"{(best or {}).get('utility_minutes', 0):>8.0f}m"
                    f"{(best or {}).get('intake_index', 0):>8.2f}   "
                    f"{recommendation['reasons'][0][:30]}"
                )
    print()
    print("=" * 104)


# ── 可读案例报告 ────────────────────────────────────────────────────────────

def _route_table(route_options: list[dict]) -> str:
    lines = [
        "| 排名 | 方式 | 路线 | 里程 | 耗时 | 等效时间 | 吸入当量 | 高暴露里程 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for option in route_options:
        exposure = option.get("exposure") or {}
        names = " → ".join(option["road_names"][:3]) or "—"
        marker = "**★**" if option["recommended"] else ""
        lines.append(
            f"| {option['rank']}{marker} | {option['mode_label']} | {names} | "
            f"{option['distance_km']:.2f} km | {option['duration_minutes']:.0f} 分 | "
            f"{option['utility_minutes']:.0f} 分 | {option['intake_index']:.2f} EU | "
            f"{exposure.get('high_exposure_km', 0):.2f} km |"
        )
    return "\n".join(lines)


def write_report(output: dict) -> None:
    profiles = _profile_meta(load_travel_config())
    lines: list[str] = []

    lines.append("# 出行建议与路线推荐｜具象化案例演示")
    lines.append("")
    lines.append(f"> 生成时间：{output['generated_at']}　|　由 `examples/run_travel_case.py` 自动生成")
    lines.append("")
    lines.append("## 一、案例设定")
    lines.append("")
    lines.append("**起终点由坐标决定，换一对坐标就是换一个行程。** 本报告给出两个方向相反的行程：")
    lines.append("")
    lines.append("| 案例 | 起点 | 终点 | 说明 |")
    lines.append("| --- | --- | --- | --- |")
    for case in output["cases"]:
        origin, destination = case["origin"], case["destination"]
        lines.append(
            f"| {case['label']} | {origin['name']}（{origin['lat']}, {origin['lon']}） | "
            f"{destination['name']}（{destination['lat']}, {destination['lon']}） | {case['direction_note']} |"
        )
    lines.append("")
    source = output["pollution_source"]
    lines.append(
        f"**污染输入**：{source['station_name']}。"
        "暴露场由各站异常分（AirQualityAgent 对 EPD 逐小时库现算）与受监测走廊的"
        "交通压力分（TrafficAgent 对 ATC 车型库现算）共同建立。"
    )
    lines.append("")
    severe = next(
        (s for s in output["scenarios"] if s["scenario_id"] == "severe_morning_peak"),
        output["scenarios"][0],
    )
    clear = next(
        (s for s in output["scenarios"] if s["scenario_id"] != severe["scenario_id"]),
        severe,
    )
    lines.append("**数据来源**")
    lines.append("")
    lines.append("| 数据 | 来源 |")
    lines.append("| --- | --- |")
    lines.append("| 路网 | 香港道路中心线 12833 段（项目空间数据集） |")
    lines.append("| 轨道站 | 项目 POI 数据集 rail_transit，去重后 175 站 |")
    lines.append("| 气象 | Open-Meteo Archive API（MeteorologyAgent 实时拉取） |")
    lines.append(
        f"| 污染信号 | AirQualityAgent 对全部 EPD 监测站现算的异常分"
        f"（场景 A {severe['air_quality_risk']}；场景 B {clear['air_quality_risk']}） |"
    )
    lines.append(
        f"| 交通压力 | TrafficAgent 对全部 ATC 监测站现算的压力分"
        f"（场景 A {severe['traffic_pressure_score']}；场景 B {clear['traffic_pressure_score']}） |"
    )
    lines.append("")

    # ── 避污效果对比（核心）──
    lines.append("## 二、避开污染：避污路线与直走路线的对比")
    lines.append("")
    lines.append(
        "下表取场景 A（高湿静风 + 高污染）下、同一出行方式内部的最优避污路线与直走路线对比。"
        "**吸入剂量 = Σ(路段里程 × 该路段暴露指数)**，单位 km·指数，是本模块衡量"
        "\"这条路吸进去多少\"的口径。"
    )
    lines.append("")

    severe = next(
        (s for s in output["scenarios"] if s["scenario_id"] == "severe_morning_peak"),
        output["scenarios"][0],
    )
    lines.append("| 案例 | 方式 | 指标 | 直走最短路线 | 避污路线 | 变化 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for case in severe["cases"]:
        run = case["runs"].get("commuter")
        if not run or run["status"] == "error":
            continue
        options = run["result"]["route_options"]
        for mode in ("drive", "walk"):
            group = [o for o in options if o["mode"] == mode]
            if len(group) < 2:
                continue
            shortest = min(group, key=lambda o: o["distance_km"])
            cleanest = min(
                group,
                key=lambda o: (
                    o["intake_index"],
                    (o.get("exposure") or {}).get("high_exposure_km", 0.0),
                ),
            )
            if shortest["route_id"] == cleanest["route_id"]:
                continue
            mode_label = shortest["mode_label"]
            for metric, getter, unit in (
                ("里程", lambda o: o["distance_km"], "km"),
                ("吸入当量", lambda o: o["intake_index"], "EU"),
                ("耗时", lambda o: o["duration_minutes"], "分"),
                ("高暴露里程", lambda o: (o.get("exposure") or {}).get("high_exposure_km", 0.0), "km"),
            ):
                base, clean = getter(shortest), getter(cleanest)
                delta = clean - base
                if base > 0:
                    pct = f"{delta / base * 100:+.0f}%"
                else:
                    pct = "—"
                lines.append(
                    f"| {case['label'].split('｜')[-1]} | {mode_label} | {metric} | "
                    f"{base:.2f}{unit} | {clean:.2f}{unit} | {pct} |"
                )
            lines.append(
                f"| | | 路线 | {' → '.join(shortest['road_names'][:2])} | "
                f"{' → '.join(cleanest['road_names'][:2])} | |"
            )
    lines.append("")
    lines.append(
        "> 注：避污路线通常更长。只有当\"多走的里程\"带来足够大的暴露下降时，"
        "它才会在该画像的评分中胜出——这由画像的污染敏感度与时间敏感度共同决定。"
    )
    lines.append("")

    # ── 场景 × 画像 ──
    lines.append("## 三、场景与出行习惯如何改变推荐")
    lines.append("")
    for scenario in output["scenarios"]:
        meteorology = scenario["meteorology"]["result"]
        lines.append(f"### {scenario['label']}")
        lines.append("")
        lines.append(
            f"气象条件：湿度 **{meteorology.get('mean_humidity')}%**，"
            f"气温 **{meteorology.get('mean_temperature')}°C**，"
            f"风速 **{meteorology.get('mean_wind_speed')} m/s**，"
            f"累计降雨 **{meteorology.get('total_precipitation')} mm**，"
            f"主导风向 **{meteorology.get('dominant_wind_direction_label')}**"
            f"（下风向 {meteorology.get('downwind_direction')}°）。"
            f"空气质量风险 **{scenario['air_quality_risk']}**。"
        )
        lines.append("")
        for case in scenario["cases"]:
            lines.append(f"**{case['label']}**　{case['direction_note']}")
            lines.append("")
            lines.append("| 画像 | 出行条件分 | 推荐方式 | 里程 | 耗时 | 吸入剂量 | 高暴露里程 | 推荐理由 |")
            lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
            for profile_id, run in case["runs"].items():
                if run["status"] == "error":
                    lines.append(
                        f"| {profiles[profile_id]['profile_name']} | — | — | — | — | — | — | "
                        f"运行失败：{run['data_quality']['note']} |"
                    )
                    continue
                result = run["result"]
                recommendation = result["recommendation"]
                best = next((o for o in result["route_options"] if o["recommended"]), {})
                lines.append(
                    f"| {profiles[profile_id]['profile_name']} | "
                    f"{result['travel_condition']['score']:.1f} | "
                    f"**{recommendation['mode_label']}** | "
                    f"{best.get('distance_km', 0):.2f} km | "
                    f"{best.get('duration_minutes', 0):.0f} 分 | "
                    f"{best.get('intake_index', 0):.2f} EU | "
                    f"{(best.get('exposure') or {}).get('high_exposure_km', 0):.2f} km | "
                    f"{recommendation['reasons'][0]} |"
                )
            lines.append("")

    # ── 路线明细 ──
    lines.append("## 四、路线明细（场景 A｜通勤族）")
    lines.append("")
    for case in severe["cases"]:
        run = case["runs"].get("commuter")
        if not run or run["status"] == "error":
            continue
        lines.append(f"### {case['label']}")
        lines.append("")
        field = run["result"].get("pollution_field") or {}
        peak = field.get("peak") or {}
        lines.append(
            f"暴露场：{field.get('source_count', 0)} 个源"
            f"（监测站 {len(field.get('station_sources', []))} 个 + "
            f"走廊采样点 {field.get('corridor_source_count', 0)} 个），"
            f"峰值相对指数 {peak.get('index', 0):.2f}"
            f"（位于 {peak.get('lon')}, {peak.get('lat')}）。"
        )
        lines.append("")
        lines.append(_route_table(run["result"]["route_options"]))
        lines.append("")
        advice = run["result"].get("pollution_advice") or {}
        if advice.get("notes"):
            lines.append("**避污提示**")
            lines.append("")
            for note in advice["notes"]:
                lines.append(f"- {note}")
            lines.append("")

    # ── 出发时段 × 吸入当量 ──
    lines.append("## 五、出发时段 × 吸入当量")
    lines.append("")
    lines.append(
        "逐小时风场是气象 Agent 已经返回的字段，不需要额外请求：按每个小时的风速风向"
        "重建暴露场，再把推荐方案的分段重新采样，就能回答\"晚走半小时能少吸多少\"。"
        "这一步把原来的出发建议（只看降雨与温湿）变成了定量的污染项。"
    )
    lines.append("")
    for case in severe["cases"]:
        run = case["runs"].get("commuter")
        if not run or run["status"] == "error":
            continue
        curve = run["result"].get("departure_dose_curve")
        lines.append(f"### {case['label']}")
        lines.append("")
        if not curve:
            lines.append("本次未取得可用的逐小时风场，无法生成出发时段曲线。")
            lines.append("")
            continue
        lines.append("| 出发时刻 | 风速 (m/s) | 风向 | 推荐方式 | 吸入当量 (EU) | 等效时间 (分) |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for point in curve["points"]:
            lines.append(
                f"| {_short_time(point['time'])} | {point['wind_speed_ms']:.2f} | "
                f"{point['wind_direction']:.0f}° | {point['recommended_mode']} | "
                f"{point['intake_index']:.2f} | {point['utility_minutes']:.0f} |"
            )
        lines.append("")
        lines.append(f"**{curve['note']}**")
        lines.append("")

    # ── 稳健性 ──
    lines.append("## 六、结论稳健性")
    lines.append("")
    lines.append(
        "筛查级模型最该回答的问题不是\"分数多少\"，而是\"结论会不会因为输入误差翻转\"。"
        "这里对风速（±15%）、风向（±20°）与画像的吸入交换率（±20%）做蒙特卡洛抽样，"
        "每一次都重建暴露场并重算全部候选方案的效用。"
    )
    lines.append("")
    lines.append("| 案例 | 推荐方式 | 推荐方案仍为第一的比例 | 判定 |")
    lines.append("| --- | --- | --- | --- |")
    for case in severe["cases"]:
        run = case["runs"].get("commuter")
        if not run or run["status"] == "error":
            continue
        report = run["result"].get("uncertainty")
        if not report:
            lines.append(f"| {case['label'].split('｜')[-1]} | — | 未能计算 | — |")
            continue
        lines.append(
            f"| {case['label'].split('｜')[-1]} | {run['result']['recommendation']['mode_label']} | "
            f"{report['recommended_share'] * 100:.0f}% | {report['level_label']} |"
        )
    lines.append("")

    # ── 结论 ──
    lines.append("## 七、结论")
    lines.append("")
    lines.append("1. **避污是可被计算并选出真实不同路线的**：模块不再用\"是否在下风向\""
                 "这种粗判据，而是建立 0–1 相对暴露场，用超线性成本让搜索绕开高暴露路段，"
                 "并同时用\"吸入当量\"与\"高暴露里程\"两个指标评价结果。")
    lines.append("2. **吸入量按方式折算才有可比性**：吸入当量 = 平均暴露指数 × 停留分钟 × "
                 "（该段呼吸速率 ÷ 步行呼吸速率）× 该段一次穿透比例，单位 EU。"
                 "同样是主干道上的 3 公里，步行的吸入量是车内的一到两个数量级，"
                 "旧口径（Σ指数×公里）表达不了这一点。")
    lines.append("3. **评分只有一个式子**：U = 户外等效时间 ×(1+画像天气乘数×天气负担) "
                 "+ 画像交换率×吸入当量 + 方式常数 + 超限罚分，全部以分钟计。"
                 "旧的加性扣分模型把\"走多远\"拆成四个高相关项重复计分，"
                 "并以候选集最大值归一化，导致同一条路的得分随\"这次还比了哪几条\"变化。")
    lines.append("4. **耗时不再是恒定速度乘里程**：步行与驾车都计入路口等待（按合并后的"
                 "路口数），驾车另按时段拥堵与道路等级区分。")
    lines.append("5. **避污绕行的代价与收益都由具体行程决定**：同一走廊反向走，最短路线与"
                 "最低暴露路线都未必重合，绕行带来的吸入下降幅度也不同。模块按实际数值说明"
                 "这一趟绕不绕得值，而不是无脑建议绕路：")
    for case in severe["cases"]:
        run = case["runs"].get("commuter") or {}
        options = (run.get("result") or {}).get("route_options") or []
        if not options:
            continue
        group = [item for item in options if item["mode"] == "drive"] or options
        shortest = min(group, key=lambda item: item["distance_km"])
        cleanest = min(
            group,
            key=lambda item: (
                item["intake_index"],
                (item.get("exposure") or {}).get("high_exposure_km", 0.0),
            ),
        )
        intake_short, intake_clean = shortest["intake_index"], cleanest["intake_index"]
        delta = f"{(intake_clean - intake_short) / intake_short * 100:+.0f}%" if intake_short > 0 else "—"
        lines.append(
            f"   - {case['label'].split('｜')[-1]}（驾车，场景 A）：直走最短 "
            f"{shortest['distance_km']:.2f} km / {intake_short:.2f} EU；"
            f"避污 {cleanest['distance_km']:.2f} km / {intake_clean:.2f} EU（吸入 {delta}）。"
        )
    lines.append("")
    lines.append("**局限说明**：暴露指数为筛查级相对值，不含化学反应、地形效应与垂直分层；"
                 "吸入当量按分段呼吸速率与车厢穿透系数折算，是相对口径而非实测吸入量；"
                 "未叠加实时交通流量与临时封路；轨道网络由车站点位近似重建。"
                 "以上假设随每次运行写入输出的 `assumptions` 与 `pollution_field.assumptions` 字段。")
    lines.append("")

    OUTPUT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _compact(output: dict) -> dict:
    """Shrink the case payload without losing information.

    Two things dominate the size: the exposure-field grid (identical for every
    profile in a scenario, so it belongs at scenario level) and the route
    geometry (5 decimal places is already ~1 m, more is noise).
    """
    for scenario in output["scenarios"]:
        grid: list[dict] | None = None
        for case in scenario["cases"]:
            for run in case["runs"].values():
                result = run.get("result") or {}
                field = result.get("pollution_field")
                if field:
                    if grid is None and field.get("grid"):
                        grid = [
                            {
                                "lon": round(sample["lon"], 5),
                                "lat": round(sample["lat"], 5),
                                "index": sample["index"],
                            }
                            for sample in field["grid"]
                        ]
                    field["grid"] = []
                for option in result.get("route_options") or []:
                    option["geometry"] = [
                        [round(point[0], 5), round(point[1], 5)]
                        for point in option["geometry"]
                    ]
        scenario["pollution_grid"] = grid or []
    return output


def write_frontend_snapshot(output: dict) -> None:
    """把完整案例结果原样复制到前端的离线快照。

    这里**不做任何裁剪**。以前这个函数会把暴露场网格隔点抽稀、把候选路线删到
    每种出行方式只剩一条，结果前端看到的和 Agent 的真实输出对不上——
    页面上少了一半网格、少了两条候选路线，排查起来非常费劲。
    快照只用于后端没起来时兜底，内容必须与 OUTPUT_JSON 完全一致。
    """
    OUTPUT_FRONTEND_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FRONTEND_SNAPSHOT.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> None:
    output = _compact(run_case())
    OUTPUT_JSON.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_report(output)
    write_frontend_snapshot(output)
    print_summary(output)
    print(f"[saved] {OUTPUT_JSON}")
    print(f"[saved] {OUTPUT_REPORT}")
    print(f"[saved] {OUTPUT_FRONTEND_SNAPSHOT}")


if __name__ == "__main__":
    main()
