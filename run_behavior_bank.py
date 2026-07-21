from __future__ import annotations

import argparse
import json
from pathlib import Path

from behavior_bank import audit_database, behavior_comparison, build_transitions, extract_behavior, load_ohlcv, write_database


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a market behavior database without trading signals.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--asset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--audit", action="append", default=[])
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    audits = [audit_database(path) for path in args.audit if Path(path).exists()]
    candles = load_ohlcv(args.input)
    extracted = extract_behavior(args.asset, candles)
    transitions = build_transitions(args.asset, candles, extracted)
    write_database(args.output, args.asset, extracted, transitions)
    comparison = behavior_comparison(extracted)

    report = [
        f"# {args.asset} Market Behavior Analysis",
        "",
        "This report describes market behavior records only. No trading signals or orders are generated.",
        "",
        "## Input",
        f"- File: `{args.input}`",
        f"- Hourly candles: {len(candles):,}",
        f"- Range: `{candles.index.min().isoformat()}` to `{candles.index.max().isoformat()}`",
        "",
        "## New database",
        f"- Output: `{args.output}`",
        f"- Trends: {len(extracted['trends']):,}",
        f"- Corrections: {len(extracted['corrections']):,}",
        f"- Ranges: {len(extracted['ranges']):,}",
        f"- Transitions: {len(transitions):,}",
        f"- Transition feature count: {max((row['feature_count'] for row in transitions), default=0):,}",
        "- Delta/order pressure: unavailable unless trade-level or order-book data is supplied.",
        "",
        "## Descriptive DNA comparison",
        "This is a behavior similarity audit only; it does not emit entries, exits, or signals.",
        f"- Status: {comparison['status']}",
        f"- Comparisons: {len(comparison.get('comparisons', [])):,}",
        "",
        "## Existing database audits",
        "```json",
        json.dumps(audits, indent=2, default=str),
        "```",
    ]
    Path(args.report).write_text("\n".join(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
