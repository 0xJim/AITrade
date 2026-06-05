#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
python3 strategies/S24-ignition/s24_paper_trader.py \
  --loop \
  --interval 60 \
  --hour-only \
  --dynamic \
  --sym-cap 5 \
  --margin-cap 300 \
  --exclude BUSDT,BILLUSDT,BNBUSDT,LINKUSDT,SAGAUSDT
