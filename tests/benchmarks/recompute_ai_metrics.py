"""Recompute derived AI-model-layer metrics from the saved answers (offline).

The evaluation in ``bench_ai_model_metrics.py`` already stores the raw answers, so
derived metrics can be recomputed without spending model quota. This script fixes
two definition issues found during review:

  1. date statements: answers may write "2023年8月15日" as well as "2023-08-15",
     so the statement check must accept both formats;
  2. the "evidence citation" flag conflated two different things (naming the
     station vs. quoting a verifiable number); they are reported separately here.

Usage: /Users/chenweiyang/.workbuddy/binaries/python/envs/llm-eval/bin/python tests/benchmarks/recompute_ai_metrics.py
"""
from __future__ import annotations

import json
import re
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

RESULT_PATH = Path(__file__).resolve().parent / "results" / "ai_model_metrics.json"

#: 支持 "2023-08-15" / "2023年8月15日" / "2023.8.15" 三种写法
DATE_ANY_RE = re.compile(r"(\d{4})\s*[-年./]\s*(\d{1,2})\s*[-月./]\s*(\d{1,2})")

#: 澄清/反问：回答未给出分析结论、仅询问下一步该做什么
ASK_RE = re.compile(r"请问|您需要|您想|需要优先|需要继续|需要哪|可选择|哪项分析|哪类分析|下一步")


def stated_date(text: str) -> str | None:
    """回答中第一处日期陈述，归一化为 YYYY-MM-DD。"""
    m = DATE_ANY_RE.search(text or "")
    if not m:
        return None
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def station_aliases(station_english: str) -> list[str]:
    """站名 -> 别名（含中文），用于判断回答是否点名站点。"""
    from agents.coordinator import STATION_ALIAS_MAP

    key = (str(station_english).lower().replace("roadside", "")
           .replace(" ", "").replace("/", "").replace("_", ""))
    aliases = [station_english, station_english.replace(" Roadside", "")]
    for alias, target in STATION_ALIAS_MAP.items():
        tkey = (str(target).lower().replace(" ", "").replace("/", "").replace("_", ""))
        if tkey == key:
            aliases.append(alias)
    return list({a for a in aliases if len(a) >= 2})


def main() -> int:
    payload = json.loads(RESULT_PATH.read_text())
    rows = payload["rows"]
    completed = [r for r in rows if r.get("ok")]

    named = 0
    verifiable = 0
    date_checked = 0
    date_ok = 0
    clarifying = []
    for r in completed:
        answer = r.get("answer") or ""
        aliases = station_aliases(r["expected_station"]) if r.get("expected_station") else []
        cites = any(a.lower() in answer.lower() for a in aliases)
        r["cites_station"] = bool(cites)
        named += int(cites)
        verifiable += int(bool((r.get("numbers_matched") or 0) > 0))
        # 澄清/反问：无任何业务数值 且 以询问下一步为主
        is_asking = bool(ASK_RE.search(answer)) and not (r.get("numbers_in_answer") or 0)
        r["clarifying"] = is_asking
        if is_asking:
            clarifying.append({"task": r["task"], "answer": answer[:60]})
        ctx_start = (r.get("context") or {}).get("start_time")
        if ctx_start:
            stated = stated_date(answer)
            r["stated_date"] = stated
            if stated:
                date_checked += 1
                hit = ctx_start.startswith(stated)
                r["answer_window_matches_context"] = hit
                date_ok += int(hit)
            else:
                r["answer_window_matches_context"] = None

    numbered = [r for r in completed if (r.get("numbers_in_answer") or 0) > 0]
    payload["completion_detail"] = {
        "successful_return": f"{len(completed)}/{len(rows)}",
        "answered_directly": f"{len(completed) - len(clarifying)}/{len(rows)}",
        "clarifying_or_asking": f"{len(clarifying)}/{len(rows)}",
        "clarifying_tasks": clarifying,
    }
    payload["numeric_consistency_scope"] = {
        "answers_with_verifiable_numbers": f"{len(numbered)}/{len(rows)}",
        "answers_without_business_numbers": f"{len(rows) - len(numbered)}/{len(rows)}",
    }
    payload["station_named_rate"] = f"{named}/{len(completed)}"
    payload["verifiable_number_rate"] = f"{verifiable}/{len(completed)}"
    payload["evidence_citation_rate"] = f"{named + verifiable - sum(1 for r in completed if r.get('cites_station') and (r.get('numbers_matched') or 0) > 0)}/{len(completed)}"
    payload["evidence_both_rate"] = f"{sum(1 for r in completed if r.get('cites_station') and (r.get('numbers_matched') or 0) > 0)}/{len(completed)}"
    payload["date_statement"] = {
        "checked": date_checked,
        "consistent": date_ok,
        "rate": f"{date_ok}/{date_checked}",
        "mismatches": [{"task": r["task"], "stated": r.get("stated_date"),
                        "actual": (r.get("context") or {}).get("start_time")}
                       for r in completed if r.get("answer_window_matches_context") is False],
    }
    RESULT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    print("完成率:", payload["completion_rate"])
    print("成功返回率:", payload["completion_detail"]["successful_return"],
          "| 直接完成回答率:", payload["completion_detail"]["answered_directly"],
          "| 澄清/反问:", payload["completion_detail"]["clarifying_or_asking"])
    for c in payload["completion_detail"]["clarifying_tasks"]:
        print("    反问:", c["task"], "::", c["answer"])
    print("数值一致率(均值):", payload["numeric_consistency_mean"],
          "| 分母:", payload["numeric_consistency_scope"]["answers_with_verifiable_numbers"])
    print("点名站点率:", payload["station_named_rate"])
    print("含可核验数值的回答:", payload["verifiable_number_rate"])
    print("证据引用率(点站且有可核验数值):", payload["evidence_both_rate"])
    print("多轮继承率:", payload["multi_turn_inheritance_rate"])
    print("回答日期陈述一致:", payload["date_statement"]["rate"],
          "| 不一致:", json.dumps(payload["date_statement"]["mismatches"], ensure_ascii=False))
    print("平均响应:", payload["mean_latency_s"], "s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
