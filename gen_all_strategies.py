#!/usr/bin/env python3
"""
策略全量盘点脚本 — 读取所有回测结果，生成统一格式的策略文档
"""
import json, os
from collections import defaultdict

DATA_DIR = os.path.expanduser("~/.hermes/trading/data")
STRAT_DIR = os.path.expanduser("~/.hermes/trading/strategies")

# === 策略元数据 (手动整理) ===
STRATEGIES = {
    "v5": {
        "name": "v5 — 初始版本",
        "description": "最早的基础回测，15m异动+费率信号",
        "signal_types": ["15m异动", "极端费率"],
        "key_params": "杠杆3x / env≥4 / ATR×2.0止损 / 最大持仓3",
        "changes_from": "无 (初始版)",
    },
    "v6": {
        "name": "v6 — 极端费率核心",
        "description": "以极端负费率做多为核心策略，低ATR(<3%)75%胜率",
        "signal_types": ["极端负费率做多(主力)", "极端正费率做空"],
        "key_params": "杠杆3x / env≥4 / ATR<3%优先 / SL ATR×2.0 / 最大持仓2",
        "changes_from": "v5: 聚焦极端费率信号",
    },
    "v7plus": {
        "name": "v7+ — 精准狙击宽松版",
        "description": "低ATR SL放宽3倍 + 冷却7天 + 趋势跟随降低2%",
        "signal_types": ["极端费率", "15m异动"],
        "key_params": "杠杆3x / env≥3 / ATR×3.0(低ATR) / 持仓3 / 冷却168h",
        "changes_from": "v6: SL放宽/冷却加长/趋势跟随降低/多持仓",
    },
    "v7tuned": {
        "name": "v7tuned — 精准狙击严格版",
        "description": "v7的严格版，env门槛提高到5",
        "signal_types": ["极端费率", "15m异动"],
        "key_params": "杠杆3x / env≥5 / ATR×3.0(低ATR) / 持仓2 / 冷却168h",
        "changes_from": "v7+: env≥5(原3) / 持仓2(原3)",
    },
    "v8": {
        "name": "v8 — 六维评分系统",
        "description": "引入OI/费率/量价/宏观/清算/聪明钱六维加权评分 + Kelly仓位",
        "signal_types": ["六维信号质量评分", "K线形态", "三要素入场"],
        "key_params": "杠杆3x / 信号质量≥0 / Kelly仓位 / RR≥0 / 最大持仓2",
        "changes_from": "v7: 引入v8评分框架",
    },
    "v9": {
        "name": "v9 — 全量扫描",
        "description": "关闭ATR危险区跳过，全量扫描，env≥4",
        "signal_types": ["全量扫描"],
        "key_params": "杠杆3x / env≥4 / ATR危险区不跳过 / 仓位上限20%",
        "changes_from": "v8: ATR危险区不跳过 / env boost×1.2",
    },
    "v10": {
        "name": "v10 — 1h K线双触发",
        "description": "15m异动 + 费率异动双触发，做空quality≥85",
        "signal_types": ["15m异动(1%)", "费率异动(±5%)"],
        "key_params": "杠杆3x / 做多ATR<5%+quality≥80 / 做空quality≥85+ATR≥3%",
        "changes_from": "v9: 双触发系统",
    },
    "v10c": {
        "name": "v10c — 宽限期优化",
        "description": "入场4h宽限期不扫SL / MAX_HOLD 72h / 蓄势突破强制SL 3-4%",
        "signal_types": ["15m异动", "费率异动", "蓄势突破"],
        "key_params": "杠杆3x / 4h宽限 / 72h持仓 / SL 3-4%",
        "changes_from": "v10: 宽限期+延长持仓",
        "initial_balance": 1000,
    },
    "v11": {
        "name": "v11 — 黑名单+评分门槛",
        "description": "贪心黑名单15币 + v8≥4 + 做空v8≥5减半",
        "signal_types": ["v10c全部信号"],
        "key_params": "黑名单15币 / v8≥4 / 做空v8≥5仓位×0.5",
        "changes_from": "v10c: 后置过滤(黑名单+v8门槛)",
    },
    "v12": {
        "name": "v12 — RSI+仓位+排序优化",
        "description": "RSI≥65跳过做多 + 做多仓位×0.5 + 信号排序做空优先",
        "signal_types": ["v10c信号 + 排序"],
        "key_params": "RSI≥65不做多 / 做多×0.5 / 做空优先排序",
        "changes_from": "v10c: RSI过滤/仓位/排序",
    },
    "v13": {
        "name": "v13 — v12调参版",
        "description": "基于v12的参数调整版",
        "signal_types": ["同v12"],
        "key_params": "同v12参数微调",
        "changes_from": "v12: 参数微调",
    },
    "v14": {
        "name": "v14 — ATR做多限制",
        "description": "ATR≥2%禁止做多 / 低ATR做空仓位上限$700 / env=5仓位半价",
        "signal_types": ["同v12 + 蓄势突破"],
        "key_params": "ATR≥2%禁做多 / 低ATR做空$700上限 / env=5半仓",
        "changes_from": "v12: 3项数据驱动优化",
    },
    "v15": {
        "name": "v15 — 做空ATR过滤",
        "description": "做空A级+ATR≥1.4%跳过 / ATR<0.7%做空跳过",
        "signal_types": ["同v14"],
        "key_params": "做空ATR≥1.4%跳过 / ATR<0.7%做空跳过",
        "changes_from": "v14: 2项做空ATR过滤",
    },
    "v16": {
        "name": "v16 — 回归v11广撒网",
        "description": "回归v11策略核心: 广撒网+动态淘汰，尝试将v14过滤逻辑应用到v11框架",
        "signal_types": ["同v11 + v14过滤"],
        "key_params": "v11框架 + v14过滤",
        "changes_from": "v11: 合并v14过滤",
    },
    "v17": {
        "name": "v17 — 极端费率+蓄势合并",
        "description": "合并v6极端费率(56笔/64%/+$688)和v14蓄势突破(42笔/52%/+$607)",
        "signal_types": ["极端负费率做多(主力)", "极端正费率做空", "蓄势突破", "pump_short"],
        "key_params": "杠杆3x / 持仓4 / 极端费率重启用",
        "changes_from": "v14: 合并v6信号+MAX_POS=4",
    },
    "v18": {
        "name": "v18 — v6微调(止损/单币上限/加仓)",
        "description": "基于v6微调3项: SL上限8% / 单币亏损上限$100 / SL<5%仓位×1.5",
        "signal_types": ["同v6(极端费率)"],
        "key_params": "SL≤8% / 单币亏$100暂停 / SL<5%仓位×1.5",
        "changes_from": "v6: 3项微调不改核心",
    },
}


def load_result(version):
    """加载回测结果"""
    path = os.path.join(DATA_DIR, f"backtest_{version}_result.json")
    if not os.path.exists(path):
        return None
    
    with open(path) as f:
        data = json.load(f)
    
    trades = data.get('trades', [])
    if not trades:
        return None
    
    # Calculate from trades
    n = len(trades)
    total_pnl = sum(t.get('pnl_usd', 0) for t in trades)
    wins = sum(1 for t in trades if t.get('pnl_usd', 0) > 0)
    wr = wins / n * 100 if n > 0 else 0
    
    # Calculate drawdown
    balance = data.get('initial_balance', 5000)
    peak = balance
    max_dd = 0
    for t in trades:
        balance += t.get('pnl_usd', 0)
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd:
            max_dd = dd
    
    # Monthly breakdown
    monthly = defaultdict(list)
    for t in trades:
        entry = t.get('entry_time', t.get('entry_date', ''))
        if entry:
            m = str(entry)[:7]  # "2025-11"
            monthly[m].append(t)
    
    monthly_detail = {}
    for m in sorted(monthly.keys()):
        ts = monthly[m]
        mpnl = sum(t.get('pnl_usd', 0) for t in ts)
        mw = sum(1 for t in ts if t.get('pnl_usd', 0) > 0)
        monthly_detail[m] = {
            'n': len(ts),
            'pnl': mpnl,
            'wr': mw / len(ts) * 100,
            'win': mpnl > 0
        }
    
    profit_months = sum(1 for m in monthly_detail.values() if m['win'])
    total_months = len(monthly_detail)
    
    return {
        'n': n,
        'pnl': total_pnl,
        'wr': wr,
        'dd': max_dd,
        'final_bal': balance,
        'profit_months': profit_months,
        'total_months': total_months,
        'monthly': monthly_detail
    }


def gen_strategy_md(version, meta, result):
    """生成策略文档"""
    lines = []
    lines.append(f"# 策略 {meta['name']}")
    lines.append(f"")
    lines.append(f"**状态**: archived")
    lines.append(f"**基于**: {meta['changes_from']}")
    lines.append(f"**数据文件**: data/backtest_{version}_result.json")
    lines.append(f"**脚本**: backtest_{version}.py")
    lines.append(f"")
    
    if result:
        lines.append(f"## 回测结果")
        lines.append(f"")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 交易数 | {result['n']}笔 |")
        lines.append(f"| 胜率 | {result['wr']:.1f}% |")
        lines.append(f"| PnL | {result['pnl']:+,.0f}U |")
        lines.append(f"| 最大回撤 | {result['dd']:.1f}% |")
        lines.append(f"| 最终余额 | {result['final_bal']:,.0f}U |")
        lines.append(f"| 月胜率 | {result['profit_months']}/{result['total_months']} |")
        lines.append(f"")
        
        if result['monthly']:
            lines.append(f"### 月度明细")
            lines.append(f"")
            for m, d in result['monthly'].items():
                icon = "📈" if d['win'] else "📉"
                lines.append(f"- {icon} {m}: {d['n']}笔 PnL={d['pnl']:+,.0f}U WR={d['wr']:.0f}%")
            lines.append(f"")
    
    lines.append(f"## 策略逻辑")
    lines.append(f"")
    lines.append(f"**描述**: {meta['description']}")
    lines.append(f"")
    lines.append(f"**信号类型**: {' / '.join(meta['signal_types'])}")
    lines.append(f"")
    lines.append(f"**核心参数**: {meta['key_params']}")
    lines.append(f"")
    
    return '\n'.join(lines)


# === Main ===
os.makedirs(STRAT_DIR, exist_ok=True)

# Generate all strategy docs
print("生成策略文档...")
registry_versions = []

for version, meta in sorted(STRATEGIES.items(), key=lambda x: x[0]):
    result = load_result(version)
    
    # Generate doc
    md = gen_strategy_md(version, meta, result)
    doc_path = os.path.join(STRAT_DIR, f"{version}_strategy.md")
    with open(doc_path, 'w') as f:
        f.write(md)
    
    # Registry entry
    pnl_str = f"{result['pnl']:+,.0f}" if result else "N/A"
    wr_str = f"{result['wr']:.1f}%" if result else "N/A"
    
    entry = {
        "name": meta['name'],
        "file": f"strategies/{version}_strategy.md",
        "script": f"backtest_{version}.py",
        "data": f"data/backtest_{version}_result.json",
        "result": f"{result['n']}笔/{wr_str}/{pnl_str}U/回撤{result['dd']:.1f}%" if result else "无数据",
        "status": "archived",
        "date": "2026-05-08",
        "changes_from": meta['changes_from']
    }
    registry_versions.append((version, entry))
    
    status_icon = "✅" if result and result['pnl'] > 0 else "❌"
    print(f"  {status_icon} {version:10s} {result['n']:>4d}笔  PnL={result['pnl']:+8,.0f}  WR={result['wr']:5.1f}%  DD={result['dd']:5.1f}%  {meta['name']}" if result else f"  ⚠️  {version:10s} 无回测数据")

# Write registry
registry = {
    "description": "策略版本注册表 — 每个版本独立保存，不覆盖，用字母后缀迭代",
    "versions": {v: e for v, e in registry_versions}
}

reg_path = os.path.join(DATA_DIR, "version_registry.json")
with open(reg_path, 'w') as f:
    json.dump(registry, f, indent=2, ensure_ascii=False)

print(f"\n✅ 注册表已更新: {reg_path}")
print(f"✅ 策略文档目录: {STRAT_DIR}/")

# Print summary table
print(f"\n{'='*80}")
print(f"{'版本':<10} {'笔数':>5} {'胜率':>7} {'PnL':>10} {'回撤':>7} {'余额':>9} 描述")
print(f"{'='*80}")

sorted_results = []
for version, meta in sorted(STRATEGIES.items(), key=lambda x: x[0]):
    result = load_result(version)
    if result:
        sorted_results.append((version, result, meta))

# Sort by PnL desc
sorted_results.sort(key=lambda x: x[1]['pnl'], reverse=True)

for version, r, meta in sorted_results:
    icon = "🟢" if r['pnl'] > 0 else "🔴"
    print(f"{icon} {version:<9} {r['n']:>4}笔 {r['wr']:>6.1f}% {r['pnl']:>+9,.0f}U {r['dd']:>6.1f}% {r['final_bal']:>8,.0f}U {meta['name']}")

print(f"\n🏆 赚钱策略: {sum(1 for _,r,_ in sorted_results if r['pnl']>0)}/{len(sorted_results)}")
print(f"💵 最佳PnL: {sorted_results[0][0]} ({sorted_results[0][1]['pnl']:+,.0f}U)")
print(f"🎯 最高胜率: {max(sorted_results, key=lambda x: x[1]['wr'])[0]} ({max(sorted_results, key=lambda x: x[1]['wr'])[1]['wr']:.1f}%)")
print(f"🛡️ 最低回撤: {min(sorted_results, key=lambda x: x[1]['dd'])[0]} ({min(sorted_results, key=lambda x: x[1]['dd'])[1]['dd']:.1f}%)")
