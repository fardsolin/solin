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
| full 2020-2026 | 700 | $189.78 | -81.02% | 37.14% | 0.866 |
| excluding 2020 (COVID) | 604 | $152.83 | -84.72% | 36.42% | 0.783 |

## Yearly breakdown (full run)
| year | trades | net PnL | win rate |
|---|---|---|---|
| 2020 | 95 | $260.33 | 42.1% |
| 2021 | 123 | $-565.37 | 30.9% |
| 2022 | 112 | $-34.41 | 38.4% |
| 2023 | 99 | $-77.78 | 39.4% |
| 2024 | 129 | $-282.96 | 36.4% |
| 2025 | 136 | $-108.11 | 38.2% |
| 2026 | 6 | $-1.91 | 16.7% |

## Honest assessment
- The signature and confirmation rules come from the research studies, but min_score=6 and the correction-depth thresholds were selected after seeing full-history results (in-sample tuning). Walk-forward validation is required before trusting any positive number here.
- Compounding at 3% risk makes yearly PnL dollars depend on path; win rate and profit factor are the more comparable numbers.
- Delta/taker-buy columns remain invalid in the source parquet and are not used.
