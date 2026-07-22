# BTCUSDT Trend-Bar Reliability Study

Question: how many bars that look like trend starts are actually fake?
The signature is causal; forward bars are used only to grade outcomes. No signals are produced.

## Definition
- Signature features: structural_breakout_20, range_expansion, volume_expansion, strong_body, directional_close, slope_aligned
- Real trend: +4.0 ATR reached before -2.0 ATR within 48 bars

Total candles: **53,099** — trend-like events (score >= 3): **25,314**

## Real-trend rate by signature score
| score | events | real | fake | unresolved | real % (of decided) |
|---|---|---|---|---|---|
| 3 | 16,031 | 4,354 | 9,524 | 2,153 | 31.4% |
| 4 | 5,969 | 1,653 | 3,533 | 783 | 31.9% |
| 5 | 1,666 | 484 | 995 | 187 | 32.7% |
| 6 | 1,648 | 510 | 988 | 150 | 34.0% |

## Real-trend rate by individual feature (among trend-like events)
| feature | real | fake | real % (of decided) |
|---|---|---|---|
| structural_breakout_20 | 1,258 | 2,522 | 33.3% |
| range_expansion | 4,924 | 10,416 | 32.1% |
| volume_expansion | 5,606 | 11,847 | 32.1% |
| strong_body | 4,289 | 9,379 | 31.4% |
| directional_close | 3,968 | 8,558 | 31.7% |
| slope_aligned | 5,109 | 10,885 | 31.9% |

## Honest conclusion
No signature level reaches anywhere near 100% reliability; a trend-looking bar is a probabilistic clue, not proof of a trend. The correct use of these numbers is as base rates for position sizing and confirmation logic, not as a certainty filter.
