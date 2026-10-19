# TravelAgent 接口说明（出行建议与路线推荐）

**Agent 名称**：`TravelAgent`
**代码位置**：`agents/travel_agent.py`
**配置文件**：`config/travel_profile.json`
**工具模块**：`tools/route_tools.py`（路网与轨道）、`tools/travel_advice_tools.py`（气象-习惯评分）
**HTTP 入口**：`api/travel_api.py`，注册前缀 `/api/travel`
**案例演示**：`examples/run_travel_case.py` → `examples/travel_case_output.json`、`examples/travel_case_report.md`

---

## 一、职责

给定**起点位置**，结合**气象条件**与**用户出行习惯**，输出可解释的路线方案与出行建议。

产出为筛查级、可解释的比较结果：把气象与污染条件按用户敏感度换算成路线扣分项，用于出行前快速比选。**不等同于实时路况导航，也不代表官方交通或健康通告。**

**起终点完全由坐标决定**，换一对坐标就是换一个行程；没有"只能走某条固定路线"的限制。

---

## 二、污染暴露场（"避开污染"的依据）

模块不使用"是否在下风向""是否在受监测道路上"这类粗粒度判据——`near_stations` 字段覆盖全部
12833 条路段，无法区分主干道与小路，且会与精细模型重复计数。改为建立 **0–1 相对暴露场**。

**源的两类构成**

| 源类型 | 强度来源 | 建模方式 |
|--------|---------|---------|
| 监测站源 | `air_quality_result.anomaly_score` 或 `air_quality_risk` | 点源，参与高斯烟羽扩散 |
| 交通走廊源 | `traffic_result.traffic_pressure_score` 或 `traffic_pressure_score` | 把污染源周边 1.5 km 内的道路按里程等间隔采样成一组弱源，近似线源 |

**扩散计算**（`tools/pollution_field.py`，参数集中在 `config/pollution_rules.json`）

- 沿主导风向做高斯烟羽：横向 `σy`、垂向 `σz` 随下风向距离按幂律增长，扩散体积增大即稀释。
- 时间衰减 `0.5^(t / half_life)`，风速越弱稀释越差，用 `stability_boost` 表达静风积聚。
- 低风速时无定向输送，改为各向同性积聚。**这不是开关**：`field = calm_weight × 积聚项 +
  (1 − calm_weight) × 烟羽项`，`calm_weight` 在 `threshold_ms`(0.5) 到 `transition_ms`(1.2)
  之间线性过渡。早先用 `wind < 0.5` 硬判断，风速 0.49→0.51（差 0.02 m/s）会让行程区暴露
  中位数从 0.636 掉到 0.090，差 7 倍——那是不连续，不是物理。
- 两个分支各有独立的标定系数（`plume.plume_scale` 与 `calm.accumulation_scale`）。烟羽分支的
  横向高斯会丢弃绝大部分偏离风向的源，各向同性分支把半径内的源全部计入；共用同一系数会让
  静风分支顶到 0–1 上限而失去区分度（实测行程区 30.9% 的路段饱和到 1.0，避污成本全部相同）。
- **缺少风速或风向时不建场**，而不是退回静风假设。「测得静风」和「没测到风速」是两件不同的
  事，后者按静风处理会给出一个全域饱和、没有梯度的假场，看起来正常但绕行无从选择。
  此时 `pollution_field` 为 `null`、`pollution_advice.available` 为 `false`，并给出警告。
- 采样点强度与该点代表的走廊长度成正比，**场值因此与采样密度无关**（配置的采样间隔在
  0.35–0.7 km 区间内结果一致）。

**输出口径**：`0–1 相对暴露指数`，用于路线之间的**相对比较**，不是浓度值。所有证据与建议
都必须以"相对指数""筛查级"表述。

---

## 三、请求

### 单任务模式

```json
{
  "task_id": "TRAVEL_HK_TST_PE_20230815_0800",
  "origin":      { "name": "尖沙咀站", "lat": 22.29770, "lon": 114.17218 },
  "destination": { "name": "太子站",   "lat": 22.32441, "lon": 114.16828 },
  "start_time": "2023-08-15 08:00:00",
  "end_time":   "2023-08-15 11:00:00",
  "travel_profile": "commuter",
  "pollution_source": {
    "station_id": "MK",
    "station_name": "Mong Kok Roadside",
    "lat": 22.3193,
    "lon": 114.1694,
    "radius_km": 3.0
  },
  "air_quality_risk": 0.94,
  "traffic_pressure_score": 0.7363
}
```

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `task_id` | 是 | string | 任务唯一标识 |
| `origin` | 是 | object | 起点，含 `name` / `lat` / `lon` |
| `destination` | 是 | object | 终点，含 `name` / `lat` / `lon` |
| `start_time` / `end_time` | 是 | string | 分析窗口，格式 `YYYY-MM-DD HH:mm:ss`，按 Asia/Hong_Kong 解释 |
| `travel_profile` | 否 | string | 画像 ID，默认取配置文件第一条（`commuter`） |
| `pollution_source` | 否 | object | 单个污染事件点位，用于建立暴露场 |
| `pollution_sources` | 否 | array | 多个污染事件点位；与 `pollution_source` 合并使用 |
| `air_quality_risk` | 否 | number | 监测站源强度 0–1；缺省取 `air_quality_result.anomaly_score` |
| `traffic_pressure_score` | 否 | number | 走廊源强度 0–1；缺省取 `traffic_result.traffic_pressure_score` |
| `meteorology_result` | 否 | object | 已算好的气象 Agent 响应；传入则不再实时请求气象接口 |
| `air_quality_result` | 否 | object | 空气质量 Agent 响应 |
| `traffic_result` | 否 | object | 交通 Agent 响应 |

缺少污染源位置时不会报错：退化为"只按气象条件比选"，并在 `warnings` 中说明。

### 出行习惯画像（`config/travel_profile.json`）

| profile_id | 名称 | 步行上限 | 单程上限 | 时间弹性 | 吸入交换率 | 天气乘数 | 方式常数（公交/驾车/步行） |
|------------|------|---------|---------|---------|-----------|---------|--------------------------|
| `commuter` | 通勤族 | 1.2 km | 25 min | 10 min | 2.0 | 0.8 | −5 / −2 / 0 |
| `parent_with_child` | 带娃家长 | 0.8 km | 30 min | 20 min | 5.0 | 1.2 | −5 / −3 / 0 |
| `elderly` | 长者 | 0.6 km | 35 min | 40 min | 4.0 | 1.2 | −6 / 0 / 0 |
| `runner` | 跑步爱好者 | 6.0 km | 60 min | 120 min | 6.0 | 0.4 | 0 / +3 / −8 |
| `tourist` | 游客 | 3.0 km | 50 min | 60 min | 1.5 | 0.9 | −2 / +2 / −6 |

每个画像还包含：
- `sensitivities`：对降雨 / 高温 / 湿度 / 风力 / 空气质量的敏感度（乘在全局分级结果上）
- `pollution_exchange_minutes`：愿意为减少 1 EU 吸入当量多花的分钟数（时间 ↔ 健康的取舍）
- `mode_constant_minutes`：离散选择里的方式常数（ASC），负数表示偏好该方式
- `weather_time_multiplier`：天气把户外等效时间最多放大多少倍
- `walk_speed_factor`：相对基准步速的系数（长者 0.75、跑步爱好者 1.7）

---

## 四、响应及字段

整体遵循项目统一的 Agent 输出契约：

```json
{
  "task_id": "...",
  "agent_name": "TravelAgent",
  "status": "success | warning | error",
  "result": { /* 业务字段，见下 */ },
  "evidence": ["..."],
  "confidence": 0.85,
  "data_quality": { "missing_rate": 0.0, "note": "..." },
  "warnings": [],
  "created_at": "2026-09-27T18:10:48+08:00"
}
```

### `result` 关键字段

```ts
interface TravelResult {
  origin: TravelPoint
  destination: TravelPoint
  straight_line_km: number
  travel_profile: TravelProfile      // 生效的画像与其阈值
  weather_summary: WeatherSummary    // 时段气象摘要
  weather_severity: Severity         // 气象分级（0-1，未按用户缩放）
  personalized_severity: Severity    // 按用户敏感度缩放后的影响因子
  travel_condition: {                // 出行条件总分
    score: number                    // 0-100
    level: 'good' | 'fair' | 'poor' | 'severe'
    level_label: string              // 中文标签
    dominant_factors: string[]
  }
  route_options: RouteOption[]       // 已按该画像评分降序排列
  recommendation: {
    route_id: string
    mode: 'walk' | 'drive' | 'transit'
    mode_label: string
    headline: string
    reasons: string[]                // 含与跨方式次优方案的对比
    alternative_by_mode: Record<string, ModeSummary>
  }
  departure_advice: {                // 出发时段建议
    recommended_window: string | null
    avoid_window: string | null
    note: string
    hourly_burden: { time: string; burden: number }[]
  }
  gear_reminders: string[]           // 装备与防护提醒
  assumptions: string[]              // 模型局限，随结果一起返回
}
```

### `RouteOption`

```ts
interface RouteOption {
  route_id: string                   // 如 "transit_0" / "walk_1" / "drive_2"
  mode: 'walk' | 'drive' | 'transit'
  mode_label: string
  rank: number
  recommended: boolean
  distance_km: number                // 总里程 = 路网里程 + 起终点吸附接驳段
  road_distance_km: number           // 路网内里程
  connector_km: number               // 起终点到路网的直线接驳里程
  duration_minutes: number           // 在途时间（含路口等待与时段拥堵）
  junctions: number                  // 沿途路口数（相邻 30 m 内视为同一路口）
  utility_minutes: number            // 等效时间：排序依据，越小越好
  score: number                      // 0-100，由 utility_minutes 单调映射而来
  intake_index: number               // 吸入当量 (EU)
  intake_detail: {
    minutes: number
    outdoor_equivalent_minutes: number
    intake_index: number
    by_kind: Record<string, { label: string; minutes: number; intake_index: number }>
  }
  utility_detail: {
    outdoor_equivalent_minutes: number
    weather_added_minutes: number
    pollution_added_minutes: number
    mode_constant_minutes: number
    overrun_minutes: number
    walk_overrun_minutes: number
  }
  exchange_minutes_per_eu: number    // 该画像下 1 EU 折算成多少分钟
  timeline: {                        // 各段耗时明细
    connector_minutes?: number
    total_minutes?: number
    main?: { moving_minutes: number; junction_wait_minutes: number; avg_speed_kmh: number }
    access?: { ... }
    egress?: { ... }
    rail?: { running_minutes: number; dwell_minutes: number; fixed_minutes: number }
  }
  road_names: string[]               // 途经道路中文名（公共交通为站序）
  geometry: [number, number][]       // [[lon, lat], ...]
  exposure: {
    total_km: number
    dose_index: number               // Σ(路段里程 × 指数)，几何口径，仅供对照
    mean_index: number
    peak_index: number
    high_exposure_km: number         // 处于高暴露区（指数 ≥ 阈值）的里程
    monitored_corridor_km: number
    monitored_corridor_ratio: number
  }
  transit_detail: {                  // 仅 mode = transit
    boarding_station: string
    alighting_station: string
    stations: string[]
    stop_count: number
    rail_distance_km: number
    access_distance_km: number
    egress_distance_km: number
    access_junctions: number
    egress_junctions: number
    access_mean_index: number
    egress_mean_index: number
    wait_index: number               // 上下车站中暴露较高的一侧
    platform_access_minutes: number
    wait_minutes: number
  } | null
  reason: string
}
```

---

## 五、评分口径

从 v2 起，评分不再是"多个扣分项相加"，而是**一个以分钟为单位的效用式**：

```
U = 户外等效时间 × (1 + 画像天气乘数 × 天气负担)
    + 画像吸入交换率 × 吸入当量
    + 方式常数
    + 超限罚分（超出步行耐受距离、超出单程时间上限）
```

`score = 100 × anchor / (anchor + U)`，其中 `anchor = 40` 分钟（`scoring.score_anchor_minutes`）。
得分只是效用的单调映射，**排序依据始终是 `utility_minutes` 升序**。

### 为什么改

旧模型把"走多远"拆成 `duration`／`distance`／`weather_exposure`／`over_max_trip_minutes`
四项，实测两两相关系数 **0.94~0.99**，等于把同一件事重复计了四遍；而真正独立的两个轴
（污染暴露 r≈0.5~0.7、路口复杂度 r≈0.1~0.6）被淹没。更严重的是污染项以
"本次候选集内的最差方案"做归一化，导致**同一路线的得分会随"这次还比了哪几条"变化**
（实测偏移 2.4 分）。新式子全部是绝对量，因此可分解、可复现。

### 各项含义

| 项 | 单位 | 口径 |
|---|---|---|
| `outdoor_equivalent_minutes` | 分钟 | Σ 各分段时长 × 该段对天气的隔绝系数 |
| `weather_added_minutes` | 分钟 | 户外等效时间 × 画像天气乘数 × 天气负担（雨/热/湿/风的均值） |
| `pollution_added_minutes` | 分钟 | 画像吸入交换率 × 吸入当量（EU） |
| `mode_constant_minutes` | 分钟 | 离散选择里的方式常数（ASC），负值表示偏好该方式 |
| `overrun_minutes` | 分钟 | 超出 `max_trip_minutes` 的部分 |
| `walk_overrun_minutes` | 分钟 | 步行超出 `max_walk_km` 的部分 × `walk_overrun_minutes_per_km` |

**吸入交换率**是本模型里画像唯一表达"时间 ↔ 健康"取舍的参数：它表示该画像愿意为
减少 1 EU 吸入当量多花多少分钟。时间敏感的通勤族取 2，带娃家长取 5，跑步爱好者取 6。
这个数可直接读出：`exchange_minutes_per_eu` 会随结果返回。

### 吸入当量（EU）

```
intake = Σ 分段[ 平均暴露指数 × 停留分钟 × (该段呼吸速率 ÷ 步行呼吸速率) × 该段一次穿透比例 ]
```

单位 EU = **以步行呼吸量计的污染分钟数**。步行 1 分钟、指数 1.0 的空气 = 1 EU。

| 分段 | 呼吸速率 (L/min) | 穿透比例 | 天气隔绝 |
|---|---|---|---|
| 步行 | 22 | 1.0 | 1.0 |
| 驾车 | 10 | 0.35 | 0.6 |
| 候车与进出站 | 12 | 0.6 | 0.5 |
| 轨道区间 | 10 | 0.1 | 0.1 |

这样同样是主干道上的 3 公里，步行与车内的吸入量差一到两个数量级。旧的
`Σ指数×公里` 口径不含停留时间与呼吸量，三种方式的"剂量"其实不是同一个物理量，
却被直接同台比较、同台归一化。`exposure.dose_index` 仍然返回，但只作几何对照。

### 耗时模型

- 步行 / 驾车：`里程 ÷ 天气折减后速度` + `路口数 × 单次路口等待`。
  路口按"节点度 ≥ 3、相邻 30 m 内合并"计数——只看方位差会把中心线折点算成转向，
  一条弥敦道算出一个 21 个"转弯"的直路。
- 驾车另按道路等级（`route_number` 有编号的干线 ×1.3，其余 ×0.9）与时段拥堵
  （07–09 / 17–19 时 ×0.7）区分。
- 轨道：运行时间 + 每站停站 45 s + 进站 4 分钟 + 候车 3 分钟。
- 起终点到路网的**吸附接驳段**计入里程、耗时与吸入当量。

### 避污路线的生成

搜索时给每条路段乘上 `1 + W × 指数^p`（默认 `W=8`，`p=2`）。超线性意味着强烈回避高暴露
核心、但容忍轻度暴露——这比线性加权更接近"避开污染"的语义。常规搜索与避污搜索的结果
按主干道路名序列去重后合并，因此**最脏的基准路线也会保留在列表里**，便于对照。

所有阈值集中在 `config/travel_profile.json`（`weather_thresholds` / `segments` / `intake` /
`time_model` / `scoring` / `departure_curve` / `uncertainty`）与 `config/pollution_rules.json`
（`source_strength` / `plume` / `calm` / `grid` / `avoidance`），代码内不硬编码。

---

## 六、出发时刻曲线与结论稳健性

这两项把"筛查级"这个自我声明变成了可量化的数字。

### `result.departure_dose_curve`

逐小时风场是气象 Agent 已经返回的字段，不需要额外请求。按每个小时的风速风向**重建暴露场**，
再把候选方案的分段重新采样，得到"如果这个时刻出发，会吸入多少"；驾车方案还会按时段拥堵
重算耗时。因此它同时回答两个问题：晚走能少吸多少、出发时间会不会改变方式选择。

```jsonc
{
  "available": true,
  "unit": "EU",
  "note": "未来 4 小时内，11:00 出发吸入当量最低（0.70 EU），09:00 最高（1.69 EU），相差 59%。",
  "baseline_window": "2023-08-15 08:00:00+08:00",
  "best_window": "...",
  "worst_window": "...",
  "intake_range": { "min": 0.7, "max": 1.69, "reduction_pct": 59.0 },
  "recommendation_changes_with_time": false,
  "points": [
    {
      "time": "...", "wind_speed_ms": 1.08, "wind_direction": 304.0,
      "precipitation": 0.0, "recommended_route_id": "transit_0",
      "recommended_mode": "公共交通", "intake_index": 1.12,
      "utility_minutes": 2.3, "duration_minutes": 23.2
    }
  ]
}
```

### `result.uncertainty`

对风速（±15%）、风向（±20°）与画像吸入交换率（±20%）做蒙特卡洛，每次重建暴露场并重算
全部候选方案的效用，统计推荐方案仍是第一的比例。`agent.confidence` 直接取这个比例。

```jsonc
{
  "available": true,
  "samples": 16,
  "perturbations": { "wind_speed_rel_sigma": 0.15, "wind_direction_sigma_deg": 20.0, "exchange_rel_sigma": 0.2 },
  "recommended_route_id": "transit_0",
  "recommended_share": 1.0,
  "level": "high",
  "level_label": "稳健",
  "top_pick_distribution": [{ "route_id": "transit_0", "mode_label": "公共交通", "share": 1.0 }],
  "intake_intervals": { "transit_0": { "p05": 0.52, "p50": 0.71, "p95": 0.94 } },
  "note": "在风速 ±15%、风向 ±20°、吸入交换率 ±20% 的 16 次扰动中，推荐方案仍为第一的比例为 100%（稳健）。"
}
```

**耗时**：每次扰动要重建一次暴露场（约 0.13 s），16 次约 2 s；加上出发曲线与主流程，
一次完整规划在本机沙箱下约 18 s，其中约 13 s 是沙箱文件代理在 pandas 时区处理上的开销。
样本数由 `config/travel_profile.json` 的 `uncertainty.samples` 控制。

---

## 七、HTTP 接口

### `GET /api/travel/health`

```json
{ "agent_name": "TravelAgent", "status": "ok" }
```

### `GET /api/travel/profiles`

列出可用画像，供前端下拉选择。

### `POST /api/travel/plan`

请求体同第二节。校验失败返回 `400` 与 `{"status":"error","message":"..."}`；成功返回完整 Agent 响应。

```bash
curl -X POST http://127.0.0.1:8000/api/travel/plan \
  -H 'Content-Type: application/json' \
  -d @examples/travel_case_request.json
```

---

## 八、案例演示

运行：

```bash
python examples/run_travel_case.py
```

产出：

| 文件 | 内容 |
|------|------|
| `examples/travel_case_output.json` | 案例 × 场景 × 画像的完整结构化结果 |
| `examples/travel_case_report.md` | 可读案例报告 |
| `examples/travel_case_request.json` | 单次请求示例 |
| `frontend/src/mock/travel.mock.json` | **同一脚本生成**的前端精简数据，避免手工裁剪导致前后端不同步 |

**两个方向相反的行程**，用来验证避污是否真的有用：

| 案例 | 行程 | 特点 |
|------|------|------|
| 案例一 | 旺角站 → 尖沙咀站 | 向南，背离下风向烟羽；**起点位于污染热点内部** |
| 案例二 | 尖沙咀站 → 太子站 | 向北，行程方向与扩散方向一致；**最短路径会穿过高暴露走廊** |

**避污效果（场景 A：高湿静风 + 空气质量高风险 + 交通压力 0.7363）**

| 案例 | 方式 | 指标 | 直走最短路线 | 避污路线 | 变化 |
|------|------|------|------------|---------|------|
| 案例二 | 驾车 / 步行 | 里程 | 3.00 km | 3.38 km | +13% |
| 案例二 | 驾车 / 步行 | 吸入剂量 | 0.45 | 0.39 | **−15%** |
| 案例二 | 驾车 / 步行 | 高暴露里程 | 0.30 km | 0.00 km | **−100%** |
| 案例一 | — | — | 最短路线本身就是暴露最低的路线 | — | — |

案例一的结论是**如实的"绕行无用"**：起点就在热点内，任何路线都要先穿出去，
最短路径同时也是最快离开热点的路径，绕行只会增加暴露里程。模块会直接说明这一点，
而不是无脑建议绕路。

**推荐仍受出行习惯支配**：同一对起终点、同一气象下，时间敏感的通勤族与偏好步行、
时间弹性的跑步爱好者会得到不同推荐。

---

## 九、模型局限

以下假设随每次运行写入输出的 `assumptions` 与 `pollution_field.assumptions`：

1. 路线基于香港道路中心线数据集规划，**未叠加实时交通流量与临时封路**。
2. 轨道交通网络由车站点位按邻近关系（≤1.6 km，每站 3 邻居）重建，站序为筛查级近似，
   **非官方运营线路图**。
3. **暴露指数是 0–1 相对值**，不含化学反应、地形效应与垂直分层，**不是浓度预测**；
   它只支持路线之间的相对比较。
4. 交通走廊按沿线等间隔采样点近似线源，采样间隔见 `config/pollution_rules.json`。
5. 步行与驾车速度按画像基准速度叠加天气折减、路口等待与时段拥堵，**非个人实测速度**；
   路口等待按"节点度 ≥ 3 且相邻 30 m 内合并"的路口数估算，单次等待时长是参数。
6. **吸入当量是相对口径**：呼吸速率与车厢穿透系数取自常识量级（步行 22 L/min、车内 10 L/min、
   穿透 0.35 等），**不等同于实测吸入剂量**，只支持方案之间的相对比较。
7. **拥堵与道路等级只做粗分**：`route_number` 能识别有编号的干线（占 6.5% 路段），
   但无法区分市区主干道与小路，两者都按"非干线"处理。
8. **结论稳健性来自输入扰动，不包含模型结构误差**：蒙特卡洛只扰动风速、风向与画像交换率，
   不覆盖暴露场公式本身的不确定性。

---

**最后更新**：2026-09-27
