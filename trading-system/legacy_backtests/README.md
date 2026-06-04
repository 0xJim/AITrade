# Legacy Backtests

This folder keeps older one-off research backtest scripts for historical
reproduction only.

Use these current entrypoints for new work:

- CLI backtests: `trading-system/universal_backtest.py`
- Web dashboard: `trading-system/backtest_web.py`
- Strategy variables: `trading-system/configs/*.json`

Do not compare new strategy ideas by editing these legacy scripts. Copy or add a
JSON config under `trading-system/configs/` and run it through the universal
engine instead.
