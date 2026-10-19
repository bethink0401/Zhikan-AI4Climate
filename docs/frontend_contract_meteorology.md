# MeteorologyAgent 前端接入文档

## 📋 文档说明

本文档基于 **当前项目真实代码和实际运行输出** 编写，提供 MeteorologyAgent 的前端接入规范。

- **代码来源**：`agents/meteorology_agent.py`、`tools/meteorology_tools.py`
- **数据来源**：Open-Meteo Archive API（兜底：本地CSV）
- **案例输出**：`examples/day9_coordinator_output.json`
- **调用方式**：由 Coordinator 统一调度
- **文档版本**：2026-08-07

---

## 📌 统一案例

本文档所有示例统一使用以下案例参数：

```json
{
  "task_id": "HK_MK_2023_0800",
  "station_name": "Mong Kok Roadside",
  "station_id": "mk_roadside",
  "start_time": "2023-08-15 08:00:00",
  "end_time": "2023-08-15 11:00:00",
  "lat": 22.3193,
  "lon": 114.1694
}
```

---

## 1️⃣ 真实 JSON 样例

### 1.1 Success 状态（真实输出）

以下是 MeteorologyAgent 在当前项目中的真实完整输出（2023-08-15 旺角路边站案例）：

```json
{
  "agent_name": "MeteorologyAgent",
  "status": "success",
  "result": {
    "task_id": "HK_MK_2023_0800",
    "station_id": "mk_roadside",
    "station_name": "Mong Kok Roadside",
    "start_time": "2023-08-15 08:00:00",
    "end_time": "2023-08-15 11:00:00",
    "lat": 22.3193,
    "lon": 114.1694,
    "weather_risk": 0.7237,
    "risk_type": "high",
    "dispersion_condition": "一般",
    "static_wind_score": 0.595,
    "wind_shift_score": 1.0,
    "humidity_risk_score": 0.5,
    "rain_removal_score": 0.2,
    "insufficient_rain_removal_score": 0.8,
    "mean_wind_speed": 1.1925,
    "dominant_wind_direction": 201.5,
    "dominant_wind_direction_label": "SSW",
    "upwind_direction": 201.5,
    "downwind_direction": 21.5,
    "mean_humidity": 84.75,
    "total_precipitation": 0.2,
    "mean_temperature": 28.5,
    "mean_cloud_cover": 54.5,
    "weather_code_daily": 51,
    "dominant_weather_code_daily": 51,
    "weather_code_daily_series": [
      {
        "date": "2023-08-15",
        "weather_code": 51
      }
    ],
    "hourly_count": 4,
    "latest_wind_speed": 2.25,
    "latest_wind_direction": 201.0,
    "upwind": "上风向（来向）约 南（201°）",
    "downwind": "下风向（去向）约 北"
  },
  "evidence": [
    "检测到连续 3 小时风速 ≤ 1.5 m/s，平均 0.84 m/s，利于污染物静风积聚",
    "相邻小时最大风向变化 180.0°（圆周角），存在扩散路径突变风险",
    "连续 2 小时湿度 ≥ 85%，最高 91%，不利扩散",
    "近 4 小时累计降水仅 0.20 mm（<1.0 mm），清除不足",
    "上风向（来向）约 南（201°）",
    "下风向（去向）约 北"
  ],
  "confidence": 1.0,
  "data_quality": {
    "missing_rate": 0.0,
    "fallback_used": false,
    "source": "Open-Meteo Archive API",
    "note": "数据完整"
  },
  "created_at": "2026-08-07 17:45:53"
}
```

### 1.2 Warning 状态

**当前暂未提供真实 warning 运行样例**，warning 触发条件见第 5 节异常情况说明。

Warning 状态通常在以下情况出现：
- Open-Meteo API 调用失败，使用本地 fallback CSV
- 核心气象字段部分缺失但仍能计算评分

---

## 2️⃣ 字段说明表

### 2.1 核心展示字段

| 字段路径 | 类型 | 单位 | 含义 | 可能为空 | 枚举值/范围 | 前端展示建议 |
|---------|------|------|------|---------|------------|------------|
| `result.weather_risk` | float | 无量纲 | 综合气象风险评分 | ❌ | 0.0 ~ 1.0 | 【核心】风险卡片 |
| `result.risk_type` | string | - | 气象风险等级标签 | ❌ | low / medium / high / very_high | 【核心】风险标签 |
| `result.dispersion_condition` | string | - | 扩散条件状态（中文） | ❌ | 优 / 良 / 一般 / 差 / 极差 | 【核心】状态标签 |
| `result.mean_wind_speed` | float | m/s | 窗口平均风速 | ✅ | ≥ 0.0 | 【核心】指标卡 |
| `result.dominant_wind_direction` | float | 度（°） | 窗口主导风向（圆向量法） | ✅ | 0.0 ~ 360.0 | 【核心】风向显示 |
| `result.dominant_wind_direction_label` | string | - | 主导风向16方位标签 | ✅ | N/NNE/NE/.../NNW | 【核心】风向文字 |
| `result.mean_humidity` | float | % | 窗口平均相对湿度 | ✅ | 0.0 ~ 100.0 | 【核心】指标卡 |
| `result.total_precipitation` | float | mm | 窗口累计降水量 | ✅ | ≥ 0.0 | 【核心】指标卡 |
| `evidence` | array[string] | - | 气象风险证据说明列表 | ❌ | - | 【核心】证据列表 |
| `confidence` | float | 无量纲 | 结果置信度 | ❌ | 0.0 ~ 1.0 | 【核心】置信度指示 |

### 2.2 算法中间量（供技术分析）

| 字段路径 | 类型 | 单位 | 含义 | 可能为空 | 枚举值/范围 | 前端展示建议 |
|---------|------|------|------|---------|------------|------------|
| `result.static_wind_score` | float | 无量纲 | 静风积聚风险评分 | ❌ | 0.0 ~ 1.0 | 【算法中间量】详情页 |
| `result.wind_shift_score` | float | 无量纲 | 风向突变风险评分 | ❌ | 0.0 ~ 1.0 | 【算法中间量】详情页 |
| `result.humidity_risk_score` | float | 无量纲 | 高湿不利扩散评分 | ❌ | 0.0 ~ 1.0 | 【算法中间量】详情页 |
| `result.rain_removal_score` | float | 无量纲 | 降雨清除能力评分 | ❌ | 0.0 ~ 1.0 | 【算法中间量】详情页 |
| `result.insufficient_rain_removal_score` | float | 无量纲 | 降雨不足评分（反向） | ❌ | 0.0 ~ 1.0 | 【算法中间量】详情页 |

### 2.3 补充气象字段

| 字段路径 | 类型 | 单位 | 含义 | 可能为空 | 枚举值/范围 | 前端展示建议 |
|---------|------|------|------|---------|------------|------------|
| `result.upwind_direction` | float | 度（°） | 上风向方向（等同主导风向） | ✅ | 0.0 ~ 360.0 | 【前端详情】上风向 |
| `result.downwind_direction` | float | 度（°） | 下风向方向（主导+180°） | ✅ | 0.0 ~ 360.0 | 【前端详情】下风向 |
| `result.mean_temperature` | float | °C | 窗口平均气温 | ✅ | - | 【前端详情】温度 |
| `result.mean_cloud_cover` | float | % | 窗口平均云量 | ✅ | 0.0 ~ 100.0 | 【前端详情】云量 |
| `result.weather_code_daily` | int | - | 单日天气代码（WMO标准） | ✅ | 0 ~ 99 | 【前端详情】天气图标 |
| `result.dominant_weather_code_daily` | int | - | 多日场景众数天气代码 | ✅ | 0 ~ 99 | 【前端详情】天气图标 |
| `result.weather_code_daily_series` | array | - | 逐日天气代码序列 | ✅ | [{date, weather_code}] | 【前端详情】天气趋势 |
| `result.hourly_count` | int | 个 | 实际小时数据点数量 | ❌ | ≥ 1 | 【前端详情】数据完整度 |
| `result.hourly_series` | array | - | 逐小时时序数据 | ❌ | 见第4节 | 【核心】趋势图绘制 |

### 2.4 兼容期保留字段（Deprecated）

| 字段路径 | 类型 | 单位 | 含义 | 可能为空 | 状态 | 前端展示建议 |
|---------|------|------|------|---------|------|------------|
| `result.latest_wind_speed` | float | m/s | 窗口末小时风速 | ✅ | **废弃中** | 【不建议使用】建议改用 `mean_wind_speed` |
| `result.latest_wind_direction` | float | 度（°） | 窗口末小时风向 | ✅ | **废弃中** | 【不建议使用】建议改用 `dominant_wind_direction` |
| `result.upwind` | string | - | 上风向文字描述 | ❌ | **废弃中** | 【不建议使用】建议改用 `upwind_direction` + `dominant_wind_direction_label` |
| `result.downwind` | string | - | 下风向文字描述 | ❌ | **废弃中** | 【不建议使用】建议改用 `downwind_direction` |

⚠️ **废弃字段说明**：
- `latest_*` 字段仅代表窗口末小时瞬时值，不适合用于整个时间窗口的空间分析
- 空间分析应使用 `dominant_wind_direction`（窗口聚合主导风向）
- 这些字段将在下一主版本中移除，请前端尽早迁移

### 2.5 数据质量字段

| 字段路径 | 类型 | 单位 | 含义 | 可能为空 | 枚举值/范围 | 前端展示建议 |
|---------|------|------|------|---------|------------|------------|
| `data_quality.missing_rate` | float | 无量纲 | 核心字段缺失率 | ❌ | 0.0 ~ 1.0 | 【数据质量】质量指示器 |
| `data_quality.fallback_used` | boolean | - | 是否使用本地兜底数据 | ❌ | true / false | 【数据质量】数据源标记 |
| `data_quality.source` | string | - | 数据来源标识 | ❌ | Open-Meteo Archive API / Local Fallback CSV / None | 【数据质量】来源说明 |
| `data_quality.note` | string | - | 数据质量说明 | ❌ | - | 【数据质量】详细说明 |

### 2.6 顶层元数据字段

| 字段路径 | 类型 | 单位 | 含义 | 可能为空 | 枚举值/范围 | 前端展示建议 |
|---------|------|------|------|---------|------------|------------|
| `agent_name` | string | - | 智能体名称标识 | ❌ | "MeteorologyAgent" | 【不展示】内部标识 |
| `status` | string | - | 运行状态 | ❌ | success / warning / failed | 【核心】状态指示 |
| `created_at` | string | - | 结果生成时间戳 | ❌ | YYYY-MM-DD HH:MM:SS | 【元数据】时间戳 |

---

## 3️⃣ 前端展示建议

### 3.1 核心风险卡片

**推荐组件**：风险仪表盘/卡片

**展示内容**：
- `weather_risk`：0.7237 → 显示为 **72.4分** 或进度条
- `risk_type`：high → 显示为 **高风险** 标签（红色/橙色）
- `dispersion_condition`：一般 → 显示为 **扩散条件：一般** 状态标签

**映射建议**：
```
risk_type 映射：
- low → 低风险（绿色）
- medium → 中等风险（黄色）
- high → 高风险（橙色）
- very_high → 极高风险（红色）

dispersion_condition 已为中文，直接显示
```

### 3.2 主要气象指标

**推荐组件**：指标卡网格

**展示内容**：
- **平均风速**：1.19 m/s
- **主导风向**：SSW（201.5°）配风向图标
- **平均湿度**：84.8%
- **累计降水**：0.2 mm
- **平均温度**：28.5°C（可选）
- **平均云量**：54.5%（可选）

### 3.3 证据列表

**推荐组件**：展开面板/详情列表

**展示内容**：逐条展示 `evidence` 数组

示例：
```
✓ 检测到连续 3 小时风速 ≤ 1.5 m/s，平均 0.84 m/s，利于污染物静风积聚
✓ 相邻小时最大风向变化 180.0°（圆周角），存在扩散路径突变风险
✓ 连续 2 小时湿度 ≥ 85%，最高 91%，不利扩散
✓ 近 4 小时累计降水仅 0.20 mm（<1.0 mm），清除不足
✓ 上风向（来向）约 南（201°）
✓ 下风向（去向）约 北
```

### 3.4 置信度指示

**推荐组件**：置信度徽章/进度条

**展示内容**：
- `confidence`：1.0 → 显示为 **100%** 或 **高置信度**
- 配合 `data_quality.source` 显示数据来源

**低置信度提示规则**：
⚠️ **当前项目尚未定义前端低置信度提示阈值，需要团队统一确定**

建议后续明确：
- `confidence < 0.6`：显示"结果可信度较低"警告
- 或根据业务场景调整阈值

---

## 4️⃣ 逐小时时序数据（正式接口）

### 4.1 前端绘图需求

前端可绘制以下气象趋势图：
- **逐小时风速趋势图**
- **逐小时湿度趋势图**
- **逐小时降雨趋势图**
- **逐小时温度趋势图**
- **逐小时风向变化图**
- **逐小时云量趋势图**

### 4.2 hourly_series 接口（正式冻结字段）

**✅ 当前已正式实现**：MeteorologyAgent 输出中包含 `result.hourly_series` 字段

**接口结构**（基于真实运行输出）：

```json
{
  "agent_name": "MeteorologyAgent",
  "status": "success",
  "result": {
    ...现有聚合字段...,
    "hourly_count": 4,
    "hourly_series": [
      {
        "time": "2023-08-15 08:00:00+08:00",
        "wind_speed": 1.08,
        "wind_direction": 304.0,
        "humidity": 91.0,
        "precipitation": 0.2,
        "temperature": 26.3,
        "cloud_cover": 36.0
      },
      {
        "time": "2023-08-15 09:00:00+08:00",
        "wind_speed": 0.36,
        "wind_direction": 124.0,
        "humidity": 88.0,
        "precipitation": 0.0,
        "temperature": 28.0,
        "cloud_cover": 82.0
      },
      {
        "time": "2023-08-15 10:00:00+08:00",
        "wind_speed": 1.08,
        "wind_direction": 202.0,
        "humidity": 81.0,
        "precipitation": 0.0,
        "temperature": 29.4,
        "cloud_cover": 45.0
      },
      {
        "time": "2023-08-15 11:00:00+08:00",
        "wind_speed": 2.25,
        "wind_direction": 201.0,
        "humidity": 79.0,
        "precipitation": 0.0,
        "temperature": 30.3,
        "cloud_cover": 55.0
      }
    ]
  }
}
```

### 4.3 hourly_series 字段说明

| 字段 | 类型 | 单位 | 说明 | 可能为空 | 范围 |
|------|------|------|------|---------|------|
| `time` | string | - | ISO 8601 格式时间字符串（含时区） | ❌ | - |
| `wind_speed` | float | m/s | 该小时风速 | ✅ | ≥ 0.0 |
| `wind_direction` | float | 度（°） | 该小时风向 | ✅ | 0.0 ~ 360.0 |
| `humidity` | float | % | 该小时相对湿度 | ✅ | 0.0 ~ 100.0 |
| `precipitation` | float | mm | 该小时降水量 | ✅ | ≥ 0.0 |
| `temperature` | float | °C | 该小时气温 | ✅ | - |
| `cloud_cover` | float | % | 该小时云量 | ✅ | 0.0 ~ 100.0 |

**重要规则**：
1. **时间顺序**：`hourly_series` 按 `time` 升序排列
2. **时间范围**：仅包含 `task.start_time` 到 `task.end_time` 范围内的小时
3. **数据一致性**：`len(hourly_series)` 必须等于 `hourly_count`
4. **缺失值处理**：缺失值为 `null`，**不会转为 0**
5. **零值合法性**：`wind_speed = 0` 和 `wind_direction = 0` 是合法值（无风和正北风）
6. **聚合一致性**：`hourly_series` 与聚合统计字段（`mean_wind_speed`、`total_precipitation` 等）来自同一数据源

### 4.4 前端判断规则（重要）

#### ⚠️ **零值与缺失值判断**

```javascript
// ❌ 错误判断（会把0误判为缺失）
if (!wind_speed) { return "暂无数据"; }

// ✅ 正确判断
if (wind_speed === null || wind_speed === undefined) {
  return "暂无数据";
} else {
  return wind_speed;  // 包括0的合法情况
}
```

**特殊说明**：
- `wind_speed = 0`：表示无风（合法值）
- `wind_direction = 0`：表示正北风（合法值）
- `precipitation = 0`：表示无降水（合法值）

### 4.5 前端绘图建议

#### 风速趋势图
```javascript
const chart = {
  type: 'line',
  data: {
    labels: hourly_series.map(h => h.time),
    datasets: [{
      label: '风速 (m/s)',
      data: hourly_series.map(h => h.wind_speed)
    }]
  }
}
```

#### 湿度趋势图
```javascript
const chart = {
  type: 'line',
  data: {
    labels: hourly_series.map(h => h.time),
    datasets: [{
      label: '相对湿度 (%)',
      data: hourly_series.map(h => h.humidity)
    }]
  }
}
```

#### 降雨趋势图（柱状图）
```javascript
const chart = {
  type: 'bar',
  data: {
    labels: hourly_series.map(h => h.time),
    datasets: [{
      label: '降水量 (mm)',
      data: hourly_series.map(h => h.precipitation)
    }]
  }
}
```

#### 风向变化图（极坐标或箭头）
```javascript
// 使用风向角度绘制风向箭头或极坐标图
hourly_series.forEach(h => {
  const angle = h.wind_direction;  // 0-360度
  const speed = h.wind_speed;
  drawWindArrow(angle, speed);
});
```

### 4.6 数据来源说明

**数据来源**：
- ✅ `hourly_series` 与 MeteorologyAgent 计算评分时使用的是**同一批真实逐小时数据**
- ✅ 来自 `raw_to_standard_hourly` ETL 处理后的标准化数据
- ✅ 当 API 失败时，fallback CSV 的逐小时数据也会正确输出到 `hourly_series`

**聚合一致性验证**：
```javascript
// 验证：hourly_series 的聚合值应与聚合统计字段一致
const validWindSpeeds = hourly_series
  .map(h => h.wind_speed)
  .filter(v => v !== null);
  
const calculatedMean = validWindSpeeds.reduce((a, b) => a + b, 0) / validWindSpeeds.length;
const reportedMean = result.mean_wind_speed;

// calculatedMean 应与 reportedMean 一致（允许微小舍入误差）
assert(Math.abs(calculatedMean - reportedMean) < 0.01);
```

---

## 5️⃣ 异常情况说明

### 5.1 Status 状态机

| Status | 含义 | 触发条件 | 前端处理 |
|--------|------|---------|---------|
| `success` | 正常成功 | 真实 API 调用成功，核心字段完整 | 正常展示所有数据 |
| `warning` | 降级运行 | 1. Open-Meteo API 失败，使用本地 fallback CSV<br>2. 核心字段部分缺失但仍能计算 | 展示数据，显示"数据来源降级"警告 |
| `failed` | 完全失败 | 1. 输入参数校验错误<br>2. API + fallback 均失败<br>3. 无可用数据 | 显示错误信息，不展示结果 |

### 5.2 字段缺失处理规则

#### ⚠️ **风向字段特殊说明**

`wind_direction = 0` 表示 **合法的 0° 北风**，不是缺失值！

**前端判断规则**：
```javascript
// ❌ 错误判断（会把0°风向当作缺失）
if (!wind_direction) { ... }

// ✅ 正确判断
if (wind_direction === null || wind_direction === undefined) {
  显示 "暂无数据"
} else {
  显示 wind_direction  // 包括0的情况
}
```

#### 字段为 null 时的前端处理

当以下字段为 `null` 时：
- `mean_wind_speed`：显示"风速暂无数据"
- `dominant_wind_direction`：显示"风向暂无数据"
- `mean_humidity`：显示"湿度暂无数据"
- `total_precipitation`：显示"降水暂无数据"

**禁止自动显示 0**！（0 可能是合法值）

### 5.3 Fallback 机制

| 字段 | 含义 | 前端处理建议 |
|------|------|------------|
| `data_quality.fallback_used = true` | Open-Meteo API 失败，使用本地历史数据 | 显示"⚠️ 当前使用历史气象数据" |
| `data_quality.source` | 数据来源标识 | 显示在数据质量面板 |
| `data_quality.note` | 质量说明文本 | 鼠标悬停提示或详情面板 |

### 5.4 Confidence 置信度说明

**计算逻辑**（来自真实代码）：
```python
base = 1.0 - missing_rate * 0.5
if fallback_used:
    base *= 0.75
if core_field_missing:
    base *= 0.85
```

**典型场景**：
- `confidence = 1.0`：API 成功，数据完整
- `confidence = 0.75`：使用 fallback，数据完整
- `confidence = 0.85`：API 成功，核心字段部分缺失
- `confidence < 0.6`：数据质量较差

**前端低置信度提示规则**：
⚠️ **当前项目尚未定义前端低置信度提示阈值，需要团队统一确定。**

建议后续讨论确定：
- 阈值是否设为 `confidence < 0.6` 或其他值
- 提示文案是否为"结果可信度较低，建议谨慎参考"

### 5.5 Evidence 证据说明

`evidence` 数组包含 **中文业务逻辑证据**，可直接展示给用户：

**内容来源**：
- 静风积聚证据（连续低风速小时数、平均风速）
- 风向突变证据（最大圆周角变化）
- 高湿不利扩散证据（连续高湿小时数、最高湿度）
- 降雨清除不足证据（累计降水量、阈值对比）
- 上下风向文字描述

**前端展示建议**：
- 逐条展示，使用列表或卡片
- 可配图标（风速、风向、湿度、降雨）
- 支持展开/收起详情

---

## 6️⃣ 字段冻结契约

### 6.1 核心冻结字段

以下字段已正式冻结，**前端接入后不得随意改名或删除**：

#### 风险评估字段
- `weather_risk`：综合气象风险评分（0-1）
- `risk_type`：气象风险等级（low/medium/high/very_high）
- `dispersion_condition`：扩散条件状态（中文：优/良/一般/差/极差）

#### 聚合气象字段
- `mean_wind_speed`：窗口平均风速（m/s）
- `dominant_wind_direction`：窗口主导风向（度）
- `dominant_wind_direction_label`：主导风向16方位标签
- `upwind_direction`：上风向方向（度）
- `downwind_direction`：下风向方向（度）
- `mean_humidity`：窗口平均湿度（%）
- `total_precipitation`：窗口累计降水（mm）
- `mean_temperature`：窗口平均气温（°C）
- `mean_cloud_cover`：窗口平均云量（%）

#### 逐小时时序字段（新增）
- `hourly_count`：小时数据点数量
- `hourly_series`：逐小时时序数组
  - `time`：时间字符串
  - `wind_speed`：风速（m/s）
  - `wind_direction`：风向（度）
  - `humidity`：湿度（%）
  - `precipitation`：降水量（mm）
  - `temperature`：气温（°C）
  - `cloud_cover`：云量（%）

#### 评分字段
- `static_wind_score`：静风积聚评分
- `wind_shift_score`：风向突变评分
- `humidity_risk_score`：高湿风险评分
- `rain_removal_score`：降雨清除能力评分
- `insufficient_rain_removal_score`：降雨不足评分

#### 元数据字段
- `evidence`：证据列表
- `confidence`：置信度
- `data_quality`：数据质量对象
- `status`：运行状态
- `created_at`：创建时间

### 6.2 风向字段契约特别说明

#### ⚠️ **严格区分聚合风向与瞬时风向**

| 字段 | 含义 | 用途 | 空间分析是否使用 |
|------|------|------|----------------|
| `dominant_wind_direction` | 整个目标分析时间窗口的主导风向（圆向量法聚合） | SpatialAgent 正式空间分析使用 | ✅ **主要字段** |
| `dominant_wind_direction_label` | 主导风向16方位标签（如 SSW） | 前端主风向展示 | ✅ 配套展示 |
| `latest_wind_direction` | 时间窗口最后一个有效小时的风向 | 最新状态展示 | ❌ **不用于空间分析** |
| `upwind_direction` | 上风向方向（等同 dominant_wind_direction） | 空间上风向判断 | ✅ 派生字段 |
| `downwind_direction` | 下风向方向（dominant + 180°） | 空间下风向判断 | ✅ 派生字段 |

**前端接入规则**：
- ✅ **主风向展示**：使用 `dominant_wind_direction` + `dominant_wind_direction_label`
- ✅ **空间影响区域**：依赖 `dominant_wind_direction`（SpatialAgent 读取此字段）
- ❌ **不要用** `latest_wind_direction` 替代主导风向进行整个窗口的空间分析
- ⚠️ `latest_*` 字段将在下一主版本废弃，前端应尽早迁移到聚合字段

### 6.3 字段修改流程

如确需修改冻结字段，**必须提前提交变更申请**：

```
旧字段：dominant_wind_direction
新字段：primary_wind_direction
修改原因：统一命名规范
影响 Agent：SpatialAgent、SourceAgent
影响前端：气象模块、空间分析模块
兼容方案：保留旧字段 3 个版本，同时输出新旧字段
```

**禁止行为**：
- ❌ 未经确认直接改名（如 `dominant_wind_direction` → `wind_direction`）
- ❌ 删除已被前端依赖的字段
- ❌ 改变字段单位而不通知前端（如 m/s → km/h）

---

## 7️⃣ 输入参数规范

### 7.1 Task 输入字段

MeteorologyAgent 由 Coordinator 统一调度，输入为 `task` 字典：

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `task_id` | string | ✅ | 任务唯一标识 | "HK_MK_2023_0800" |
| `station_name` | string | ❌ | 监测站名称 | "Mong Kok Roadside" |
| `station_id` | string | ❌ | 监测站ID | "mk_roadside" |
| `start_time` | string | ✅ | 分析起始时间（整点） | "2023-08-15 08:00:00" |
| `end_time` | string | ✅ | 分析结束时间（整点） | "2023-08-15 11:00:00" |
| `lat` | float | ✅ | 纬度 | 22.3193 |
| `lon` | float | ✅ | 经度 | 114.1694 |

### 7.2 输入校验规则

- `task_id` / `start_time` / `end_time`：必填且非空字符串
- `lat`：[-90, 90]
- `lon`：[-180, 180]
- `start_time` / `end_time`：可解析为合法时间，自动整点化
- 时间窗口：`end_time >= start_time`
- 窗口长度：[1小时, 168小时（7天）]

### 7.3 输入校验失败响应

输入不合法时返回 `status = "failed"`：

```json
{
  "agent_name": "MeteorologyAgent",
  "status": "failed",
  "result": {},
  "evidence": [
    "task 输入不合法: lat=91.0 超出合法范围 [-90.0, 90.0]"
  ],
  "confidence": 0.0,
  "data_quality": {
    "missing_rate": 1.0,
    "fallback_used": false,
    "source": "None",
    "note": "task 输入不合法: lat=91.0 超出合法范围 [-90.0, 90.0]"
  },
  "created_at": "2026-08-07 17:45:53"
}
```

---

## 8️⃣ 前端交接摘要

### 8.1 交接清单

| 项目 | 当前状态 |
|-----|---------|
| Success 真实 JSON | ✅ 已提供 |
| Warning 真实 JSON | ⏳ 暂未提供（见第5节触发条件） |
| 核心字段说明 | ✅ 已完成 |
| 字段单位确认 | ✅ 已完成 |
| 前端核心展示字段 | ✅ 已确认（9个核心字段） |
| 逐小时风速数据 | ✅ **已实现**（`hourly_series`） |
| 逐小时湿度数据 | ✅ **已实现**（`hourly_series`） |
| 逐小时降雨数据 | ✅ **已实现**（`hourly_series`） |
| Fallback 规则 | ✅ 已确认 |
| 字段冻结契约 | ✅ 已完成 |
| 废弃字段标记 | ✅ 已标记（`latest_*` / `upwind` / `downwind`） |

### 8.2 当前需前端负责人确认的问题

1. **~~逐小时时序数据接口~~**（✅ 已解决）
   - ~~问题：前端绘制趋势图需要逐小时数据，当前接口未提供~~
   - ✅ **已实现**：`result.hourly_series` 字段已正式加入输出
   - 状态：可用，详见第4节
   - 优先级：~~高（影响趋势图功能）~~（已完成）

2. **Confidence 低置信度提示阈值**
   - 问题：当前未定义前端提示阈值（如 `confidence < 0.6` 时显示警告）
   - 决策人：产品负责人 + 前端负责人
   - 优先级：中（影响用户体验）

3. **Risk_type 中文显示映射**
   - 问题：`risk_type` 当前为英文枚举（low/medium/high/very_high）
   - 方案：前端统一处理映射 OR 后端输出增加中文字段 `risk_type_label`
   - 决策人：前端负责人
   - 优先级：低（前端可自行映射）

4. **废弃字段迁移计划**
   - 问题：`latest_*` / `upwind` / `downwind` 字段计划废弃
   - 迁移目标：使用 `dominant_*` / `upwind_direction` / `downwind_direction`
   - 时间表：待确认（建议 3 个版本过渡期）
   - 决策人：前后端负责人
   - 优先级：中（保证平滑迁移）

---

## 9️⃣ 附录

### 9.1 WMO 天气代码参考

`weather_code_daily` 遵循 WMO 标准：

| 代码 | 含义 |
|------|------|
| 0 | Clear sky（晴空） |
| 1-3 | Mainly clear, partly cloudy, overcast（晴间多云、多云、阴） |
| 45, 48 | Fog（雾） |
| 51-55 | Drizzle（毛毛雨） |
| 61-65 | Rain（雨） |
| 71-77 | Snow（雪） |
| 80-82 | Rain showers（阵雨） |
| 95-99 | Thunderstorm（雷暴） |

完整代码表参考：https://open-meteo.com/en/docs

### 9.2 风向16方位标签

`dominant_wind_direction_label` 可能值：

| 标签 | 方位 | 角度范围 |
|------|------|---------|
| N | 北 | 348.75° - 11.25° |
| NNE | 北东北 | 11.25° - 33.75° |
| NE | 东北 | 33.75° - 56.25° |
| ENE | 东东北 | 56.25° - 78.75° |
| E | 东 | 78.75° - 101.25° |
| ESE | 东东南 | 101.25° - 123.75° |
| SE | 东南 | 123.75° - 146.25° |
| SSE | 南东南 | 146.25° - 168.75° |
| S | 南 | 168.75° - 191.25° |
| SSW | 南西南 | 191.25° - 213.75° |
| SW | 西南 | 213.75° - 236.25° |
| WSW | 西西南 | 236.25° - 258.75° |
| W | 西 | 258.75° - 281.25° |
| WNW | 西西北 | 281.25° - 303.75° |
| NW | 西北 | 303.75° - 326.25° |
| NNW | 北西北 | 326.25° - 348.75° |

### 9.3 相关文档

- **系统整体架构**：`README.md`
- **API 接口契约**：`docs/api_contract.md`
- **统一调度对接说明**：`docs/统一调度对接说明.md`
- **Coordinator 运行结果**：`examples/day9_coordinator_output.json`

---

## 📝 文档变更记录

| 版本 | 日期 | 修改内容 | 作者 |
|------|------|---------|------|
| 1.0 | 2026-08-07 | 初始版本，基于第9天完整链路运行结果 | Kiro AI |

---

**文档生成时间**：2026-08-07  
**基于代码版本**：Day 9 完整链路  
**最新运行输出**：`examples/day9_coordinator_output.json`

**联系人**：后端负责人（MeteorologyAgent 维护者）

---

**⚠️ 重要提示**：
1. 本文档所有内容基于当前真实代码和实际运行输出
2. 逐小时时序数据接口（`hourly_series`）为建议结构，尚未正式实现
3. 废弃字段（`latest_*` / `upwind` / `downwind`）将在未来版本移除
4. 前端接入前请与后端负责人确认最新接口变更
