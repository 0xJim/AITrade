#!/usr/bin/env python3
"""
v11系列回测 — 1000U初始资金，真实余额曲线模拟

方法: 从v10原始信号出发，用1000U余额重新计算每笔仓位和盈亏。
- v10数据的pnl_pct(百分比收益)与仓位大小无关，可以直接复用
- position_usd按余额比例重新计算: pos_1000u = pos_5000u × (balance_1000u / balance_5000u)
- pnl_usd = position_usd × pnl_pct / 100

这样1000U和5000U的仓位比例一致（都是Kelly动态仓位），只是绝对值缩放。
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
V10_PATH = DATA_DIR / "backtest_v10_result.json"
INITIAL_BALANCE_1000 = 1000.0

# ═══ v11基础参数（所有版本共用） ═══
STATIC_BLACKLIST = {
    "ZECUSDT", "DUSDT", "NILUSDT", "SOLUSDT", "DOGEUSDT",
    "CLUSDT", "KSMUSDT", "HYPEUSDT", "TAOUSDT", "PUMPUSDT",
    "BZUSDT", "FILUSDT", "WLFIUSDT", "ONDOUSDT", "ENAUSDT",
}
V11_MIN_V8_SCORE = 4
SHORT_V8_THRESHOLD = 5
SHORT_POSITION_FACTOR = 0.5

# ═══ 各版本仓位参数 ═══
VERSIONS = {
    "v11": {
        "desc": "基础版: 黑名单+V8≥4+做空减半",
        "hard_filter": False,
        "params": {},
    },
    "v11g": {
        "desc": "仓位调整: V8反转+RSI+SL+连续亏损冷却",
        "hard_filter": True,
        "params": {
            "v8_low_threshold": 4.0, "v8_low_mult": 1.3,
            "v8_high_threshold": 6.5, "v8_high_mult_long": 0.6, "v8_high_mult_short": 0.6,
            "rsi_weak": 50, "rsi_weak_mult": 0.7,
            "rsi_mid_low": None, "rsi_mid_high": None, "rsi_mid_mult": None,
            "rsi_strong_low": 65, "rsi_strong_high": 75, "rsi_strong_mult": 1.2,
            "rsi_very_strong": 75, "rsi_very_strong_mult": 1.1,
            "sl_medium_low": 4.0, "sl_medium_high": 6.0, "sl_medium_mult": 0.65,
            "sl_wide_low": 8.0, "sl_wide_high": 10.0, "sl_wide_mult": 1.2,
            "max_sl_pct": 10.0, "max_atr_pct": 5.0, "filter_v8_rsi": True,
            "consec_loss_threshold": 2, "consec_loss_mult": 0.7,
        },
    },
    "v11h": {
        "desc": "v11g+4优化: RSI55-60×0.7+ATR5.5%+SKYAI×0.8+做空V8高×0.6",
        "hard_filter": True,
        "params": {
            "v8_low_threshold": 4.0, "v8_low_mult": 1.3,
            "v8_high_threshold": 6.5, "v8_high_mult_long": 0.6, "v8_high_mult_short": 0.6,
            "rsi_weak": 50, "rsi_weak_mult": 0.7,
            "rsi_mid_low": 55, "rsi_mid_high": 60, "rsi_mid_mult": 0.7,
            "rsi_strong_low": 65, "rsi_strong_high": 75, "rsi_strong_mult": 1.2,
            "rsi_very_strong": 75, "rsi_very_strong_mult": 1.1,
            "sl_medium_low": 4.0, "sl_medium_high": 6.0, "sl_medium_mult": 0.65,
            "sl_wide_low": 8.0, "sl_wide_high": 10.0, "sl_wide_mult": 1.2,
            "max_sl_pct": 10.0, "max_atr_pct": 5.5, "filter_v8_rsi": True,
            "consec_loss_threshold": 2, "consec_loss_mult": 0.7,
            "low_wr_symbols": {"SKYAIUSDT"}, "low_wr_mult": 0.8,
            "short_v8high_threshold": 6.5, "short_v8high_mult": 0.6,
        },
    },
    "v11i": {
        "desc": "v11g微调: V8≥6.5做多×0.8(原0.6)+RSI55-60×0.4(原无)",
        "hard_filter": True,
        "params": {
            "v8_low_threshold": 4.0, "v8_low_mult": 1.3,
            "v8_high_threshold": 6.5, "v8_high_mult_long": 0.8, "v8_high_mult_short": 0.6,
            "rsi_weak": 50, "rsi_weak_mult": 0.7,
            "rsi_mid_low": 55, "rsi_mid_high": 60, "rsi_mid_mult": 0.4,
            "rsi_strong_low": 65, "rsi_strong_high": 75, "rsi_strong_mult": 1.2,
            "rsi_very_strong": 75, "rsi_very_strong_mult": 1.1,
            "sl_medium_low": 4.0, "sl_medium_high": 6.0, "sl_medium_mult": 0.65,
            "sl_wide_low": 8.0, "sl_wide_high": 10.0, "sl_wide_mult": 1.2,
            "max_sl_pct": 10.0, "max_atr_pct": 5.0, "filter_v8_rsi": True,
            "consec_loss_threshold": 2, "consec_loss_mult": 0.7,
        },
    },
}


def get_rsi(t):
    ts = t.get("tech_snapshot", {})
    return ts.get("rsi") if isinstance(ts, dict) else None

def get_atr_pct(t):
    ts = t.get("tech_snapshot", {})
    return ts.get("atr_pct") if isinstance(ts, dict) else None


def apply_v11_base_filter(trades):
    return [t for t in trades
            if t["symbol"] not in STATIC_BLACKLIST
            and (t.get("v8_score", 0) or t.get("v8_quality", 0)) >= V11_MIN_V8_SCORE]


def apply_hard_filters(trades, params):
    kept = []
    for t in trades:
        sl_pct = t.get("signal_sl_pct", 0) * 100
        if sl_pct > params["max_sl_pct"]:
            continue
        atr = get_atr_pct(t)
        if atr is not None and atr * 100 > params["max_atr_pct"]:
            continue
        if params["filter_v8_rsi"] and t["direction"] == "long":
            v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
            rsi = get_rsi(t)
            if v8 >= params["v8_high_threshold"] and rsi is not None and rsi < 55:
                continue
        kept.append(t)
    return kept


def calc_mult(trade, consec_losses, params):
    m = 1.0
    v8 = trade.get("v8_score", 0) or trade.get("v8_quality", 0)
    rsi = get_rsi(trade)
    sl_pct = trade.get("signal_sl_pct", 0) * 100
    d = trade["direction"]

    if v8 <= params["v8_low_threshold"]:
        m *= params["v8_low_mult"]
    elif v8 >= params["v8_high_threshold"]:
        m *= params["v8_high_mult_long"] if d == "long" else params["v8_high_mult_short"]

    if d == "long" and rsi is not None:
        if rsi < params["rsi_weak"]:
            m *= params["rsi_weak_mult"]
        elif params.get("rsi_mid_low") is not None and params["rsi_mid_low"] <= rsi < params["rsi_mid_high"]:
            m *= params["rsi_mid_mult"]
        elif params["rsi_strong_low"] <= rsi <= params["rsi_strong_high"]:
            m *= params["rsi_strong_mult"]
        elif rsi >= params["rsi_very_strong"]:
            m *= params["rsi_very_strong_mult"]

    if params["sl_medium_low"] <= sl_pct <= params["sl_medium_high"]:
        m *= params["sl_medium_mult"]
    elif params["sl_wide_low"] <= sl_pct <= params["sl_wide_high"]:
        m *= params["sl_wide_mult"]

    if consec_losses >= params["consec_loss_threshold"]:
        m *= params["consec_loss_mult"]

    if "low_wr_symbols" in params and trade["symbol"] in params["low_wr_symbols"]:
        m *= params["low_wr_mult"]

    if "short_v8high_threshold" in params:
        if d == "short" and v8 >= params["short_v8high_threshold"]:
            m *= params["short_v8high_mult"]

    return m


def simulate_1000u(trades, params, use_position_mult):
    """
    真实1000U余额曲线模拟。
    
    核心逻辑:
    - v10数据的pnl_pct(%)与仓位大小无关，反映的是杠杆后的价格变动百分比
    - v10数据的position_usd是基于5000U余额的Kelly仓位
    - 我们用1000U余额，按相同比例计算position_usd:
      scale = balance_1000u / balance_5000u (动态)
      pos_1000u = pos_5000u × scale
      pnl_usd_1000u = pos_1000u × pnl_pct / 100
    - 再叠加v11系列的仓位调整系数mult
    
    等效简化:
      pnl_usd_1000u = pnl_usd_5000u × scale × mult
    其中scale随余额动态变化，但因为仓位是余额的固定百分比，
    scale在任意时刻都等于 balance_1000u / balance_5000u。
    """
    bal_5000 = 5000.0  # 追踪5000U余额曲线（用于计算scale）
    bal_1000 = 1000.0  # 1000U余额曲线
    peak = 1000.0
    max_dd = 0
    consec = 0
    gross_profit = 0.0
    gross_loss = 0.0
    monthly = defaultdict(list)
    daily = defaultdict(list)

    for t in trades:
        v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
        base = SHORT_POSITION_FACTOR if (t["direction"] == "short" and v8 >= SHORT_V8_THRESHOLD) else 1.0

        if use_position_mult:
            mult = base * calc_mult(t, consec, params)
        else:
            mult = base

        # 原始5000U下的pnl
        orig_pnl_5000 = t.get("pnl_usd", 0)
        
        # 按当前余额比例缩放: 1000U下的仓位 = 5000U下的仓位 × (bal_1000 / bal_5000)
        # pnl也等比缩放
        scale = bal_1000 / bal_5000 if bal_5000 > 0 else 0.2
        
        # 计算新的position_usd和pnl
        new_pos_usd = t.get("position_usd", 0) * scale
        new_pnl_raw = orig_pnl_5000 * scale  # 不含v11仓位调整
        new_pnl = new_pnl_raw * mult  # 含v11仓位调整
        
        # 更新5000U余额（用原始pnl，不含v11调整）
        bal_5000 += orig_pnl_5000
        
        # 更新1000U余额（用缩放后的pnl，含v11调整）
        bal_1000 += new_pnl
        
        peak = max(peak, bal_1000)
        dd = (peak - bal_1000) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

        if new_pnl > 0:
            gross_profit += new_pnl
        else:
            gross_loss += abs(new_pnl)

        month = t["entry_time"][:7]
        day = t["entry_time"][:10]
        monthly[month].append({
            "symbol": t["symbol"], "direction": t["direction"],
            "pnl_usd": round(new_pnl, 2), "pnl_pct": t.get("pnl_pct", 0),
            "position_usd": round(new_pos_usd, 2), "position_mult": round(mult, 3),
        })
        daily[day].append({"pnl_usd": round(new_pnl, 2)})

        consec = consec + 1 if new_pnl < 0 else 0

    total = len(trades)
    wins = sum(1 for ts in monthly.values() for t in ts if t["pnl_usd"] > 0)
    wr = wins / total * 100 if total else 0
    pf = gross_profit / gross_loss if gross_loss > 0 else 99
    profit_months = sum(1 for ts in monthly.values() if sum(t["pnl_usd"] for t in ts) > 0)

    # 日度统计
    profit_days = sum(1 for ts in daily.values() if sum(t["pnl_usd"] for t in ts) > 0)
    total_days = len(daily)

    return {
        "initial_balance": INITIAL_BALANCE_1000,
        "final_balance": round(bal_1000, 2),
        "total_pnl": round(bal_1000 - INITIAL_BALANCE_1000, 2),
        "total_trades": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": round(wr, 1),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(pf, 2),
        "max_drawdown": round(max_dd, 2),
        "profit_months": profit_months,
        "total_months": len(monthly),
        "profit_days": profit_days,
        "total_days": total_days,
        "monthly": dict(monthly),
    }


def main():
    if not V10_PATH.exists():
        print(f"❌ 未找到v10数据: {V10_PATH}")
        sys.exit(1)

    with open(V10_PATH) as f:
        raw = json.load(f).get("trades", [])
    
    print(f"{'='*70}")
    print(f"📊 v11系列回测 — 1000U初始资金，动态余额模拟")
    print(f"{'='*70}")
    print(f"v10原始数据: {len(raw)}笔 (基于5000U+3x杠杆)")
    print(f"目标: 1000U初始资金，按余额比例动态重算仓位和盈亏")
    print()

    base_filtered = apply_v11_base_filter(raw)
    print(f"v11基础过滤后: {len(base_filtered)}笔\n")

    results = {}

    for ver_name, ver_cfg in VERSIONS.items():
        params = ver_cfg["params"]
        print(f"{'─'*70}")
        print(f"📦 {ver_name}: {ver_cfg['desc']}")
        print(f"{'─'*70}")

        if ver_cfg["hard_filter"] and params:
            filtered = apply_hard_filters(base_filtered, params)
            print(f"  硬过滤后: {len(filtered)}笔")
        else:
            filtered = base_filtered
            print(f"  无硬过滤: {len(filtered)}笔")

        use_mult = ver_name != "v11"
        result = simulate_1000u(filtered, params if params else {}, use_mult)
        results[ver_name] = result

        print(f"  交易:     {result['total_trades']}笔")
        print(f"  胜率:     {result['win_rate']}% ({result['wins']}W/{result['losses']}L)")
        print(f"  PnL:      {result['total_pnl']:+.2f}U")
        print(f"  最终余额: {result['final_balance']:.2f}U")
        print(f"  收益率:   {result['total_pnl']/10:.1f}%")
        print(f"  最大回撤: {result['max_drawdown']}%")
        print(f"  盈亏比PF: {result['profit_factor']}")
        print(f"  盈利月:   {result['profit_months']}/{result['total_months']}")
        print(f"  盈利天:   {result['profit_days']}/{result['total_days']}")
        print(f"  毛利:     +{result['gross_profit']:.2f}U / 毛亏: -{result['gross_loss']:.2f}U")

        print(f"\n  月度明细:")
        for m in sorted(result["monthly"].keys()):
            ts = result["monthly"][m]
            mpnl = sum(t["pnl_usd"] for t in ts)
            mw = sum(1 for t in ts if t["pnl_usd"] > 0)
            icon = "📈" if mpnl > 0 else "📉"
            print(f"    {icon} {m}: {len(ts):>3}笔 {mpnl:>+8.1f}U WR={mw/len(ts)*100:>4.0f}%")
        print()

    # ═══ 汇总对比 ═══
    print(f"\n{'='*70}")
    print(f"📊 v11系列回测对比 — 1000U初始资金 (动态余额模拟)")
    print(f"{'='*70}")
    print(f"{'版本':<8} {'笔数':<6} {'胜率':<8} {'PnL':<11} {'收益':<8} {'回撤':<7} {'PF':<6} {'月盈/总'}")
    print(f"{'─'*8} {'─'*6} {'─'*8} {'─'*11} {'─'*8} {'─'*7} {'─'*6} {'─'*8}")
    for ver in ["v11", "v11g", "v11h", "v11i"]:
        r = results[ver]
        print(f"{ver:<8} {r['total_trades']:<6} {r['win_rate']:>5.1f}%  "
              f"{r['total_pnl']:>+8.1f}U  {r['total_pnl']/10:>+6.1f}% "
              f"{r['max_drawdown']:>5.1f}%  {r['profit_factor']:<6.2f} "
              f"{r['profit_months']}/{r['total_months']}")

    # ═══ 增量对比 ═══
    print(f"\n{'='*70}")
    print(f"📊 版本间增量改进")
    print(f"{'='*70}")
    prev = None
    for ver in ["v11", "v11g", "v11h", "v11i"]:
        r = results[ver]
        if prev:
            delta_pnl = r['total_pnl'] - prev['total_pnl']
            delta_dd = r['max_drawdown'] - prev['max_drawdown']
            delta_pf = r['profit_factor'] - prev['profit_factor']
            print(f"  {prev_name}→{ver}: PnL {delta_pnl:>+7.1f}U | 回撤 {delta_dd:>+5.1f}% | PF {delta_pf:>+5.2f}")
        prev = r
        prev_name = ver

    # 保存
    out_path = DATA_DIR / "backtest_v11_compare_1000u.json"
    out = {
        "version": "v11_compare_1000u_v2",
        "method": "动态余额模拟: position_usd按bal_1000/bal_5000缩放",
        "source": str(V10_PATH),
        "results": {
            ver: {k: v for k, v in r.items() if k != "monthly"}
            for ver, r in results.items()
        },
    }
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\n💾 结果已保存: {out_path}")


if __name__ == "__main__":
    main()
