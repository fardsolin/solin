# Losing-Trade Diagnosis

Source: `BTCUSDT_confirmed_trend_backtest.json` (section: `full`). Detailed transactions and a
breakdown of where the losing trades come from. Diagnostic only; not
financial advice and no orders are placed.

## Overall
- Trades: **370** (159 wins / 211 losses, win rate **43.0%**)
- Gross win: **$8797.27**, gross loss: **$-8448.14**, net: **$349.13**, profit factor: **1.041**
- Avg win: **1.09 R**, avg loss: **-0.74 R**, expectancy: **0.047 R/trade**
- Median holding time — winners **13.0h**, losers **6.0h**

## Where the losses come from
### By exit type
| exit type | losers | net loss | share of loss | avg R |
|---|---|---|---|---|
| risk_stop | 116 | $-6602.53 | 78.2% | -1.06 |
| correction_exit | 95 | $-1845.61 | 21.8% | -0.36 |

### By side
| side | losers | net loss | share of loss | avg R |
|---|---|---|---|---|
| long | 119 | $-4727.31 | 56.0% | -0.73 |
| short | 92 | $-3720.83 | 44.0% | -0.75 |

### By year
| year | losers | net loss | share of loss | avg R |
|---|---|---|---|---|
| 2024 | 46 | $-1856.19 | 22.0% | -0.71 |
| 2021 | 38 | $-1743.77 | 20.6% | -0.77 |
| 2025 | 43 | $-1482.81 | 17.6% | -0.77 |
| 2023 | 28 | $-1292.56 | 15.3% | -0.62 |
| 2022 | 26 | $-1180.28 | 14.0% | -0.84 |
| 2020 | 26 | $-792.83 | 9.4% | -0.76 |
| 2026 | 4 | $-99.70 | 1.2% | -0.60 |

### By holding time
| duration | losers | net loss | share of loss | avg R |
|---|---|---|---|---|
| 9-24h | 56 | $-2456.85 | 29.1% | -0.80 |
| 2-3h | 56 | $-1990.91 | 23.6% | -0.65 |
| 4-8h | 55 | $-1744.09 | 20.6% | -0.58 |
| >24h | 30 | $-1506.42 | 17.8% | -0.93 |
| <=1h | 14 | $-749.87 | 8.9% | -1.07 |

## Immediate fakes (losers closed within 2h)
- Count: **58** (27.5% of all losers)
- Net loss: **$-2266.48** (26.8% of all lost dollars)

## Loss streaks
- Max consecutive losers: **12**
- Worst losing-streak drawdown: **$-610.28**

## Biggest single losses
| entry | exit | side | net PnL | R | hours | exit type |
|---|---|---|---|---|---|---|
| 2023-07-05T12:00 | 2023-07-06T07:00 | short | $-93.49 | -1.09 | 19 | risk_stop |
| 2023-07-10T20:00 | 2023-07-10T21:00 | long | $-87.12 | -1.05 | 1 | risk_stop |
| 2023-08-14T16:00 | 2023-08-14T18:00 | long | $-83.84 | -1.12 | 2 | risk_stop |
| 2023-08-02T01:00 | 2023-08-02T15:00 | long | $-82.89 | -1.05 | 14 | risk_stop |
| 2023-09-27T11:00 | 2023-09-27T14:00 | long | $-79.08 | -1.13 | 3 | risk_stop |
| 2023-09-18T12:00 | 2023-09-18T17:00 | long | $-77.59 | -1.07 | 5 | risk_stop |
| 2023-02-26T20:00 | 2023-02-27T16:00 | long | $-77.02 | -1.07 | 20 | risk_stop |
| 2023-03-17T12:00 | 2023-03-17T14:00 | long | $-76.87 | -1.03 | 2 | risk_stop |
| 2023-04-11T02:00 | 2023-04-17T11:00 | long | $-75.88 | -1.04 | 153 | risk_stop |
| 2023-11-09T04:00 | 2023-11-09T16:00 | long | $-73.34 | -1.05 | 12 | risk_stop |

## Diagnosis & fix directions

- Edge is thin: expectancy **0.047 R/trade** on a 43% win rate with 1.09R winners vs -0.74R losers. Small changes to entry quality move it either way.
- The dominant leak is **wrong entries, not exits**: risk-stop hits are 78% of all lost dollars, while correction exits are only 22% and cut small (-0.36R avg). Tightening the entry filter attacks the biggest cost.
- **Immediate fakes still get through**: 58 losers close within 2h (27% of lost dollars). A stricter confirmation (higher `min_score`, stronger `close_location_min`, or a second confirmation bar) targets these directly.
- **Streak risk is real**: up to 12 consecutive losers; sizing/risk must survive that run before this is tradeable.
- Not answerable from trade records alone: whether correction exits also cut *winners* early (needs per-bar MFE/MAE). Re-run the strategy with MFE/MAE instrumentation on the source candles to confirm before loosening the exit.

