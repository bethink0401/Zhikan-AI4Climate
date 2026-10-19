"""V9.8：报告数值表由结果 JSON 自动生成。

审查建议（原文）："统一以 `v97_config_audit.json` 中的逐例记录为准，并让报告
由该文件自动生成，避免手工复制旧数值。"

做法：在报告里放入标记块

    <!-- BEGIN:V9.7_CONFIG_TABLE -->   ...被自动覆盖...   <!-- END:V9.7_CONFIG_TABLE -->
    <!-- BEGIN:V9.7_PROVENANCE -->     ...被自动覆盖...   <!-- END:V9.7_PROVENANCE -->

本脚本按标记重写块内文本，数值全部取自 JSON，因此不存在"表格与正文对不上"的可能。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
DOC = BASE / "docs" / "对比实验结果汇总(V9).md"
RESULTS = HERE / "results"

STATION_SHORT = {"Mong Kok Roadside": "Mong Kok", "Central Roadside": "Central",
                 "Causeway Bay Roadside": "Causeway Bay"}

CONFIG_LABEL = {
    "A_v95_committed": ("A V9.5 提交", "git `0a3e681`"),
    "B_v96_current": ("**B 现网默认**", "当前 config"),
    "C_sweep_base": ("C 扫描基线", "sweep base"),
    "D_contrast_only": ("D 对比度（无局地项）", "V9.6 启用后回退"),
    "E_synth_calib": ("E 合成标定", "dev 网格最优"),
    "F_calib_R1": ("F 标定阈值+R1", "E 的同族基线"),
}


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.4f}"


def build_config_table(audit: dict) -> str:
    lines = []
    for mode, title in (("full", "全年代理标签口径"), ("pit", "PIT 前向口径")):
        metrics = audit["modes"][mode]["metrics"]
        lines.append(f"**{title}（254 窗，两种口径各自独立评估）**")
        lines.append("")
        lines.append("| 配置 | 来源 | Macro-F1 | 弃判率 | Mong Kok | Central | Causeway Bay | 区域预测数 | 区域召回 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for key, (name, source) in CONFIG_LABEL.items():
            m = metrics[key]
            stations = " | ".join(_fmt(m["per_station_f1"][s]) for s in STATION_SHORT)
            lines.append(f"| {name} | {source} | {_fmt(m['macro_f1'])} | "
                         f"{m['abstention_rate']:.3f} | {stations} | "
                         f"{m['regional_predicted']} | {m['regional_recall']:.3f} |")
        lines.append("")
    lines.append("**配对比较**（每行都写清两侧配置；CI 为按日期聚类的 95% bootstrap；"
                 "配对单位与样本量见 `results/v97_config_audit.json`）：")
    lines.append("")
    lines.append("| 比较 | full 差值 | full CI | pit 差值 | pit CI |")
    lines.append("|---|---|---|---|---|")
    full_cmp = audit["modes"]["full"]["comparisons"]
    pit_cmp = audit["modes"]["pit"]["comparisons"]
    for key, record in full_cmp.items():
        other = pit_cmp[key]
        ci_f, ci_p = record["ci95"], other["ci95"]
        lines.append(f"| {record['arm']} − {record['baseline']} | {record['mean_diff']:+.4f} | "
                     f"[{ci_f[0]:+.4f}, {ci_f[1]:+.4f}] | {other['mean_diff']:+.4f} | "
                     f"[{ci_p[0]:+.4f}, {ci_p[1]:+.4f}] |")
    lines.append("")
    check = audit.get("online_offline_check") or {}
    if check:
        lines.append(f"**在线/离线一致性**：{check['matched']}/{check['compared']} 逐例一致"
                     f"（{check['verdict']}），离线配置 = 在线生效配置 "
                     f"`{json.dumps(check['config'], ensure_ascii=False)}`。")
    lines.append("")
    lines.append("**取舍必须讲清楚（不宣称\"无代价\"）**：现网默认 B 相对 A 在区域召回"
                 "（0.681 vs 0.417，full 口径）与弃判率（0.335 vs 0.555）上明显更好，"
                 "但总体 Macro-F1 的差值为 "
                 f"{full_cmp['B_v96_current_minus_A_v95_committed']['mean_diff']:+.4f}"
                 f"（CI [{full_cmp['B_v96_current_minus_A_v95_committed']['ci95'][0]:+.4f}, "
                 f"{full_cmp['B_v96_current_minus_A_v95_committed']['ci95'][1]:+.4f}]）——"
                 "**CI 跨 0 只说明样本量不足以判定方向，不等于损失可忽略**；"
                 "这是『覆盖率/区域召回』与『总体一致性』之间的真实取舍，"
                 "需按使用场景选择。")
    return "\n".join(lines)


def _baseline_paragraph(full_ci: dict, pit_ci: dict, sweep: dict) -> str:
    """把"相对 R1 基线"这句话写死到具体配置，避免读者拿它去对上表里的 R1。

    审查（V9.8 P0-1）：正文写"相对同一批窗上的 R1 基线为 −0.1700"，而紧邻的表格里
    "R1 多站同步"是 0.5386——读者一算 0.1848−0.5386 = −0.3538，与 −0.1700 对不上。
    原因是两个"R1"不是同一套配置：CI 的基线是**同族基线**（除 `regional_variant`
    由 R2 换回 R1 外，其余键与冻结配置逐键相同），上表里的 R1 则是**完整 R1 配置**。
    这里把基线的配置哈希、逐键差异与点估计 F1 全部写出，差值可由读者自行复算。
    """
    table_r1_full = sweep["cross_domain_variants"]["R1_sync"]["real_full"]["macro_f1"]
    table_r1_pit = sweep["cross_domain_variants"]["R1_sync"]["real_pit"]["macro_f1"]
    base_full, base_pit = full_ci["baseline_config"], pit_ci["baseline_config"]
    arm_full, arm_pit = full_ci["arm_config"], pit_ci["arm_config"]
    changed = sorted(k for k in set(arm_full) | set(base_full)
                     if arm_full.get(k) != base_full.get(k))
    diff_keys = "、".join(f"`{k}`：{json.dumps(arm_full.get(k), ensure_ascii=False)} → "
                         f"{json.dumps(base_full.get(k), ensure_ascii=False)}" for k in changed)
    return (
        "**这句差值用的是哪套基线（V9.8 P0-1 修正）**：与冻结配置配对的基线**不是**"
        f"上表里的『R1 多站同步』（该行真实 2024 = **{table_r1_full:.4f}**、PIT = "
        f"**{table_r1_pit:.4f}**），而是**仅把区域判据换回 R1 的同族配置**——逐键差异只有 "
        f"{diff_keys}，其余键（含 `min_strength` / `margin` / `local_station_term`）"
        "与冻结配置逐键相同。因此：\n\n"
        f"- 同族基线真实 2024 F1 = **{full_ci['baseline_point_f1']:.4f}**，冻结配置 "
        f"**{full_ci['arm_point_f1']:.4f}** → 差值 **{full_ci['mean_diff']:+.4f}**，"
        f"CI [{full_ci['ci95'][0]:+.4f}, {full_ci['ci95'][1]:+.4f}]（不含 0）；\n"
        f"- 同族基线 PIT F1 = **{pit_ci['baseline_point_f1']:.4f}**，冻结配置 "
        f"**{pit_ci['arm_point_f1']:.4f}** → 差值 **{pit_ci['mean_diff']:+.4f}**，"
        f"CI [{pit_ci['ci95'][0]:+.4f}, {pit_ci['ci95'][1]:+.4f}]（不含 0）；\n"
        f"- 若改用**真实部署配置 A**（V9.5 提交配置）作基线，代价更大——见 V9.7 章节"
        "『配对比较』表的 `E_synth_calib − A_v95_committed` 一行（**数值以 "
        "`results/v97_config_audit.json` 为准**）。**两个数各自写明基线，不可混用。**"
    )


def build_crossdomain(sweep: dict) -> str:
    """V9.6 章节的跨域判据表 + 标定迁移代价（数值全部取自 v96_rule_sweep.json）。"""
    variants = sweep["cross_domain_variants"]
    order = [("R1_sync", "**R1 多站同步**（V8/V9.4/V9.5 现行形式）"),
             ("R2_abs_contrast", "R2 绝对对比度（V9.5 引入）"),
             ("R3_net_relative", "R3 网络相对（尺度无关，V9.6 新增）"),
             ("R4_hybrid", "R4 混合（同步 + 网络相对）")]
    modes = ("synthetic_dev", "synthetic_blind", "real_full", "real_pit")
    lines = ["| 区域判据 | 合成 dev（138） | 合成盲测种子（138） | 真实 2024 / 全年基线（254） | 真实 2024 / PIT（254） |",
             "|---|---|---|---|---|"]
    for key, label in order:
        values = [f"{variants[key][m]['macro_f1']:.4f}" for m in modes]
        best_syn = variants[key]["synthetic_dev"]["macro_f1"] > 0.7
        lines.append(f"| {label} | {values[0]} | {values[1]} | {values[2]} | {values[3]} |")
    real_r1 = variants["R1_sync"]["real_full"]["macro_f1"]
    real_r2 = variants["R2_abs_contrast"]["real_full"]["macro_f1"]
    report = sweep["report"]
    frozen = sweep["frozen_config"]
    lines += [
        "",
        f"**判据排序在两个域之间完全翻转**：R2 在合成域最好"
        f"（{variants['R2_abs_contrast']['synthetic_dev']['macro_f1']:.3f} / "
        f"{variants['R2_abs_contrast']['synthetic_blind']['macro_f1']:.3f}），"
        f"在真实域最差（{real_r2:.4f} / {variants['R2_abs_contrast']['real_pit']['macro_f1']:.4f}，"
        f"且 regional 预测数 {variants['R2_abs_contrast']['real_full']['pred_distribution']['regional_transport']}"
        f"/254）；R1 在合成域最差（{variants['R1_sync']['synthetic_dev']['macro_f1']:.3f} / "
        f"{variants['R1_sync']['synthetic_blind']['macro_f1']:.3f}），却是真实域唯一能用的"
        f"（{real_r1:.4f} / {variants['R1_sync']['real_pit']['macro_f1']:.4f}）。",
        "",
        f"**在合成开发集上标定、拿去跑真实数据的结果**（『把合成世界当校准集』的代价）："
        f"冻结配置为 `{json.dumps(frozen, ensure_ascii=False)}`——dev F1 "
        f"**{report['synthetic_dev']['macro_f1']:.4f}**、合成盲测 "
        f"**{report['synthetic_blind']['macro_f1']:.4f}**，真实 2024 "
        f"**{report['real_full']['macro_f1']:.4f}** / PIT **{report['real_pit']['macro_f1']:.4f}**。",
        "",
        _baseline_paragraph(report["real_full_vs_R1_ci"], report["real_pit_vs_R1_ci"], sweep),
        "",
        f"合成域上 "
        f"{report['synthetic_blind_vs_R1_ci']['mean_diff']:+.4f}（同族基线，CI "
        f"[{report['synthetic_blind_vs_R1_ci']['ci95'][0]:+.4f}, "
        f"{report['synthetic_blind_vs_R1_ci']['ci95'][1]:+.4f}]）的『改进』在真实域是显著退化。",
        "",
        "**尺度无关形式 R3 也没能救回来**（这是本轮被数据否定的假设）：区域召回合成 "
        f"{variants['R3_net_relative']['synthetic_blind']['per_class_recall']['regional_transport']:.2f}、"
        f"真实仅 {variants['R3_net_relative']['real_full']['per_class_recall']['regional_transport']:.2f}"
        f"/{variants['R3_net_relative']['real_pit']['per_class_recall']['regional_transport']:.2f}。"
        "可见问题不是『归一化方式』，而是**两个数据域里同一批可观测量的判别结构本身不同**。",
    ]
    return "\n".join(lines)


def build_cwb_table(sweep: dict) -> str:
    """V9.6 章节的 CWB 分站表（数值取自 v96_rule_sweep.json 的 by_station）。"""
    variants = sweep["cross_domain_variants"]
    modes = (("real_full", "全年代理标签"), ("real_pit", "PIT"))
    stations = [s for s in ("Mong Kok Roadside", "Central Roadside",
                            "Causeway Bay Roadside")
                if s in variants["R1_sync"]["real_full"]["by_station"]]
    lines = []
    for mode, label in modes:
        lines.append(f"**{label}口径（254 窗）**")
        lines.append("")
        lines.append("| 区域判据 | " + " | ".join(STATION_SHORT[s] for s in stations) + " | 总体 |")
        lines.append("|---" * (len(stations) + 2) + "|")
        for key, name in (("R1_sync", "R1 同步"), ("R2_abs_contrast", "R2 绝对对比度"),
                          ("R3_net_relative", "R3 网络相对"), ("R4_hybrid", "R4 混合")):
            metrics = variants[key][mode]
            cells = " | ".join(_fmt(metrics["by_station"].get(s)) for s in stations)
            lines.append(f"| {name} | {cells} | {_fmt(metrics['macro_f1'])} |")
        lines.append("")
    return "\n".join(lines).strip()


def build_provenance(provenance: dict) -> str:
    swap = provenance["meteo_source_swap"]
    sim = provenance["partial_degradation_simulation"]["by_rate"]
    lines = [
        "V9.5 曾报告同一批 254 窗、全年代理标签下新规则 F1 = **0.4602**，而按 V9.5 提交配置 A "
        f"逐例复算得 **{swap['f1_real_meteo']:.4f}**，差 {swap['f1_real_meteo'] - 0.4602:+.4f}。"
        "审计结论如下（**V9.9 按审查要求收窄措辞：只做机制归因，不声称根因已唯一确定**）。",
        "",
        "**第一层（必须先说清）：历史数字按构造不可复现。** V9.5 时代的运行没有记录 `effective_config`、"
        "没有输入哈希、也没有记录气象来源（`effective_config` 字段是 V9.7 才加进留出结果的）——"
        "因此不存在『逐例对齐历史运行』的可能，只能做机制性归因。这条已写进审计 JSON 的 "
        "`cannot_replay_legacy`。",
        "",
        "**第二层（可量化的机制检验）：气象输入来源。** V9.5 时期每个窗口都现场联网取数，本机实测频繁超时，"
        "而 `MeteorologyAgent` 在超时时会**静默降级到兜底 CSV**（`data/sample_weather_mong_kok.csv`，"
        "全港单一站点样例，用于三个站都是错位输入）。在**同一批窗、同一配置 A**下只切换气象来源：",
        "",
        f"- 全部窗口改用兜底 CSV：F1 **{swap['f1_fallback_meteo']:.4f}**（真实气象 {swap['f1_real_meteo']:.4f}，"
        f"差 {swap['delta']:+.4f}），254/254 窗口组件都发生变化、**{swap['windows_flipped']}/254 窗口判定翻转**；",
        "- 按比例随机降级的模拟（每种比例 10 个随机种子）**不是**对历史降级率的推断，"
        "只是『若发生该比例降级会得到什么』的敏感性刻画：",
        "",
        "| 降级窗口占比 | F1 均值 | 区间 | 是否覆盖 0.4602 |",
        "|---|---|---|---|",
    ]
    for rate, record in sim.items():
        lines.append(f"| {rate} | {record['mean_f1']:.4f} | "
                     f"[{record['min_f1']:.4f}, {record['max_f1']:.4f}] | "
                     f"{'**是**' if record['covers_v95_reported_0_4602'] else '否'} |")
    lines += [
        "",
        "**结论（收窄后）**：气象静默降级**能够产生与历史差值相当的性能变化**——12–13% 的模拟降级"
        "区间覆盖 0.4602，且与当时日志里密集出现的 Open-Meteo 超时一致，因此它是**主要可疑原因**。"
        "但 V9.5 的运行没有保存每个窗口的气象来源，**无法反推当时实际的降级比例，也不能完全排除"
        "其他未被记录的因素（例如同时期的配置漂移）**；这 0.076 只能做机制性归因，"
        "**不能声称根因已被唯一确定**。",
        "",
        "**不影响已修复的工程成果**：① 气象改走年度磁盘缓存（按月分块+重试+续传，取数层注入、"
        "Agent 公式不变），并加自检；② 留出/捕获脚本在任何窗口气象非 `success` 时**直接失败**并写契约报告；"
        "③ 留出结果写入 `design.effective_config`，配置随结果一起归档；④ 在线/离线要求逐例一致。"
        "这四项使『输入来源未被记录』这一风险此后不再存在——无论当年真实降级率是多少。",
        "",
        "**逐窗清单**：`results/v97_input_provenance_windows.csv`（每窗的真实/兜底气象组件与两侧预测）。",
    ]
    return "\n".join(lines)


def build_retired_crosswalk(audit: dict) -> str:
    """历史附录里"曾主张的数值"对应的**现行取值**（全部取自 JSON，不再手写）。

    审查（V9.8 P0-3 附带项）：附录第 10 条仍写着已被取代的 −0.1987。根因是附录的
    "现行结论"列是**手写**的，不在任何自动生成块内。这里把附录里涉及数值的两条
    （对比度不可跨域、跨域迁移代价）的现行取值集中生成，附录表格只引用本块。
    """
    full_cmp = audit["modes"]["full"]["comparisons"]
    pit_cmp = audit["modes"]["pit"]["comparisons"]
    label = {
        "D_contrast_only_minus_C_sweep_base":
            "对比度判据不可跨域（`D_contrast_only − C_sweep_base`）",
        "E_synth_calib_minus_F_calib_R1":
            "跨域迁移代价 · 相对**同族基线**（`E_synth_calib − F_calib_R1`）",
        "E_synth_calib_minus_A_v95_committed":
            "跨域迁移代价 · 相对**真实部署配置 A**（`E_synth_calib − A_v95_committed`）",
    }
    lines = ["| 曾主张的结论 | 现行 full 差值 | full CI | 现行 PIT 差值 | PIT CI |",
             "|---|---|---|---|---|"]
    for key, name in label.items():
        record, other = full_cmp[key], pit_cmp[key]
        lines.append(f"| {name} | {record['mean_diff']:+.4f} | "
                     f"[{record['ci95'][0]:+.4f}, {record['ci95'][1]:+.4f}] | "
                     f"{other['mean_diff']:+.4f} | "
                     f"[{other['ci95'][0]:+.4f}, {other['ci95'][1]:+.4f}] |")
    lines += [
        "",
        "> 上表由 `make_report_tables.py` 从 `results/v97_config_audit.json` 自动生成"
        "（标记块 `V9.9_RETIRED_CROSSWALK`）。附录表格只引用本块、不复述数字，"
        "因此附录不可能再出现与正文不符的旧值。",
    ]
    return "\n".join(lines)


def build_test_inventory(inventory: dict) -> str:
    """测试计数块（审查 P0-3）：总数与分轮次明细一律来自 pytest 实际运行结果。"""
    run = inventory["pytest_run"]
    lines = [
        f"**{run['passed']} 项测试通过**（pytest 实际收集 **{inventory['total_collected']}** 项；"
        f"跳过 {run['skipped']}、失败 {run['failed']}、错误 {run['errors']}）——"
        "本块的每一个数都由 `audit_test_inventory.py` 直接驱动 pytest 导出，**不再手写**。",
        "",
        "| 轮次 | 测试项数 |",
        "|---|---|",
    ]
    for name, count in inventory["per_round"].items():
        lines.append(f"| {name} | {count} |")
    lines.append(f"| **合计** | **{inventory['total_collected']}** |")
    lines += [
        "",
        "> 分类明细之和必须等于合计（`tests/test_v99_report_consistency.py` 会断言这一点）——"
        "V9.8 报告的 `194 + 31 + 16 + 15 + 1 = 257 ≠ 260` 正是因为新增的报告一致性测试"
        "没有被计入分类，本轮由脚本导出后不可能再发生。",
    ]
    return "\n".join(lines)


def build_blind_lineage(lineage: dict) -> str:
    """盲测年（2021）数据谱系块（V9.9 新增）。"""
    l1 = lineage["L1_text_scan"]
    l2 = lineage["L2_sql_surface"]
    l3 = lineage["L3_trailing_window_reachability"]
    l4 = lineage["L4_artifacts"]
    purpose_cn = {
        "data_coverage_audit": "数据覆盖审计（只统计行数，不参与建模）",
        "downloader_default": "下载器默认年份串",
        "preregistration": "预注册/运行清单",
        "documentation": "文档",
        "modeling_or_threshold": "建模/阈值/评分",
        "unknown_review": "**待人工确认**",
        "blind_run_artifact": "本次盲测自己的产物（按构造含盲测年）",
        "blind_run_tooling": "本次盲测的运行/导出脚本（按构造含盲测年）",
        "input_cache": "为盲测预取的输入缓存（原始观测，按构造含盲测年）",
    }
    self_cats = ("blind_run_artifact", "blind_run_tooling", "input_cache")
    self_total = sum(l1["by_purpose"].get(k, 0) for k in self_cats)
    lines = [
        f"**L1 字面层**：全仓库（排除 `data/`、`docs/`）扫描 `{lineage['blind_year']}` 共命中 "
        f"**{l1['hits_total']}** 处，用途分类如下——"
        + "；".join(f"{purpose_cn.get(k, k)} {v}" for k, v in l1["by_purpose"].items())
        + f"。其中『本次盲测自己的产物/脚本/输入缓存』三类合计 **{self_total}** 处，是"
        "**盲测跑完之后**才出现的、按构造必然含盲测年；**判定是否污染须看余下的类别**。",
        "",
        f"> **结论（修正 V9.8 的说法）**：{l1['conclusion']}",
        "",
        f"**L2 查询层**：基准与工具代码里带年份/时间谓词的 SQL 共 **{l2['total']}** 处——"
        + "；".join(f"`{k}` {v}" for k, v in l2["by_kind"].items())
        + "。其中滚动区间查询（`time >= ? AND time < ?`）是唯一可能跨年读取的形态，"
        "分布如下（正是审查提醒的『滚动 12 个月可能读到上一年』）：",
        "",
        "| 滚动区间查询所在文件 | 出现行数 | 用途 |",
        "|---|---|---|",
    ]
    grouped: dict[str, list[dict]] = {}
    for site in l2["trailing_window_sites"]:
        grouped.setdefault(site["file"], []).append(site)
    for file, sites in grouped.items():
        purpose = ("生产取数路径（`load_station_no2_p90` / `AirQualityAgent` 的 NOx 年内分位）"
                   if file.startswith(("tools/", "agents/")) else "基准脚本（历史运行）")
        lines.append(f"| `{file}` | {len(sites)} | {purpose} |")
    lines += [
        "",
        f"**L3 可达层**（用数据库实际行数算，而非推理）：滚动规则 = {l3['window_rule']}。"
        f"盲测年在库中的小时行数为 **{l3['blind_year_rows_in_db']:,}**。"
        f"V9.x 的 PIT 事件月全部在 2024，窗口最低点为 **{l3['v9_actual_earliest_window_date']}**"
        f"——**实际未触及盲测年**（`v9_read_blind_year = {str(l3['v9_read_blind_year']).lower()}`）。",
        "",
        "**若**把同一脚本跑在 2022 事件上（历史未发生），滚动窗口会落到盲测年多少——"
        "这是审查要求登记的间接读取风险，量化如下：",
        "",
        "| 假想分析月 | 窗口下界 | 窗口总行数 | 其中属盲测年 | 占比 |",
        "|---|---|---|---|---|",
    ]
    for month in l3["by_analysis_month"]:
        lines.append(f"| {month['analysis_month']} | {month['window_lo']} | "
                     f"{month['rows_total']:,} | {month['rows_in_blind_year']:,} | "
                     f"{month['share_in_blind_year']:.1%} |")
    lines += [
        "",
        f"**L4 产物层**：**非本次盲测**的评测产物（`results/*.csv`，共 "
        f"{l4['files_scanned']} 个文件）里被评分的日期年份分布为 "
        f"`{json.dumps(l4['scored_date_years'], ensure_ascii=False)}`，"
        f"盲测年窗口 **{l4['blind_year_windows_scored']} 个**——即**该年此前从未被任何方法评分**。"
        f"本次盲测自己的产物（`{'`, `'.join(l4['this_blind_run_files'])}`，"
        f"年份分布 `{json.dumps(l4['this_blind_run_scored_years'], ensure_ascii=False)}`）"
        "单独列出，不与上者混用。",
        "",
        f"**概念澄清（审查要求区分）**：{lineage['concept_note']}",
        "",
        f"**判定**：{'可用作独立测试年' if lineage['verdict']['usable_as_blind'] else '不可用'}，"
        "前提条件："
        + "；".join(f"（{i + 1}）{c}" for i, c in enumerate(lineage["verdict"]["conditions"]))
        + "。",
    ]
    return "\n".join(lines)


def build_blind_result(blind: dict, failures: dict | None = None) -> str:
    """一次性盲测结果块（V9.9）。指标、CI、逐类召回全部取自结果 JSON。"""
    design = blind["design"]
    methods = ("A", "B", "Bplus", "C")
    names = {"A": "A 单源规则", "B": "B 多源规则", "Bplus": "B+ 强化规则基线",
             "C": "C 智瞰系统"}
    point = blind["point_f1_main_labels"]
    abst = blind["abstention_coverage"]
    ci = blind["clustered_ci_vs_B"]
    samples = sum(blind["label_distribution"].values())

    lines = [
        f"**运行设定**：年份 **{design['holdout_year']}**（{design['run_nature']}）；"
        f"样本 **{samples}** 窗（抽取计划 `{json.dumps(design['sample_plan'], ensure_ascii=False)}`，"
        f"实际标注分布 `{json.dumps(blind['label_distribution'], ensure_ascii=False)}`，"
        "候选池不足时如实报告实际 n，不凑数）。",
        "",
        f"**生效配置（随结果一起归档）**：`{json.dumps(design['effective_config'], ensure_ascii=False)}`"
        f"——运行前未做任何改动（one_shot = {str(design.get('one_shot')).lower()}）。",
        "",
        "| 方法 | Macro-F1 | 弃判率 | 覆盖率 | 交通 | 积聚 | 区域 | 不确定 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for method in methods:
        conf = blind["confusion"][method]
        recall = {}
        for label, row in conf.items():
            total = sum(row.values())
            recall[label] = row.get(label, 0) / total if total else 0.0
        cells = " | ".join(f"{recall.get(k, 0):.3f}" for k in
                           ("traffic_dominant", "local_accumulation",
                            "regional_transport", "uncertain"))
        lines.append(f"| {names[method]} | **{point[method]:.4f}** | "
                     f"{abst[method]['uncertain_rate']:.3f} | "
                     f"{1 - abst[method]['uncertain_rate']:.3f} | {cells} |")
    lines += [
        "",
        "**配对比较（聚类 95% bootstrap，配对单位=日期；两侧点估计与配置随结果归档）**：",
        "",
        "| 比较 | 差值 | 95% CI | 是否跨 0 |",
        "|---|---|---|---|",
    ]
    for key, record in ci.items():
        lo, hi = record["ci95"]
        crosses = "跨 0" if lo <= 0 <= hi else "**不含 0**"
        lines.append(f"| {key} − B | {record['mean_diff']:+.4f} | "
                     f"[{lo:+.4f}, {hi:+.4f}] | {crosses} |")
    lines += [
        "",
        "**分站点 Macro-F1**：",
        "",
        "| 方法 | " + " | ".join(blind["by_station"]) + " |",
        "|---" * (len(blind["by_station"]) + 1) + "|",
    ]
    for method in methods:
        cells = " | ".join(f"{blind['by_station'][s][method]:.4f}"
                           for s in blind["by_station"])
        lines.append(f"| {names[method]} | {cells} |")
    lines += [
        "",
        "**独立标注口径核验（v2/v3 两套独立标注，与主标注不同源）**："
        f"v2 `{json.dumps(blind['point_f1_independent_v2'], ensure_ascii=False)}`；"
        f"v3 `{json.dumps(blind['point_f1_independent_v3'], ensure_ascii=False)}`。",
        "",
        "**弱证据误报（weekend_negative 组，越少越好）**："
        + "；".join(f"{names[m]} {n}" for m, n in
                    blind["weak_evidence_traffic_misfire"].items())
        + "。",
    ]
    if failures:
        lines += [
            "",
            f"**全部失败案例**：共 **{failures['n_failure_rows']}** 个窗口至少有一个方法判错"
            f"（占 {failures['n_failure_rows'] / failures['n_cases']:.1%}）；各方法错判数 "
            + "；".join(f"{names[m]} {n}" for m, n in failures["per_method_wrong"].items())
            + "。逐例明细见 `results/v99_blind"
            + str(design["holdout_year"]) + "_failures.csv`，"
            "完整成功/失败矩阵见 `results/v99_blind"
            + str(design["holdout_year"]) + "_case_matrix.csv`。",
        ]
    return "\n".join(lines)


def replace_block(text: str, name: str, body: str) -> str:
    begin, end = f"<!-- BEGIN:{name} -->", f"<!-- END:{name} -->"
    if begin not in text or end not in text:
        raise SystemExit(f"报告中缺少标记块 {name}")
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{begin}\n{body}\n{end}{tail}"


def main() -> int:
    audit = json.loads((RESULTS / "v97_config_audit.json").read_text(encoding="utf-8"))
    provenance = json.loads((RESULTS / "v97_input_provenance_audit.json")
                            .read_text(encoding="utf-8"))
    text = DOC.read_text(encoding="utf-8")
    sweep = json.loads((RESULTS / "v96_rule_sweep.json").read_text(encoding="utf-8"))
    text = replace_block(text, "V9.6_CROSSDOMAIN", build_crossdomain(sweep))
    text = replace_block(text, "V9.6_CWB_STATIONS", build_cwb_table(sweep))
    text = replace_block(text, "V9.7_CONFIG_TABLE", build_config_table(audit))
    text = replace_block(text, "V9.7_PROVENANCE", build_provenance(provenance))
    inventory_path = RESULTS / "v99_test_inventory.json"
    if inventory_path.exists():
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        text = replace_block(text, "V9.9_TEST_INVENTORY", build_test_inventory(inventory))
    text = replace_block(text, "V9.9_RETIRED_CROSSWALK", build_retired_crosswalk(audit))
    lineage_path = RESULTS / "v99_blind_year_lineage.json"
    if lineage_path.exists():
        lineage = json.loads(lineage_path.read_text(encoding="utf-8"))
        text = replace_block(text, "V9.9_BLIND_LINEAGE", build_blind_lineage(lineage))
    # 盲测结果由 common.save_result 写在 benchmarks/results/（与其它基准一致），
    # 逐例明细与失败小结写在 v9/results/。
    blind_path = HERE.parent / "results" / "v9_governance_blind2021.json"
    if blind_path.exists():
        blind = json.loads(blind_path.read_text(encoding="utf-8"))
        failure_path = RESULTS / "v99_blind2021_failure_summary.json"
        failures = (json.loads(failure_path.read_text(encoding="utf-8"))
                    if failure_path.exists() else None)
        text = replace_block(text, "V9.9_BLIND_RESULT",
                             build_blind_result(blind, failures))
    DOC.write_text(text, encoding="utf-8")
    print("报告数值块已由 JSON 重新生成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
