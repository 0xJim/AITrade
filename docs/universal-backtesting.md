# AITrade Universal Backtesting

## What Changed

The old runtime used one Binance base URL for both public market data and signed
trading requests. When `BINANCE_TESTNET=true`, the scanner could read testnet
market data while historical backtests used production futures data. That makes
the two environments diverge before the strategy logic even runs.

The system now separates the two endpoints:

- `DATA_FAPI`: public market data for scanner and backtests, default `https://fapi.binance.com`
- `TRADE_FAPI`: signed order/account endpoint, default testnet when `BINANCE_TESTNET=true`

This keeps testnet as an execution sandbox while strategy decisions use the same
production futures market data that backtests use.

## Command Line

Fast local smoke test with deterministic sample data:

```bash
python3 trading-system/universal_backtest.py --source sample --days 30 --symbols BTCUSDT,ETHUSDT
python3 trading-system/universal_backtest.py --source sample --days 30 --end 2026-05-12T10:00:00+08:00 --symbols BTCUSDT,ETHUSDT
```

Production Binance public data:

```bash
python3 trading-system/universal_backtest.py --source binance --days 90 --symbols BTCUSDT,ETHUSDT
```

Use the ranked market pool from the strategy config:

```bash
python3 trading-system/universal_backtest.py --source binance --days 90
```

Reports are written to:

```text
trading-system/data/backtest_reports/
```

Run the standard project health check before strategy research:

```bash
python3 scripts/health_check.py
```

When external network access is available, include Binance public K-line
verification:

```bash
python3 scripts/health_check.py --binance
```

## Web Dashboard

Start the local dashboard:

```bash
python3 trading-system/backtest_web.py --port 8765
```

Open:

```text
http://127.0.0.1:8765
```

The dashboard can run a backtest and display:

- summary metrics
- equity curve
- symbol-level performance
- recent trade details

## Strategy Variables

The default universal config is:

```text
trading-system/configs/backtest_v11j.json
```

Common variables to change:

- `risk.initial_balance`
- `risk.leverage`
- `risk.position_pct`
- `risk.max_positions`
- `risk.max_loss_per_trade`
- `signals.extreme_pos_funding`
- `signals.extreme_neg_funding`
- `signals.min_score`
- `signals.rr`
- `simulation.fee_rate`
- `simulation.slippage_rate`

Keep one JSON per strategy variant, then compare reports from the same engine.
That avoids each strategy carrying a separate backtest script with slightly
different assumptions.

Legacy one-off backtest scripts now live in:

```text
trading-system/legacy_backtests/
```

They are retained only for historical reproduction. New experiments should be
JSON configs under `trading-system/configs/`.

## Validation Rule

Before trusting any testnet/live result, compare these three layers:

1. Same market data source: scanner and backtest should both use `DATA_FAPI`.
2. Same execution cost model: fee and slippage should be explicit in JSON.
3. Same strategy variables: every experiment should name its JSON config.

If a result still diverges after that, inspect fill quality, stop-order behavior,
and exchange precision rather than tuning signal thresholds first.
