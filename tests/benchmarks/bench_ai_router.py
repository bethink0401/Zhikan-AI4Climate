"""Benchmark: AI interaction layer — offline evaluable deterministic parts.

The LLM layer itself needs an API key and network, so this script evaluates
what can be measured reproducibly offline: the rule-based slot extraction
(Chinese/English station aliases, date and hour-range parsing) and the
intent router that the LLM scheduler calls *before* any model request.

Metrics reported:
  - station slot accuracy (top-1 match against the expected station id)
  - time slot accuracy (start/end exact match for the cases that carry time)
  - intent routing accuracy over six intents

An evaluation protocol for the model-dependent parts (answer grounding,
multi-turn inheritance, degradation success rate) is listed at the end and
is intended to run in an environment with model credentials configured.

Usage: /usr/bin/python3 tests/benchmarks/bench_ai_router.py
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RESULTS_DIR, save_result
import json

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _install_stubs() -> None:
    """本脚本只评测规则层函数，LLM/服务端依赖用桩模块占位即可（不安装任何包）。"""
    def _mod(name: str, **attrs: object) -> None:
        if name in sys.modules:
            return
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[name] = module

    class _Placeholder:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __getattr__(self, item: str):
            """支持 app.post(...)/app.get(...) 这类装饰器式调用。"""
            def _deco(*args: object, **kwargs: object):
                def _wrap(fn):
                    return fn
                return _wrap
            return _deco

    def _decorator(*args: object, **kwargs: object):
        def _wrap(fn):
            return fn
        return _wrap

    _mod("langchain_openai", ChatOpenAI=_Placeholder)
    _mod("langchain.agents", create_agent=lambda *a, **k: None)
    _mod("langchain_core.tools", StructuredTool=_Placeholder)
    _mod("langchain_core.chat_history", InMemoryChatMessageHistory=_Placeholder)
    _mod("langchain_core.runnables.history", RunnableWithMessageHistory=_Placeholder)
    _mod("langchain_core.messages", HumanMessage=_Placeholder, SystemMessage=_Placeholder)
    _mod("fastapi", FastAPI=_Placeholder)
    _mod("fastapi.responses", StreamingResponse=_Placeholder)
    _mod("uvicorn", run=lambda *a, **k: None)
    _mod("tenacity", retry=_decorator, stop_after_attempt=_decorator,
         wait_exponential=_decorator, retry_if_exception_type=_decorator)


_install_stubs()

# ── 测试用例：(输入, 期望站点 id 或 None, 期望 start, 期望 end, 期望意图或 None)
CASES: list[tuple[str, str | None, str | None, str | None, str | None]] = [
    ("分析2023年8月15日8点到11点旺角的污染情况", "mong_kok", "2023-08-15 08:00:00", "2023-08-15 11:00:00", None),
    ("看看铜锣湾早高峰", "causeway_bay", None, None, None),
    ("中环路边站的空气质量", "central", None, None, "air_quality"),
    ("2023/8/15 旺角 空气质量怎么样", "mong_kok", "2023-08-15 00:00:00", "2023-08-15 23:59:59", "air_quality"),
    ("旺角的风速风向", "mong_kok", None, None, "meteorology"),
    ("观塘天气如何", "kwun_tong", None, None, "meteorology"),
    ("沙田的湿度", "shatin", None, None, "meteorology"),
    ("深水埗 2023-08-15 交通压力", "sham_shui_po", "2023-08-15 00:00:00", "2023-08-15 23:59:59", "traffic"),
    ("元朗的车流情况", "yuen_long", None, None, "traffic"),
    ("东涌交通怎么样", "tung_chung", None, None, "traffic"),
    ("塔门的污染来源", "tap_mun", None, None, "source"),
    ("大埔污染是什么来源", "tai_po", None, None, "source"),
    ("中环的空间影响范围", "central", None, None, None),
    ("旺角影响范围多大", "mong_kok", None, None, None),
    ("将军澳该采取什么措施", "tseung_kwan_o", None, None, "decision"),
    ("屯门的决策建议", "tuen_mun", None, None, "decision"),
    ("北区气象风险", "north", None, None, "meteorology"),
    ("南区降雨", "southern", None, None, "meteorology"),
    ("葵涌2023年12月1日9点到18点的情况", "kwai_chung", "2023-12-01 09:00:00", "2023-12-01 18:00:00", None),
    ("荃湾 8月15号 8点至11点 污染", "tsuen_wan", None, None, "source"),
    ("Mong Kok roadside pollution", "mong_kok", None, None, "source"),
    ("Causeway Bay Roadside 的交通", "causeway_bay", None, None, "traffic"),
    ("Central 空气", "central", None, None, "air_quality"),
    ("旺角 2023.8.15 的气象", "mong_kok", "2023-08-15 00:00:00", "2023-08-15 23:59:59", "meteorology"),
    ("沙田2023/11/20 7点-10点 空气质量", "shatin", "2023-11-20 07:00:00", "2023-11-20 10:00:00", "air_quality"),
]


def main() -> int:
    from agents.coordinator import LangChainCoordinator, TaskContext

    # 绕开 __init__（它要构建 LangChain agent）；规则层函数只需要 context。
    coordinator = LangChainCoordinator.__new__(LangChainCoordinator)
    coordinator.context = TaskContext()

    station_hit = station_total = 0
    time_hit = time_total = 0
    intent_hit = intent_total = 0
    details = []

    for text, exp_station, exp_start, exp_end, exp_intent in CASES:
        coordinator.context.station_name = None
        coordinator.context.station_id = None
        coordinator.context.start_time = None
        coordinator.context.end_time = None
        coordinator.context.intent = None

        coordinator._auto_extract_params(text)
        got_station = coordinator.context.station_id
        got_intent = coordinator._detect_intent(text)
        if got_intent and not coordinator.context.intent:
            coordinator.context.intent = got_intent

        if exp_station is not None:
            station_total += 1
            station_hit += int(got_station == exp_station)
        if exp_start is not None:
            time_total += 1
            time_hit += int(
                coordinator.context.start_time == exp_start
                and coordinator.context.end_time == exp_end
            )
        if exp_intent is not None:
            intent_total += 1
            intent_hit += int(got_intent == exp_intent or coordinator.context.intent == exp_intent)

        details.append({
            "input": text,
            "expected_station": exp_station,
            "got_station": got_station,
            "expected_start": exp_start,
            "got_start": coordinator.context.start_time,
            "got_end": coordinator.context.end_time,
            "expected_intent": exp_intent,
            "got_intent": got_intent or coordinator.context.intent,
        })

    # ── 留档 25 条任务的规则命中审计（离线；交叉模型层留档结果）──
    # 目的：区分"规则层即可完成"与"必须由 LLM 介入"的任务，并找出规则未命中
    # 却由 LLM 正确完成的案例（AI 正向增量），供方案 5.2(7) 引用。
    audit = []
    archived_path = RESULTS_DIR / "ai_model_metrics.json"
    if archived_path.exists():
        archived = json.loads(archived_path.read_text())
        for row in archived.get("rows", []):
            text = row.get("task") or ""
            coordinator.context.station_name = None
            coordinator.context.station_id = None
            coordinator.context.start_time = None
            coordinator.context.end_time = None
            coordinator.context.intent = None
            coordinator._auto_extract_params(text)
            intent = coordinator._detect_intent(text)
            route = coordinator._route_agent(text)
            ready = bool(route) and coordinator._params_ready_for(route)
            audit.append({
                "index": row.get("index"), "task": text,
                "multi_turn": bool(row.get("multi_turn")),
                "rule_intent": intent, "rule_route": route, "params_ready": ready,
                "rule_can_route": ready,
                "model_ok": bool(row.get("ok")),
                "clarifying": bool(row.get("clarifying")),
                "numbers_matched": row.get("numbers_matched"),
                "numbers_in_answer": row.get("numbers_in_answer"),
            })

    rule_routed = [a for a in audit if a["rule_can_route"]]
    llm_needed = [a for a in audit if not a["rule_can_route"]]
    llm_delivered = [a for a in llm_needed
                     if a["model_ok"] and not a["clarifying"] and (a["numbers_matched"] or 0) > 0]

    payload = {
        "scope": "离线可评测的规则层（站点槽位 / 时间槽位 / 意图路由）；"
                 "LLM 依赖部分需在有模型凭证环境执行（协议见 llm_eval_protocol）",
        "cases": len(CASES),
        "station_slot": {"hit": station_hit, "total": station_total},
        "time_slot": {"hit": time_hit, "total": time_total},
        "intent_routing": {"hit": intent_hit, "total": intent_total},
        "details": details,
        "archive_audit": {
            "source": "results/ai_model_metrics.json（留档的 25 条任务）",
            "rule_can_route": f"{len(rule_routed)}/{len(audit)}",
            "needs_llm_or_clarification": f"{len(llm_needed)}/{len(audit)}",
            "llm_delivered_with_numbers": [a["task"] for a in llm_delivered],
            "rows": audit,
        },
        "llm_eval_protocol": [
            "30–50 条自然语言任务：站点别名、时间窗、多轮追问、模糊表达",
            "指标：意图路由准确率 / 槽位提取准确率 / 数值回答与 Agent 输出一致率 / 模型不可用时降级成功率",
            "对照：单脚本流程 vs 多智能体方案在故障定位、局部替换、证据回溯上的差异",
        ],
    }
    path = save_result("ai_router", payload)
    print(f"saved -> {path}")
    print(f"station slot: {station_hit}/{station_total}")
    print(f"time slot:    {time_hit}/{time_total}")
    print(f"intent route: {intent_hit}/{intent_total}")
    if audit:
        print(f"archive audit: rule can route {len(rule_routed)}/{len(audit)}; "
              f"needs LLM or clarification {len(llm_needed)}/{len(audit)}")
        print(f"  LLM 交付且含可核对数值: {[a['task'][:24] for a in llm_delivered]}")
    misses = [d for d in details if (d["expected_station"] and d["got_station"] != d["expected_station"])
              or (d["expected_start"] and d["got_start"] != d["expected_start"])
              or (d["expected_intent"] and d["got_intent"] != d["expected_intent"])]
    for m in misses:
        print("  miss:", m)
    return 0


if __name__ == "__main__":
    sys.exit(main())
