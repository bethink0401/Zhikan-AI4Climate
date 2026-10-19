# AirQualityAgent 接口说明

## 1. 概述

AirQualityAgent 负责空气质量异常识别，接入和处理香港 EPD AQHI/多污染物数据，判断目标站点和时间段是否出现污染异常，并识别主要异常污染物。

## 2. 目录结构

```
agents/
  air_quality_agent.py      # Agent 主体
tools/
  air_quality_tools.py      # 工具函数
data_process/
  air_quality.py            # 数据 ETL
  schema_air_quality.sql    # 数据库 Schema
data/
  air_quality.db            # SQLite 数据库
```

## 3. 数据接入

### 3.1 支持格式

- **CSV**：标准 CSV 文件，包含 station_name, time, aqhi, no2, nox, pm25, pm10, o3, so2, co 等字段
- **JSON**：JSON 文件，结构同上

### 3.2 导入命令

```bash
# CSV 导入
python data_process/air_quality.py --csv data/sample_air_quality.csv

# JSON 导入
python data_process/air_quality.py --json data/air_quality.json
```

### 3.3 数据清洗

- **时间格式**：统一为 `YYYY-MM-DD HH:MM:SS`
- **站点名称**：支持中英文别名（如"旺角" → "Mong Kok"）
- **缺失值**：标记为 `None`，不直接删除

## 4. 数据表

### air_quality_hourly

| 字段 | 类型 | 说明 |
|------|------|------|
| station_id | TEXT | 站点 ID |
| station_name | TEXT | 站点名称 |
| station_type | TEXT | 站点类型（general/roadside） |
| time | TEXT | 时间（YYYY-MM-DD HH:MM:SS） |
| aqhi | REAL | 空气质量健康指数 |
| no2 | REAL | 二氧化氮 (μg/m³) |
| nox | REAL | 氮氧化物 (μg/m³) |
| pm25 | REAL | PM2.5 (μg/m³) |
| pm10 | REAL | PM10 (μg/m³) |
| o3 | REAL | 臭氧 (μg/m³) |
| so2 | REAL | 二氧化硫 (μg/m³) |
| co | REAL | 一氧化碳 (mg/m³) |

### air_quality_stations

| 字段 | 类型 | 说明 |
|------|------|------|
| station_id | TEXT | 站点 ID（主键） |
| station_name | TEXT | 站点名称 |
| station_type | TEXT | 站点类型 |
| lat | REAL | 纬度 |
| lon | REAL | 经度 |
| district | TEXT | 区域 |
| is_roadside | INTEGER | 是否为路边站（0/1） |

## 5. Agent 接口

### 5.1 输入（Task JSON）

```json
{
  "task_id": "HK_MK_2023_0800",
  "station_name": "Mong Kok Roadside",
  "start_time": "2023-08-15 08:00:00",
  "end_time": "2023-08-15 11:00:00"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| task_id | string | 建议 | 任务追踪 ID |
| station_name | string | 是 | 站点名称 |
| start_time | string | 是 | 开始时间 |
| end_time | string | 是 | 结束时间 |

### 5.2 输出（标准 JSON）

```json
{
  "agent_name": "AirQualityAgent",
  "status": "success",
  "result": {
    "pollution_status": "abnormal",
    "main_pollutants": ["NOX", "PM25", "NO2"],
    "aqhi_level": "high",
    "avg_aqhi": 5.4,
    "traffic_pollutant_signal": "strong",
    "traffic_pollutant_signal_score": 0.95,
    "anomaly_score": 0.88,
    "roadside_station": true,
    "pollutant_details": {
      "no2": {"avg": 83.67, "max": 98.7, "min": 65.2},
      "nox": {"avg": 120.58, "max": 142.3, "min": 92.1}
    }
  },
  "evidence": [
    "AQHI level is high at Mong Kok Roadside.",
    "NO2 and NOx increased significantly during the selected period.",
    "Roadside station signal indicates possible traffic-related pollution."
  ],
  "confidence": 0.8,
  "data_quality": {
    "missing_rate": 0.0,
    "note": "使用 EPD AQHI/多污染物小时数据；异常指数仅供风险判断。"
  },
  "created_at": "2026-07-25 12:50:55"
}
```

### 5.3 输出字段说明

| 字段 | 说明 |
|------|------|
| result.pollution_status | 污染状态：abnormal / moderate / normal / unknown |
| result.main_pollutants | 主要污染物列表 |
| result.aqhi_level | AQHI 等级：low / moderate / high / very_high |
| result.traffic_pollutant_signal | 交通源信号：strong / moderate / weak / unknown |
| result.anomaly_score | 异常分数 (0-1) |
| result.roadside_station | 是否为路边站 |
| evidence | 证据列表，供溯源/报告引用 |
| confidence | 置信度 (0-1) |

## 6. 异常识别算法

### 6.1 AQHI 分级

| 等级 | AQHI 范围 |
|------|-----------|
| low | < 4.0 |
| moderate | 4.0 - 5.0 |
| high | 5.0 - 7.0 |
| very_high | > 7.0 |

### 6.2 污染物分级

| 污染物 | normal | high | very_high |
|--------|--------|------|-----------|
| NO2 | < 80 | 80-120 | > 120 |
| NOx | < 100 | 100-150 | > 150 |
| PM2.5 | < 25 | 25-50 | > 50 |
| PM10 | < 50 | 50-100 | > 100 |
| O3 | < 100 | 100-160 | > 160 |

### 6.3 交通源信号

- **检测方法**：比较早晚高峰（7-9时、17-19时）与非高峰时段的 NO2/NOx 浓度
- **判断标准**：高峰时段浓度 > 非高峰 × 1.3 时判定为 "strong"

### 6.4 路边站增强

- Mong Kok Roadside、Causeway Bay Roadside、Central Roadside 为路边站
- 路边站异常分数增强 20%，并加 0.1 基础分

## 7. 独立运行

```bash
# 运行 Mong Kok 早高峰示例
python agents/air_quality_agent.py --demo

# 指定数据库
python agents/air_quality_agent.py --db data/air_quality.db

# 指定任务 JSON
python agents/air_quality_agent.py --task-json task.json
```

## 8. 下游对接

AirQualityAgent 的输出可被以下 Agent 消费：

| Agent | 使用字段 |
|-------|----------|
| SourceAgent | anomaly_score, traffic_pollutant_signal, main_pollutants |
| DecisionAgent | pollution_status, aqhi_level |
| ReportAgent | evidence, result 全文 |

## 9. 样例

运行 demo 后输出保存至：`examples/air_quality_agent_last_output.json`
