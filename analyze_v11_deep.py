#!/usr/bin/env python3
"""v11 深度分析 — 找优化空间"""
import json
from collections import defaultdict

with open("data/backtest_v11_result.json") as f:
    d = json.load(f)
trades = d["trades"]

# v8=4 vs v8=5-6
v8_4 = [t for t in trades if (t.get("v8_score", t.get("v8_quality",0)) or 0) < 5]
v8_56 = [t for t in trades if 5 <= (t.get("v8_score", t.get("v8_quality",0)) or 0) < 7]

for label, ts in [("v8=4", v8_4), ("v8=5-6", v8_56)]:
    longs = [t for t in ts if t["direction"]=="long"]
    shorts = [t for t in ts if t["direction"]=="short"]
    long_pnl = sum(t.get("pnl_usd",0) for t in longs)
    short_pnl = sum(t.get("pnl_usd",0) for t in shorts)
    long_wr = sum(1 for t in longs if t.get("pnl_usd",0)>0)/len(longs)*100 if longs else 0
    short_wr = sum(1 for t in shorts if t.get("pnl_usd",0)>0)/len(shorts)*100 if shorts else 0
    
    monthly = defaultdict(list)
    for t in ts:
        monthly[t["entry_time"][:7]].append(t)
    
    print(f"--- {label}: {len(ts)}笔 ---")
    print(f"  做多: {len(longs)}笔 WR={long_wr:.0f}% PnL={long_pnl:+.0f}")
    print(f"  做空: {len(shorts)}笔 WR={short_wr:.0f}% PnL={short_pnl:+.0f}")
    for m in sorted(monthly.keys()):
        mpnl = sum(t.get("pnl_usd",0) for t in monthly[m])
        mw = sum(1 for t in monthly[m] if t.get("pnl_usd",0)>0)
        print(f"  {m}: {len(monthly[m])}笔 {mpnl:+.0f} WR={mw/len(monthly[m])*100:.0f}%")
    print()

# 做多SL 5-7% 按月
print("=== 做多SL 5-7% 按月 ===")
long_sl57 = [t for t in trades if t["direction"]=="long" and 5 <= t.get("signal_sl_pct",0.05)*100 < 7]
monthly = defaultdict(list)
for t in long_sl57:
    monthly[t["entry_time"][:7]].append(t)
for m in sorted(monthly.keys()):
    mpnl = sum(t.get("pnl_usd",0) for t in monthly[m])
    mw = sum(1 for t in monthly[m] if t.get("pnl_usd",0)>0)
    print(f"  {m}: {len(monthly[m])}笔 {mpnl:+.0f} WR={mw/len(monthly[m])*100:.0f}%")

# 做空SL>7%
print("\n=== 做空SL>=7% 按月 ===")
short_sl7 = [t for t in trades if t["direction"]=="short" and t.get("signal_sl_pct",0.05)*100 >= 7]
monthly = defaultdict(list)
for t in short_sl7:
    monthly[t["entry_time"][:7]].append(t)
for m in sorted(monthly.keys()):
    mpnl = sum(t.get("pnl_usd",0) for t in monthly[m])
    mw = sum(1 for t in monthly[m] if t.get("pnl_usd",0)>0)
    print(f"  {m}: {len(monthly[m])}笔 {mpnl:+.0f} WR={mw/len(monthly[m])*100:.0f}%")

# 3月亏损分析
print("\n=== 3月亏损分析 ===")
mar = [t for t in trades if t["entry_time"].startswith("2026-03")]
mar_long = [t for t in mar if t["direction"]=="long"]
mar_short = [t for t in mar if t["direction"]=="short"]
print(f"  做多: {len(mar_long)}笔 PnL={sum(t.get('pnl_usd',0) for t in mar_long):+.0f}")
print(f"  做空: {len(mar_short)}笔 PnL={sum(t.get('pnl_usd',0) for t in mar_short):+.0f}")
# 按v8
for label, ts2 in [("v8=4", [t for t in mar if (t.get('v8_score',0) or 0) < 5]), ("v8=5-6", [t for t in mar if 5 <= (t.get('v8_score',0) or 0)])]:
    if ts2:
        pnl = sum(t.get("pnl_usd",0) for t in ts2)
        wr = sum(1 for t in ts2 if t.get("pnl_usd",0)>0)/len(ts2)*100
        print(f"  {label}: {len(ts2)}笔 {pnl:+.0f} WR={wr:.0f}%")

# v8=5-6做空详细
print("\n=== v8=5-6做空(最大问题) ===")
v56_short = [t for t in trades if t["direction"]=="short" and 5 <= (t.get("v8_score",0) or 0)]
for t in v56_short:
    sym = t["symbol"]
    pnl = t.get("pnl_usd",0)
    sl = t.get("signal_sl_pct",0)*100
    mtf = t.get("mtf_agree",0)
    exit_r = t.get("exit_reason","")
    print(f"  {sym} SL={sl:.1f}% MTF={mtf} PnL={pnl:+.0f} exit={exit_r}")
