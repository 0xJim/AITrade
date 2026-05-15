#!/usr/bin/env python3
"""止损策略对比测试 - 修改params.yaml后自动跑回测"""
import json, subprocess, sys, time, os, copy

sys.path.insert(0, os.path.dirname(__file__))
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
        [PYTHON, SCRIPT],
        capture_output=True, text=True, timeout=600,
        env={**os.environ, "PYTHONUNBUFFERED": "1"}
    )
    if result.returncode != 0:
        print(f"回测失败: {result.stderr[-500:]}")
        return None
    # 解析结果
    try:
        with open(RESULT_FILE) as f:
            data = json.load(f)
        trades = data['trades']
        wins = [t for t in trades if t['pnl_usd'] > 0]
        losses = [t for t in trades if t['pnl_usd'] <= 0]
        sl_trades = [t for t in trades if t['exit_reason'] == '止损']
        
        # 计算回撤
        balance = 5000
        peak = 5000
        max_dd = 0
        for t in sorted(trades, key=lambda x: x['entry_time']):
            balance += t['pnl_usd']
            if balance > peak:
                peak = balance
            dd = (peak - balance) / peak * 100
            if dd > max_dd:
                max_dd = dd
        
        total_pnl = sum(t['pnl_usd'] for t in trades)
        return {
            'trades': len(trades),
            'wins': len(wins),
            'winrate': len(wins)/len(trades)*100 if trades else 0,
            'pnl': total_pnl,
            'max_dd': max_dd,
            'sl_count': len(sl_trades),
            'sl_loss': sum(t['pnl_usd'] for t in sl_trades),
            'avg_win': sum(t['pnl_usd'] for t in wins)/len(wins) if wins else 0,
            'avg_loss': sum(t['pnl_usd'] for t in losses)/len(losses) if losses else 0,
        }
    except Exception as e:
        print(f"解析失败: {e}")
        # 尝试从stdout解析
        for line in result.stdout.split('\n'):
            if '初始' in line or '总盈亏' in line:
                print(f"  {line.strip()}")
        return None

# 保存原始参数
original_params = load_params()

# 定义测试方案
scenarios = [
    {
        'name': '基线(当前): ATR×1.5, MIN_SL=3%',
        'changes': {}  # 不改
    },
    {
        'name': '方案A: ATR×2.5, MIN_SL=4%',
        'changes': {
            ('止损止盈', 'ATR止损乘数'): 2.5,
            ('止损止盈', '最小止损百分比'): 0.04,
        }
    },
    {
        'name': '方案B: ATR×2.0, MIN_SL=3.5%',
        'changes': {
            ('止损止盈', 'ATR止损乘数'): 2.0,
            ('止损止盈', '最小止损百分比'): 0.035,
        }
    },
    {
        'name': '方案C: ATR×3.0, MIN_SL=5%',
        'changes': {
            ('止损止盈', 'ATR止损乘数'): 3.0,
            ('止损止盈', '最小止损百分比'): 0.05,
        }
    },
    {
        'name': '方案D: ATR×2.0, MIN_SL=4%, TP×3.0',
        'changes': {
            ('止损止盈', 'ATR止损乘数'): 2.0,
            ('止损止盈', '最小止损百分比'): 0.04,
        }
        # TP也在代码里改，这个方案先看SL
    },
]

results = []

for i, sc in enumerate(scenarios):
    print(f"\n{'='*60}")
    print(f"[{i+1}/{len(scenarios)}] {sc['name']}")
    print(f"{'='*60}")
    
    # 恢复原始参数
    params = copy.deepcopy(original_params)
    
    # 应用修改
    for (section, key), val in sc['changes'].items():
        if section in params and key in params[section]:
            params[section][key] = val
            print(f"  {section}.{key} = {val}")
    
    save_params(params)
    time.sleep(0.5)
    
    r = run_backtest()
    if r:
        results.append((sc['name'], r))
        print(f"  ✅ {r['trades']}笔, 胜率{r['winrate']:.1f}%, 净利${r['pnl']:.1f}, 回撤{r['max_dd']:.1f}%")
        print(f"     止损: {r['sl_count']}笔亏${r['sl_loss']:.1f}")
    else:
        results.append((sc['name'], None))
        print(f"  ❌ 失败")

# 恢复原始参数
save_params(original_params)

# 打印对比表
print(f"\n{'='*70}")
print("📊 止损优化对比结果")
print(f"{'='*70}")
print(f"{'方案':<35} {'笔数':>4} {'胜率':>5} {'净利':>8} {'回撤':>5} {'止损笔':>5} {'止损亏':>8}")
print("-"*70)
for name, r in results:
    if r:
        print(f"{name:<35} {r['trades']:>4} {r['winrate']:>4.0f}% ${r['pnl']:>7.1f} {r['max_dd']:>4.1f}% {r['sl_count']:>5} ${r['sl_loss']:>7.1f}")

# 找最优
valid = [(n, r) for n, r in results if r and r['pnl'] > 0]
if valid:
    best = max(valid, key=lambda x: x[1]['pnl'])
    print(f"\n🏆 最优: {best[0]} — 净利${best[1]['pnl']:.1f}")
