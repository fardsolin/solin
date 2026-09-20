"""Reject partial, duplicate, out-of-order and gapped closed candles."""

from .models import Candle, HOUR_MS

CLOSE_GRACE_MS = 5_000


def parse_closed(raw, now_ms):
    candles = []
    for row in raw:
        timestamp = int(row["created_at"])
        if timestamp + HOUR_MS + CLOSE_GRACE_MS > now_ms:
            continue
        candles.append(
            Candle(timestamp, *(float(row[key]) for key in ["open", "high", "low", "close", "volume"]))
        )
    candles.sort(key=lambda c: c.time)
    for previous, current in zip(candles, candles[1:]):
        if current.time != previous.time + HOUR_MS:
            raise ValueError("Duplicate or missing candle in public response")
    return candles


def fetch_closed(client, last_time, now_ms):
    if last_time is None:
        return parse_closed(client.get_klines(limit=805), now_ms)
    end = ((now_ms - CLOSE_GRACE_MS) // HOUR_MS - 1) * HOUR_MS
    cursor = last_time + HOUR_MS
    result = []
    while cursor <= end:
        batch_end = min(end, cursor + 999 * HOUR_MS)
        raw = client.get_klines(limit=1000, start_time=cursor, end_time=batch_end + HOUR_MS - 1)
        candles = parse_closed(raw, now_ms)
        candles = [c for c in candles if cursor <= c.time <= batch_end]
        if not candles or candles[0].time != cursor or candles[-1].time != batch_end:
            raise ValueError("Incomplete backfill; trading checkpoint not advanced")
        result.extend(candles)
        cursor = batch_end + HOUR_MS
    return result
