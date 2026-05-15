#!/usr/bin/env python3
"""v12 全组合搜索 — 基于v11深度分析"""
import json
from collections import defaultdict
from itertools import combinations

with open("data/backtest_v11_result.json") as f:
    d = json.load(f)
trades = d["trades"]

def stats(ts):
    if not ts: return None
    pnl = sum(t.get("pnl_usd",0) for t in ts)
    wins = sum(1 for t in ts if t.get("pnl_usd",0) > 0)
    wr = wins/len(ts)*100
    # 回撤
    bal = 5000.0
    peak = bal
    max_dd = 0
    for t in ts:
        bal += t.get("pnl_usd",0)
        peak = max(peak, bal)
        dd = (peak - bal) / peak * 100
        max_dd = max(max_dd, dd)
    return {"n": len(ts), "pnl": pnl, "wr": wr, "dd": max_dd, "bal": bal}

def fmt(s):
    return f"{s['n']}笔 PnL={s['pnl']:+.0f} WR={s['wr']:.0f}% DD={s['dd']:.1f}%"

# 定义所有候选过滤器
filters = {
    # A: v8_score门槛
    "v8<5(只保留v8=4)": lambda t: (t.get("v8_score",0) or 0) < 5,
    # B: 做多SL限制
    "做多SL<5%": lambda t: not (t["direction"]=="long" and t.get("signal_sl_pct",0.05)*100 >= 5),
    "做多SL5-7%减半": lambda t: True,  # 特殊处理
    # C: 做空SL限制
    "做空SL>=7%过滤": lambda t: not (t["direction"]=="short" and t.get("signal_sl_pct",0.05)*100 >= 7),
    # D: 仓位限制
    "仓位<150U过滤": lambda t: t.get("position_usd",0) >= 150,
    # E: MTF门槛
    "MTF<3过滤": lambda t: (t.get("mtf_agree",0) or 0) >= 3,
    "MTF<5过滤": lambda t: (t.get("mtf_agree",0) or 0) >= 5,
}

base = stats(trades)
print(f"基线 v11: {fmt(base)}")
print()

# 先单独测每个过滤
print("=== 单个过滤效果 ===")
for name, func in filters.items():
    if name == "做多SL5-7%减半":
        # 特殊处理：减半PnL
        ts = list(trades)
        for t in ts:
            if t["direction"]=="long" and 5 <= t.get("signal_sl_pct",0.05)*100 < 7:
                t = dict(t)
                t["pnl_usd"] = t.get("pnl_usd",0) * 0.5
    else:
        ts = [t for t in trades if func(t)]
    s = stats(ts)
    if s:
        diff = s["pnl"] - base["pnl"]
        print(f"  {name}: {fmt(s)} ({diff:+.0f})")

# 最优组合搜索
print("\n=== 全组合搜索 (top 10) ===")
filter_items = list(filters.items())
results = []

for r in range(1, len(filter_items) + 1):
    for combo in combinations(range(len(filter_items)), r):
        # 简单过滤（不做特殊处理）
        kept = list(trades)
        names = []
        for idx in combo:
            name, func = filter_items[idx]
            if name == "做多SL5-7%减半":
                continue  # 跳过特殊处理
            kept = [t for t in kept if func(t)]
            names.append(name)
        
        if not kept:
            continue
        s = stats(kept)
        if s:
            results.append((s["pnl"], names, s))

# 特殊组合：v8<5 + 做空SL>=7%过滤
kept = [t for t in trades if (t.get("v8_score",0) or 0) < 5]
kept = [t for t in kept if not (t["direction"]=="short" and t.get("signal_sl_pct",0.05)*100 >= 7)]
s = stats(kept)
results.append((s["pnl"], ["v8<5 + 做空SL>=7%过滤"], s))

results.sort(key=lambda x: -x[0])
for pnl, names, s in results[:10]:
    print(f"  {fmt(s)} | {' + '.join(names)}")

# 逐笔验证：v8<5过滤掉什么
print("\n=== v8=5-6被过滤的140笔总PnL ===")
v56 = [t for t in trades if 5 <= (t.get("v8_score",0) or 0)]
v56_pnl = sum(t.get("pnl_usd",0) for t in v56)
print(f"  140笔总PnL={v56_pnl:+.0f}")
v56_win = sum(1 for t in v56 if t.get("pnl_usd",0) > 0)
print(f"  WR={v56_win/len(v56)*100:.0f}%")
# 但是v8=4只有133笔，可能太少？
v4 = [t for t in trades if (t.get("v8_score",0) or 0) < 5]
v4_pnl = sum(t.get("pnl_usd",0) for t in v4)
print(f"\n  v8=4: 133笔 PnL={v4_pnl:+.0f}")

# 折中方案：v8=5的做多减半
print("\n=== 折中方案测试 ===")
# v8=5做多减半
ts = []
for t in trades:
    nt = dict(t)
    if (t.get("v8_score",0) or 0) == 5 and t["direction"] == "long":
        nt["pnl_usd"] = t.get("pnl_usd",0) * 0.5
    ts.append(nt)
s = stats(ts)
print(f"  v8=5做多减半: {fmt(s)}")

# v8>=5做多减半
ts2 = []
for t in trades:
    nt = dict(t)
    if (t.get("v8_score",0) or 0) >= 5 and t["direction"] == "long":
        nt["pnl_usd"] = t.get("pnl_usd",0) * 0.5
    ts2.append(nt)
s2 = stats(ts2)
print(f"  v8>=5做多减半: {fmt(s2)}")

# v8=5-6 + 做多SL>=7%过滤 + 做空SL>=7%过滤
ts3 = [t for t in trades if not (
    (t["direction"]=="long" and t.get("signal_sl_pct",0.05)*100 >= 7 and (t.get("v8_score",0) or 0) >= 5)
)]
ts3 = [t for t in ts3 if not (
    t["direction"]=="short" and t.get("signal_sl_pct",0.05)*100 >= 7
)]
s3 = stats(ts3)
print(f"  去做多SL>=7%且v8>=5 + 去做空SL>=7%: {fmt(s3)}")
