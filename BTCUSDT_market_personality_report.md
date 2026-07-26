# BTCUSDT Market Personality Report

This is a descriptive behavior analysis only. It does not generate signals and does not run a backtest.

## Dataset coverage
- Trend records: **982**
- Correction records: **982**
- Range records: **335**
- Transition records: **1,316**

## Trend personality
- Uptrends: **518**
- Downtrends: **464**
- Mean duration: **53.15 hours**
- Mean movement: **0.4764%**
- Mean wave count: **27.78**

### Volume, delta, pressure, and liquidity
- Uptrend volume middle→end change: `0.12177259565015074`
- Downtrend volume middle→end change: `0.12668458871174282`
- Uptrend start/end power proxies: `1.08735021631888` → `0.9702622411715317`
- Downtrend start/end power proxies: `1.0458734632100004` → `0.9843163985357657`
- Delta is available in `982` trend records.
- Pressure is summarized where stored; liquidity is not stored in trend/range rows.

## Correction personality
- Continuation: **596**
- Reversal: **385**
- Range/unknown: **1**
- Post-correction return is not stored, so the report uses the extractor's outcome label only.

## Range personality
- Successful breakout: **211**
- False breakout: **105**
- No classified breakout: **19**
- Mean range width: **3.1870%**

## Transition personality
- `trend_to_correction`: **981** (observed)
  - body: after-before `0.00560246`
  - lower_wick: after-before `-0.00345426`
  - range: after-before `0.000736791`
  - return: after-before `-0.000191406`
  - upper_wick: after-before `-0.0021482`
- `correction_to_trend`: **0** (not_observed_in_database)
- `trend_to_reversal`: **0** (not_observed_in_database)
- `range_to_breakout`: **335** (observed)
  - body: after-before `0.00593042`
  - lower_wick: after-before `-0.00586123`
  - range: after-before `0.00315188`
  - return: after-before `7.73415e-05`
  - upper_wick: after-before `-6.91955e-05`
- `breakout_to_failure`: **0** (not_observed_in_database)

## Data-quality limitations
- `correction_to_trend` and `breakout_to_failure` are not represented in the current 1,316 transition rows.
- `rare_cases` is not reclassified here; this artifact describes the existing behavior database.
- No trading signal, entry/exit rule, or backtest result is produced.
