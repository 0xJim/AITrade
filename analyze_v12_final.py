#!/usr/bin/env python3
"""v12 最终方案验证 — 折中：v8≥5不减半仓位而是调整"""
import json
from collections import defaultdict

with open("data/backtest_v11_result.json") as f:
    d = json.load(f)
trades = d["trades"]

def stats(ts, label=""):
    if not ts: return
    pnl = sum(t.get("pnl_usd",0) for t in ts)
    wins = sum(1 for t in ts if t.get("pnl_usd",0) > 0)
    wr = wins/len(ts)*100
    bal = 5000.0
    peak = bal
    max_dd = 0
    for t in ts:
        bal += t.get("pnl_usd",0)
        peak = max(peak, bal)
        dd = (peak - bal) / peak * 100
        max_dd = max(max_dd, dd)
    profit_months = 0
    total_months = 0
    monthly = defaultdict(list)
    for t in ts:
        monthly[t["entry_time"][:7]].append(t)
    for m in sorted(monthly.keys()):
        total_months += 1
        if sum(t.get("pnl_usd",0) for t in monthly[m]) > 0:
            profit_months += 1
    print(f"  {label}: {len(ts)}笔 PnL={pnl:+.0f} WR={wr:.0f}% DD={max_dd:.1f}% 月胜={profit_months}/{total_months}")
    for m in sorted(monthly.keys()):
        mpnl = sum(t.get("pnl_usd",0) for t in monthly[m])
        mw = sum(1 for t in monthly[m] if t.get("pnl_usd",0)>0)
        icon = "📈" if mpnl > 0 else "📉"
        print(f"    {icon} {m}: {len(monthly[m])}笔 {mpnl:+.0f} WR={mw/len(monthly[m])*100:.0f}%")

print("=== v12 方案对比 ===\n")

# 基线v11
stats(trades, "v11基线")

# 方案A: v8<5 硬过滤
stats([t for t in trades if (t.get("v8_score",0) or 0) < 5], "A: v8<5硬过滤")

# 方案B: v8<5 + 做空SL>=7%过滤
kept = [t for t in trades if (t.get("v8_score",0) or 0) < 5]
kept = [t for t in kept if not (t["direction"]=="short" and t.get("signal_sl_pct",0.05)*100 >= 7)]
stats(kept, "B: v8<5+做空SL>=7%")

# 方案C: v8>=5做多减半仓位
ts = []
for t in trades:
    nt = dict(t)
    v8 = t.get("v8_score",0) or 0
    if v8 >= 5 and t["direction"] == "long":
        nt["pnl_usd"] = t.get("pnl_usd",0) * 0.5
    ts.append(nt)
stats(ts, "C: v8>=5做多减半")

# 方案D: v8>=5全部减半仓位
ts = []
for t in trades:
    nt = dict(t)
    v8 = t.get("v8_score",0) or 0
    if v8 >= 5:
        nt["pnl_usd"] = t.get("pnl_usd",0) * 0.5
    ts.append(nt)
stats(ts, "D: v8>=5全部减半")

# 方案E: v8>=5减半 + 做空SL>=7%过滤
ts = []
for t in trades:
    if t["direction"]=="short" and t.get("signal_sl_pct",0.05)*100 >= 7:
        continue
    nt = dict(t)
    v8 = t.get("v8_score",0) or 0
    if v8 >= 5:
        nt["pnl_usd"] = t.get("pnl_usd",0) * 0.5
    ts.append(nt)
stats(ts, "E: v8>=5减半+做空SL>=7%")

# 方案F: v8=5减半(保留v8=6全仓)
ts = []
for t in trades:
    nt = dict(t)
    v8 = t.get("v8_score",0) or 0
    if v8 == 5:
        nt["pnl_usd"] = t.get("pnl_usd",0) * 0.5
    ts.append(nt)
stats(ts, "F: 仅v8=5减半")

print("\n=== 关键问题: v8=5 vs v8=6 ===")
for v in [5, 6]:
    ts = [t for t in trades if (t.get("v8_score",0) or 0) == v]
    pnl = sum(t.get("pnl_usd",0) for t in ts)
    wr = sum(1 for t in ts if t.get("pnl_usd",0)>0)/len(ts)*100
    longs = [t for t in ts if t["direction"]=="long"]
    shorts = [t for t in ts if t["direction"]=="short"]
    lp = sum(t.get("pnl_usd",0) for t in longs)
    sp = sum(t.get("pnl_usd",0) for t in shorts)
    print(f"  v8={v}: {len(ts)}笔 PnL={pnl:+.0f} WR={wr:.0f}% | 做多{len(longs)}笔{lp:+.0f} 做空{len(shorts)}笔{sp:+.0f}")
