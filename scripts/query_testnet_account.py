#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "trading-system"))

from binance_api import get_balance, get_positions, signed_get  # noqa: E402


def redact_balance_rows(rows):
    if not isinstance(rows, list):
        return rows
    out = []
    for row in rows:
        if row.get("asset") != "USDT":
            continue
        out.append({
            "asset": row.get("asset"),
            "balance": row.get("balance"),
            "availableBalance": row.get("availableBalance"),
            "crossWalletBalance": row.get("crossWalletBalance"),
            "crossUnPnl": row.get("crossUnPnl"),
            "updateTime": row.get("updateTime"),
        })
    return out


def active_positions(rows):
    if not isinstance(rows, list):
        return rows
    out = []
    for row in rows:
        try:
            amt = float(row.get("positionAmt", 0) or 0)
        except Exception:
            amt = 0.0
        if amt == 0:
            continue
        out.append({
            "symbol": row.get("symbol"),
            "positionAmt": row.get("positionAmt"),
            "entryPrice": row.get("entryPrice"),
            "breakEvenPrice": row.get("breakEvenPrice"),
            "markPrice": row.get("markPrice"),
            "unRealizedProfit": row.get("unRealizedProfit"),
            "liquidationPrice": row.get("liquidationPrice"),
            "leverage": row.get("leverage"),
            "marginType": row.get("marginType"),
            "positionSide": row.get("positionSide"),
        })
    return out


def open_orders(rows):
    if not isinstance(rows, list):
        return rows
    out = []
    for row in rows:
        out.append({
            "symbol": row.get("symbol"),
            "orderId": row.get("orderId"),
            "type": row.get("type"),
            "side": row.get("side"),
            "origQty": row.get("origQty"),
            "price": row.get("price"),
            "stopPrice": row.get("stopPrice"),
            "reduceOnly": row.get("reduceOnly"),
            "positionSide": row.get("positionSide"),
            "status": row.get("status"),
            "time": row.get("time"),
        })
    return out


def main():
    required = ["BINANCE_API_KEY", "BINANCE_API_SECRET"]
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise SystemExit(f"missing env: {', '.join(missing)}")

    payload = {
        "balance": redact_balance_rows(get_balance()),
        "positions": active_positions(get_positions()),
        "openOrders": open_orders(signed_get("/fapi/v1/openOrders", {})),
    }
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
