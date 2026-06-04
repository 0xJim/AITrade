# AITrade Project Structure

This project has three layers:

1. Active runtime and comparable research: `trading-system/`
2. Historical strategy archive: `strategies/`
3. Documentation and normalized summary data: `docs/`, `data/`

## Active Runtime

Use `trading-system/` for anything that should affect current scans, testnet,
live safety, or new comparable backtests.

```text
trading-system/
├── README.md
├── universal_backtest.py
├── backtest_web.py
├── cron_scan.py
├── config.py
├── binance_api.py
├── notifier.py
├── review_db.py
├── backtesting/
├── configs/
├── data/
└── legacy_backtests/
```

Rules:

- New strategy variables belong in `trading-system/configs/*.json`.
- Shared backtest logic belongs in `trading-system/backtesting/`.
- Do not add new one-off backtest scripts to `trading-system/`.
- Generated reports and caches stay under `trading-system/data/` and are ignored by Git.

## Strategy Archive

Use `strategies/` for historical strategy versions and their original result
files. These folders are evidence and research history, not the active runtime.

```text
strategies/
├── S01-v5/
├── ...
└── S22-v11j/
```

Rules:

- Keep one `README.md` and original result files per strategy.
- Do not use archive scripts as the main comparison engine for new research.
- If an archived strategy idea is revived, port its variables into a JSON config
  and run it through `universal_backtest.py`.

## Health Check

Run the local health check before strategy work:

```bash
python3 scripts/health_check.py
```

Run Binance public-data verification when external network access is available:

```bash
python3 scripts/health_check.py --binance
```

The Binance check validates public K-line reachability only. It does not enable
live trading or place orders.
