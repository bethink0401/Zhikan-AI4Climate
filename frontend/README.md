# 空气污染智能预警与区域协同治理平台 - 前端

## 项目说明

这是"空气污染智能预警与区域协同治理平台"的前端工程骨架（第一阶段）。

**当前状态**：工程骨架已建立，待安装依赖后可运行

## 技术栈

- **框架**: Vue 3 + TypeScript
- **构建工具**: Vite
- **路由**: Vue Router
- **状态管理**: Pinia
- **UI 组件库**: Element Plus
- **HTTP 客户端**: Axios
- **图表库**: ECharts 5
- **地图库**: 腾讯地图 GL JS（需在 `.env.local` 配置 `VITE_TMAP_KEY`，见下）
- **空间计算**: @turf/turf
- **日期处理**: dayjs

## 安装依赖

```bash
cd frontend
npm install
```

## 配置地图密钥（必须）

地图底图使用腾讯地图 GL JS，需要你自己的密钥，否则地图区域会显示一段配置引导而不是底图。

1. 打开 https://lbs.qq.com/dev/console/application/mine 登录
2. 「应用管理」→「我的应用」→ 创建应用 → 添加 Key
   （Javascript API GL 不需要勾选任何产品，直接创建即可）
3. 把 `frontend/.env.local.example` 复制成 `frontend/.env.local`，填入 Key：

   ```
   VITE_TMAP_KEY=你的Key
   ```

`.env.local` 已被 `.gitignore` 的 `*.local` 忽略，不会提交到版本库。

> 前端明文密钥有被嗅探的风险：个人使用建议在控制台给该 Key 配置 Referer 域名白名单；
> 商用则应由后端代理鉴权，不要把 Key 直接放在前端。

## 运行项目

```bash
# 开发模式
npm run dev

# 构建生产版本
npm run build

# 预览生产构建
npm run preview
```

## 项目结构

```
frontend/
├── src/
│   ├── api/              # API 请求层
│   │   ├── request.ts    # Axios 实例
│   │   └── analysis.ts   # 分析接口
│   │
│   ├── assets/           # 静态资源
│   │
│   ├── components/       # 业务组件
│   │   ├── global/       # 全局组件
│   │   ├── overview/     # 综合态势组件
│   │   ├── restriction/  # 污染溯源组件
│   │   ├── meteorology/  # 气象组件
│   │   ├── traffic/      # 交通组件
│   │   ├── source/       # 溯源组件
│   │   ├── spatial/      # 空间组件
│   │   └── report/       # 报告组件
│   │
│   ├── layouts/          # 布局组件
│   │   └── MainLayout.vue
│   │
│   ├── mock/             # Mock 数据
│   │
│   ├── router/           # 路由配置
│   │   └── index.ts
│   │
│   ├── stores/           # Pinia 状态管理
│   │   └── analysis.ts   # 分析状态
│   │
│   ├── styles/           # 全局样式
│   │   ├── reset.css     # 重置样式
│   │   ├── variables.css # 设计变量
│   │   └── global.css    # 全局样式
│   │
│   ├── types/            # TypeScript 类型定义
│   │   ├── analysis.ts   # 分析类型
│   │   ├── airQuality.ts # 空气质量类型
│   │   ├── meteorology.ts # 气象类型
│   │   ├── traffic.ts    # 交通类型
│   │   ├── spatial.ts    # 空间类型
│   │   ├── source.ts     # 溯源类型
│   │   ├── decision.ts   # 决策类型
│   │   └── report.ts     # 报告类型
│   │
│   ├── utils/            # 工具函数
│   │   ├── riskMapper.ts    # 风险等级映射
│   │   ├── vehicleMapper.ts # 车型映射
│   │   ├── agentMapper.ts   # Agent 名称映射
│   │   ├── mapLayers.ts     # 地图图层工具
│   │   └── format.ts        # 格式化工具
│   │
│   ├── views/            # 页面视图
│   │   ├── OverviewView.vue              # 综合态势
│   │   ├── RestrictionWarningView.vue    # 污染溯源与区域交通治理
│   │   └── WeatherPropagationView.vue    # 恶劣天气与公里级传播预警
│   │
│   ├── App.vue           # 根组件
│   └── main.ts           # 入口文件
│
├── .env.development      # 开发环境配置
├── .env.production       # 生产环境配置
├── index.html            # HTML 模板
├── package.json          # 项目配置
├── tsconfig.json         # TypeScript 配置
├── tsconfig.node.json    # Node TypeScript 配置
└── vite.config.ts        # Vite 配置
```

## 路由结构

系统包含 3 个主要业务页面：

- `/overview` - 综合态势驾驶舱
- `/restriction` - 污染溯源与区域交通治理
- `/weather` - 恶劣天气与公里级传播预警

默认路由 `/` 自动重定向到 `/overview`

## 状态管理

使用统一的 `analysisStore` 管理 Coordinator 分析任务和结果，不为每个 Agent 单独创建 store。

## API 设计

所有业务页面通过 `src/api/analysis.ts` 访问 Coordinator，不直接调用单个 Agent 接口。

## 开发说明

### 第一阶段（当前）

- ✅ 建立工程骨架
- ✅ 配置基础依赖
- ✅ 建立路由和布局
- ✅ 建立类型系统
- ✅ 建立工具函数
- ⏳ 待安装依赖

### 第二阶段（后续）

- 实现具体业务组件
- 实现 ECharts 图表
- 接入真实后端接口
- 实现 Report Drawer
- 实现 Agent Status Drawer

## 环境变量

### 开发环境（.env.development）

```
VITE_API_BASE_URL=http://127.0.0.1:8000
```

### 生产环境（.env.production）

```
VITE_API_BASE_URL=/api
```

## 注意事项

1. **不要修改后端代码**：前端与后端完全隔离
2. **统一风险颜色**：使用 CSS 变量 `--risk-low/medium/high/very-high`
3. **统一 API 调用**：通过 `analysis.ts` 调用 Coordinator
4. **类型安全**：避免使用 `any`，使用明确类型或 `unknown` + TODO
5. **路径别名**：使用 `@/` 引用 `src/` 目录

## 联系方式

如有问题，请联系项目负责人。
