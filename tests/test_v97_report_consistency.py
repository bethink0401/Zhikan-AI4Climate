"""V9.8 一致性测试：报告的数值块必须与结果 JSON 保持同步。

审查发现"主表写 D−C = −0.2369，正文却写 −0.1987"这类自相矛盾，根因是**手工复制
数值**。现在的约束是：报告里 V9.7 的两个数值块由 `make_report_tables.py` 从
`results/v97_config_audit.json` 与 `results/v97_input_provenance_audit.json`
自动生成；本测试重跑生成器并比对文本，任何人手工改动都会让它失败。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V9 = ROOT / "tests" / "benchmarks" / "v9"
if str(V9) not in sys.path:
    sys.path.insert(0, str(V9))

import make_report_tables as gen  # noqa: E402

DOC = ROOT / "docs" / "对比实验结果汇总(V9).md"
RESULTS = V9 / "results"


def _block(text: str, name: str) -> str:
    begin, end = f"<!-- BEGIN:{name} -->", f"<!-- END:{name} -->"
    assert begin in text and end in text, f"报告缺少标记块 {name}"
    return text.split(begin, 1)[1].split(end, 1)[0].strip()


def test_report_blocks_match_json() -> None:
    text = DOC.read_text(encoding="utf-8")
    import json

    audit = json.loads((RESULTS / "v97_config_audit.json").read_text(encoding="utf-8"))
    provenance = json.loads(
        (RESULTS / "v97_input_provenance_audit.json").read_text(encoding="utf-8"))

    sweep = json.loads((RESULTS / "v96_rule_sweep.json").read_text(encoding="utf-8"))
    assert _block(text, "V9.6_CROSSDOMAIN") == gen.build_crossdomain(sweep), (
        "报告中的跨域判据表与 v96_rule_sweep.json 不同步——请运行 make_report_tables.py")
    assert _block(text, "V9.6_CWB_STATIONS") == gen.build_cwb_table(sweep), (
        "报告中的 CWB 分站表与 v96_rule_sweep.json 不同步——请运行 make_report_tables.py")
    assert _block(text, "V9.7_CONFIG_TABLE") == gen.build_config_table(audit), (
        "报告中的配置表与 v97_config_audit.json 不同步——请运行 make_report_tables.py")
    assert _block(text, "V9.7_PROVENANCE") == gen.build_provenance(provenance), (
        "报告中的溯源小节与 v97_input_provenance_audit.json 不同步——"
        "请运行 make_report_tables.py")

def test_no_superseded_numbers_in_document() -> None:
    """已被取代的旧数值不得再出现在正文里（只允许出现在历史附录的"曾主张"列）。"""
    text = DOC.read_text(encoding="utf-8")
    # 只检查 V9.8 之前的正文：V9.8 的"处置表"需要**引述**这些被取代的数值来说明问题，
    # 那是合法的引用而非残留。历史附录（含"曾主张"列）同样不在此检查范围。
    body = text.split("## V9.8 增补")[0]
    superseded = ["0.1561", "0.1987", "0.3271", "0.5459", "0.2164"]
    leftovers = [value for value in superseded if value in body]
    assert not leftovers, f"正文仍含已被取代的数值：{leftovers}"


def test_appendix_points_to_consistent_values() -> None:
    """历史附录的"现行结论"列必须与 JSON 一致。

    V9.9 起附录不再手写数字（值会随配置修正而变，手写必然过期），改为引用自动生成块
    `V9.9_RETIRED_CROSSWALK`；本测试因此改为断言"引用关系存在 + 旧值已清除"。
    """
    text = DOC.read_text(encoding="utf-8").split("## 历史附录")[1]
    assert "V9.9_RETIRED_CROSSWALK" in text, "附录未引用自动生成的现行取值块"
    assert "−0.3271" not in text, "附录仍保留旧的 −0.3271"
    assert "−0.1987" not in text, "附录仍保留旧的 −0.1987（与 D−C 的现行值不符）"
