#!/usr/bin/env python3
"""快速信号扫描 — 只用批量API，避免逐个查询"""
import sys
sys.path.insert(0, '.')

from binance_api import get_qualified_symbols, get_funding_rates, get_funding_history, get_btc_trend
from config import EXTREME_NEG_FUNDING, EXTREME_POS_FUNDING

print("=" * 60)
print("快速信号扫描")
print("=" * 60)

# BTC环境
btc = get_btc_trend()
print(f"\nBTC: ${btc['price']:.0f} | 24h: {btc['change_pct']:+.1f}%")

# 获取行情和费率
tickers = get_qualified_symbols()
funding = get_funding_rates()
print(f"合格交易对: {len(tickers)}个")

# === 策略1: 极端负费率 ===
print("\n--- 策略1: 极端负费率(逼空做多) ---")
neg_signals = []
for t in tickers:
    sym = t["symbol"]
    fr = funding.get(sym, 0)
    if fr < EXTREME_NEG_FUNDING:
        neg_signals.append((sym, fr, float(t.get("priceChangePercent", 0))))

neg_signals.sort(key=lambda x: x[1])
for sym, fr, chg in neg_signals[:10]:
    print(f"  {sym:15s} 费率={fr:+.4f}% | 24h={chg:+.1f}%")

# === 策略2: 极端正费率 ===
print("\n--- 策略2: 极端正费率(拥挤做空) ---")
pos_signals = []
for t in tickers:
    sym = t["symbol"]
    fr = funding.get(sym, 0)
    if fr > EXTREME_POS_FUNDING:
        pos_signals.append((sym, fr, float(t.get("priceChangePercent", 0))))

pos_signals.sort(key=lambda x: x[1], reverse=True)
for sym, fr, chg in pos_signals[:10]:
    print(f"  {sym:15s} 费率={fr:+.4f}% | 24h={chg:+.1f}%")

# === 策略3: 暴跌反弹 ===
print("\n--- 策略3: 暴跌反弹(24h跌>25%) ---")
crash = []
for t in tickers:
    chg = float(t.get("priceChangePercent", 0))
    if chg < -25:
        crash.append((t["symbol"], chg, float(t.get("lastPrice", 0))))

crash.sort(key=lambda x: x[1])
for sym, chg, price in crash[:10]:
    print(f"  {sym:15s} 24h={chg:+.1f}% | 价格={price}")

# === 策略4: 暴涨做空 ===
print("\n--- 策略4: 暴涨做空(24h涨>40%) ---")
pump = []
for t in tickers:
    chg = float(t.get("priceChangePercent", 0))
    if chg > 40:
        pump.append((t["symbol"], chg, float(t.get("lastPrice", 0))))

pump.sort(key=lambda x: x[1], reverse=True)
for sym, chg, price in pump[:10]:
    print(f"  {sym:15s} 24h={chg:+.1f}% | 价格={price}")

# 汇总
total = len(neg_signals) + len(pos_signals) + len(crash) + len(pump)
print(f"\n{'=' * 60}")
print(f"信号汇总: {total}个")
print(f"  极端负费率: {len(neg_signals)}个")
print(f"  极端正费率: {len(pos_signals)}个") 
print(f"  暴跌反弹: {len(crash)}个")
print(f"  暴涨做空: {len(pump)}个")
