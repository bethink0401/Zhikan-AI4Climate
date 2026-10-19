# 前后端联调接口说明

**后端基地址（本地开发）**：`http://127.0.0.1:8000`

本文说明当前已实现、可供前端调用的后端接口。前端当前的 `/api/analyze` 尚无对应后端路由，真实联调请使用本文的气象与空间接口。

## 1. 通用约定

- 请求及响应均为 `application/json`。
- 时间格式：`YYYY-MM-DD HH:mm:ss`，按 `Asia/Hong_Kong` 解释。
- 坐标系：WGS84；`lat` 为纬度、`lon` 为经度。
- 风向使用气象学**来风方向**：`0°` 为北风（从北吹来）。污染传输实际去向为 `(wind_from + 180) % 360`。
- 预测结果为筛查级、可解释的风矢量平流结果，并非浓度、化学反应或地形效应模拟。

## 2. 气象分析与污染路径预测

### `POST /api/meteorology/analyze`

### 请求体

```json
{
  "task_id": "HK_MK_20260913_0800",
  "station_id": "MK",
  "station_name": "Mong Kok Roadside",
  "lat": 22.3227,
  "lon": 114.1683,
  "start_time": "2026-09-13 08:00:00",
  "end_time": "2026-09-13 11:00:00",
  "prediction_horizon_hours": 3
}
```

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `task_id` | 是 | string | 任务唯一标识 |
| `lat` / `lon` | 是 | number | 站点 WGS84 坐标 |
| `start_time` / `end_time` | 是 | string | 分析窗口，1–168 小时 |
| `station_id` / `station_name` | 否 | string | 站点标识与显示名称 |
| `prediction_horizon_hours` | 否 | number | 短时预测步数，默认 3，自动限制到 1–24 小时 |

### 响应外层

```ts
interface MeteorologyApiResponse {
  mode: 'single'
  count: 1
  result: MeteorologyAgentResponse
}
```

前端需要读取的气象结果位于 `response.result.result`。

### 风向预测字段

```ts
interface WindForecastHour {
  lead_hour: number
  wind_from_direction: number // 气象来风方向，0–360°
  wind_to_direction: number   // 污染实际传输方向，来向 + 180°
  wind_speed_ms: number
  direction_uncertainty_deg: number
}

interface WindDirectionForecast {
  status: 'ok' | 'calm' | 'unavailable'
  method: 'recency_weighted_circular_persistence'
  lookback_hours_used?: number
  resultant_length?: number
  turn_rate_deg_per_hour?: number
  confidence: number
  reason?: string
  forecast_hours: WindForecastHour[]
}
```

`wind_direction_forecast` 采用最近最多 12 小时观测数据进行加权圆统计和趋势外推。`resultant_length` 越接近 1，说明风向越集中；`direction_uncertainty_deg` 会随预报时效增大。

### 污染路径预测字段

```ts
interface PollutionPathStep {
  lead_hour: number
  lon: number
  lat: number
  wind_to_direction: number
  distance_this_hour_km: number
  distance_from_origin_km: number
  remaining_fraction: number // 一阶衰减代理值，不是浓度
  corridor_half_width_km: number
}

interface PollutionPathPrediction {
  status: 'ok' | 'unavailable'
  method: 'forward_lagrangian_advection'
  reason?: string
  assumptions?: string[]
  path_geojson: {
    type: 'LineString'
    coordinates: [number, number][]
  } | null
  uncertainty_corridor_geojson: {
    type: 'Polygon'
    coordinates: [number, number][][]
  } | null
  steps: PollutionPathStep[]
}
```

将以下字段加入前端的 `MeteorologyResult`：

```ts
wind_direction_forecast: WindDirectionForecast
pollution_path_prediction: PollutionPathPrediction
```

### 地图渲染规则

```ts
const prediction = meteorology.pollution_path_prediction

if (prediction.status === 'ok') {
  // path_geojson：预测中心路径，建议实线绘制
  // uncertainty_corridor_geojson：预测不确定性走廊，建议半透明填充
} else {
  // 展示：弱风、观测不足或风向不稳定，暂无可靠预测轨迹
}
```

> 必须用 `wind_to_direction` 绘制污染移动方向；`wind_from_direction` 仅用于表示风从哪里来。

当风速低于 `0.5 m/s`、有效风场记录不足或风向过于离散时，路径返回 `status: "unavailable"`，`path_geojson` 和 `uncertainty_corridor_geojson` 为 `null`。前端不得生成默认箭头或默认路径。

## 3. 空间传播分析

### `POST /api/spatial/analyze`

### 请求体

```json
{
  "task": {
    "task_id": "HK_MK_20260913_0800",
    "station_id": "MK",
    "lat": 22.3227,
    "lon": 114.1683
  },
  "meteorology_result": {
    "result": {
      "dominant_wind_direction": 135,
      "mean_wind_speed": 2.1,
      "pollution_path_prediction": {}
    }
  },
  "radius_km": 3
}
```

| 字段 | 必填 | 说明 |
|------|------|------|
| `task` | 是 | 站点任务；也支持直接平铺在请求根对象 |
| `meteorology_result` | 否 | 直接传入气象 Agent 的完整 `result` 或 Agent 响应 |
| `radius_km` | 否 | 空间分析半径，范围 1–3 km，默认 3 km |

### 关键响应字段

```ts
interface SpatialResult {
  upwind_roads: UpwindRoad[]
  affected_area_geojson: PolygonGeometry
  sensitive_pois: SensitivePOI[]
  sensitive_poi_count: number
  wind_direction: number | null
  wind_speed: number | null
  pollution_path_prediction?: PollutionPathPrediction
}
```

- `affected_area_geojson`：传统的下风向影响扇区；当风向缺失或弱风时为圆形缓冲区。
- `pollution_path_prediction`：若气象请求中传入该字段，空间接口会原样透传。它是逐小时预测路径，应与静态扇区并列绘制，不应相互替代。

## 4. 原始逐小时气象数据

### `GET /api/meteorology/hourly`

示例：

```text
/api/meteorology/hourly?lat=22.3227&lon=114.1683&station_id=MK&start_time=2026-09-13%2008:00:00&end_time=2026-09-13%2011:00:00
```

此接口用于绘制风速、湿度、降雨、温度与风向趋势。风险等级及污染路径结论应使用 `POST /api/meteorology/analyze` 的正式结果。

## 5. 前端联调配置

前端 Vite 默认端口是 `5173`，后端 Flask 端口是 `8000`。这是跨源请求，建议开发环境配置 Vite 代理：

```ts
// frontend/vite.config.ts
server: {
  port: 5173,
  host: true,
  proxy: {
    '/api': {
      target: 'http://127.0.0.1:8000',
      changeOrigin: true
    }
  }
}
```

配置代理后，前端环境变量可设为：

```text
VITE_API_BASE_URL=
```

请求路径直接使用：

```ts
'/api/meteorology/analyze'
'/api/spatial/analyze'
```

## 6. 接入顺序建议

1. 调用 `/api/meteorology/analyze`，保存 `response.result`。
2. 从 `response.result.result` 读取气象评分、逐小时数据与预测路径。
3. 将该 Agent 响应作为 `meteorology_result` 传入 `/api/spatial/analyze`。
4. 在地图上同时绘制静态影响扇区、上风向道路、敏感 POI、预测中心线和不确定性走廊。
