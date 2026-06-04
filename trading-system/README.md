# Trading System

This is the active runtime area for AITrade.

## Current Entrypoints

- `universal_backtest.py`: canonical comparable backtest runner.
- `backtest_web.py`: local web dashboard for running and reviewing backtests.
- `cron_scan.py`: scanner / execution loop for the current v11j risk-control runtime.
- `config.py`: runtime config, Binance endpoint split, and live-trading safety gate.
- `binance_api.py`: Binance public data and signed execution adapter.
- `review_db.py`: local review database helper.
- `notifier.py`: notification formatting and delivery.

## Folder Rules

- `configs/`: one JSON file per strategy experiment.
- `backtesting/`: shared reusable backtest engine and data providers.
- `data/`: runtime-generated local state, reports, caches, and review database.
- `legacy_backtests/`: old research scripts kept only for reproduction.

New strategy research should start with a config file in `configs/`, not by
forking a Python backtest script.
