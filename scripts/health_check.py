#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRADING_SYSTEM = ROOT / "trading-system"


def run_step(name: str, cmd: list[str], env: dict[str, str] | None = None) -> None:
    print(f"[check] {name}")
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        env=merged_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output = result.stdout.strip()
    if output:
        print(output)
    if result.returncode != 0:
        raise SystemExit(f"[fail] {name} exited with {result.returncode}")
    print(f"[ok] {name}\n")


def python_eval(name: str, code: str, env: dict[str, str] | None = None) -> None:
    run_step(name, [sys.executable, "-c", code], env=env)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AITrade local health checks.")
    parser.add_argument("--binance", action="store_true", help="Also verify Binance public K-line data.")
    args = parser.parse_args()

    run_step("python compile", [sys.executable, "-m", "compileall", "-q", "."])
    python_eval(
        "review_db import",
        "import sys; sys.path.insert(0, 'trading-system'); import review_db; print('review ok')",
    )
    python_eval(
        "cron_scan import",
        "import sys; sys.path.insert(0, 'trading-system'); import cron_scan; print('cron ok')",
    )
    run_step(
        "sample data provider",
        [sys.executable, "trading-system/universal_backtest.py", "--source", "sample", "--check-data"],
    )
    run_step(
        "sample 30d BTC/ETH backtest",
        [
            sys.executable,
            "trading-system/universal_backtest.py",
            "--source",
            "sample",
            "--days",
            "30",
            "--end",
            "2026-05-12T10:00:00+08:00",
            "--symbols",
            "BTCUSDT,ETHUSDT",
            "--no-save",
        ],
    )
    python_eval(
        "config env override keeps live locked",
        (
            "import sys; sys.path.insert(0, 'trading-system'); import config; "
            "assert config.BINANCE_TESTNET is False; "
            "assert config.LIVE_TRADING_ENABLED is False; "
            "assert config.TRADE_FAPI == 'https://fapi.binance.com'; "
            "print(config.BINANCE_TESTNET, config.LIVE_TRADING_ENABLED, config.TRADE_FAPI)"
        ),
        env={"BINANCE_TESTNET": "false", "ENABLE_LIVE_TRADING": "false"},
    )
    python_eval(
        "web strategy path guard",
        (
            "import sys; sys.path.insert(0, 'trading-system'); "
            "from backtest_web import resolve_strategy_path; "
            "p=resolve_strategy_path('configs/backtest_v11j.json'); "
            "print(p.name)"
        ),
    )
    if args.binance:
        run_step(
            "Binance public K-line data",
            [sys.executable, "trading-system/universal_backtest.py", "--source", "binance", "--check-data"],
        )

    print("[done] AITrade health check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
