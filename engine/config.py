"""Validated, fail-closed configuration. This release cannot place real orders."""

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path


class LiveTradingDisabled(RuntimeError):
    pass


@dataclass(frozen=True)
class SimulationConfig:
    starting_equity: float = 10000.0
    risk_per_trade: float = 0.01
    max_daily_loss: float = 0.03
    max_leverage: float = 2.0
    fee_bps: float = 6.0
    slippage_bps: float = 2.0
    quantity_step: float = 0.000001
    hold_bars: int = 10
    enable_whale: bool = False

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name not in {"enable_whale", "hold_bars"} and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.starting_equity <= 0 or not 0 < self.risk_per_trade <= 0.05:
            raise ValueError("Positive capital and risk in (0, 0.05] required")
        if not 0 < self.max_daily_loss <= 0.25 or not 1 <= self.max_leverage <= 3:
            raise ValueError("Invalid daily loss or leverage limit")
        if not 0 <= self.fee_bps <= 100 or not 0 <= self.slippage_bps <= 100:
            raise ValueError("Costs must be between 0 and 100 basis points")
        if not 0 < self.quantity_step <= 1 or not 1 <= self.hold_bars <= 100:
            raise ValueError("Invalid quantity step or holding period")
        if not isinstance(self.hold_bars, int) or not isinstance(self.enable_whale, bool):
            raise ValueError("Invalid configuration types")

    def fingerprint(self):
        payload = {"version": 2, **asdict(self)}
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    simulation: SimulationConfig
    poll_seconds: int = 60
    market: str = "BTCUSDT"

    @classmethod
    def from_env(cls):
        if os.getenv("TRADING_MODE", "paper").lower() != "paper":
            raise LiveTradingDisabled("Only paper mode is supported; live execution is disabled")
        if os.getenv("TRADING_MARKET", "BTCUSDT") != "BTCUSDT":
            raise ValueError("This release has only been specified for BTCUSDT")
        poll = int(os.getenv("POLL_SECONDS", "60"))
        if not 5 <= poll <= 300:
            raise ValueError("POLL_SECONDS must be between 5 and 300")
        if os.getenv("ENABLE_WHALE", "false").lower() not in {"false", "0"}:
            raise ValueError("CoinEx public klines have no taker flow; Whale is disabled in paper polling")
        config = SimulationConfig(
            starting_equity=float(os.getenv("PAPER_START_EQUITY", "10000")),
            risk_per_trade=float(os.getenv("RISK_PER_TRADE_PCT", "0.01")),
            max_daily_loss=float(os.getenv("MAX_DAILY_LOSS_PCT", "0.03")),
            max_leverage=float(os.getenv("MAX_LEVERAGE", "2")),
            fee_bps=float(os.getenv("FEE_BPS", "6")),
            slippage_bps=float(os.getenv("SLIPPAGE_BPS", "2")),
        )
        return cls(Path(os.getenv("DATA_DIR", "data")).resolve(), config, poll)
