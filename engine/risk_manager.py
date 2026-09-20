"""UTC mark-to-market daily loss latch. Entry permission only, never exit gating."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone


@dataclass
class RiskManager:
    max_daily_loss: float = 0.03
    day: str | None = None
    day_start_equity: float = 0.0
    tripped: bool = False
    daily_pnl_pct: float = 0.0  # fraction internally; converted once for the API

    def roll_day(self, timestamp_ms, previous_equity):
        day = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).date().isoformat()
        if self.day != day:
            self.day = day
            self.day_start_equity = previous_equity
            self.tripped = previous_equity <= 0
            self.daily_pnl_pct = 0.0

    def observe(self, equity):
        self.daily_pnl_pct = equity / self.day_start_equity - 1 if self.day_start_equity > 0 else -1.0
        self.tripped = self.tripped or self.daily_pnl_pct <= -self.max_daily_loss or equity <= 0

    def can_enter(self):
        return not self.tripped

    def snapshot(self):
        return asdict(self)
