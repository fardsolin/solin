"""Compare observed BTCUSDT behavior categories without trading logic."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any


def stats(values: list[float]) -> dict[str, float | int | None]:
    values = sorted(value for value in values if math.isfinite(value))
    if not values:
        return {"count": 0, "mean": None, "median": None, "std": None, "min": None, "max": None}
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    middle = len(values) // 2
    median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    return {
        "count": len(values),
        "mean": mean,
        "median": median,
        "std": math.sqrt(variance),
        "min": values[0],
        "max": values[-1],
    }


def rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cursor = connection.execute(f'SELECT * FROM "{table}"')
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def numeric_comparison(left: list[dict[str, Any]], right: list[dict[str, Any]], fields: list[str]) -> dict[str, Any]:
    result = {}
    for field in fields:
        left_values = [float(row[field]) for row in left if row.get(field) is not None]
        right_values = [float(row[field]) for row in right if row.get(field) is not None]
        left_stats, right_stats = stats(left_values), stats(right_values)
        result[field] = {
            "left": left_stats,
            "right": right_stats,
            "mean_difference_right_minus_left": (
                right_stats["mean"] - left_stats["mean"]
                if left_stats["mean"] is not None and right_stats["mean"] is not None
                else None
            ),
        }
    return result


def feature_comparison(left: list[dict[str, float]], right: list[dict[str, float]]) -> dict[str, Any]:
    keys = sorted(set().union(*(row.keys() for row in left + right)))
    result = {}
    for key in keys:
        left_values = [row[key] for row in left if key in row]
        right_values = [row[key] for row in right if key in row]
        left_stats, right_stats = stats(left_values), stats(right_values)
        result[key] = {
            "left": left_stats,
            "right": right_stats,
            "mean_difference_right_minus_left": (
                right_stats["mean"] - left_stats["mean"]
                if left_stats["mean"] is not None and right_stats["mean"] is not None
                else None
            ),
        }
    return result


def parse_features(items: list[dict[str, Any]], column: str) -> list[dict[str, float]]:
    parsed = []
    for item in items:
        try:
            values = json.loads(item[column])
        except (KeyError, TypeError, json.JSONDecodeError):
            values = {}
        parsed.append({key: float(value) for key, value in values.items() if isinstance(value, (int, float)) and math.isfinite(float(value))})
    return parsed


def z_score(value: float, values: list[float]) -> float:
    center = sum(values) / len(values)
    spread = math.sqrt(sum((item - center) ** 2 for item in values) / len(values)) or 1
    return abs(value - center) / spread


def build_analysis(database: str | Path) -> dict[str, Any]:
    with sqlite3.connect(database) as connection:
        trends = rows(connection, "trends")
        corrections = rows(connection, "corrections")
        ranges = rows(connection, "ranges")
        transitions = rows(connection, "transitions")

    trend_fields = [
        "duration_hours", "movement_pct", "velocity", "slope", "acceleration",
        "wave_count", "correction_count", "correction_depth_mean", "volume_mean",
        "volume_start", "volume_middle", "volume_end", "delta_change",
        "buy_sell_pressure", "swing_high_strength", "swing_low_strength",
    ]
    correction_fields = ["retracement_pct", "duration_hours", "volume_change"]
    range_fields = ["duration_hours", "width_pct", "upper_touches", "lower_touches", "volume_mean"]

    healthy_corrections = [row for row in corrections if row["outcome"] == "continuation"]
    reversing_corrections = [row for row in corrections if row["outcome"] == "reversal"]
    range_success = [row for row in ranges if row["successful_breakout"] == 1]
    range_fake = [row for row in ranges if row["false_breakout"] == 1]

    transition_groups = defaultdict(list)
    for row, features in zip(transitions, parse_features(transitions, "feature_json")):
        transition_groups[row["event_type"]].append(features)

    # Match range transition snapshots to the range outcome by timestamp.
    range_outcomes = {row["end_time"]: row for row in ranges}
    real_transition_features, fake_transition_features = [], []
    for row, features in zip(transitions, parse_features(transitions, "feature_json")):
        matched = range_outcomes.get(row["timestamp"])
        if row["event_type"] == "range_to_breakout" and matched:
            if matched["successful_breakout"] == 1:
                real_transition_features.append(features)
            elif matched["false_breakout"] == 1:
                fake_transition_features.append(features)

    movement_values = [abs(float(row["movement_pct"])) for row in trends]
    acceleration_values = [abs(float(row["acceleration"])) for row in trends]
    rare_trends = []
    for row in trends:
        anomaly = max(
            z_score(abs(float(row["movement_pct"])), movement_values),
            z_score(abs(float(row["acceleration"])), acceleration_values),
        )
        if anomaly >= 3:
            rare_trends.append({"trend_id": row["trend_id"], "timestamp": row["end_time"], "anomaly_score": anomaly, "status": "candidate_sudden_break"})

    categories = {
        "healthy_trend": {
            "count": len(trends),
            "metrics": numeric_comparison(trends, [], trend_fields),
            "feature_metrics_available": len(set().union(*(row.keys() for row in parse_features(trends, "feature_json")))),
            "label_status": "proxy_all_trend_records",
        },
        "healthy_correction": {
            "count": len(healthy_corrections),
            "metrics": numeric_comparison(healthy_corrections, [], correction_fields),
            "label_status": "continuation",
        },
        "correction_to_trend": {
            "count": len(reversing_corrections),
            "metrics": numeric_comparison(reversing_corrections, [], correction_fields),
            "label_status": "reversal_correction_proxy; explicit transition row absent",
        },
        "new_trend_after_correction": {
            "count": 0,
            "metrics": {},
            "label_status": "not_observed_as_explicit_transition",
        },
        "range": {"count": len(ranges), "metrics": numeric_comparison(ranges, [], range_fields)},
        "real_range_breakout": {"count": len(range_success), "metrics": numeric_comparison(range_success, [], range_fields)},
        "fake_range_breakout": {"count": len(range_fake), "metrics": numeric_comparison(range_fake, [], range_fields)},
    }
    comparisons = {
        "trend_vs_healthy_correction": {
            "status": "different schemas; compare table-specific metrics, not raw values",
            "trend_metrics": categories["healthy_trend"]["metrics"],
            "correction_metrics": categories["healthy_correction"]["metrics"],
        },
        "healthy_correction_vs_correction_to_trend": numeric_comparison(healthy_corrections, reversing_corrections, correction_fields),
        "real_breakout_vs_fake_breakout": numeric_comparison(range_success, range_fake, range_fields),
        "transition_196_feature_comparisons": {
            "trend_to_correction_vs_range_to_breakout": feature_comparison(
                transition_groups["trend_to_correction"], transition_groups["range_to_breakout"]
            ),
            "real_breakout_vs_fake_breakout": feature_comparison(real_transition_features, fake_transition_features),
        },
    }
    return {
        "asset": "BTCUSDT",
        "scope": "behavior comparison only; no signals and no backtests",
        "feature_availability": {
            "transition_feature_count": 196,
            "available_in_transition_snapshots": ["open", "high", "low", "close", "volume", "body", "wick", "range", "return"],
            "available_only_in_trend_scalars": ["delta_change", "buy_sell_pressure"],
            "missing_from_current_bank": ["delta_persistence", "pressure_persistence", "liquidity", "price_regression_slope", "delta_reversal_timing"],
        },
        "categories": categories,
        "comparisons": comparisons,
        "transition_snapshot_counts": {
            "real_breakout": len(real_transition_features),
            "fake_breakout": len(fake_transition_features),
        },
        "rare_cases": {
            "sudden_trend_break_candidates": sorted(rare_trends, key=lambda item: item["anomaly_score"], reverse=True),
            "correction_to_trend": [{"correction_id": row["correction_id"], "start_time": row["start_time"], "end_time": row["end_time"], "outcome": row["outcome"]} for row in reversing_corrections],
            "fake_range_breakouts": [{"range_id": row["range_id"], "start_time": row["start_time"], "end_time": row["end_time"]} for row in range_fake],
        },
        "limitations": [
            "The current bank stores 196 transition features, but trend/correction/range rows store fewer scalar fields.",
            "Explicit correction_to_trend, trend_to_reversal, and breakout_to_failure transition rows are absent.",
            "Sudden trend breaks are anomaly candidates, not validated event labels.",
            "Liquidity is not present in the trend/range scalar rows.",
            "Delta persistence, pressure persistence, liquidity, and delta-reversal timing require richer transition records.",
        ],
    }


def report(analysis: dict[str, Any]) -> str:
    categories = analysis["categories"]
    comparisons = analysis["comparisons"]
    rare = analysis["rare_cases"]
    lines = [
        "# BTCUSDT Behavior Category Comparison",
        "",
        "Descriptive market-behavior analysis only. No signals and no backtests were produced.",
        "",
        "## Category counts",
    ]
    for name, value in categories.items():
        lines.append(f"- `{name}`: **{value['count']:,}**")
    lines += [
        "",
        "## 1. Trend versus healthy correction",
        "These tables have different schemas; the report preserves units and compares each group's own metrics rather than pretending unlike fields are directly interchangeable.",
        f"- Trend records analyzed: {categories['healthy_trend']['count']:,}",
        f"- Healthy correction records analyzed: {categories['healthy_correction']['count']:,}",
        f"- Transition feature dimensionality: {categories['healthy_trend']['feature_metrics_available']} trend feature fields and 196 transition fields.",
        "",
        "## 2. Healthy correction versus correction-to-trend",
        f"- Continuation corrections: {categories['healthy_correction']['count']:,}",
        f"- Reversal corrections used as the explicit-transition proxy: {categories['correction_to_trend']['count']:,}",
        "",
        "## 3. Real versus fake range breakout",
        f"- Real breakouts: {categories['real_range_breakout']['count']:,}",
        f"- Fake breakouts: {categories['fake_range_breakout']['count']:,}",
        f"- Matched transition snapshots: {analysis['transition_snapshot_counts']['real_breakout']:,} real and {analysis['transition_snapshot_counts']['fake_breakout']:,} fake.",
        f"- Feature comparison width: {len(comparisons['transition_196_feature_comparisons']['real_breakout_vs_fake_breakout']):,} metrics.",
        "",
        "### Correction comparison table",
        "| Metric | Healthy correction mean | Correction-to-trend mean | Difference |",
        "|---|---:|---:|---:|",
    ]
    for field, comparison in comparisons["healthy_correction_vs_correction_to_trend"].items():
        lines.append(
            f"| `{field}` | {comparison['left']['mean']:.6g} | {comparison['right']['mean']:.6g} | {comparison['mean_difference_right_minus_left']:.6g} |"
        )
    lines += [
        "",
        "### Range breakout comparison table",
        "| Metric | Real breakout mean | Fake breakout mean | Difference |",
        "|---|---:|---:|---:|",
    ]
    for field, comparison in comparisons["real_breakout_vs_fake_breakout"].items():
        lines.append(
            f"| `{field}` | {comparison['left']['mean']:.6g} | {comparison['right']['mean']:.6g} | {comparison['mean_difference_right_minus_left']:.6g} |"
        )
    lines += [
        "",
        "### 196-feature transition matrix",
        "The JSON artifact contains the complete 196-feature before/after matrices for trend-to-correction versus range-to-breakout and real versus fake breakout snapshots.",
        "",
        "## Rare cases",
        f"- Sudden trend-break candidates: {len(rare['sudden_trend_break_candidates']):,}",
        f"- Corrections classified as reversal: {len(rare['correction_to_trend']):,}",
        f"- Fake range breakouts: {len(rare['fake_range_breakouts']):,}",
        "",
        "## Limitations",
    ]
    lines.extend(f"- {item}" for item in analysis["limitations"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare BTCUSDT behavior categories.")
    parser.add_argument("--database", required=True)
    parser.add_argument("--json-output", default="BTCUSDT_behavior_category_comparison.json")
    parser.add_argument("--report-output", default="BTCUSDT_behavior_category_comparison.md")
    args = parser.parse_args()
    analysis = build_analysis(args.database)
    Path(args.json_output).write_text(json.dumps(analysis, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    Path(args.report_output).write_text(report(analysis), encoding="utf-8")


if __name__ == "__main__":
    main()
