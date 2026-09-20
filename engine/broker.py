"""Linear USDT paper fills; no private exchange access or synthetic PnL clipping."""

from dataclasses import asdict
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_EVEN

from .config import LiveTradingDisabled, SimulationConfig
from .models import Candle, HOUR_MS, Position
from .strategy import RR, STAIRCASE_TIERS


def dec(value):
    return Decimal(str(value))


def money(value):
    return float(dec(value).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_EVEN))


class LiveBroker:
    def __init__(self, *args, **kwargs):
        raise LiveTradingDisabled(
            "Live broker disabled until exchange reconciliation and protection are certified"
        )


class PaperBroker:
    is_live = False

    def __init__(self, config: SimulationConfig, state=None):
        self.config = config
        state = state or {}
        self.cash = state.get("cash", config.starting_equity)
        self.position = Position(**state["position"]) if state.get("position") else None
        self.closed_trades = state.get("closed_trades", 0)
        self.wins = state.get("wins", 0)
        self.fees_paid = state.get("fees_paid", 0.0)
        self.funding_paid = state.get("funding_paid", 0.0)

    def snapshot(self):
        return {
            "cash": self.cash,
            "position": asdict(self.position) if self.position else None,
            "closed_trades": self.closed_trades,
            "wins": self.wins,
            "fees_paid": self.fees_paid,
            "funding_paid": self.funding_paid,
        }

    def equity(self, mark):
        p = self.position
        return (
            money(dec(self.cash) + dec(p.quantity) * p.sign * (dec(mark) - dec(p.entry_price)))
            if p
            else self.cash
        )

    def fill_price(self, reference, sign, opening):
        multiplier = 1 + sign * self.config.slippage_bps / 10000 * (1 if opening else -1)
        return float(dec(reference) * dec(multiplier))

    def open_position(self, signal, candle: Candle):
        if self.position is not None:
            raise ValueError("Only one paper position may be open")
        if signal["signal_time"] + HOUR_MS != candle.time:
            raise ValueError("Entry signal must come from the immediately preceding closed candle")
        if signal["direction"] not in {"long", "short"}:
            raise ValueError("Invalid direction")
        sign = 1 if signal["direction"] == "long" else -1
        stop = float(signal["stop"])
        fill = self.fill_price(candle.open, sign, True)
        if (candle.open - stop) * sign <= 0 or self.cash <= 0:
            return {"type": "entry_rejected", "time": candle.time, "reason": "gap_through_stop_or_no_capital"}
        fee_rate = dec(self.config.fee_bps) / 10000
        stop_fill = self.fill_price(stop, sign, False)
        unit_risk = abs(dec(fill) - dec(stop_fill)) + (dec(fill) + dec(stop_fill)) * fee_rate
        risk_budget = dec(self.cash) * dec(self.config.risk_per_trade)
        max_notional = dec(self.cash) / (1 / dec(self.config.max_leverage) + fee_rate)
        raw_qty = min(risk_budget / unit_risk, max_notional / dec(fill))
        step = dec(self.config.quantity_step)
        qty = (raw_qty / step).to_integral_value(rounding=ROUND_FLOOR) * step
        target = fill + sign * RR * abs(fill - stop)
        if qty <= 0 or target <= 0:
            return {"type": "entry_rejected", "time": candle.time, "reason": "invalid_size_or_target"}
        fee = money(qty * dec(fill) * fee_rate)
        position = Position(
            trade_id=f"paper-{signal['signal_time']}-{signal['direction']}",
            direction=signal["direction"],
            entry_time=candle.time,
            entry_price=fill,
            quantity=float(qty),
            initial_stop=stop,
            current_stop=stop,
            target=target,
            entry_fee=fee,
            entry_equity=self.cash,
            risk_budget=float(risk_budget),
            signal_source=signal["signal_source"],
        )
        self.cash = money(dec(self.cash) - dec(fee))
        self.fees_paid = money(dec(self.fees_paid) + dec(fee))
        self.position = position
        return {"type": "enter", "mode": "paper", "time": candle.time, **asdict(position)}

    def close_position(self, reference, timestamp, reason):
        if self.position is None:
            raise ValueError("No position to close")
        p = self.position
        # Compute and validate the fill before mutating account or position state.
        fill = self.fill_price(reference, p.sign, False)
        fee = money(dec(p.quantity) * dec(fill) * dec(self.config.fee_bps) / 10000)
        gross = money(dec(p.quantity) * p.sign * (dec(fill) - dec(p.entry_price)))
        net = money(dec(gross) - dec(p.entry_fee) - dec(fee) + dec(p.funding))
        cash_after = money(dec(self.cash) + dec(gross) - dec(fee))
        event = {
            "type": "exit",
            "mode": "paper",
            "time": timestamp,
            **asdict(p),
            "exit_price": fill,
            "exit_reason": reason,
            "gross_pnl": gross,
            "net_pnl": net,
            "exit_fee": fee,
            "equity_after": cash_after,
            "return_on_entry_equity": net / p.entry_equity,
            "r_multiple_net": net / p.risk_budget,
        }
        self.cash = cash_after
        self.fees_paid = money(dec(self.fees_paid) + dec(fee))
        self.closed_trades += 1
        self.wins += net > 0
        self.position = None
        return event

    def apply_funding(self, rate, mark, timestamp, adverse_drag=0.0):
        if self.position is None:
            return None
        p = self.position
        cashflow = money(-dec(p.quantity) * dec(mark) * (p.sign * dec(rate) + dec(adverse_drag)))
        self.cash = money(dec(self.cash) + dec(cashflow))
        p.funding = money(dec(p.funding) + dec(cashflow))
        self.funding_paid = money(dec(self.funding_paid) - dec(cashflow))
        return {
            "type": "funding",
            "mode": "paper",
            "time": timestamp,
            "trade_id": p.trade_id,
            "cashflow": cashflow,
        }

    def manage_bar(self, candle: Candle):
        """Known opening gaps first; stop-first for ambiguous high/low; new trail next bar."""
        p = self.position
        if p is None:
            return None
        stop, target = p.current_stop, p.target
        if p.sign == 1:
            hit = (
                (candle.open <= stop, candle.open, "stop-gap"),
                (candle.open >= target, candle.open, "target-gap"),
                (candle.low <= stop, stop, "stop"),
                (candle.high >= target, target, "target"),
            )
        else:
            hit = (
                (candle.open >= stop, candle.open, "stop-gap"),
                (candle.open <= target, candle.open, "target-gap"),
                (candle.high >= stop, stop, "stop"),
                (candle.low <= target, target, "target"),
            )
        p.bars_held += 1
        for touched, reference, reason in hit:
            if touched:
                return self.close_position(reference, candle.time, reason)
        favorable = (
            (candle.high - p.entry_price) if p.sign == 1 else (p.entry_price - candle.low)
        ) / p.entry_price
        p.peak_favorable = max(p.peak_favorable, favorable)
        locked = max(
            (lock * p.peak_favorable for trigger, lock in STAIRCASE_TIERS if p.peak_favorable >= trigger),
            default=0.0,
        )
        if locked:
            new_stop = p.entry_price * (1 + p.sign * locked)
            p.current_stop = max(stop, new_stop) if p.sign == 1 else min(stop, new_stop)
        return None
