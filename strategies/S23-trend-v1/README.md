# S23 Trend-v1

Trend-v1 is a strict research strategy for catching the middle of a 2-7 day trend.

It is not connected to live trading yet. Use it as a backtest-only module until the
one-year and 1000-day results are stable.

## Logic

- Signal timeframe: closed 1h candles.
- Execution: next 1h open.
- Higher-timeframe filter: 4h EMA21/EMA55 plus 1d EMA20.
- BTC macro filter: avoid longs when BTC 4h is clearly bearish; avoid shorts when BTC 4h is clearly bullish.
- Entry style:
  - trend pullback reclaim near 1h EMA21
  - trend continuation breakout/breakdown of the previous 20h range
- Stop: ATR-based, capped before entry.
- Exit: stop/trailing stop, breakeven after 1.25R, max hold 168h.
- Costs: 0.04% fee and 0.05% slippage.

## Run

```bash
python3 strategies/S23-trend-v1/backtest_trend_v1.py --days 365 --label base
```

Output:

- `strategies/S23-trend-v1/data/strict_backtest_trend_v1_365d_base.json`

