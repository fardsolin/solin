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
| full 2020-2026 | 370 | $1349.13 | 34.91% | 42.97% | 1.041 |
| excluding 2020 (COVID) | 313 | $698.97 | -30.10% | 40.89% | 0.924 |

## Yearly breakdown (full run)
| year | trades | net PnL | win rate |
|---|---|---|---|
| 2020 | 56 | $892.21 | 53.6% |
| 2021 | 61 | $-156.89 | 37.7% |
| 2022 | 52 | $174.98 | 50.0% |
| 2023 | 45 | $251.94 | 37.8% |
| 2024 | 77 | $-616.42 | 40.3% |
| 2025 | 74 | $-129.05 | 41.9% |
| 2026 | 5 | $-67.64 | 20.0% |

## Honest assessment
- The signature and confirmation rules come from the research studies, but min_score=6 and the correction-depth thresholds were selected after seeing full-history results (in-sample tuning). Walk-forward validation is required before trusting any positive number here.
- Compounding at 3% risk makes yearly PnL dollars depend on path; win rate and profit factor are the more comparable numbers.
- Delta/taker-buy columns remain invalid in the source parquet and are not used.
