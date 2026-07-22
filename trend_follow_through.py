"""Stage-2 reliability study: follow-through and the fate of corrections.

The first study graded trend-looking bars with a strict +4/-2 ATR rule.
Here we relax the standard, as requested:

1. Of all trend-like bars (score >= min_score), how many kept moving in
   their direction for MORE THAN 4 bars (closes never violating the
   signal bar close within the first bars)?
2. For moves that did follow through, walk forward correction by
   correction and classify the fate at each correction:
   - continuation: a new extreme beyond the pre-correction extreme
   - reversal: the correction breaks the structural origin of the move
   - range: neither happens within the resolution window
3. Compare measurable properties of corrections that killed the trend
   (reversal/range) versus corrections the trend survived.

All signature features are causal; forward candles are used only to
grade outcomes. No trading signals are produced.
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


def follow_through_length(data: pd.DataFrame, index: int, direction: int, max_bars: int = 48) -> int:
    """Bars after the signal bar until close first violates the signal close."""
    entry = float(data["close"].iloc[index])
    count = 0
    for offset in range(1, max_bars + 1):
        pos = index + offset
        if pos >= len(data):
            break
        close = float(data["close"].iloc[pos])
        if direction > 0 and close <= entry:
            break
        if direction < 0 and close >= entry:
            break
        count += 1
    return count


def correction_walk(
    data: pd.DataFrame,
    index: int,
    direction: int,
    pullback_atr: float = 1.0,
    resolve_bars: int = 24,
    max_bars: int = 240,
) -> dict[str, Any]:
    """Walk forward from a followed-through signal bar and grade each correction.

    A correction starts when price pulls back >= pullback_atr * ATR from the
    running extreme. Its fate:
    - continuation: a new extreme beyond the pre-correction extreme occurs
      within resolve_bars after the correction low/high forms
    - reversal: price breaks the move origin (signal bar's opposite side)
      before making a new extreme
    - range: neither within resolve_bars
    """
    atr = float(data["atr"].iloc[index])
    origin = float(data["low"].iloc[index]) if direction > 0 else float(data["high"].iloc[index])
    extreme = float(data["close"].iloc[index])
    corrections: list[dict[str, Any]] = []
    pos = index + 1
    end = min(len(data), index + 1 + max_bars)
    pre_volume = float(data["volume"].iloc[max(0, index - 20) : index + 1].mean())

    while pos < end:
        # advance until a pullback of pullback_atr * ATR from running extreme
        corr_start = None
        while pos < end:
            row = data.iloc[pos]
            if direction > 0:
                extreme = max(extreme, float(row["high"]))
                if extreme - float(row["low"]) >= pullback_atr * atr:
                    corr_start = pos
                    break
            else:
                extreme = min(extreme, float(row["low"]))
                if float(row["high"]) - extreme >= pullback_atr * atr:
                    corr_start = pos
                    break
            pos += 1
        if corr_start is None:
            break

        pre_corr_extreme = extreme
        deepest = float(data["low"].iloc[corr_start]) if direction > 0 else float(data["high"].iloc[corr_start])
        fate = "range"
        fate_pos = None
        corr_end = corr_start
        scan_limit = min(end, corr_start + resolve_bars)
        scan = corr_start
        while scan < scan_limit:
            row = data.iloc[scan]
            if direction > 0:
                deepest = min(deepest, float(row["low"]))
                if float(row["low"]) <= origin:
                    fate, fate_pos = "reversal", scan
                    break
                if float(row["high"]) > pre_corr_extreme:
                    fate, fate_pos = "continuation", scan
                    break
            else:
                deepest = max(deepest, float(row["high"]))
                if float(row["high"]) >= origin:
                    fate, fate_pos = "reversal", scan
                    break
                if float(row["low"]) < pre_corr_extreme:
                    fate, fate_pos = "continuation", scan
                    break
            scan += 1
        corr_end = fate_pos if fate_pos is not None else scan_limit - 1

        depth_atr = abs(pre_corr_extreme - deepest) / atr if atr > 0 else np.nan
        move_size = abs(pre_corr_extreme - float(data["close"].iloc[index]))
        depth_pct_of_move = abs(pre_corr_extreme - deepest) / move_size * 100 if move_size > 0 else np.nan
        corr_slice = data.iloc[corr_start : corr_end + 1]
        corr_volume = float(corr_slice["volume"].mean()) if len(corr_slice) else np.nan
        opposing = corr_slice["close"] < corr_slice["open"] if direction > 0 else corr_slice["close"] > corr_slice["open"]
        corrections.append(
            {
                "number": len(corrections) + 1,
                "fate": fate,
                "duration_bars": int(corr_end - corr_start + 1),
                "depth_atr": float(depth_atr),
                "depth_pct_of_move": float(depth_pct_of_move) if np.isfinite(depth_pct_of_move) else None,
                "volume_vs_pre_move": float(corr_volume / pre_volume) if pre_volume > 0 and np.isfinite(corr_volume) else None,
                "opposing_body_share": float(opposing.mean()) if len(corr_slice) else None,
            }
        )
        if fate != "continuation":
            break
        pos = corr_end + 1
        extreme = pre_corr_extreme

    return {"corrections": corrections}


def summarize_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"count": 0}
    frame = pd.DataFrame(rows)
    out: dict[str, Any] = {"count": len(frame)}
    for col in ("duration_bars", "depth_atr", "depth_pct_of_move", "volume_vs_pre_move", "opposing_body_share"):
        series = pd.to_numeric(frame[col], errors="coerce").dropna()
        if len(series):
            out[col] = {"mean": float(series.mean()), "median": float(series.median())}
    return out


def analyze(frame: pd.DataFrame, min_score: int = 3) -> dict[str, Any]:
    data = prepare(frame).dropna(subset=["atr", "prior_high_20", "prior_low_20", "slope_24", "volume_median_48"]).reset_index(drop=True)
    scores = signature_scores(data)

    ft_by_score: dict[int, dict[str, int]] = {}
    all_corrections: list[dict[str, Any]] = []
    death_by_correction_number: dict[int, dict[str, int]] = {}
    survived_counts = {"continuation": 0, "reversal": 0, "range": 0}
    followed_events = []

    for index in range(len(data) - 5):
        for direction in (1, -1):
            score = int((scores["up_score"] if direction > 0 else scores["down_score"]).iloc[index])
            if score < min_score:
                continue
            atr = float(data["atr"].iloc[index])
            if not np.isfinite(atr) or atr <= 0:
                continue
            length = follow_through_length(data, index, direction)
            bucket = ft_by_score.setdefault(score, {"events": 0, "more_than_4": 0})
            bucket["events"] += 1
            if length > 4:
                bucket["more_than_4"] += 1
                followed_events.append((index, direction, score))

    # sample the correction walk on followed-through events (cap for runtime)
    step = max(1, len(followed_events) // 4000)
    sampled = followed_events[::step]
    for index, direction, _score in sampled:
        walk = correction_walk(data, index, direction)
        corrs = walk["corrections"]
        if not corrs:
            continue
        for corr in corrs:
            all_corrections.append(corr)
            survived_counts[corr["fate"]] += 1
        last = corrs[-1]
        if last["fate"] in ("reversal", "range"):
            slot = death_by_correction_number.setdefault(last["number"], {"reversal": 0, "range": 0})
            slot[last["fate"]] += 1

    ft_summary = {
        score: {
            **vals,
            "more_than_4_pct": vals["more_than_4"] / vals["events"] * 100 if vals["events"] else None,
        }
        for score, vals in sorted(ft_by_score.items())
    }
    total_events = sum(v["events"] for v in ft_by_score.values())
    total_ft = sum(v["more_than_4"] for v in ft_by_score.values())

    cont_rows = [c for c in all_corrections if c["fate"] == "continuation"]
    rev_rows = [c for c in all_corrections if c["fate"] == "reversal"]
    rng_rows = [c for c in all_corrections if c["fate"] == "range"]

    return {
        "definition": {
            "min_score": min_score,
            "follow_through": "closes stay beyond the signal-bar close for more than 4 bars",
            "correction": "pullback >= 1 ATR from the running extreme",
            "fates": {
                "continuation": "new extreme beyond the pre-correction extreme within 24 bars",
                "reversal": "price breaks the signal bar's opposite side before a new extreme",
                "range": "neither within 24 bars",
            },
            "note": "Forward data used only for grading; no signals produced.",
        },
        "candles": len(data),
        "trend_like_events": total_events,
        "followed_through_more_than_4_bars": total_ft,
        "followed_through_pct": total_ft / total_events * 100 if total_events else None,
        "follow_through_by_score": ft_summary,
        "correction_walk_sampled_events": len(sampled),
        "correction_fate_counts": survived_counts,
        "trend_death_by_correction_number": {k: death_by_correction_number[k] for k in sorted(death_by_correction_number)},
        "correction_profiles": {
            "survived_continuation": summarize_group(cont_rows),
            "killed_reversal": summarize_group(rev_rows),
            "faded_into_range": summarize_group(rng_rows),
        },
    }


def write_report(result: dict[str, Any], path: str | Path) -> None:
    lines = [
        "# BTCUSDT Trend Follow-Through and Correction Fate Study",
        "",
        "Relaxed standard requested by the user: how many trend-looking bars moved",
        "more than 4 bars in their direction, and what happened at the corrections",
        "that followed (continuation vs reversal vs drifting into a range).",
        "",
        "## Definitions",
    ]
    for key, value in result["definition"].items():
        lines.append(f"- **{key}**: {value}")
    lines += [
        "",
        f"Trend-like events: **{result['trend_like_events']:,}** — followed through >4 bars: "
        f"**{result['followed_through_more_than_4_bars']:,} ({result['followed_through_pct']:.1f}%)**",
        "",
        "## Follow-through (>4 bars) by signature score",
        "| score | events | >4 bars | % |",
        "|---|---|---|---|",
    ]
    for score, vals in result["follow_through_by_score"].items():
        lines.append(f"| {score} | {vals['events']:,} | {vals['more_than_4']:,} | {vals['more_than_4_pct']:.1f}% |")
    lines += [
        "",
        f"## Correction fates (sampled {result['correction_walk_sampled_events']:,} followed-through moves)",
        f"- continuation: {result['correction_fate_counts']['continuation']:,}",
        f"- reversal: {result['correction_fate_counts']['reversal']:,}",
        f"- range: {result['correction_fate_counts']['range']:,}",
        "",
        "## At which correction did the trend die?",
        "| correction # | reversal | range |",
        "|---|---|---|",
    ]
    for number, counts in result["trend_death_by_correction_number"].items():
        lines.append(f"| {number} | {counts['reversal']:,} | {counts['range']:,} |")
    lines += ["", "## Correction profiles (what distinguishes killers from survivors)"]
    for name, profile in result["correction_profiles"].items():
        lines.append("")
        lines.append(f"### {name} (n={profile.get('count', 0):,})")
        for col in ("duration_bars", "depth_atr", "depth_pct_of_move", "volume_vs_pre_move", "opposing_body_share"):
            if col in profile:
                lines.append(f"- {col}: mean {profile[col]['mean']:.3f}, median {profile[col]['median']:.3f}")
    lines += [
        "",
        "## Honest notes",
        "- These are descriptive statistics; nothing here is a signal or a guarantee.",
        "- Correction fates are graded with future data (evaluation only).",
        "- Delta/taker-buy columns in the source parquet are invalid and were not used.",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Follow-through and correction-fate study for trend-looking bars.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--min-score", type=int, default=3)
    parser.add_argument("--output", default="BTCUSDT_trend_follow_through.json")
    parser.add_argument("--report-output", default="BTCUSDT_trend_follow_through.md")
    args = parser.parse_args()

    frame = load_ohlcv(args.input)
    result = analyze(frame, min_score=args.min_score)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(result, args.report_output)
    print(json.dumps({
        "trend_like_events": result["trend_like_events"],
        "followed_through_pct": result["followed_through_pct"],
        "correction_fate_counts": result["correction_fate_counts"],
        "trend_death_by_correction_number": result["trend_death_by_correction_number"],
    }, indent=2))


if __name__ == "__main__":
    main()
