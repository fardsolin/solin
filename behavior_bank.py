"""Market-behavior database extraction and audit utilities.

This module deliberately produces behavior records only. It does not create
orders, entries, exits, or trading signals.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


REQUIRED_COLUMNS = ("timestamp", "open", "high", "low", "close", "volume")
TRANSITION_TYPES = (
    "trend_to_correction",
    "correction_to_trend",
    "trend_to_reversal",
    "range_to_breakout",
)


def _native(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_native(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def load_ohlcv(path: str | Path, timeframe: str = "1h") -> pd.DataFrame:
    source = Path(path)
    frame = pd.read_parquet(source) if source.suffix.lower() == ".parquet" else pd.read_csv(source)
    columns = {str(column).lower(): column for column in frame.columns}
    missing = [column for column in REQUIRED_COLUMNS if column not in columns]
    if missing:
        raise ValueError(f"{source}: missing required columns: {missing}")

    renamed = frame.rename(columns={columns[key]: key for key in REQUIRED_COLUMNS})
    timestamp = renamed["timestamp"]
    if pd.api.types.is_numeric_dtype(timestamp):
        unit = "ms" if timestamp.dropna().abs().max() > 10_000_000_000 else "s"
        timestamp = pd.to_datetime(timestamp, unit=unit, utc=True)
    else:
        timestamp = pd.to_datetime(timestamp, utc=True)

    result = renamed[list(REQUIRED_COLUMNS)].copy()
    result["timestamp"] = timestamp
    for column in REQUIRED_COLUMNS[1:]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = (
        result.dropna()
        .sort_values("timestamp")
        .drop_duplicates("timestamp")
        .set_index("timestamp")
    )
    result = result[(result["high"] >= result["low"]) & (result["volume"] >= 0)]
    return result.resample(timeframe).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()


def _segment_ids(frame: pd.DataFrame, window: int = 24, minimum: int = 12) -> pd.Series:
    returns = frame["close"].pct_change(window)
    direction = returns.where(returns.abs() >= returns.abs().rolling(window * 3, min_periods=window).median())
    direction = np.sign(direction.fillna(0)).replace(0, np.nan).ffill().fillna(0)
    groups = direction.ne(direction.shift()).cumsum()
    counts = groups.value_counts()
    short = groups.map(counts).lt(minimum)
    direction = direction.mask(short).ffill().fillna(0)
    return direction.ne(direction.shift()).cumsum()


def _window_features(frame: pd.DataFrame, prefix: str) -> dict[str, float | None]:
    if frame.empty:
        return {}
    close = frame["close"]
    returns = close.pct_change().dropna()
    ranges = (frame["high"] - frame["low"]) / close.replace(0, np.nan)
    volume = frame["volume"]
    features: dict[str, float | None] = {}
    for statistic, value in (
        ("return", close.iloc[-1] / close.iloc[0] - 1),
        ("volatility", returns.std()),
        ("range_mean", ranges.mean()),
        ("range_max", ranges.max()),
        ("volume_mean", volume.mean()),
        ("volume_first", volume.iloc[: max(1, len(volume) // 3)].mean()),
        ("volume_middle", volume.iloc[len(volume) // 3 : max(1, len(volume) * 2 // 3)].mean()),
        ("volume_last", volume.iloc[max(1, len(volume) * 2 // 3) :].mean()),
        ("close_position", (close.iloc[-1] - frame["low"].min()) / max(frame["high"].max() - frame["low"].min(), 1e-12)),
    ):
        features[f"{prefix}_{statistic}"] = float(value) if pd.notna(value) else None
    return features


def _trend_record(frame: pd.DataFrame, start: int, end: int, trend_id: str) -> dict[str, Any]:
    segment = frame.iloc[start : end + 1]
    close = segment["close"]
    returns = close.pct_change().dropna()
    slope = np.polyfit(np.arange(len(close)), close.to_numpy(), 1)[0] if len(close) > 1 else 0.0
    direction = "up" if close.iloc[-1] >= close.iloc[0] else "down"
    thirds = np.array_split(segment, 3)
    record = {
        "trend_id": trend_id,
        "asset": "",
        "start_time": segment.index[0].isoformat(),
        "end_time": segment.index[-1].isoformat(),
        "direction": direction,
        "duration_hours": (segment.index[-1] - segment.index[0]).total_seconds() / 3600,
        "movement_pct": float((close.iloc[-1] / close.iloc[0] - 1) * 100),
        "slope": float(slope),
        "velocity": float(returns.mean() if not returns.empty else 0),
        "acceleration": float(returns.diff().mean() if len(returns) > 1 else 0),
        "wave_count": int((np.sign(returns).diff().abs() > 0).sum()),
        "correction_count": 0,
        "correction_depth_mean": 0.0,
        "volume_mean": float(segment["volume"].mean()),
        "volume_start": float(thirds[0]["volume"].mean()),
        "volume_middle": float(thirds[1]["volume"].mean()),
        "volume_end": float(thirds[2]["volume"].mean()),
        "delta_available": 0,
        "delta_change": None,
        "buy_sell_pressure": None,
        "swing_high_strength": float((segment["high"].max() - close.iloc[-1]) / max(close.iloc[-1], 1e-12)),
        "swing_low_strength": float((close.iloc[-1] - segment["low"].min()) / max(close.iloc[-1], 1e-12)),
        "feature_json": json.dumps(_native(_window_features(segment, "trend")), sort_keys=True),
    }
    return record


def _range_record(frame: pd.DataFrame, start: int, end: int, range_id: str) -> dict[str, Any]:
    segment = frame.iloc[start : end + 1]
    high, low = segment["high"].max(), segment["low"].min()
    width = (high - low) / max(segment["close"].mean(), 1e-12)
    return {
        "range_id": range_id,
        "asset": "",
        "start_time": segment.index[0].isoformat(),
        "end_time": segment.index[-1].isoformat(),
        "duration_hours": (segment.index[-1] - segment.index[0]).total_seconds() / 3600,
        "width_pct": float(width * 100),
        "upper_touches": int((segment["high"] >= high * 0.998).sum()),
        "lower_touches": int((segment["low"] <= low * 1.002).sum()),
        "volume_mean": float(segment["volume"].mean()),
        "delta_available": 0,
        "delta_behavior": "unavailable",
        "successful_breakout": None,
        "false_breakout": None,
        "feature_json": json.dumps(_native(_window_features(segment, "range")), sort_keys=True),
    }


def extract_behavior(asset: str, frame: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    segment_ids = _segment_ids(frame)
    trends: list[dict[str, Any]] = []
    ranges: list[dict[str, Any]] = []
    for _, group in frame.groupby(segment_ids):
        start, end = frame.index.get_loc(group.index[0]), frame.index.get_loc(group.index[-1])
        if len(group) < 12:
            continue
        trend = _trend_record(frame, start, end, f"{asset}-trend-{len(trends) + 1:05d}")
        trend["asset"] = asset
        trends.append(trend)

    rolling_width = (frame["high"].rolling(24).max() - frame["low"].rolling(24).min()) / frame["close"]
    quiet = rolling_width < rolling_width.rolling(240, min_periods=24).quantile(0.35)
    range_groups = quiet.ne(quiet.shift()).cumsum()
    for _, group in frame[quiet].groupby(range_groups[quiet]):
        if len(group) < 24:
            continue
        start, end = frame.index.get_loc(group.index[0]), frame.index.get_loc(group.index[-1])
        record = _range_record(frame, start, end, f"{asset}-range-{len(ranges) + 1:05d}")
        record["asset"] = asset
        ranges.append(record)

    corrections: list[dict[str, Any]] = []
    for trend in trends:
        start = frame.index.get_indexer([pd.Timestamp(trend["start_time"])])[0]
        end = frame.index.get_indexer([pd.Timestamp(trend["end_time"])])[0]
        segment = frame.iloc[start : end + 1]
        local = segment["close"].pct_change().fillna(0)
        opposite = np.sign(local) != np.sign(trend["movement_pct"])
        for run_id, correction in segment[opposite].groupby(opposite[opposite].ne(opposite[opposite].shift()).cumsum()):
            if len(correction) < 3:
                continue
            cstart, cend = frame.index.get_loc(correction.index[0]), frame.index.get_loc(correction.index[-1])
            part = frame.iloc[cstart : cend + 1]
            corrections.append(
                {
                    "correction_id": f"{asset}-correction-{len(corrections) + 1:05d}",
                    "asset": asset,
                    "parent_trend_id": trend["trend_id"],
                    "start_time": part.index[0].isoformat(),
                    "end_time": part.index[-1].isoformat(),
                    "retracement_pct": float(abs(part["close"].iloc[-1] / part["close"].iloc[0] - 1) * 100),
                    "duration_hours": (part.index[-1] - part.index[0]).total_seconds() / 3600,
                    "volume_change": float(part["volume"].iloc[-1] / max(part["volume"].iloc[0], 1e-12) - 1),
                    "delta_behavior": "unavailable",
                    "start_type": "opposite_price_move",
                    "outcome": "continuation" if cend < end else "unknown",
                }
            )
    for trend in trends:
        trend["correction_count"] = sum(c["parent_trend_id"] == trend["trend_id"] for c in corrections)
    return {"trends": trends, "corrections": corrections, "ranges": ranges}


def build_transitions(asset: str, frame: pd.DataFrame, extracted: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    boundaries = [*extracted["trends"], *extracted["ranges"]]
    for item in boundaries:
        timestamp = pd.Timestamp(item["end_time"])
        index = frame.index.get_indexer([timestamp])[0]
        if index < 20 or index + 10 >= len(frame):
            continue
        if item in extracted["trends"]:
            event_type = "trend_to_correction" if item["correction_count"] else "trend_to_reversal"
        else:
            event_type = "range_to_breakout"
        before, after = frame.iloc[index - 20 : index], frame.iloc[index + 1 : index + 11]
        features = {}
        for size, window in (("before_20", before), ("after_10", after)):
            features.update(_window_features(window, size))
        for offset, candle in enumerate(pd.concat([before, after]).itertuples()):
            base = "before" if offset < 20 else "after"
            number = offset + 1 if offset < 20 else offset - 19
            candle_range = max(candle.high - candle.low, 1e-12)
            features.update(
                {
                    f"{base}_{number:02d}_return": float(candle.close / candle.open - 1),
                    f"{base}_{number:02d}_range": float(candle_range / max(candle.close, 1e-12)),
                    f"{base}_{number:02d}_volume": float(candle.volume),
                    f"{base}_{number:02d}_body": float(abs(candle.close - candle.open) / candle_range),
                    f"{base}_{number:02d}_upper_wick": float((candle.high - max(candle.open, candle.close)) / candle_range),
                    f"{base}_{number:02d}_lower_wick": float((min(candle.open, candle.close) - candle.low) / candle_range),
                }
            )
        transitions.append(
            {
                "event_id": f"{asset}-transition-{len(transitions) + 1:05d}",
                "asset": asset,
                "timestamp": timestamp.isoformat(),
                "event_type": event_type,
                "before_candles": 20,
                "after_candles": 10,
                "feature_count": len(features),
                "feature_json": json.dumps(_native(features), sort_keys=True),
            }
        )
    return transitions


def behavior_comparison(extracted: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Evaluate similarity of behavior records without creating trade signals."""
    trends = extracted["trends"]
    if len(trends) < 2:
        return {"status": "insufficient_history", "comparisons": []}
    fields = ("movement_pct", "slope", "velocity", "acceleration", "wave_count", "volume_mean")
    matrix = np.array([[float(row[field]) for field in fields] for row in trends], dtype=float)
    scale = np.nanstd(matrix, axis=0)
    scale[scale == 0] = 1
    normalized = matrix / scale
    comparisons = []
    for index, row in enumerate(trends):
        prior = np.delete(normalized, index, axis=0)
        distances = np.sqrt(((prior - normalized[index]) ** 2).mean(axis=1))
        nearest = np.sort(distances)[: min(5, len(distances))]
        comparisons.append(
            {
                "trend_id": row["trend_id"],
                "prior_samples": len(prior),
                "nearest_behavior_distance": float(nearest.mean()),
                "similarity_score": float(1 / (1 + nearest.mean())),
                "outcome": "descriptive_only",
            }
        )
    return {"status": "complete", "comparisons": comparisons}


def write_database(path: str | Path, asset: str, extracted: dict[str, list[dict[str, Any]]], transitions: list[dict[str, Any]]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(destination) as connection:
        connection.executescript(
            """
            DROP TABLE IF EXISTS trends;
            DROP TABLE IF EXISTS corrections;
            DROP TABLE IF EXISTS ranges;
            DROP TABLE IF EXISTS transitions;
            DROP TABLE IF EXISTS rare_cases;
            DROP TABLE IF EXISTS market_personality;
            CREATE TABLE trends (
                trend_id TEXT PRIMARY KEY, asset TEXT, start_time TEXT, end_time TEXT,
                direction TEXT, duration_hours REAL, movement_pct REAL, slope REAL,
                velocity REAL, acceleration REAL, wave_count INTEGER, correction_count INTEGER,
                correction_depth_mean REAL, volume_mean REAL, volume_start REAL,
                volume_middle REAL, volume_end REAL, delta_available INTEGER,
                delta_change REAL, buy_sell_pressure REAL, swing_high_strength REAL,
                swing_low_strength REAL, feature_json TEXT NOT NULL
            );
            CREATE TABLE corrections (
                correction_id TEXT PRIMARY KEY, asset TEXT, parent_trend_id TEXT,
                start_time TEXT, end_time TEXT, retracement_pct REAL, duration_hours REAL,
                volume_change REAL, delta_behavior TEXT, start_type TEXT, outcome TEXT
            );
            CREATE TABLE ranges (
                range_id TEXT PRIMARY KEY, asset TEXT, start_time TEXT, end_time TEXT,
                duration_hours REAL, width_pct REAL, upper_touches INTEGER, lower_touches INTEGER,
                volume_mean REAL, delta_available INTEGER, delta_behavior TEXT,
                successful_breakout INTEGER, false_breakout INTEGER, feature_json TEXT NOT NULL
            );
            CREATE TABLE transitions (
                event_id TEXT PRIMARY KEY, asset TEXT, timestamp TEXT, event_type TEXT,
                before_candles INTEGER, after_candles INTEGER, feature_count INTEGER,
                feature_json TEXT NOT NULL
            );
            CREATE TABLE rare_cases (
                event_id TEXT PRIMARY KEY, asset TEXT, timestamp TEXT, event_type TEXT,
                rarity_score REAL, source_transition_id TEXT
            );
            CREATE TABLE market_personality (
                asset TEXT PRIMARY KEY, trend_count INTEGER, correction_count INTEGER,
                range_count INTEGER, transition_count INTEGER, summary_json TEXT NOT NULL
            );
            """
        )
        for table in ("trends", "corrections", "ranges", "transitions"):
            rows = extracted.get(table, []) if table != "transitions" else transitions
            if rows:
                columns = list(rows[0])
                placeholders = ",".join("?" for _ in columns)
                connection.executemany(
                    f"INSERT INTO {table} ({','.join(columns)}) VALUES ({placeholders})",
                    [[_native(row[column]) for column in columns] for row in rows],
                )
        connection.executemany(
            "INSERT INTO rare_cases VALUES (?, ?, ?, ?, ?, ?)",
            [
                (row["event_id"], asset, row["timestamp"], row["event_type"], 0.0, row["event_id"])
                for row in transitions
            ],
        )
        summary = {
            "asset": asset,
            "trend_count": len(extracted["trends"]),
            "correction_count": len(extracted["corrections"]),
            "range_count": len(extracted["ranges"]),
            "transition_count": len(transitions),
            "delta_status": "unavailable_without_trade_or_order_book_data",
        }
        connection.execute(
            "INSERT INTO market_personality VALUES (?, ?, ?, ?, ?, ?)",
            (asset, len(extracted["trends"]), len(extracted["corrections"]), len(extracted["ranges"]), len(transitions), json.dumps(summary)),
        )


def audit_database(path: str | Path) -> dict[str, Any]:
    result: dict[str, Any] = {"database": str(path), "tables": {}}
    with sqlite3.connect(path) as connection:
        tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for table in tables:
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()
            timestamp_columns = [column for column in columns if "time" in column.lower() or "date" in column.lower()]
            timestamps = [row[columns.index(timestamp_columns[0])] for row in rows if timestamp_columns and row[columns.index(timestamp_columns[0])] is not None]
            zero_features = 0
            nulls = 0
            for row in rows:
                for value in row:
                    nulls += value is None
                    zero_features += isinstance(value, (int, float)) and value == 0
            result["tables"][table] = {
                "records": len(rows),
                "valid_records": sum(all(value is not None for value in row) for row in rows),
                "null_values": nulls,
                "zero_numeric_features": zero_features,
                "first_timestamp": min(timestamps) if timestamps else None,
                "last_timestamp": max(timestamps) if timestamps else None,
                "columns": columns,
            }
    return result
