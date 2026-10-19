# Meteorology Agent 接口契约

> 版本：1.5.0
> 本文件是对外接口的唯一权威说明。任何新增业务逻辑不得破坏本文档定义的结构。

---

## 输出契约（已冻结）

### 统一外层结构

所有情况（success / warning / failed）均返回以下 7 键结构，不得缺少任何键：

```jsonc
{
  "agent_name":  "MeteorologyAgent",       // string，固定值
  "status":      "success|warning|failed", // string，见状态规则表
  "result":      { ... } | {},             // object，见下节
  "evidence":    ["...", "..."],           // string[]，各维度评分说明
  "confidence":  0.9500,                   // float [0,1]
  "data_quality": {
    "missing_rate":  0.0,                  // float [0,1]，核心字段缺失比例
    "fallback_used": false,                // bool
    "source":        "Open-Meteo Archive API", // string，见来源枚举
    "note":          "数据完整"            // string，可读说明
  },
  "created_at":  "2026-07-23 08:00:00"    // string，Asia/Hong_Kong 本地时间
}
```

### 输出状态规则

| 场景 | status | result | confidence |
|------|--------|--------|------------|
| 真实 API 成功，核心字段完整 | `success` | 完整 result 对象 | 高（≥0.85） |
| 使用 fallback CSV，仍完成分析 | `warning` | 完整 result 对象 | 中（×0.75 折减） |
| 核心字段部分缺失，仍能计算 | `warning` | 完整 result 对象 | 低（额外×0.85 折减） |
| 非核心字段缺失（如 weather_code_daily） | `success` 或 `warning` | 完整 result 对象，note 有说明 | 不降低 |
| API 和 fallback 均失败 / 无可用数据 | `failed` | `{}` | 0.0 |
| 输入参数错误 | `failed` | `{}` | 0.0 |

> **规则**：`failed` 时 result 固定为空对象 `{}`，不包含任何评分字段。

### data_quality.source 枚举

| 值 | 含义 |
|----|------|
| `"Open-Meteo Archive API"` | 数据来自真实 API |
| `"Local Fallback CSV"` | API 失败，降级使用本地 CSV |
| `"None"` | 未获取到任何数据（输入错误或 API+fallback 均失败） |


---

## result 对象字段参考（v1.5.0 已冻结）

### status = success 或 warning 时

#### 身份标识字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | `str` | 原样透传 |
| `station_id` | `str` | 原样透传 |
| `station_name` | `str` \| `null` | 原样透传 |
| `start_time` | `str` | 整点化后的时间，`YYYY-MM-DD HH:mm:ss` |
| `end_time` | `str` | 整点化后的时间，`YYYY-MM-DD HH:mm:ss` |
| `lat` | `float` | WGS84 纬度 |
| `lon` | `float` | WGS84 经度 |

#### 综合风险（核心冻结字段）

| 字段 | 类型 | 说明 |
|------|------|------|
| `weather_risk` | `float` [0,1] | **综合污染物积聚风险**。值越高，扩散条件越差、积聚风险越大 |
| `risk_type` | `"low"` \| `"medium"` \| `"high"` | 风险等级，由 `weather_risk` 阈值映射 |

#### 四维评分（核心冻结字段）

| 字段 | 类型 | 语义 | 越高表示 |
|------|------|------|---------|
| `dispersion_condition` | `str` | 扩散条件文字（有利扩散 / 一般 / 不利扩散（静风或弱风） / 未知） | — |
| `static_wind_score` | `float` [0,1] | 静风积聚评分 | 低风速持续越久，积聚风险越大 |
| `wind_shift_score` | `float` [0,1] | 风向突变评分 | 风向突变越剧烈，扩散路径越不稳定 |
| `humidity_risk_score` | `float` [0,1] | 高湿度风险评分 | 持续高湿，不利扩散 |
| `rain_removal_score` | `float` [0,1] | **降水清除条件强度**。值越高，降水对污染物的清除作用越强 |
| `insufficient_rain_removal_score` | `float` [0,1] | **降雨清除不足风险**。值越高，降水清除不足风险越大 |

> **互补关系（不变式）**：`insufficient_rain_removal_score = 1 - rain_removal_score`
> 两者始终满足此等式，下游无需单独计算。


#### 主导风向（核心冻结字段，基于时间窗口聚合）

| 字段 | 类型 | 说明 |
|------|------|------|
| `mean_wind_speed` | `float` \| `null` | 窗口内 10m 风速均值（m/s） |
| `dominant_wind_direction` | `float` \| `null` | 主导**来风**方向（°，0–360，圆向量均值法），见下方算法说明 |
| `dominant_wind_direction_label` | `str` \| `null` | 16 方位英文缩写，如 `"SE"`，由 `dominant_wind_direction` 映射 |
| `upwind_direction` | `float` \| `null` | 上风向（来风方向数值），等于 `dominant_wind_direction` |
| `downwind_direction` | `float` \| `null` | 下风向（去风方向数值）= `(dominant_wind_direction + 180) % 360` |

**dominant_wind_direction 算法（圆向量均值法）**

将每个小时的风向角度转为单位向量后求平均，再 `atan2` 反算：

```
mean_sin = mean(sin(θ_i))
mean_cos = mean(cos(θ_i))
dominant = atan2(mean_sin, mean_cos) % 360
```

此方法在风向跨越 0°/360° 边界时仍正确（如 350° 和 10° 的均值约为 0°，而非 180°）。

**16 方位标签映射**

| 范围 | 标签 | 范围 | 标签 |
|------|------|------|------|
| 348.75°–11.25° | `N` | 168.75°–191.25° | `S` |
| 11.25°–33.75° | `NNE` | 191.25°–213.75° | `SSW` |
| 33.75°–56.25° | `NE` | 213.75°–236.25° | `SW` |
| 56.25°–78.75° | `ENE` | 236.25°–258.75° | `WSW` |
| 78.75°–101.25° | `E` | 258.75°–281.25° | `W` |
| 101.25°–123.75° | `ESE` | 281.25°–303.75° | `WNW` |
| 123.75°–146.25° | `SE` | 303.75°–326.25° | `NW` |
| 146.25°–168.75° | `SSE` | 326.25°–348.75° | `NNW` |

#### 聚合统计（核心冻结字段）

| 字段 | 类型 | 说明 |
|------|------|------|
| `mean_humidity` | `float` \| `null` | 窗口内相对湿度均值（%） |
| `total_precipitation` | `float` \| `null` | 窗口内累计降水量（mm） |
| `mean_temperature` | `float` \| `null` | 窗口内气温均值（°C） |
| `mean_cloud_cover` | `float` \| `null` | 窗口内云量均值（%） |

#### 短时风场与污染路径预测（筛查级扩展字段）

| 字段 | 类型 | 说明 |
|------|------|------|
| `wind_direction_forecast` | object | 最近最多 12 小时风场的加权圆统计预测，默认预测未来 3 小时 |
| `pollution_path_prediction` | object | 由逐小时预测风矢量驱动的前向拉格朗日路径及不确定性走廊 |

`wind_direction_forecast.forecast_hours` 每项给出气象来向 `wind_from_direction`、用于平流的去向 `wind_to_direction`、风速和随预报时效增长的角度不确定性。路径以 GeoJSON `LineString` 返回，`uncertainty_corridor_geojson` 为相应的扩散走廊。风速低于 0.5 m/s、风向离散或观测不足时，两个对象须返回 `status: "unavailable"`（或风场为 `"calm"`），且不得伪造路径。该结果用于快速筛查，不是浓度、化学反应或地形效应模拟。

> **缺失规范**：以上所有字段在对应原始数据全部缺失时返回 `null`，不允许伪造默认数值。


#### 天气代码（核心冻结字段）

**单日场景**（时间窗口仅跨一个自然日）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `weather_code_daily` | `int` \| `null` | 当日天气代码（WMO code），单日时返回标量 |
| `dominant_weather_code_daily` | `int` \| `null` | 同 `weather_code_daily`（单日时两者相同） |
| `weather_code_daily_series` | `list` \| `null` | 含 1 条记录：`[{"date": "YYYY-MM-DD", "weather_code": int}]` |

**多日场景**（时间窗口跨两个或以上自然日）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `weather_code_daily` | `null` | 多日时固定为 `null`，强制下游使用下两个字段 |
| `dominant_weather_code_daily` | `int` \| `null` | 时段内各日代码的**众数**，见聚合规则 |
| `weather_code_daily_series` | `list` \| `null` | 每日一条记录，按日期升序：`[{"date": "YYYY-MM-DD", "weather_code": int}, ...]` |

**多日聚合规则（dominant_weather_code_daily）**

1. 将时间窗口按自然日分组，每日取该日各小时代码的众数作为当日代表值。
2. 统计所有日代表值中出现次数最多的代码，作为 `dominant_weather_code_daily`。
3. 若多个代码出现次数相同（平局），取**数值最小**的代码（稳定性优先，结果可复现）。

**缺失处理**：`weather_code_daily` 列完全缺失或全为 `null` 时，三个字段均返回 `null`，Agent 仍正常运行，评分不受影响（该字段当前不参与四维评分）。

#### 计数字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `hourly_count` | `int` | 实际参与计算的小时行数 |

#### 兼容期保留字段（deprecated，将在下一主版本移除）

> 以下字段在 v1.5.0 **过渡期内保留**，新开发不应建立对其的依赖，请迁移到对应替代字段。

| 字段 | 类型 | 替代字段 | 说明 |
|------|------|---------|------|
| `wind_speed` | `float` \| `null` | `mean_wind_speed` | 末小时 10m 风速（m/s），**deprecated** |
| `wind_direction` | `float` \| `null` | `dominant_wind_direction` | 末小时来风方向（°），**deprecated** |
| `upwind` | `str` | `upwind_direction` + `dominant_wind_direction_label` | 上风向文字描述，**deprecated** |
| `downwind` | `str` | `downwind_direction` | 下风向文字描述，**deprecated** |

### result 对象（status=failed）

```jsonc
{}   // 固定空对象，不包含任何字段
```


### 类型约束

所有数值字段使用 **Python 原生类型**（`float` / `int` / `bool`），不得含 numpy 或 pandas 标量。全部输出可直接 `json.dumps()` 序列化。

### 各状态响应示例

**success（单日）：**
```jsonc
{
  "agent_name": "MeteorologyAgent",
  "status": "success",
  "result": {
    "task_id": "HK_MK_20260723_0800",
    "weather_risk": 0.62,
    "risk_type": "medium",
    "dispersion_condition": "不利扩散（静风或弱风）",
    "static_wind_score": 0.75,
    "wind_shift_score": 0.30,
    "humidity_risk_score": 0.55,
    "rain_removal_score": 0.20,
    "insufficient_rain_removal_score": 0.80,
    "mean_wind_speed": 1.2,
    "dominant_wind_direction": 135.0,
    "dominant_wind_direction_label": "SE",
    "upwind_direction": 135.0,
    "downwind_direction": 315.0,
    "mean_humidity": 84.0,
    "total_precipitation": 0.0,
    "mean_temperature": 28.5,
    "mean_cloud_cover": 65.0,
    "weather_code_daily": 3,
    "dominant_weather_code_daily": 3,
    "weather_code_daily_series": [{"date": "2026-07-23", "weather_code": 3}],
    "hourly_count": 4,
    // deprecated，兼容期保留
    "wind_speed": 1.2,
    "wind_direction": 135.0,
    "upwind": "上风向（来向）约 东南（135°）",
    "downwind": "下风向（去向）约 西北"
  },
  "evidence": ["..."],
  "confidence": 0.95,
  "data_quality": {
    "missing_rate": 0.0,
    "fallback_used": false,
    "source": "Open-Meteo Archive API",
    "note": "数据完整"
  },
  "created_at": "2026-07-23 11:00:00"
}
```

**success（多日，weather_code_daily 为 null）：**
```jsonc
{
  "result": {
    "weather_code_daily": null,
    "dominant_weather_code_daily": 3,
    "weather_code_daily_series": [
      {"date": "2026-07-22", "weather_code": 1},
      {"date": "2026-07-23", "weather_code": 3}
    ]
    // ...其余字段同上
  }
}
```

**warning（fallback）：**
```jsonc
{
  "status": "warning",
  "result": { /* 同 success，包含完整评分字段 */ },
  "confidence": 0.7125,
  "data_quality": {
    "missing_rate": 0.0,
    "fallback_used": true,
    "source": "Local Fallback CSV",
    "note": "已使用本地样例数据兜底；API 原因: connection timeout；数据完整"
  }
}
```

**failed（输入错误）：**
```jsonc
{
  "status": "failed",
  "result": {},
  "evidence": ["task 输入不合法: 缺少必填字段: 'task_id'"],
  "confidence": 0.0,
  "data_quality": {
    "missing_rate": 1.0,
    "fallback_used": false,
    "source": "None",
    "note": "task 输入不合法: 缺少必填字段: 'task_id'"
  }
}
```


---

## 输入契约（已冻结）

### 标准 task 结构

```jsonc
{
  "task_id":      "HK_MK_20260723_0800",  // 必填
  "start_time":   "2026-07-23 08:00:00",  // 必填，YYYY-MM-DD HH:mm:ss
  "end_time":     "2026-07-23 11:00:00",  // 必填，YYYY-MM-DD HH:mm:ss
  "lat":          22.3193,                // 必填，WGS84，[-90, 90]
  "lon":          114.1694,               // 必填，WGS84，[-180, 180]
  "station_id":   "MK",                   // 可选，建议联调时提供
  "station_name": "Mong Kok Roadside"     // 可选，建议联调时提供
}
```

### 必填字段

| 字段 | 类型 | 约束 | 说明 |
|------|------|------|------|
| `task_id` | `str` | 非空字符串 | 任务唯一标识，透传到输出 result |
| `start_time` | `str` | 合法时间，格式 `YYYY-MM-DD HH:mm:ss` | 分析窗口起始，整点化后使用 |
| `end_time` | `str` | 合法时间，格式 `YYYY-MM-DD HH:mm:ss` | 分析窗口结束，整点化后使用 |
| `lat` | `float` / `int` | ∈ [-90, 90]，WGS84 | 站点纬度 |
| `lon` | `float` / `int` | ∈ [-180, 180]，WGS84 | 站点经度 |

> **lat 和 lon 必须同时提供**，缺少任意一个均视为输入错误。

### 可选字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `station_id` | `str` | `"UNKNOWN"` | 气象站编号，用于 ETL 标记，建议联调时提供 |
| `station_name` | `str` | `null` | 气象站名称，原样透传到 result |

### 时间约束

| 约束 | 规则 |
|------|------|
| 格式 | `YYYY-MM-DD HH:mm:ss`，无时区标注时按 **Asia/Hong_Kong** 解释 |
| 整点化 | `start_time` 和 `end_time` 均向下取整到小时（如 `08:30` → `08:00`） |
| 顺序 | 整点化后 `end_time ≥ start_time` |
| 最小窗口 | 整点化后差值 ≥ **1 小时** |
| 最大窗口 | 整点化后差值 ≤ **168 小时（7 天）** |


---

## 语义约定（已冻结）

本节定义系统全链路的时间、单位、坐标、风向和天气代码语义。所有输入输出均遵循以下约定，不得单独覆盖。

### 时间与时区

| 项目 | 约定 |
|------|------|
| 时间格式 | `YYYY-MM-DD HH:mm:ss` |
| 时区 | **Asia/Hong_Kong（UTC+8）**，固定不变，不使用 `auto` |
| 无时区标注的时间 | 按 Asia/Hong_Kong 解释 |
| 示例 | `2026-07-23 08:00:00` |

### 单位

| 物理量 | 单位 | Open-Meteo 参数 |
|--------|------|-----------------|
| 风速 | **m/s** | `wind_speed_unit=ms` |
| 气温 | **°C（摄氏度）** | `temperature_unit=celsius` |
| 降水量 | **mm** | `precipitation_unit=mm` |
| 风向 | **度（°），0–360** | — |

### 坐标系

经纬度统一使用 **WGS84**，lat ∈ [-90, 90]，lon ∈ [-180, 180]。

### 风向语义

| 术语 | 含义 |
|------|------|
| `wind_direction`（deprecated） | **来风方向**（末小时），0–360°，0/360=正北 |
| `dominant_wind_direction` | **来风主导方向**（时间窗口聚合，圆向量均值法），0–360° |
| `upwind_direction` | 等于 `dominant_wind_direction`（来风方向数值） |
| `downwind_direction` | `(dominant_wind_direction + 180) % 360`（去风方向数值） |

示例：`dominant_wind_direction=135`（东南风，风从东南吹来），`downwind_direction=315`（向西北扩散），`dominant_wind_direction_label="SE"`。

### weather_code_daily 语义

| 项目 | 说明 |
|------|------|
| 来源 | Open-Meteo Archive API `daily.weather_code` |
| 广播规则 | `_merge_daily_into_hourly()` 将当天的 `weather_code` 复制到同日所有小时行 |
| 标准列名 | `weather_code_daily`（加 `_daily` 后缀区分 hourly 字段） |
| 含义 | 当天**主导天气代码**（WMO Weather Interpretation Code） |
| 非含义 | **不代表**每个小时独立观测到的天气代码 |
| 评分参与 | **当前不参与**四维污染物扩散风险评分 |
| 单日输出 | `weather_code_daily` 返回 `int` 标量 |
| 多日输出 | `weather_code_daily` 固定为 `null`；使用 `dominant_weather_code_daily`（众数）和 `weather_code_daily_series`（各日列表） |
| 缺失容错 | daily 数据缺失时，三个代码字段均为 `null`，Agent 仍正常运行并返回 `success` |

---

## 数据来源：Open-Meteo Archive API

Agent 使用 [Open-Meteo Archive API](https://archive-api.open-meteo.com/v1/archive) 获取历史气象数据。

### 固定请求参数

```
timezone=Asia/Hong_Kong
wind_speed_unit=ms
temperature_unit=celsius
precipitation_unit=mm
```

所有参数定义在 `config/field_schema.json` 的 `api_request_params` 节，`open_meteo_client.py` 从该节读取，不硬编码。

### hourly 字段（共 11 个）

| 原始列名 | 标准列名 | 说明 |
|----------|----------|------|
| `temperature_2m` | `temperature` | 2m 气温（°C） |
| `relative_humidity_2m` | `humidity` | 2m 相对湿度（%） |
| `precipitation` | `precipitation` | 小时累计降水（mm） |
| `rain` | `rain` | 小时降雨量（mm） |
| `snowfall` | `snowfall` | 小时降雪量（cm） |
| `wind_speed_10m` | `wind_speed` | 10m 风速（m/s），**参与评分** |
| `wind_direction_10m` | `wind_direction` | 10m 风向（°，来风方向），**参与评分** |
| `wind_gusts_10m` | `wind_gusts` | 10m 阵风（m/s） |
| `wind_speed_100m` | `wind_speed_100m` | 100m 风速（m/s），保留备用 |
| `wind_direction_100m` | `wind_direction_100m` | 100m 风向（°，来风方向），保留备用 |
| `cloud_cover` | `cloud_cover` | 总云量（%），保留备用 |

### daily 字段（共 1 个）

| 原始列名 | 广播后标准列名 | 说明 |
|----------|---------------|------|
| `weather_code` | `weather_code_daily` | WMO 天气代码，按日期广播到当天全部小时行 |


### 已移除字段（v1.0.0 → v1.1.0）

以下字段已从 API 请求和标准输出中移除，不得再被引用：

| 字段名 | 移除原因 |
|--------|----------|
| `apparent_temperature` | 不参与任何评分，已从请求中删除 |
| `weather_code`（hourly） | 已改为 daily 层级，广播后以 `weather_code_daily` 存储 |
| `snow_depth` | 不参与任何评分，已从请求中删除 |
| `cloud_cover_low` | 不参与任何评分，已从请求中删除 |
| `cloud_cover_mid` | 不参与任何评分，已从请求中删除 |
| `cloud_cover_high` | 不参与任何评分，已从请求中删除 |
| `soil_temperature_0_to_7cm` | 不参与任何评分，已从请求中删除 |

---

## 入口函数

### 单任务

```python
from agents.meteorology_agent import run

output: dict = run(task: dict)
```

### 批处理

```python
from agents.meteorology_agent import batch_run

outputs: list[dict] = batch_run(tasks: list[dict])
```

`batch_run` 支持三种组合：

| 场景 | tasks 内容 |
|------|-----------|
| 单站点单时段 | 长度为 1 的列表 |
| 单站点多时段 | 多个 task 共享相同 `lat/lon`，不同 `start_time/end_time` |
| 多站点多时段 | 多个 task 各有不同 `lat/lon` 与时间段 |

- 返回列表与输入列表**等长、顺序一致**
- 任何单个任务失败只影响自身，其余任务继续执行
- 每条结果均含 `result.task_id`，方便联调定位
- 若 task 未提供 `task_id`，自动补全为 `batch_{index}`

额外可选参数（仅供内部测试注入，调用方无需关心）：

```python
run(task, rules_path=Path(...), fallback_csv=Path(...))
batch_run(tasks, rules_path=Path(...), fallback_csv=Path(...))
```

---

## 变更原则

1. **固定字段**（顶层 7 键 + result 全部冻结字段）永远不得删除或重命名
2. 新增业务字段只能放在 result 的**附加字段**区，并在本文档补充说明
3. 任何破坏性变更须升级版本号并保留旧字段兼容期
4. **语义约定**（时区、单位、风向、时间格式）不得在单个模块内单独覆盖，须通过修改 `config/field_schema.json` 统一变更
5. **deprecated 字段**在整个兼容期内必须继续输出，不得提前删除；移除须在下一主版本明确声明

---

## 版本历史

| 版本 | 变更说明 |
|------|----------|
| 1.5.0 | **冻结 result 核心字段结构**。新增：`insufficient_rain_removal_score`（= 1 - rain_removal_score，互补字段）；`mean_wind_speed`、`dominant_wind_direction`、`dominant_wind_direction_label`、`upwind_direction`、`downwind_direction`（聚合风向，圆向量均值法）；`mean_humidity`、`total_precipitation`、`mean_temperature`、`mean_cloud_cover`（窗口聚合统计）；`dominant_weather_code_daily`、`weather_code_daily_series`（多日天气代码聚合，众数 + 序列）。`weather_code_daily` 单日返回 int 标量，多日返回 null。旧字段 `wind_speed`、`wind_direction`、`upwind`、`downwind` 标记为 deprecated，兼容期保留。明确 null 规范：缺失时返回 null，不伪造默认值。 |
| 1.4.0 | 新增"输出契约"章节；status 三值化（success/warning/failed）；result 在 failed 时固定为 `{}`；data_quality 新增 source 字段；所有数值确保 Python 原生类型；更新各状态响应示例 |
| 1.3.0 | 新增"输入契约"章节；task_id 升为必填；新增 lat/lon 同时存在约束、坐标范围校验、时间整点化、窗口长度约束 [1h, 168h]；统一输入错误响应格式；batch_run 单条错误隔离说明 |
| 1.2.0 | 新增"语义约定"章节（时区/单位/坐标/风向/weather_code_daily 全部冻结）；Open-Meteo 请求固定 timezone=Asia/Hong_Kong、wind_speed_unit=ms、temperature_unit=celsius、precipitation_unit=mm；移除 timezone=auto |
| 1.1.0 | 新增 Data Source 章节；hourly 精简为 11 字段；新增 daily weather_code，广播为 weather_code_daily；移除 apparent_temperature / snow_depth / cloud_cover_low/mid/high / soil_temperature_0_to_7cm / hourly weather_code |
| 1.0.0 | 初始版本 |
