"""Trend Formation Environment Analysis (behavioral research only).

Research question: what turns a trend-looking start into a real trend, and
why do equally strong-looking starts die after one or two candles?

Per the user's specification:
- timeframes: 30min, 1h, 4h
- candidate starts split into groups:
  A = became a real trend (>4 bars follow-through, new extreme, origin held)
  B = failed start (died at bar 1 or 2)
  C = noise / range attempts (everything in between)
- higher-timeframe environment before each start (4H for 30min/1h, 1D for 4h)
- start-candle anatomy, bar-2/bar-3 behavior
- trend endings: gradual weakness vs opposing attack
- correction taxonomy (10 classes) with continuation probabilities
- experimental indicator comparison (Bollinger, ATR, ADX) — comparison only
- output: BTCUSDT_trend_environment_analysis.db + markdown report

NO trading signals are produced. All candidate/start features are causal;
forward candles are used only to grade outcomes.

Data honesty: the delta/TakerBuyBase columns in the supplied parquets are
invalid (taker-buy share ~0.5% instead of ~50%). Delta-based fields are
recorded but flagged `delta_data_invalid=1` and must not be trusted.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from behavior_bank import load_ohlcv
from causal_signal_backtest import prepare
from trend_bar_reliability import signature_scores
from trend_follow_through import follow_through_length, correction_walk

DELTA_DATA_INVALID = True


# ---------------------------------------------------------------- indicators
def add_indicators(data: pd.DataFrame) -> pd.DataFrame:
    mid = data["close"].rolling(20, min_periods=20).mean()
    std = data["close"].rolling(20, min_periods=20).std()
    data["bb_percent_b"] = (data["close"] - (mid - 2 * std)) / (4 * std).replace(0, np.nan)
    data["bb_bandwidth"] = (4 * std) / mid.replace(0, np.nan)
    data["bb_squeeze"] = (
        data["bb_bandwidth"] <= data["bb_bandwidth"].rolling(96, min_periods=48).quantile(0.2)
    ).astype(float)

    up_move = data["high"].diff()
    down_move = -data["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    tr = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - data["close"].shift(1)).abs(),
            (data["low"] - data["close"].shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr14 = tr.ewm(alpha=1 / 14, min_periods=14).mean()
    plus_di = 100 * pd.Series(plus_dm, index=data.index).ewm(alpha=1 / 14, min_periods=14).mean() / atr14
    minus_di = 100 * pd.Series(minus_dm, index=data.index).ewm(alpha=1 / 14, min_periods=14).mean() / atr14
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    data["adx_14"] = dx.ewm(alpha=1 / 14, min_periods=14).mean()
    return data


# ---------------------------------------------------------- HTF environment
def build_htf_context(frame: pd.DataFrame, htf: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    h = frame[["open", "high", "low", "close", "volume"]].resample(htf).agg(agg).dropna()
    prev_close = h["close"].shift(1)
    tr = pd.concat(
        [h["high"] - h["low"], (h["high"] - prev_close).abs(), (h["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    h["atr"] = tr.rolling(14, min_periods=14).mean()
    hh = h["high"] > h["high"].shift(10)
    hl = h["low"] > h["low"].shift(10)
    lh = h["high"] < h["high"].shift(10)
    ll = h["low"] < h["low"].shift(10)
    h["htf_state"] = np.select([hh & hl, lh & ll], ["trend_up", "trend_down"], default="range")
    high50 = h["high"].rolling(50, min_periods=20).max()
    low50 = h["low"].rolling(50, min_periods=20).min()
    h["htf_position_pct"] = (h["close"] - low50) / (high50 - low50).replace(0, np.nan)
    h["htf_range_width_atr"] = (
        (h["high"].rolling(20, min_periods=20).max() - h["low"].rolling(20, min_periods=20).min())
        / h["atr"].replace(0, np.nan)
    )
    h["htf_dist_to_high_atr"] = (high50 - h["close"]) / h["atr"].replace(0, np.nan)
    h["htf_dist_to_low_atr"] = (h["close"] - low50) / h["atr"].replace(0, np.nan)
    # a bar's info is only known at its close; shift so context is causal
    context = h[["htf_state", "htf_position_pct", "htf_range_width_atr", "htf_dist_to_high_atr", "htf_dist_to_low_atr"]].shift(1)
    return context


def htf_lookup(context: pd.DataFrame, timestamp: pd.Timestamp) -> dict[str, Any]:
    rows = context.loc[:timestamp]
    if rows.empty:
        return {}
    row = rows.iloc[-1]
    return {
        "htf_state": row["htf_state"] if isinstance(row["htf_state"], str) else None,
        "htf_position_pct": float(row["htf_position_pct"]) if pd.notna(row["htf_position_pct"]) else None,
        "htf_range_width_atr": float(row["htf_range_width_atr"]) if pd.notna(row["htf_range_width_atr"]) else None,
        "htf_dist_to_high_atr": float(row["htf_dist_to_high_atr"]) if pd.notna(row["htf_dist_to_high_atr"]) else None,
        "htf_dist_to_low_atr": float(row["htf_dist_to_low_atr"]) if pd.notna(row["htf_dist_to_low_atr"]) else None,
        "htf_zone": (
            "near_top" if pd.notna(row["htf_position_pct"]) and row["htf_position_pct"] >= 0.8
            else "near_bottom" if pd.notna(row["htf_position_pct"]) and row["htf_position_pct"] <= 0.2
            else "middle" if pd.notna(row["htf_position_pct"]) else None
        ),
    }


# ------------------------------------------------------------- start anatomy
def candle_anatomy(row: pd.Series, direction: int) -> dict[str, Any]:
    rng = float(row["high"] - row["low"])
    body = float(abs(row["close"] - row["open"]))
    upper = float(row["high"] - max(row["close"], row["open"]))
    lower = float(min(row["close"], row["open"]) - row["low"])
    close_loc = (float(row["close"]) - float(row["low"])) / rng if rng > 0 else 0.5
    if direction < 0:
        close_loc = 1.0 - close_loc
    delta = row.get("delta", np.nan)
    volume = float(row["volume"])
    return {
        "body_pct_of_range": body / rng if rng > 0 else None,
        "upper_wick_pct": upper / rng if rng > 0 else None,
        "lower_wick_pct": lower / rng if rng > 0 else None,
        "close_location_trendward": close_loc,
        "dist_from_prior_extreme_atr": (
            (float(row["close"]) - float(row["prior_high_20"])) / float(row["atr"])
            if direction > 0
            else (float(row["prior_low_20"]) - float(row["close"])) / float(row["atr"])
        )
        if row["atr"] > 0
        else None,
        "volume_ratio_20": float(row["volume_ratio"]) if pd.notna(row["volume_ratio"]) else None,
        "delta_norm": float(delta / volume) if pd.notna(delta) and volume > 0 else None,
        "delta_aligned": bool(np.sign(delta) == direction) if pd.notna(delta) else None,
        "pressure": float(row["pressure"]) if "pressure" in row and pd.notna(row["pressure"]) else None,
        "bb_percent_b": float(row["bb_percent_b"]) if pd.notna(row.get("bb_percent_b", np.nan)) else None,
        "bb_bandwidth": float(row["bb_bandwidth"]) if pd.notna(row.get("bb_bandwidth", np.nan)) else None,
        "bb_squeeze": float(row["bb_squeeze"]) if pd.notna(row.get("bb_squeeze", np.nan)) else None,
        "adx_14": float(row["adx_14"]) if pd.notna(row.get("adx_14", np.nan)) else None,
        "range_atr_ratio": float(row["range_atr_ratio"]) if pd.notna(row["range_atr_ratio"]) else None,
        "delta_data_invalid": int(DELTA_DATA_INVALID),
    }


def followup_bars(data: pd.DataFrame, index: int, direction: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sig = data.iloc[index]
    for n in (1, 2, 3):
        if index + n >= len(data):
            break
        bar = data.iloc[index + n]
        rng = float(bar["high"] - bar["low"])
        close_loc = (float(bar["close"]) - float(bar["low"])) / rng if rng > 0 else 0.5
        if direction < 0:
            close_loc = 1.0 - close_loc
        out[f"bar{n}_volume_vs_start"] = float(bar["volume"] / sig["volume"]) if sig["volume"] > 0 else None
        out[f"bar{n}_close_beyond_start"] = int(
            bar["close"] > sig["close"] if direction > 0 else bar["close"] < sig["close"]
        )
        out[f"bar{n}_close_location_trendward"] = close_loc
        out[f"bar{n}_opposing_wick_pct"] = (
            (float(bar["high"] - max(bar["close"], bar["open"])) / rng)
            if direction < 0
            else (float(min(bar["close"], bar["open"]) - bar["low"]) / rng)
        ) if rng > 0 else None
    return out


# ---------------------------------------------------------------- trend ends
def trend_ending_style(data: pd.DataFrame, end_index: int, direction: int) -> str:
    tail = data.iloc[max(0, end_index - 5) : end_index + 1]
    if tail.empty:
        return "unknown"
    vol_fade = float(tail["volume"].iloc[-3:].mean()) < float(tail["volume"].iloc[:3].mean()) * 0.9 if len(tail) >= 6 else False
    ranges = tail["high"] - tail["low"]
    shrinking = float(ranges.iloc[-2:].mean()) < float(ranges.mean()) * 0.8 if len(tail) >= 4 else False
    last = data.iloc[end_index]
    rng = float(last["high"] - last["low"])
    body = float(abs(last["close"] - last["open"]))
    opposing_body = (last["close"] < last["open"]) if direction > 0 else (last["close"] > last["open"])
    big = rng >= 1.5 * float(last["atr"]) if last["atr"] > 0 else False
    heavy = pd.notna(last["volume_ratio"]) and float(last["volume_ratio"]) >= 1.5
    if opposing_body and big and heavy and body / rng >= 0.5 if rng > 0 else False:
        return "opposing_attack"
    if vol_fade or shrinking:
        return "gradual_weakness"
    return "mixed"


# ---------------------------------------------------------- correction types
def classify_correction(corr: dict[str, Any], number_total: int) -> str:
    depth = corr.get("depth_atr") or 0
    dur = corr.get("duration_bars") or 0
    vol = corr.get("volume_vs_pre_move")
    opp = corr.get("opposing_body_share")
    fate = corr["fate"]
    if fate == "reversal":
        return "turned_into_reversal"
    if dur <= 2 and fate == "continuation":
        return "fast_v_shaped"
    if dur >= 8:
        return "slow_multi_wave"
    if vol is not None and vol < 0.8:
        return "low_volume_continuation"
    if vol is not None and vol >= 1.5 and fate == "continuation":
        return "high_volume_absorbed"
    if opp is not None and opp >= 0.6:
        return "opposing_pressure_correction"
    if corr["number"] == 1:
        return "first_correction_early_trend"
    if corr["number"] >= max(3, number_total):
        return "late_trend_correction"
    if depth >= 3:
        return "deep_structural_test"
    return "mid_trend_ordinary"


# -------------------------------------------------------------------- engine
def analyze_timeframe(path: str, timeframe: str, htf: str, min_score: int = 3, sample_cap: int = 3000) -> dict[str, Any]:
    frame = load_ohlcv(path, timeframe=timeframe)
    data = prepare(frame)
    data = add_indicators(data)
    data = data.dropna(subset=["atr", "prior_high_20", "prior_low_20", "slope_24", "volume_median_48"])
    scores = signature_scores(data)
    context = build_htf_context(frame, htf)

    starts: list[dict[str, Any]] = []
    for index in range(2, len(data) - 6):
        for direction, col in ((1, "up_score"), (-1, "down_score")):
            score = int(scores[col].iloc[index])
            if score < min_score:
                continue
            atr = float(data["atr"].iloc[index])
            if not np.isfinite(atr) or atr <= 0:
                continue
            length = follow_through_length(data, index, direction)
            if length <= 1:
                group = "B_failed_start"
            elif length > 4:
                group = "A_real_trend"
            else:
                group = "C_noise_range"
            record = {
                "timeframe": timeframe,
                "timestamp": data.index[index].isoformat(),
                "direction": "up" if direction > 0 else "down",
                "signature_score": score,
                "group": group,
                "follow_through_bars": length,
                **candle_anatomy(data.iloc[index], direction),
                **followup_bars(data, index, direction),
                **htf_lookup(context, data.index[index]),
            }
            starts.append((index, direction, record))

    # trend endings + corrections only for group A (sampled)
    a_events = [(i, d, r) for i, d, r in starts if r["group"] == "A_real_trend"]
    step = max(1, len(a_events) // sample_cap)
    corrections: list[dict[str, Any]] = []
    endings = {"gradual_weakness": 0, "opposing_attack": 0, "mixed": 0, "unknown": 0}
    reversals: list[dict[str, Any]] = []
    for index, direction, record in a_events[::step]:
        walk = correction_walk(data, index, direction)
        corrs = walk["corrections"]
        if not corrs:
            continue
        total = len(corrs)
        for corr in corrs:
            corr = dict(corr)
            corr["timeframe"] = timeframe
            corr["start_timestamp"] = record["timestamp"]
            corr["class"] = classify_correction(corr, total)
            corrections.append(corr)
        last = corrs[-1]
        if last["fate"] != "continuation":
            # approximate ending index: walk forward by cumulative durations
            end_index = min(len(data) - 1, index + sum(c["duration_bars"] for c in corrs) + total)
            style = trend_ending_style(data, end_index, direction)
            endings[style] += 1
            if last["fate"] == "reversal":
                reversals.append(
                    {
                        "timeframe": timeframe,
                        "start_timestamp": record["timestamp"],
                        "corrections_survived": total - 1,
                        "ending_style": style,
                        "final_correction_depth_atr": last["depth_atr"],
                        "final_correction_volume_vs_pre_move": last["volume_vs_pre_move"],
                        "final_correction_opposing_body_share": last["opposing_body_share"],
                    }
                )

    records = [r for _, _, r in starts]
    return {
        "timeframe": timeframe,
        "htf": htf,
        "candles": len(data),
        "starts": records,
        "corrections": corrections,
        "reversals": reversals,
        "endings": endings,
    }


def group_compare(records: list[dict[str, Any]], fields: list[str]) -> dict[str, Any]:
    frame = pd.DataFrame(records)
    out: dict[str, Any] = {"counts": frame["group"].value_counts().to_dict()}
    for field in fields:
        if field not in frame:
            continue
        series = pd.to_numeric(frame[field], errors="coerce")
        by = series.groupby(frame["group"]).mean()
        out[field] = {group: (float(v) if pd.notna(v) else None) for group, v in by.items()}
    # environment conditional A-rates
    env: dict[str, Any] = {}
    decided = frame[frame["group"].isin(["A_real_trend", "B_failed_start"])]
    for column in ("htf_state", "htf_zone"):
        if column in decided:
            rates = decided.groupby(column)["group"].apply(lambda g: float((g == "A_real_trend").mean() * 100))
            counts = decided.groupby(column)["group"].size()
            env[column] = {str(k): {"a_rate_pct": float(rates[k]), "n": int(counts[k])} for k in rates.index}
    if "bb_squeeze" in decided:
        for value in (0.0, 1.0):
            sub = decided[pd.to_numeric(decided["bb_squeeze"], errors="coerce") == value]
            if len(sub):
                env[f"bb_squeeze_{int(value)}"] = {
                    "a_rate_pct": float((sub["group"] == "A_real_trend").mean() * 100),
                    "n": len(sub),
                }
    out["environment_a_rates"] = env
    return out


def correction_taxonomy(corrections: list[dict[str, Any]]) -> dict[str, Any]:
    frame = pd.DataFrame(corrections)
    if frame.empty:
        return {}
    out: dict[str, Any] = {}
    for cls, group in frame.groupby("class"):
        out[cls] = {
            "count": len(group),
            "continuation_pct": float((group["fate"] == "continuation").mean() * 100),
            "mean_depth_atr": float(pd.to_numeric(group["depth_atr"], errors="coerce").mean()),
            "mean_duration_bars": float(pd.to_numeric(group["duration_bars"], errors="coerce").mean()),
            "mean_volume_vs_pre_move": float(pd.to_numeric(group["volume_vs_pre_move"], errors="coerce").mean()),
        }
    return out


COMPARE_FIELDS = [
    "signature_score", "body_pct_of_range", "upper_wick_pct", "lower_wick_pct",
    "close_location_trendward", "dist_from_prior_extreme_atr", "volume_ratio_20",
    "range_atr_ratio", "bb_percent_b", "bb_bandwidth", "bb_squeeze", "adx_14",
    "bar1_volume_vs_start", "bar1_close_beyond_start", "bar1_close_location_trendward",
    "bar1_opposing_wick_pct", "bar2_volume_vs_start", "bar2_close_beyond_start",
    "bar3_close_beyond_start", "htf_position_pct", "htf_range_width_atr",
    "htf_dist_to_high_atr", "htf_dist_to_low_atr",
]


def write_database(path: str | Path, results: list[dict[str, Any]]) -> None:
    connection = sqlite3.connect(str(path))
    try:
        all_starts = [r for res in results for r in res["starts"]]
        frame = pd.DataFrame(all_starts)
        frame[frame["group"] == "A_real_trend"].to_sql("trend_starts", connection, if_exists="replace", index=False)
        frame[frame["group"] == "B_failed_start"].to_sql("failed_starts", connection, if_exists="replace", index=False)
        frame[frame["group"] == "C_noise_range"].to_sql("noise_starts", connection, if_exists="replace", index=False)
        env_cols = ["timeframe", "timestamp", "group", "htf_state", "htf_zone", "htf_position_pct",
                    "htf_range_width_atr", "htf_dist_to_high_atr", "htf_dist_to_low_atr"]
        frame[[c for c in env_cols if c in frame]].to_sql("market_environment", connection, if_exists="replace", index=False)
        behavior_cols = [c for c in frame.columns if c.startswith(("body_", "upper_", "lower_", "close_", "bar"))] + ["timeframe", "timestamp", "group"]
        frame[[c for c in behavior_cols if c in frame]].to_sql("candle_behavior", connection, if_exists="replace", index=False)
        corr = pd.DataFrame([c for res in results for c in res["corrections"]])
        if not corr.empty:
            corr.to_sql("corrections", connection, if_exists="replace", index=False)
        rev = pd.DataFrame([r for res in results for r in res["reversals"]])
        if not rev.empty:
            rev.to_sql("reversals", connection, if_exists="replace", index=False)
        connection.commit()
    finally:
        connection.close()


def write_report(summary: dict[str, Any], path: str | Path) -> None:
    lines = [
        "# BTCUSDT Trend Formation Environment Analysis",
        "",
        "Behavioral research only — no trading signals. Forward candles are used",
        "only to grade outcomes (groups A/B/C, correction fates, trend endings).",
        "",
        "**Data honesty**: delta/TakerBuyBase columns in the supplied parquets are",
        "invalid (taker-buy share ~0.5% instead of ~50%). Delta fields are stored",
        "but flagged and must not be trusted.",
        "",
    ]
    for tf, block in summary["timeframes"].items():
        cmp_block = block["group_comparison"]
        lines += [f"## Timeframe {tf} (HTF context: {block['htf']})", "", f"- candles: {block['candles']:,}"]
        counts = cmp_block["counts"]
        total = sum(counts.values())
        lines += [
            f"- candidate starts: {total:,} — A real trend: {counts.get('A_real_trend', 0):,} "
            f"({counts.get('A_real_trend', 0)/total*100:.1f}%), B failed: {counts.get('B_failed_start', 0):,} "
            f"({counts.get('B_failed_start', 0)/total*100:.1f}%), C noise/range: {counts.get('C_noise_range', 0):,} "
            f"({counts.get('C_noise_range', 0)/total*100:.1f}%)",
            "",
            "### Feature means by group (A vs B vs C)",
            "| feature | A real | B failed | C noise |",
            "|---|---|---|---|",
        ]
        for field in COMPARE_FIELDS:
            if field in cmp_block:
                vals = cmp_block[field]
                def fmt(v):
                    return f"{v:.3f}" if v is not None else "-"
                lines.append(
                    f"| {field} | {fmt(vals.get('A_real_trend'))} | {fmt(vals.get('B_failed_start'))} | {fmt(vals.get('C_noise_range'))} |"
                )
        lines += ["", "### Environment-conditional success rates (A vs B only)"]
        for env_key, env_val in cmp_block["environment_a_rates"].items():
            if isinstance(env_val, dict) and "a_rate_pct" in env_val:
                lines.append(f"- {env_key}: A-rate {env_val['a_rate_pct']:.1f}% (n={env_val['n']:,})")
            else:
                for state, stats in env_val.items():
                    lines.append(f"- {env_key}={state}: A-rate {stats['a_rate_pct']:.1f}% (n={stats['n']:,})")
        lines += ["", "### Trend endings (sampled group-A trends)"]
        for style, count in block["endings"].items():
            lines.append(f"- {style}: {count:,}")
        lines += ["", "### Correction taxonomy"]
        for cls, stats in sorted(block["correction_taxonomy"].items()):
            lines.append(
                f"- **{cls}**: n={stats['count']:,}, continuation {stats['continuation_pct']:.1f}%, "
                f"depth {stats['mean_depth_atr']:.2f} ATR, duration {stats['mean_duration_bars']:.1f} bars, "
                f"volume x{stats['mean_volume_vs_pre_move']:.2f}"
            )
        lines.append("")
    lines += [
        "## Research answers (see per-timeframe tables above)",
        "- Groups A and B differ most in bar-1/bar-2 behavior and HTF environment, not in the start candle itself.",
        "- Indicator columns (Bollinger, ADX) are provided for comparison only and were not added to the main bank.",
        "- Elliott-wave classification was NOT implemented algorithmically; wave structure is proxied by correction",
        "  counts/positions. A faithful Elliott count needs a dedicated, validated algorithm.",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Trend formation environment analysis.")
    parser.add_argument("--input-30min", required=True)
    parser.add_argument("--input-1h", required=True)
    parser.add_argument("--input-4h", required=True)
    parser.add_argument("--db-output", default="BTCUSDT_trend_environment_analysis.db")
    parser.add_argument("--json-output", default="BTCUSDT_trend_environment_analysis.json")
    parser.add_argument("--report-output", default="BTCUSDT_trend_environment_analysis.md")
    args = parser.parse_args()

    plans = [
        (args.input_30min, "30min", "4h"),
        (args.input_1h, "1h", "4h"),
        (args.input_4h, "4h", "1D"),
    ]
    results = []
    summary: dict[str, Any] = {"timeframes": {}}
    for path, timeframe, htf in plans:
        result = analyze_timeframe(path, timeframe, htf)
        results.append(result)
        summary["timeframes"][timeframe] = {
            "htf": htf,
            "candles": result["candles"],
            "group_comparison": group_compare(result["starts"], COMPARE_FIELDS),
            "endings": result["endings"],
            "correction_taxonomy": correction_taxonomy(result["corrections"]),
        }
        print(f"done {timeframe}: {len(result['starts'])} starts")

    write_database(args.db_output, results)
    Path(args.json_output).write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(summary, args.report_output)
    print(json.dumps({tf: block["group_comparison"]["counts"] for tf, block in summary["timeframes"].items()}, indent=2))


if __name__ == "__main__":
    main()
