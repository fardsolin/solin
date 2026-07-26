# BTCUSDT Bank-Driven Signal Backtest

Rules are calibrated on the training window only and evaluated unchanged on the test window.
All features are causal and execution happens at the next candle open. Research backtest only.

## Calibrated thresholds (train window only)
- `continuation_depth`: `0.018598`
- `reversal_depth`: `0.050060`
- `reversal_volume_ratio`: `1.492894`
- `breakout_volume_ratio`: `1.666157`
- `breakout_range_ratio`: `1.313147`

## Train window
- Range: `2020-01-03T00:00:00+00:00` to `2024-03-31T09:00:00+00:00`
- Candles: **37,154**
- Trades: **557**
- Final equity: **$38.61** (from $1000)
- Return: **-96.14%**
- Win rate: **21.01%**
- Profit factor: **0.9354531965131786**

## Test window
- Range: `2024-03-31T10:00:00+00:00` to `2026-01-24T18:00:00+00:00`
- Candles: **15,945**
- Trades: **248**
- Final equity: **$2528.30** (from $1000)
- Return: **152.83%**
- Win rate: **26.61%**
- Profit factor: **1.1201509391733027**

## Rules
- Entry A (trend continuation): trend regime + bank-profile continuation correction depth + no reversal-volume expansion + resumption bar (+ pressure/delta/rotation-bar confirmation, score >= 5).
- Entry B (range breakout): compressed range + close beyond 20-bar boundary + bank-profile real-breakout volume/range/body.
- Exit: 2 ATR stop, or >=2 bank reversal evidences (reversal-depth retracement, reversal-volume expansion against position, structural break).

## Honest interpretation
The test window is profitable but the training window loses money, so the edge is unstable across regimes; this rule set must not be traded live and needs regime analysis and walk-forward validation.

## Safety
- No live orders, exchange API calls, or keys are used.
