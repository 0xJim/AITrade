#!/usr/bin/env python3
"""分析v11系列1000U回测结果"""
import json
import os
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

files = [
    ('backtest_v11_result.json', 'v11'),
    ('backtest_v11g_result.json', 'v11g'),
    ('backtest_v11h_result.json', 'v11h'),
    ('backtest_v11i_result.json', 'v11i'),
]

print('═' * 80)
print('📊 v11系列回测对比 (1000U / 3年数据 2023.08~2026.05)')
print('═' * 80)

rows = []
for fname, name in files:
    fpath = os.path.join(DATA_DIR, fname)
    with open(fpath) as f:
        d = json.load(f)
    
    ib = d.get('initial_balance', 1000)
    fb = d.get('final_balance', 0)
    tp = d.get('total_pnl', 0)
    tt = d.get('total_trades', 0)
    w = d.get('wins', 0)
    l = d.get('losses', 0)
    wr = d.get('win_rate', 0)
    dd = d.get('max_drawdown', 0)
    
    trades = d.get('trades', [])
    
    # 月度统计
    monthly = defaultdict(float)
    for t in trades:
        month = t.get('entry_time', '')[:7]
        if month:
            monthly[month] += t.get('pnl_usd', 0)
    
    profit_months = sum(1 for v in monthly.values() if v > 0)
    total_months = len(monthly)
    
    # 计算 profit factor
    gross_profit = sum(t.get('pnl_usd', 0) for t in trades if t.get('pnl_usd', 0) > 0)
    gross_loss = abs(sum(t.get('pnl_usd', 0) for t in trades if t.get('pnl_usd', 0) < 0))
    calc_pf = gross_profit / gross_loss if gross_loss else 0
    
    ret_pct = tp / ib * 100 if ib else 0
    
    rows.append({
        'name': name,
        'tt': tt, 'wr': wr, 'tp': tp, 'dd': dd, 'pf': calc_pf,
        'profit_months': profit_months, 'total_months': total_months,
        'ret_pct': ret_pct, 'fb': fb, 'trades': trades
    })

# 排序 by PnL
rows.sort(key=lambda x: x['tp'], reverse=True)

print(f'\n{"策略":<8} {"笔数":>6} {"WR":>7} {"PnL":>10} {"收益%":>8} {"DD%":>7} {"PF":>6} {"月胜":>6}')
print('─' * 65)
for r in rows:
    print(f'{r["name"]:<8} {r["tt"]:>6} {r["wr"]:>6.1f}% {r["tp"]:>+9.0f}U {r["ret_pct"]:>+7.1f}% {r["dd"]:>6.1f}% {r["pf"]:>6.2f} {r["profit_months"]}/{r["total_months"]}')

# 深度分析每个版本
for r in rows:
    print(f'\n{"═"*80}')
    print(f'📊 {r["name"]} 深度分析')
    print(f'{"═"*80}')
    trades = r['trades']
    
    # 按方向
    longs = [t for t in trades if t['direction'] == 'long']
    shorts = [t for t in trades if t['direction'] == 'short']
    for label, group in [('做多', longs), ('做空', shorts)]:
        n = len(group)
        pnl = sum(t.get('pnl_usd', 0) for t in group)
        wins = sum(1 for t in group if t.get('pnl_usd', 0) > 0)
        wr = wins/n*100 if n else 0
        avg_win = sum(t.get('pnl_usd', 0) for t in group if t.get('pnl_usd', 0) > 0) / max(wins, 1)
        losses_n = n - wins
        avg_loss = sum(t.get('pnl_usd', 0) for t in group if t.get('pnl_usd', 0) < 0) / max(losses_n, 1) if losses_n else 0
        rr = abs(avg_win / avg_loss) if avg_loss else 0
        print(f'  {label}: {n}笔 WR={wr:.1f}% PnL={pnl:+.0f}U 均赢={avg_win:+.1f}U 均亏={avg_loss:+.1f}U RR={rr:.2f}')
    
    # 最赚/最亏币种
    by_sym = defaultdict(lambda: {'n': 0, 'pnl': 0, 'wins': 0})
    for t in trades:
        s = t['symbol']
        by_sym[s]['n'] += 1
        by_sym[s]['pnl'] += t.get('pnl_usd', 0)
        if t.get('pnl_usd', 0) > 0:
            by_sym[s]['wins'] += 1
    
    top5 = sorted(by_sym.items(), key=lambda x: x[1]['pnl'], reverse=True)[:5]
    worst5 = sorted(by_sym.items(), key=lambda x: x[1]['pnl'])[:5]
    
    print(f'\n  🏆 最赚钱TOP5:')
    for sym, s in top5:
        wr = s['wins'] / s['n'] * 100
        print(f'    {sym:<15} {s["n"]:>3}笔 WR={wr:.0f}% PnL={s["pnl"]:+.0f}U')
    
    print(f'\n  💀 最亏损TOP5:')
    for sym, s in worst5:
        wr = s['wins'] / s['n'] * 100
        print(f'    {sym:<15} {s["n"]:>3}笔 WR={wr:.0f}% PnL={s["pnl"]:+.0f}U')
