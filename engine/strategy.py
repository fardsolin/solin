"""
engine/strategy.py
====================
همان منطق اعتبارسنجی‌شدهٔ v5 (شکست فشرده + سیگنال مستقل Whale + تریلینگ پلکانی)،
اما بازنویسی‌شده برای اجرای زنده: به‌جای پردازش یک بلوک تاریخی، هر بار که یک
کندل ۱ساعتهٔ جدید *کاملاً بسته* می‌شود صدا زده می‌شود و یا سیگنال ورود می‌دهد یا
وضعیت معاملهٔ باز را به‌روزرسانی می‌کند.

نکتهٔ حیاتی ضدتقلب: این کلاس هرگز به کندل در حال شکل‌گیری (partial candle) دسترسی
پیدا نمی‌کند -- فقط با last_closed_candle کار می‌کند، دقیقاً مثل بک‌تست.
"""
import numpy as np
import pandas as pd

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

MIN_HISTORY_BARS = COMPRESSION_TRAIL + H4_STRENGTH_WINDOW + 50  # حاشیهٔ اطمینان


class OpenTrade:
    def __init__(self, direction, entry_time, entry_price, initial_stop, target, bar_index):
        self.direction = direction
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.initial_stop = initial_stop
        self.current_stop = initial_stop
        self.target = target
        self.entry_bar_index = bar_index
        self.peak_favorable = 0.0

    def to_dict(self):
        return dict(direction=self.direction, entry_time=str(self.entry_time),
                    entry_price=self.entry_price, initial_stop=self.initial_stop,
                    current_stop=self.current_stop, target=self.target,
                    peak_favorable_pct=round(self.peak_favorable * 100, 3))


class StrategyEngine:
    """نگه‌دارندهٔ حافظهٔ کندل‌ها (۱ساعته + ۴ساعتهٔ مشتق‌شده) و منطق سیگنال/مدیریت معامله."""

    def __init__(self):
        self.candles = pd.DataFrame(columns=["Time", "Open", "High", "Low", "Close", "Volume", "TakerBuyBase"])
        self.open_trade: OpenTrade = None

    def load_history(self, df: pd.DataFrame):
        """df باید ستون‌های Time, Open, High, Low, Close, Volume, TakerBuyBase داشته باشد،
        مرتب بر اساس زمان، شامل فقط کندل‌های کاملاً بسته‌شده."""
        self.candles = df.sort_values("Time").reset_index(drop=True)

    def append_closed_candle(self, candle: dict):
        """یک کندل ۱ساعتهٔ تازه‌بسته‌شده اضافه می‌شود. فقط بعد از این تابع باید
        check_new_bar() صدا زده شود."""
        row = pd.DataFrame([candle])
        self.candles = pd.concat([self.candles, row], ignore_index=True)
        # فقط تاریخچهٔ لازم را نگه می‌داریم (جلوگیری از رشد بی‌نهایت حافظه)
        max_keep = MIN_HISTORY_BARS + 200
        if len(self.candles) > max_keep:
            self.candles = self.candles.iloc[-max_keep:].reset_index(drop=True)

    def _engineer(self):
        d = self.candles.copy()
        d["Range"] = d["High"] - d["Low"]
        d["Body"] = (d["Close"] - d["Open"]).abs()
        d["BodyRatio"] = np.where(d["Range"] > 0, d["Body"] / d["Range"], 0)
        upper = d["High"] - d[["Open", "Close"]].max(axis=1)
        lower = d[["Open", "Close"]].min(axis=1) - d["Low"]
        d["UpperShadowRatio"] = np.where(d["Range"] > 0, upper / d["Range"], 0)
        d["LowerShadowRatio"] = np.where(d["Range"] > 0, lower / d["Range"], 0)
        d["roll_high"] = d["High"].shift(1).rolling(LOOKBACK).max()
        d["roll_low"] = d["Low"].shift(1).rolling(LOOKBACK).min()
        d["avg_vol"] = d["Volume"].shift(1).rolling(LOOKBACK).mean()
        d["avg_range"] = d["Range"].shift(1).rolling(LOOKBACK).mean()
        d["trail_median_range"] = d["avg_range"].rolling(COMPRESSION_TRAIL, min_periods=COMPRESSION_TRAIL).median()
        d["range_width_pct"] = (d["roll_high"] - d["roll_low"]) / d["roll_low"]
        d["range_width_rank"] = d["range_width_pct"].rolling(COMPRESSION_TRAIL, min_periods=COMPRESSION_TRAIL).rank(pct=True)
        hour = pd.to_datetime(d["Time"], utc=True).dt.hour
        weekday = pd.to_datetime(d["Time"], utc=True).dt.weekday
        d["good_time_window"] = (~hour.isin(BAD_HOURS_UTC)) & (weekday != BAD_WEEKDAY)

        d["Delta"] = 2 * d["TakerBuyBase"] - d["Volume"]
        sell_vol = d["Volume"] - d["TakerBuyBase"]
        d["whale_buy_th"] = d["TakerBuyBase"].shift(1).rolling(COMPRESSION_TRAIL, min_periods=COMPRESSION_TRAIL).quantile(WHALE_PERCENTILE)
        d["whale_sell_th"] = sell_vol.shift(1).rolling(COMPRESSION_TRAIL, min_periods=COMPRESSION_TRAIL).quantile(WHALE_PERCENTILE)
        d["WhaleBuy"] = (d["TakerBuyBase"] > d["whale_buy_th"]) & (d["Delta"] > 0)
        d["WhaleSell"] = (sell_vol > d["whale_sell_th"]) & (d["Delta"] < 0)

        # زمینهٔ ۴ساعته از همان کندل‌های ۱ساعته resample می‌شود
        h4 = d.set_index(pd.to_datetime(d["Time"], utc=True))[["Open", "High", "Low", "Close"]].resample(
            "4h", label="right", closed="right").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
        ema_fast = h4["Close"].ewm(span=10, adjust=False).mean()
        ema_slow = h4["Close"].ewm(span=30, adjust=False).mean()
        h4["trend_up"] = ema_fast > ema_slow
        h4["trend_down"] = ema_fast < ema_slow
        h4["trend_strength"] = (ema_fast - ema_slow).abs() / ema_slow
        h4_shifted = h4.shift(1)
        mapped = h4_shifted.reindex(pd.to_datetime(d["Time"], utc=True), method="ffill")
        mapped.index = d.index
        d["h4_trend_up"] = mapped["trend_up"]
        d["h4_trend_down"] = mapped["trend_down"]
        d["h4_strength_rank"] = mapped["trend_strength"].rolling(H4_STRENGTH_WINDOW, min_periods=H4_STRENGTH_WINDOW // 2).rank(pct=True)
        return d

    def ready(self) -> bool:
        return len(self.candles) >= MIN_HISTORY_BARS

    def check_new_bar(self):
        """باید دقیقاً یک‌بار بعد از append_closed_candle صدا زده شود.
        خروجی: dict با یکی از این حالت‌ها:
          {'action': 'none'}
          {'action': 'enter', 'direction':'long'/'short', 'entry_price':..., 'stop':..., 'target':...}
          {'action': 'exit', 'exit_price':..., 'reason':...}
          {'action': 'hold'}  (پوزیشن باز است، هنوز شرط خروج نرسیده، ولی استاپ به‌روزرسانی شد)
        """
        if not self.ready():
            return {"action": "none", "reason": "insufficient_history"}

        d = self._engineer()
        last = d.iloc[-1]
        last_idx = len(d) - 1

        if self.open_trade is not None:
            return self._manage_open_trade(last)

        core_long = (
            last["Close"] > last["roll_high"] * 1.001 and
            last["BodyRatio"] >= BODY_RATIO_MIN and
            last["LowerShadowRatio"] <= OPPOSING_WICK_MAX and
            last["Volume"] >= VOLUME_MULT_MIN * last["avg_vol"] and
            last["avg_range"] <= last["trail_median_range"] and
            last["range_width_rank"] <= RANGE_WIDTH_PCTL_MAX and
            bool(last["good_time_window"])
        )
        core_short = (
            last["Close"] < last["roll_low"] * 0.999 and
            last["BodyRatio"] >= BODY_RATIO_MIN and
            last["UpperShadowRatio"] <= OPPOSING_WICK_MAX and
            last["Volume"] >= VOLUME_MULT_MIN * last["avg_vol"] and
            last["avg_range"] <= last["trail_median_range"] and
            last["range_width_rank"] <= RANGE_WIDTH_PCTL_MAX and
            bool(last["good_time_window"])
        )
        strong_opp = (last["h4_strength_rank"] >= H4_STRENGTH_REJECT_PCTL) if not pd.isna(last["h4_strength_rank"]) else False
        if core_long and bool(last["h4_trend_down"]) and strong_opp:
            core_long = False
        if core_short and bool(last["h4_trend_up"]) and strong_opp:
            core_short = False

        whale_long = bool(last["WhaleBuy"]) and bool(last["h4_trend_up"]) and bool(last["good_time_window"])
        whale_short = bool(last["WhaleSell"]) and bool(last["h4_trend_down"]) and bool(last["good_time_window"])

        take_long = core_long or whale_long
        take_short = core_short or whale_short

        if take_long:
            entry_price = last["Close"]  # ورود نظری در قیمت close؛ اجرای واقعی با اردر مارکت روی کندل بعد خواهد بود
            stop = last["roll_low"]
            if entry_price - stop > 0:
                target = entry_price + RR * (entry_price - stop)
                source = "whale" if (whale_long and not core_long) else ("both" if (whale_long and core_long) else "breakout")
                return {"action": "enter", "direction": "long", "entry_price_ref": entry_price,
                        "stop": stop, "target": target, "signal_source": source}
        if take_short:
            entry_price = last["Close"]
            stop = last["roll_high"]
            if stop - entry_price > 0:
                target = entry_price - RR * (stop - entry_price)
                source = "whale" if (whale_short and not core_short) else ("both" if (whale_short and core_short) else "breakout")
                return {"action": "enter", "direction": "short", "entry_price_ref": entry_price,
                        "stop": stop, "target": target, "signal_source": source}

        return {"action": "none"}

    def open_position(self, direction, entry_price, stop, target):
        self.open_trade = OpenTrade(direction, self.candles["Time"].iloc[-1], entry_price, stop, target, len(self.candles) - 1)

    def _manage_open_trade(self, last_row):
        t = self.open_trade
        hi, lo, cl = last_row["High"], last_row["Low"], last_row["Close"]
        fav = (hi - t.entry_price) / t.entry_price if t.direction == "long" else (t.entry_price - lo) / t.entry_price
        t.peak_favorable = max(t.peak_favorable, fav)
        locked = 0.0
        for trig, lock_pct in STAIRCASE_TIERS:
            if t.peak_favorable >= trig:
                locked = max(locked, lock_pct * t.peak_favorable)
        if locked > 0:
            trail = t.entry_price * (1 + locked) if t.direction == "long" else t.entry_price * (1 - locked)
            t.current_stop = max(t.current_stop, trail) if t.direction == "long" else min(t.current_stop, trail)

        bars_held = (len(self.candles) - 1) - t.entry_bar_index
        if t.direction == "long":
            if lo <= t.current_stop:
                return self._close("stop" if t.current_stop == t.initial_stop else "staircase-lock", t.current_stop)
            if hi >= t.target:
                return self._close("target", t.target)
        else:
            if hi >= t.current_stop:
                return self._close("stop" if t.current_stop == t.initial_stop else "staircase-lock", t.current_stop)
            if lo <= t.target:
                return self._close("target", t.target)

        if bars_held >= HOLD_BARS:
            return self._close("time-exit", cl)

        return {"action": "hold", "open_trade": t.to_dict()}

    def _close(self, reason, exit_price):
        t = self.open_trade
        result = {"action": "exit", "reason": reason, "exit_price": exit_price,
                   "direction": t.direction, "entry_price": t.entry_price,
                   "initial_stop": t.initial_stop, "entry_time": str(t.entry_time)}
        self.open_trade = None
        return result
