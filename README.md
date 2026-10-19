# 香港空气质量污染源溯源多智能体系统

基于多智能体协作的空气质量污染事件快速溯源与决策支持系统，专注于香港路边站交通污染源的实时识别与应急响应。

## 📋 项目概述

本系统采用模块化多智能体架构，整合空气质量监测、气象分析、交通压力评估、空间建模和污染源归因等多个维度，为污染事件提供 7 节结构化溯源报告与分级管控建议，并为公众提供暴露感知的出行建议。

### 核心特性

- **多智能体协同**：8 个专业 Agent（7 个事件分析 Agent + 1 个出行建议 Agent）独立运行，统一输出契约
- **实时数据驱动**：基于香港EPD AQHI数据、Open-Meteo气象数据、HKE ATC交通数据
- **空间建模**：整合POI敏感点、道路网络、风场传输的空间影响评估
- **规则评分**：交通源可能性评分(0-1)，不等同于排放贡献率
- **分级决策**：根据污染程度和来源特征提供4级响应建议
- **中文报告**：生成7节结构化中文报告，支持快速研判

- **暴露感知出行**：以吸入当量 EU 统一量纲，在路线、方式、出发时刻三层联合优化，附蒙特卡洛稳健性等级
- **对话式研判**：规则意图引擎自动出图（风向分布、气象风险雷达等），自由问题由 LLM 结合链路上下文作答
- **契约测试**：201 项契约测试（`pytest tests/ test_consistency_fixes.py test_hourly_series.py`）+ 19 个基准与专项评测脚本（`tests/benchmarks/`）
- **AI 模型层评测**：Qwen3-32B 实跑 25 条自然语言任务——成功返回 25/25、直接完成回答 22/25、数值一致率 0.92（17 条含可核验数值的回答）、多轮参数继承 4/5、无密钥降级 100%（`tests/benchmarks/bench_ai_model_metrics.py`，需 `COORDINATOR_API_KEY`）

## 🧩 依赖与运行

- 基础依赖：`pip install -r requirements.txt`（Python 3.9+，无 GPU 要求）
- 可选：LLM 调度链路需配置 `COORDINATOR_API_KEY` 等环境变量并安装 LLM 相关依赖。**未配置时确定性主链路、四页面功能与离线基准照常运行**（含 AI 规则层评测与无密钥降级验证）；仅 `bench_ai_model_metrics.py` 的模型在线实跑需要凭证，其历史结果与派生指标可由留档回答离线复算（`recompute_ai_metrics.py`）

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Coordinator (调度层)                       │
└─────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
        ▼                     ▼                     ▼
┌──────────────┐      ┌──────────────┐     ┌──────────────┐
│ AirQuality   │      │ Meteorology  │     │   Traffic    │
│   Agent      │      │    Agent     │     │    Agent     │
└──────────────┘      └──────────────┘     └──────────────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                              ▼
                      ┌──────────────┐
                      │   Spatial    │
                      │    Agent     │
                      └──────────────┘
                              │
                              ▼
                      ┌──────────────┐
                      │   Source     │
                      │    Agent     │
                      └──────────────┘
                              │
                              ▼
                      ┌──────────────┐
                      │   Decision   │
                      │    Agent     │
                      └──────────────┘
                              │
                              ▼
                      ┌──────────────┐
                      │   Report     │
                      │    Agent     │
                      └──────────────┘
```

## 🤖 智能体说明

### 1. AirQualityAgent（空气质量分析）
- **输入**：站点名称、时间范围
- **数据源**：EPD AQHI小时数据、多污染物监测数据
- **输出**：污染状态、主要污染物、异常评分(0-1)、路边站增强信号
- **关键指标**：`anomaly_score`, `traffic_pollutant_signal_score`

### 2. MeteorologyAgent（气象扩散分析）
- **输入**：站点坐标、时间范围
- **数据源**：Open-Meteo历史气象数据
- **输出**：扩散条件等级、风速风向、降雨清除效果、气象风险评分
- **关键指标**：`dispersion_condition`(一般/较差/较好), `weather_risk`(0-1)

### 3. TrafficAgent（交通压力评估）
- **输入**：站点名称、时间范围
- **数据源**：HKE ATC车辆分类计数数据
- **输出**：交通压力等级、车型结构、早晚高峰特征、压力评分
- **关键指标**：`traffic_pressure_score`(0-1), `vehicle_focus`

### 4. SpatialAgent（空间影响建模）
- **输入**：站点坐标、气象风向
- **数据源**：香港道路网络GeoJSON、敏感POI数据
- **输出**：上风向道路清单、下风向影响扇区、敏感点统计
- **关键指标**：`upwind_roads_count`, `sensitive_poi_count`

### 5. SourceAgent（污染源归因）
- **输入**：上游4个Agent结果
- **算法**：规则评分模型
- **输出**：5类源可能性评分、主判断类型、风险等级
- **关键指标**：`traffic_source`(0-1), `main_judgement`

**交通源评分公式**：
```python
traffic_source = (
    0.35 * NO2异常分 + 
    0.25 * 交通压力分 + 
    0.20 * 上风向匹配分 + 
    0.10 * 静风累积分 + 
    0.10 * 路边站加权
)
```

### 6. DecisionAgent（分级决策）
- **输入**：SourceAgent、SpatialAgent、TrafficAgent结果
- **规则**：4级响应阈值
- **输出**：决策等级(low/medium/high/very_high)、管控建议、重点区域、重点车型

**决策等级划分**：
- **低**：交通源<0.5 且 气象风险<0.5
- **中**：交通源0.5-0.7 或 气象风险0.5-0.7
- **高**：交通源≥0.7 或 气象风险≥0.7
- **很高**：交通源≥0.8 且 气象风险≥0.8

### 7. ReportAgent（报告生成）
- **输入**：全部上游Agent结果
- **输出**：7节结构化中文报告
- **章节**：
  1. 事件概况
  2. 空气质量异常情况
  3. 气象扩散条件分析
  4. 交通污染压力分析
  5. 空间影响范围分析
  6. 污染源归因分析
  7. 治理决策与复盘建议

## 📊 数据准备

### 1. 空气质量数据
```bash
# 使用EPD实时数据（推荐）
cd data_process
python import_epd_real.py

# 或使用CSV样本数据
python air_quality.py
```

**数据库表结构**：
- `air_quality_hourly`: 小时级AQHI和多污染物浓度
- 字段：`station_name`, `time`, `aqhi`, `no2`, `nox`, `pm25`, `pm10`, `o3`, `so2`, `co`

### 2. 交通数据
```bash
cd data_process
python traffic.py
```

**数据库表结构**：
- `traffic_atc`: ATC站点车型分类小时计数
- 字段：`atc_station_id`, `year`, `month`, `day`, `hour`, `vehicle_type`, `vehicle_count`

### 3. 空间数据
```bash
cd spatial_agent/data_process

# 导入道路网络（需要GeoJSON文件）
python import_roads.py

# 清洗POI数据
python clean_poi.py

# 构建空间数据库
python build_database.py
```

**空间数据库**：
- `roads`: 道路中心线（LineString）
- `sensitive_pois`: 敏感点位（学校、医院、社区中心）

## 🚀 快速开始

### 1. 克隆仓库

#### Windows

1. 安装 [Git for Windows](https://git-scm.com/download/win)（一路默认即可，自带 Git Bash）
2. 安装 Git LFS：管理员 PowerShell 执行 `winget install GitHub.GitLFS`，然后**关掉并重开终端**，执行 `git lfs install`
3. 克隆（Git Bash 或 PowerShell 均可）：

```powershell
git clone https://github.com/bethink0401/WeatherMulti-Agent.git
cd WeatherMulti-Agent
git lfs pull   # 拉取真实数据库（未装 git-lfs 时跳过，数据可稍后重新生成，见「📊 数据准备」）
```

#### macOS

macOS 先装 git-lfs（`brew install git-lfs && git lfs install`），克隆命令相同：

```bash
git clone https://github.com/bethink0401/WeatherMulti-Agent.git
cd WeatherMulti-Agent
git lfs pull
```

### 2. 启动后端（Flask，端口 8000）

```bash
# Python 3.9+，建议虚拟环境
# macOS / Linux:
python3 -m venv .venv && source .venv/bin/activate
# Windows (PowerShell):
#   py -m venv .venv
#   .venv\Scripts\Activate.ps1

pip install -r requirements.txt
pip install -r spatial_agent/requirements.txt

# 启动后端（首次启动会预热 Agent，约需 1 分钟）
python3 api/app.py
```

后端监听 `http://127.0.0.1:8000`。

### 3. 启动前端（Vite + Vue3，端口 5173）

底图使用腾讯地图 GL JS，**密钥已随仓库内置**（`frontend/.env.local`，Windows 下同样直接可用），克隆后无需申请。如需换成自己的 Key，参照 `frontend/.env.local.example` 内的申请步骤。

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 `http://127.0.0.1:5173` 即可。前端已配置调用 `http://127.0.0.1:8000` 的后端接口；若后端未启动，页面会自动降级为内置快照数据并在界面上标注来源。

### 运行示例

#### 1. 完整端到端运行
```bash
# 运行旺角路边站2023年8月15日早高峰案例
python agents/coordinator_min.py

# 查看输出
cat examples/day9_coordinator_output.json
```

**预期输出**：
```json
{
  "pipeline_status": {
    "air_quality_ready": true,
    "meteo_ready": true,
    "traffic_ready": true,
    "spatial_ready": true,
    "source_ready": true,
    "decision_ready": true,
    "report_ready": true,
    "coordinator_min_ok": true
  },
  "source": {
    "result": {
      "source_attribution": {
        "traffic_source": 0.7911
      },
      "main_judgement": "Traffic source with local accumulation",
      "spatial_summary": {
        "upwind_roads_count": 15
      }
    }
  },
  "decision": {
    "result": {
      "decision_level": "high"
    }
  },
  "report": {
    "result": {
      "sections": [...],  // 7节报告
      "report_text": "一、事件概况\n..."
    }
  }
}
```

#### 2. 单独运行各Agent

**空气质量Agent**：
```bash
python agents/air_quality_agent.py --demo
```

**气象Agent**：
```bash
python agents/meteorology_agent.py --demo
```

**交通Agent**：
```bash
python agents/traffic_agent.py --demo
```

**空间Agent**：
```bash
cd spatial_agent
python api.py --demo
```

**污染源Agent**：
```bash
python agents/source_agent.py --demo
```

## 📖 使用示例

### 案例：旺角路边站早高峰污染事件

**任务定义**：
```json
{
  "task_id": "HK_MK_2023_0800",
  "station_name": "Mong Kok Roadside",
  "start_time": "2023-08-15 08:00:00",
  "end_time": "2023-08-15 11:00:00",
  "lat": 22.3193,
  "lon": 114.1694
}
```

**分析结果**：
- **空气质量**：CO、NOX异常，anomaly_score=0.94
- **气象条件**：平均风速1.19 m/s，扩散条件"一般"，气象风险0.72（高）
- **交通压力**：早高峰压力0.74（高），重点车型：公交车、货运车辆、出租车
- **空间影响**：识别15条上风向道路，下风向3 km扇区内343个敏感点
- **污染源归因**：交通源可能性79%，判定为"交通源影响并伴随局地积聚"
- **决策建议**：执行"高"等级响应，建议早高峰分流、增加公交运力、发布健康防护提醒

**完整报告**见：[examples/day9_coordinator_output.json](examples/day9_coordinator_output.json)

## 🔧 配置说明

### 站点配置
站点坐标映射在 `data/station_mapping.json`：
```json
{
  "Mong Kok Roadside": {
    "lat": 22.3193,
    "lon": 114.1694,
    "station_type": "roadside"
  }
}
```

### 气象规则
气象风险判定规则在 `config/meteorology_rules.json`：
- 静风阈值：<1.5 m/s
- 高湿度阈值：≥85%
- 降雨清除阈值：<1.0 mm

### 决策规则
决策等级阈值在 `tools/decision_rules.py`：
```python
DECISION_THRESHOLDS = {
    "traffic_source": 0.5,
    "weather_risk": 0.5,
    "high_level": 0.7,
    "very_high_level": 0.8
}
```

## 🧪 测试

### 运行测试套件

```bash
# 一致性修复测试
python test_consistency_fixes.py

# 中文展示优化测试
python test_report_chinese.py

# 审计第1-9天完成度
python examples/audit_day1_9.py
```

### 数据库诊断

```bash
# 检查空气质量数据库
python examples/diagnose_db.py

# 检查案例站点数据
python examples/check_case_stations.py

# 检查数据库年份覆盖
python examples/check_db_years.py
```

## 📁 项目结构

```
WeatherMulti-Agent_Dong/
├── agents/                      # 智能体模块
│   ├── air_quality_agent.py     # 空气质量Agent
│   ├── meteorology_agent.py     # 气象Agent
│   ├── traffic_agent.py         # 交通Agent
│   ├── spatial_agent.py         # 空间Agent（调用子模块）
│   ├── source_agent.py          # 污染源归因Agent
│   ├── decison_agent.py         # 决策Agent
│   ├── report_agent.py          # 报告生成Agent
│   └── coordinator_min.py       # 最小调度器
├── spatial_agent/               # 空间分析子系统
│   ├── agents/
│   │   └── spatial_agent.py     # 空间Agent实现
│   ├── data_process/            # 空间数据处理
│   └── data/
│       └── processed/
│           └── spatial.db       # 空间数据库（SpatiaLite）
├── tools/                       # 工具函数
│   ├── air_quality_tools.py     # 空气质量工具
│   ├── meteo_tools.py           # 气象工具
│   ├── traffic_tools.py         # 交通工具
│   ├── decision_rules.py        # 决策规则
│   └── decision_templates.py    # 决策模板
├── config/                      # 配置文件
│   ├── field_schema.json        # 字段契约
│   └── meteorology_rules.json   # 气象规则
├── data/                        # 数据目录
│   ├── air_quality.db           # 空气质量数据库（160MB）
│   ├── traffic.db               # 交通数据库
│   └── station_mapping.json     # 站点映射
├── data_process/                # 数据处理脚本
│   ├── import_epd_real.py       # EPD实时数据导入
│   ├── air_quality.py           # 空气质量ETL
│   └── traffic.py               # 交通数据ETL
├── examples/                    # 示例输出
│   ├── day9_coordinator_output.json  # 完整输出样例
│   └── audit_day1_9.py          # 审计脚本
├── docs/                        # 文档
│   ├── api_contract.md          # API契约
│   ├── AirQualityAgent_接口说明.md
│   ├── TrafficAgent_接口说明.md
│   └── 统一调度对接说明.md
└── requirements.txt             # Python依赖
```

## 🔄 数据流

```
1. 输入任务 (task)
   ↓
2. AirQualityAgent → 空气质量异常识别
   ↓
3. MeteorologyAgent → 气象条件分析
   ↓
4. TrafficAgent → 交通压力评估
   ↓
5. SpatialAgent → 空间影响建模（需气象结果）
   ↓
6. SourceAgent → 污染源归因（汇总1-5结果）
   ↓
7. DecisionAgent → 分级决策（基于6的归因结果）
   ↓
8. ReportAgent → 生成7节报告（汇总全部结果）
   ↓
9. 输出 (JSON + 中文报告)
```

## ⚠️ 重要说明

### 规则评分 vs 科学归因
- **规则评分**：本系统基于统计特征和经验规则，给出交通源**可能性评分**(0-1)
- **科学归因**：准确的排放贡献率需要CMB/PMF受体模型、在线源解析或扩散模型反演
- **适用场景**：本系统适用于污染事件快速筛查和应急响应，不等同于科学归因研究

### 数据质量要求
- 空气质量数据需完整覆盖目标时段
- 气象数据精度：Open-Meteo历史数据（2m高度）
- 交通数据：ATC站点需与监测站点距离<3km
- 空间数据：道路网络需包含road_name_zh中文字段

### 路边站增强判断
- 路边站（Roadside Station）对交通污染更敏感
- 系统对Mong Kok/Causeway Bay/Central Roadside启用增强判断
- `roadside_boost`因子：0.75-0.95

## 📈 已验证案例

| 案例ID | 站点 | 时间 | 交通源评分 | 主判断 | 决策等级 |
|--------|------|------|-----------|--------|---------|
| HK_MK_2023_0800 | Mong Kok Roadside | 2023-08-15 08:00-11:00 | 0.79 | 交通源+局地积聚 | 高 |

## 🛠️ 开发指南

### 添加新Agent
1. 继承Agent基类或遵循输出契约（见`docs/api_contract.md`）
2. 实现`run(task)`方法
3. 返回标准JSON格式：`{agent_name, status, result, evidence, confidence, data_quality, created_at}`
4. 在Coordinator中集成

### 修改评分规则
- **SourceAgent**：修改`agents/source_agent.py`中的权重和阈值
- **DecisionAgent**：修改`tools/decision_rules.py`中的决策规则
- **MeteorologyAgent**：修改`config/meteorology_rules.json`中的气象阈值

### 数据契约
所有Agent输出字段定义在 `config/field_schema.json`。修改字段需同步更新：
1. Agent实现
2. Coordinator调用
3. ReportAgent展示
4. 文档说明

## 📞 技术支持

### 常见问题

**Q: 数据库文件损坏（file is not a database）？**  
A: 数据库文件使用Git LFS管理，需运行`git lfs pull`下载真实文件。

**Q: SpatialAgent找不到上风向道路？**  
A: 检查气象风向数据是否可用，风向为null时无法匹配上风向道路。

**Q: 交通源评分为0？**  
A: 检查TrafficAgent是否成功获取ATC数据，站点名称需与ATC站点匹配。

**Q: 报告显示英文证据？**  
A: 确保ReportAgent已更新为中文展示版本（包含`_air_evidence_desc`方法）。

### 日志与调试

每个Agent输出包含：
- `status`: success/warning/error
- `data_quality.missing_rate`: 数据缺失率(0-1)
- `data_quality.note`: 数据质量说明
- `warnings`: 警告信息列表
- `confidence`: 置信度(0-1)

## 📄 许可证

本项目仅供学习研究使用。

## 🙏 致谢

- **数据来源**：香港环保署(EPD)、Open-Meteo、香港电力(HKE)
- **空间数据**：香港政府开放数据平台
- **技术栈**：Python 3.11, SQLite/SpatiaLite, Anthropic Claude

---

**最后更新**：2026-08-07  
**版本**：v1.0 - 第9天完整链路验证版
