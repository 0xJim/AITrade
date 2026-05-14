#!/usr/bin/env python3
"""Run cron_scan.py repeatedly with the G60B testnet/simulation profile."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
INTERVAL_SECONDS = int(os.environ.get("G60B_LOOP_INTERVAL", "60"))


def main() -> int:
    env = os.environ.copy()
    env.update({
        "STRATEGY_PROFILE": "G60B",
        "BINANCE_TESTNET": "true",
        "INITIAL_BALANCE": env.get("INITIAL_BALANCE", "1000"),
        "CLOSED_15M_ANOMALY_ENABLED": "true",
        "CLOSED_15M_ANOMALY_THRESHOLD_PCT": env.get("CLOSED_15M_ANOMALY_THRESHOLD_PCT", "1.0"),
        "PYTHONUNBUFFERED": "1",
    })

    while True:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"\n===== {stamp} G60B scan =====", flush=True)
        result = subprocess.run([sys.executable, "cron_scan.py"], cwd=BASE_DIR, env=env)
        print(f"===== exit={result.returncode}; sleeping {INTERVAL_SECONDS}s =====", flush=True)
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
