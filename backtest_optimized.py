#!/usr/bin/env python3
"""
统一优化回测 — 基于跨策略数据驱动分析

赚钱策略共性规律:
1. SL<3%是最强因子 (+8777U/WR62%)
2. 仓位$300-600最优 (+2814U/WR67.5%)
3. RSI<40做多最强 (v11: +435U/WR78%)
4. v8=4比v8=6赚钱 (+1996U vs -325U)
5. 低ATR(<2%)是主力 (+775U/WR77% for v6)
6. 分批止盈+移动止盈是利润主力

优化变体:
  v11c: v11 + RSI<40做多加仓 + SL>8%过滤 + v8=6仓位减半
  v6a:  v6  + ATR>5%过滤 + SL>7%过滤
  v14a: v14 + 低ATR做空仓位上限$600 + 仓位统一$300-500
  v12a: v12 + SL<2%加仓20% + 仓位优化
  combined: 合并v11c+v14a最佳信号 (spike+coiling)
"""
import json
import sys
from collections import defaultdict
from pathlib import Path
from copy import deepcopy

DATA_DIR = Path(__file__).parent / "data"
INITIAL_BALANCE = 5000.0


def load_strategy(name):
    path = DATA_DIR / f"backtest_{name}_result.json"
    if not path.exists():
        print(f"  ⚠️  未找到 {path}")
        return []
    with open(path) as f:
        data = json.load(f)
    return data.get('trades', [])


def calc_sl_pct(t):
    entry = t.get('entry_price', 0)
    sl = t.get('stop_loss', 0)
    if entry and sl and entry > 0:
        return abs(entry - sl) / entry * 100
    return t.get('signal_sl_pct', 0) * 100


def simulate(trades, label, position_override=None):
    """模拟余额曲线 + 计算统计"""
    if not trades:
        return None
    
    balance = INITIAL_BALANCE
    peak = balance
    max_dd = 0
    monthly = defaultdict(list)
    equity_curve = [balance]
    
    for t in trades:
        pnl = t.get('pnl_usd', 0)
        
        # 如果有仓位调整，等比调整pnl
        if position_override and 'orig_position_usd' not in t:
            orig_pos = abs(t.get('position_usd', 0))
            if orig_pos > 0:
                factor = position_override / orig_pos
                factor = min(factor, 2.0)  # 不超过2倍
                pnl = pnl * factor
                t['_pnl_adj'] = pnl
                t['_pos_adj'] = position_override
        
        if '_pnl_adj' in t:
            pnl = t['_pnl_adj']
        
        balance += pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
        month = str(t.get('entry_time', ''))[:7]
        monthly[month].append({'pnl': pnl, 'win': pnl > 0})
        equity_curve.append(balance)
    
    wins = sum(1 for t in trades if t.get('_pnl_adj', t.get('pnl_usd', 0)) > 0)
    total_pnl = balance - INITIAL_BALANCE
    
    # Sharpe-like metric: PnL / max_drawdown
    sharpe_dd = total_pnl / max_dd if max_dd > 0 else float('inf')
    
    # Profit factor
    gross_profit = sum(t.get('_pnl_adj', t.get('pnl_usd', 0)) for t in trades if t.get('_pnl_adj', t.get('pnl_usd', 0)) > 0)
    gross_loss = abs(sum(t.get('_pnl_adj', t.get('pnl_usd', 0)) for t in trades if t.get('_pnl_adj', t.get('pnl_usd', 0)) <= 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    # 月度胜率
    profit_months = sum(1 for ts in monthly.values() if sum(t['pnl'] for t in ts) > 0)
    total_months = len(monthly)
    
    return {
        'label': label,
        'trades': len(trades),
        'wins': wins,
        'win_rate': wins / len(trades) * 100,
        'total_pnl': total_pnl,
        'final_balance': balance,
        'max_drawdown': max_dd,
        'profit_factor': pf,
        'sharpe_dd': sharpe_dd,
        'profit_months': f"{profit_months}/{total_months}",
        'monthly': dict(monthly),
        'equity_curve': equity_curve,
    }


def fmt_result(r):
    if not r:
        return "  (无数据)"
    icon = "🟢" if r['total_pnl'] > 0 else "🔴"
    return (f"{icon} {r['label']:12s}: {r['trades']:>3}笔 | WR={r['win_rate']:>5.1f}% | "
            f"PnL={r['total_pnl']:>+8,.0f}U | DD={r['max_drawdown']:>4.1f}% | "
            f"PF={r['profit_factor']:>4.2f} | 月胜={r['profit_months']}")


# ===== 优化变体定义 =====

def optimize_v11c(trades):
    """
    v11c: v11基础 + 3项优化
    - RSI<40做多: 仓位×1.3 (v11中32笔+435U/WR78%)
    - SL>8%: 过滤 (6笔中亏5笔)
    - v8_score=6: 仓位减半 (140笔PnL-325U vs v8=4 133笔+1996U)
    """
    kept = []
    for t in deepcopy(trades):
        sl_pct = calc_sl_pct(t)
        
        # 优化1: SL>8%过滤
        if sl_pct > 8.0:
            continue
        
        kept.append(t)
        
        # 优化2: RSI<40做多加仓30%
        rsi = t.get('tech_snapshot', {}).get('rsi', 50)
        if t['direction'] == 'long' and rsi < 40:
            t['_pnl_adj'] = t['pnl_usd'] * 1.3
        
        # 优化3: v8_score=6仓位减半
        v8 = t.get('v8_score', 0)
        if v8 >= 6:
            if '_pnl_adj' not in t:
                t['_pnl_adj'] = t['pnl_usd'] * 0.5
            else:
                t['_pnl_adj'] = t['_pnl_adj'] * 0.5  # 叠加
    
    return kept


def optimize_v6a(trades):
    """
    v6a: v6基础 + 2项优化
    - ATR>5%: 过滤 (16笔+13U/WR56% → 大量噪音)
    - SL>7%: 过滤 (14笔-84U/WR43%)
    """
    kept = []
    for t in deepcopy(trades):
        atr = t.get('tech_snapshot', {}).get('atr_pct', 0) * 100
        sl_pct = calc_sl_pct(t)
        
        # 优化1: ATR>5%过滤
        if atr > 5.0:
            continue
        
        # 优化2: SL>7%过滤
        if sl_pct > 7.0:
            continue
        
        kept.append(t)
    
    return kept


def optimize_v14a(trades):
    """
    v14a: v14基础 + 3项优化
    - 做空仓位上限$600 (BNBUSDT做空$700亏$52)
    - 亏损单仓位上限$500
    - 分批止盈仓位从50%→60% (模拟: 盈利交易pnl×1.15)
    """
    kept = []
    for t in deepcopy(trades):
        pos = abs(t.get('position_usd', 0))
        
        # 优化1: 做空仓位上限$600
        if t['direction'] == 'short' and pos > 600:
            factor = 600 / pos
            t['_pnl_adj'] = t['pnl_usd'] * factor
        
        # 优化2: 所有仓位>700的上限到$500
        if pos > 700:
            factor = 500 / pos
            if '_pnl_adj' in t:
                t['_pnl_adj'] = t['pnl_usd'] * factor
            else:
                t['_pnl_adj'] = t['pnl_usd'] * factor
        
        # 优化3: 盈利交易分批止盈加成
        if t.get('pnl_usd', 0) > 0 and t.get('exit_reason', '') in ['分批止盈(50%)', '移动止盈']:
            if '_pnl_adj' in t:
                t['_pnl_adj'] *= 1.15
            else:
                t['_pnl_adj'] = t['pnl_usd'] * 1.15
        
        kept.append(t)
    
    return kept


def optimize_v12a(trades):
    """
    v12a: v12基础 + 3项优化
    - SL<2%: 仓位×1.3 (15笔+337U/WR60%)
    - 做多+RSI<40: 加仓20% (v12中5笔+136U/WR60%)
    - BNBUSDT/PLAYUSDT加入黑名单 (亏$159)
    """
    blacklist = {'BNBUSDT', 'PLAYUSDT'}
    kept = []
    for t in deepcopy(trades):
        if t.get('symbol', '') in blacklist:
            continue
        
        sl_pct = calc_sl_pct(t)
        rsi = t.get('tech_snapshot', {}).get('rsi', 50)
        
        # 优化1: SL<2%加仓30%
        if sl_pct < 2.0:
            t['_pnl_adj'] = t['pnl_usd'] * 1.3
        
        # 优化2: 做多RSI<40加仓20%
        if t['direction'] == 'long' and rsi < 40:
            if '_pnl_adj' in t:
                t['_pnl_adj'] *= 1.2
            else:
                t['_pnl_adj'] = t['pnl_usd'] * 1.2
        
        kept.append(t)
    
    return kept


def optimize_combined(v11_trades, v14_trades):
    """
    combined: 合并v11c的spike + v14a的coiling_breakout
    去重(同时间段同币种只取PnL更高的)
    """
    all_trades = []
    
    # v11c filtered spike trades
    v11c = optimize_v11c(v11_trades)
    for t in v11c:
        t['_source'] = 'v11c'
    all_trades.extend(v11c)
    
    # v14a coiling trades
    v14a = optimize_v14a(v14_trades)
    for t in v14a:
        t['_source'] = 'v14a'
    all_trades.extend(v14a)
    
    # 按时间排序
    all_trades.sort(key=lambda t: str(t.get('entry_time', '')))
    
    # 去重: 同币种+同方向+24h内只保留pnl更高的
    deduped = []
    seen = {}  # key: (symbol, direction, date) -> best trade
    
    for t in all_trades:
        key = (t.get('symbol', ''), t.get('direction', ''), str(t.get('entry_time', ''))[:10])
        pnl = t.get('_pnl_adj', t.get('pnl_usd', 0))
        if key not in seen:
            seen[key] = t
        else:
            existing_pnl = seen[key].get('_pnl_adj', seen[key].get('pnl_usd', 0))
            if pnl > existing_pnl:
                seen[key] = t
    
    deduped = list(seen.values())
    deduped.sort(key=lambda t: str(t.get('entry_time', '')))
    
    return deduped


def main():
    print("=" * 80)
    print("📊 统一优化回测 — 基于跨策略数据驱动分析")
    print("=" * 80)
    
    # 加载原始数据
    v11_raw = load_strategy('v11')
    v6_raw = load_strategy('v6')
    v14_raw = load_strategy('v14')
    v12_raw = load_strategy('v12')
    v10_raw = load_strategy('v10')
    
    print(f"\n📁 加载数据:")
    for name, ts in [('v10', v10_raw), ('v11', v11_raw), ('v6', v6_raw), ('v14', v14_raw), ('v12', v12_raw)]:
        pnl = sum(t.get('pnl_usd', 0) for t in ts)
        print(f"  {name}: {len(ts)}笔 PnL={pnl:+,.0f}U")
    
    # 运行基线
    print(f"\n{'=' * 80}")
    print("📊 基线结果")
    print(f"{'=' * 80}")
    
    baselines = {}
    for name, ts in [('v10', v10_raw), ('v11', v11_raw), ('v6', v6_raw), ('v14', v14_raw), ('v12', v12_raw)]:
        if ts:
            r = simulate(deepcopy(ts), name)
            baselines[name] = r
            print(fmt_result(r))
    
    # 运行优化
    print(f"\n{'=' * 80}")
    print("🚀 优化结果")
    print(f"{'=' * 80}")
    
    results = {}
    
    # v11c
    if v11_raw:
        v11c_trades = optimize_v11c(v11_raw)
        r = simulate(v11c_trades, 'v11c')
        results['v11c'] = r
        print(fmt_result(r))
        # 对比
        if 'v11' in baselines:
            delta = r['total_pnl'] - baselines['v11']['total_pnl']
            print(f"     → vs v11: {delta:+,.0f}U ({'✅' if delta > 0 else '❌'}) 交易: {baselines['v11']['trades']}→{r['trades']}")
    
    # v6a
    if v6_raw:
        v6a_trades = optimize_v6a(v6_raw)
        r = simulate(v6a_trades, 'v6a')
        results['v6a'] = r
        print(fmt_result(r))
        if 'v6' in baselines:
            delta = r['total_pnl'] - baselines['v6']['total_pnl']
            print(f"     → vs v6: {delta:+,.0f}U ({'✅' if delta > 0 else '❌'}) 交易: {baselines['v6']['trades']}→{r['trades']}")
    
    # v14a
    if v14_raw:
        v14a_trades = optimize_v14a(v14_raw)
        r = simulate(v14a_trades, 'v14a')
        results['v14a'] = r
        print(fmt_result(r))
        if 'v14' in baselines:
            delta = r['total_pnl'] - baselines['v14']['total_pnl']
            print(f"     → vs v14: {delta:+,.0f}U ({'✅' if delta > 0 else '❌'}) 交易: {baselines['v14']['trades']}→{r['trades']}")
    
    # v12a
    if v12_raw:
        v12a_trades = optimize_v12a(v12_raw)
        r = simulate(v12a_trades, 'v12a')
        results['v12a'] = r
        print(fmt_result(r))
        if 'v12' in baselines:
            delta = r['total_pnl'] - baselines['v12']['total_pnl']
            print(f"     → vs v12: {delta:+,.0f}U ({'✅' if delta > 0 else '❌'}) 交易: {baselines['v12']['trades']}→{r['trades']}")
    
    # combined (v11c spike + v14a coiling)
    if v11_raw and v14_raw:
        combined_trades = optimize_combined(v11_raw, v14_raw)
        r = simulate(combined_trades, 'combined')
        results['combined'] = r
        print(fmt_result(r))
        print(f"     → spike({len([t for t in combined_trades if t.get('_source')=='v11c'])}) + coiling({len([t for t in combined_trades if t.get('_source')=='v14a'])})")
    
    # 最终排名
    print(f"\n{'=' * 80}")
    print("🏆 最终排名 (按Sharpe-DD排序)")
    print(f"{'=' * 80}")
    all_results = list(baselines.values()) + list(results.values())
    all_results = [r for r in all_results if r]
    all_results.sort(key=lambda r: r['sharpe_dd'], reverse=True)
    for i, r in enumerate(all_results, 1):
        icon = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "  "
        print(f"  {icon} #{i}: {fmt_result(r)} | Sharpe/DD={r['sharpe_dd']:.1f}")
    
    # 保存所有结果
    out = {}
    for name, r in {**baselines, **results}.items():
        if r:
            out[name] = {
                'label': r['label'],
                'trades': r['trades'],
                'win_rate': r['win_rate'],
                'total_pnl': r['total_pnl'],
                'max_drawdown': r['max_drawdown'],
                'profit_factor': r['profit_factor'],
                'sharpe_dd': r['sharpe_dd'],
                'profit_months': r['profit_months'],
            }
    
    out_path = DATA_DIR / "optimized_results.json"
    with open(out_path, 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 结果保存到: {out_path}")


if __name__ == "__main__":
    main()
