"""Causal BTCUSDT signal/backtest prototype.

Every decision at candle i uses data through candle i only and executes at
the next candle open. This is a research backtest, not financial advice.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from behavior_bank import load_ohlcv


@dataclass
class Trade:
    side: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: float
    risk_dollars: float
    gross_pnl: float
    fees: float
    slippage: float
    net_pnl: float
    return_on_equity: float
    exit_reason: str
    entry_score: int
    entry_evidence: list[str]
    exit_evidence: list[str]


def rolling_slope(values: np.ndarray) -> float:
    if len(values) < 2 or np.any(~np.isfinite(values)):
        return np.nan
    x = np.arange(len(values), dtype=float)
    return float(np.polyfit(x, np.log(np.maximum(values, 1e-12)), 1)[0])


def prepare(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["return"] = data["close"].pct_change()
    data["range"] = (data["high"] - data["low"]) / data["close"].replace(0, np.nan)
    data["body"] = (data["close"] - data["open"]).abs() / data["close"].replace(0, np.nan)
    data["close_location"] = (data["close"] - data["low"]) / (data["high"] - data["low"]).replace(0, np.nan)
    previous_close = data["close"].shift(1)
    true_range = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - previous_close).abs(),
            (data["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    data["atr"] = true_range.rolling(14, min_periods=14).mean()
    data["prior_high_20"] = data["high"].rolling(20, min_periods=20).max().shift(1)
    data["prior_low_20"] = data["low"].rolling(20, min_periods=20).min().shift(1)
    data["prior_low_10"] = data["low"].rolling(10, min_periods=10).min().shift(1)
    data["prior_high_10"] = data["high"].rolling(10, min_periods=10).max().shift(1)
    data["volume_median_48"] = data["volume"].rolling(48, min_periods=48).median().shift(1)
    data["slope_24"] = data["close"].rolling(24, min_periods=24).apply(rolling_slope, raw=True)
    data["pressure_signed"] = (
        data.get("pressure", pd.Series(1.0, index=data.index))
        * np.sign(data["close"] - data["open"])
    )
    if "delta" not in data:
        data["delta"] = np.nan
    data["delta_3"] = data["delta"].rolling(3, min_periods=3).sum()
    data["delta_3_median"] = data["delta_3"].abs().rolling(96, min_periods=48).median().shift(1)
    data["body_ratio"] = data["body"] / data["range"].replace(0, np.nan)
    data["range_atr_ratio"] = (data["high"] - data["low"]) / data["atr"].replace(0, np.nan)
    data["volume_ratio"] = data["volume"] / data["volume_median_48"].replace(0, np.nan)
    return data


def evidence(row: pd.Series, side: str) -> tuple[int, list[str]]:
    bullish = side == "long"
    score = 0
    labels: list[str] = []
    if (row["close"] > row["prior_high_20"]) if bullish else (row["close"] < row["prior_low_20"]):
        score += 1
        labels.append("20-bar structural breakout")
    if (row["slope_24"] > 0) if bullish else (row["slope_24"] < 0):
        score += 1
        labels.append("positive regression slope" if bullish else "negative regression slope")
    if row["range_atr_ratio"] >= 1.15:
        score += 1
        labels.append("range expansion")
    if row["volume_ratio"] >= 1.10:
        score += 1
        labels.append("volume participation")
    if row["body_ratio"] >= 0.55 and ((row["close_location"] >= 0.65) if bullish else (row["close_location"] <= 0.35)):
        score += 1
        labels.append("directional close/body")
    if pd.notna(row["delta_3"]) and pd.notna(row["delta_3_median"]):
        if (row["delta_3"] > row["delta_3_median"]) if bullish else (row["delta_3"] < -row["delta_3_median"]):
            score += 1
            labels.append("delta persistence")
    if pd.notna(row["pressure_signed"]):
        if (row["pressure_signed"] > 0) if bullish else (row["pressure_signed"] < 0):
            score += 1
            labels.append("directional pressure")
    return score, labels


def run_backtest(
    frame: pd.DataFrame,
    initial_equity: float = 1000.0,
    leverage: float = 20.0,
    risk_fraction: float = 0.03,
    fee_rate: float = 0.0004,
    slippage_rate: float = 0.0002,
) -> dict[str, Any]:
    data = prepare(frame).dropna(subset=["atr", "prior_high_20", "prior_low_20", "volume_median_48", "slope_24"])
    equity = initial_equity
    position: dict[str, Any] | None = None
    trades: list[Trade] = []
    equity_curve = []

    def execution_price(price: float, side: str, entering: bool) -> float:
        adverse = slippage_rate if side == "long" else -slippage_rate
        if not entering:
            adverse = -adverse
        return price * (1 + adverse)

    for index in range(len(data) - 1):
        row = data.iloc[index]
        next_row = data.iloc[index + 1]
        timestamp = data.index[index]
        if position is not None:
            side = position["side"]
            stop = position["stop"]
            stop_hit = next_row["low"] <= stop if side == "long" else next_row["high"] >= stop
            score, exit_labels = evidence(row, "short" if side == "long" else "long")
            structural_exit = (
                row["close"] < row["prior_low_10"] if side == "long" else row["close"] > row["prior_high_10"]
            )
            adverse_exit = score >= 4 and structural_exit
            if stop_hit or adverse_exit:
                reason = "risk_stop" if stop_hit else "opposite_evidence"
                raw_exit = stop if stop_hit else next_row["open"]
                exit_price = execution_price(float(raw_exit), side, entering=False)
                direction = 1 if side == "long" else -1
                gross = (exit_price - position["entry_price"]) * position["quantity"] * direction
                fees = (position["entry_price"] * position["quantity"] + exit_price * position["quantity"]) * fee_rate
                slippage = abs(exit_price - raw_exit) * position["quantity"]
                net = gross - fees
                trade = Trade(
                    side=side,
                    entry_time=position["entry_time"],
                    exit_time=next_row.name.isoformat(),
                    entry_price=position["entry_price"],
                    exit_price=exit_price,
                    quantity=position["quantity"],
                    risk_dollars=position["risk_dollars"],
                    gross_pnl=gross,
                    fees=fees,
                    slippage=slippage,
                    net_pnl=net,
                    return_on_equity=net / position["equity_before"],
                    exit_reason=reason,
                    entry_score=position["entry_score"],
                    entry_evidence=position["entry_evidence"],
                    exit_evidence=(["stop reached"] if stop_hit else exit_labels),
                )
                equity += net
                trades.append(trade)
                position = None
        if position is None and equity > 0:
            long_score, long_labels = evidence(row, "long")
            short_score, short_labels = evidence(row, "short")
            side = "long" if long_score >= 5 and long_score > short_score else "short" if short_score >= 5 else None
            if side:
                entry_raw = float(next_row["open"])
                entry_price = execution_price(entry_raw, side, entering=True)
                stop_distance = max(float(row["atr"]) * 2.0, entry_price * 0.002)
                risk_dollars = equity * risk_fraction
                quantity_by_risk = risk_dollars / stop_distance
                quantity_by_leverage = equity * leverage / entry_price
                quantity = min(quantity_by_risk, quantity_by_leverage)
                if quantity > 0:
                    position = {
                        "side": side,
                        "entry_time": next_row.name.isoformat(),
                        "entry_price": entry_price,
                        "quantity": quantity,
                        "risk_dollars": risk_dollars,
                        "equity_before": equity,
                        "entry_score": long_score if side == "long" else short_score,
                        "entry_evidence": long_labels if side == "long" else short_labels,
                        "stop": entry_price - stop_distance if side == "long" else entry_price + stop_distance,
                    }
        equity_curve.append({"timestamp": timestamp.isoformat(), "equity": equity})

    if position is not None:
        final = data.iloc[-1]
        side = position["side"]
        exit_price = execution_price(float(final["close"]), side, entering=False)
        direction = 1 if side == "long" else -1
        gross = (exit_price - position["entry_price"]) * position["quantity"] * direction
        fees = (position["entry_price"] * position["quantity"] + exit_price * position["quantity"]) * fee_rate
        net = gross - fees
        trades.append(
            Trade(
                side=side,
                entry_time=position["entry_time"],
                exit_time=final.name.isoformat(),
                entry_price=position["entry_price"],
                exit_price=exit_price,
                quantity=position["quantity"],
                risk_dollars=position["risk_dollars"],
                gross_pnl=gross,
                fees=fees,
                slippage=0.0,
                net_pnl=net,
                return_on_equity=net / position["equity_before"],
                exit_reason="end_of_data",
                entry_score=position["entry_score"],
                entry_evidence=position["entry_evidence"],
                exit_evidence=["end of historical data"],
            )
        )
        equity += net

    net_values = [trade.net_pnl for trade in trades]
    wins = [value for value in net_values if value > 0]
    losses = [value for value in net_values if value <= 0]
    return {
        "parameters": {
            "initial_equity": initial_equity,
            "leverage": leverage,
            "risk_fraction": risk_fraction,
            "fee_rate": fee_rate,
            "slippage_rate": slippage_rate,
            "execution": "next candle open; stops checked against next candle range",
        },
        "rules": {
            "entry": "score >= 5 from structural breakout, regression slope, range expansion, volume, directional body/close, delta persistence, pressure",
            "exit": "2 ATR risk stop or opposite structural break with >=4 opposing evidence points",
            "lookahead": "none; all features are shifted/rolling-causal and execution is next-open",
        },
        "summary": {
            "candles_used": len(data),
            "trades": len(trades),
            "final_equity": equity,
            "net_pnl": equity - initial_equity,
            "return_pct": (equity / initial_equity - 1) * 100,
            "win_rate_pct": len(wins) / len(net_values) * 100 if net_values else 0,
            "profit_factor": sum(wins) / abs(sum(losses)) if losses and sum(losses) else None,
            "average_trade": float(np.mean(net_values)) if net_values else 0,
            "max_win": max(net_values, default=0),
            "max_loss": min(net_values, default=0),
        },
        "trades": [asdict(trade) for trade in trades],
        "equity_curve": equity_curve,
    }


def validate_causality(frame: pd.DataFrame, cutoff: int = 1000) -> dict[str, Any]:
    original = prepare(frame)
    altered = frame.copy()
    if len(altered) <= cutoff + 10:
        return {"status": "insufficient_rows"}
    altered.iloc[cutoff:, altered.columns.get_indexer(["open", "high", "low", "close", "volume"])] *= 1.37
    altered_prepared = prepare(altered)
    fields = ["atr", "prior_high_20", "prior_low_20", "slope_24", "volume_ratio", "delta_3"]
    mismatches = []
    for field in fields:
        left = original[field].iloc[:cutoff].to_numpy()
        right = altered_prepared[field].iloc[:cutoff].to_numpy()
        if not np.allclose(left, right, equal_nan=True):
            mismatches.append(field)
    return {"status": "passed" if not mismatches else "failed", "cutoff": cutoff, "future_fields_altered": 5, "mismatched_pre_cutoff_features": mismatches}


def write_report(result: dict[str, Any], path: str | Path) -> None:
    summary = result["summary"]
    parameters = result["parameters"]
    lines = [
        "# BTCUSDT Causal Signal Backtest",
        "",
        "This is an initial research backtest, not a production strategy or financial advice.",
        "All features are causal; entries execute at the next candle open.",
        "",
        "## Parameters",
        f"- Initial equity: `${parameters['initial_equity']:.2f}`",
        f"- Leverage cap: `{parameters['leverage']}x`",
        f"- Risk budget: `{parameters['risk_fraction'] * 100:.1f}%` of current equity per trade",
        f"- Fee: `{parameters['fee_rate'] * 100:.3f}%` per side",
        f"- Slippage: `{parameters['slippage_rate'] * 100:.3f}%` per side",
        "",
        "## Results",
        f"- Candles: **{summary['candles_used']:,}**",
        f"- Trades: **{summary['trades']:,}**",
        f"- Final equity: **${summary['final_equity']:.2f}**",
        f"- Net PnL: **${summary['net_pnl']:.2f}**",
        f"- Return: **{summary['return_pct']:.2f}%**",
        f"- Win rate: **{summary['win_rate_pct']:.2f}%**",
        f"- Profit factor: **{summary['profit_factor']}**",
        "",
        "## Rules",
        f"- Entry: {result['rules']['entry']}",
        f"- Exit: {result['rules']['exit']}",
        f"- Look-ahead: {result['rules']['lookahead']}",
        "",
        "## Interpretation",
        "This first fixed-rule version is not acceptable for deployment because the observed result is negative. The result is retained as an honest baseline; thresholds must not be tuned on the same full history and then called validation.",
        "",
        "## Safety checks",
        f"- Causality perturbation: **{result['validation']['status']}**",
        "- No live orders, API keys, or exchange actions were used.",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a causal BTCUSDT signal backtest.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="BTCUSDT_signal_backtest.json")
    parser.add_argument("--report-output", default="BTCUSDT_signal_backtest_report.md")
    args = parser.parse_args()
    frame = load_ohlcv(args.input)
    result = run_backtest(frame)
    result["validation"] = validate_causality(frame)
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(result, args.report_output)
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
