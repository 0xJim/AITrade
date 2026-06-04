# S23 Ignition-v2

S23 Ignition-v2 replaces the failed 1h Trend-v1 / Ignition-Pro idea.

Conclusion from the strict one-year rerun: for altcoin markup-start detection,
the useful signal is not slow 1h trend following. The useful signal is a 15m
closed-candle ignition with 1h trend filtering and fast exit.

## Best Current Variant

`hold4_p5`

Strict backtest basis:

- Window: 2025-05-14 10:00:00 +08 to 2026-05-14 10:00:00 +08
- Initial balance: 1000U
- Fee: 0.04%
- Slippage: 0.05%
- Entry: next 15m open after a closed 15m ignition signal
- Position: 5% fixed 1000U margin base
- Leverage: 3x
- Max hold: 4h
- Signal: 15m candle up >= 1%, 1h EMA bullish, RSI >= 50, quality >= 70

Result:

| Variant | PnL | ROI | DD | Trades | Win Rate | PF |
|---|---:|---:|---:|---:|---:|---:|
| hold4_p5 | +1296.41U | 129.64% | 7.96% | 1042 | 49.62% | 1.80 |

## Why This Replaces Trend-v1

The 1h Trend-v1 family was too late and too weak:

- S23 Trend-v1 base: +100.77U / DD 17.39%
- S23 Ignition-Pro confirm_p30: +105.26U / DD 7.50%, only 25 trades

The 15m ignition model catches the actual altcoin launch behavior and exits
quickly enough to avoid holding failed trends.

## Run

The current implementation reuses the strict Spike-v13 backtester with the
S23 Ignition-v2 parameters:

```bash
python3 strategies/S22-spike-v13/backtest_spike_v13_strict.py \
  --days 365 \
  --position-pct 0.05 \
  --max-hold-hours 4 \
  --label s23_ignition_hold4_p5
```

Output:

- `strategies/S22-spike-v13/data/strict_backtest_spike_v13_365d_s23_ignition_hold4_p5.json`

## Current Status

This is the first S23 candidate that is worth further work.

Do not put it on live trading yet. The next checks should be:

1. 1000-day strict rerun.
2. Same result with a dedicated `S23-ignition-v2` backtester file.
3. Add exchange-level stop protection before any live use.
4. Paper/testnet only until at least several days of live scanning confirms signal frequency and order behavior.

