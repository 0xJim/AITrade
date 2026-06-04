# S23 Ignition-Pro v1

Ignition-Pro v1 is a launch-start detection strategy.

It tries to identify this sequence:

`compression -> money inflow -> closed breakout -> confirmation -> momentum exit`

This module is backtest-only. Do not connect it to live trading until strict
one-year and 1000-day results are acceptable.

## Strict Backtest Rules

- Signal candle must be closed.
- Entry is the next 1h open.
- Fee: 0.04%.
- Slippage: 0.05%.
- Initial balance: 1000U.
- Max positions: 3.
- Stop risk is capped before entry.

## Score Model

The signal score is capped at 100:

- Setup/compression: 20
- Money inflow: 20
- Breakout ignition: 25
- Multi-timeframe confirmation: 15
- Risk position: 10
- BTC environment contributes through the confirmation and hard filters.

Minimum score is 78.

## Run

```bash
python3 strategies/S23-ignition-pro-v1/backtest_ignition_pro_v1.py --days 365 --label base
```

