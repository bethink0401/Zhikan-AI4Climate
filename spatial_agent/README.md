# Spatial Agent（空间匹配智能体）

负责人：陈维阳。本模块实现站点—道路—风向—敏感 POI 空间匹配，并依照项目分工表返回统一 Agent JSON。

## 已完成

- 整理 Mong Kok、Causeway Bay、Central 三个路边站坐标。
- 从 iGeoCom POI 中清洗学校、医疗、铁路/地铁、社区中心与社区服务设施；原始 CSV 不会被修改。
- 将香港 1980 方格网 EPSG:2326 转换为 WGS84。
- 筛选站点 1–3 km 内道路，按气象学“风来向”匹配上风向道路。
- 生成下风向扇形影响区，并统计区内敏感 POI。风缺失或风速低于 0.5 m/s 时自动降级为圆形缓冲区。
- 提供 Python 函数调用、Flask HTTP 接口、GeoJSON/CSV 产物和 SQLite 建库脚本。

## 数据来源

- 道路：用户提供的香港运输署 IRNP `RdNet_IRNP.gdb`；数据集说明见 <https://data.gov.hk/en-data/dataset/hk-td-tis_15-road-network-v2>。
- POI：用户提供的 iGeoCom CSV；官方数据集说明见 <https://data.gov.hk/en-data/dataset/hk-landsd-openmap-development-hkms-digital-geocom>。
- 空气质路边站名单与地址：香港环保署 AQHI 监测网络，<https://www.aqhi.gov.hk/en/monitoring-network/air-quality-monitoring-stations.html?mid=17>。

处理结果：原始 37,356 条 POI 中提取并去重得到 5,830 个敏感设施；GDB `CENTERLINE` 共 36,395 个道路要素，保留三个站点 4 km 预处理范围内 12,833 个道路线段。实际请求仍会按 1–3 km 参数二次筛选。

## RdNet_IRNP.gdb 说明

`task/data/RdNet_IRNP.gdb` 是 Esri File Geodatabase，坐标系为 EPSG:2326。它不仅是普通道路线，还包含 `CENTERLINE`、`SPEED_LIMIT`、`VEHICLE_RESTRICTION`、`PROHIBITION`、`BUS_ONLY_LANE`、`TURN` 等图层。本阶段空间 Agent 使用 `CENTERLINE`；限速、禁行和公交专用道可在 Decision Agent 联调时继续接入。

若电脑已安装 QGIS/ArcGIS/GDAL，可直接从 GDB 导出 `CENTERLINE` 为 GML。为避免把数百 MB 原始文件纳入仓库，代码仓仅保留站点周边处理后 GeoJSON。

## 数据处理

```bash
python -m spatial_agent.data_process.clean_poi
python -m spatial_agent.data_process.import_gdb_roads
python -m spatial_agent.data_process.build_database
```

POI 坐标转换需要 `pyproj`；GDB 导入额外需要 `pyogrio` 和 `shapely`。运行时空间计算本身不需要 GIS 桌面软件。`import_roads.py` 仍保留为 GML 备用导入器。

## Python 调用

```python
from spatial_agent import SpatialAgent

result = SpatialAgent().run(
    {
        "task_id": "HK_MK_2023_0800",
        "station_name": "Mong Kok Roadside",
        "station_id": "MK",
        "start_time": "2023-08-15 08:00:00",
        "end_time": "2023-08-15 11:00:00",
    },
    {
        "agent_name": "MeteorologyAgent",
        "status": "success",
        "result": {"wind_direction": 45, "wind_speed": 3.2},
    },
    radius_km=3,
)
```

也可以独立运行 Agent 并保存标准 JSON：

```bash
python -m spatial_agent.agents.spatial_agent \
  --input spatial_agent/examples/request.json \
  --output spatial_agent/examples/output.json
```

`wind_direction` 单位为度，0° 为北、90° 为东，表示风从哪里吹来；`wind_speed` 单位为 m/s。`radius_km` 只允许 1–3。

## HTTP 接口

```bash
python -m spatial_agent.api
curl -X POST http://127.0.0.1:5052/api/spatial/analyze \
  -H 'Content-Type: application/json' \
  --data @spatial_agent/examples/request.json
```

- `GET /api/spatial/health`：健康检查。
- `POST /api/spatial/analyze`：请求体参见 `examples/request.json`。
- 返回包含 `agent_name`、`status`、`result`、`evidence`、`confidence`、`data_quality`和 `created_at`。

## 关键输出字段

- `upwind_roads`：上风向道路候选及距离、方位、对齐度。
- `affected_area_geojson`：可直接给前端地图展示的 Polygon。
- `sensitive_pois`：影响区内敏感点列表。
- `spatial_confidence`：`high` / `medium` / `low`。
- `status=warning`：输入缺失时仍返回可解释的降级结果，不直接崩溃。

## 测试

```bash
python -m pytest spatial_agent/tests -q
```

## 独立 MySQL 数据库

MySQL 导入脚本会创建全新的数据库，避免与旧业务数据混合。密码只在终端中输入，不写入代码：

```bash
python -m spatial_agent.data_process.import_mysql \
  --host YOUR_HOST --port 3306 --user YOUR_USER \
  --database spatial_agent_20260723
```

新库包含 `station`、`road_network`、`poi_sensitive` 和 `data_import_log` 四张表。
