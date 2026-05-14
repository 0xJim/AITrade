# Server Run Guide: 1000U G60B + Spike-v13-P4

This branch is a runnable package for the 1000U simulation plan.

## Download

```bash
git clone -b codex/1000u-g60b-spike-package https://github.com/0xJim/AITrade.git
cd AITrade
```

If the server uses SSH:

```bash
git clone -b codex/1000u-g60b-spike-package git@github.com:0xJim/AITrade.git
cd AITrade
```

## Capital Split

```text
600U G60B
300U Spike-v13-P4
100U cash reserve
```

Do not enlarge the Spike single-trade margin. Spike-v13-P4 keeps `SPIKE_POSITION_PCT=0.04`.

## Run G60B Scanner Tick

```bash
cd trading-system
STRATEGY_PROFILE=G60B \
BINANCE_TESTNET=true \
ENABLE_LIVE_TRADING=false \
INITIAL_BALANCE=1000 \
CLOSED_15M_ANOMALY_ENABLED=true \
CLOSED_15M_ANOMALY_THRESHOLD_PCT=1.0 \
PYTHONUNBUFFERED=1 \
python3 cron_scan.py
```

## Run G60B Loop

```bash
cd trading-system
STRATEGY_PROFILE=G60B \
BINANCE_TESTNET=true \
ENABLE_LIVE_TRADING=false \
INITIAL_BALANCE=1000 \
CLOSED_15M_ANOMALY_ENABLED=true \
CLOSED_15M_ANOMALY_THRESHOLD_PCT=1.0 \
G60B_LOOP_INTERVAL=60 \
PYTHONUNBUFFERED=1 \
python3 run_g60b_loop.py
```

## Run Spike-v13-P4 Scanner

This scans the current live market for Spike-v13-P4 candidates. It does not switch to live trading by itself.

```bash
SPIKE_LONG_ONLY=true \
SPIKE_REQUIRE_EMA_UP=true \
SPIKE_THRESHOLD=0.01 \
SPIKE_MIN_ATR=0.005 \
SPIKE_MIN_RSI=50 \
SPIKE_POSITION_PCT=0.04 \
SPIKE_ALLOCATED_BALANCE=300 \
PYTHONUNBUFFERED=1 \
python3 strategies/S22-spike-v13/spike_scanner.py
```

## Recheck Strict Spike Backtest

```bash
python3 strategies/S22-spike-v13/backtest_spike_v13_strict.py \
  --days 365 \
  --position-pct 0.04 \
  --label pos4
```

Expected strict one-year result for Spike-v13-P4:

```text
PnL: +1060.85U
DD: 6.59%
Trades: 942
```

## Hard Stop Rules

```text
Account equity <= 950U: pause Spike-v13-P4
Account equity <= 900U: pause all strategies
Spike daily loss >= 25U: pause Spike for the day
Spike consecutive losses >= 3: pause Spike for 24h
G60B consecutive losses >= 5: pause G60B for 24h
No live trading unless exchange-level STOP_MARKET reduceOnly protection is confirmed
```

## Files Included

```text
docs/1000u-g60b-spike-v13-plan.md
docs/server-run-1000u-g60b-spike.md
trading-system/config.py
trading-system/cron_scan.py
trading-system/binance_api.py
trading-system/run_g60b_loop.py
trading-system/configs/backtest_v11j_g60_b.json
strategies/S22-spike-v13/
```
