#!/usr/bin/env python3
"""
v11 后置过滤分析 — 在 v10 回测数据上叠加过滤规则
目标：找到最优过滤组合，最大化 PnL
"""
import json
import sys
from collections import defaultdict, Counter
from pathlib import Path

DATA_PATH = Path(__file__).parent / "data" / "backtest_v10_result.json"

def load_trades():
    with open(DATA_PATH) as f:
        d = json.load(f)
    return d.get("trades", [])

def analyze(trades, filters, label=""):
    """应用过滤规则，返回保留交易的统计"""
    kept = list(trades)
    
    for fname, ffunc in filters:
        before = len(kept)
        kept = [t for t in kept if ffunc(t)]
        removed = before - len(kept)
    
    if not kept:
        return None
    
    total_pnl = sum(t.get("pnl_usd", 0) for t in kept)
    wins = sum(1 for t in kept if t.get("pnl_usd", 0) > 0)
    wr = wins / len(kept) * 100
    
    # 按方向
    longs = [t for t in kept if t["direction"] == "long"]
    shorts = [t for t in kept if t["direction"] == "short"]
    long_pnl = sum(t.get("pnl_usd", 0) for t in longs)
    short_pnl = sum(t.get("pnl_usd", 0) for t in shorts)
    
    # 最大回撤
    balance = 5000.0
    peak = balance
    max_dd = 0
    for t in kept:
        balance += t.get("pnl_usd", 0)
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100
        max_dd = max(max_dd, dd)
    
    print(f"\n{'='*60}")
    print(f"📊 {label}")
    print(f"{'='*60}")
    print(f"交易: {len(kept)}笔 | 胜率: {wr:.1f}% | PnL: {total_pnl:+.2f}U")
    print(f"做多: {len(longs)}笔 PnL={long_pnl:+.2f}U | 做空: {len(shorts)}笔 PnL={short_pnl:+.2f}U")
    print(f"最大回撤: {max_dd:.1f}% | 最终余额: {5000+total_pnl:.2f}U")
    
    # 按月
    monthly = defaultdict(list)
    for t in kept:
        monthly[t["entry_time"][:7]].append(t)
    print(f"\n📅 月度:")
    for m in sorted(monthly.keys()):
        ts = monthly[m]
        mpnl = sum(t.get("pnl_usd", 0) for t in ts)
        mw = sum(1 for t in ts if t.get("pnl_usd", 0) > 0)
        icon = "📈" if mpnl > 0 else "📉"
        print(f"  {icon} {m}: {len(ts)}笔 {mpnl:+.2f}U WR={mw/len(ts)*100:.0f}%")
    
    # 最差币种
    sym_pnl = defaultdict(float)
    sym_count = Counter()
    for t in kept:
        sym_pnl[t["symbol"]] += t.get("pnl_usd", 0)
        sym_count[t["symbol"]] += 1
    worst = sorted(sym_pnl.items(), key=lambda x: x[1])[:5]
    best = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)[:5]
    print(f"\n🔴 最差5币:")
    for s, p in worst:
        print(f"  {s}: {sym_count[s]}笔 {p:+.2f}U")
    print(f"🟢 最佳5币:")
    for s, p in best:
        print(f"  {s}: {sym_count[s]}笔 {p:+.2f}U")
    
    return {
        "trades": len(kept),
        "pnl": total_pnl,
        "win_rate": wr,
        "max_dd": max_dd,
        "longs": len(longs),
        "shorts": len(shorts),
    }


def find_worst_symbols(trades, top_n=10):
    """找出最差的币种"""
    sym_pnl = defaultdict(float)
    sym_count = Counter()
    for t in trades:
        sym_pnl[t["symbol"]] += t.get("pnl_usd", 0)
        sym_count[t["symbol"]] += 1
    return sorted(sym_pnl.items(), key=lambda x: x[1])[:top_n]


def greedy_blacklist(trades, max_bl=15):
    """贪心法找最优黑名单 — 每次去掉最差币种"""
    print("\n🔍 贪心黑名单搜索:")
    print("-" * 50)
    
    current = list(trades)
    blacklist = []
    
    for i in range(max_bl):
        sym_pnl = defaultdict(float)
        for t in current:
            sym_pnl[t["symbol"]] += t.get("pnl_usd", 0)
        
        worst_sym, worst_pnl = min(sym_pnl.items(), key=lambda x: x[1])
        
        if worst_pnl >= 0:
            print(f"  第{i+1}轮: 最差币 {worst_sym} PnL={worst_pnl:+.2f} — 已无亏损币，停止")
            break
        
        blacklist.append(worst_sym)
        current = [t for t in current if t["symbol"] != worst_sym]
        
        total = sum(t.get("pnl_usd", 0) for t in current)
        wins = sum(1 for t in current if t.get("pnl_usd", 0) > 0)
        wr = wins / len(current) * 100 if current else 0
        print(f"  第{i+1}轮: 屏蔽 {worst_sym} ({worst_pnl:+.2f}U) → 剩{len(current)}笔 PnL={total:+.2f}U WR={wr:.1f}%")
    
    return blacklist


def main():
    trades = load_trades()
    if not trades:
        print("❌ 无回测数据！先跑 backtest_v10.py")
        sys.exit(1)
    
    print(f"📊 加载 v10 数据: {len(trades)}笔")
    total = sum(t.get("pnl_usd", 0) for t in trades)
    wins = sum(1 for t in trades if t.get("pnl_usd", 0) > 0)
    print(f"   基线: PnL={total:+.2f}U WR={wins/len(trades)*100:.1f}%")
    
    # ====== 第1步：贪心黑名单 ======
    bl = greedy_blacklist(trades, max_bl=15)
    print(f"\n🏆 最优黑名单: {bl}")
    
    # ====== 第2步：逐个分析每项过滤 ======
    
    # 过滤器定义
    filter_blacklist = lambda bl_list: (
        f"黑名单{len(bl_list)}币: {', '.join(bl_list)}", 
        lambda t: t["symbol"] not in set(bl_list)
    )
    
    filter_short_reduce = (
        "做空v8≥5减半仓位(效果≈过滤一半)",
        lambda t: not (t["direction"] == "short" and t.get("v8_score", 0) >= 5 and hash(t["id"]) % 2 == 0)
    )
    
    # ATR过滤
    filter_low_atr = (
        "ATR<3%(低波动做多)",
        lambda t: not (t["direction"] == "long" and t.get("signal_sl_pct", 0.05) < 0.03)
    )
    
    filter_high_atr = (
        "ATR>7%(高波动)",
        lambda t: t.get("signal_sl_pct", 0.05) <= 0.07
    )
    
    # v8 score过滤
    filter_v8_low = (
        "v8_score < 4",
        lambda t: t.get("v8_score", 0) >= 4
    )
    
    # quality过滤
    filter_quality_low = (
        "v8_quality < 70",
        lambda t: t.get("v8_quality", 0) >= 70
    )
    
    # ====== 第3步：测试所有组合 ======
    
    # 基线
    analyze(trades, [], "基线 v10 (无过滤)")
    
    # 仅黑名单
    bl_name, bl_func = filter_blacklist(bl)
    analyze(trades, [(bl_name, bl_func)], f"v11-①: {bl_name}")
    
    # 黑名单 + 做空减仓
    analyze(trades, [(bl_name, bl_func), filter_short_reduce], "v11-①+②: 黑名单+做空减半")
    
    # 黑名单 + 高ATR过滤
    analyze(trades, [(bl_name, bl_func), filter_high_atr], "v11-①+ATR>7%过滤")
    
    # 黑名单 + v8低分过滤
    analyze(trades, [(bl_name, bl_func), filter_v8_low], "v11-①+v8≥4过滤")
    
    # ====== 第4步：最优组合 ======
    print("\n\n" + "="*60)
    print("🎯 全组合搜索 (自动找最优)")
    print("="*60)
    
    all_filters = [
        filter_high_atr,
        filter_v8_low,
        filter_quality_low,
        filter_short_reduce,
    ]
    
    best_pnl = total
    best_combo = []
    best_filters = []
    
    # 测试黑名单 + 0~4个额外过滤的所有组合
    from itertools import combinations
    
    for r in range(len(all_filters) + 1):
        for combo in combinations(range(len(all_filters)), r):
            filters = [(bl_name, bl_func)]
            names = [bl_name]
            for idx in combo:
                filters.append(all_filters[idx])
                names.append(all_filters[idx][0])
            
            kept = list(trades)
            for _, ffunc in filters:
                kept = [t for t in kept if ffunc(t)]
            
            if not kept:
                continue
            
            pnl = sum(t.get("pnl_usd", 0) for t in kept)
            wins = sum(1 for t in kept if t.get("pnl_usd", 0) > 0)
            wr = wins / len(kept) * 100
            
            if pnl > best_pnl:
                best_pnl = pnl
                best_combo = names
                best_filters = filters
                best_count = len(kept)
                best_wr = wr
    
    if best_combo:
        print(f"\n🏆 最优组合:")
        for i, n in enumerate(best_combo, 1):
            print(f"  {i}. {n}")
        print(f"\n  结果: {best_count}笔 PnL={best_pnl:+.2f}U WR={best_wr:.1f}%")
        print(f"  vs 基线: +{best_pnl - total:.2f}U")
        
        # 详细分析最优组合
        analyze(trades, best_filters, "🏆 最优v11方案")
    else:
        print("没有找到比基线更好的组合")
    
    # ====== 第5步：按月稳定性 ======
    print("\n\n" + "="*60)
    print("📊 稳定性分析: 最优方案 vs 基线 (月度)")
    print("="*60)
    
    if best_filters:
        kept = list(trades)
        for _, ffunc in best_filters:
            kept = [t for t in kept if ffunc(t)]
        
        monthly_base = defaultdict(list)
        monthly_v11 = defaultdict(list)
        for t in trades:
            monthly_base[t["entry_time"][:7]].append(t)
        for t in kept:
            monthly_v11[t["entry_time"][:7]].append(t)
        
        all_months = sorted(set(list(monthly_base.keys()) + list(monthly_v11.keys())))
        print(f"{'月份':>8} | {'基线PnL':>10} | {'v11 PnL':>10} | {'改善':>8}")
        print("-" * 50)
        for m in all_months:
            bp = sum(t.get("pnl_usd", 0) for t in monthly_base.get(m, []))
            vp = sum(t.get("pnl_usd", 0) for t in monthly_v11.get(m, []))
            diff = vp - bp
            icon = "✅" if diff > 0 else "❌" if diff < 0 else "➖"
            print(f"{m:>8} | {bp:+10.2f} | {vp:+10.2f} | {icon}{diff:+.2f}")


if __name__ == "__main__":
    main()
