# Strategy Configs

Each JSON file in this folder is a strategy variant that can run through the
same universal backtest engine.

Default:

- `backtest_v11j.json`: current v11j universal config.

Current variants:

- `backtest_v11j_g60.json`: G60 profile, the best non-filtered high-return profile from the final one-year rerun.
- `backtest_v11j_g60_b.json`: G60B low-drawdown balanced validation profile, the current recommended testnet profile.
- `backtest_v11j_g60_s.json`: G60S strict low-drawdown validation profile, prioritizing DD and PF over trade count.
- `backtest_v11j_g60_o6.json`: G60O6 optimized validation profile, excluding ADA/LDO/SKYAI/SUI/TON/XRP based on the final one-year drag analysis.
- `backtest_v11j_g60_p.json`: G60P profit-focused validation profile, higher return with higher drawdown.
- `backtest_v11j_d60.json`: D60 comparison profile.
- `backtest_v11j_l7.json`: SL<=7% research profile.

Recommended naming:

- `backtest_<strategy-name>.json`
- `backtest_v11j_m50.json`
- `backtest_v11i_baseline.json`

Run a config:

```bash
python3 trading-system/universal_backtest.py --source sample --strategy trading-system/configs/backtest_v11j.json --days 30 --symbols BTCUSDT,ETHUSDT
```

Keep all experiment variables in JSON so strategy comparisons use the same data
provider, cost model, position accounting, and report format.
