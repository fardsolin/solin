"""Fixed, declared scenario matrix. No fitting, optimization or winner selection.

Large ledgers/curves stay in ignored artifacts/. Only compact, provenance-rich
summaries and SVGs are suitable for review in Git. Mirrors remain explicitly
unverified and are never represented as original CoinEx historical data.
"""

import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path
import platform

import numpy as np
import pandas as pd

from engine.config import SimulationConfig
from research.backtest import run_backtest, source_fingerprint, utc_ms, write_result
from research.data import read_candles, read_funding
from research.import_mirror import import_snapshot


def equity_svg(curves, path):
    width, height = 1000, 420
    colors = ["#2d7bb6", "#ba3b45", "#27916c", "#9b67b1"]
    all_values = [v["equity"] for curve in curves.values() for v in curve]
    low, high = min(all_values + [10000]), max(all_values + [10000])
    pad = max((high - low) * 0.08, 1)
    low, high = low - pad, high + pad

    def y(value):
        return 345 - (value - low) / (high - low) * 285

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        '<g font-family="sans-serif" font-size="13" fill="#24364a">',
        '<text x="65" y="25" font-size="18">Solin — simulated net equity; third-party historical mirror</text>',
        '<text x="65" y="45">2020-02-03 to 2026-06-30 • initial 10,000 USDT • not CoinEx execution</text>',
    ]
    for value in np.linspace(low, high, 6):
        svg += [
            f'<path d="M65 {y(value):.2f} H965" stroke="#dbe3ed"/>',
            f'<text x="5" y="{y(value) + 4:.2f}">{value:,.0f}</text>',
        ]
    svg.append(f'<path d="M65 {y(10000):.2f} H965" stroke="#6b7280" stroke-dasharray="5 4"/>')
    for index, (name, curve) in enumerate(curves.items()):
        sample = np.unique(np.linspace(0, len(curve) - 1, min(1000, len(curve))).astype(int))
        points = " ".join(
            f"{65 + i / max(1, len(curve) - 1) * 900:.2f},{y(curve[i]['equity']):.2f}" for i in sample
        )
        color = colors[index % len(colors)]
        svg += [
            f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.5"/>',
            f'<text x="{65 + (index % 2) * 455}" y="{375 + (index // 2) * 23}" fill="{color}">{name}</text>',
        ]
    svg += ["</g></svg>"]
    Path(path).write_text("\n".join(svg) + "\n")


def main(data_dir, output_dir, compact_dir):
    data_dir, output_dir, compact_dir = map(Path, (data_dir, output_dir, compact_dir))
    output_dir.mkdir(parents=True, exist_ok=True)
    compact_dir.mkdir(parents=True, exist_ok=True)
    manifests = {source: import_snapshot(source, data_dir) for source in ["binance", "bybit"]}
    candles = {s: read_candles(data_dir / manifests[s]["normalized_path"]) for s in manifests}
    funding = read_funding(data_dir / "binance_funding.csv")
    base = SimulationConfig()
    scenarios = []
    for whale in [False, True]:
        label = "combined" if whale else "core"
        config = replace(base, enable_whale=whale)
        scenarios += [
            (f"{label}_net_full", "binance", config, None, "2026-07-01", True, 0),
            (
                f"{label}_stress_full",
                "binance",
                replace(config, fee_bps=10, slippage_bps=10),
                None,
                "2026-07-01",
                True,
                2,
            ),
            (
                f"{label}_zero_cost_DIAGNOSTIC",
                "binance",
                replace(config, fee_bps=0, slippage_bps=0),
                None,
                "2026-07-01",
                False,
                0,
            ),
        ]
        for start, end in [
            ("2024-01-01", "2025-01-01"),
            ("2025-01-01", "2026-01-01"),
            ("2026-01-01", "2026-07-01"),
        ]:
            scenarios.append((f"{label}_period_{start[:4]}", "binance", config, start, end, True, 0))
    scenarios += [
        ("bybit_core_funding_proxy", "bybit", base, None, "2025-12-01", False, 1),
        (
            "bybit_core_stress_funding_proxy",
            "bybit",
            replace(base, fee_bps=10, slippage_bps=10),
            None,
            "2025-12-01",
            False,
            2,
        ),
    ]
    # Write declarations BEFORE observing results; do not tune the rules against the periods.
    declarations = [
        {
            "name": name,
            "source": source,
            "config": asdict(config),
            "start": start,
            "end_exclusive": end,
            "historical_funding": rates,
            "additional_adverse_funding_bps_8h": drag,
        }
        for name, source, config, start, end, rates, drag in scenarios
    ]
    (output_dir / "scenario_plan.json").write_text(json.dumps(declarations, indent=2) + "\n")
    reports, selected_curves = {}, {}
    for name, source, config, start, end, rates, drag in scenarios:
        result = run_backtest(
            candles[source],
            config,
            funding if rates else None,
            utc_ms(start) if start else None,
            utc_ms(end),
            drag,
        )
        assert (
            abs(
                result["summary"]["final_equity"]
                - config.starting_equity
                - sum(t["net_pnl"] for t in result["trades"])
            )
            < 1e-4
        ), "Ledger does not reconcile"
        provenance = {
            **manifests[source],
            "historical_funding_used": rates,
            "additional_adverse_funding_bps_8h": drag,
            "funding_coverage_policy": "complete UTC 8h settlements required when historical funding is used",
        }
        reports[name] = write_result(result, output_dir / name, config, provenance)
        if name in ["core_net_full", "combined_net_full", "core_stress_full", "combined_stress_full"]:
            selected_curves[name] = result["curve"]
        summary = result["summary"]
        print(
            f"{name}: trades={summary['trades']}, return={summary['net_return_pct']:.2f}%, "
            f"PF={summary['profit_factor_net']}, DD={summary['max_drawdown_close_to_close_pct']:.2f}%",
            flush=True,
        )
    comparison = pd.DataFrame({name: report["summary"] for name, report in reports.items()}).T
    comparison.to_csv(output_dir / "comparison.csv", index_label="scenario")
    compact = {
        "review_date": "2026-09-20",
        "code_sha256": source_fingerprint(),
        "python": platform.python_version(),
        "assessment": "Exploratory historical mirror results; not certification of CoinEx or live performance",
        "no_parameter_optimization": True,
        "scenario_plan": declarations,
        "results": reports,
    }
    (compact_dir / "backtests-2026-09-20.json").write_text(
        json.dumps(compact, indent=2, allow_nan=False) + "\n"
    )
    equity_svg(selected_curves, compact_dir / "equity-2026-09-20.svg")
    return comparison


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data/historical")
    parser.add_argument("--output-dir", default="artifacts/backtests-2026-09-20")
    parser.add_argument("--compact-dir", default="docs/reports")
    args = parser.parse_args()
    main(args.data_dir, args.output_dir, args.compact_dir)
