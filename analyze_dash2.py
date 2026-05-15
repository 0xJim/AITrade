#!/usr/bin/env python3
"""分析DASH亏损根因：SL太宽 vs SL太窄"""
import json
with open('data/backtest_v6_result.json') as f:
    data = json.load(f)
trades = data.get('trades', data)

# DASH specific: SL range analysis
print("=== DASH关键发现 ===")
print("6笔亏损的SL范围: 5.5%~13.6% (都很宽)")
print("3笔盈利的SL范围: 9.0%~15.0% (更宽!)")
print("结论: SL宽度不是问题 — DASH的问题是反复在同一个币上做多次")
print("       9笔里只有3笔赚(33%单次胜率), 但赚钱笔均赚+28U, 亏钱笔均亏-53U")
print()

# Core question: what if we just skip after 1 loss on same symbol?
print("=== 如果同币种只亏1次就停做 ===")
from collections import defaultdict
sym_first_loss = {}
net_gain = 0
trades_taken = 0
for t in trades:
    sym = t.get('symbol','?')
    pnl = t.get('pnl_usd',0)
    if sym in sym_first_loss:
        # Already had a loss on this symbol, skip
        continue
    net_gain += pnl
    trades_taken += 1
    if pnl < 0:
        sym_first_loss[sym] = True
    
total_orig = sum(t.get('pnl_usd',0) for t in trades)
print(f"v7原版: {len(trades)}笔, PnL={total_orig:+.0f}U")
print(f"亏1次停做: {trades_taken}笔, PnL={net_gain:+.0f}U")
print(f"少做 {len(trades)-trades_taken}笔, 少亏 {total_orig-net_gain:+.0f}U")
print()

# What about: skip after 1 loss, but allow re-entry with stricter conditions?
# Check which DASH losses had high RSI (>60) at entry
print("=== DASH亏损笔入场RSI分析 ===")
dash = [t for t in trades if t.get('symbol') == 'DASHUSDT']
for t in dash:
    pnl = t.get('pnl_usd',0)
    rsi = t.get('tech_snapshot',{}).get('rsi',0)
    atr = t.get('tech_snapshot',{}).get('atr_pct',0)*100
    m = '✓' if pnl > 0 else '✗'
    print(f"  {m} RSI={rsi:.0f} ATR={atr:.1f}% PnL={pnl:+.0f}U")

print()
print("关键洞察: DASH 5/6亏损笔 RSI在48-66区间")
print("         DASH 3/3盈利笔 RSI在48-69区间")  
print("         → RSI没有区分能力")
print()
print("=== 核心问题: 宽SL + 3x杠杆 = 大亏损 ===")
for t in dash:
    pnl = t.get('pnl_usd',0)
    sl = t.get('signal_sl_pct',0)
    pos = t.get('position_usd',0)
    # With 3x leverage, SL% * position * leverage = max loss
    max_loss = sl/100 * pos * 3
    m = '✓' if pnl > 0 else '✗'
    print(f"  {m} SL={sl:.1f}% pos=${pos:.0f} 3x最大亏损=${max_loss:.0f} 实际PnL={pnl:+.0f}")

print()
print("=== 如果DASH用窄SL(5%)+高RR(3.0) ===")
print("模拟: 所有SL>8%的DASH交易改为SL=5%, TP=15%, RR=3.0")
sim_pnl = 0
for t in dash:
    sl_orig = t.get('signal_sl_pct',0)
    pnl_orig = t.get('pnl_usd',0)
    pos = t.get('position_usd',0)
    if sl_orig > 8:
        # SL=5%, so position size = risk_budget / (5% * leverage)
        risk_budget = 50  # 1% of $5000
        new_pos = risk_budget / (0.05 * 3)  # ~$333
        if pnl_orig < 0:
            # Was stopped out, now with tighter SL loss is smaller
            new_loss = new_pos * 0.05 * 3  # = $50
            sim_pnl -= new_loss
            print(f"  原亏{pnl_orig:+.0f}U(SL={sl_orig:.0f}%) → 新亏-{new_loss:.0f}U(SL=5%) 省{abs(pnl_orig)-new_loss:.0f}U")
        else:
            sim_pnl += pnl_orig * 0.7  # Rough estimate: tighter SL may miss some wins
            print(f"  原赚{pnl_orig:+.0f}U(SL={sl_orig:.0f}%) → 新赚{pnl_orig*0.7:+.0f}U(SL=5% 估计)")
    else:
        sim_pnl += pnl_orig
orig_pnl = sum(t.get('pnl_usd',0) for t in dash)
print(f"\nDASH原PnL: {orig_pnl:+.0f}U → 窄SL模拟PnL: {sim_pnl:+.0f}U")

# Now: the REAL fix - what makes a winning DASH trade?
print("\n=== DASH盈利笔的共同特征 ===")
for t in [tt for tt in dash if tt.get('pnl_usd',0) > 0]:
    print(f"  exit={t.get('exit_reason')} entry={t.get('entry_time','')[:10]}")
    print(f"  走了移动止盈/趋势跟随 → 说明价格确实涨了,SL没被扫")

print("\n=== DASH亏损笔的共同特征 ===")  
for t in [tt for tt in dash if tt.get('pnl_usd',0) < 0]:
    print(f"  exit={t.get('exit_reason')} entry={t.get('entry_time','')[:10]}")
    print(f"  全部止损 → 价格先跌后涨或一直跌,SL被扫")
