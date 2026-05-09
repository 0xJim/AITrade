#!/usr/bin/env python3
"""
v11j 方案M 回测对比: 1年 vs 1000天
方案M: 仅加单笔亏损上限$40 (其余v11i参数不变)
基于v10原始数据 + v11i过滤/仓位参数
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
V10_PATH = DATA_DIR / "backtest_v10_result.json"

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
CONSEC_LOSS_MULT = 0.7

# 方案M参数
MAX_LOSS_PER_TRADE = 40  # 单笔亏损上限$40


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
        m *= CONSEC_LOSS_MULT

    return m


def simulate(trades, max_loss_per_trade=MAX_LOSS_PER_TRADE):
    """方案M: 仅加单笔亏损上限"""
    filtered = apply_hard_filters(trades)

    balance = INITIAL_BALANCE
    peak = balance
    max_dd = 0
    max_dd_peak = balance
    max_dd_trough = balance
    consec = 0
    adj = []
    monthly = defaultdict(list)
    gross_profit = 0
    gross_loss = 0
    loss_cap_savings = 0

    for t in filtered:
        v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
        base = SHORT_POSITION_FACTOR if (t["direction"] == "short" and v8 >= SHORT_V8_THRESHOLD) else 1.0
        mult = base * calc_mult(t, consec)

        pnl = t.get("pnl_usd", 0) * mult

        # 单笔亏损上限
        if max_loss_per_trade is not None and pnl < 0 and abs(pnl) > max_loss_per_trade:
            loss_cap_savings += abs(pnl) - max_loss_per_trade
            pnl = -max_loss_per_trade

        balance += pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100 if peak > 0 else 0

        if dd > max_dd:
            max_dd = dd
            max_dd_peak = peak
            max_dd_trough = balance

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
    top_10_losses = sorted([t["pnl_usd"] for t in adj])[:10]

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
        "max_dd_peak": round(max_dd_peak, 2),
        "max_dd_trough": round(max_dd_trough, 2),
        "profit_months": profit_months,
        "total_months": total_months,
        "monthly_win_rate": round(profit_months / total_months * 100, 1) if total_months > 0 else 0,
        "max_consec_loss": max_consec_loss,
        "max_single_loss": round(max_single_loss, 2),
        "top_10_losses": [round(x, 2) for x in top_10_losses],
        "roi": round((balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100, 1),
        "roi_dd_ratio": round((balance - INITIAL_BALANCE) / INITIAL_BALANCE / max_dd * 100, 1) if max_dd > 0 else 0,
        "loss_cap_savings": round(loss_cap_savings, 2),
        "monthly_pnl": {k: round(v, 2) for k, v in monthly_pnl.items()},
    }


def main():
    print("加载v10原始数据...")
    with open(V10_PATH) as f:
        raw = json.load(f)
    all_trades = raw if isinstance(raw, list) else raw.get("trades", [])
    print(f"  原始交易: {len(all_trades)} 笔")

    # v11i基础过滤
    filtered = apply_v11_base_filter(all_trades)
    print(f"  v11i过滤后: {len(filtered)} 笔")

    # 按时间段分割
    one_year_cutoff = "2025-05-09"
    trades_1yr = [t for t in filtered if t.get("entry_time", "") >= one_year_cutoff]
    trades_1yr_early = [t for t in filtered if t.get("entry_time", "") < one_year_cutoff]

    print(f"\n时间段分割:")
    print(f"  1年 (2025-05-09~): {len(trades_1yr)} 笔")
    print(f"  前2年 (2023-08~2025-05): {len(trades_1yr_early)} 笔")
    print(f"  总计 (1000天): {len(filtered)} 笔")

    # 跑方案M
    print("\n" + "="*60)
    print("方案M: 单笔亏损上限$40")
    print("="*60)

    r_1yr = simulate(trades_1yr)
    r_1000d = simulate(filtered)

    print("\n┌─────────────────────────────────────────────────────┐")
    print("│           v11j 方案M 对比报告                        │")
    print("├──────────────────┬──────────────┬───────────────────-─┤")
    print("│ 指标             │ 1年          │ 1000天              │")
    print("├──────────────────┼──────────────┼─────────────────────┤")

    def row(label, v1, v2, fmt="{}"):
        print(f"│ {label:<16} │ {fmt.format(v1):<12} │ {fmt.format(v2):<19} │")

    row("交易笔数", r_1yr["total_trades"], r_1000d["total_trades"])
    row("胜率", f'{r_1yr["win_rate"]}%', f'{r_1000d["win_rate"]}%')
    row("总盈亏", f'${r_1yr["total_pnl"]}', f'${r_1000d["total_pnl"]}')
    row("最终余额", f'${r_1yr["final_balance"]}', f'${r_1000d["final_balance"]}')
    row("盈利因子PF", r_1yr["profit_factor"], r_1000d["profit_factor"])
    row("最大回撤", f'{r_1yr["max_drawdown"]}%', f'{r_1000d["max_drawdown"]}%')
    row("月胜率", f'{r_1yr["monthly_win_rate"]}%', f'{r_1000d["monthly_win_rate"]}%')
    row("盈利月/总月", f'{r_1yr["profit_months"]}/{r_1yr["total_months"]}',
        f'{r_1000d["profit_months"]}/{r_1000d["total_months"]}')
    row("ROI", f'{r_1yr["roi"]}%', f'{r_1000d["roi"]}%')
    row("ROI/DD比", r_1yr["roi_dd_ratio"], r_1000d["roi_dd_ratio"])
    row("单笔最大亏", f'${r_1yr["max_single_loss"]}', f'${r_1000d["max_single_loss"]}')
    row("最大连亏", r_1yr["max_consec_loss"], r_1000d["max_consec_loss"])
    row("亏损上限省", f'${r_1yr["loss_cap_savings"]}', f'${r_1000d["loss_cap_savings"]}')

    print("├──────────────────┴──────────────┴─────────────────────┤")

    # 月度对比
    print("│ 1年月度盈亏:                                         │")
    for m, p in sorted(r_1yr["monthly_pnl"].items()):
        flag = "✓" if p > 0 else "✗"
        print(f"│   {m}: ${p:>8} {flag}                                  │")

    print("│ 1000天前2年月度 (方案M前半段):                       │")
    # 计算前半段
    r_early = simulate(trades_1yr_early)
    if r_early:
        for m, p in sorted(r_early["monthly_pnl"].items()):
            flag = "✓" if p > 0 else "✗"
            print(f"│   {m}: ${p:>8} {flag}                                  │")
        print(f"│   前2年汇总: {r_early['total_trades']}笔/"
              f'{r_early["win_rate"]}%/'
              f'${r_early["total_pnl"]}/'
              f'DD{r_early["max_drawdown"]}%                          │')

    print("└──────────────────────────────────────────────────────┘")

    # 保存结果
    result = {
        "version": "v11j-compare",
        "scheme_M": {
            "config": {"max_loss_per_trade": 40, "desc": "单笔亏损上限$40"},
        },
        "1year": r_1yr,
        "1000days": r_1000d,
    }
    if r_early:
        result["first_2years"] = r_early

    out_path = DATA_DIR / "backtest_v11j_compare.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(f"\n结果已保存到 {out_path}")


if __name__ == "__main__":
    main()
