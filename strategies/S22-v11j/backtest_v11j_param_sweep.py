#!/usr/bin/env python3
"""
v11j 参数扫描 — MAX_LOSS_PER_TRADE 和 equity_pct_loss_cap 组合
基于 v10 原始数据，测试不同参数组合的效果

MAX_LOSS_PER_TRADE: 30, 40, 50, 60 (单笔亏损上限 USDT)
equity_pct_loss_cap: 0.6%, 0.8%, 1.0% (按当前权益计算的单笔风险上限)

输出对比表，按 ROI/DD、PF、月胜率、最大单亏排序
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
V10_PATH = DATA_DIR / "backtest_v10_result.json"
if not V10_PATH.exists():
    V10_PATH = Path(__file__).parents[1] / "S07-v10" / "backtest_v10_result.json"

# ═══ v11i 基础参数 ═══
STATIC_BLACKLIST = {
    "ZECUSDT", "DUSDT", "NILUSDT", "SOLUSDT", "DOGEUSDT",
    "CLUSDT", "KSMUSDT", "HYPEUSDT", "TAOUSDT", "PUMPUSDT",
    "BZUSDT", "FILUSDT", "WLFIUSDT", "ONDOUSDT", "ENAUSDT",
}
V11_MIN_V8_SCORE = 4
SHORT_V8_THRESHOLD = 5
SHORT_POSITION_FACTOR = 0.5
INITIAL_BALANCE = 1000.0

# v11i仓位参数
V8_HIGH_THRESHOLD = 6.5
V8_HIGH_MULT_LONG = 0.8
V8_LOW_MULT_LONG = 1.3
V8_LOW_MULT_SHORT = 1.3
RSI_WEAK = 50
RSI_WEAK_MULT = 0.7
RSI_MID_LOW = 55
RSI_MID_HIGH = 60
RSI_MID_MULT = 0.4
RSI_STRONG_LOW = 65
RSI_STRONG_HIGH = 75
RSI_STRONG_MULT = 1.2
RSI_VERY_STRONG = 75
RSI_VERY_STRONG_MULT = 1.1
SL_MEDIUM_LOW = 4.0
SL_MEDIUM_HIGH = 6.0
SL_MEDIUM_MULT = 0.65
SL_WIDE_LOW = 8.0
SL_WIDE_HIGH = 10.0
SL_WIDE_MULT = 1.2
MAX_SL_PCT_BASE = 10.0
MAX_ATR_PCT = 5.0
FILTER_V8_RSI = True
CONSEC_LOSS_THRESHOLD = 2
CONSEC_LOSS_MULT_BASE = 0.7


def get_rsi(t):
    ts = t.get("tech_snapshot", {})
    return ts.get("rsi") if isinstance(ts, dict) else None


def get_atr_pct(t):
    ts = t.get("tech_snapshot", {})
    return ts.get("atr_pct") if isinstance(ts, dict) else None


def apply_v11_base_filter(trades):
    kept = []
    for t in trades:
        if t["symbol"] in STATIC_BLACKLIST:
            continue
        v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
        if v8 < V11_MIN_V8_SCORE:
            continue
        kept.append(t)
    return kept


def apply_hard_filters(trades, max_sl_pct=10.0):
    kept = []
    for t in trades:
        sl_pct = t.get("signal_sl_pct", 0) * 100
        if sl_pct > max_sl_pct:
            continue
        atr = get_atr_pct(t)
        if atr is not None and atr * 100 > MAX_ATR_PCT:
            continue
        if FILTER_V8_RSI and t["direction"] == "long":
            v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
            rsi = get_rsi(t)
            if v8 >= V8_HIGH_THRESHOLD and rsi is not None and rsi < 55:
                continue
        kept.append(t)
    return kept


def calc_mult(trade, consec_losses):
    m = 1.0
    v8 = trade.get("v8_score", 0) or trade.get("v8_quality", 0)
    rsi = get_rsi(trade)
    sl_pct = trade.get("signal_sl_pct", 0) * 100
    d = trade["direction"]

    if v8 <= 4:
        m *= V8_LOW_MULT_LONG if d == "long" else V8_LOW_MULT_SHORT
    elif v8 >= V8_HIGH_THRESHOLD:
        m *= V8_HIGH_MULT_LONG if d == "long" else 0.6

    if d == "long" and rsi is not None:
        if rsi < RSI_WEAK:
            m *= RSI_WEAK_MULT
        elif RSI_MID_LOW <= rsi < RSI_MID_HIGH:
            m *= RSI_MID_MULT
        elif RSI_STRONG_LOW <= rsi <= RSI_STRONG_HIGH:
            m *= RSI_STRONG_MULT
        elif rsi >= RSI_VERY_STRONG:
            m *= RSI_VERY_STRONG_MULT

    if SL_MEDIUM_LOW <= sl_pct <= SL_MEDIUM_HIGH:
        m *= SL_MEDIUM_MULT
    elif SL_WIDE_LOW <= sl_pct <= SL_WIDE_HIGH:
        m *= SL_WIDE_MULT

    if consec_losses >= CONSEC_LOSS_THRESHOLD:
        m *= CONSEC_LOSS_MULT_BASE

    return m


def simulate(trades, max_loss_per_trade=40, equity_pct_loss_cap=None):
    """
    模拟交易
    max_loss_per_trade: 单笔最大亏损 (USDT)
    equity_pct_loss_cap: 当前权益百分比风险上限 (如 0.8 表示每笔最多风险权益0.8%)
    """
    filtered = apply_hard_filters(trades)

    balance = INITIAL_BALANCE
    peak = balance
    max_dd = 0
    consec = 0
    adj = []
    monthly = defaultdict(list)
    gross_profit = 0
    gross_loss = 0
    loss_cap_savings = 0
    capped_win_count = 0
    capped_loss_count = 0

    for t in filtered:
        v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
        base = SHORT_POSITION_FACTOR if (t["direction"] == "short" and v8 >= SHORT_V8_THRESHOLD) else 1.0
        mult = base * calc_mult(t, consec)

        raw_pnl = t.get("pnl_usd", 0)

        # 单笔风险上限: 开仓前估算风险并缩仓，盈亏同倍数缩放。
        # 固定USDT上限与权益百分比上限同时存在时取更小值。
        effective_cap = None
        if max_loss_per_trade is not None:
            effective_cap = max_loss_per_trade
        if equity_pct_loss_cap is not None:
            equity_cap = balance * equity_pct_loss_cap / 100
            effective_cap = equity_cap if effective_cap is None else min(effective_cap, equity_cap)

        if effective_cap is not None:
            pos_usd = t.get("position_usd", 0)
            sl_pct = t.get("signal_sl_pct", 0)
            lev = t.get("leverage", 3)
            est_risk = pos_usd * sl_pct * lev * mult
            if est_risk > effective_cap:
                shrink_ratio = effective_cap / est_risk
                loss_cap_savings += est_risk - effective_cap
                mult *= shrink_ratio
                if raw_pnl > 0:
                    capped_win_count += 1
                elif raw_pnl < 0:
                    capped_loss_count += 1

        pnl = raw_pnl * mult

        balance += pnl

        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100 if peak > 0 else 0

        if dd > max_dd:
            max_dd = dd

        if pnl > 0:
            gross_profit += pnl
        else:
            gross_loss += abs(pnl)

        at = {**t}
        at["pnl_usd"] = round(pnl, 2)
        at["position_mult"] = round(mult, 3)
        at["running_balance"] = round(balance, 2)
        adj.append(at)
        monthly[t["entry_time"][:7]].append(at)

        consec = consec + 1 if pnl < 0 else 0

    total_trades = len(adj)
    if total_trades == 0:
        return None

    wins = sum(1 for t in adj if t["pnl_usd"] > 0)
    losses = total_trades - wins
    win_rate = wins / total_trades * 100
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 99

    monthly_pnl = {}
    for m, ts in sorted(monthly.items()):
        monthly_pnl[m] = sum(t["pnl_usd"] for t in ts)

    profit_months = sum(1 for p in monthly_pnl.values() if p > 0)
    total_months = len(monthly_pnl)

    max_consec_loss = 0
    current_consec = 0
    for t in adj:
        if t["pnl_usd"] < 0:
            current_consec += 1
            max_consec_loss = max(max_consec_loss, current_consec)
        else:
            current_consec = 0

    max_single_loss = min(t["pnl_usd"] for t in adj)

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "total_pnl": round(balance - INITIAL_BALANCE, 2),
        "final_balance": round(balance, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_dd, 2),
        "profit_months": profit_months,
        "total_months": total_months,
        "monthly_win_rate": round(profit_months / total_months * 100, 1) if total_months > 0 else 0,
        "max_consec_loss": max_consec_loss,
        "max_single_loss": round(max_single_loss, 2),
        "roi": round((balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100, 1),
        "roi_dd_ratio": round(((balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100) / max_dd, 2) if max_dd > 0 else 0,
        "loss_cap_savings": round(loss_cap_savings, 2),
        "capped_win_count": capped_win_count,
        "capped_loss_count": capped_loss_count,
        "gross_profit_after_cap": round(gross_profit, 2),
        "gross_loss_after_cap": round(gross_loss, 2),
        "monthly_pnl": {k: round(v, 2) for k, v in monthly_pnl.items()},
    }


def main():
    print("加载v10原始数据...")
    with open(V10_PATH) as f:
        raw = json.load(f)
    all_trades = raw if isinstance(raw, list) else raw.get("trades", [])
    print(f"  原始交易: {len(all_trades)} 笔")

    # v11i基础过滤
    base = apply_v11_base_filter(all_trades)
    print(f"v11i基础过滤后: {len(base)} 笔")

    # ═══ 参数组合 ═══
    max_loss_values = [30, 40, 50, 60]
    equity_cap_values = [None, 0.6, 0.8, 1.0]  # None 表示不限制

    results = {}

    print("\n" + "=" * 80)
    print("v11j 参数扫描 — MAX_LOSS_PER_TRADE × equity_pct_loss_cap(单笔权益风险)")
    print("=" * 80)

    for max_loss in max_loss_values:
        for equity_cap in equity_cap_values:
            key = f"max_loss={max_loss}"
            if equity_cap is not None:
                key += f"_cap={equity_cap}%"

            print(f"\n扫描 {key}...")
            r = simulate(base, max_loss_per_trade=max_loss, equity_pct_loss_cap=equity_cap)
            if r:
                r["config"] = {
                    "max_loss_per_trade": max_loss,
                    "equity_pct_loss_cap": equity_cap,
                }
                results[key] = r

    # ═══ 输出对比表 ═══
    print("\n" + "=" * 120)
    print(f"{'方案':<25} {'笔数':>5} {'WR%':>6} {'PnL($)':>10} {'ROI%':>8} {'DD%':>7} {'PF':>5} {'月胜率':>7} {'ROI/DD':>8} {'最大连亏':>7} {'单笔最大亏':>10}")
    print("-" * 120)

    # 按ROI/DD排序
    sorted_results = sorted(results.items(), key=lambda x: x[1]["roi_dd_ratio"], reverse=True)

    for key, r in sorted_results:
        max_loss = r["config"]["max_loss_per_trade"]
        equity_cap = r["config"]["equity_pct_loss_cap"]
        label = f"L${max_loss}"
        if equity_cap is not None:
            label += f" E{equity_cap}%"

        print(
            f"{label:<25} "
            f"{r['total_trades']:>5} "
            f"{r['win_rate']:>5.1f}% "
            f"{r['total_pnl']:>+10.0f} "
            f"{r['roi']:>+8.1f} "
            f"{r['max_drawdown']:>6.1f}% "
            f"{r['profit_factor']:>5.2f} "
            f"{r['monthly_win_rate']:>6.1f}% "
            f"{r['roi_dd_ratio']:>8.2f} "
            f"{r['max_consec_loss']:>7} "
            f"${r['max_single_loss']:>+9.1f}"
        )

    # ═══ Top 5 详细分析 ═══
    print("\n" + "=" * 120)
    print("Top 5 最优方案 (按ROI/DD排序)")
    print("=" * 120)

    for i, (key, r) in enumerate(sorted_results[:5], 1):
        max_loss = r["config"]["max_loss_per_trade"]
        equity_cap = r["config"]["equity_pct_loss_cap"]
        label = f"L${max_loss}"
        if equity_cap is not None:
            label += f" E{equity_cap}%"

        print(f"\n  #{i} {label}")
        print(f"      PnL: +${r['total_pnl']:.0f} | ROI: {r['roi']:.1f}% | DD: {r['max_drawdown']:.1f}% | ROI/DD: {r['roi_dd_ratio']:.2f}")
        print(f"      WR: {r['win_rate']:.1f}% ({r['wins']}W/{r['losses']}L) | PF: {r['profit_factor']:.2f}")
        print(f"      月胜率: {r['monthly_win_rate']:.1f}% ({r['profit_months']}/{r['total_months']}) | 最大连亏: {r['max_consec_loss']}笔")
        print(f"      单笔最大亏: ${r['max_single_loss']:.1f} | 亏损上限节省: ${r['loss_cap_savings']:.2f}")

    # ═══ 按不同指标排序的对比 ═══
    print("\n" + "=" * 120)
    print("按不同指标排序的 Top 3")
    print("=" * 120)

    # 按PF排序
    pf_sorted = sorted(results.items(), key=lambda x: x[1]["profit_factor"], reverse=True)
    print("\n按盈利因子PF排序:")
    for i, (key, r) in enumerate(pf_sorted[:3], 1):
        max_loss = r["config"]["max_loss_per_trade"]
        equity_cap = r["config"]["equity_pct_loss_cap"]
        label = f"L${max_loss}"
        if equity_cap is not None:
            label += f" E{equity_cap}%"
        print(f"  #{i} {label}: PF={r['profit_factor']:.2f} ROI/DD={r['roi_dd_ratio']:.2f}")

    # 按月胜率排序
    wr_sorted = sorted(results.items(), key=lambda x: x[1]["monthly_win_rate"], reverse=True)
    print("\n按月胜率排序:")
    for i, (key, r) in enumerate(wr_sorted[:3], 1):
        max_loss = r["config"]["max_loss_per_trade"]
        equity_cap = r["config"]["equity_pct_loss_cap"]
        label = f"L${max_loss}"
        if equity_cap is not None:
            label += f" E{equity_cap}%"
        print(f"  #{i} {label}: 月胜率={r['monthly_win_rate']:.1f}% ROI/DD={r['roi_dd_ratio']:.2f}")

    # 按最大单亏排序（越小越好）
    loss_sorted = sorted(results.items(), key=lambda x: x[1]["max_single_loss"], reverse=True)
    print("\n按最大单亏排序（绝对值越小越好）:")
    for i, (key, r) in enumerate(loss_sorted[:3], 1):
        max_loss = r["config"]["max_loss_per_trade"]
        equity_cap = r["config"]["equity_pct_loss_cap"]
        label = f"L${max_loss}"
        if equity_cap is not None:
            label += f" E{equity_cap}%"
        print(f"  #{i} {label}: 最大单亏=${r['max_single_loss']:.1f} ROI/DD={r['roi_dd_ratio']:.2f}")

    # ═══ 保存结果 ═══
    out = {
        "version": "v11j-param-sweep",
        "description": "v11j参数扫描: MAX_LOSS_PER_TRADE × equity_pct_loss_cap(单笔权益风险)",
        "results": {k: {"config": v["config"], **{kk: vv for kk, vv in v.items() if kk != "config" and kk != "monthly_pnl"}}
                     for k, v in results.items()},
    }

    out_path = Path(__file__).parent / "backtest_v11j_param_sweep.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 已保存: {out_path}")
    print("=" * 120)


if __name__ == "__main__":
    main()
