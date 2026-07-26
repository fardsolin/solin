# BTCUSDT Causal Signal Backtest

This is an initial research backtest, not a production strategy or financial advice.
All features are causal; entries execute at the next candle open.

## Parameters
- Initial equity: `$1000.00`
- Leverage cap: `20.0x`
- Risk budget: `3.0%` of current equity per trade
- Fee: `0.040%` per side
- Slippage: `0.020%` per side

## Results
- Candles: **53,099**
- Trades: **1,816**
- Final equity: **$37.36**
- Net PnL: **$-962.64**
- Return: **-96.26%**
- Win rate: **26.87%**
- Profit factor: **0.9709917132585734**

## Rules
- Entry: score >= 5 from structural breakout, regression slope, range expansion, volume, directional body/close, delta persistence, pressure
- Exit: 2 ATR risk stop or opposite structural break with >=4 opposing evidence points
- Look-ahead: none; all features are shifted/rolling-causal and execution is next-open

## Interpretation
This first fixed-rule version is not acceptable for deployment because the observed result is negative. The result is retained as an honest baseline; thresholds must not be tuned on the same full history and then called validation.

## Safety checks
- Causality perturbation: **passed**
- No live orders, API keys, or exchange actions were used.
