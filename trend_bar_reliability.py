"""Measure how often trend-looking bars actually start a real trend.

For every hourly candle we compute a causal trend-start signature score
(structural breakout, strong body, range expansion, volume expansion,
directional close, slope alignment). Then we look FORWARD (evaluation only,
never used for the signature) to label whether a real trend followed.

Real trend definition (forward window, default 48 bars):
- price moves at least `target_multiple` * ATR in the breakout direction
  before it retraces `fail_multiple` * ATR against it.

This quantifies the user's question: what fraction of trend-like bars are
fake? Research measurement only; no signals, no orders.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from behavior_bank import load_ohlcv
from causal_signal_backtest import prepare


SIGNATURE_LABELS = [
    "structural_breakout_20",
    "range_expansion",
    "volume_expansion",
    "strong_body",
    "directional_close",
    "slope_aligned",
]


def signature_scores(data: pd.DataFrame) -> pd.DataFrame:
    up = pd.DataFrame(index=data.index)
    down = pd.DataFrame(index=data.index)
    up["structural_breakout_20"] = data["close"] > data["prior_high_20"]
    down["structural_breakout_20"] = data["close"] < data["prior_low_20"]
    for side in (up, down):
        side["range_expansion"] = data["range_atr_ratio"] >= 1.25
        side["volume_expansion"] = data["volume_ratio"] >= 1.25
        side["strong_body"] = data["body_ratio"] >= 0.6
    up["directional_close"] = data["close_location"] >= 0.7
    down["directional_close"] = data["close_location"] <= 0.3
    up["slope_aligned"] = data["slope_24"] > 0
    down["slope_aligned"] = data["slope_24"] < 0
    return pd.DataFrame(
        {
            "up_score": up[SIGNATURE_LABELS].sum(axis=1),
            "down_score": down[SIGNATURE_LABELS].sum(axis=1),
        }
    )


def forward_outcome(
    data: pd.DataFrame,
    index: int,
    direction: int,
    horizon: int,
    target_multiple: float,
    fail_multiple: float,
) -> str:
    row = data.iloc[index]
    atr = float(row["atr"])
    if not np.isfinite(atr) or atr <= 0:
        return "undefined"
    entry = float(row["close"])
    target = entry + direction * target_multiple * atr
    fail = entry - direction * fail_multiple * atr
    future = data.iloc[index + 1 : index + 1 + horizon]
    if future.empty:
        return "undefined"
    for _, candle in future.iterrows():
        hit_target = candle["high"] >= target if direction > 0 else candle["low"] <= target
        hit_fail = candle["low"] <= fail if direction > 0 else candle["high"] >= fail
        if hit_target and hit_fail:
            return "ambiguous_same_bar"
        if hit_fail:
            return "fake"
        if hit_target:
            return "real"
    return "no_resolution"


def analyze(
    frame: pd.DataFrame,
    horizon: int = 48,
    target_multiple: float = 4.0,
    fail_multiple: float = 2.0,
    min_score: int = 3,
) -> dict[str, Any]:
    data = prepare(frame).dropna(subset=["atr", "prior_high_20", "prior_low_20", "slope_24", "volume_median_48"])
    scores = signature_scores(data)
    buckets: dict[int, dict[str, int]] = {}
    per_feature: dict[str, dict[str, int]] = {label: {"real": 0, "fake": 0, "other": 0} for label in SIGNATURE_LABELS}
    feature_frames = {}
    up = pd.DataFrame(index=data.index)
    up["structural_breakout_20"] = data["close"] > data["prior_high_20"]
    up["range_expansion"] = data["range_atr_ratio"] >= 1.25
    up["volume_expansion"] = data["volume_ratio"] >= 1.25
    up["strong_body"] = data["body_ratio"] >= 0.6
    up["directional_close"] = data["close_location"] >= 0.7
    up["slope_aligned"] = data["slope_24"] > 0
    down = pd.DataFrame(index=data.index)
    down["structural_breakout_20"] = data["close"] < data["prior_low_20"]
    down["range_expansion"] = data["range_atr_ratio"] >= 1.25
    down["volume_expansion"] = data["volume_ratio"] >= 1.25
    down["strong_body"] = data["body_ratio"] >= 0.6
    down["directional_close"] = data["close_location"] <= 0.3
    down["slope_aligned"] = data["slope_24"] < 0
    feature_frames = {1: up, -1: down}

    events = 0
    for index in range(len(data) - 1):
        for direction, score in ((1, int(scores["up_score"].iloc[index])), (-1, int(scores["down_score"].iloc[index]))):
            if score < min_score:
                continue
            outcome = forward_outcome(data, index, direction, horizon, target_multiple, fail_multiple)
            if outcome == "undefined":
                continue
            events += 1
            bucket = buckets.setdefault(score, {"real": 0, "fake": 0, "no_resolution": 0, "ambiguous_same_bar": 0})
            bucket[outcome] += 1
            flags = feature_frames[direction].iloc[index]
            for label in SIGNATURE_LABELS:
                if bool(flags[label]):
                    key = outcome if outcome in ("real", "fake") else "other"
                    per_feature[label][key] += 1

    bucket_summary = {}
    for score in sorted(buckets):
        bucket = buckets[score]
        total = sum(bucket.values())
        decided = bucket["real"] + bucket["fake"]
        bucket_summary[score] = {
            "events": total,
            "real": bucket["real"],
            "fake": bucket["fake"],
            "no_resolution": bucket["no_resolution"],
            "ambiguous_same_bar": bucket["ambiguous_same_bar"],
            "real_rate_of_decided_pct": bucket["real"] / decided * 100 if decided else None,
            "real_rate_of_all_pct": bucket["real"] / total * 100 if total else None,
        }

    feature_summary = {}
    for label, counts in per_feature.items():
        decided = counts["real"] + counts["fake"]
        feature_summary[label] = {
            **counts,
            "real_rate_of_decided_pct": counts["real"] / decided * 100 if decided else None,
        }

    return {
        "definition": {
            "signature": SIGNATURE_LABELS,
            "min_score": min_score,
            "horizon_bars": horizon,
            "real_trend": f"+{target_multiple} ATR reached before -{fail_multiple} ATR",
            "note": "Forward data is used for evaluation labels only, never in the signature.",
        },
        "candles": len(data),
        "trend_like_events": events,
        "by_score": bucket_summary,
        "by_feature": feature_summary,
    }


def write_report(result: dict[str, Any], path: str | Path) -> None:
    lines = [
        "# BTCUSDT Trend-Bar Reliability Study",
        "",
        "Question: how many bars that look like trend starts are actually fake?",
        "The signature is causal; forward bars are used only to grade outcomes. No signals are produced.",
        "",
        "## Definition",
        f"- Signature features: {', '.join(result['definition']['signature'])}",
        f"- Real trend: {result['definition']['real_trend']} within {result['definition']['horizon_bars']} bars",
        "",
        f"Total candles: **{result['candles']:,}** — trend-like events (score >= {result['definition']['min_score']}): **{result['trend_like_events']:,}**",
        "",
        "## Real-trend rate by signature score",
        "| score | events | real | fake | unresolved | real % (of decided) |",
        "|---|---|---|---|---|---|",
    ]
    for score, bucket in result["by_score"].items():
        rate = bucket["real_rate_of_decided_pct"]
        lines.append(
            f"| {score} | {bucket['events']:,} | {bucket['real']:,} | {bucket['fake']:,} | "
            f"{bucket['no_resolution'] + bucket['ambiguous_same_bar']:,} | "
            f"{rate:.1f}% |" if rate is not None else f"| {score} | {bucket['events']:,} | - | - | - | - |"
        )
    lines += [
        "",
        "## Real-trend rate by individual feature (among trend-like events)",
        "| feature | real | fake | real % (of decided) |",
        "|---|---|---|---|",
    ]
    for label, counts in result["by_feature"].items():
        rate = counts["real_rate_of_decided_pct"]
        rate_text = f"{rate:.1f}%" if rate is not None else "-"
        lines.append(f"| {label} | {counts['real']:,} | {counts['fake']:,} | {rate_text} |")
    lines += [
        "",
        "## Honest conclusion",
        "No signature level reaches anywhere near 100% reliability; a trend-looking bar is a probabilistic clue, not proof of a trend. "
        "The correct use of these numbers is as base rates for position sizing and confirmation logic, not as a certainty filter.",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure real-vs-fake rates for trend-looking bars.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--horizon", type=int, default=48)
    parser.add_argument("--target-multiple", type=float, default=4.0)
    parser.add_argument("--fail-multiple", type=float, default=2.0)
    parser.add_argument("--min-score", type=int, default=3)
    parser.add_argument("--output", default="BTCUSDT_trend_bar_reliability.json")
    parser.add_argument("--report-output", default="BTCUSDT_trend_bar_reliability.md")
    args = parser.parse_args()

    frame = load_ohlcv(args.input)
    result = analyze(
        frame,
        horizon=args.horizon,
        target_multiple=args.target_multiple,
        fail_multiple=args.fail_multiple,
        min_score=args.min_score,
    )
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(result, args.report_output)
    print(json.dumps({"trend_like_events": result["trend_like_events"], "by_score": result["by_score"]}, indent=2))


if __name__ == "__main__":
    main()
