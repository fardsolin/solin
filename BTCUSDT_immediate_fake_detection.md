# BTCUSDT Immediate-Fake Detection Study

Per the user's redefinition: a trend-looking bar is completely fake only if it
dies at bar 1 or at most bar 2. Moves that reach the first correction are not
fake; there we look for early evidence of trend weakness / correction strength.

## Definitions
- **min_score**: 3
- **immediate_fake**: close returns through the signal-bar close on bar 1 or bar 2 (never reaches a correction)
- **hard_fake**: an immediate fake that also breaks the signal bar's opposite extreme within 5 bars
- **survived**: closes held beyond the signal close for at least 2 bars; graded at its first correction
- **note**: Bar-1 features are observable at bar-1 close (causal for a bar-2 decision). Forward data used only for grading.

Trend-like events: **25,312**

## How many are completely fake?
- died on bar 1: 13,282 (52.5%)
- died on bar 2: 2,994 (11.8%)
- **total immediate fakes: 64.3%**
- of those, hard fakes (opposite extreme broken within 5 bars): 70.7%
- survived at least 2 bars: 9,036 (35.7%)

## Bar-1 features: immediate fakes vs survivors
| feature | fakes mean | survivors mean |
|---|---|---|
| close_location | 0.354 | 0.714 |
| body_ratio | 0.399 | 0.462 |
| directional_body | 0.184 | 1.000 |
| made_new_extreme | 0.423 | 0.631 |
| closed_back_past_signal_mid | 0.575 | 0.199 |
| volume_vs_signal | 0.943 | 1.123 |
| range_atr | 1.223 | 1.442 |

## First-correction fates (sampled survivors)
- continuation: 2,768
- reversal: 1,678
- range: 72
- no correction within window: 0

## Early evidence inside the first TWO bars of the first correction
| feature | trend survived (mean) | trend killed (mean) |
|---|---|---|
| first2_depth_atr | 0.828 | 1.673 |
| first2_opposing_body_share | 0.237 | 0.431 |
| first2_volume_vs_pre_move | 1.798 | 1.443 |
| first2_close_location_trendward | 0.620 | 0.532 |
| first2_opposing_body_ratio | 0.468 | 0.422 |

## Honest notes
- Descriptive statistics only; no signals, no orders.
- Bar-1 features imply a decision made AFTER bar 1 closes (one-bar confirmation).
- Delta/taker-buy columns in the source parquet are invalid and were not used.
