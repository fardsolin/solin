"""UTC hourly candle contract shared by polling, replay and backtesting."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import math

HOUR_MS = 3_600_000


def iso_time(timestamp_ms):
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat()


@dataclass(frozen=True)
class Candle:
    time: int  # UTC opening timestamp, milliseconds; close is time + HOUR_MS.
    open: float
    high: float
    low: float
    close: float
    volume: float
    taker_buy: float | None = None

    def __post_init__(self):
        if not isinstance(self.time, int) or self.time < 0 or self.time % HOUR_MS:
            raise ValueError("Candle time must be an aligned UTC hourly millisecond timestamp")
        values = [self.open, self.high, self.low, self.close, self.volume]
        if not all(math.isfinite(v) for v in values):
            raise ValueError("OHLCV must be finite")
        if min(values[:4]) <= 0 or self.volume < 0:
            raise ValueError("Prices must be positive and volume nonnegative")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("Invalid OHLC bounds")
        if self.taker_buy is not None:
            if not math.isfinite(self.taker_buy) or not 0 <= self.taker_buy <= self.volume:
                raise ValueError("Invalid taker-buy base volume")

    def to_dict(self):
        return asdict(self)


@dataclass
class Position:
    trade_id: str
    direction: str
    entry_time: int
    entry_price: float
    quantity: float
    initial_stop: float
    current_stop: float
    target: float
    entry_fee: float
    entry_equity: float
    risk_budget: float
    signal_source: str
    funding: float = 0.0
    bars_held: int = 0
    peak_favorable: float = 0.0

    @property
    def sign(self):
        return 1 if self.direction == "long" else -1
