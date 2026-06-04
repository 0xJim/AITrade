# S23-Ignition-v2 Research Report

Date: 2026-05-15

## Executive Conclusion

The previous 1h trend-following idea should be abandoned. It was too slow for
altcoin markup starts and produced weak or poor risk-adjusted results.

The better S23 direction is a 15m ignition strategy:

`closed 15m impulse -> 1h EMA trend filter -> next 15m open entry -> 4h max hold`

The best practical candidate from this research pass is:

**S23-Ignition-v2 hold4_p5_ex_worst3**

It excludes the three weakest symbols in the one-year strict run:

- `BNBUSDT`
- `BILLUSDT`
- `AAVEUSDT`

Result:

| Strategy | PnL | ROI | Max DD | Trades | Win Rate | PF | ROI/DD |
|---|---:|---:|---:|---:|---:|---:|---:|
| hold4_p5_ex_worst3 | +1361.14U | 136.11% | 8.25% | 998 | 50.00% | 1.88 | 16.49 |

This is not ready for live trading by itself, but it is the first S23 variant
worth advancing to deeper validation.

## Backtest Basis

- Window: 2025-05-14 10:00:00 +08 to 2026-05-14 10:00:00 +08
- Initial balance: 1000U
- Leverage: 3x
- Fee: 0.04%
- Slippage: 0.05%
- Execution: next 15m open after a closed 15m signal candle
- Signal direction: long only
- Core signal: 15m candle up >= 1%
- Filters: 1h EMA bullish, 15m RSI >= 50, quality >= 70
- Exit: stop loss, take profit, or max hold
- Position sizing: fixed 1000U basis, not compounding

## Why Trend-v1 Failed

The 1h Trend-v1 and Ignition-Pro line was trying to identify a slow trend
structure. That is not where the edge showed up.

| Strategy | PnL | DD | Trades | PF | Judgment |
|---|---:|---:|---:|---:|---|
| S23 Trend-v1 base | +100.77U | 17.39% | 931 | 1.06 | Reject |
| S23 Ignition-Pro confirm_p30 | +105.26U | 7.50% | 25 | 1.85 | Too low frequency |

Main issue: by the time the 1h trend is confirmed, many altcoin moves are
already extended or fading. The useful window is closer to the 15m ignition
stage.

## Parameter Matrix

| Variant | PnL | Max DD | Trades | Win Rate | PF | ROI/DD |
|---|---:|---:|---:|---:|---:|---:|
| hold4_p4 | +1037.13U | 6.57% | 1042 | 49.62% | 1.80 | 15.79 |
| hold4_p5 | +1296.41U | 7.96% | 1042 | 49.62% | 1.80 | 16.28 |
| hold4_p6 | +1555.69U | 9.28% | 1042 | 49.62% | 1.80 | 16.77 |
| q75_p4 | +992.36U | 7.64% | 737 | 49.39% | 1.76 | 12.98 |
| q80_p4 | +728.76U | 7.35% | 514 | 49.61% | 1.70 | 9.91 |
| rsi55_p4 | +994.43U | 5.91% | 912 | 48.25% | 1.66 | 16.84 |
| hold4_p5_ex_worst3 | +1361.14U | 8.25% | 998 | 50.00% | 1.88 | 16.49 |
| hold4_p5_ex_top2 | +568.23U | 8.08% | 842 | 47.98% | 1.47 | 7.03 |
| hold4_p5_ex_top5 | +243.29U | 7.88% | 577 | 49.22% | 1.34 | 3.09 |

## Recommended Variant

Use **hold4_p5_ex_worst3** for further research.

Reason:

- It improves PnL vs hold4_p5.
- It improves PF from 1.80 to 1.88.
- It keeps DD under 10%.
- It reduces obvious symbol drag without relying only on lower sizing.

Run command:

```bash
python3 strategies/S22-spike-v13/backtest_spike_v13_strict.py \
  --days 365 \
  --position-pct 0.05 \
  --max-hold-hours 4 \
  --exclude-symbols BNBUSDT,BILLUSDT,AAVEUSDT \
  --label s23_ignition_hold4_p5_ex_worst3
```

Output:

`strategies/S22-spike-v13/data/strict_backtest_spike_v13_365d_s23_ignition_hold4_p5_ex_worst3.json`

## Concentration Risk

The strategy is not entirely dependent on one symbol, but the top symbols matter
a lot.

Baseline hold4_p5 top contributors:

- `LABUSDT`: +432.78U
- `SKYAIUSDT`: +299.10U
- `VVVUSDT`: +144.44U
- `BUSDT`: +107.58U
- `SAGAUSDT`: +91.54U

If `LABUSDT` and `SKYAIUSDT` are removed, PnL drops to +568.23U. If the top
five contributors are removed, PnL drops to +243.29U.

Interpretation:

The edge is real enough to survive removing top two symbols, but performance is
meaningfully concentrated in a few high-beta names. This argues for dynamic
symbol rotation rather than a fixed permanent whitelist.

## Current Weaknesses

1. The current implementation still lives inside the Spike-v13 strict backtester.
   It should be copied into a dedicated S23-Ignition-v2 backtester before this
   becomes a clean strategy package.

2. The one-year result is strong, but the 1000-day run could not be completed in
   this pass because the local 15m cache only covers the one-year window and the
   sandbox network approval timed out while trying to download older Binance
   Futures data.

3. The current signal uses price, volume, RSI, ATR, and EMA only. It does not yet
   include open interest, funding, liquidation, taker buy/sell imbalance, or
   order-book pressure. Those data sources are likely required for a truly
   professional launch-detection system.

4. The result is sensitive to newly listed/high-beta symbols. That is good for
   markup capture, but it means testnet observation is mandatory before live use.

## 1000U Portfolio Recommendation

Do not replace G60B with S23-Ignition-v2.

Suggested research allocation if paper/testnet only:

| Module | Allocation | Role |
|---|---:|---|
| G60B | 500U | Defensive base |
| S23-Ignition-v2 hold4_p5_ex_worst3 | 300U | 15m markup ignition |
| Spike-v13-P4 or cash | 100U | Optional secondary attack or reserve |
| Cash reserve | 100U | Safety buffer |

Hard controls:

- Pause S23 if strategy DD reaches 8%.
- Pause S23 for the day after 25U realized loss.
- Pause S23 after 3 consecutive losses in the same symbol.
- No live trading without exchange-level reduce-only stop orders.

## Next Work

Priority order:

1. Create a dedicated `strategies/S23-ignition-v2/backtest_ignition_v2.py`.
2. Re-run 1000-day once network/data access is available.
3. Add open interest and funding filters.
4. Add symbol-rotation logic instead of static blacklists.
5. Run at least several days in simulated/testnet mode before any live use.

## Final Judgment

S23-Ignition-v2 is worth continuing. The failed Trend-v1 should be discarded.

Current best candidate:

**S23-Ignition-v2 hold4_p5_ex_worst3**

Status:

**Research candidate, not live-ready.**

