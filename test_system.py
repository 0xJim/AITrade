#!/usr/bin/env python3
"""测试交易系统连通性和信号扫描"""
import json
import sys
sys.path.insert(0, '.')

from binance_api import *
from signals import scan_all_signals

print("=" * 50)
print("交易系统测试")
print("=" * 50)

# 1. API连通性
print("\n[1] API连通性测试...")
btc = get_btc_trend()
print(f"  BTC: ${btc['price']:.0f} | 24h: {btc['change_pct']:+.1f}%")

# 2. 合格交易对
print("\n[2] 合格交易对...")
tickers = get_qualified_symbols()
print(f"  共 {len(tickers)} 个")

# 3. 费率
print("\n[3] 费率分布...")
fr = get_funding_rates()
sorted_fr = sorted(fr.items(), key=lambda x: x[1])
print("  最低5:", [(s, f'{r:.4f}%') for s, r in sorted_fr[:5]])
print("  最高5:", [(s, f'{r:.4f}%') for s, r in sorted_fr[-5:]])

# 4. 信号扫描
print("\n[4] 信号扫描...")
signals = scan_all_signals()
if signals:
    print(f"  发现 {len(signals)} 个信号:")
    for s in signals[:10]:
        print(f"    [{s['strength']}] {s['symbol']} {'做多' if s['direction']=='long' else '做空'} | {s['reason'][:50]}")
else:
    print("  当前无信号（市场平静）")

# 5. 恐惧贪婪
print("\n[5] 市场情绪...")
fgi = get_fear_greed()
print(f"  恐惧贪婪指数: {fgi}")

print("\n" + "=" * 50)
print("测试完成!")
