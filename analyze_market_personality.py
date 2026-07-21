"""Create descriptive BTCUSDT market-personality artifacts.

This module reads an existing behavior database. It never creates orders,
signals, or backtests.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


def _native(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _native(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_native(item) for item in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _stats(values: Iterable[float]) -> dict[str, float | int | None]:
    clean = sorted(float(value) for value in values if value is not None and math.isfinite(float(value)))
    if not clean:
        return {"count": 0, "mean": None, "median": None, "std": None, "min": None, "p25": None, "p75": None, "max": None}
    middle = len(clean) // 2
    median = clean[middle] if len(clean) % 2 else (clean[middle - 1] + clean[middle]) / 2
    def percentile(position: float) -> float:
        index = (len(clean) - 1) * position
        lower, upper = math.floor(index), math.ceil(index)
        if lower == upper:
            return clean[lower]
        return clean[lower] + (clean[upper] - clean[lower]) * (index - lower)
    mean = sum(clean) / len(clean)
    variance = sum((value - mean) ** 2 for value in clean) / len(clean)
    return {
        "count": len(clean),
        "mean": mean,
        "median": median,
        "std": math.sqrt(variance),
        "min": clean[0],
        "p25": percentile(0.25),
        "p75": percentile(0.75),
        "max": clean[-1],
    }


def _rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cursor = connection.execute(f'SELECT * FROM "{table}"')
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _feature_rows(rows: list[dict[str, Any]], column: str = "feature_json") -> list[dict[str, float]]:
    result = []
    for row in rows:
        try:
            parsed = json.loads(row[column])
        except (KeyError, TypeError, json.JSONDecodeError):
            parsed = {}
        result.append({key: float(value) for key, value in parsed.items() if isinstance(value, (int, float)) and math.isfinite(float(value))})
    return result


def _numeric_profile(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: _stats(row.get(field) for row in rows) for field in fields}


def _trend_profiles(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = (
        "duration_hours", "movement_pct", "velocity", "slope", "acceleration",
        "wave_count", "correction_count", "correction_depth_mean",
        "volume_mean", "volume_start", "volume_middle", "volume_end",
        "delta_change", "buy_sell_pressure", "swing_high_strength", "swing_low_strength",
    )
    by_direction = {}
    for direction in ("up", "down"):
        subset = [row for row in rows if row["direction"] == direction]
        by_direction[direction] = {
            "count": len(subset),
            "metrics": _numeric_profile(subset, fields),
            "volume_behavior": {
                "start_to_middle": _stats(
                    row["volume_middle"] / row["volume_start"] - 1
                    for row in subset if row["volume_start"]
                ),
                "middle_to_end": _stats(
                    row["volume_end"] / row["volume_middle"] - 1
                    for row in subset if row["volume_middle"]
                ),
            },
            "delta_behavior": {
                "available_records": sum(row["delta_available"] == 1 for row in subset),
                "change": _stats(row["delta_change"] for row in subset),
            },
            "pressure_behavior": _stats(row["buy_sell_pressure"] for row in subset),
            "power_behavior": {
                "start_power_proxy": _stats(
                    row["volume_start"] / row["volume_mean"]
                    for row in subset if row["volume_mean"]
                ),
                "end_power_proxy": _stats(
                    row["volume_end"] / row["volume_mean"]
                    for row in subset if row["volume_mean"]
                ),
            },
            "liquidity_behavior": {
                "status": "not stored in trend records",
                "available_records": 0,
            },
        }
    return {"total": len(rows), "all": {"metrics": _numeric_profile(rows, fields)}, "by_direction": by_direction}


def _correction_profiles(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups = {
        "continuation": [row for row in rows if row["outcome"] == "continuation"],
        "reversal": [row for row in rows if row["outcome"] == "reversal"],
        "range": [row for row in rows if row["outcome"] == "unknown"],
    }
    fields = ("retracement_pct", "duration_hours", "volume_change")
    return {
        "total": len(rows),
        "classification": {
            "continuation": len(groups["continuation"]),
            "reversal": len(groups["reversal"]),
            "range": len(groups["range"]),
        },
        "profiles": {
            name: {
                "count": len(group),
                "metrics": _numeric_profile(group, fields),
                "delta_behavior": sorted({row["delta_behavior"] for row in group}),
                "start_types": sorted({row["start_type"] for row in group}),
            }
            for name, group in groups.items()
        },
        "return_after_correction": {
            "status": "not stored; outcome is available but post-correction return is not",
            "metrics": _stats([]),
        },
    }


def _range_profiles(rows: list[dict[str, Any]]) -> dict[str, Any]:
    breakout_counts = {
        "successful": sum(row["successful_breakout"] == 1 for row in rows),
        "false": sum(row["false_breakout"] == 1 for row in rows),
        "none": sum(row["successful_breakout"] == 0 and row["false_breakout"] == 0 for row in rows),
    }
    return {
        "total": len(rows),
        "metrics": _numeric_profile(rows, ("duration_hours", "width_pct", "upper_touches", "lower_touches", "volume_mean")),
        "breakout_outcomes": breakout_counts,
        "delta_behavior": {
            "available_records": sum(row["delta_available"] == 1 for row in rows),
            "labels": sorted({row["delta_behavior"] for row in rows}),
        },
    }


def _transition_features(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["event_type"]].append(row)
    expected = (
        "trend_to_correction",
        "correction_to_trend",
        "trend_to_reversal",
        "range_to_breakout",
        "breakout_to_failure",
    )
    profiles: dict[str, Any] = {}
    for event_type in expected:
        subset = groups.get(event_type, [])
        feature_rows = _feature_rows(subset)
        suffixes: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for feature_row in feature_rows:
            for key, value in feature_row.items():
                match = re.match(r"(before|after)_(?:\d+|20|10)_(.+)", key)
                if match:
                    suffixes[match.group(2)][match.group(1)].append(value)
        comparisons = {}
        for suffix, sides in suffixes.items():
            before, after = sides.get("before", []), sides.get("after", [])
            if before and after:
                comparisons[suffix] = {
                    "before_mean": sum(before) / len(before),
                    "after_mean": sum(after) / len(after),
                    "after_minus_before": sum(after) / len(after) - sum(before) / len(before),
                }
        profiles[event_type] = {
            "count": len(subset),
            "feature_count": max((row["feature_count"] for row in subset), default=0),
            "before_candles": sorted({row["before_candles"] for row in subset}),
            "after_candles": sorted({row["after_candles"] for row in subset}),
            "feature_comparisons": comparisons,
            "status": "observed" if subset else "not_observed_in_database",
        }
    return {"total": len(rows), "profiles": profiles}


def build_personality(database: str | Path) -> dict[str, Any]:
    with sqlite3.connect(database) as connection:
        trends = _rows(connection, "trends")
        corrections = _rows(connection, "corrections")
        ranges = _rows(connection, "ranges")
        transitions = _rows(connection, "transitions")
        personality = {
            "asset": trends[0]["asset"] if trends else "BTCUSDT",
            "source_database": str(database),
            "scope": "descriptive market behavior only; no signals and no backtests",
            "trend_profiles": _trend_profiles(trends),
            "correction_profiles": _correction_profiles(corrections),
            "reversal_profiles": {
                "count": sum(row["outcome"] == "reversal" for row in corrections),
                "metrics": _numeric_profile([row for row in corrections if row["outcome"] == "reversal"], ("retracement_pct", "duration_hours", "volume_change")),
            },
            "range_profiles": _range_profiles(ranges),
            "transition_profiles": _transition_features(transitions),
            "data_quality_notes": [
                "Liquidity is not present in trend/range rows and cannot be profiled there.",
                "Correction-to-trend and breakout-to-failure transitions are not observed in the current database.",
                "The one unknown correction is grouped as range behavior descriptively, not as a validated regime label.",
            ],
        }
    return _native(personality)


def write_report(personality: dict[str, Any], path: str | Path) -> None:
    trends = personality["trend_profiles"]
    corrections = personality["correction_profiles"]
    ranges = personality["range_profiles"]
    transitions = personality["transition_profiles"]
    lines = [
        "# BTCUSDT Market Personality Report",
        "",
        "This is a descriptive behavior analysis only. It does not generate signals and does not run a backtest.",
        "",
        "## Dataset coverage",
        f"- Trend records: **{trends['total']:,}**",
        f"- Correction records: **{corrections['total']:,}**",
        f"- Range records: **{ranges['total']:,}**",
        f"- Transition records: **{transitions['total']:,}**",
        "",
        "## Trend personality",
        f"- Uptrends: **{trends['by_direction']['up']['count']:,}**",
        f"- Downtrends: **{trends['by_direction']['down']['count']:,}**",
        f"- Mean duration: **{trends['all']['metrics']['duration_hours']['mean']:.2f} hours**",
        f"- Mean movement: **{trends['all']['metrics']['movement_pct']['mean']:.4f}%**",
        f"- Mean wave count: **{trends['all']['metrics']['wave_count']['mean']:.2f}**",
        "",
        "### Volume, delta, pressure, and liquidity",
        f"- Uptrend volume middle→end change: `{trends['by_direction']['up']['volume_behavior']['middle_to_end']['mean']}`",
        f"- Downtrend volume middle→end change: `{trends['by_direction']['down']['volume_behavior']['middle_to_end']['mean']}`",
        f"- Uptrend start/end power proxies: `{trends['by_direction']['up']['power_behavior']['start_power_proxy']['mean']}` → `{trends['by_direction']['up']['power_behavior']['end_power_proxy']['mean']}`",
        f"- Downtrend start/end power proxies: `{trends['by_direction']['down']['power_behavior']['start_power_proxy']['mean']}` → `{trends['by_direction']['down']['power_behavior']['end_power_proxy']['mean']}`",
        f"- Delta is available in `{trends['by_direction']['up']['delta_behavior']['available_records'] + trends['by_direction']['down']['delta_behavior']['available_records']:,}` trend records.",
        "- Pressure is summarized where stored; liquidity is not stored in trend/range rows.",
        "",
        "## Correction personality",
        f"- Continuation: **{corrections['classification']['continuation']:,}**",
        f"- Reversal: **{corrections['classification']['reversal']:,}**",
        f"- Range/unknown: **{corrections['classification']['range']:,}**",
        "- Post-correction return is not stored, so the report uses the extractor's outcome label only.",
        "",
        "## Range personality",
        f"- Successful breakout: **{ranges['breakout_outcomes']['successful']:,}**",
        f"- False breakout: **{ranges['breakout_outcomes']['false']:,}**",
        f"- No classified breakout: **{ranges['breakout_outcomes']['none']:,}**",
        f"- Mean range width: **{ranges['metrics']['width_pct']['mean']:.4f}%**",
        "",
        "## Transition personality",
    ]
    for name, profile in transitions["profiles"].items():
        lines.append(f"- `{name}`: **{profile['count']:,}** ({profile['status']})")
        if profile["feature_comparisons"]:
            for feature, comparison in list(profile["feature_comparisons"].items())[:5]:
                lines.append(f"  - {feature}: after-before `{comparison['after_minus_before']:.6g}`")
    lines.extend(
        [
            "",
            "## Data-quality limitations",
            "- `correction_to_trend` and `breakout_to_failure` are not represented in the current 1,316 transition rows.",
            "- `rare_cases` is not reclassified here; this artifact describes the existing behavior database.",
            "- No trading signal, entry/exit rule, or backtest result is produced.",
        ]
    )
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a descriptive market-personality profile.")
    parser.add_argument("--database", required=True)
    parser.add_argument("--json-output", default="BTCUSDT_market_personality.json")
    parser.add_argument("--report-output", default="BTCUSDT_market_personality_report.md")
    args = parser.parse_args()
    personality = build_personality(args.database)
    Path(args.json_output).write_text(json.dumps(personality, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(personality, args.report_output)


if __name__ == "__main__":
    main()
