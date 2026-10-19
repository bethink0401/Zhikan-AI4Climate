"""Benchmark: recommended plans vs naive travel baselines.

Reads the validated travel case output (examples/travel_case_output.json)
and computes the exposure / departure-time numbers used in the competition
deck. Baselines model what a user without exposure information would do:

  naive mode  : default/naive mode choice (walking or transit) vs the
                recommended plan
  naive time  : departing at the worst hour of the window vs the best
  worst option: the highest-intake option offered vs the recommended one

Usage: python3 tests/benchmark_travel_vs_naive.py
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASE_FILE = os.path.join(BASE, "examples", "travel_case_output.json")


def collect_runs(doc):
    for scenario in doc.get("scenarios", []):
        for case in scenario.get("cases", []):
            for profile, run in case.get("runs", {}).items():
                yield scenario, case, profile, run


def main():
    with open(CASE_FILE) as f:
        doc = json.load(f)

    mode_gaps = []        # naive walking/transit intake vs recommended
    time_gaps = []        # departure-window best vs worst intake
    worst_gaps = []       # worst offered option vs recommended
    alt_route_gaps = []   # same-mode alternative route vs recommended
    shares = []
    n_runs = 0

    for scenario, case, profile, run in collect_runs(doc):
        result = run.get("result") or {}
        options = result.get("route_options") or []
        rec_id = (result.get("recommendation") or {}).get("route_id")
        rec = next((o for o in options if o.get("route_id") == rec_id), None)
        if not rec or len(options) < 2:
            continue
        n_runs += 1
        rec_intake = rec.get("intake_index") or 0.0

        # naive mode choice: best non-recommended alternative of another mode
        others = [o for o in options if o.get("mode") != rec.get("mode")]
        if others and rec_intake > 0:
            naive = min(others, key=lambda o: o.get("intake_index") or 0.0)
            naive_intake = naive.get("intake_index") or 0.0
            if naive_intake > 0:
                mode_gaps.append((naive_intake - rec_intake) / rec_intake * 100.0)

        # same-mode alternative route (choosing a parallel street)
        alts = [o for o in options
                if o.get("mode") == rec.get("mode")
                and o.get("route_id") != rec_id]
        if alts and rec_intake > 0:
            alt = alts[0]
            alt_intake = alt.get("intake_index") or 0.0
            if alt_intake > 0:
                alt_route_gaps.append((alt_intake - rec_intake) / rec_intake * 100.0)

        # worst offered option
        worst = max(options, key=lambda o: o.get("intake_index") or 0.0)
        worst_intake = worst.get("intake_index") or 0.0
        if rec_intake > 0 and worst_intake > 0:
            worst_gaps.append((worst_intake - rec_intake) / rec_intake * 100.0)

        # departure-time curve
        curve = result.get("departure_dose_curve") or {}
        spread = (curve.get("intake_range") or {}).get("reduction_pct")
        if spread is not None:
            time_gaps.append(spread)

        unc = result.get("uncertainty") or {}
        if unc.get("recommended_share") is not None:
            shares.append(unc["recommended_share"])

    def stats(values, fmt="{:+.1f}%"):
        values = sorted(values)
        n = len(values)
        if not n:
            return "n/a"
        mean = sum(values) / n
        median = values[n // 2]
        return (fmt.format(mean) + " (median " + fmt.format(median)
                + f", range {fmt.format(values[0])}..{fmt.format(values[-1])}, n={n})")

    print(f"runs evaluated: {n_runs} "
          f"(scenarios x directions x profiles from travel_case_output.json)")
    print()
    print("[1] naive mode choice (best non-recommended mode) vs recommended plan")
    print(f"    extra intake of the naive choice: {stats(mode_gaps)}")
    print()
    print("[2] same-mode parallel route vs recommended route")
    print(f"    extra intake of the parallel route: {stats(alt_route_gaps)}")
    print()
    print("[3] worst offered option vs recommended plan")
    print(f"    extra intake of the worst option: {stats(worst_gaps)}")
    print()
    print("[4] departure-window optimisation (best vs worst hour, per run)")
    print(f"    intake reduction: {stats(time_gaps)}")
    print()
    print("[5] monte-carlo robustness (recommended stays first)")
    print(f"    recommended share: {stats(shares, '{:.2f}')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
