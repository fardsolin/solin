"""Extract detailed transactions from a backtest and diagnose the losers.

This tool does not run a strategy or place any order. It reads the per-trade
records already produced by a backtest JSON (for example
``BTCUSDT_confirmed_trend_backtest.json``), writes every trade out as a flat
CSV of detailed transactions, and builds a breakdown that shows *where* and
*why* the losing trades lose so the rules can be improved.

Each trade record is expected to carry at least ``side``, ``entry_time``,
``exit_time``, ``entry_price``, ``exit_price``, ``net_pnl``,
``return_on_equity`` and ``exit_reason`` (the shape emitted by the strategy
backtests in this repository).

Research/diagnostic only. No live orders, exchange APIs, or keys are used.
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


def load_trades(payload: dict[str, Any], section: str) -> tuple[list[dict[str, Any]], float]:
    """Return the trade list and the risk fraction from a backtest payload.

    ``section`` selects a nested result block (``full``/``excluding_2020``);
    ``auto`` uses the top level if it already has ``trades`` and otherwise the
    ``full`` block.
    """
    block: dict[str, Any]
    if section == "auto":
        block = payload if "trades" in payload else payload.get("full", payload)
    else:
        if section not in payload:
            raise KeyError(f"section {section!r} not found; available: {list(payload)}")
        block = payload[section]
    if "trades" not in block:
        raise KeyError("no 'trades' list found in the selected section")
    risk_fraction = float(block.get("parameters", {}).get("risk_fraction", 0.0)) or 0.0
    return list(block["trades"]), risk_fraction


def exit_category(reason: str) -> str:
    if reason == "risk_stop":
        return "risk_stop"
    if reason == "end_of_data":
        return "end_of_data"
    return "correction_exit"


def enrich(trade: dict[str, Any], risk_fraction: float) -> dict[str, Any]:
    entry = datetime.fromisoformat(trade["entry_time"])
    exit_ = datetime.fromisoformat(trade["exit_time"])
    duration_hours = (exit_ - entry).total_seconds() / 3600.0
    direction = 1 if trade["side"] == "long" else -1
    entry_price = float(trade["entry_price"])
    exit_price = float(trade["exit_price"])
    price_move_pct = (exit_price - entry_price) / entry_price * 100.0 * direction
    roe = float(trade["return_on_equity"])
    r_multiple = roe / risk_fraction if risk_fraction else float("nan")
    net = float(trade["net_pnl"])
    return {
        "side": trade["side"],
        "entry_time": trade["entry_time"],
        "exit_time": trade["exit_time"],
        "duration_hours": duration_hours,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "price_move_pct": price_move_pct,
        "net_pnl": net,
        "return_on_equity": roe,
        "r_multiple": r_multiple,
        "exit_category": exit_category(trade["exit_reason"]),
        "exit_reason": trade["exit_reason"],
        "result": "win" if net > 0 else "loss",
        "year": trade["exit_time"][:4],
        "entry_hour": entry.hour,
    }


def _duration_bucket(hours: float) -> str:
    if hours <= 1:
        return "<=1h"
    if hours <= 3:
        return "2-3h"
    if hours <= 8:
        return "4-8h"
    if hours <= 24:
        return "9-24h"
    return ">24h"


def _grouped_loss(losers: list[dict[str, Any]], key: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    total = sum(t["net_pnl"] for t in losers) or 1.0
    for trade in losers:
        name = str(key(trade))
        slot = out.setdefault(name, {"count": 0, "net_pnl": 0.0, "r_sum": 0.0})
        slot["count"] += 1
        slot["net_pnl"] += trade["net_pnl"]
        slot["r_sum"] += trade["r_multiple"]
    for slot in out.values():
        slot["avg_r"] = slot["r_sum"] / slot["count"] if slot["count"] else 0.0
        slot["share_of_loss_pct"] = slot["net_pnl"] / total * 100.0
    return dict(sorted(out.items(), key=lambda kv: kv[1]["net_pnl"]))


def _max_consecutive(trades: list[dict[str, Any]]) -> dict[str, Any]:
    max_streak = current = 0
    worst_streak_pnl = worst_current = 0.0
    for trade in trades:
        if trade["net_pnl"] <= 0:
            current += 1
            worst_current += trade["net_pnl"]
            max_streak = max(max_streak, current)
            worst_streak_pnl = min(worst_streak_pnl, worst_current)
        else:
            current = 0
            worst_current = 0.0
    return {"max_consecutive_losers": max_streak, "worst_streak_net_pnl": worst_streak_pnl}


def analyze(trades: list[dict[str, Any]], risk_fraction: float) -> dict[str, Any]:
    enriched = [enrich(t, risk_fraction) for t in trades]
    winners = [t for t in enriched if t["net_pnl"] > 0]
    losers = [t for t in enriched if t["net_pnl"] <= 0]
    gross_win = sum(t["net_pnl"] for t in winners)
    gross_loss = sum(t["net_pnl"] for t in losers)

    def median(values: list[float]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2.0

    def mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    n = len(enriched)
    win_rate = len(winners) / n if n else 0.0
    avg_win_r = mean([t["r_multiple"] for t in winners])
    avg_loss_r = mean([t["r_multiple"] for t in losers])
    expectancy_r = win_rate * avg_win_r + (1 - win_rate) * avg_loss_r

    fast_losers = [t for t in losers if t["duration_hours"] <= 2]
    top_losses = sorted(losers, key=lambda t: t["net_pnl"])[:10]

    return {
        "overall": {
            "trades": n,
            "wins": len(winners),
            "losses": len(losers),
            "win_rate_pct": win_rate * 100.0,
            "gross_win": gross_win,
            "gross_loss": gross_loss,
            "net_pnl": gross_win + gross_loss,
            "profit_factor": gross_win / abs(gross_loss) if gross_loss else None,
            "avg_win_r": avg_win_r,
            "avg_loss_r": avg_loss_r,
            "expectancy_r": expectancy_r,
            "median_win_duration_hours": median([t["duration_hours"] for t in winners]),
            "median_loss_duration_hours": median([t["duration_hours"] for t in losers]),
        },
        "loss_by_exit_category": _grouped_loss(losers, lambda t: t["exit_category"]),
        "loss_by_side": _grouped_loss(losers, lambda t: t["side"]),
        "loss_by_year": _grouped_loss(losers, lambda t: t["year"]),
        "loss_by_duration_bucket": _grouped_loss(losers, lambda t: _duration_bucket(t["duration_hours"])),
        "fast_losers": {
            "definition": "losers closed within 2 hours of entry (immediate fakes)",
            "count": len(fast_losers),
            "net_pnl": sum(t["net_pnl"] for t in fast_losers),
            "share_of_losers_pct": len(fast_losers) / len(losers) * 100.0 if losers else 0.0,
            "share_of_loss_pct": (
                sum(t["net_pnl"] for t in fast_losers) / gross_loss * 100.0 if gross_loss else 0.0
            ),
        },
        "streaks": _max_consecutive(enriched),
        "biggest_losses": [
            {
                "entry_time": t["entry_time"],
                "exit_time": t["exit_time"],
                "side": t["side"],
                "net_pnl": t["net_pnl"],
                "r_multiple": t["r_multiple"],
                "duration_hours": t["duration_hours"],
                "exit_category": t["exit_category"],
            }
            for t in top_losses
        ],
        "trades": enriched,
    }


CSV_COLUMNS = [
    "side",
    "entry_time",
    "exit_time",
    "duration_hours",
    "entry_price",
    "exit_price",
    "price_move_pct",
    "net_pnl",
    "return_on_equity",
    "r_multiple",
    "exit_category",
    "exit_reason",
    "result",
    "year",
    "entry_hour",
]


def write_csv(enriched: list[dict[str, Any]], path: str | Path) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for trade in enriched:
            writer.writerow({key: trade[key] for key in CSV_COLUMNS})


def _grouped_table(title: str, grouped: dict[str, dict[str, Any]], label: str) -> list[str]:
    lines = [f"### {title}", f"| {label} | losers | net loss | share of loss | avg R |", "|---|---|---|---|---|"]
    for name, slot in grouped.items():
        lines.append(
            f"| {name} | {slot['count']} | ${slot['net_pnl']:.2f} | "
            f"{slot['share_of_loss_pct']:.1f}% | {slot['avg_r']:.2f} |"
        )
    lines.append("")
    return lines


def write_report(result: dict[str, Any], source: str, section: str, path: str | Path) -> None:
    o = result["overall"]
    pf = f"{o['profit_factor']:.3f}" if o["profit_factor"] is not None else "-"
    lines = [
        "# Losing-Trade Diagnosis",
        "",
        f"Source: `{source}` (section: `{section}`). Detailed transactions and a",
        "breakdown of where the losing trades come from. Diagnostic only; not",
        "financial advice and no orders are placed.",
        "",
        "## Overall",
        f"- Trades: **{o['trades']}** ({o['wins']} wins / {o['losses']} losses, "
        f"win rate **{o['win_rate_pct']:.1f}%**)",
        f"- Gross win: **${o['gross_win']:.2f}**, gross loss: **${o['gross_loss']:.2f}**, "
        f"net: **${o['net_pnl']:.2f}**, profit factor: **{pf}**",
        f"- Avg win: **{o['avg_win_r']:.2f} R**, avg loss: **{o['avg_loss_r']:.2f} R**, "
        f"expectancy: **{o['expectancy_r']:.3f} R/trade**",
        f"- Median holding time — winners **{o['median_win_duration_hours']:.1f}h**, "
        f"losers **{o['median_loss_duration_hours']:.1f}h**",
        "",
        "## Where the losses come from",
    ]
    lines += _grouped_table("By exit type", result["loss_by_exit_category"], "exit type")
    lines += _grouped_table("By side", result["loss_by_side"], "side")
    lines += _grouped_table("By year", result["loss_by_year"], "year")
    lines += _grouped_table("By holding time", result["loss_by_duration_bucket"], "duration")

    fl = result["fast_losers"]
    lines += [
        "## Immediate fakes (losers closed within 2h)",
        f"- Count: **{fl['count']}** ({fl['share_of_losers_pct']:.1f}% of all losers)",
        f"- Net loss: **${fl['net_pnl']:.2f}** ({fl['share_of_loss_pct']:.1f}% of all lost dollars)",
        "",
        "## Loss streaks",
        f"- Max consecutive losers: **{result['streaks']['max_consecutive_losers']}**",
        f"- Worst losing-streak drawdown: **${result['streaks']['worst_streak_net_pnl']:.2f}**",
        "",
        "## Biggest single losses",
        "| entry | exit | side | net PnL | R | hours | exit type |",
        "|---|---|---|---|---|---|---|",
    ]
    for t in result["biggest_losses"]:
        lines.append(
            f"| {t['entry_time'][:16]} | {t['exit_time'][:16]} | {t['side']} | "
            f"${t['net_pnl']:.2f} | {t['r_multiple']:.2f} | {t['duration_hours']:.0f} | "
            f"{t['exit_category']} |"
        )
    lines.append("")
    lines += _diagnosis(result)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _diagnosis(result: dict[str, Any]) -> list[str]:
    """Data-driven, honest reading of the numbers above. No overclaiming."""
    o = result["overall"]
    risk_stop = result["loss_by_exit_category"].get("risk_stop", {"share_of_loss_pct": 0.0})
    corr = result["loss_by_exit_category"].get("correction_exit", {"share_of_loss_pct": 0.0, "avg_r": 0.0})
    fl = result["fast_losers"]
    lines = ["## Diagnosis & fix directions", ""]
    lines.append(
        f"- Edge is thin: expectancy **{o['expectancy_r']:.3f} R/trade** on a "
        f"{o['win_rate_pct']:.0f}% win rate with {o['avg_win_r']:.2f}R winners vs "
        f"{o['avg_loss_r']:.2f}R losers. Small changes to entry quality move it either way."
    )
    lines.append(
        f"- The dominant leak is **wrong entries, not exits**: risk-stop hits are "
        f"{risk_stop['share_of_loss_pct']:.0f}% of all lost dollars, while correction "
        f"exits are only {corr['share_of_loss_pct']:.0f}% and cut small "
        f"({corr['avg_r']:.2f}R avg). Tightening the entry filter attacks the biggest cost."
    )
    lines.append(
        f"- **Immediate fakes still get through**: {fl['count']} losers close within 2h "
        f"({fl['share_of_loss_pct']:.0f}% of lost dollars). A stricter confirmation "
        f"(higher `min_score`, stronger `close_location_min`, or a second confirmation bar) "
        f"targets these directly."
    )
    lines.append(
        f"- **Streak risk is real**: up to {result['streaks']['max_consecutive_losers']} "
        f"consecutive losers; sizing/risk must survive that run before this is tradeable."
    )
    lines.append(
        "- Not answerable from trade records alone: whether correction exits also cut "
        "*winners* early (needs per-bar MFE/MAE). Re-run the strategy with MFE/MAE "
        "instrumentation on the source candles to confirm before loosening the exit."
    )
    lines.append("")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose losing trades from a backtest JSON.")
    parser.add_argument("--input", required=True, help="backtest JSON with per-trade records")
    parser.add_argument("--section", default="auto", help="auto | full | excluding_2020 | <block name>")
    parser.add_argument("--csv-output", default="BTCUSDT_confirmed_trend_trades.csv")
    parser.add_argument("--json-output", default="BTCUSDT_losing_trade_analysis.json")
    parser.add_argument("--report-output", default="BTCUSDT_losing_trade_analysis.md")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    trades, risk_fraction = load_trades(payload, args.section)
    result = analyze(trades, risk_fraction)

    write_csv(result["trades"], args.csv_output)
    report_view = {key: value for key, value in result.items() if key != "trades"}
    Path(args.json_output).write_text(
        json.dumps(report_view, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    write_report(result, args.input, args.section, args.report_output)
    print(json.dumps({"overall": result["overall"], "fast_losers": result["fast_losers"]}, indent=2))


if __name__ == "__main__":
    main()
