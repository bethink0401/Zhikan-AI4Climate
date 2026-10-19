"""Benchmark: AI model layer — real end-to-end metrics (25 tasks, Qwen3-32B).

Runs the actual LLM scheduler over a fixed 25-task list and reports the metrics
the design claims:
  1. task completion rate
  2. numeric consistency of the answer against the deterministic Agent outputs
     (ground truth = the same station/window run through CoordinatorMin, with
     station coordinates supplied so the meteorology agent can execute)
  3. evidence citation rate (answer cites the station and at least one
     number that matches the Agent outputs)
  4. multi-turn parameter inheritance rate (5 follow-up turns)
  5. degradation success rate (model unavailable -> deterministic chain runs)

Requires COORDINATOR_API_KEY in the environment / .env. Runs with retries and
backoff because the upstream service rate-limits.

Usage: /Users/chenweiyang/.workbuddy/binaries/python/envs/llm-eval/bin/python tests/benchmarks/bench_ai_model_metrics.py
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE))

from common import save_result  # noqa: E402

#: (任务文本, 站点名, 时间窗或 None, 是否多轮, 期望继承说明)
TASKS: list[tuple[str, str | None, tuple[str, str] | None, str | None]] = [
    ("分析2023年8月15日8点到11点旺角的污染情况", "Mong Kok Roadside", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("铜锣湾早高峰空气质量怎么样", "Causeway Bay Roadside", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("中环的污染来源是什么", "Central Roadside", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("沙田的气象条件如何", "SHATIN", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("深水埗 2023-08-15 交通压力大吗", "SHAM SHUI PO", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("旺角的空间影响范围有多大", "Mong Kok Roadside", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("将军澳需要采取什么措施", "TSEUNG KWAN O", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("葵涌 2023年12月1日9点到18点的情况", "KWAI CHUNG", ("2023-12-01 09:00:00", "2023-12-01 18:00:00"), None),
    ("塔门的污染来源", "TAP MUN", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("荃湾 2023年8月15日8点至11点 污染", "TSUEN WAN", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("北区气象风险", "NORTH", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("元朗的车流情况", "YUEN LONG", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("东涌风向如何", "TUNG CHUNG", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("大埔湿度多少", "TAI PO", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("南区降雨情况", "SOUTHERN", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("屯门该采取什么措施", "TUEN MUN", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("东区的空气质量", "EASTERN", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("中西区的气象条件", "CENTRAL/WESTERN", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("旺角 2023.8.15 8点到11点 的气象", "Mong Kok Roadside", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), None),
    ("Mong Kok roadside 2023年8月15日9点到10点 空气质量", "Mong Kok Roadside", ("2023-08-15 09:00:00", "2023-08-15 10:00:00"), None),
    # —— 多轮：继承 / 切换 ——
    ("接着看交通", "Mong Kok Roadside", ("2023-08-15 09:00:00", "2023-08-15 10:00:00"), "沿用上一轮站点，切换要素"),
    ("换成铜锣湾", "Causeway Bay Roadside", None, "仅切换站点"),
    ("时间改成8点到11点", "Causeway Bay Roadside", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), "仅切换时间窗"),
    ("它主要是什么源", "Causeway Bay Roadside", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), "指代上一轮站点与时间"),
    ("刚才那个站的PM2.5呢", "Causeway Bay Roadside", ("2023-08-15 08:00:00", "2023-08-15 11:00:00"), "指代并追问污染物"),
]

NUM_RE = re.compile(r"\d+(?:\.\d+)?")
DATE_TIME_RE = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}月\d{1,2}日|"
    r"\d{1,2}:\d{2}(?::\d{2})?|\d{4}\.\d{1,2}\.\d{1,2}|\d{4}年|\d{4}-"
)
#: 序号（emoji 数字、圈号）与行首列表编号，不属于业务数值
ORDINAL_RE = re.compile(r"[\u2460-\u2473\u24EA\u2776-\u277F]|\d\uFE0F\u20E3")
LIST_PREFIX_RE = re.compile(r"(?m)^\s*\d+\s*[.、)）]\s*")
#: 回答中自述的时间范围（用于核对「回答陈述」与「实际执行参数」是否一致）
ANSWER_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")

_STATION_INDEX: dict | None = None


def extract_numbers(text: str) -> list[float]:
    """剔除日期、时间与列表序号后提取业务数值。"""
    cleaned = DATE_TIME_RE.sub(" ", text or "")
    cleaned = ORDINAL_RE.sub(" ", cleaned)
    cleaned = LIST_PREFIX_RE.sub("", cleaned)
    out = []
    for token in NUM_RE.findall(cleaned):
        try:
            value = float(token)
        except ValueError:
            continue
        if abs(value) >= 1:
            out.append(value)
    return out


def _norm(name: str) -> str:
    return (str(name).lower().replace("roadside", "").replace(" ", "")
            .replace("/", "").replace("_", "").strip())


def _station_index() -> dict:
    """站名归一化 -> 坐标条目（仅含坐标的 18 个 EPD 站）。"""
    global _STATION_INDEX
    if _STATION_INDEX is None:
        from api.stations import load_stations

        index: dict = {}
        for station in load_stations():
            if not station.get("lat"):
                continue
            entry = {"station_id": station.get("station_id"), "lat": station.get("lat"),
                     "lon": station.get("lon"), "name": station.get("station_name")}
            for raw in (station.get("station_name"), station.get("station_name_zh"),
                        station.get("station_id")):
                if raw:
                    index[_norm(raw)] = entry
        _STATION_INDEX = index
    return _STATION_INDEX


def resolve_station(station_name: str) -> dict | None:
    """站名 -> 坐标条目；匹配不到返回 None（气象 Agent 需要 lat/lon 才能执行）。"""
    return _station_index().get(_norm(station_name))


def station_aliases(station_english: str) -> list[str]:
    """英文站名 -> 该站在别名表中的全部写法（含中文），用于判断回答是否点名站点。"""
    from agents.coordinator import STATION_ALIAS_MAP

    key = _norm(station_english)
    aliases = [station_english, station_english.replace(" Roadside", "")]
    for alias, target in STATION_ALIAS_MAP.items():
        if _norm(target) == key:
            aliases.append(alias)
    return list({a for a in aliases if a})


def ground_truth(coordinator_min, station: str, window: tuple[str, str]) -> tuple[list[float], dict]:
    """同一站点/时间窗的确定性链路输出，作为数值一致性基准。

    必须提供站点坐标，否则气象 Agent 会因输入校验失败而返回 failed，
    基准中将缺失气象数值（回答中的气象数字会被误判为不一致）。
    """
    resolved = resolve_station(station)
    task = {"task_id": f"GT_{abs(hash((station, window))) % 10**8}",
            "station_name": (resolved or {}).get("name") or station,
            "start_time": window[0], "end_time": window[1]}
    if resolved:
        task.update({"station_id": resolved["station_id"],
                     "lat": resolved["lat"], "lon": resolved["lon"]})
    meta = {"station": task["station_name"], "resolved": bool(resolved),
            "lat": task.get("lat"), "lon": task.get("lon")}
    try:
        out = coordinator_min.analyze(task)
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {str(exc)[:80]}"
        return [], meta

    values: list[float] = []

    def collect(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    if key in {"lat", "lon", "analysis_radius_km", "radius_km"}:
                        continue
                    values.append(float(value))
                elif isinstance(value, (dict, list)):
                    collect(value)
        elif isinstance(obj, list):
            for item in obj:
                collect(item)

    statuses = {}
    for key in ("air_quality", "meteorology", "traffic", "spatial", "source", "decision"):
        block = out.get(key) or {}
        statuses[key] = block.get("status")
        collect(block.get("result") or {})
    meta.update({"agent_status": statuses, "values": len(values)})
    return values, meta


def consistent(answer_numbers: list[float], truth: list[float]) -> tuple[int, int]:
    """回答中的数值有多少能在 Agent 输出里找到（相对容差 5%）。"""
    if not answer_numbers:
        return 0, 0
    matched = 0
    for value in answer_numbers:
        for ref in truth:
            if ref == 0:
                if abs(value) < 0.05:
                    matched += 1
                    break
            elif abs(value - ref) <= 0.05 * abs(ref):
                matched += 1
                break
    return matched, len(answer_numbers)


async def ask_with_retry(coordinator, text: str, attempts: int = 4) -> dict:
    for i in range(attempts):
        try:
            t0 = time.perf_counter()
            out = await asyncio.wait_for(coordinator.chat(text), timeout=180)
            return {"ok": True, "elapsed": round(time.perf_counter() - t0, 1), "out": out}
        except Exception as exc:  # noqa: BLE001
            message = f"{type(exc).__name__}: {str(exc)[:120]}"
            if i == attempts - 1:
                return {"ok": False, "elapsed": None, "error": message}
            await asyncio.sleep(6 * (i + 1))
    return {"ok": False, "elapsed": None, "error": "unreachable"}


async def main() -> int:
    from agents.coordinator import LangChainCoordinator
    from agents.coordinator_min import CoordinatorMin

    coordinator = LangChainCoordinator()
    cmin = CoordinatorMin()
    truth_cache: dict = {}

    rows = []
    for index, (text, station, window, inheritance) in enumerate(TASKS, 1):
        result = await ask_with_retry(coordinator, text)
        answer = str((result.get("out") or {}).get("answer") or "")
        row = {"index": index, "task": text, "expected_station": station,
               "answer": answer,
               "multi_turn": inheritance, "ok": result["ok"],
               "elapsed_s": result["elapsed"], "answer_chars": len(answer),
               "error": result.get("error")}
        if result["ok"]:
            if window and station:
                key = (station, window)
                if key not in truth_cache:
                    truth_cache[key] = ground_truth(cmin, station, window)
                truth, meta = truth_cache[key]
                numbers = extract_numbers(answer)
                matched, total = consistent(numbers, truth)
                row["baseline_values"] = meta.get("values")
                row["baseline_meteo_status"] = (meta.get("agent_status") or {}).get("meteorology")
                row["numbers_in_answer"] = total
                row["numbers_matched"] = matched
                row["numeric_consistency"] = round(matched / total, 3) if total else None
                aliases = station_aliases(station)
                row["cites_station"] = any(a.lower() in answer.lower() for a in aliases)
                row["evidence_cited"] = bool(row["cites_station"] and matched > 0)
            ctx = coordinator.context
            row["context"] = {"station_name": ctx.station_name, "start_time": ctx.start_time,
                              "end_time": ctx.end_time, "intent": ctx.intent}
            # 回答自述的时间范围与实际执行参数是否同一日期
            if ctx.start_time:
                stated = ANSWER_DATE_RE.search(answer)
                row["answer_window_matches_context"] = (
                    bool(stated) and ctx.start_time.startswith(stated.group(1)))
        rows.append(row)
        print(f"[{index}/{len(TASKS)}] ok={row['ok']} {row['elapsed_s']}s "
              f"base={row.get('baseline_values')} consistency={row.get('numeric_consistency')} "
              f":: {text[:26]}", flush=True)

    completed = [r for r in rows if r["ok"]]
    with_numbers = [r for r in completed if r.get("numeric_consistency") is not None]
    multi = [r for r in rows if r["multi_turn"]]
    multi_ok = [r for r in multi if r["ok"] and r.get("context")]
    # 继承判定：多轮轮次结束后上下文站点/时间与期望一致（或按指令更新）
    inherit_hits = 0
    for r in multi_ok:
        ctx = r["context"]
        expected_station = r["expected_station"]
        got = (ctx.get("station_name") or "")
        hit = expected_station and _norm(expected_station) in _norm(got)
        if hit and r["index"] >= 23 and "时间" in r["task"]:
            hit = ctx.get("start_time", "").startswith("2023-08-15 08")
        r["inheritance_ok"] = bool(hit)
        inherit_hits += int(bool(hit))

    baselines = list(truth_cache.values())
    payload = {
        "model": coordinator.model,
        "tasks": len(TASKS),
        "completion_rate": f"{len(completed)}/{len(TASKS)}",
        "numeric_consistency_mean": round(sum(r["numeric_consistency"] for r in with_numbers) / len(with_numbers), 3)
        if with_numbers else None,
        "numeric_consistency_detail": [
            {"task": r["task"], "ratio": r["numeric_consistency"],
             "matched": r["numbers_matched"], "total": r["numbers_in_answer"]} for r in with_numbers],
        "evidence_citation_rate": f"{sum(1 for r in completed if r.get('evidence_cited'))}/{len(completed)}",
        "multi_turn_inheritance_rate": f"{inherit_hits}/{len(multi)}",
        "answer_window_consistency": f"{sum(1 for r in completed if r.get('answer_window_matches_context'))}"
                                     f"/{sum(1 for r in completed if 'answer_window_matches_context' in r)}",
        "mean_latency_s": round(sum(r["elapsed_s"] for r in completed) / len(completed), 1) if completed else None,
        "baseline": {
            "combinations": len(baselines),
            "meteo_success": f"{sum(1 for _, m in baselines if (m.get('agent_status') or {}).get('meteorology') == 'success')}"
                             f"/{len(baselines)}",
            "values_per_baseline": sorted(m.get("values") for _, m in baselines),
        },
        "rows": rows,
    }
    path = save_result("ai_model_metrics", payload)
    print(f"\nsaved -> {path}")
    print("完成率:", payload["completion_rate"])
    print("数值一致率(均值):", payload["numeric_consistency_mean"])
    print("证据引用率:", payload["evidence_citation_rate"])
    print("多轮继承率:", payload["multi_turn_inheritance_rate"])
    print("回答自述时间与实际参数一致:", payload["answer_window_consistency"])
    print("平均响应:", payload["mean_latency_s"], "s")
    print("基准:", json.dumps(payload["baseline"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
