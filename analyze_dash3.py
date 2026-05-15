#!/usr/bin/env python3
"""关键分析：DASH盈利笔 vs 亏损笔 — 找出可区分特征"""
import json
with open('data/backtest_v6_result.json') as f:
    data = json.load(f)
trades = data.get('trades', data)

# 全局分析：连续做同一币种，第N笔的胜率
from collections import defaultdict
sym_trades = defaultdict(list)
for t in trades:
    sym_trades[t.get('symbol','?')].append(t)

print("=== 同币种第N笔交易胜率 ===")
for n in range(1, 10):
    pnls = []
    for sym, tlist in sym_trades.items():
        tlist_sorted = sorted(tlist, key=lambda x: x.get('entry_time',''))
        if n <= len(tlist_sorted):
            pnls.append(tlist_sorted[n-1].get('pnl_usd',0))
    if pnls:
        wins = sum(1 for p in pnls if p > 0)
        total_pnl = sum(pnls)
        print(f"  第{n}笔: {len(pnls)}笔, 胜率{wins/len(pnls)*100:.0f}%, PnL={total_pnl:+.0f}U")

print()
print("=== 关键发现：第1笔 vs 第2+笔 ===")
first_pnl = 0
first_wins = 0
first_count = 0
later_pnl = 0
later_wins = 0
later_count = 0
for sym, tlist in sym_trades.items():
    tlist_sorted = sorted(tlist, key=lambda x: x.get('entry_time',''))
    for i, t in enumerate(tlist_sorted):
        pnl = t.get('pnl_usd',0)
        if i == 0:
            first_pnl += pnl
            first_count += 1
            if pnl > 0: first_wins += 1
        else:
            later_pnl += pnl
            later_count += 1
            if pnl > 0: later_wins += 1

print(f"  第1笔: {first_count}笔, 胜率{first_wins/first_count*100:.0f}%, PnL={first_pnl:+.0f}U")
print(f"  第2+笔: {later_count}笔, 胜率{later_wins/later_count*100:.0f}%, PnL={later_pnl:+.0f}U")

print()
print("=== 如果只在同币种亏后加严(不是屏蔽) ===")
# Strategy: after 1 loss on same symbol, require higher env_score (6 instead of 4)
sim_pnl = 0
sim_count = 0
sym_loss_count = defaultdict(int)
for t in trades:
    sym = t.get('symbol','?')
    pnl = t.get('pnl_usd',0)
    env = t.get('env_score',0)
    
    losses_so_far = sym_loss_count[sym]
    if losses_so_far >= 1:
        # After 1 loss: require env_score >= 6 (very strong signal only)
        if env >= 6:
            sim_pnl += pnl
            sim_count += 1
        # else skip
    else:
        sim_pnl += pnl
        sim_count += 1
    
    if pnl < 0:
        sym_loss_count[sym] += 1

orig = sum(t.get('pnl_usd',0) for t in trades)
print(f"  v7原版: {len(trades)}笔, PnL={orig:+.0f}U")
print(f"  亏后加严(env>=6): {sim_count}笔, PnL={sim_pnl:+.0f}U")
print(f"  差异: {sim_pnl - orig:+.0f}U, 少做{len(trades)-sim_count}笔")

print()
print("=== 另一策略：亏后仓位减半 ===")
sim_pnl2 = 0
sim_count2 = 0
sym_loss_count2 = defaultdict(int)
for t in trades:
    sym = t.get('symbol','?')
    pnl = t.get('pnl_usd',0)
    
    losses_so_far = sym_loss_count2[sym]
    if losses_so_far >= 1:
        # Half position = half PnL impact
        sim_pnl2 += pnl * 0.5
        sim_count2 += 1
    else:
        sim_pnl2 += pnl
        sim_count2 += 1
    
    if pnl < 0:
        sym_loss_count2[sym] += 1

print(f"  亏后减半仓: {sim_count2}笔, PnL={sim_pnl2:+.0f}U")
print(f"  差异: {sim_pnl2 - orig:+.0f}U")

print()
print("=== 综合策略：亏后env>=6 + 仓位减半 ===")
sim_pnl3 = 0
sim_count3 = 0
sym_loss_count3 = defaultdict(int)
for t in trades:
    sym = t.get('symbol','?')
    pnl = t.get('pnl_usd',0)
    env = t.get('env_score',0)
    
    losses_so_far = sym_loss_count3[sym]
    if losses_so_far >= 1:
        if env >= 6:
            sim_pnl3 += pnl * 0.5
            sim_count3 += 1
    else:
        sim_pnl3 += pnl
        sim_count3 += 1
    
    if pnl < 0:
        sym_loss_count3[sym] += 1

print(f"  亏后env>=6+减半仓: {sim_count3}笔, PnL={sim_pnl3:+.0f}U")
print(f"  差异: {sim_pnl3 - orig:+.0f}U, 少做{len(trades)-sim_count3}笔")
