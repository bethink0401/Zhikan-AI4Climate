"""V9.9 一致性测试：报告里的每一个统计断言都必须能追溯到结果 JSON。

V9.7 发现"主表 −0.2369、正文 −0.1987"；V9.8 发现"差值对不上它自己声明的基线"；
两轮都是同一类问题：**手写数字**。本测试把三类防护全部锁定：

1. 六个数值块必须与生成器输出逐字一致（重跑生成器比对）；
2. 测试计数必须自洽（分类之和 == 总数），且来自 pytest 实际运行；
3. **全文所有『差值 + CI』断言**必须能在结果 JSON 或自校验的历史登记里找到；
4. 历史附录不得再手写这类数字。

第 3 条由 `audit_report_claims.py` 提供，是审查建议"自动检查比较对象、指标、
实际差值、CI 和配置 ID"的落地实现。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V9 = ROOT / "tests" / "benchmarks" / "v9"
if str(V9) not in sys.path:
    sys.path.insert(0, str(V9))

import audit_report_claims as claims  # noqa: E402
import make_report_tables as gen  # noqa: E402

DOC = ROOT / "docs" / "对比实验结果汇总(V9).md"
RESULTS = V9 / "results"

BLOCKS = {
    "V9.6_CROSSDOMAIN": lambda: gen.build_crossdomain(
        json.loads((RESULTS / "v96_rule_sweep.json").read_text(encoding="utf-8"))),
    "V9.6_CWB_STATIONS": lambda: gen.build_cwb_table(
        json.loads((RESULTS / "v96_rule_sweep.json").read_text(encoding="utf-8"))),
    "V9.7_CONFIG_TABLE": lambda: gen.build_config_table(
        json.loads((RESULTS / "v97_config_audit.json").read_text(encoding="utf-8"))),
    "V9.7_PROVENANCE": lambda: gen.build_provenance(
        json.loads((RESULTS / "v97_input_provenance_audit.json").read_text(encoding="utf-8"))),
    "V9.9_RETIRED_CROSSWALK": lambda: gen.build_retired_crosswalk(
        json.loads((RESULTS / "v97_config_audit.json").read_text(encoding="utf-8"))),
    "V9.9_TEST_INVENTORY": lambda: gen.build_test_inventory(
        json.loads((RESULTS / "v99_test_inventory.json").read_text(encoding="utf-8"))),
    "V9.9_BLIND_LINEAGE": lambda: gen.build_blind_lineage(
        json.loads((RESULTS / "v99_blind_year_lineage.json").read_text(encoding="utf-8"))),
    "V9.9_BLIND_RESULT": lambda: gen.build_blind_result(
        json.loads((V9.parent / "results" / "v9_governance_blind2021.json")
                   .read_text(encoding="utf-8")),
        json.loads((RESULTS / "v99_blind2021_failure_summary.json")
                   .read_text(encoding="utf-8"))),
}


def _block(text: str, name: str) -> str:
    begin, end = f"<!-- BEGIN:{name} -->", f"<!-- END:{name} -->"
    assert begin in text and end in text, f"报告缺少标记块 {name}"
    return text.split(begin, 1)[1].split(end, 1)[0].strip()


def test_all_generated_blocks_match_json() -> None:
    text = DOC.read_text(encoding="utf-8")
    for name, builder in BLOCKS.items():
        assert _block(text, name) == builder(), (
            f"报告中的 {name} 与生成器输出不同步——请运行 make_report_tables.py")


def test_claim_audit_is_clean() -> None:
    """全文『差值 + CI』断言可追溯，且无已被取代的旧表述残留。"""
    report = claims.audit()
    assert report["claims_checked"] >= 20, "断言数量异常偏少，可能正则失效"
    assert not report["violations"], (
        "以下断言无法追溯到任何结果 JSON：\n"
        + "\n".join(f"  L{v['line']}: {v['claim']}" for v in report["violations"]))
    assert not report["stale_phrases"], (
        "以下已被取代的旧表述仍出现在正文（非引用/勘误语境）——这正是"
        "『没有紧邻 CI 的旧错误文字』这类逃过差值正则的残留：\n"
        + "\n".join(f"  L{v['line']}: 「{v['phrase']}」 {v['context'][:120]}"
                    for v in report["stale_phrases"]))


def test_test_inventory_is_self_consistent() -> None:
    """测试计数必须自洽，且覆盖当前磁盘上的全部测试文件。

    这里刻意**不**用"清单生成时间晚于最新文件修改时间"来判定新鲜度：清单是"某次
    运行"的快照，而本测试自己就在 pytest 里跑，用时间做判据会形成循环依赖
    （改了测试 → 清单未更新 → 本测试失败 → 下一次快照又记录失败）。改用**内容
    不变量**：清单里统计到的测试文件集合，必须与磁盘上实际存在的测试文件集合
    完全一致——这样"新增/删除测试文件却忘记重导清单"一定被抓住，而单纯修改
    脚本内容不会误报。
    """
    inventory_path = RESULTS / "v99_test_inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    assert sum(inventory["per_round"].values()) == inventory["total_collected"], (
        "测试分类明细之和与总数不一致——这正是 V9.8 报告 257≠260 的错误")
    assert inventory["total_collected"] == sum(inventory["per_file"].values())
    run = inventory["pytest_run"]
    assert run["errors"] == 0, f"pytest 存在收集错误：{run['summary_line']}"
    assert run["collected"] == inventory["total_collected"], (
        "清单记录的收集数与按文件统计不一致")

    on_disk = {str(path.relative_to(ROOT)) for path in (ROOT / "tests").glob("test_*.py")}
    recorded = set(inventory["per_file"])
    assert recorded == on_disk, (
        "测试清单与磁盘上的测试文件集合不一致——请重新运行 audit_test_inventory.py\n"
        f"  仅清单有：{sorted(recorded - on_disk)}\n"
        f"  仅磁盘有：{sorted(on_disk - recorded)}")


def test_appendix_does_not_hardcode_retired_numbers() -> None:
    """历史附录只允许引用自动生成块，不得再手写这类差值。"""
    appendix = DOC.read_text(encoding="utf-8").split("## 历史附录")[1]
    for stale in ("−0.1987", "−0.3337", "−0.3271"):
        assert stale not in appendix, f"附录仍手写了已被取代的数值 {stale}"
    assert "V9.9_RETIRED_CROSSWALK" in appendix, "附录未引用自动生成的现行取值块"


def test_config_audit_e_equals_sweep_frozen() -> None:
    """E_synth_calib 必须逐键等于扫描脚本的 frozen_config（V9.9 修复的隐藏不一致）。"""
    audit = json.loads((RESULTS / "v97_config_audit.json").read_text(encoding="utf-8"))
    sweep = json.loads((RESULTS / "v96_rule_sweep.json").read_text(encoding="utf-8"))
    assert audit["configs"]["E_synth_calib"] == sweep["frozen_config"], (
        "审计里的『合成标定』与扫描冻结配置不是同一套——报告会出现两个同名数")
    assert audit["configs"]["F_calib_R1"] == {
        **sweep["frozen_config"], "regional_variant": "R1_sync"}
