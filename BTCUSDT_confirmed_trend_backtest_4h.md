# BTCUSDT Confirmed-Trend Strategy Backtest

Strategy built from the immediate-fake and correction-fate studies:
one-bar confirmation entry, hard-fake-line stop, first-correction
early-warning exit. Research backtest only; not financial advice.

## Execution & capital
- signal bar S, confirmation bar S+1, entry at S+2 open; exits at next open; stops intra-bar
- $1000 initial, 20x cap, 3% risk, 0.04% fee/side, 0.02% slippage/side
- Causality perturbation check: **passed**

## Results
| dataset | trades | final equity | return | win rate | profit factor |
|---|---|---|---|---|---|
| full 2020-2026 | 104 | $1692.00 | 69.20% | 48.08% | 1.365 |
| excluding 2020 (COVID) | 88 | $1266.09 | 26.61% | 46.59% | 1.206 |

## Yearly breakdown (full run)
| year | trades | net PnL | win rate |
|---|---|---|---|
| 2020 | 14 | $202.82 | 50.0% |
| 2021 | 13 | $321.80 | 53.8% |
| 2022 | 15 | $194.54 | 53.3% |
| 2023 | 20 | $-99.07 | 45.0% |
| 2024 | 20 | $-85.10 | 40.0% |
| 2025 | 21 | $211.17 | 52.4% |
| 2026 | 1 | $-54.17 | 0.0% |

## Honest assessment
- The signature and confirmation rules come from the research studies, but min_score=6 and the correction-depth thresholds were selected after seeing full-history results (in-sample tuning). Walk-forward validation is required before trusting any positive number here.
- Compounding at 3% risk makes yearly PnL dollars depend on path; win rate and profit factor are the more comparable numbers.
- Delta/taker-buy columns remain invalid in the source parquet and are not used.
