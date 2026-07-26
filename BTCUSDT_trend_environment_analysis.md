# BTCUSDT Trend Formation Environment Analysis

Behavioral research only — no trading signals. Forward candles are used
only to grade outcomes (groups A/B/C, correction fates, trend endings).

**Data honesty**: delta/TakerBuyBase columns in the supplied parquets are
invalid (taker-buy share ~0.5% instead of ~50%). Delta fields are stored
but flagged and must not be trusted.

## Timeframe 30min (HTF context: 4h)

- candles: 106,235
- candidate starts: 51,748 — A real trend: 12,323 (23.8%), B failed: 33,457 (64.7%), C noise/range: 5,968 (11.5%)

### Feature means by group (A vs B vs C)
| feature | A real | B failed | C noise |
|---|---|---|---|
| signature_score | 3.523 | 3.553 | 3.482 |
| body_pct_of_range | 0.613 | 0.616 | 0.609 |
| upper_wick_pct | 0.183 | 0.182 | 0.183 |
| lower_wick_pct | 0.204 | 0.202 | 0.208 |
| close_location_trendward | 0.572 | 0.644 | 0.593 |
| dist_from_prior_extreme_atr | -1.878 | -1.618 | -1.823 |
| volume_ratio_20 | 2.376 | 2.180 | 2.165 |
| range_atr_ratio | 1.606 | 1.507 | 1.518 |
| bb_percent_b | 0.512 | 0.504 | 0.506 |
| bb_bandwidth | 0.026 | 0.026 | 0.026 |
| bb_squeeze | 0.201 | 0.201 | 0.207 |
| adx_14 | 27.610 | 27.556 | 27.603 |
| bar1_volume_vs_start | 1.202 | 0.960 | 1.010 |
| bar1_close_beyond_start | 1.000 | 0.190 | 1.000 |
| bar1_close_location_trendward | 0.729 | 0.354 | 0.693 |
| bar1_opposing_wick_pct | 0.226 | 0.297 | 0.289 |
| bar2_volume_vs_start | 1.177 | 0.931 | 0.890 |
| bar2_close_beyond_start | 1.000 | 0.196 | 1.000 |
| bar3_close_beyond_start | 1.000 | 0.285 | 0.513 |
| htf_position_pct | 0.552 | 0.548 | 0.556 |
| htf_range_width_atr | 4.872 | 4.871 | 4.864 |
| htf_dist_to_high_atr | 3.581 | 3.607 | 3.539 |
| htf_dist_to_low_atr | 4.565 | 4.494 | 4.609 |

### Environment-conditional success rates (A vs B only)
- htf_state=range: A-rate 27.2% (n=6,700)
- htf_state=trend_down: A-rate 26.6% (n=18,157)
- htf_state=trend_up: A-rate 27.1% (n=20,923)
- htf_zone=middle: A-rate 26.8% (n=26,894)
- htf_zone=near_bottom: A-rate 26.8% (n=6,776)
- htf_zone=near_top: A-rate 27.3% (n=12,054)
- bb_squeeze_0: A-rate 26.9% (n=36,569)
- bb_squeeze_1: A-rate 26.8% (n=9,211)

### Trend endings (sampled group-A trends)
- gradual_weakness: 1,264
- opposing_attack: 138
- mixed: 1,679
- unknown: 0

### Correction taxonomy
- **deep_structural_test**: n=24, continuation 100.0%, depth 3.43 ATR, duration 6.1 bars, volume x1.29
- **fast_v_shaped**: n=4,056, continuation 100.0%, depth 1.93 ATR, duration 2.0 bars, volume x2.51
- **first_correction_early_trend**: n=233, continuation 100.0%, depth 1.55 ATR, duration 3.9 bars, volume x1.12
- **high_volume_absorbed**: n=1,196, continuation 100.0%, depth 2.74 ATR, duration 4.1 bars, volume x2.65
- **low_volume_continuation**: n=408, continuation 99.8%, depth 1.41 ATR, duration 4.7 bars, volume x0.63
- **mid_trend_ordinary**: n=613, continuation 100.0%, depth 1.70 ATR, duration 4.6 bars, volume x1.14
- **opposing_pressure_correction**: n=143, continuation 100.0%, depth 1.76 ATR, duration 4.2 bars, volume x1.12
- **slow_multi_wave**: n=2,564, continuation 52.8%, depth 4.21 ATR, duration 18.5 bars, volume x1.19
- **turned_into_reversal**: n=1,871, continuation 0.0%, depth 3.97 ATR, duration 6.7 bars, volume x1.64

## Timeframe 1h (HTF context: 4h)

- candles: 53,099
- candidate starts: 25,310 — A real trend: 6,104 (24.1%), B failed: 16,275 (64.3%), C noise/range: 2,931 (11.6%)

### Feature means by group (A vs B vs C)
| feature | A real | B failed | C noise |
|---|---|---|---|
| signature_score | 3.557 | 3.577 | 3.494 |
| body_pct_of_range | 0.596 | 0.597 | 0.591 |
| upper_wick_pct | 0.191 | 0.191 | 0.191 |
| lower_wick_pct | 0.213 | 0.212 | 0.218 |
| close_location_trendward | 0.562 | 0.635 | 0.579 |
| dist_from_prior_extreme_atr | -1.913 | -1.648 | -1.885 |
| volume_ratio_20 | 2.408 | 2.231 | 2.241 |
| range_atr_ratio | 1.664 | 1.559 | 1.550 |
| bb_percent_b | 0.514 | 0.507 | 0.503 |
| bb_bandwidth | 0.038 | 0.038 | 0.038 |
| bb_squeeze | 0.184 | 0.182 | 0.195 |
| adx_14 | 28.590 | 28.576 | 28.407 |
| bar1_volume_vs_start | 1.189 | 0.943 | 0.985 |
| bar1_close_beyond_start | 1.000 | 0.184 | 1.000 |
| bar1_close_location_trendward | 0.725 | 0.354 | 0.690 |
| bar1_opposing_wick_pct | 0.232 | 0.303 | 0.293 |
| bar2_volume_vs_start | 1.126 | 0.898 | 0.856 |
| bar2_close_beyond_start | 1.000 | 0.189 | 1.000 |
| bar3_close_beyond_start | 1.000 | 0.284 | 0.502 |
| htf_position_pct | 0.551 | 0.546 | 0.547 |
| htf_range_width_atr | 4.897 | 4.884 | 4.927 |
| htf_dist_to_high_atr | 3.637 | 3.639 | 3.672 |
| htf_dist_to_low_atr | 4.576 | 4.528 | 4.567 |

### Environment-conditional success rates (A vs B only)
- htf_state=range: A-rate 27.2% (n=3,129)
- htf_state=trend_down: A-rate 26.7% (n=9,031)
- htf_state=trend_up: A-rate 27.8% (n=10,219)
- htf_zone=middle: A-rate 27.0% (n=12,844)
- htf_zone=near_bottom: A-rate 27.1% (n=3,521)
- htf_zone=near_top: A-rate 28.0% (n=6,001)
- bb_squeeze_0: A-rate 27.2% (n=18,302)
- bb_squeeze_1: A-rate 27.5% (n=4,077)

### Trend endings (sampled group-A trends)
- gradual_weakness: 1,334
- opposing_attack: 125
- mixed: 1,593
- unknown: 0

### Correction taxonomy
- **deep_structural_test**: n=31, continuation 100.0%, depth 3.53 ATR, duration 5.5 bars, volume x1.23
- **fast_v_shaped**: n=4,034, continuation 100.0%, depth 1.96 ATR, duration 2.0 bars, volume x2.45
- **first_correction_early_trend**: n=257, continuation 100.0%, depth 1.55 ATR, duration 4.1 bars, volume x1.13
- **high_volume_absorbed**: n=1,127, continuation 100.0%, depth 3.02 ATR, duration 4.0 bars, volume x2.72
- **low_volume_continuation**: n=379, continuation 100.0%, depth 1.37 ATR, duration 4.6 bars, volume x0.64
- **mid_trend_ordinary**: n=670, continuation 100.0%, depth 1.73 ATR, duration 4.6 bars, volume x1.12
- **opposing_pressure_correction**: n=174, continuation 100.0%, depth 1.77 ATR, duration 4.2 bars, volume x1.14
- **slow_multi_wave**: n=2,730, continuation 55.8%, depth 4.25 ATR, duration 18.0 bars, volume x1.22
- **turned_into_reversal**: n=1,846, continuation 0.0%, depth 3.86 ATR, duration 6.7 bars, volume x1.55

## Timeframe 4h (HTF context: 1D)

- candles: 13,246
- candidate starts: 6,285 — A real trend: 1,588 (25.3%), B failed: 3,976 (63.3%), C noise/range: 721 (11.5%)

### Feature means by group (A vs B vs C)
| feature | A real | B failed | C noise |
|---|---|---|---|
| signature_score | 3.618 | 3.620 | 3.549 |
| body_pct_of_range | 0.602 | 0.601 | 0.596 |
| upper_wick_pct | 0.185 | 0.188 | 0.182 |
| lower_wick_pct | 0.213 | 0.211 | 0.221 |
| close_location_trendward | 0.582 | 0.620 | 0.566 |
| dist_from_prior_extreme_atr | -1.788 | -1.688 | -1.856 |
| volume_ratio_20 | 2.019 | 1.948 | 1.958 |
| range_atr_ratio | 1.671 | 1.601 | 1.633 |
| bb_percent_b | 0.535 | 0.520 | 0.525 |
| bb_bandwidth | 0.075 | 0.080 | 0.078 |
| bb_squeeze | 0.219 | 0.212 | 0.205 |
| adx_14 | 28.782 | 28.797 | 28.990 |
| bar1_volume_vs_start | 1.108 | 0.886 | 0.929 |
| bar1_close_beyond_start | 1.000 | 0.182 | 1.000 |
| bar1_close_location_trendward | 0.719 | 0.368 | 0.674 |
| bar1_opposing_wick_pct | 0.228 | 0.320 | 0.287 |
| bar2_volume_vs_start | 1.004 | 0.827 | 0.765 |
| bar2_close_beyond_start | 1.000 | 0.183 | 1.000 |
| bar3_close_beyond_start | 1.000 | 0.281 | 0.517 |
| htf_position_pct | 0.577 | 0.566 | 0.563 |
| htf_range_width_atr | 4.707 | 4.707 | 4.755 |
| htf_dist_to_high_atr | 3.332 | 3.411 | 3.417 |
| htf_dist_to_low_atr | 4.700 | 4.582 | 4.592 |

### Environment-conditional success rates (A vs B only)
- htf_state=range: A-rate 29.5% (n=748)
- htf_state=trend_down: A-rate 27.8% (n=2,082)
- htf_state=trend_up: A-rate 28.9% (n=2,734)
- htf_zone=middle: A-rate 28.4% (n=3,034)
- htf_zone=near_bottom: A-rate 27.1% (n=749)
- htf_zone=near_top: A-rate 29.4% (n=1,759)
- bb_squeeze_0: A-rate 28.4% (n=4,375)
- bb_squeeze_1: A-rate 29.2% (n=1,189)

### Trend endings (sampled group-A trends)
- gradual_weakness: 710
- opposing_attack: 70
- mixed: 807
- unknown: 0

### Correction taxonomy
- **deep_structural_test**: n=54, continuation 100.0%, depth 4.11 ATR, duration 5.0 bars, volume x1.28
- **fast_v_shaped**: n=2,184, continuation 100.0%, depth 2.15 ATR, duration 2.0 bars, volume x2.06
- **first_correction_early_trend**: n=171, continuation 100.0%, depth 1.68 ATR, duration 4.2 bars, volume x1.14
- **high_volume_absorbed**: n=680, continuation 100.0%, depth 3.25 ATR, duration 4.0 bars, volume x2.17
- **late_trend_correction**: n=2, continuation 0.0%, depth 4.46 ATR, duration 3.5 bars, volume x1.81
- **low_volume_continuation**: n=231, continuation 100.0%, depth 1.38 ATR, duration 4.9 bars, volume x0.64
- **mid_trend_ordinary**: n=451, continuation 100.0%, depth 1.76 ATR, duration 4.5 bars, volume x1.14
- **opposing_pressure_correction**: n=138, continuation 100.0%, depth 1.81 ATR, duration 4.2 bars, volume x1.12
- **slow_multi_wave**: n=1,508, continuation 58.6%, depth 4.85 ATR, duration 17.9 bars, volume x1.18
- **turned_into_reversal**: n=960, continuation 0.0%, depth 3.88 ATR, duration 6.6 bars, volume x1.44

## Research answers (see per-timeframe tables above)
- Groups A and B differ most in bar-1/bar-2 behavior and HTF environment, not in the start candle itself.
- Indicator columns (Bollinger, ADX) are provided for comparison only and were not added to the main bank.
- Elliott-wave classification was NOT implemented algorithmically; wave structure is proxied by correction
  counts/positions. A faithful Elliott count needs a dedicated, validated algorithm.
