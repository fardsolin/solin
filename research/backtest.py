"""No optimization: fixed rules, next-open fills, explicit costs, and chronological evaluation."""

import argparse
from dataclasses import asdict
import hashlib
import json
import math
import platform
from pathlib import Path

import numpy as np
import pandas as pd

from engine.config import SimulationConfig
from engine.models import HOUR_MS, iso_time
from engine.session import TradingSession
from engine.strategy import MIN_HISTORY_BARS
from research.data import read_candles, read_funding, sha256_file


def metrics(trades, curve, initial_equity, fees, funding_paid):
    equity = np.array([initial_equity] + [row["equity"] for row in curve], dtype=float)
    peaks = np.maximum.accumulate(equity)
    returns = [t["net_pnl"] for t in trades]
    gains = sum(v for v in returns if v > 0)
    losses = -sum(v for v in returns if v < 0)
    years = len(curve) / (24 * 365.25)
    days = (
        pd.Series(
            [r["equity"] for r in curve],
            index=pd.to_datetime([r["time"] for r in curve], unit="ms", utc=True),
        )
        .resample("1D")
        .last()
    )
    daily = days.pct_change().dropna()
    daily_sharpe = (
        float(daily.mean() / daily.std() * math.sqrt(365)) if len(daily) > 2 and daily.std() > 0 else None
    )
    return {
        "initial_equity": initial_equity,
        "final_equity": float(equity[-1]),
        "net_return_pct": float((equity[-1] / initial_equity - 1) * 100),
        "cagr_pct": float(((equity[-1] / initial_equity) ** (1 / years) - 1) * 100)
        if years > 0 and equity[-1] > 0
        else None,
        "max_drawdown_close_to_close_pct": float(np.min(equity / peaks - 1) * 100),
        "profit_factor_net": gains / losses if losses > 0 else None,
        "trades": len(trades),
        "win_rate_pct": 100 * sum(p > 0 for p in returns) / len(returns) if returns else None,
        "average_net_pnl": float(np.mean(returns)) if returns else None,
        "fees_paid": fees,
        "funding_paid_net": funding_paid,
        "daily_sharpe_365_no_risk_free": daily_sharpe,
        "exposure_bar_pct": 100 * sum(r["exposed"] for r in curve) / len(curve) if curve else 0,
        "bankrupt_at_any_close": bool(np.any(equity <= 0)),
        "long_trades": sum(t["direction"] == "long" for t in trades),
        "short_trades": sum(t["direction"] == "short" for t in trades),
    }


def run_backtest(candles, config, funding=None, start_ms=None, end_ms=None, funding_drag_bps=0):
    if len(candles) <= MIN_HISTORY_BARS:
        raise ValueError("More than 800 hourly candles are required, including warmup")
    funding_supplied = funding is not None
    funding = funding or {}
    session = TradingSession(config)
    begin = MIN_HISTORY_BARS
    if start_ms is not None:
        begin = max(begin, next((i for i, c in enumerate(candles) if c.time >= start_ms), len(candles)))
    evaluation = [c for c in candles[begin:] if end_ms is None or c.time < end_ms]
    if not evaluation:
        raise ValueError("No evaluation bars in the requested date range")
    if funding_supplied:
        interval = 8 * HOUR_MS
        first_settlement = ((evaluation[0].time + interval - 1) // interval) * interval
        expected = set(range(first_settlement, evaluation[-1].time + HOUR_MS, interval))
        if not expected.issubset(funding):
            raise ValueError("Incomplete funding coverage for the specified UTC 8-hour settlement model")
    session.warmup(candles[:begin])
    events, curve = [], []
    for c in evaluation:
        was_exposed = session.broker.position is not None
        drag = funding_drag_bps / 10000 if c.time % (8 * HOUR_MS) == 0 else 0
        events.extend(session.process(c, funding_rate=funding.get(c.time, 0.0), funding_drag=drag))
        curve.append(
            {
                "time": c.time + HOUR_MS,
                "equity": session.broker.equity(c.close),
                "exposed": was_exposed
                or session.broker.position is not None
                or any(e["type"] == "enter" and e["time"] == c.time for e in events[-3:]),
            }
        )
    if session.broker.position:
        events.append(
            session.broker.close_position(evaluation[-1].close, evaluation[-1].time + HOUR_MS, "end-of-data")
        )
        curve[-1]["equity"] = session.broker.cash
    trades = [e for e in events if e["type"] == "exit"]
    summary = metrics(
        trades, curve, config.starting_equity, session.broker.fees_paid, session.broker.funding_paid
    )
    summary.update(
        start_utc=iso_time(evaluation[0].time),
        end_utc_exclusive=iso_time(evaluation[-1].time + HOUR_MS),
        warmup_bars=begin,
        evaluation_bars=len(evaluation),
        buy_hold_price_return_pct_gross=(evaluation[-1].close / evaluation[0].open - 1) * 100,
    )
    return {"summary": summary, "trades": trades, "curve": curve, "events": events}


def source_fingerprint():
    root = Path(__file__).resolve().parents[1]
    paths = sorted([*root.glob("engine/*.py"), *root.glob("research/*.py")])
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def write_result(result, output, config, provenance):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(result["trades"]).to_csv(output / "trades.csv", index=False)
    pd.DataFrame(result["curve"]).to_csv(output / "equity.csv", index=False)
    report = {
        "schema_version": 1,
        "code_sha256": source_fingerprint(),
        "environment": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "runtime_lock_sha256": sha256_file(Path(__file__).resolve().parents[1] / "requirements.txt"),
        },
        "config": asdict(config),
        "provenance": provenance,
        "summary": result["summary"],
        "execution_contract": {
            "entries": "next candle open plus adverse slippage",
            "stops": "opening gaps first, then stop-first if ambiguous",
            "trailing": "updated after old stop/target checks; effective next bar",
            "time_exit": "next open after 10 held hourly bars (default)",
            "funding": "carry positions only; candle-open notional approximation",
            "end_of_data": "forced close with exit costs",
        },
        "limitations": [
            "No order-book depth/latency/partial-fill or liquidation engine",
            "Drawdown is hourly close-to-close, not tick or intrabar drawdown",
            "No parameter search was performed; split results are exploratory, not a blind preregistered trial",
            "Passing software tests is not evidence of profitability",
        ],
    }
    (output / "summary.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def utc_ms(value):
    return int(pd.Timestamp(value, tz="UTC").timestamp() * 1000)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--funding")
    parser.add_argument("--allow-missing-funding", action="store_true")
    parser.add_argument("--whale", action="store_true")
    parser.add_argument("--fee-bps", type=float, default=6)
    parser.add_argument("--slippage-bps", type=float, default=2)
    parser.add_argument("--funding-drag-bps", type=float, default=0)
    parser.add_argument("--start")
    parser.add_argument("--end", help="Exclusive UTC date")
    parser.add_argument("--output", default="artifacts/backtest")
    args = parser.parse_args()
    if not args.funding and not args.allow_missing_funding:
        parser.error("Supply --funding or explicitly acknowledge --allow-missing-funding")
    if not math.isfinite(args.funding_drag_bps) or not 0 <= args.funding_drag_bps <= 100:
        parser.error("Funding drag must be between 0 and 100 bps")
    config = SimulationConfig(fee_bps=args.fee_bps, slippage_bps=args.slippage_bps, enable_whale=args.whale)
    candles = read_candles(args.csv)
    funding = read_funding(args.funding) if args.funding else None
    result = run_backtest(
        candles,
        config,
        funding,
        utc_ms(args.start) if args.start else None,
        utc_ms(args.end) if args.end else None,
        args.funding_drag_bps,
    )
    provenance = {
        "dataset_sha256": sha256_file(args.csv),
        "funding_sha256": sha256_file(args.funding) if args.funding else None,
        "funding_missing": not bool(args.funding),
        "additional_funding_drag_bps_8h": args.funding_drag_bps,
    }
    print(json.dumps(write_result(result, args.output, config, provenance)["summary"], indent=2))
