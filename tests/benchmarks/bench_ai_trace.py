"""Benchmark: natural-language tasks traced end to end through the AI chain.

The metric benchmark answers "did the model produce a well-supported answer".
This script answers "what did the AI actually do, and which layer did it":
which parameters the rule layer parsed, whether the deterministic hard route or
the LLM orchestration branch was taken, which agents ran, and what the answer
said. Three shapes are covered so the division of labour can be read off data:

  A. explicit window     — parameter-complete request (rules should suffice)
  B. vague, no history   — "旺角早高峰…" with an empty context
  C. cross-dimension     — one topic asked, then "接着看交通" in the same session

Requires a model credential (COORDINATOR_API_KEY); it is one of the two online
scripts. Results are archived so the trace can be cited without re-runs.

Usage: python tests/benchmarks/bench_ai_trace.py
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import save_result

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

WARMUP = "分析2023年8月15日8点到11点旺角的污染情况"

TASKS = [
    {"tag": "explicit_window", "kind": "参数齐全的结构化任务",
     "text": "分析2023年8月15日8点到11点旺角的污染来源", "session": "s1", "warmup": None},
    {"tag": "vague_no_history", "kind": "模糊提问（无历史上下文）",
     "text": "旺角早高峰污染会影响哪里，是否要调整出行", "session": "s2", "warmup": None},
    {"tag": "vague_station_only", "kind": "仅有站点、无时间窗（需 AI 介入）",
     "text": "铜锣湾早高峰空气质量怎么样", "session": "s4", "warmup": None},
    {"tag": "cross_dimension_followup", "kind": "多轮跨维度追问",
     "text": "接着看交通", "session": "s3", "warmup": WARMUP},
]


async def run_one(coordinator, text: str, retries: int = 3) -> dict:
    """Run one turn and capture the routing log plus the executed agents."""
    started = time.perf_counter()
    last_error = None
    for attempt in range(retries):
        buffer = io.StringIO()
        try:
            with contextlib.redirect_stdout(buffer):
                out = await coordinator.chat(text)
            logs = buffer.getvalue()
            results = out.get("agent_results") or {}
            context = out.get("context") or {}
            answer = str(out.get("answer") or "")
            # 路由判定：意图命中且参数齐全才走确定性硬路由；仅出现 [ROUTER]
            # 只说明意图识别命中，参数不齐时仍会落到 LLM 流程，故两者分开记录。
            rule_agent = coordinator._route_agent(text)
            params_ready = bool(rule_agent) and coordinator._params_ready_for(rule_agent)
            route = "hard_route" if params_ready else "llm_orchestration"
            return {
                "elapsed_s": round(time.perf_counter() - started, 2),
                "route": route,
                "route_log_signal": "hard_route" if "[DEPENDENCY]" in logs else "no_dependency_log",
                "rule_agent": rule_agent,
                "params_ready": params_ready,
                "executed_agents": list(results.keys()),
                "parsed": {"station": context.get("station_name"),
                           "start_time": context.get("start_time"),
                           "end_time": context.get("end_time")},
                "answer_chars": len(answer),
                "answer_head": answer[:320],
            }
        except Exception as exc:  # noqa: BLE001
            last_error = f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(5 * (attempt + 1))
    return {"error": last_error}


async def main() -> int:
    from agents.coordinator import LangChainCoordinator

    sessions: dict[str, object] = {}
    rows = []
    for task in TASKS:
        coord = sessions.get(task["session"])
        if coord is None:
            coord = LangChainCoordinator()
            sessions[task["session"]] = coord
        warm = None
        if task["warmup"]:
            warm = await run_one(coord, task["warmup"])
            print(f"  [{task['tag']}] 预热轮：{warm.get('route')} "
                  f"{warm.get('parsed', {}).get('station')} "
                  f"{warm.get('parsed', {}).get('start_time')}", flush=True)
        row = await run_one(coord, task["text"])
        row.update({"tag": task["tag"], "kind": task["kind"], "task": task["text"],
                    "warmup_turn": warm})
        rows.append(row)
        if row.get("error"):
            print(f"  {task['tag']:<26} 失败: {row['error']}", flush=True)
        else:
            print(f"  {task['tag']:<26} {row['route']:<18} 智能体 {len(row['executed_agents'])} 个 "
                  f"| {row['parsed'].get('station')} | {row['parsed'].get('start_time')} | "
                  f"{row['elapsed_s']}s", flush=True)

    payload = {
        "design": {"note": "三种任务形态的端到端轨迹：参数解析、路由分支（确定性硬路由 / LLM 编排）、"
                           "执行的智能体、回答摘要；B 组使用空上下文，C 组为同会话跨维度追问",
                   "model": LangChainCoordinator().model},
        "rows": rows,
    }
    path = save_result("ai_trace", payload)
    print(f"\nsaved -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
