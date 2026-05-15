#!/usr/bin/env python3
"""
全策略1000天回测 — 依次运行所有20个策略，输出排名
"""
import subprocess
import sys
import json
import os
import time
from datetime import datetime
from pathlib import Path

VENV_PY = "/home/ubuntu/.hermes/hermes-agent/venv/bin/python3"
BASE = Path.home() / ".hermes" / "trading"
DATA_DIR = BASE / "data"
DAYS = 1000

# ── 策略定义 ──
# 格式: (脚本文件, 输出json名, 运行方式)
#   run_type: "api" = 独立API回测, "filter" = 依赖v10数据, "skip" = 跳过
STRATEGIES = [
    # A类 - 独立回测引擎
    ("backtest.py",            "data/bt_1kd_base.json",     "api"),
    ("backtest_v7plus.py",     "data/bt_1kd_v7plus.json",   "api"),
    ("backtest_v7tuned.py",    "data/bt_1kd_v7tuned.json",  "api"),
    ("backtest_v8.py",         "data/bt_1kd_v8.json",       "api"),
    ("backtest_v10.py",        "data/bt_1kd_v10.json",      "api"),
    ("backtest_v12.py",        "data/bt_1kd_v12.json",      "api"),
    ("backtest_v13.py",        "data/bt_1kd_v13.json",      "api"),
    ("backtest_v14.py",        "data/bt_1kd_v14.json",      "api"),
    ("backtest_v15.py",        "data/bt_1kd_v15.json",      "api"),
    ("backtest_v16.py",        "data/bt_1kd_v16.json",      "api"),
    ("backtest_v17.py",        "data/bt_1kd_v17.json",      "api"),
    ("backtest_v18.py",        "data/bt_1kd_v18.json",      "api"),
    # v6 系列 (backtest.py的变体配置)
    ("backtest.py",            "data/bt_1kd_v6.json",       "api_v6"),
    ("backtest.py",            "data/bt_1kd_v6a.json",      "api_v6a"),
    # v11 系列 (后处理 - 依赖v10)
    ("backtest_v11.py",        "data/bt_1kd_v11.json",      "filter"),
    ("backtest_v11g.py",       "data/bt_1kd_v11g.json",     "filter"),
    # v10c (backtest.py的另一种配置)
    ("backtest.py",            "data/bt_1kd_v10c.json",     "api_10c"),
    # v12a (backtest_v12.py的变体)
    ("backtest_v12.py",        "data/bt_1kd_v12a.json",     "api_v12a"),
    # v11c (backtest_v11.py的变体)
    ("backtest_v11.py",        "data/bt_1kd_v11c.json",     "filter_v11c"),
]

def patch_days(script_path):
    """临时把脚本里的 timedelta(days=180) 或默认天数改为1000"""
    with open(script_path, 'r') as f:
        content = f.read()
    return content

def run_cmd(cmd, timeout=1800):
    """运行命令，返回stdout"""
    print(f"  ▶ {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, 
            timeout=timeout, cwd=str(BASE)
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT", -1

def parse_result(json_path):
    """解析回测结果JSON，提取关键指标"""
    try:
        with open(json_path) as f:
            data = json.load(f)
        
        # 兼容不同格式
        if isinstance(data, dict):
            summary = data.get('summary', data)
            trades = data.get('trades', [])
        else:
            return None
        
        total_pnl = summary.get('total_pnl', summary.get('pnl', 0))
        total_trades = summary.get('total_trades', summary.get('trades', len(trades)))
        win_rate = summary.get('win_rate', 0)
        max_dd = summary.get('max_drawdown', summary.get('max_dd', 0))
        profit_factor = summary.get('profit_factor', summary.get('pf', 0))
        months_win = summary.get('months_profitable', summary.get('months_win', 0))
        avg_rr = summary.get('avg_rr', summary.get('reward_risk', 0))
        
        return {
            'trades': total_trades,
            'win_rate': win_rate,
            'pnl': total_pnl,
            'max_dd': max_dd,
            'pf': profit_factor,
            'months_win': months_win,
            'avg_rr': avg_rr,
        }
    except Exception as e:
        print(f"  ⚠️ 解析失败: {e}")
        return None

def main():
    print(f"🚀 全策略1000天回测")
    print(f"   开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   策略数: {len(STRATEGIES)}")
    print("=" * 60)
    
    results = {}
    
    for i, (script, output, run_type) in enumerate(STRATEGIES):
        strategy_name = Path(output).stem.replace('bt_1kd_', '')
        print(f"\n[{i+1}/{len(STRATEGIES)}] {strategy_name} ({script}, {run_type})")
        
        start_t = time.time()
        
        if run_type == "api":
            # 独立回测 - 直接运行脚本
            # 需要先确保脚本里用的是1000天
            stdout, stderr, rc = run_cmd([
                VENV_PY, "-c", f"""
import sys
sys.argv = ['{script}']
# 临时修改回测天数为1000
exec(open('{BASE}/{script}').read().replace('days=180', 'days={DAYS}').replace('days=30', 'days={DAYS}'))
""", 
                str(BASE / script)
            ])
        elif run_type == "api":
            stdout, stderr, rc = run_cmd([VENV_PY, str(BASE / script)])
        
        elapsed = time.time() - start_t
        
        # 检查输出
        output_path = BASE / output
        if output_path.exists():
            r = parse_result(output_path)
            if r:
                results[strategy_name] = r
                pnl_str = f"+{r['pnl']:.0f}U" if r['pnl'] > 0 else f"{r['pnl']:.0f}U"
                print(f"  ✅ {elapsed:.0f}s | {r['trades']}笔 | WR {r['win_rate']:.1f}% | {pnl_str} | DD {r['max_dd']:.1f}%")
            else:
                print(f"  ⚠️ {elapsed:.0f}s | 无法解析结果")
        else:
            print(f"  ❌ {elapsed:.0f}s | 无输出文件")
    
    # 排名
    print("\n" + "=" * 60)
    print("📊 1000天回测排名")
    print("=" * 60)
    
    # 按PnL排序
    ranked = sorted(results.items(), key=lambda x: x[1]['pnl'], reverse=True)
    
    for rank, (name, r) in enumerate(ranked, 1):
        emoji = "🟢" if r['pnl'] > 0 else "🔴"
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"{rank:2d}")
        pnl_str = f"+{r['pnl']:.0f}" if r['pnl'] > 0 else f"{r['pnl']:.0f}"
        print(f"  {medal} {emoji} {name:10s} {r['trades']:4d}笔 WR{r['win_rate']:5.1f}% {pnl_str:>7s}U DD{r['max_dd']:5.1f}% PF{r['pf']:.2f}")
    
    # 保存
    ranking = {
        'generated': datetime.now().isoformat(),
        'days': DAYS,
        'results': {k: v for k, v in ranked}
    }
    out_file = DATA_DIR / "ranking_1000d.json"
    with open(out_file, 'w') as f:
        json.dump(ranking, f, indent=2, default=str)
    print(f"\n💾 结果已保存: {out_file}")

if __name__ == "__main__":
    main()
