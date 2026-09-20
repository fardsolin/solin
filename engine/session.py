"""Shared deterministic execution path for paper polling and historical replay."""

from .broker import PaperBroker
from .config import SimulationConfig
from .models import Candle, HOUR_MS
from .risk_manager import RiskManager
from .strategy import StrategyEngine


class TradingSession:
    def __init__(self, config: SimulationConfig, state=None):
        if state is not None and (not isinstance(state, dict) or not state):
            raise ValueError("Empty or malformed checkpoint; refusing to reset")
        state = state or {}
        if state and (
            state.get("schema_version") != 2
            or state.get("mode") != "paper"
            or state.get("config_fingerprint") != config.fingerprint()
        ):
            raise ValueError("State schema/mode/config mismatch; refusing to reset or silently migrate")
        self.config = config
        self.strategy = StrategyEngine(config.enable_whale, state.get("strategy"))
        self.broker = PaperBroker(config, state.get("broker"))
        self.risk = RiskManager(**state["risk"]) if state else RiskManager(config.max_daily_loss)
        self.pending = state.get("pending")
        self.last_close = state.get("last_close")
        self.revision = 0

    @property
    def last_time(self):
        return self.strategy.last_time

    def snapshot(self):
        return {
            "schema_version": 2,
            "mode": "paper",
            "config_fingerprint": self.config.fingerprint(),
            "strategy": self.strategy.snapshot(),
            "broker": self.broker.snapshot(),
            "risk": self.risk.snapshot(),
            "pending": self.pending,
            "last_close": self.last_close,
        }

    def warmup(self, candles):
        if self.last_time is not None:
            raise ValueError("Warmup cannot replace existing state")
        for candle in candles:
            self.pending = self.strategy.update(candle)
            self.last_close = candle.close

    def process(self, candle: Candle, store=None, funding_rate=0.0, funding_drag=0.0):
        if self.last_time is not None and candle.time == self.last_time:
            if candle.to_dict() != self.strategy.prior[-1]:
                raise ValueError("Previously processed candle changed")
            return []
        if self.last_time is not None and candle.time != self.last_time + HOUR_MS:
            raise ValueError("Missing or out-of-order candle; checkpoint not advanced")
        if self.config.enable_whale and candle.taker_buy is None:
            raise ValueError("Missing genuine order flow")
        if store is None:
            return self._process(candle, funding_rate, funding_drag)
        # Durable commit and memory swap are all-or-nothing. Failed writes can be retried.
        draft = TradingSession(self.config, self.snapshot())
        events = draft._process(candle, funding_rate, funding_drag)
        revision = store.save(draft.snapshot(), events, expected_revision=self.revision)
        self.__dict__.update(draft.__dict__)
        self.revision = revision
        return events

    def _process(self, c, funding_rate, funding_drag):
        events = []
        account = self.broker
        prior_equity = account.equity(self.last_close if self.last_close is not None else c.open)
        self.risk.roll_day(c.time, prior_equity)
        self.risk.observe(account.equity(c.open))
        # Funding applies only to positions carried into this instant, before new entries.
        if account.position and (funding_rate or funding_drag):
            events.append(account.apply_funding(funding_rate, c.open, c.time, funding_drag))
        if account.position and account.position.bars_held >= self.config.hold_bars:
            events.append(account.close_position(c.open, c.time, "time-exit"))
        self.risk.observe(account.equity(c.open))
        if self.pending and account.position is None and self.risk.can_enter():
            events.append(account.open_position(self.pending, c))
        self.pending = None
        # The daily entry latch never prevents management of an existing position.
        result = account.manage_bar(c)
        if result:
            events.append(result)
        self.risk.observe(account.equity(c.close))
        signal = self.strategy.update(c)
        if account.position is None and self.risk.can_enter():
            self.pending = signal
        self.last_close = c.close
        return [e for e in events if e is not None]

    def status(self):
        equity = self.broker.equity(self.last_close) if self.last_close is not None else self.broker.cash
        return {
            "mode": "paper",
            "live_enabled": False,
            "last_candle_open_ms": self.last_time,
            "equity": equity,
            "cash": self.broker.cash,
            "position": self.broker.snapshot()["position"],
            "paper_trades_completed": self.broker.closed_trades,
            "win_rate_pct": (
                100 * self.broker.wins / self.broker.closed_trades if self.broker.closed_trades else None
            ),
            "daily_pnl_pct": self.risk.daily_pnl_pct * 100,
            "circuit_breaker_tripped": self.risk.tripped,
            "fees_paid": self.broker.fees_paid,
            "funding_paid": self.broker.funding_paid,
            "whale_enabled": self.config.enable_whale,
        }
