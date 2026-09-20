"""Strict canonical CSV input; no gap filling, resampling, or fabricated order flow."""

from datetime import datetime, timezone
import hashlib
from pathlib import Path

import pandas as pd

from engine.models import Candle, HOUR_MS


def read_candles(path, as_of_ms=None):
    frame = pd.read_csv(path)
    required = ["time", "open", "high", "low", "close", "volume"]
    if not set(required) <= set(frame.columns):
        raise ValueError(f"CSV must contain {required}; optional: taker_buy")
    if frame.empty:
        raise ValueError("Empty candle dataset")
    if as_of_ms is None:
        as_of_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    candles = []
    for row in frame.to_dict("records"):
        raw_time = row["time"]
        timestamp = int(raw_time)
        if timestamp != raw_time:
            raise ValueError("Use integer UTC millisecond opening timestamps")
        flow = row.get("taker_buy")
        c = Candle(
            timestamp,
            *(float(row[k]) for k in required[1:]),
            None if flow is None or pd.isna(flow) else float(flow),
        )
        if c.time + HOUR_MS > as_of_ms:
            raise ValueError("Future or not-yet-closed historical candle")
        if candles and c.time != candles[-1].time + HOUR_MS:
            raise ValueError(f"Duplicate, out-of-order or gap at {c.time}; refusing to fabricate bars")
        candles.append(c)
    return candles


def read_funding(path):
    frame = pd.read_csv(path)
    if list(frame.columns) != ["time", "rate"] or frame["time"].duplicated().any():
        raise ValueError("Funding CSV needs unique UTC hourly time,rate columns")
    result = {}
    for time, rate in frame.itertuples(index=False, name=None):
        if int(time) != time or int(time) % HOUR_MS or not pd.notna(rate) or not -0.05 <= rate <= 0.05:
            raise ValueError("Invalid funding settlement")
        result[int(time)] = float(rate)
    return result


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
