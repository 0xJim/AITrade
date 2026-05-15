#!/usr/bin/env python3
"""做空门槛优化 - 多方案对比"""
import json, subprocess, sys, time, os, copy
import yaml

PARAMS_FILE = os.path.join(os.path.dirname(__file__), "params.yaml")
RESULT_FILE = os.path.join(os.path.dirname(__file__), "data/backtest_v10_result.json")
PYTHON = "/home/ubuntu/.hermes/hermes-agent/venv/bin/python3"
SCRIPT = os.path.join(os.path.dirname(__file__), "backtest_v10.py")

def load_params():
    with open(PARAMS_FILE) as f:
        return yaml.safe_load(f)

def save_params(params):
    with open(PARAMS_FILE, 'w') as f:
        yaml.dump(params, f, allow_unicode=True, default_flow_style=False)

def run_backtest():
    result = subprocess.run(
        [PYTHON, SCRIPT], capture_output=True, text=True, timeout=600,
        env={**os.environ, "PYTHONUNBUFFERED": "1"}
    )
    if result.returncode != 0:
        return None
    try:
        with open(RESULT_FILE) as f:
            data = json.load(f)
        trades = data['trades']
        wins = [t for t in trades if t['pnl_usd'] > 0]
        longs = [t for t in trades if t['direction']=='long']
        shorts = [t for t in trades if t['direction']=='short']
        sl = [t for t in trades if t['exit_reason'] == '止损']
        total_pnl = sum(t['pnl_usd'] for t in trades)
        
        bal = 5000; peak = 5000; max_dd = 0
        for t in sorted(trades, key=lambda x: x['entry_time']):
            bal += t['pnl_usd']
            if bal > peak: peak = bal
            dd = (peak - bal) / peak * 100
            if dd > max_dd: max_dd = dd
        
        return {
            'trades': len(trades), 'winrate': len(wins)/len(trades)*100 if trades else 0,
            'pnl': total_pnl, 'max_dd': max_dd,
            'long_n': len(longs), 'short_n': len(shorts),
            'long_pnl': sum(t['pnl_usd'] for t in longs),
            'short_pnl': sum(t['pnl_usd'] for t in shorts),
            'sl_count': len(sl),
        }
    except:
        return None

original = load_params()

scenarios = [
    ("只做多(无做空)", {"做空最低质量": 999, "做空ATR下限": 99}),
    ("做多Q80+做空Q80&ATR>=3%", {"做空最低质量": 80, "做空ATR下限": 0.03}),
    ("做多Q80+做空Q85&ATR>=3%", {"做空最低质量": 85, "做空ATR下限": 0.03}),
    ("做多Q80+做空Q90", {"做空最低质量": 90, "做空ATR下限": 0.0}),
    ("做多Q80+做空Q85", {"做空最低质量": 85, "做空ATR下限": 0.0}),
    ("做多Q80+做空全放行", {"做空最低质量": 0, "做空ATR下限": 0.0}),
]

results = []
for i, (name, changes) in enumerate(scenarios):
    print(f"\n[{i+1}/{len(scenarios)}] {name}...")
    params = copy.deepcopy(original)
    for key, val in changes.items():
        params['入场过滤'][key] = val
    save_params(params)
    time.sleep(0.3)
    r = run_backtest()
    if r:
        results.append((name, r))
        print(f"  {r['trades']}笔 | 胜率{r['winrate']:.0f}% | ${r['pnl']:+.0f} | 回撤{r['max_dd']:.1f}% | 做{r['long_n']}空{r['short_n']} | 做多${r['long_pnl']:+.0f} 做空${r['short_pnl']:+.0f}")
    else:
        results.append((name, None))
        print(f"  失败")

save_params(original)

print(f"\n{'='*80}")
print(f"{'方案':<30} {'笔':>3} {'胜率':>4} {'净利':>7} {'回撤':>5} {'做':>3} {'空':>3} {'多$':>6} {'空$':>6}")
print("-"*80)
for name, r in results:
    if r:
        print(f"{name:<30} {r['trades']:>3} {r['winrate']:>3.0f}% ${r['pnl']:>6.0f} {r['max_dd']:>4.1f}% {r['long_n']:>3} {r['short_n']:>3} {r['long_pnl']:>+6.0f} {r['short_pnl']:>+6.0f}")

best = max([(n,r) for n,r in results if r], key=lambda x: x[1]['pnl'])
print(f"\n最优: {best[0]} -> ${best[1]['pnl']:+.0f} (回撤{best[1]['max_dd']:.1f}%)")
