# BTCUSDT Trend Follow-Through and Correction Fate Study

Relaxed standard requested by the user: how many trend-looking bars moved
more than 4 bars in their direction, and what happened at the corrections
that followed (continuation vs reversal vs drifting into a range).

## Definitions
- **min_score**: 3
- **follow_through**: closes stay beyond the signal-bar close for more than 4 bars
- **correction**: pullback >= 1 ATR from the running extreme
- **fates**: {'continuation': 'new extreme beyond the pre-correction extreme within 24 bars', 'reversal': "price breaks the signal bar's opposite side before a new extreme", 'range': 'neither within 24 bars'}
- **note**: Forward data used only for grading; no signals produced.

Trend-like events: **25,313** — followed through >4 bars: **6,105 (24.1%)**

## Follow-through (>4 bars) by signature score
| score | events | >4 bars | % |
|---|---|---|---|
| 3 | 16,031 | 3,890 | 24.3% |
| 4 | 5,968 | 1,429 | 23.9% |
| 5 | 1,666 | 385 | 23.1% |
| 6 | 1,648 | 401 | 24.3% |

## Correction fates (sampled 6,105 followed-through moves)
- continuation: 16,376
- reversal: 3,696
- range: 2,409

## At which correction did the trend die?
| correction # | reversal | range |
|---|---|---|
| 1 | 1,544 | 108 |
| 2 | 974 | 270 |
| 3 | 602 | 367 |
| 4 | 287 | 346 |
| 5 | 142 | 265 |
| 6 | 60 | 238 |
| 7 | 37 | 190 |
| 8 | 17 | 130 |
| 9 | 18 | 118 |
| 10 | 6 | 91 |
| 11 | 4 | 60 |
| 12 | 2 | 57 |
| 13 | 1 | 48 |
| 14 | 0 | 26 |
| 15 | 0 | 30 |
| 16 | 0 | 16 |
| 17 | 1 | 8 |
| 18 | 1 | 8 |
| 19 | 0 | 8 |
| 20 | 0 | 10 |
| 21 | 0 | 5 |
| 22 | 0 | 5 |
| 23 | 0 | 2 |
| 24 | 0 | 2 |
| 25 | 0 | 1 |

## Correction profiles (what distinguishes killers from survivors)

### survived_continuation (n=16,376)
- duration_bars: mean 4.818, median 3.000
- depth_atr: mean 2.289, median 1.800
- depth_pct_of_move: mean 68.006, median 55.980
- volume_vs_pre_move: mean 2.003, median 1.547
- opposing_body_share: mean 0.317, median 0.385

### killed_reversal (n=3,696)
- duration_bars: mean 6.717, median 5.000
- depth_atr: mean 3.861, median 3.170
- depth_pct_of_move: mean 173.738, median 155.730
- volume_vs_pre_move: mean 1.549, median 1.251
- opposing_body_share: mean 0.533, median 0.619

### faded_into_range (n=2,409)
- duration_bars: mean 24.000, median 24.000
- depth_atr: mean 5.660, median 4.432
- depth_pct_of_move: mean 78.459, median 74.529
- volume_vs_pre_move: mean 1.173, median 0.920
- opposing_body_share: mean 0.536, median 0.542

## Honest notes
- These are descriptive statistics; nothing here is a signal or a guarantee.
- Correction fates are graded with future data (evaluation only).
- Delta/taker-buy columns in the source parquet are invalid and were not used.
