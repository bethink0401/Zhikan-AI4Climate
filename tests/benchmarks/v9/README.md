# V9：独立泛化与用户价值验证

预注册协议见 `protocol.json`（Git SHA、数据哈希、冻结时间、泄漏审计）。
执行工作单来自用户提供的《智瞰_V9_实验实施指令.md》；人工盲评与用户研究
在无参与者时一律标 PENDING，不伪造任何标签或胜率。

## 一键复现

```bash
# 0. P0：V8 离线/在线 57/60 差异定位（产出 60/60 一致性证明）
/usr/bin/python3 tests/benchmarks/v9/replay_v8.py

# 1. 实验一：创新一 2024 留出年 A/B/B+/C 对比（含 B+ 强化规则基线、弃判曲线）
/usr/bin/python3 tests/benchmarks/v9/governance_holdout.py

# 2. 实验二：创新二 留出站点观测回测（2024，历史气象驱动，非预报）
/usr/bin/python3 tests/benchmarks/v9/propagation_external.py

# 3. 实验三：出行推荐材料导出（用户盲评已按 V9.2 原则取消，材料保留）
/usr/bin/python3 tests/benchmarks/v9/travel_user_study.py

# ── V9.2：纯自动化验证 ────────────────────────────
/usr/bin/python3 tests/benchmarks/v9/travel_multiopt.py       # 创新三：四臂多目标基准（HV/约束/稳定性）
/usr/bin/python3 tests/benchmarks/v9/propagation_ablation.py  # 创新二：双尺度消融 + 参数扫描
/usr/bin/python3 tests/benchmarks/v9/governance_pit.py        # 创新一：PIT 前向回测（滚动 12 个月基线）

# ── V9.6–V9.7：跨域扫描与配置审计 ──────────────────
/usr/bin/python3 tests/benchmarks/v9/meteo_cache.py            # 气象缓存自检（与联网原始帧逐值一致）
/usr/bin/python3 tests/benchmarks/v9/governance_capture.py     # 真实 254 窗原始组件捕获
/usr/bin/python3 tests/benchmarks/v9/governance_sweep.py       # 跨域判据扫描
/usr/bin/python3 tests/benchmarks/v9/governance_config_audit.py # 六套命名配置逐例复算 + 显式 CI
/usr/bin/python3 tests/benchmarks/v9/audit_input_provenance.py # V9.5/V9.7 差值溯源

# ── V9.9：统计审计（不改算法，只查报告与数据谱系）──────
/usr/bin/python3 tests/benchmarks/v9/audit_test_inventory.py       # 测试计数（直接驱动 pytest）
/usr/bin/python3 tests/benchmarks/v9/audit_report_claims.py --strict  # 全文『差值 + CI』断言可追溯性
/usr/bin/python3 tests/benchmarks/v9/audit_blind_year_lineage.py   # 盲测年数据谱系（字面/查询/可达/产物四层）
/usr/bin/python3 tests/benchmarks/v9/make_report_tables.py         # 由 JSON 重写报告全部数值块
/usr/bin/python3 tests/benchmarks/v9/run_manifest.py               # 代码+配置+数据+气象缓存 哈希清单

# ── 盲测（一次性；必须先把取数做完）────────────────
/usr/bin/python3 tests/benchmarks/v9/prefetch_blind_year_parallel.py 2021 5
/usr/bin/python3 tests/benchmarks/v9/governance_holdout.py 2021   # 产物 *_blind2021，不覆盖开发年
```

报告一致性由 `tests/test_v97_report_consistency.py` 与
`tests/test_v99_report_consistency.py` 双重把关：数值块重生成比对、断言可追溯性、
附录不得手写数字、测试分类之和等于总数。

依赖：`/usr/bin/python3`（3.9，numpy/sqlite3/requests 已装）；风数据首次运行
会请求 Open-Meteo Archive（之后走 `results/wind_openmeteo_*.json` 缓存）。

## 目录

| 文件 | 作用 |
|---|---|
| `protocol.json` | 预注册：版本/哈希/盲测样本分配/评价规则 |
| `data_manifest.json` | 数据来源与泄漏审计（2024 为真正未见年） |
| `metrics.py` | 聚类 bootstrap、覆盖率—风险曲线、混淆矩阵 |
| `replay_v8.py` | P0：离线/在线一致性回放（60/60 验收） |
| `governance_holdout.py` | 实验一主脚本（可传年份；非 2024 即一次性盲测） |
| `propagation_external.py` | 实验二主脚本 |
| `travel_user_study.py` | 实验三材料导出（研究本身 PENDING） |
| `audit_report_claims.py` | V9.9：全文『差值 + CI』断言的可追溯性审计 |
| `audit_test_inventory.py` | V9.9：驱动 pytest 导出测试计数（报告不再手写） |
| `audit_blind_year_lineage.py` | V9.9：盲测年数据谱系四层审计 |
| `run_manifest.py` | 代码 + 配置 + 数据 + 气象缓存的不可变哈希清单 |
| `results/` | 全部 JSON/CSV/PNG 与盲评模板 |

## 结果速览（实际运行数据）

- **P0**：V8 消融 3/60 假性不一致根因 = 评测器口径缺陷（离线带暴露上限、
  在线未传约束）；对齐口径后 60/60，回归测试 `tests/test_v9_offline_online_consistency.py`
  （31 例）。
- **实验一 / 实验二**：数值见 `results/` 与 `docs/对比实验结果汇总(V9).md`。
- **实验三**：仅推荐材料，用户盲评 PENDING。
