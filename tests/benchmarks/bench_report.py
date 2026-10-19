"""Benchmark: ReportAgent (structure completeness).

Feeds the stored end-to-end Mong Kok output into ReportAgent and checks:
  - all 7 sections present and non-empty
  - Chinese content (CJK char ratio)
  - generation latency

Usage: /usr/bin/python3 tests/benchmarks/bench_report.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result

from agents.report_agent import ReportAgent

EXPECTED_SECTIONS = 7


def cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk / max(len(text), 1)


def main() -> int:
    base = Path(__file__).resolve().parents[2]
    doc = json.loads((base / "examples" / "day9_coordinator_output.json").read_text())

    agent = ReportAgent()
    t0 = time.perf_counter()
    out = agent.run(doc)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    result = out.get("result") or {}
    sections = result.get("sections") or {}
    if isinstance(sections, list):
        section_list = [
            {"title": s.get("title"), "chars": len(str(s.get("content") or s.get("text") or ""))}
            for s in sections if isinstance(s, dict)
        ]
    else:
        section_list = [
            {"title": k, "chars": len(str(v))} for k, v in sections.items()
        ]
    full_text = str(result.get("report_text") or json.dumps(sections, ensure_ascii=False))
    payload = {
        "agent": "ReportAgent",
        "status": out.get("status"),
        "sections_found": len(section_list),
        "sections_expected": EXPECTED_SECTIONS,
        "sections": section_list,
        "total_chars": len(full_text),
        "cjk_ratio": round(cjk_ratio(full_text), 3),
        "latency_ms": round(elapsed_ms, 1),
    }
    path = save_result("report", payload)
    print(f"saved -> {path}")
    print(f"sections: {payload['sections_found']}/{payload['sections_expected']}  "
          f"chars: {payload['total_chars']}  cjk: {payload['cjk_ratio']}  "
          f"latency: {payload['latency_ms']}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
