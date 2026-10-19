"""V9.8：输入溯源审计——解释"同配置同窗口为何 F1 会差 0.076"。

审查提出的问题（原文）："V9.5 曾报告 2024 年 254 窗口全年代理标签下新规则 F1 =
0.4602；V9.7 称 V9.5 的提交配置 A 在相同窗口上得到 0.5361，两者相差 0.0759…
应比较两次运行的输入哈希、特征、有效配置、预测标签及评价标签，找出所有发生
变化的窗口。"

**为什么无法逐例回溯历史运行**：V9.5 时代的运行没有记录 `effective_config`、
没有输入哈希、也没有记录气象来源；`v9_governance.json` 里连判定配置都没有
（该字段是 V9.7 才加的）。因此历史数字**按构造不可复现**——这一点必须如实说明，
而不是再去"对齐"它。

**能做的、也是更诚实的做法**：把当时**已知存在但未记录**的输入差异显式复现出来，
量化它对 F1 的影响。已知的头号嫌疑是气象：V9.5 时期每窗口都现场联网取数，本机实测
频繁超时，而 `MeteorologyAgent` 在超时时会**静默降级到兜底 CSV**
（`data/sample_weather_mong_kok.csv`，且是全港单一站点样例，用于三个站都是错位输入）。
本脚本在**同一批 254 窗、同一份捕获组件、同一配置 A**下，只切换气象来源，
输出逐窗差异清单与 F1 变化。
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parents[2]
for path in (str(BASE), str(HERE), str(HERE.parent)):
    if path not in sys.path:
        sys.path.insert(0, path)

import bench_governance_v6 as gv6  # noqa: E402
import governance_sweep as gs  # noqa: E402
from contracts import ContractViolation  # noqa: E402
from metrics import clustered_macro_f1_diff, macro_f1  # noqa: E402

# V9.5 提交配置（git 0a3e681）
CONFIG_A = {"gate_anomaly_min": 0.45, "traffic_gate_uses_no2_signal": False,
            "local_station_term": "none", "min_strength": 0.35, "margin": 0.12,
            "traffic_nox_pct_min": 0.55, "regional_variant": "R1_sync"}


def _install_fallback_meterology() -> None:
    """把气象取数强制指向兜底 CSV（复现"联网失败 → 静默降级"的输入）。"""
    import pandas as pd
    from tools.open_meteo_client import load_fallback_csv
    # 兜底 CSV 路径与 MeteorologyAgent 的常量保持一致
    from agents.meteorology_agent import DEFAULT_FALLBACK_CSV
    import agents.meteorology_agent as met

    frame = load_fallback_csv(DEFAULT_FALLBACK_CSV)
    if frame["time"].dt.tz is None:
        frame["time"] = frame["time"].dt.tz_localize("Asia/Hong_Kong")

    def forced(lat, lon, start_time, end_time, fallback_csv, timezone="Asia/Hong_Kong"):
        start = pd.Timestamp(start_time)
        end = pd.Timestamp(end_time)
        if start.tzinfo is None:
            start, end = start.tz_localize(timezone), end.tz_localize(timezone)
        low, high = start.floor("h"), end.floor("h")
        sliced = frame[(frame["time"] >= low) & (frame["time"] <= high)]
        if sliced.empty:  # 兜底样例覆盖不到该时段时，退化为整份样例
            sliced = frame.copy()
        return sliced.reset_index(drop=True), True, "forced fallback (V9.5-era network failure)"

    met.fetch_with_fallback = forced


def main() -> int:
    from agents import meteorology_agent as met

    rows = gs.load_real("full")
    print(f"真实窗 {len(rows)}", flush=True)

    # ── 1. 真实气象（缓存）下的气象组件，即捕获 CSV 里的值 ──
    real_components = {f"{r['date']}|{r['start_hour']}":
                       (r["static_wind"], r["wind_shift"], r["mean_wind"]) for r in rows}

    # ── 2. 兜底 CSV 气象下的组件 ──
    _install_fallback_meterology()
    station = {s["name"]: s for s in gv6.STATIONS}
    diff_rows = []
    changed = 0
    for row in rows:
        info = station[row["station"]]
        start = f"{row['date']} {row['start_hour']:02d}:00:00"
        end = f"{row['date']} {row['start_hour'] + 2:02d}:59:59"
        out = met.run({"task_id": "fb", "station_id": info["id"], "lat": info["lat"],
                       "lon": info["lon"], "start_time": start, "end_time": end})
        res = out.get("result") or {}
        key = f"{row['date']}|{row['start_hour']}"
        fallback = (res.get("static_wind_score"), res.get("wind_shift_score"),
                    res.get("mean_wind_speed"))
        real = real_components[key]
        differs = any(
            (a is None) != (b is None) or (a is not None and abs(float(a) - float(b)) > 1e-9)
            for a, b in zip(real, fallback))
        if differs:
            changed += 1
        row["fallback_static_wind"] = fallback[0]
        row["fallback_wind_shift"] = fallback[1]
        row["fallback_mean_wind"] = fallback[2]
        diff_rows.append({
            "station": row["station"], "date": row["date"], "start_hour": row["start_hour"],
            "label": row["label"],
            "real_static_wind": real[0], "fallback_static_wind": fallback[0],
            "real_mean_wind": real[2], "fallback_mean_wind": fallback[2],
            "component_changed": differs,
            "pred_real": gs.predict(row, CONFIG_A),
            "pred_fallback": None,  # 稍后填
        })
    print(f"气象组件发生变化的窗口：{changed}/{len(rows)}", flush=True)

    # ── 3. 同一配置 A、只换气象来源 → 预测与 F1 ──
    truths = [r["label"] for r in rows]
    pred_real = [gs.predict(r, CONFIG_A) for r in rows]
    pred_fb = []
    for row, record in zip(rows, diff_rows):
        swapped = dict(row, static_wind=row["fallback_static_wind"],
                       wind_shift=row["fallback_wind_shift"],
                       mean_wind=row["fallback_mean_wind"])
        prediction = gs.predict(swapped, CONFIG_A)
        pred_fb.append(prediction)
        record["pred_fallback"] = prediction
    f1_real = round(macro_f1(truths, pred_real), 4)
    f1_fb = round(macro_f1(truths, pred_fb), 4)
    flips = [record for record, a, b in zip(diff_rows, pred_real, pred_fb) if a != b]
    print(f"配置 A：真实气象 F1 = {f1_real} ｜ 兜底 CSV 气象 F1 = {f1_fb}"
          f"（差 {round(f1_fb - f1_real, 4)}）", flush=True)
    print(f"仅因气象来源而改变判定的窗口：{len(flips)}/{len(rows)}", flush=True)

    for station_name in ("Mong Kok Roadside", "Central Roadside", "Causeway Bay Roadside"):
        idx = [i for i, r in enumerate(rows) if r["station"] == station_name]
        print(f"  {station_name:<24} 真实 {round(macro_f1([truths[i] for i in idx], [pred_real[i] for i in idx]), 4)}"
              f" ｜ 兜底 {round(macro_f1([truths[i] for i in idx], [pred_fb[i] for i in idx]), 4)}", flush=True)

    # ── 3b. 部分降级模拟：反推 V9.5 报告值需要多少比例的窗口气象降级 ──
    import random as _random

    rows_swapped = [dict(row, static_wind=row["fallback_static_wind"],
                         wind_shift=row["fallback_wind_shift"],
                         mean_wind=row["fallback_mean_wind"]) for row in rows]
    simulation = {}
    for rate in (0.05, 0.10, 0.12, 0.13, 0.15, 0.20, 0.25, 0.30):
        scores = []
        for seed in range(10):
            rng = _random.Random(1000 + seed)
            n_degraded = int(round(rate * len(rows)))
            picked = set(rng.sample(range(len(rows)), n_degraded))
            preds = [gs.predict(rows_swapped[i] if i in picked else rows[i], CONFIG_A)
                     for i in range(len(rows))]
            scores.append(macro_f1(truths, preds))
        simulation[f"{int(rate * 100)}%"] = {
            "mean_f1": round(sum(scores) / len(scores), 4),
            "min_f1": round(min(scores), 4), "max_f1": round(max(scores), 4),
            "covers_v95_reported_0_4602": bool(min(scores) <= 0.4602 <= max(scores)),
        }
        print(f"  降级 {int(rate * 100):>3}% → F1 均值 {simulation[f'{int(rate * 100)}%']['mean_f1']}"
              f"（区间 [{simulation[f'{int(rate * 100)}%']['min_f1']}, "
              f"{simulation[f'{int(rate * 100)}%']['max_f1']}]）"
              f" 覆盖 0.4602：{simulation[f'{int(rate * 100)}%']['covers_v95_reported_0_4602']}",
              flush=True)

    # ── 4. 契约：历史运行不可回溯这一点必须显式记录，而不是静默略过 ──
    legacy = BASE / "tests" / "benchmarks" / "results" / "v9_governance.json"
    has_config = False
    if legacy.exists():
        has_config = "effective_config" in (json.loads(legacy.read_text(encoding="utf-8"))
                                           .get("design") or {})
    if not has_config:
        print("注意：历史留出结果未记录 effective_config（V9.7 才加入该字段）→ "
              "历史数字按构造不可复现，只能做机制性归因。", flush=True)

    payload = {
        "question": "同配置同窗口下 F1 差异的来源",
        "config": CONFIG_A,
        "cannot_replay_legacy": {
            "reason": "V9.5 时代运行未记录 effective_config / 输入哈希 / 气象来源",
            "legacy_json_has_effective_config": has_config,
        },
        "meteo_source_swap": {
            "f1_real_meteo": f1_real,
            "f1_fallback_meteo": f1_fb,
            "delta": round(f1_fb - f1_real, 4),
            "windows_with_changed_components": changed,
            "windows_flipped": len(flips),
            "flipped_examples": flips[:10],
        },
        "partial_degradation_simulation": {
            "note": "V9.5 报告值 0.4602 落在哪个降级比例区间内",
            "by_rate": simulation,
        },
        "paired_ci": clustered_macro_f1_diff(
            truths, pred_fb, pred_real, [r["date"] for r in rows], reps=10000),
        "per_window": diff_rows,
    }
    dest = HERE / "results" / "v97_input_provenance_audit.json"
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    per_window = HERE / "results" / "v97_input_provenance_windows.csv"
    with per_window.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(diff_rows[0].keys()))
        writer.writeheader()
        writer.writerows(diff_rows)
    print(f"\nsaved -> {dest}\nsaved -> {per_window}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
