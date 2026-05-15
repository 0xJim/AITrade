#!/usr/bin/env python3
"""批量回测所有版本，统一1000U本金"""
import subprocess, sys, re, json, time
from pathlib import Path

VENV = "/home/ubuntu/.hermes/hermes-agent/venv/bin/python3"
BASE = Path("/home/ubuntu/.hermes/trading")

# 需要跑的版本: (名称, 脚本路径, 输出文件)
scripts = [
    ("v7plus", BASE / "backtest_v7plus.py"),
    ("v7tuned", BASE / "backtest_v7tuned.py"),
    ("v8", BASE / "backtest_v8.py"),
    ("v9", BASE / "backtest_v9_result.json"),  # 无脚本，需找
    ("v12", BASE / "backtest_v12.py"),
    ("v13", BASE / "backtest_v13.py"),
    ("v14", BASE / "backtest_v14.py"),
]

# v6 和 v9 没有独立脚本，跳过（它们是基于主backtest.py的参数变体）
# 只跑有独立脚本的版本

to_run = [(n, p) for n, p in scripts if p.suffix == '.py' and p.exists()]

print(f"📋 需要跑 {len(to_run)} 个版本")
print("=" * 50)

results_summary = {}

for name, script_path in to_run:
    # 读取脚本，替换 INITIAL_BALANCE = 5000.0 → 1000.0
    content = script_path.read_text()
    
    # 多种模式替换
    original = content
    content = re.sub(r'INITIAL_BALANCE\s*=\s*5000\.0', 'INITIAL_BALANCE = 1000.0', content)
    content = re.sub(r'INITIAL_BALANCE\s*=\s*5000', 'INITIAL_BALANCE = 1000.0', content)
    
    if content == original:
        # 检查当前值
        m = re.search(r'INITIAL_BALANCE\s*=\s*([\d.]+)', content)
        if m:
            cur = m.group(1)
            if float(cur) == 1000.0:
                print(f"  {name}: 已经是1000U，无需修改")
            else:
                print(f"  {name}: 当前本金={cur}U，修改为1000U")
                content = re.sub(r'INITIAL_BALANCE\s*=\s*[\d.]+', 'INITIAL_BALANCE = 1000.0', content)
    
    # 临时写入
    tmp_script = BASE / f"_tmp_bt_{name}.py"
    tmp_script.write_text(content)
    
    print(f"\n🚀 跑 {name} ({script_path.name})...")
    start = time.time()
    
    try:
        proc = subprocess.run(
            [VENV, "-u", str(tmp_script)],
            capture_output=True, text=True, timeout=600, cwd=str(BASE)
        )
        elapsed = time.time() - start
        
        # 找结果文件
        data_dir = BASE / "data"
        # 尝试多种命名
        possible = [
            data_dir / f"backtest_{name}_result.json",
            data_dir / f"backtest_result.json",
        ]
        
        result = None
        for pf in possible:
            if pf.exists():
                try:
                    with open(pf) as f:
                        d = json.load(f)
                    if d.get("total_trades", 0) > 0:
                        result = d
                        # 保存为标准名称
                        std_name = data_dir / f"bt1000_{name}_result.json"
                        with open(std_name, 'w') as f:
                            json.dump(d, f, ensure_ascii=False, indent=2, default=str)
                        break
                except:
                    pass
        
        if result:
            pnl = result.get("total_pnl", 0)
            wr = result.get("win_rate", 0)
            trades = result.get("total_trades", 0)
            dd = result.get("max_drawdown", 0)
            results_summary[name] = {
                "pnl": pnl, "roi": pnl/10, "trades": trades, 
                "wr": wr, "dd": dd, "time": elapsed
            }
            print(f"  ✅ {name}: {trades}笔 胜率{wr:.1f}% 盈亏{pnl:+.1f}U 回撤{dd:.1f}% ({elapsed:.0f}s)")
        else:
            # 尝试从输出解析
            out = proc.stdout[-500:] if len(proc.stdout) > 500 else proc.stdout
            print(f"  ⚠️ {name}: 没找到结果文件")
            if "Error" in proc.stderr:
                print(f"  错误: {proc.stderr[:200]}")
            results_summary[name] = {"error": True, "time": elapsed}
            
    except subprocess.TimeoutExpired:
        print(f"  ❌ {name}: 超时(>600s)")
        results_summary[name] = {"error": "timeout"}
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        results_summary[name] = {"error": str(e)}
    finally:
        # 清理临时文件
        if tmp_script.exists():
            tmp_script.unlink()

# 输出汇总
print("\n" + "=" * 60)
print("📊 全版本1000U回测汇总")
print("=" * 60)

# 加上已有的1000U结果
existing_1000 = {
    "v10c": BASE / "data" / "backtest_v10c_result.json",
    "v11g": BASE / "data" / "backtest_v11g_result.json",
    "v11h": BASE / "data" / "backtest_v11h_result.json",
    "v11i": BASE / "data" / "backtest_v11i_result.json",
    "v11new": BASE / "data" / "backtest_v11_result.json",
}
for name, fp in existing_1000.items():
    if fp.exists():
        with open(fp) as f:
            d = json.load(f)
        if d.get("initial_balance") == 1000:
            pnl = d.get("total_pnl", 0)
            results_summary[name] = {
                "pnl": pnl, "roi": pnl/10, "trades": d.get("total_trades", 0),
                "wr": d.get("win_rate", 0), "dd": d.get("max_drawdown", 0)
            }

# 排名
ranked = sorted(results_summary.items(), key=lambda x: x[1].get("pnl", -99999), reverse=True)
print(f"\n{'排名':>2} {'版本':<10} {'交易':>5} {'胜率':>6} {'盈亏':>9} {'ROI':>7} {'回撤':>6}")
print("-" * 55)
for i, (name, r) in enumerate(ranked):
    if "error" in r:
        print(f" {i+1:>2} {name:<10} ❌ 跑失败")
    else:
        print(f" {i+1:>2} {name:<10} {r['trades']:>5} {r['wr']:>5.1f}% {r['pnl']:>+9.1f} {r['roi']:>+6.1f}% {r['dd']:>5.1f}%")

# 保存汇总
ranking_path = BASE / "data" / "all_1000u_ranking.json"
with open(ranking_path, 'w') as f:
    json.dump(ranked, f, ensure_ascii=False, indent=2, default=str)
print(f"\n📁 保存到: {ranking_path}")
