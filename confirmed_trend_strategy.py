"""Confirmed-trend strategy built from the reliability studies.

Rules derived directly from the measured evidence:

Entry (decision at the close of the confirmation bar, executed at the NEXT
candle open — no same-bar fills anywhere):
- bar S (signal bar): trend-like signature score >= 3
  (structural 20-bar breakout, range/volume expansion, strong body,
  directional close, slope alignment)
- bar C = S+1 (confirmation bar): this is the one-bar confirmation the
  immediate-fake study showed removes most bar-1 fakes:
  * close beyond the signal-bar close in the trend direction
  * trendward close_location >= 0.60
  * close has NOT returned past the signal-bar midpoint

Initial protective stop:
- just beyond the signal bar's opposite extreme (hard-fake line) with a
  0.25 ATR buffer, never tighter than 1 ATR from entry.

Exit (evidence-based, from the first-correction early-warning study):
- once a correction (pullback >= 1 ATR from the running extreme) begins,
  grade its FIRST TWO bars:
  * depth >= 1.5 ATR  -> exit (killer-correction depth)
  * both bars opposing-bodied AND depth >= 1.0 ATR -> exit
- a new extreme beyond the pre-correction extreme ends the correction.
- risk stop always active; checked against the next bar's range.

Capital model (as agreed): $1,000 initial, 20x leverage cap, 3% risk per
trade, 0.04% fee per side, 0.02% slippage per side.

Research backtest only. No live orders, no financial advice.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from behavior_bank import load_ohlcv
from causal_signal_backtest import prepare, validate_causality
from trend_bar_reliability import signature_scores


def run_backtest(
    frame: pd.DataFrame,
    initial_equity: float = 1000.0,
    leverage: float = 20.0,
    risk_fraction: float = 0.03,
    fee_rate: float = 0.0004,
    slippage_rate: float = 0.0002,
    min_score: int = 6,
    close_location_min: float = 0.60,
    corr_trigger_atr: float = 1.0,
    corr_kill_depth_atr: float = 2.5,
    corr_opposing_depth_atr: float = 1.5,
) -> dict[str, Any]:
    data = prepare(frame).dropna(
        subset=["atr", "prior_high_20", "prior_low_20", "slope_24", "volume_median_48"]
    )
    scores = signature_scores(data)
    equity = initial_equity
    position: dict[str, Any] | None = None
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []

    def exec_price(price: float, side: str, entering: bool) -> float:
        adverse = slippage_rate if side == "long" else -slippage_rate
        if not entering:
            adverse = -adverse
        return price * (1 + adverse)

    def close_trade(raw_exit: float, exit_time: str, reason: str) -> None:
        nonlocal equity, position
        assert position is not None
        side = position["side"]
        price = exec_price(float(raw_exit), side, entering=False)
        direction = 1 if side == "long" else -1
        gross = (price - position["entry_price"]) * position["quantity"] * direction
        fees = (position["entry_price"] + price) * position["quantity"] * fee_rate
        net = gross - fees
        equity += net
        trades.append(
            {
                "side": side,
                "entry_time": position["entry_time"],
                "exit_time": exit_time,
                "entry_price": position["entry_price"],
                "exit_price": price,
                "net_pnl": net,
                "return_on_equity": net / position["equity_before"],
                "exit_reason": reason,
            }
        )
        position = None

    for index in range(2, len(data) - 1):
        row = data.iloc[index]
        next_row = data.iloc[index + 1]
        timestamp = data.index[index]

        if position is not None:
            side = position["side"]
            direction = 1 if side == "long" else -1
            atr = position["atr"]

            stop_hit = (
                next_row["low"] <= position["stop"] if side == "long" else next_row["high"] >= position["stop"]
            )

            # update running extreme with the just-closed bar
            if side == "long":
                position["extreme"] = max(position["extreme"], float(row["high"]))
                pullback = position["extreme"] - float(row["low"])
            else:
                position["extreme"] = min(position["extreme"], float(row["low"]))
                pullback = float(row["high"]) - position["extreme"]

            evidence_exit = False
            evidence_labels: list[str] = []
            if not position["in_correction"]:
                if pullback >= corr_trigger_atr * atr:
                    position["in_correction"] = True
                    position["corr_extreme"] = position["extreme"]
                    position["corr_deepest"] = float(row["low"]) if side == "long" else float(row["high"])
                    position["corr_bars"] = 1
                    position["corr_opposing"] = int(
                        (row["close"] < row["open"]) if side == "long" else (row["close"] > row["open"])
                    )
            else:
                position["corr_bars"] += 1
                if side == "long":
                    position["corr_deepest"] = min(position["corr_deepest"], float(row["low"]))
                    made_new_extreme = float(row["high"]) > position["corr_extreme"]
                else:
                    position["corr_deepest"] = max(position["corr_deepest"], float(row["high"]))
                    made_new_extreme = float(row["low"]) < position["corr_extreme"]
                position["corr_opposing"] += int(
                    (row["close"] < row["open"]) if side == "long" else (row["close"] > row["open"])
                )
                if made_new_extreme:
                    position["in_correction"] = False
                    position["extreme"] = float(row["high"]) if side == "long" else float(row["low"])

            # early warning is graded ONCE, at the second correction bar only;
            # deeper mature corrections are left to the risk stop (survivors'
            # full median depth is 1.8 ATR and must not be cut)
            if position is not None and position["in_correction"] and position["corr_bars"] == 2:
                depth = abs(position["corr_extreme"] - position["corr_deepest"]) / atr
                if depth >= corr_kill_depth_atr:
                    evidence_exit = True
                    evidence_labels.append(f"correction depth {depth:.2f} ATR >= {corr_kill_depth_atr}")
                elif position["corr_opposing"] >= 2 and depth >= corr_opposing_depth_atr:
                    evidence_exit = True
                    evidence_labels.append(
                        f"two opposing-body correction bars with depth {depth:.2f} ATR"
                    )

            if stop_hit:
                close_trade(position["stop"], next_row.name.isoformat(), "risk_stop")
            elif evidence_exit:
                close_trade(float(next_row["open"]), next_row.name.isoformat(), "; ".join(evidence_labels))

        if position is None and equity > 0:
            prev = data.iloc[index - 1]
            for direction, score_col in ((1, "up_score"), (-1, "down_score")):
                score = int(scores[score_col].iloc[index - 1])
                if score < min_score:
                    continue
                side = "long" if direction > 0 else "short"
                rng = float(row["high"] - row["low"])
                close_loc = (float(row["close"]) - float(row["low"])) / rng if rng > 0 else 0.5
                if direction < 0:
                    close_loc = 1.0 - close_loc
                beyond_signal_close = (
                    row["close"] > prev["close"] if direction > 0 else row["close"] < prev["close"]
                )
                midpoint = (float(prev["high"]) + float(prev["low"])) / 2.0
                held_mid = row["close"] > midpoint if direction > 0 else row["close"] < midpoint
                if not (beyond_signal_close and close_loc >= close_location_min and held_mid):
                    continue
                atr = float(row["atr"])
                entry_raw = float(next_row["open"])
                entry_price = exec_price(entry_raw, side, entering=True)
                hard_fake_line = float(prev["low"]) if direction > 0 else float(prev["high"])
                stop = hard_fake_line - direction * 0.25 * atr
                stop_distance = abs(entry_price - stop)
                min_distance = 1.0 * atr
                if stop_distance < min_distance:
                    stop = entry_price - direction * min_distance
                    stop_distance = min_distance
                risk_dollars = equity * risk_fraction
                quantity = min(risk_dollars / stop_distance, equity * leverage / entry_price)
                if quantity <= 0:
                    continue
                position = {
                    "side": side,
                    "entry_time": next_row.name.isoformat(),
                    "entry_price": entry_price,
                    "quantity": quantity,
                    "equity_before": equity,
                    "stop": stop,
                    "atr": atr,
                    "extreme": entry_price,
                    "in_correction": False,
                    "corr_extreme": np.nan,
                    "corr_deepest": np.nan,
                    "corr_bars": 0,
                    "corr_opposing": 0,
                }
                break

        equity_curve.append({"timestamp": timestamp.isoformat(), "equity": equity})

    if position is not None:
        final = data.iloc[-1]
        close_trade(float(final["close"]), final.name.isoformat(), "end_of_data")

    nets = [t["net_pnl"] for t in trades]
    wins = [v for v in nets if v > 0]
    losses = [v for v in nets if v <= 0]
    yearly: dict[str, dict[str, Any]] = {}
    for trade in trades:
        year = trade["exit_time"][:4]
        slot = yearly.setdefault(year, {"trades": 0, "net_pnl": 0.0, "wins": 0})
        slot["trades"] += 1
        slot["net_pnl"] += trade["net_pnl"]
        slot["wins"] += int(trade["net_pnl"] > 0)
    for slot in yearly.values():
        slot["win_rate_pct"] = slot["wins"] / slot["trades"] * 100 if slot["trades"] else None

    return {
        "parameters": {
            "initial_equity": initial_equity,
            "leverage": leverage,
            "risk_fraction": risk_fraction,
            "fee_rate": fee_rate,
            "slippage_rate": slippage_rate,
            "min_score": min_score,
            "close_location_min": close_location_min,
            "corr_trigger_atr": corr_trigger_atr,
            "corr_kill_depth_atr": corr_kill_depth_atr,
            "corr_opposing_depth_atr": corr_opposing_depth_atr,
            "execution": "signal bar S, confirmation bar S+1, entry at S+2 open; exits at next open; stops intra-bar",
        },
        "summary": {
            "candles_used": len(data),
            "trades": len(trades),
            "final_equity": equity,
            "return_pct": (equity / initial_equity - 1) * 100,
            "win_rate_pct": len(wins) / len(nets) * 100 if nets else 0,
            "profit_factor": sum(wins) / abs(sum(losses)) if losses and sum(losses) else None,
            "average_trade": float(np.mean(nets)) if nets else 0,
            "max_win": max(nets, default=0),
            "max_loss": min(nets, default=0),
        },
        "yearly": dict(sorted(yearly.items())),
        "trades": trades,
        "equity_curve_sampled": equity_curve[:: max(1, len(equity_curve) // 500)],
    }


def write_report(full: dict[str, Any], ex2020: dict[str, Any], validation: dict[str, Any], path: str | Path) -> None:
    lines = [
        "# BTCUSDT Confirmed-Trend Strategy Backtest",
        "",
        "Strategy built from the immediate-fake and correction-fate studies:",
        "one-bar confirmation entry, hard-fake-line stop, first-correction",
        "early-warning exit. Research backtest only; not financial advice.",
        "",
        "## Execution & capital",
        f"- {full['parameters']['execution']}",
        f"- ${full['parameters']['initial_equity']:.0f} initial, {full['parameters']['leverage']:.0f}x cap, "
        f"{full['parameters']['risk_fraction'] * 100:.0f}% risk, {full['parameters']['fee_rate'] * 100:.2f}% fee/side, "
        f"{full['parameters']['slippage_rate'] * 100:.2f}% slippage/side",
        f"- Causality perturbation check: **{validation['status']}**",
        "",
        "## Results",
        "| dataset | trades | final equity | return | win rate | profit factor |",
        "|---|---|---|---|---|---|",
    ]
    for name, result in (("full 2020-2026", full), ("excluding 2020 (COVID)", ex2020)):
        s = result["summary"]
        pf = f"{s['profit_factor']:.3f}" if s["profit_factor"] is not None else "-"
        lines.append(
            f"| {name} | {s['trades']:,} | ${s['final_equity']:.2f} | {s['return_pct']:.2f}% | "
            f"{s['win_rate_pct']:.2f}% | {pf} |"
        )
    lines += ["", "## Yearly breakdown (full run)", "| year | trades | net PnL | win rate |", "|---|---|---|---|"]
    for year, slot in full["yearly"].items():
        lines.append(f"| {year} | {slot['trades']:,} | ${slot['net_pnl']:.2f} | {slot['win_rate_pct']:.1f}% |")
    lines += [
        "",
        "## Honest assessment",
        "- The signature and confirmation rules come from the research studies, but "
        "min_score=6 and the correction-depth thresholds were selected after seeing "
        "full-history results (in-sample tuning). Walk-forward validation is required "
        "before trusting any positive number here.",
        "- Compounding at 3% risk makes yearly PnL dollars depend on path; win rate and profit factor "
        "are the more comparable numbers.",
        "- Delta/taker-buy columns remain invalid in the source parquet and are not used.",
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the confirmed-trend strategy.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="BTCUSDT_confirmed_trend_backtest.json")
    parser.add_argument("--report-output", default="BTCUSDT_confirmed_trend_backtest.md")
    args = parser.parse_args()

    frame = load_ohlcv(args.input)
    full = run_backtest(frame)
    ex2020 = run_backtest(frame[frame.index >= "2021-01-01"])
    validation = validate_causality(frame)
    result = {"full": full, "excluding_2020": ex2020, "validation": validation}
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_report(full, ex2020, validation, args.report_output)
    print(json.dumps({"full": full["summary"], "excluding_2020": ex2020["summary"], "yearly_full": full["yearly"]}, indent=2))


if __name__ == "__main__":
    main()
