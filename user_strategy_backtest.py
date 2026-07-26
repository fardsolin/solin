"""Backtest of the user's MarketBehaviorStrategy script.

Two evaluations are reported:
1. raw: identical execution to the original script (entry/exit on the same
   candle close that produced the signal). This contains same-bar lookahead
   and is kept only for comparison with the user's own output.
2. causal: signals from candle i execute at candle i+1 open with fees,
   slippage, a 2 ATR protective stop, $1000 initial equity, a 20x leverage
   cap, and 3% risk per trade.

Research backtest only; not financial advice.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from behavior_bank import load_ohlcv


class MarketBehaviorStrategy:
    """User-provided strategy, logic preserved."""

    def __init__(self, trend_window=20, correction_depth=0.382, delta_reverse_threshold=0.35):
        self.trend_window = trend_window
        self.correction_depth = correction_depth
        self.delta_reverse_threshold = delta_reverse_threshold

    def calculate_features(self, df):
        df = df.copy()
        df["return"] = df.close.pct_change()
        df["body"] = abs(df.close - df.open)
        df["range"] = df.high - df.low
        df["body_ratio"] = df.body / (df.range.replace(0, np.nan))
        df["pressure"] = (df.close - df.low) / (df.high - df.low).replace(0, np.nan)
        if "delta" in df.columns:
            df["delta_norm"] = df.delta / df.volume.replace(0, np.nan)
        else:
            df["delta_norm"] = 0
        df["volume_change"] = df.volume / df.volume.rolling(20).mean()
        df["swing_high"] = df.high == df.high.rolling(self.trend_window).max()
        df["swing_low"] = df.low == df.low.rolling(self.trend_window).min()
        return df

    def detect_trend(self, row_history):
        highs = row_history.high
        lows = row_history.low
        higher_high = highs.iloc[-1] > highs.iloc[-10]
        higher_low = lows.iloc[-1] > lows.iloc[-10]
        lower_high = highs.iloc[-1] < highs.iloc[-10]
        lower_low = lows.iloc[-1] < lows.iloc[-10]
        if higher_high and higher_low:
            return "UP"
        if lower_high and lower_low:
            return "DOWN"
        return "RANGE"

    def detect_correction(self, history, trend):
        if trend == "UP":
            peak = history.high.max()
            retrace = (peak - history.close.iloc[-1]) / peak
            if retrace > self.correction_depth / 10:
                return True
        if trend == "DOWN":
            bottom = history.low.min()
            retrace = (history.close.iloc[-1] - bottom) / bottom
            if retrace > self.correction_depth / 10:
                return True
        return False

    def find_entry(self, df):
        signals = []
        for i in range(self.trend_window, len(df)):
            hist = df.iloc[i - self.trend_window : i]
            trend = self.detect_trend(hist)
            current = df.iloc[i]
            signal = None
            if trend == "UP":
                if self.detect_correction(hist, trend):
                    delta_change = current.delta_norm - hist.delta_norm.mean()
                    pressure_recovery = current.pressure > hist.pressure.mean()
                    if delta_change > 0 and pressure_recovery and current.body_ratio > 0.6:
                        signal = "LONG"
            if trend == "DOWN":
                if self.detect_correction(hist, trend):
                    delta_change = current.delta_norm - hist.delta_norm.mean()
                    pressure_drop = current.pressure < hist.pressure.mean()
                    if delta_change < 0 and pressure_drop and current.body_ratio > 0.6:
                        signal = "SHORT"
            signals.append(signal)
        return signals


def raw_backtest(strategy: MarketBehaviorStrategy, df: pd.DataFrame) -> pd.DataFrame:
    """Original same-close execution, identical to the user's script."""
    df = strategy.calculate_features(df)
    df["signal"] = None
    signals = strategy.find_entry(df)
    df.iloc[strategy.trend_window :, df.columns.get_loc("signal")] = signals
    position = None
    entry = 0.0
    trades = []
    for _, row in df.iterrows():
        if position is None:
            if row.signal == "LONG":
                position, entry = "LONG", row.close
            elif row.signal == "SHORT":
                position, entry = "SHORT", row.close
        else:
            exit_trade = (row.delta_norm < 0) if position == "LONG" else (row.delta_norm > 0)
            if exit_trade:
                pnl = (row.close - entry) / entry
                if position == "SHORT":
                    pnl = -pnl
                trades.append({"entry": entry, "exit": row.close, "direction": position, "return": pnl})
                position = None
    return pd.DataFrame(trades)


def causal_backtest(
    strategy: MarketBehaviorStrategy,
    df: pd.DataFrame,
    initial_equity: float = 1000.0,
    leverage: float = 20.0,
    risk_fraction: float = 0.03,
    fee_rate: float = 0.0004,
    slippage_rate: float = 0.0002,
) -> dict[str, Any]:
    """Same signal logic, but decisions at i execute at i+1 open."""
    data = strategy.calculate_features(df)
    data["signal"] = None
    signals = strategy.find_entry(data)
    data.iloc[strategy.trend_window :, data.columns.get_loc("signal")] = signals
    previous_close = data["close"].shift(1)
    true_range = pd.concat(
        [data["high"] - data["low"], (data["high"] - previous_close).abs(), (data["low"] - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    data["atr"] = true_range.rolling(14, min_periods=14).mean()

    equity = initial_equity
    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []

    def execution_price(price: float, side: str, entering: bool) -> float:
        adverse = slippage_rate if side == "LONG" else -slippage_rate
        if not entering:
            adverse = -adverse
        return price * (1 + adverse)

    for index in range(len(data) - 1):
        row = data.iloc[index]
        next_row = data.iloc[index + 1]
        if position is not None:
            side = position["side"]
            stop = position["stop"]
            stop_hit = next_row["low"] <= stop if side == "LONG" else next_row["high"] >= stop
            delta_exit = (row["delta_norm"] < 0) if side == "LONG" else (row["delta_norm"] > 0)
            if stop_hit or delta_exit:
                raw_exit = stop if stop_hit else float(next_row["open"])
                exit_price = execution_price(float(raw_exit), side, entering=False)
                direction = 1 if side == "LONG" else -1
                gross = (exit_price - position["entry_price"]) * position["quantity"] * direction
                fees = (position["entry_price"] + exit_price) * position["quantity"] * fee_rate
                net = gross - fees
                equity += net
                trades.append(
                    {
                        "side": side,
                        "entry_time": position["entry_time"],
                        "exit_time": next_row.name.isoformat(),
                        "entry_price": position["entry_price"],
                        "exit_price": exit_price,
                        "net_pnl": net,
                        "exit_reason": "risk_stop" if stop_hit else "delta_flip",
                    }
                )
                position = None
        if position is None and equity > 0 and pd.notna(row["atr"]):
            side = row["signal"]
            if side in ("LONG", "SHORT"):
                entry_raw = float(next_row["open"])
                entry_price = execution_price(entry_raw, side, entering=True)
                stop_distance = max(float(row["atr"]) * 2.0, entry_price * 0.002)
                risk_dollars = equity * risk_fraction
                quantity = min(risk_dollars / stop_distance, equity * leverage / entry_price)
                if quantity > 0:
                    position = {
                        "side": side,
                        "entry_time": next_row.name.isoformat(),
                        "entry_price": entry_price,
                        "quantity": quantity,
                        "stop": entry_price - stop_distance if side == "LONG" else entry_price + stop_distance,
                    }

    values = [trade["net_pnl"] for trade in trades]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    return {
        "trades": len(trades),
        "final_equity": equity,
        "net_pnl": equity - initial_equity,
        "return_pct": (equity / initial_equity - 1) * 100,
        "win_rate_pct": len(wins) / len(values) * 100 if values else 0,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses and sum(losses) else None,
        "average_trade": float(np.mean(values)) if values else 0,
        "trade_log": trades,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the user's MarketBehaviorStrategy under agreed conditions.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="BTCUSDT_user_strategy_backtest.json")
    parser.add_argument("--report-output", default="BTCUSDT_user_strategy_backtest_report.md")
    args = parser.parse_args()

    frame = load_ohlcv(args.input).reset_index().set_index("timestamp")
    strategy = MarketBehaviorStrategy()

    raw = raw_backtest(strategy, frame)
    raw_summary = {
        "trades": int(len(raw)),
        "win_rate_pct": float((raw["return"] > 0).mean() * 100) if len(raw) else 0.0,
        "sum_of_returns_pct": float(raw["return"].sum() * 100) if len(raw) else 0.0,
        "compounded_return_pct": float(((1 + raw["return"]).prod() - 1) * 100) if len(raw) else 0.0,
        "execution": "same-candle close; contains same-bar lookahead; kept only for comparison",
    }

    causal = causal_backtest(strategy, frame)
    causal_summary = {key: value for key, value in causal.items() if key != "trade_log"}

    result = {
        "parameters": {
            "initial_equity": 1000.0,
            "leverage": 20.0,
            "risk_fraction": 0.03,
            "fee_rate": 0.0004,
            "slippage_rate": 0.0002,
        },
        "raw_script_execution": raw_summary,
        "causal_execution": causal_summary,
        "causal_trades": causal["trade_log"],
    }
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# BTCUSDT User MarketBehaviorStrategy Backtest",
        "",
        "The strategy logic is the user's script, unchanged. Two executions are reported.",
        "",
        "## 1. Raw script execution (same-candle close)",
        "This matches the original script but entries/exits use the same candle that produced the signal, which is same-bar lookahead.",
        f"- Trades: **{raw_summary['trades']:,}**",
        f"- Win rate: **{raw_summary['win_rate_pct']:.2f}%**",
        f"- Sum of per-trade returns: **{raw_summary['sum_of_returns_pct']:.2f}%**",
        f"- Compounded (no leverage, no costs): **{raw_summary['compounded_return_pct']:.2f}%**",
        "",
        "## 2. Causal execution ($1000, 20x cap, 3% risk, fees, slippage, 2 ATR stop)",
        f"- Trades: **{causal_summary['trades']:,}**",
        f"- Final equity: **${causal_summary['final_equity']:.2f}**",
        f"- Return: **{causal_summary['return_pct']:.2f}%**",
        f"- Win rate: **{causal_summary['win_rate_pct']:.2f}%**",
        f"- Profit factor: **{causal_summary['profit_factor']}**",
        "",
        "## Notes",
        "- delta comes from TakerBuyBase-derived flow, not order-book data.",
        "- Data-quality finding: in this parquet `TakerBuyBase/Volume` has a median of ~0.005 instead of ~0.5, so the column is not real taker-buy volume and the derived delta is negative on ~99.99% of candles. The delta-flip exit therefore closes LONGs almost immediately and almost never closes SHORTs; the raw run's single trade held a short from ~8,115 to ~49,684 (-512%). A corrected taker-buy column is required before any delta-based rule is meaningful.",
        "- No live orders, exchange APIs, or keys are used.",
    ]
    Path(args.report_output).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"raw": raw_summary, "causal": causal_summary}, indent=2))


if __name__ == "__main__":
    main()
