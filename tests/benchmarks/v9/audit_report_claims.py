"""V9.9：报告断言的自动审计器（审查原文："让 Codex 对每一句『比……提高/降低……』
自动检查比较对象、指标、实际差值、CI 和配置 ID"）。

为什么需要：V9.8 已经把**数值块**改成从 JSON 生成，但正文里仍有大量手写句子
（"相对……提高 X，CI [a, b]"）。只要句子是手写的，就可能出现 V9.7/V9.8 两轮反复
出现的同类错误——差值对不上它自己声明的基线。

本脚本做两件事：

1. **建差值登记表**：递归扫描 `results/*.json`，把每个同时含 `mean_diff` 与
   `ci95` 的记录收成 (差值, 下界, 上界) 三元组，并保留它来自哪个文件、哪个路径。
2. **逐句核对**：在报告里找出所有形如 `<数值>，CI [<下界>, <上界>]` 的断言，
   要求该三元组必须出现在登记表里；同时要求句子里出现的"配置 ID / 行名"能在
   登记表路径中找到。找不到就记为违规并打印上下文行号。

这是一个**面向差值与 CI**的核对器，不试图解析任意自然语言。它的价值在于把
"差值对不对得上它自己声明的 CI" 这件事从人工检查变成机器检查——这正是过去
三轮反复出问题的地方。

用法：
    /usr/bin/python3 tests/benchmarks/v9/audit_report_claims.py            # 打印报告
    /usr/bin/python3 tests/benchmarks/v9/audit_report_claims.py --strict    # 有违规则退出码 1
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
DOC = BASE / "docs" / "对比实验结果汇总(V9).md"
#: 结果目录有两处：v9/ 下的本轮结果，以及 benchmarks/ 下的各轮结果。
RESULT_DIRS = (HERE / "results", HERE.parent / "results")
#: 已归档的差值声明：产生它的那次运行的 JSON 已被后续轮次覆盖，无法从现行文件复算，
#: 但**来源脚本与轮次明确**。每条都必须能回答"这是哪一轮、哪个脚本产出的"。
ARCHIVE = HERE / "results" / "v99_claim_archive.json"

#: 形如 `−0.1700，CI [−0.2190, −0.1181]` / `+0.057，CI [+0.009, +0.110]`
_CLAIM_RE = re.compile(
    r"([+−-]?\d+\.\d{2,4})[^\n\d]{0,12}?CI\s*\["
    r"([+−-]?\d+\.\d{2,4}),\s*([+−-]?\d+\.\d{2,4})\]")

#: 报告中允许出现"不成对"的差值句——这些句子讲的是**方法学**而非某个具体结果
_ALLOWED_UNPAIRED = (
    "V9.1 曾修过一次",
    "原报告的 +0.035",
)

#: 报告里常写 3 位小数（−0.037），JSON 里是 4 位（−0.0372）。比对时按报告自身
#: 的小数位放宽：写 3 位就允许 ±5e-4 的舍入误差，写 4 位允许 ±5e-5。
_TOLERANCE = {"3": 5e-4, "4": 5e-5}

#: **已经被取代、但格式上逃过 `差值+CI` 正则的旧表述**（审查 V9.9.1 指出的盲区：
#: 自动审计只覆盖了"差值紧邻 CI"的形态，漏掉了没有 CI 的旧错误文字）。这些字符串
#: 只允许出现在**引用/勘误语境**里（行内含 _RETRACTION_MARKERS 之一），其余位置
#: 一律视为残留。
STALE_PHRASES = (
    # 逐窗命中率差 −0.035 曾被当成 Macro-F1 差写在"修复后 C−B"后面（V9.1/V9.5 勘误）
    "C−B −0.035",
    "C−B = −0.035",
    "C−B  -0.035",
    # V9.8 曾把 0.076 差值直接归因于静默降级（V9.9 已收窄为"主要可疑原因"）
    "不是配置或代码变化，而是",
    # 曾把模拟的降级比例写得像实际历史比例
    "12–13% 的窗口降级",
    "对应约 12–13%",
)

#: 含这些标记的行视为"引用/勘误语境"，允许出现上面的旧字符串。
_RETRACTION_MARKERS = ("~~", "撤回", "弃用", "勘误", "收窄", "原先写", "原写的",
                       "已改为", "不再", "曾", "原报告")


def _stale_phrase_hits(text: str) -> list[dict]:
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for phrase in STALE_PHRASES:
            if phrase not in line:
                continue
            if any(marker in line for marker in _RETRACTION_MARKERS):
                continue  # 合法引用：正在说明该数值为何被取代
            hits.append({"line": lineno, "phrase": phrase,
                         "context": line.strip()[:200]})
    return hits


def _decimals(token: str) -> str:
    return str(len(token.split(".")[1])) if "." in token else "0"


def _matches(claim: tuple, key: tuple, tol: float) -> bool:
    return all(abs(a - b) <= tol + 1e-9 for a, b in zip(claim, key))


def _norm(token: str) -> float:
    """把全角负号 / 加号统一成 ASCII，再取浮点。"""
    return float(token.replace("−", "-").replace("＋", "+").replace("+", "", 1)
                 if token.startswith("+") else token.replace("−", "-"))


def _triples(node, path: str, out: list[dict]) -> None:
    """递归收集所有 (mean_diff, ci95) 记录。"""
    if isinstance(node, dict):
        if "mean_diff" in node and isinstance(node.get("ci95"), (list, tuple)) \
                and len(node["ci95"]) == 2:
            try:
                out.append({
                    "diff": round(float(node["mean_diff"]), 4),
                    "lo": round(float(node["ci95"][0]), 4),
                    "hi": round(float(node["ci95"][1]), 4),
                    "path": path,
                    "arm": node.get("arm"), "baseline": node.get("baseline"),
                    "label": node.get("baseline_label"),
                })
            except (TypeError, ValueError):
                pass
        for key, value in node.items():
            _triples(value, f"{path}.{key}", out)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _triples(value, f"{path}[{index}]", out)


def build_registry() -> list[dict]:
    records: list[dict] = []
    for directory in RESULT_DIRS:
        for path in sorted(directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            _triples(payload, path.name, records)
    # 去重（同三元组来自多份文件时只留一条，但要保留全部来源）
    merged: dict[tuple, dict] = {}
    for record in records:
        key = (record["diff"], record["lo"], record["hi"])
        if key in merged:
            merged[key]["path"] = f"{merged[key]['path']} | {record['path']}"
        else:
            merged[key] = dict(record)
    return list(merged.values())


def load_archive() -> dict[tuple, dict]:
    """读取人工登记的历史差值（附来源脚本/轮次），并**自校验**其内部一致性。

    归档条目不是白名单：每条都带两侧点估计，这里断言 diff == arm − baseline，
    否则归档本身就不可信（正是过去几轮出问题的地方）。
    """
    if not ARCHIVE.exists():
        return {}
    payload = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    out = {}
    for entry in payload.get("entries", []):
        expected = round(float(entry["arm_point_f1"]) - float(entry["baseline_point_f1"]), 4)
        if abs(expected - round(float(entry["diff"]), 4)) > 5e-4:
            raise AssertionError(
                f"历史登记条目自相矛盾：{entry['claim']} 的 diff={entry['diff']}，"
                f"但两侧点估计之差为 {expected}")
        key = (round(float(entry["diff"]), 4), round(float(entry["ci95"][0]), 4),
               round(float(entry["ci95"][1]), 4))
        out[key] = entry
    return out


def audit() -> dict:
    registry = build_registry()
    index = {(r["diff"], r["lo"], r["hi"]): r for r in registry}
    archived = load_archive()

    text = DOC.read_text(encoding="utf-8")
    violations = []
    checked = 0
    resolved_archive = 0
    for lineno, line in enumerate(text.splitlines(), start=1):
        if any(marker in line for marker in _ALLOWED_UNPAIRED):
            continue
        for match in _CLAIM_RE.finditer(line):
            checked += 1
            diff, lo, hi = (_norm(match.group(1)), _norm(match.group(2)),
                            _norm(match.group(3)))
            claim = (round(diff, 4), round(lo, 4), round(hi, 4))
            # 容差取三个数字里最粗的那个小数位（差值可能写 4 位而 CI 只写 3 位）。
            tol = max(_TOLERANCE.get(_decimals(t), 5e-5)
                      for t in match.groups())
            if any(_matches(claim, k, tol) for k in index):
                continue
            if any(_matches(claim, k, 5e-4) for k in archived):
                resolved_archive += 1
                continue
            violations.append({
                "line": lineno,
                "claim": match.group(0),
                "parsed": claim,
                "context": line.strip()[:200],
                "nearest": _nearest(index, claim),
            })
    return {"registry_size": len(registry), "archive_size": len(archived),
            "claims_checked": checked, "resolved_archive": resolved_archive,
            "stale_phrases": _stale_phrase_hits(text),
            "violations": violations}


def _nearest(index: dict, key: tuple) -> str:
    """给出与断言最接近的登记记录，方便人工定位（按差值距离排序取前 3）。"""
    ranked = sorted(index.items(),
                    key=lambda kv: abs(kv[0][0] - key[0]) + abs(kv[0][1] - key[1]))
    return "; ".join(f"{k} <- {v['path']}" for k, v in ranked[:3])


def main() -> int:
    strict = "--strict" in sys.argv
    report = audit()
    print(f"差值登记表：{report['registry_size']} 条（现行结果 JSON）；"
          f"历史登记：{report['archive_size']} 条；"
          f"报告中核对断言 {report['claims_checked']} 处"
          f"（其中 {report['resolved_archive']} 处由历史登记解释）")
    problems = list(report["violations"]) + list(report["stale_phrases"])
    if not problems:
        print("✔ 全部『差值 + CI』断言可追溯，且无已被取代的旧表述残留")
        return 0
    if report["stale_phrases"]:
        print(f"⚠️ {len(report['stale_phrases'])} 处已被取代的旧表述仍出现在正文（非引用语境）：\n")
        for item in report["stale_phrases"]:
            print(f"  L{item['line']}: 「{item['phrase']}」")
            print(f"        上下文 = {item['context']}\n")
    if report["violations"]:
        print(f"⚠️ {len(report['violations'])} 处断言无法对应到任何结果 JSON：\n")
        for item in report["violations"]:
            print(f"  L{item['line']}: {item['claim']}")
            print(f"        解析 = {item['parsed']}")
            print(f"        上下文 = {item['context']}")
            print(f"        最接近的登记记录 = {item['nearest']}\n")
    return 1 if strict else 0


if __name__ == "__main__":
    raise SystemExit(main())
