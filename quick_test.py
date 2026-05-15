#!/usr/bin/env python3
"""快速测试 — 只测基本连通性"""
import sys, json
sys.path.insert(0, '.')

from binance_api import get_btc_trend, get_qualified_symbols, get_funding_rates, api_get

# 1. BTC
btc = get_btc_trend()
print(f"BTC: ${btc['price']:.0f} | 24h: {btc['change_pct']:+.1f}%")

# 2. 交易对数量
data = api_get("/fapi/v1/ticker/24hr")
print(f"合约交易对: {len(data) if data else 0}个")

# 3. 费率
fr = get_funding_rates()
sorted_fr = sorted(fr.items(), key=lambda x: x[1])
print(f"费率范围: {sorted_fr[0][1]:.4f}% ~ {sorted_fr[-1][1]:.4f}%")
print(f"极负费率(>0.08%): {[(s,f'{r:.4f}%') for s,r in sorted_fr if r < -0.08]}")
print(f"极正费率(>0.10%): {[(s,f'{r:.4f}%') for s,r in sorted_fr if r > 0.10]}")
