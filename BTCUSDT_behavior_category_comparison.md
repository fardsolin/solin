# BTCUSDT Behavior Category Comparison

Descriptive market-behavior analysis only. No signals and no backtests were produced.

## Category counts
- `healthy_trend`: **982**
- `healthy_correction`: **596**
- `correction_to_trend`: **385**
- `new_trend_after_correction`: **0**
- `range`: **335**
- `real_range_breakout`: **211**
- `fake_range_breakout`: **105**

## 1. Trend versus healthy correction
These tables have different schemas; the report preserves units and compares each group's own metrics rather than pretending unlike fields are directly interchangeable.
- Trend records analyzed: 982
- Healthy correction records analyzed: 596
- Transition feature dimensionality: 9 trend feature fields and 196 transition fields.

## 2. Healthy correction versus correction-to-trend
- Continuation corrections: 596
- Reversal corrections used as the explicit-transition proxy: 385

## 3. Real versus fake range breakout
- Real breakouts: 211
- Fake breakouts: 105
- Matched transition snapshots: 211 real and 105 fake.
- Feature comparison width: 196 metrics.

### Correction comparison table
| Metric | Healthy correction mean | Correction-to-trend mean | Difference |
|---|---:|---:|---:|
| `retracement_pct` | 2.04799 | 3.48961 | 1.44163 |
| `duration_hours` | 41.854 | 68.0078 | 26.1538 |
| `volume_change` | -0.00454362 | 0.229775 | 0.234319 |

### Range breakout comparison table
| Metric | Real breakout mean | Fake breakout mean | Difference |
|---|---:|---:|---:|
| `duration_hours` | 39.6019 | 38.9143 | -0.68761 |
| `width_pct` | 3.00998 | 3.26163 | 0.251653 |
| `upper_touches` | 2.94787 | 2.75238 | -0.195486 |
| `lower_touches` | 2.76777 | 2.86667 | 0.0988942 |
| `volume_mean` | 2004.9 | 2262.38 | 257.48 |

### 196-feature transition matrix
The JSON artifact contains the complete 196-feature before/after matrices for trend-to-correction versus range-to-breakout and real versus fake breakout snapshots.

## Rare cases
- Sudden trend-break candidates: 46
- Corrections classified as reversal: 385
- Fake range breakouts: 105

## Limitations
- The current bank stores 196 transition features, but trend/correction/range rows store fewer scalar fields.
- Explicit correction_to_trend, trend_to_reversal, and breakout_to_failure transition rows are absent.
- Sudden trend breaks are anomaly candidates, not validated event labels.
- Liquidity is not present in the trend/range scalar rows.
- Delta persistence, pressure persistence, liquidity, and delta-reversal timing require richer transition records.
