#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backtesting.core import (
    BinanceFuturesDataProvider,
    SampleDataProvider,
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    strategy = load_strategy(Path(args.strategy))
    provider = SampleDataProvider() if args.source == "sample" else BinanceFuturesDataProvider()
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
