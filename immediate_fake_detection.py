"""Stage-3 reliability study: immediate fakes and early correction warnings.

New definition requested by the user:
- A trend-looking bar is COMPLETELY FAKE only if it dies on the very next
  bar or at most the second bar, before ever reaching a first correction.
- Any move that survives to the first correction is NOT fake; there the
  question becomes whether trend weakness / correction strength can be
  read early (in the first 1-2 bars of the correction) to avoid losses.

Measured here:
1. % of trend-like events that die at bar 1 or bar 2 (close returns
   through the signal-bar close), split into hard fakes (signal-bar
   opposite extreme broken within 5 bars) and stalls.
2. Which features of confirmation bar 1 (observable at its close, fully
   causal for a bar-2 decision) separate immediate fakes from continuers.
3. For moves that reached a first correction, which features of the
   correction's FIRST TWO BARS separate corrections the trend survives
   from corrections that reverse the trend or fade it into a range.

Forward candles are used only for grading. No trading signals produced.
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
from trend_bar_reliability import signature_scores
from trend_follow_through import follow_through_length, correction_walk


def bar1_features(data: pd.DataFrame, index: int, direction: int) -> dict[str, float]:
    sig = data.iloc[index]
    bar1 = data.iloc[index + 1]
    rng = float(bar1["high"] - bar1["low"])
    body = float(abs(bar1["close"] - bar1["open"]))
    close_loc = float((bar1["close"] - bar1["low"]) / rng) if rng > 0 else 0.5
    if direction < 0:
        close_loc = 1.0 - close_loc
    directional_body = (bar1["close"] > bar1["open"]) if direction > 0 else (bar1["close"] < bar1["open"])
    new_extreme = (bar1["high"] > sig["high"]) if direction > 0 else (bar1["low"] < sig["low"])
    midpoint = (float(sig["high"]) + float(sig["low"])) / 2.0
    closed_past_mid = (bar1["close"] < midpoint) if direction > 0 else (bar1["close"] > midpoint)
    return {
        "close_location": close_loc,
        "body_ratio": body / rng if rng > 0 else 0.0,
        "directional_body": float(bool(directional_body)),
        "made_new_extreme": float(bool(new_extreme)),
        "closed_back_past_signal_mid": float(bool(closed_past_mid)),
        "volume_vs_signal": float(bar1["volume"] / sig["volume"]) if sig["volume"] > 0 else np.nan,
        "range_atr": rng / float(sig["atr"]) if sig["atr"] > 0 else np.nan,
    }


def early_correction_features(data: pd.DataFrame, corr_start: int, direction: int, atr: float, pre_volume: float) -> dict[str, float]:
    bars = data.iloc[corr_start : corr_start + 2]
    if bars.empty:
        return {}
    opposing = bars["close"] < bars["open"] if direction > 0 else bars["close"] > bars["open"]
    depth = 0.0
    first_high = float(data["high"].iloc[max(0, corr_start - 1)])
    first_low = float(data["low"].iloc[max(0, corr_start - 1)])
    if direction > 0:
        depth = (first_high - float(bars["low"].min())) / atr if atr > 0 else np.nan
    else:
        depth = (float(bars["high"].max()) - first_low) / atr if atr > 0 else np.nan
    rng = (bars["high"] - bars["low"]).replace(0, np.nan)
    close_loc = ((bars["close"] - bars["low"]) / rng).mean()
    if direction < 0:
        close_loc = 1.0 - close_loc
    body_ratio = (abs(bars["close"] - bars["open"]) / rng).mean()
    return {
        "first2_depth_atr": float(depth),
        "first2_opposing_body_share": float(opposing.mean()),
        "first2_volume_vs_pre_move": float(bars["volume"].mean() / pre_volume) if pre_volume > 0 else np.nan,
        "first2_close_location_trendward": float(close_loc) if np.isfinite(close_loc) else np.nan,
        "first2_opposing_body_ratio": float(body_ratio) if np.isfinite(body_ratio) else np.nan,
    }


def group_stats(rows: list[dict[str, float]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    frame = pd.DataFrame(rows)
    out: dict[str, Any] = {"count": len(frame)}
    for col in frame.columns:
        series = pd.to_numeric(frame[col], errors="coerce").dropna()
        if len(series):
            out[col] = {"mean": float(series.mean()), "median": float(series.median())}
    return out


def analyze(frame: pd.DataFrame, min_score: int = 3) -> dict[str, Any]:
    data = prepare(frame).dropna(subset=["atr", "prior_high_20", "prior_low_20", "slope_24", "volume_median_48"]).reset_index(drop=True)
    scores = signature_scores(data)

    counts = {"immediate_fake_bar1": 0, "immediate_fake_bar2": 0, "survived_to_move": 0}
    hard_fake = 0
    fake_bar1_rows: list[dict[str, float]] = []
    continue_bar1_rows: list[dict[str, float]] = []
    events = []

    for index in range(len(data) - 6):
        for direction in (1, -1):
            score = int((scores["up_score"] if direction > 0 else scores["down_score"]).iloc[index])
            if score < min_score:
                continue
            atr = float(data["atr"].iloc[index])
            if not np.isfinite(atr) or atr <= 0:
                continue
            length = follow_through_length(data, index, direction)
            feats = bar1_features(data, index, direction)
            if length == 0:
                counts["immediate_fake_bar1"] += 1
                fake_bar1_rows.append(feats)
            elif length == 1:
                counts["immediate_fake_bar2"] += 1
                fake_bar1_rows.append(feats)
            else:
                counts["survived_to_move"] += 1
                continue_bar1_rows.append(feats)
                events.append((index, direction))
            if length <= 1:
                sig = data.iloc[index]
                fut = data.iloc[index + 1 : index + 6]
                broke = (fut["low"] < sig["low"]).any() if direction > 0 else (fut["high"] > sig["high"]).any()
                if broke:
                    hard_fake += 1

    total = sum(counts.values())

    # first-correction early evidence, sampled for runtime
    step = max(1, len(events) // 4000)
    survived_rows: list[dict[str, float]] = []
    killed_rows: list[dict[str, float]] = []
    first_corr_fates = {"continuation": 0, "reversal": 0, "range": 0, "no_correction": 0}
    for index, direction in events[::step]:
        atr = float(data["atr"].iloc[index])
        pre_volume = float(data["volume"].iloc[max(0, index - 20) : index + 1].mean())
        walk = correction_walk(data, index, direction)
        corrs = walk["corrections"]
        if not corrs:
            first_corr_fates["no_correction"] += 1
            continue
        first = corrs[0]
        first_corr_fates[first["fate"]] += 1
        # locate the first correction start again to read its first 2 bars
        # (correction_walk does not return positions, so recompute)
        extreme = float(data["close"].iloc[index])
        pos = index + 1
        corr_start = None
        end = min(len(data), index + 1 + 240)
        while pos < end:
            row = data.iloc[pos]
            if direction > 0:
                extreme = max(extreme, float(row["high"]))
                if extreme - float(row["low"]) >= atr:
                    corr_start = pos
                    break
            else:
                extreme = min(extreme, float(row["low"]))
                if float(row["high"]) - extreme >= atr:
                    corr_start = pos
                    break
            pos += 1
        if corr_start is None:
            continue
        early = early_correction_features(data, corr_start, direction, atr, pre_volume)
        if not early:
            continue
        if first["fate"] == "continuation":
            survived_rows.append(early)
        else:
            killed_rows.append(early)

    fake_stats = group_stats(fake_bar1_rows)
    cont_stats = group_stats(continue_bar1_rows)

    return {
        "definition": {
            "min_score": min_score,
            "immediate_fake": "close returns through the signal-bar close on bar 1 or bar 2 (never reaches a correction)",
            "hard_fake": "an immediate fake that also breaks the signal bar's opposite extreme within 5 bars",
            "survived": "closes held beyond the signal close for at least 2 bars; graded at its first correction",
            "note": "Bar-1 features are observable at bar-1 close (causal for a bar-2 decision). Forward data used only for grading.",
        },
        "trend_like_events": total,
        "counts": counts,
        "immediate_fake_pct": (counts["immediate_fake_bar1"] + counts["immediate_fake_bar2"]) / total * 100 if total else None,
        "fake_bar1_pct": counts["immediate_fake_bar1"] / total * 100 if total else None,
        "fake_bar2_pct": counts["immediate_fake_bar2"] / total * 100 if total else None,
        "hard_fake_share_of_immediate_fakes_pct": hard_fake / (counts["immediate_fake_bar1"] + counts["immediate_fake_bar2"]) * 100
        if (counts["immediate_fake_bar1"] + counts["immediate_fake_bar2"])
        else None,
        "bar1_features": {"immediate_fakes": fake_stats, "survivors": cont_stats},
        "first_correction_fates_sampled": first_corr_fates,
        "first_correction_early_evidence": {
            "trend_survived": group_stats(survived_rows),
            "trend_killed": group_stats(killed_rows),
        },
    }


def write_report(result: dict[str, Any], path: str | Path) -> None:
    lines = [
        "# BTCUSDT Immediate-Fake Detection Study",
        "",
        "Per the user's redefinition: a trend-looking bar is completely fake only if it",
        "dies at bar 1 or at most bar 2. Moves that reach the first correction are not",
        "fake; there we look for early evidence of trend weakness / correction strength.",
        "",
        "## Definitions",
    ]
    for key, value in result["definition"].items():
        lines.append(f"- **{key}**: {value}")
    c = result["counts"]
    lines += [
        "",
        f"Trend-like events: **{result['trend_like_events']:,}**",
        "",
        "## How many are completely fake?",
        f"- died on bar 1: {c['immediate_fake_bar1']:,} ({result['fake_bar1_pct']:.1f}%)",
        f"- died on bar 2: {c['immediate_fake_bar2']:,} ({result['fake_bar2_pct']:.1f}%)",
        f"- **total immediate fakes: {result['immediate_fake_pct']:.1f}%**",
        f"- of those, hard fakes (opposite extreme broken within 5 bars): {result['hard_fake_share_of_immediate_fakes_pct']:.1f}%",
        f"- survived at least 2 bars: {c['survived_to_move']:,} ({c['survived_to_move'] / result['trend_like_events'] * 100:.1f}%)",
        "",
        "## Bar-1 features: immediate fakes vs survivors",
        "| feature | fakes mean | survivors mean |",
        "|---|---|---|",
    ]
    fakes = result["bar1_features"]["immediate_fakes"]
    surv = result["bar1_features"]["survivors"]
    for col in ("close_location", "body_ratio", "directional_body", "made_new_extreme", "closed_back_past_signal_mid", "volume_vs_signal", "range_atr"):
        if col in fakes and col in surv:
            lines.append(f"| {col} | {fakes[col]['mean']:.3f} | {surv[col]['mean']:.3f} |")
    fates = result["first_correction_fates_sampled"]
    lines += [
        "",
        "## First-correction fates (sampled survivors)",
        f"- continuation: {fates['continuation']:,}",
        f"- reversal: {fates['reversal']:,}",
        f"- range: {fates['range']:,}",
        f"- no correction within window: {fates['no_correction']:,}",
        "",
        "## Early evidence inside the first TWO bars of the first correction",
        "| feature | trend survived (mean) | trend killed (mean) |",
        "|---|---|---|",
    ]
    ok = result["first_correction_early_evidence"]["trend_survived"]
    bad = result["first_correction_early_evidence"]["trend_killed"]
    for col in ("first2_depth_atr", "first2_opposing_body_share", "first2_volume_vs_pre_move", "first2_close_location_trendward", "first2_opposing_body_ratio"):
        if col in ok and col in bad:
            lines.append(f"| {col} | {ok[col]['mean']:.3f} | {bad[col]['mean']:.3f} |")
    lines += [
        "",
        "## Honest notes",
        "- Descriptive statistics only; no signals, no orders.",
        "- Bar-1 features imply a decision made AFTER bar 1 closes (one-bar confirmation).",
        "- Delta/taker-buy columns in the source parquet are invalid and were not used.",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Immediate-fake and early-correction-warning study.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--min-score", type=int, default=3)
    parser.add_argument("--output", default="BTCUSDT_immediate_fake_detection.json")
    parser.add_argument("--report-output", default="BTCUSDT_immediate_fake_detection.md")
    args = parser.parse_args()

    frame = load_ohlcv(args.input)
    result = analyze(frame, min_score=args.min_score)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(result, args.report_output)
    print(json.dumps({
        "trend_like_events": result["trend_like_events"],
        "immediate_fake_pct": result["immediate_fake_pct"],
        "counts": result["counts"],
        "first_correction_fates_sampled": result["first_correction_fates_sampled"],
    }, indent=2))


if __name__ == "__main__":
    main()
