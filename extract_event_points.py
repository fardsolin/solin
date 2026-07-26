"""Extract compact event-point behavior from the BTCUSDT behavior database."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from typing import Any


def rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cursor = connection.execute(f'SELECT * FROM "{table}"')
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def features(rows: list[dict[str, Any]]) -> list[dict[str, float]]:
    parsed = []
    for row in rows:
        values = json.loads(row["feature_json"])
        parsed.append({key: float(value) for key, value in values.items() if isinstance(value, (int, float)) and math.isfinite(float(value))})
    return parsed


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def percentile(values: list[float], level: float) -> float | None:
    values = sorted(values)
    if not values:
        return None
    index = (len(values) - 1) * level
    lower, upper = math.floor(index), math.ceil(index)
    return values[lower] if lower == upper else values[lower] + (values[upper] - values[lower]) * (index - lower)


def group_means(items: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[str, float | None]:
    return {field: mean([float(item[field]) for item in items if item.get(field) is not None]) for field in fields}


def summarize_events(event_rows: list[dict[str, Any]], event_features: list[dict[str, float]]) -> dict[str, Any]:
    def values(key: str) -> list[float]:
        return [item[key] for item in event_features if key in item]

    metrics = {}
    for key in ("before_20_return", "before_20_range_mean", "before_20_volatility", "before_20_volume_mean",
                "after_01_return", "after_01_range", "after_01_body", "after_01_upper_wick",
                "after_01_lower_wick", "after_01_volume", "after_10_return", "after_10_range_mean",
                "after_10_range_max", "after_10_volatility", "after_10_volume_mean", "after_10_volume_last"):
        metrics[key] = {
            "mean": mean(values(key)),
            "p25": percentile(values(key), 0.25),
            "p75": percentile(values(key), 0.75),
        }
    rotation = []
    for row, item in zip(event_rows, event_features):
        range_value = item.get("after_01_range", 0)
        body = item.get("after_01_body", 0)
        volume = item.get("after_01_volume", 0)
        rotation.append({
            "event_id": row["event_id"],
            "timestamp": row["timestamp"],
            "rotation_bar_power": range_value * volume,
            "rotation_bar_directional_pressure": math.copysign(body, item.get("after_01_return", 0)) if body else 0,
            "rotation_bar_body_to_range": body / range_value if range_value else None,
            "rotation_bar_wick_balance": item.get("after_01_lower_wick", 0) - item.get("after_01_upper_wick", 0),
        })
    powers = [item["rotation_bar_power"] for item in rotation]
    threshold = percentile(powers, 0.9) or 0
    for item in rotation:
        item["intensity"] = "high_power" if item["rotation_bar_power"] >= threshold else "regular"
    return {
        "count": len(event_rows),
        "metrics": metrics,
        "rotation_bars": rotation,
        "high_power_rotation_count": sum(item["intensity"] == "high_power" for item in rotation),
    }


def build_event_points(database: str | Path) -> dict[str, Any]:
    with sqlite3.connect(database) as connection:
        trends = rows(connection, "trends")
        corrections = rows(connection, "corrections")
        ranges = rows(connection, "ranges")
        transitions = rows(connection, "transitions")

    transition_by_type: dict[str, list[dict[str, Any]]] = {}
    for event_type in ("trend_to_correction", "range_to_breakout"):
        subset = [row for row in transitions if row["event_type"] == event_type]
        transition_by_type[event_type] = subset

    trend_features = features(trends)
    movement_abs = [abs(row["movement_pct"]) for row in trends]
    explosive_threshold = percentile(movement_abs, 0.9) or 0
    regular_threshold = percentile(movement_abs, 0.25) or 0
    explosive_trends = [row for row in trends if abs(row["movement_pct"]) >= explosive_threshold]
    regular_trends = [row for row in trends if abs(row["movement_pct"]) <= regular_threshold]
    trend_summary = {
        "count": len(trends),
        "landmark_fields": [
            "start_time", "end_time", "direction", "duration_hours", "movement_pct",
            "velocity", "acceleration", "slope", "wave_count", "correction_count",
            "volume_start", "volume_middle", "volume_end", "delta_change",
            "buy_sell_pressure", "swing_high_strength", "swing_low_strength",
        ],
        "start_end_power": {
            "start_volume_to_mean": mean([row["volume_start"] / row["volume_mean"] for row in trends if row["volume_mean"]]),
            "end_volume_to_mean": mean([row["volume_end"] / row["volume_mean"] for row in trends if row["volume_mean"]]),
        },
        "movement_types": {
            "explosive_candidates": {
                "movement_abs_p90": percentile([abs(row["movement_pct"]) for row in trends], 0.9),
                "velocity_abs_p90": percentile([abs(row["velocity"]) for row in trends], 0.9),
                "acceleration_abs_p90": percentile([abs(row["acceleration"]) for row in trends], 0.9),
            },
            "regular_candidates": {
                "movement_abs_p25": percentile([abs(row["movement_pct"]) for row in trends], 0.25),
                "velocity_abs_p25": percentile([abs(row["velocity"]) for row in trends], 0.25),
            },
        },
        "feature_coverage": len(set().union(*(item.keys() for item in trend_features))),
        "movement_profiles": {
            "explosive": {
                "count": len(explosive_trends),
                "threshold_abs_movement_pct": explosive_threshold,
                "means": group_means(explosive_trends, ("duration_hours", "movement_pct", "velocity", "acceleration", "slope", "volume_start", "volume_end", "buy_sell_pressure")),
            },
            "regular": {
                "count": len(regular_trends),
                "threshold_abs_movement_pct": regular_threshold,
                "means": group_means(regular_trends, ("duration_hours", "movement_pct", "velocity", "acceleration", "slope", "volume_start", "volume_end", "buy_sell_pressure")),
            },
        },
    }

    correction_summary = {}
    for label, outcome in (("healthy_inside_trend", "continuation"), ("trend_change", "reversal"), ("unresolved", "unknown")):
        subset = [row for row in corrections if row["outcome"] == outcome]
        correction_summary[label] = {
            "count": len(subset),
            "mean_retracement_pct": mean([row["retracement_pct"] for row in subset]),
            "mean_duration_hours": mean([row["duration_hours"] for row in subset]),
            "mean_volume_change": mean([row["volume_change"] for row in subset]),
            "start_types": sorted({row["start_type"] for row in subset}),
            "delta_behavior": sorted({row["delta_behavior"] for row in subset}),
            "landmarks": ["start_time", "end_time", "retracement_pct", "duration_hours", "volume_change", "outcome"],
        }

    range_summary = {
        "count": len(ranges),
        "mean_duration_hours": mean([row["duration_hours"] for row in ranges]),
        "mean_width_pct": mean([row["width_pct"] for row in ranges]),
        "mean_upper_touches": mean([row["upper_touches"] for row in ranges]),
        "mean_lower_touches": mean([row["lower_touches"] for row in ranges]),
        "outcomes": {
            "real_breakout": sum(row["successful_breakout"] == 1 for row in ranges),
            "fake_breakout": sum(row["false_breakout"] == 1 for row in ranges),
            "unclassified": sum(row["successful_breakout"] == 0 and row["false_breakout"] == 0 for row in ranges),
        },
    }

    transition_summary = {
        event_type: summarize_events(rows_for_type, features(rows_for_type))
        for event_type, rows_for_type in transition_by_type.items()
    }
    for profile in transition_summary.values():
        profile["rotation_profiles"] = {}
        for label in ("high_power", "regular"):
            subset = [item for item in profile["rotation_bars"] if item["intensity"] == label]
            profile["rotation_profiles"][label] = {
                "count": len(subset),
                "means": group_means(subset, ("rotation_bar_power", "rotation_bar_directional_pressure", "rotation_bar_body_to_range", "rotation_bar_wick_balance")),
            }
    return {
        "asset": "BTCUSDT",
        "scope": "event-point behavior extraction only; no signals or backtests",
        "trend_landmarks": trend_summary,
        "correction_landmarks": correction_summary,
        "range_landmarks": range_summary,
        "transition_landmarks": transition_summary,
        "interpretation": {
            "early_warning": [
                "A correction/reversal distinction is probabilistic at its start.",
                "Structural confirmation requires a prior swing breach and follow-through, which the current bank does not store explicitly.",
                "The first post-event candle is treated as a rotation-bar proxy, not as a definitive reversal candle.",
            ],
            "power_vs_pressure": {
                "power": "range multiplied by volume; describes participation and expansion.",
                "pressure": "directional body and return; wick imbalance can show rejection or absorption.",
                "caution": "A large candle with a long opposing wick can have high power but failed directional pressure.",
            },
            "missing_event_fields": [
                "explicit prior swing high/low prices",
                "delta persistence and reversal timing",
                "liquidity at the event",
                "direct correction-to-trend transition labels",
            ],
        },
    }


def report(data: dict[str, Any]) -> str:
    trend = data["trend_landmarks"]
    corrections = data["correction_landmarks"]
    ranges = data["range_landmarks"]
    transitions = data["transition_landmarks"]
    lines = [
        "# BTCUSDT Critical Event-Point Behavior",
        "",
        "This report extracts landmark behavior only. It does not generate trading signals or backtests.",
        "",
        "## 1. Trend landmarks",
        f"- Trends: **{trend['count']:,}**",
        f"- Stored event fields: **{len(trend['landmark_fields'])}**",
        f"- Start volume / mean volume: `{trend['start_end_power']['start_volume_to_mean']:.4f}`",
        f"- End volume / mean volume: `{trend['start_end_power']['end_volume_to_mean']:.4f}`",
        f"- Explosive candidates use p90 movement/velocity/acceleration thresholds.",
        f"- Regular candidates use p25 movement/velocity thresholds.",
        f"- Explosive/regular counts: `{trend['movement_profiles']['explosive']['count']}` / `{trend['movement_profiles']['regular']['count']}`.",
        "",
        "## 2. Correction landmarks",
    ]
    for name, profile in corrections.items():
        lines.append(
            f"- `{name}`: {profile['count']:,}; mean retracement `{profile['mean_retracement_pct']}`, "
            f"duration `{profile['mean_duration_hours']}` hours, volume change `{profile['mean_volume_change']}`"
        )
    lines += [
        "",
        "## 3. Range landmarks",
        f"- Ranges: **{ranges['count']:,}**",
        f"- Mean duration: `{ranges['mean_duration_hours']}` hours",
        f"- Mean width: `{ranges['mean_width_pct']}`%",
        f"- Real/fake/unclassified: `{ranges['outcomes']['real_breakout']}` / `{ranges['outcomes']['fake_breakout']}` / `{ranges['outcomes']['unclassified']}`",
        "",
        "## 4. Rotation-bar behavior",
    ]
    for event_type, profile in transitions.items():
        lines += [
            f"### {event_type}",
            f"- Events: **{profile['count']:,}**",
            f"- High-power first post-event bars: **{profile['high_power_rotation_count']:,}**",
            f"- High-power/regular rotation bars: `{profile['rotation_profiles']['high_power']['count']}` / `{profile['rotation_profiles']['regular']['count']}`",
            f"- First post-event return mean: `{profile['metrics']['after_01_return']['mean']}`",
            f"- First post-event range mean: `{profile['metrics']['after_01_range']['mean']}`",
            f"- 10-candle return mean: `{profile['metrics']['after_10_return']['mean']}`",
            f"- 10-candle volatility mean: `{profile['metrics']['after_10_volatility']['mean']}`",
        ]
    lines += [
        "",
        "## 5. How to distinguish correction from reversal",
        "At the first bars, the distinction is probabilistic. Useful early measurements are opposing return, wick balance, range expansion, volume expansion, and persistence across several bars.",
        "A stronger structural label requires a prior swing high/low breach plus follow-through. The current database does not store explicit swing prices, so this confirmation cannot yet be measured directly.",
        "",
        "## 6. Power versus pressure",
        "- Power: range × volume; identifies participation and expansion.",
        "- Pressure: directional body/return and wick imbalance; identifies acceptance, rejection, or absorption.",
        "- A large candle with a long opposing wick can be high-power but failed-pressure behavior.",
        "",
        "## 7. Data gaps before signal design",
    ]
    lines.extend(f"- {item}" for item in data["interpretation"]["missing_event_fields"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract BTCUSDT event-point behavior.")
    parser.add_argument("--database", required=True)
    parser.add_argument("--json-output", default="BTCUSDT_event_point_personality.json")
    parser.add_argument("--report-output", default="BTCUSDT_event_point_report.md")
    args = parser.parse_args()
    data = build_event_points(args.database)
    Path(args.json_output).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    Path(args.report_output).write_text(report(data), encoding="utf-8")


if __name__ == "__main__":
    main()
