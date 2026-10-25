# 智瞰 · AI4Climate —— 多智能体气象环境决策平台

面向香港地区的空气质量与气象风险决策系统：以多智能体协作的方式，把**污染溯源、区域交通治理、公里级传播预警、出行建议与路线推荐**串成一条可解释的证据链，在综合态势地图上统一呈现。

本仓库为该系统的可运行版本（代码 / 配置 / 数据库 / 示例数据），完整启动步骤见 [`运行说明.txt`](./运行说明.txt)。

## 功能页面

| 路由 | 模块 | 说明 |
| --- | --- | --- |
| `/overview` | 综合态势总览 | 站点空气质量、气象场与风险等级统一看板 |
| `/restriction` | 污染溯源与区域交通治理 | 多源证据链溯源 + 交通管控建议 |
| `/weather` | 恶劣天气与公里级传播预警 | 污染物扩散场推演与影响范围评估 |
| `/travel` | 出行建议与路线推荐 | 按暴露风险对备选路线排序推荐 |

## 技术栈

- **后端**：FastAPI（业务后端 `:8000`）+ 独立 AI 服务（`:8010`）
- **前端**：Vue 3 + TypeScript + Vite（`:5173`），腾讯地图 GL JS 底图
- **数据**：香港环保署空气质量历史库（2018–2024，约 106 万条）、车辆构成交通流、POI 敏感点、路网 GeoJSON
- **空间计算**：pyproj（EPSG:2326 / WGS84 / GCJ-02）、shapely、pyogrio
- **智能体**：多智能体编排 + 知识库检索（chromadb / sentence-transformers）

## 快速开始

```bash
# 1. 依赖
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd frontend && npm ci && cd ..

# 2. 密钥
cp .env.example .env                       # 填入 COORDINATOR_API_KEY
cp frontend/.env.local.example frontend/.env.local   # 填入 VITE_TMAP_KEY

# 3. 三个终端（均在项目根目录）
set -a; source .env; set +a && python -m api.app          # 终端一：8000
set -a; source .env; set +a && python -m ai_service.app   # 终端二：8010

cd frontend && npm run dev -- --port 5173 --strictPort    # 终端三：5173
```

浏览器打开 <http://127.0.0.1:5173/overview>。首次分析需预热气象字段，约 25–35 秒属正常。

## 数据文件（Git LFS）

`data/air_quality.db`（约 211MB）与 `data/traffic.db`（约 42MB）通过 **Git LFS** 托管，克隆后需拉取实体文件：

```bash
git lfs install
git clone https://github.com/bethink0401/Zhikan-AI4Climate.git
cd Zhikan-AI4Climate && git lfs pull
ls -lh data/air_quality.db data/traffic.db   # 应显示实际大小，而非几百字节指针文件
```

## 目录结构

```
agents/         多智能体编排与角色实现
ai_service/     AI 服务（:8010），大模型调用与知识库检索
api/            业务后端（:8000），站点/污染/交通/出行接口
config/         阈值与权重配置（JSON，可调不改代码）
data/           数据库与示例数据（*.db 走 LFS）
data_process/   数据清洗与特征加工
examples/       示例行程与案例输出
frontend/       Vue 3 前端
spatial_agent/  空间分析（路网、POI、坐标转换）
tools/          运维与校验脚本
```

## 安全说明

仓库为公开仓库，**不包含任何真实密钥**：

- `.env`、`frontend/.env.local` 已被 `.gitignore` 排除，仅提交 `.env.example` / `.env.local.example` 模板
- 运行前请自行申请 DashScope API Key 与腾讯位置服务 Key，填入本地文件后启动
