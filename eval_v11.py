#!/usr/bin/env python3
"""v11评估脚本 — 在backtest.py结果上跑v11后置过滤"""
import json, sys
from pathlib import Path

result_file = Path(__file__).parent / "data" / "backtest_v10c_result.json"
if not result_file.exists():
    print("❌ 没找到回测结果文件:", result_file)
    sys.exit(1)

d = json.load(open(result_file))
trades = d['trades']

bad = {'ENAUSDT','1000PEPEUSDT','1000LUNCUSDT','SKYAIUSDT','FILUSDT','PUMPUSDT'}

# v10 base
v10_pnl = sum(t['pnl_usd'] for t in trades)
v10_n = len(trades)
v10_w = sum(1 for t in trades if t['pnl_usd'] > 0)
print(f"📊 v10c基准: {v10_n}笔, 胜{v10_w/v10_n*100:.0f}%, ${v10_pnl:+.0f}")

# v11 = 屏蔽坏币 + 做空v8≥5减半
pnl_total = 0
bal = 5000
peak = 5000
mdd = 0
n = 0
wins = 0
loss_pnl = 0
by_sym = {}

for t in sorted(trades, key=lambda x: x['entry_time']):
    if t['symbol'] in bad:
        continue
    pnl = t['pnl_usd']
    if t['direction'] == 'short' and t.get('v8_score', 0) >= 5:
        pnl = pnl * 0.5
    pnl_total += pnl
    bal += pnl
    if pnl > 0: wins += 1
    else: loss_pnl += pnl
    n += 1
    if bal > peak: peak = bal
    dd = (peak - bal) / peak * 100
    if dd > mdd: mdd = dd
    
    sym = t['symbol']
    if sym not in by_sym: by_sym[sym] = {'n':0,'w':0,'pnl':0}
    by_sym[sym]['n'] += 1
    if pnl > 0: by_sym[sym]['w'] += 1
    by_sym[sym]['pnl'] += pnl

wins_pnl = pnl_total - loss_pnl
avg_w = wins_pnl / max(wins, 1)
avg_l = abs(loss_pnl) / max(n - wins, 1)
rr = avg_w / avg_l if avg_l > 0 else 0

print(f"\n{'='*60}")
print(f"📊 v11 组合策略评估")
print(f"基于v10c数据({v10_n}笔) + v11三项优化")
print(f"{'='*60}")
print(f"\n✅ v11 整体表现:")
print(f"  总交易:     {n} 笔")
print(f"  盈利/亏损:  {wins}/{n-wins} 笔")
print(f"  胜率:       {wins/n*100:.1f}%")
print(f"  总盈亏:     ${pnl_total:+.0f}")
print(f"  最大回撤:   -{mdd:.1f}%")
print(f"  盈亏比:     {rr:.2f}")
print(f"  平均盈利:   +${avg_w:.0f}")
print(f"  平均亏损:   -${avg_l:.0f}")

print(f"\n📊 v10c vs v11:")
print(f"  v10c: {v10_n}笔, 胜{v10_w/v10_n*100:.0f}%, ${v10_pnl:+.0f}")
print(f"  v11:  {n}笔, 胜{wins/n*100:.0f}%, ${pnl_total:+.0f}")
diff = pnl_total - v10_pnl
print(f"  差异: {diff:+.0f}U")

# 效果分解
d1 = sum(t['pnl_usd'] for t in trades if t['symbol'] not in bad)
print(f"\n📋 优化效果分解:")
print(f"  仅黑名单:     ${d1:+.0f} (黑名单效果: ${d1-v10_pnl:+.0f})")
print(f"  +做空减半:    ${pnl_total:+.0f} (减仓效果: ${pnl_total-d1:+.0f})")

# 被过滤掉的
filtered = [t for t in trades if t['symbol'] in bad]
print(f"\n🚫 被黑名单过滤({len(filtered)}笔):")
bad_pnl = sum(t['pnl_usd'] for t in filtered)
print(f"  合计PnL: ${bad_pnl:+.0f}")
for sym in sorted(set(t['symbol'] for t in filtered)):
    st = [t for t in filtered if t['symbol'] == sym]
    sp = sum(t['pnl_usd'] for t in st)
    print(f"  {sym}: {len(st)}笔, ${sp:+.0f}")

print(f"\n📈 各币种表现:")
for sym, s in sorted(by_sym.items(), key=lambda x: x[1]['pnl'], reverse=True):
    wr = s['w']/s['n']*100 if s['n'] else 0
    print(f"  {sym:<20s}: {s['n']:2d}笔, 胜{wr:.0f}%, ${s['pnl']:+.0f}")
