"""Causal streaming v5 signal rules, corrected H4 alignment, genuine optional flow.

Execution belongs to PaperBroker/TradingSession. Indicator state is bounded and
serializable: replay, polling and restart use exactly the same calculations.
"""

from collections import deque
from datetime import datetime, timezone

import numpy as np

from .models import Candle, HOUR_MS

LOOKBACK = 20
COMPRESSION_TRAIL = 250
BODY_RATIO_MIN = 0.55
OPPOSING_WICK_MAX = 0.25
VOLUME_MULT_MIN = 1.2
RANGE_WIDTH_PCTL_MAX = 0.5
BAD_HOURS_UTC = {19, 20, 21, 0}
BAD_WEEKDAY = 5
H4_STRENGTH_REJECT_PCTL = 0.9
H4_STRENGTH_WINDOW = 500
WHALE_PERCENTILE = 0.95
HOLD_BARS = 10
RR = 2.0
STAIRCASE_TIERS = ((0.007, 0.90), (0.015, 0.93), (0.03, 0.96), (0.05, 0.97))
MIN_HISTORY_BARS = 800


def percentile_rank(values):
    a = np.asarray(values)
    return float((np.count_nonzero(a < a[-1]) + (np.count_nonzero(a == a[-1]) + 1) / 2) / len(a))


class StrategyEngine:
    def __init__(self, enable_whale=False, state=None):
        self.enable_whale = enable_whale
        state = state or {}
        self.count = state.get("count", 0)
        self.last_time = state.get("last_time")
        self.prior = deque(state.get("prior", []), maxlen=LOOKBACK)
        self.avg_ranges = deque(state.get("avg_ranges", []), maxlen=COMPRESSION_TRAIL)
        self.widths = deque(state.get("widths", []), maxlen=COMPRESSION_TRAIL)
        self.buy = deque(state.get("buy", []), maxlen=COMPRESSION_TRAIL)
        self.sell = deque(state.get("sell", []), maxlen=COMPRESSION_TRAIL)
        self.strengths = deque(state.get("strengths", []), maxlen=H4_STRENGTH_WINDOW)
        self.h4_times = list(state.get("h4_times", []))
        self.ema_fast = state.get("ema_fast")
        self.ema_slow = state.get("ema_slow")

    def snapshot(self):
        return {
            name: list(value) if isinstance(value, deque) else value
            for name, value in vars(self).items()
            if name != "enable_whale"
        }

    def update(self, c: Candle):
        if self.last_time is not None and c.time != self.last_time + HOUR_MS:
            raise ValueError("Out-of-order, duplicate or missing candle")
        if self.enable_whale and c.taker_buy is None:
            raise ValueError("Whale requires genuine taker-buy data; no proxy is permitted")
        # Only publish a complete UTC [00,04), [04,08), ... H4 candle.
        if self.h4_times and self.h4_times[0] // (4 * HOUR_MS) != c.time // (4 * HOUR_MS):
            self.h4_times = []
        self.h4_times.append(c.time)
        if len(self.h4_times) == 4 and (c.time // HOUR_MS) % 4 == 3:
            self.ema_fast = (
                c.close if self.ema_fast is None else (2 / 11) * c.close + (9 / 11) * self.ema_fast
            )
            self.ema_slow = (
                c.close if self.ema_slow is None else (2 / 31) * c.close + (29 / 31) * self.ema_slow
            )
            self.h4_times = []
        up = down = False
        strength_rank = 0.0
        if self.ema_slow is not None:
            up, down = self.ema_fast > self.ema_slow, self.ema_fast < self.ema_slow
            self.strengths.append(abs(self.ema_fast - self.ema_slow) / self.ema_slow)
            if len(self.strengths) >= H4_STRENGTH_WINDOW // 2:
                strength_rank = percentile_rank(self.strengths)

        whale_buy = whale_sell = False
        if self.enable_whale and len(self.buy) == COMPRESSION_TRAIL:
            delta = 2 * c.taker_buy - c.volume
            whale_buy = delta > 0 and c.taker_buy > np.quantile(self.buy, WHALE_PERCENTILE)
            whale_sell = delta < 0 and c.volume - c.taker_buy > np.quantile(self.sell, WHALE_PERCENTILE)
        if c.taker_buy is not None:
            self.buy.append(c.taker_buy)
            self.sell.append(c.volume - c.taker_buy)
        else:
            self.buy.clear()
            self.sell.clear()

        signal = None
        roll_high = roll_low = avg_volume = avg_range = None
        if len(self.prior) == LOOKBACK:
            roll_high = max(p["high"] for p in self.prior)
            roll_low = min(p["low"] for p in self.prior)
            avg_volume = sum(p["volume"] for p in self.prior) / LOOKBACK
            avg_range = sum(p["high"] - p["low"] for p in self.prior) / LOOKBACK
            self.avg_ranges.append(avg_range)
            self.widths.append((roll_high - roll_low) / roll_low)
        self.prior.append(c.to_dict())
        self.last_time = c.time
        self.count += 1
        if self.count < MIN_HISTORY_BARS or len(self.widths) < COMPRESSION_TRAIL:
            return None
        date = datetime.fromtimestamp(c.time / 1000, timezone.utc)
        good_time = date.hour not in BAD_HOURS_UTC and date.weekday() != BAD_WEEKDAY
        width = c.high - c.low
        body = abs(c.close - c.open) / width if width else 0
        upper = (c.high - max(c.open, c.close)) / width if width else 0
        lower = (min(c.open, c.close) - c.low) / width if width else 0
        common = (
            good_time
            and body >= BODY_RATIO_MIN
            and c.volume >= VOLUME_MULT_MIN * avg_volume
            and avg_range <= float(np.median(self.avg_ranges))
            and percentile_rank(self.widths) <= RANGE_WIDTH_PCTL_MAX
        )
        strong = strength_rank >= H4_STRENGTH_REJECT_PCTL
        core_long = (
            common and c.close > roll_high * 1.001 and lower <= OPPOSING_WICK_MAX and not (down and strong)
        )
        core_short = (
            common and c.close < roll_low * 0.999 and upper <= OPPOSING_WICK_MAX and not (up and strong)
        )
        whale_long, whale_short = whale_buy and up and good_time, whale_sell and down and good_time
        for direction, core, whale, stop in [
            ("long", core_long, whale_long, roll_low),
            ("short", core_short, whale_short, roll_high),
        ]:
            valid = c.close > stop if direction == "long" else c.close < stop
            if valid and (core or whale):
                signal = {
                    "direction": direction,
                    "stop": stop,
                    "signal_time": c.time,
                    "reference_price": c.close,
                    "signal_source": "both" if core and whale else "whale" if whale else "breakout",
                }
                break
        return signal
