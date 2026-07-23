import csv
import json
from pathlib import Path

from losing_trade_analysis import (
    analyze,
    enrich,
    exit_category,
    load_trades,
    write_csv,
    write_report,
)


def _sample_trades() -> list[dict[str, object]]:
    return [
        {
            "side": "long",
            "entry_time": "2024-01-01T00:00:00+00:00",
            "exit_time": "2024-01-01T02:00:00+00:00",
            "entry_price": 100.0,
            "exit_price": 103.0,
            "net_pnl": 30.0,
            "return_on_equity": 0.03,
            "exit_reason": "correction depth 3.0 ATR >= 2.5",
        },
        {
            "side": "short",
            "entry_time": "2024-01-02T00:00:00+00:00",
            "exit_time": "2024-01-02T01:00:00+00:00",
            "entry_price": 100.0,
            "exit_price": 103.0,
            "net_pnl": -30.0,
            "return_on_equity": -0.03,
            "exit_reason": "risk_stop",
        },
        {
            "side": "long",
            "entry_time": "2024-01-03T00:00:00+00:00",
            "exit_time": "2024-01-04T00:00:00+00:00",
            "entry_price": 100.0,
            "exit_price": 99.0,
            "net_pnl": -10.0,
            "return_on_equity": -0.01,
            "exit_reason": "correction depth 1.8 ATR",
        },
    ]


def test_exit_category() -> None:
    assert exit_category("risk_stop") == "risk_stop"
    assert exit_category("end_of_data") == "end_of_data"
    assert exit_category("correction depth 3.0 ATR >= 2.5") == "correction_exit"


def test_enrich_computes_duration_and_r_multiple() -> None:
    trade = _sample_trades()[1]
    enriched = enrich(trade, risk_fraction=0.03)
    assert enriched["duration_hours"] == 1.0
    assert round(enriched["r_multiple"], 6) == -1.0
    assert enriched["result"] == "loss"
    # short that moved up 3% is a 3% adverse move -> negative price_move_pct
    assert round(enriched["price_move_pct"], 2) == -3.0


def test_analyze_overall_and_loss_breakdown() -> None:
    result = analyze(_sample_trades(), risk_fraction=0.03)
    overall = result["overall"]
    assert overall["trades"] == 3
    assert overall["wins"] == 1
    assert overall["losses"] == 2
    assert overall["gross_win"] == 30.0
    assert overall["gross_loss"] == -40.0
    assert result["loss_by_exit_category"]["risk_stop"]["count"] == 1
    assert result["loss_by_exit_category"]["correction_exit"]["count"] == 1
    # one loser (the risk_stop, 1h) is within the 2h immediate-fake window
    assert result["fast_losers"]["count"] == 1
    assert result["streaks"]["max_consecutive_losers"] == 2


def test_load_trades_auto_handles_nested_and_flat() -> None:
    flat = {"parameters": {"risk_fraction": 0.02}, "trades": _sample_trades()}
    trades, rf = load_trades(flat, "auto")
    assert len(trades) == 3 and rf == 0.02

    nested = {"full": {"parameters": {"risk_fraction": 0.03}, "trades": _sample_trades()}}
    trades, rf = load_trades(nested, "auto")
    assert len(trades) == 3 and rf == 0.03


def test_write_csv_and_report(tmp_path: Path) -> None:
    result = analyze(_sample_trades(), risk_fraction=0.03)
    csv_path = tmp_path / "trades.csv"
    write_csv(result["trades"], csv_path)
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert len(rows) == 3
    assert rows[0]["result"] == "win"

    report_path = tmp_path / "report.md"
    write_report(result, "sample.json", "auto", report_path)
    text = report_path.read_text(encoding="utf-8")
    assert "# Losing-Trade Diagnosis" in text
    assert "Diagnosis & fix directions" in text


def test_end_to_end_json_roundtrip(tmp_path: Path) -> None:
    payload = {"full": {"parameters": {"risk_fraction": 0.03}, "trades": _sample_trades()}}
    trades, rf = load_trades(payload, "full")
    result = analyze(trades, rf)
    dumped = json.dumps({k: v for k, v in result.items() if k != "trades"})
    assert "loss_by_exit_category" in dumped
