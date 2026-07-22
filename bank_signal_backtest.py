"""Bank-driven causal BTCUSDT signal backtest.

Entry and exit rules are derived from the extracted behavior bank:
- continuation corrections are shallower and lose volume, while reversal
  corrections are deeper, longer, and gain volume;
- real range breakouts show stronger participation than false breakouts.

Thresholds are calibrated on the training window only and applied unchanged
to the untouched test window. Decisions at candle i use data through candle i
and execute at candle i+1 open. Research backtest only; not financial advice.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from behavior_bank import load_ohlcv
from causal_signal_backtest import Trade, prepare


def calibrate(train: pd.DataFrame) -> dict[str, float]:
    """Derive causal thresholds from the training window.

    These mirror the behavior-bank findings: continuation corrections have a
    mean retracement near 2% with flat/declining volume, while reversal
    corrections average ~3.5% retracement with rising volume.
    """
    data = prepare(train)
    pullback = (data["close"] / data["close"].rolling(48, min_periods=48).max() - 1).abs()
    volume_trend = data["volume"].rolling(6, min_periods=6).mean() / data["volume_median_48"]
    return {
        "continuation_depth": float(np.nanquantile(pullback, 0.55)),
        "reversal_depth": float(np.nanquantile(pullback, 0.85)),
        "reversal_volume_ratio": float(np.nanquantile(volume_trend, 0.75)),
        "breakout_volume_ratio": float(np.nanquantile(data["volume_ratio"], 0.80)),
        "breakout_range_ratio": float(np.nanquantile(data["range_atr_ratio"], 0.80)),
    }


def add_bank_features(frame: pd.DataFrame) -> pd.DataFrame:
    data = prepare(frame)
    data["rolling_max_48"] = data["close"].rolling(48, min_periods=48).max()
    data["rolling_min_48"] = data["close"].rolling(48, min_periods=48).min()
    data["pullback_from_high"] = (data["close"] / data["rolling_max_48"] - 1).abs()
    data["pullback_from_low"] = (data["close"] / data["rolling_min_48"] - 1).abs()
    data["volume_trend_6"] = data["volume"].rolling(6, min_periods=6).mean() / data["volume_median_48"]
    width = (data["high"].rolling(24, min_periods=24).max() - data["low"].rolling(24, min_periods=24).min()) / data["close"]
    data["compression"] = width < width.rolling(240, min_periods=48).quantile(0.35)
    data["resume_high_5"] = data["high"].rolling(5, min_periods=5).max().shift(1)
    data["resume_low_5"] = data["low"].rolling(5, min_periods=5).min().shift(1)
    return data


def bank_entry(row: pd.Series, thresholds: dict[str, float]) -> tuple[str | None, int, list[str]]:
    for side, bullish in (("long", True), ("short", False)):
        labels: list[str] = []
        score = 0
        trend_ok = row["slope_24"] > 0 if bullish else row["slope_24"] < 0
        if not trend_ok:
            continue
        score += 1
        labels.append("trend regime aligned")
        pullback = row["pullback_from_high"] if bullish else row["pullback_from_low"]
        if thresholds["continuation_depth"] * 0.25 <= pullback <= thresholds["reversal_depth"]:
            score += 1
            labels.append("continuation-depth correction (bank profile)")
        else:
            continue
        if row["volume_trend_6"] < thresholds["reversal_volume_ratio"]:
            score += 1
            labels.append("no reversal-volume expansion (bank profile)")
        else:
            continue
        resumed = row["close"] > row["resume_high_5"] if bullish else row["close"] < row["resume_low_5"]
        if resumed:
            score += 1
            labels.append("trend resumption bar")
        else:
            continue
        if row["body_ratio"] >= 0.5 and ((row["close_location"] >= 0.6) if bullish else (row["close_location"] <= 0.4)):
            score += 1
            labels.append("directional rotation bar")
        if pd.notna(row["pressure_signed"]) and ((row["pressure_signed"] > 0) if bullish else (row["pressure_signed"] < 0)):
            score += 1
            labels.append("directional pressure")
        if pd.notna(row["delta_3"]) and pd.notna(row["delta_3_median"]):
            if (row["delta_3"] > row["delta_3_median"]) if bullish else (row["delta_3"] < -row["delta_3_median"]):
                score += 1
                labels.append("delta persistence")
        if score >= 5:
            return side, score, labels

    if bool(row.get("compression")):
        for side, bullish in (("long", True), ("short", False)):
            broke = row["close"] > row["prior_high_20"] if bullish else row["close"] < row["prior_low_20"]
            strong = (
                row["volume_ratio"] >= thresholds["breakout_volume_ratio"]
                and row["range_atr_ratio"] >= thresholds["breakout_range_ratio"]
                and row["body_ratio"] >= 0.55
            )
            if broke and strong:
                return side, 5, [
                    "range breakout",
                    "real-breakout volume (bank profile)",
                    "real-breakout range (bank profile)",
                    "strong body",
                    "close beyond range boundary",
                ]
    return None, 0, []


def bank_exit(row: pd.Series, side: str, thresholds: dict[str, float]) -> tuple[bool, list[str]]:
    bullish = side == "long"
    labels: list[str] = []
    pullback = row["pullback_from_high"] if bullish else row["pullback_from_low"]
    if pullback >= thresholds["reversal_depth"]:
        labels.append("reversal-depth retracement (bank profile)")
    if row["volume_trend_6"] >= thresholds["reversal_volume_ratio"] and (
        (row["close"] < row["resume_low_5"]) if bullish else (row["close"] > row["resume_high_5"])
    ):
        labels.append("reversal-volume expansion against position (bank profile)")
    if (row["close"] < row["prior_low_10"]) if bullish else (row["close"] > row["prior_high_10"]):
        labels.append("structural break against position")
    return len(labels) >= 2, labels


def run_segment(
    data: pd.DataFrame,
    thresholds: dict[str, float],
    initial_equity: float = 1000.0,
    leverage: float = 20.0,
    risk_fraction: float = 0.03,
    fee_rate: float = 0.0004,
    slippage_rate: float = 0.0002,
) -> dict[str, Any]:
    data = data.dropna(
        subset=[
            "atr",
            "prior_high_20",
            "prior_low_20",
            "volume_median_48",
            "slope_24",
            "pullback_from_high",
            "volume_trend_6",
            "resume_high_5",
        ]
    )
    equity = initial_equity
    position: dict[str, Any] | None = None
    trades: list[Trade] = []

    def execution_price(price: float, side: str, entering: bool) -> float:
        adverse = slippage_rate if side == "long" else -slippage_rate
        if not entering:
            adverse = -adverse
        return price * (1 + adverse)

    for index in range(len(data) - 1):
        row = data.iloc[index]
        next_row = data.iloc[index + 1]
        if position is not None:
            side = position["side"]
            stop = position["stop"]
            stop_hit = next_row["low"] <= stop if side == "long" else next_row["high"] >= stop
            should_exit, exit_labels = bank_exit(row, side, thresholds)
            if stop_hit or should_exit:
                reason = "risk_stop" if stop_hit else "bank_reversal_evidence"
                raw_exit = stop if stop_hit else float(next_row["open"])
                exit_price = execution_price(float(raw_exit), side, entering=False)
                direction = 1 if side == "long" else -1
                gross = (exit_price - position["entry_price"]) * position["quantity"] * direction
                fees = (position["entry_price"] + exit_price) * position["quantity"] * fee_rate
                net = gross - fees
                trades.append(
                    Trade(
                        side=side,
                        entry_time=position["entry_time"],
                        exit_time=next_row.name.isoformat(),
                        entry_price=position["entry_price"],
                        exit_price=exit_price,
                        quantity=position["quantity"],
                        risk_dollars=position["risk_dollars"],
                        gross_pnl=gross,
                        fees=fees,
                        slippage=abs(exit_price - raw_exit) * position["quantity"],
                        net_pnl=net,
                        return_on_equity=net / position["equity_before"],
                        exit_reason=reason,
                        entry_score=position["entry_score"],
                        entry_evidence=position["entry_evidence"],
                        exit_evidence=(["stop reached"] if stop_hit else exit_labels),
                    )
                )
                equity += net
                position = None
        if position is None and equity > 0:
            side, score, labels = bank_entry(row, thresholds)
            if side:
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
                        "risk_dollars": risk_dollars,
                        "equity_before": equity,
                        "entry_score": score,
                        "entry_evidence": labels,
                        "stop": entry_price - stop_distance if side == "long" else entry_price + stop_distance,
                    }

    if position is not None:
        final = data.iloc[-1]
        side = position["side"]
        exit_price = execution_price(float(final["close"]), side, entering=False)
        direction = 1 if side == "long" else -1
        gross = (exit_price - position["entry_price"]) * position["quantity"] * direction
        fees = (position["entry_price"] + exit_price) * position["quantity"] * fee_rate
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
                exit_evidence=["end of segment"],
            )
        )
        equity += net

    values = [trade.net_pnl for trade in trades]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    return {
        "candles": len(data),
        "start": data.index[0].isoformat() if len(data) else None,
        "end": data.index[-1].isoformat() if len(data) else None,
        "trades": len(trades),
        "final_equity": equity,
        "net_pnl": equity - initial_equity,
        "return_pct": (equity / initial_equity - 1) * 100,
        "win_rate_pct": len(wins) / len(values) * 100 if values else 0,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses and sum(losses) else None,
        "average_trade": float(np.mean(values)) if values else 0,
        "trade_log": [asdict(trade) for trade in trades],
    }


def write_report(result: dict[str, Any], path: str | Path) -> None:
    lines = [
        "# BTCUSDT Bank-Driven Signal Backtest",
        "",
        "Rules are calibrated on the training window only and evaluated unchanged on the test window.",
        "All features are causal and execution happens at the next candle open. Research backtest only.",
        "",
        "## Calibrated thresholds (train window only)",
    ]
    for key, value in result["thresholds"].items():
        lines.append(f"- `{key}`: `{value:.6f}`")
    for name in ("train", "test"):
        segment = result[name]
        lines += [
            "",
            f"## {name.capitalize()} window",
            f"- Range: `{segment['start']}` to `{segment['end']}`",
            f"- Candles: **{segment['candles']:,}**",
            f"- Trades: **{segment['trades']:,}**",
            f"- Final equity: **${segment['final_equity']:.2f}** (from $1000)",
            f"- Return: **{segment['return_pct']:.2f}%**",
            f"- Win rate: **{segment['win_rate_pct']:.2f}%**",
            f"- Profit factor: **{segment['profit_factor']}**",
        ]
    lines += [
        "",
        "## Rules",
        "- Entry A (trend continuation): trend regime + bank-profile continuation correction depth + no reversal-volume expansion + resumption bar (+ pressure/delta/rotation-bar confirmation, score >= 5).",
        "- Entry B (range breakout): compressed range + close beyond 20-bar boundary + bank-profile real-breakout volume/range/body.",
        "- Exit: 2 ATR stop, or >=2 bank reversal evidences (reversal-depth retracement, reversal-volume expansion against position, structural break).",
        "",
        "## Honest interpretation",
        result["interpretation"],
        "",
        "## Safety",
        "- No live orders, exchange API calls, or keys are used.",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bank-driven causal BTCUSDT backtest with train/test split.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--train-fraction", type=float, default=0.7)
    parser.add_argument("--output", default="BTCUSDT_bank_signal_backtest.json")
    parser.add_argument("--report-output", default="BTCUSDT_bank_signal_backtest_report.md")
    args = parser.parse_args()

    frame = load_ohlcv(args.input)
    split = int(len(frame) * args.train_fraction)
    thresholds = calibrate(frame.iloc[:split])
    featured = add_bank_features(frame)
    train_result = run_segment(featured.iloc[:split], thresholds)
    test_result = run_segment(featured.iloc[split:], thresholds)

    train_positive = train_result["net_pnl"] > 0
    test_positive = test_result["net_pnl"] > 0
    if train_positive and test_positive:
        interpretation = "Both windows are profitable under these assumptions, but a single split is weak evidence; walk-forward and stress tests are still required before any live use."
    elif test_positive:
        interpretation = "The test window is profitable but the training window loses money, so the edge is unstable across regimes; this rule set must not be traded live and needs regime analysis and walk-forward validation."
    else:
        interpretation = "The out-of-sample test window is not profitable, so this rule set must not be traded live; it is retained as an honest research result."
    result = {
        "parameters": {
            "initial_equity": 1000.0,
            "leverage": 20.0,
            "risk_fraction": 0.03,
            "fee_rate": 0.0004,
            "slippage_rate": 0.0002,
            "train_fraction": args.train_fraction,
        },
        "thresholds": thresholds,
        "train": train_result,
        "test": test_result,
        "interpretation": interpretation,
    }
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(result, args.report_output)
    printable = {
        name: {key: value for key, value in result[name].items() if key != "trade_log"}
        for name in ("train", "test")
    }
    print(json.dumps(printable, indent=2))


if __name__ == "__main__":
    main()
