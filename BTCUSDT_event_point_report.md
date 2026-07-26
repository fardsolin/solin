# BTCUSDT Critical Event-Point Behavior

This report extracts landmark behavior only. It does not generate trading signals or backtests.

## 1. Trend landmarks
- Trends: **982**
- Stored event fields: **17**
- Start volume / mean volume: `1.0678`
- End volume / mean volume: `0.9769`
- Explosive candidates use p90 movement/velocity/acceleration thresholds.
- Regular candidates use p25 movement/velocity thresholds.
- Explosive/regular counts: `99` / `246`.

## 2. Correction landmarks
- `healthy_inside_trend`: 596; mean retracement `2.047985370320524`, duration `41.854026845637584` hours, volume change `-0.004543617118239607`
- `trend_change`: 385; mean retracement `3.4896145638571427`, duration `68.0077922077922` hours, volume change `0.22977544597056926`
- `unresolved`: 1; mean retracement `0.15363482371471004`, duration `49.0` hours, volume change `-0.9324611475764433`

## 3. Range landmarks
- Ranges: **335**
- Mean duration: `40.88955223880597` hours
- Mean width: `3.1869952411966103`%
- Real/fake/unclassified: `211` / `105` / `19`

## 4. Rotation-bar behavior
### trend_to_correction
- Events: **981**
- High-power first post-event bars: **99**
- High-power/regular rotation bars: `99` / `882`
- First post-event return mean: `-0.0008069516268088362`
- First post-event range mean: `0.012572799047612604`
- 10-candle return mean: `-0.00011735374391331142`
- 10-candle volatility mean: `0.005057394491825425`
### range_to_breakout
- Events: **335**
- High-power first post-event bars: **34**
- High-power/regular rotation bars: `34` / `301`
- First post-event return mean: `-0.0008107361879718828`
- First post-event range mean: `0.016005564337733324`
- 10-candle return mean: `0.0003997724244556014`
- 10-candle volatility mean: `0.005198481538278265`

## 5. How to distinguish correction from reversal
At the first bars, the distinction is probabilistic. Useful early measurements are opposing return, wick balance, range expansion, volume expansion, and persistence across several bars.
A stronger structural label requires a prior swing high/low breach plus follow-through. The current database does not store explicit swing prices, so this confirmation cannot yet be measured directly.

## 6. Power versus pressure
- Power: range × volume; identifies participation and expansion.
- Pressure: directional body/return and wick imbalance; identifies acceptance, rejection, or absorption.
- A large candle with a long opposing wick can be high-power but failed-pressure behavior.

## 7. Data gaps before signal design
- explicit prior swing high/low prices
- delta persistence and reversal timing
- liquidity at the event
- direct correction-to-trend transition labels
