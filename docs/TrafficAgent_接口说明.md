# Traffic Agent 接口说明

## 1. 模块职责

处理香港 2018—2024 ATC 交通统计压缩包，解析车型比例 `Pro.` 与载客 `Ocp.`，入库后计算：

- 车型结构（私家车 / 出租车 / 小巴 / 货车 / 巴士）
- **交通污染压力指数**（风险判断用，非精确排放贡献率）
- 重点关注车型（Bus / Goods Vehicle / Taxi 等）

标准输出供 Source Agent、Decision Agent、Report Agent 使用。

## 2. 目录

| 路径 | 说明 |
|------|------|
| `data_process/traffic.py` | zip 解压、xls 解析、宽表转长表、入库 |
| `data_process/schema_traffic.sql` | `traffic_vehicle_mix` / `traffic_volume` / `traffic_station_meta` |
| `tools/traffic_tools.py` | 时段映射、压力指数、车型汇总、证据句 |
| `agents/traffic_agent.py` | Agent 主入口，产出统一 JSON |
| `data/station_mapping.json` | 空气站 → ATC 核心站映射 |
| `examples/run_traffic_cases.py` | Mong Kok / Causeway Bay / Central 样例 |

## 3. 环境与初始化

```bash
pip install -r requirements.txt

# 导入 2023 年案例站（默认）
python data_process/traffic.py --zip "C:\Users\mistakez\Downloads\ATC_TRAFFIC_DATA.zip" --years 2023

# 可选：导入整年全部站点
python data_process/traffic.py --years 2023 --all-stations
```

## 4. 统一输入

```json
{
  "task_id": "HK_MK_2023_0800",
  "station_name": "Mong Kok Roadside",
  "station_id": "xxx",
  "start_time": "2023-08-15 08:00:00",
  "end_time": "2023-08-15 11:00:00",
  "lat": 22.3193,
  "lon": 114.1694
}
```

字段说明：

| 字段 | 含义 |
|------|------|
| `station_name` | 空气质量站名；支持 Mong Kok / Causeway Bay / Central 及中文别名 |
| `start_time` / `end_time` | 分析时段；按小时映射到 ATC `0800-0900` 等 |
| `lat` / `lon` | 预留；当前用站名映射 ATC |

## 5. 统一输出

```json
{
  "agent_name": "TrafficAgent",
  "status": "success",
  "result": {
    "traffic_pressure": "high",
    "traffic_pressure_score": 0.76,
    "dominant_vehicle_types": ["Franchised Bus DD", "Taxi", "Private Car"],
    "heavy_vehicle_signal": "medium",
    "bus_ratio": 0.12,
    "goods_vehicle_ratio": 0.08
  },
  "evidence": [
    "Morning peak period shows high private car and taxi proportion.",
    "Bus and goods vehicle proportions increase traffic-related NOx risk."
  ],
  "confidence": 0.78,
  "data_quality": {
    "missing_rate": 0.0,
    "note": "使用 ATC 2023 年 Mon-Fri 车型结构；压力指数仅供风险判断。"
  },
  "created_at": "2026-xx-xx xx:xx:xx"
}
```

### result 字段

| 字段 | 含义 |
|------|------|
| `traffic_pressure` | `low` / `medium` / `high` |
| `traffic_pressure_score` | 0–1 归一化压力分 |
| `traffic_pressure_index` | Σ(比例×权重) 原始指数 |
| `dominant_vehicle_types` | 占比最高的车型 |
| `heavy_vehicle_signal` | 货车+巴士合计信号 low/medium/high |
| `bus_ratio` / `goods_vehicle_ratio` | 0–1 |
| `vehicle_focus` | 建议关注车型列表 |
| `proportions_pct` | 各车型平均占比（%） |
| `road_links` | 实际使用的 ATC 道路 LINK |

## 6. 压力指数公式

文档建议权重：

`私家车×1.0 + 出租车×1.2 + 小巴×1.5 + 轻型货车×2.0 + 中重型货车×4.0 + 巴士×3.0`

实现：

```
index = Σ (proportion_pct / 100 × weight)
score = min(1, index / 2.8)
```

多时段窗口内取**压力最高的小时段**作为风险分（避免早高峰被平均冲淡）。
## 7. 数据表

### traffic_vehicle_mix

`year, region_or_station, atc_station_id, road_link, day_type, time_slot, vehicle_type, proportion, occupancy, source_file`

### traffic_station_meta

`atc_station_id, year, road_link, road_network, road_type, source_file`

### 案例映射

| 空气站 | 主 ATC |
|--------|--------|
| Mong Kok Roadside | 3006 Nathan Rd |
| Causeway Bay Roadside | 1029 Hennessy Rd |
| Central Roadside | 1030 Connaught Rd C |

## 8. 独立运行

```bash
# 默认导入 2018—2024 全部站点
python data_process/traffic.py --zip "C:\Users\mistakez\Downloads\ATC_TRAFFIC_DATA.zip"

# 仅案例站（更快）
python data_process/traffic.py --case-stations --years 2023

python agents/traffic_agent.py --demo
python examples/run_traffic_cases.py
```

说明：ATC 中约 40% 站点无 OCC（车型分类）sheet，只有含 OCC 的站会写入 `traffic_vehicle_mix`；`traffic_station_meta` 仍保留全部核心站元数据。案例站 3006/1029/1030 七年均有 OCC。
## 9. 异常约定

| 情况 | 行为 |
|------|------|
| 数据缺失 | `status=warning`，`data_quality.note` 说明，不抛致命异常 |
| 请求年份无库 | 回退到最近已导入年份，并在 note 中说明 |
| 主站无行 | 自动扩展 nearby ATC 站 |

## 10. 第9天联调入口

```bash
python agents/coordinator_min.py
```

- `TrafficAgent`：真实计算  
- `SourceAgentStub`：按分工表公式消费 Traffic（及其他占位上游）  
- Air / Meteo / Spatial / Decision / Report：占位 warning，组员合入后替换即可  
