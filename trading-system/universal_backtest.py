#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path

from backtesting.core import (
    BinanceFuturesDataProvider,
    SampleDataProvider,
    TZ_UTC8,
    UniversalBacktester,
    load_strategy,
    save_report,
)


BASE_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the universal AITrade backtester.")
    parser.add_argument("--strategy", default=str(BASE_DIR / "configs" / "backtest_v11j.json"))
    parser.add_argument("--source", choices=["binance", "sample"], default="binance")
    parser.add_argument("--symbols", default="", help="Comma-separated symbols. Empty means use ranked market pool.")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--check-data", action="store_true", help="Check data provider health and exit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    provider = SampleDataProvider() if args.source == "sample" else BinanceFuturesDataProvider()

    # Data health check
    if args.check_data:
        if args.source == "sample":
            print("sample data provider ok")
            return 0
        # For binance, fetch 1 day of BTCUSDT 1h data
        end_ms = int(datetime.now(TZ_UTC8).timestamp() * 1000)
        start_ms = int((datetime.now(TZ_UTC8) - timedelta(days=1)).timestamp() * 1000)
        data = provider.klines("BTCUSDT", "1h", start_ms, end_ms)
        print(f"binance data provider ok: BTCUSDT 1h candles={len(data)}")
        return 0

    strategy = load_strategy(Path(args.strategy))
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or None
    report = UniversalBacktester(strategy, provider).run(symbols=symbols, days=args.days, interval=args.interval)
    summary = report["summary"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if not args.no_save:
        path = save_report(report)
        print(f"\nSaved report: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
