# Coordinator Agent

主智能体（Master Agent）- 动态路由版本

## 功能

1. **LLM智能调度**: 使用Qwen3-32B分析用户自然语言请求，自动选择需要的子智能体
2. **自动依赖展开**: 根据Agent依赖关系自动扩展执行链
3. **异步执行**: 所有Agent异步执行，提高效率
4. **流式输出**: 支持FastAPI StreamingResponse
5. **统一输出格式**: 所有结果遵循统一JSON格式

## 配置

API配置（在环境变量或代码中）:
- `COORDINATOR_API_KEY`: <your-api-key>（从环境变量 COORDINATOR_API_KEY 读取，勿写入源码或文档）
- `COORDINATOR_BASE_URL`: https://api.siliconflow.cn/v1/chat/completions
- `COORDINATOR_MODEL`: Qwen/Qwen3-32B

## Agent依赖关系

```
AirQualityAgent ─┐
                 │
MeteorologyAgent ├─→ SpatialAgent
                 │
TrafficAgent ────┤

AirQualityAgent ─┐
MeteorologyAgent ├─→ SourceAgent
TrafficAgent ────┤
SpatialAgent ────┘

SourceAgent ─────┐
TrafficAgent ────┤
SpatialAgent ────┤
MeteorologyAgent ├─→ DecisionAgent

AirQualityAgent ─┐
MeteorologyAgent ├─→ ReportAgent
TrafficAgent ────┤
SpatialAgent ────┤
SourceAgent ─────┤
DecisionAgent ───┘
```

## 使用方法

### 命令行

```bash
# 自然语言输入
python agents\coordinator.py --user-input "分析旺角早高峰污染情况"

# 保存结果到文件
python agents\coordinator.py --user-input "分析铜锣湾污染" --output results/output.json

# 流式输出模式
python agents\coordinator.py --user-input "分析中环污染" --stream
```

### Python API

```python
from agents.coordinator import CoordinatorAgent

coordinator = CoordinatorAgent()
result = coordinator.analyze("分析旺角早高峰污染情况")

# 输出结构
print(result["pipeline_status"])  # 所有Agent状态
print(result["air_quality"])      # 空气质量结果
print(result["meteorology"])      # 气象结果
print(result["traffic"])          # 交通结果
print(result["spatial"])          # 空间结果
print(result["source"])           # 源解析结果
print(result["decision"])         # 决策建议
print(result["report"])           # 报告结果
```

### 流式API

```python
async for chunk in coordinator.stream_chat("分析旺角污染"):
    if chunk["type"] == "status":
        print(f"Status: {chunk['message']}")
    elif chunk["type"] == "plan":
        print(f"Selected agents: {chunk['agents']}")
    elif chunk["type"] == "agent_start":
        print(f"Starting: {chunk['agent']}")
    elif chunk["type"] == "agent_finish":
        print(f"Finished: {chunk['agent']}, status={chunk['data']['status']}")
    elif chunk["type"] == "final":
        print("All done!")
        results = chunk["data"]
```

## 输出格式

```json
{
  "task": { ... },              // 解析后的任务
  "air_quality": { ... },       // AirQualityAgent结果
  "meteorology": { ... },       // MeteorologyAgent结果
  "traffic": { ... },           // TrafficAgent结果
  "spatial": { ... },           // SpatialAgent结果
  "source": { ... },            // SourceAgent结果
  "decision": { ... },          // DecisionAgent结果
  "report": { ... },            // ReportAgent结果
  "pipeline_status": {
    "AirQualityAgent": "success|warning|failed",
    "MeteorologyAgent": "success|warning|failed",
    ...
    "overall_status": "success|warning|failed",
    "success_count": 3,
    "warning_count": 1,
    "failed_count": 0,
    "total_agents": 7
  },
  "metadata": {
    "started_at": "...",
    "completed_at": "...",
    "duration_seconds": 2.72,
    "task_id": "..."
  }
}
```

## 状态说明

- **success**: Agent成功执行
- **warning**: Agent执行但有警告（如数据缺失）
- **failed**: Agent执行失败

管道整体状态：
- **success**: 所有Agent都成功
- **warning**: 有Agent返回warning但至少有一个success
- **failed**: 没有任何Agent成功
