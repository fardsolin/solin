# BTCUSDT User MarketBehaviorStrategy Backtest

The strategy logic is the user's script, unchanged. Two executions are reported.

## 1. Raw script execution (same-candle close)
This matches the original script but entries/exits use the same candle that produced the signal, which is same-bar lookahead.
- Trades: **1**
- Win rate: **0.00%**
- Sum of per-trade returns: **-512.25%**
- Compounded (no leverage, no costs): **-512.25%**

## 2. Causal execution ($1000, 20x cap, 3% risk, fees, slippage, 2 ATR stop)
- Trades: **80**
- Final equity: **$239.76**
- Return: **-76.02%**
- Win rate: **20.00%**
- Profit factor: **0.09003403250004922**

## Notes
- delta comes from TakerBuyBase-derived flow, not order-book data.
- Data-quality finding: in this parquet `TakerBuyBase/Volume` has a median of ~0.005 instead of ~0.5, so the column is not real taker-buy volume and the derived delta is negative on ~99.99% of candles. The delta-flip exit therefore closes LONGs almost immediately and almost never closes SHORTs; the raw run's single trade held a short from ~8,115 to ~49,684 (-512%). A corrected taker-buy column is required before any delta-based rule is meaningful.
- No live orders, exchange APIs, or keys are used.
