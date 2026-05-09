#!/usr/bin/env python3
"""
v11j Profile 系统回测对比: 1年 vs 全期
四个Profile: M40/D60/G60/L7
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
MAX_ATR_PCT = 5.0
FILTER_V8_RSI = True
CONSEC_LOSS_THRESHOLD = 2

# ═══ Profile 定义 ═══
PROFILES = {
    "M40": {"max_loss_per_trade": 40, "consec_loss_mult": 0.7, "max_sl_pct": 10.0},
    "D60": {"max_loss_per_trade": 60, "consec_loss_mult": 0.7, "max_sl_pct": 10.0},
    "G60": {"max_loss_per_trade": 60, "consec_loss_mult": 0.5, "max_sl_pct": 10.0},
    "L7":  {"max_loss_per_trade": None, "consec_loss_mult": 0.7, "max_sl_pct": 7.0},
}


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


def calc_mult(trade, consec_losses, consec_loss_mult=0.7):
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
        m *= consec_loss_mult

    return m


def simulate(trades, profile_name):
    """根据 Profile 参数运行回测"""
    pconf = PROFILES[profile_name]
    max_loss = pconf["max_loss_per_trade"]
    cl_mult = pconf["consec_loss_mult"]
    max_sl = pconf["max_sl_pct"]

    filtered = apply_hard_filters(trades, max_sl_pct=max_sl)

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
    capped_win_count = 0
    capped_loss_count = 0

    for t in filtered:
        v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
        base = SHORT_POSITION_FACTOR if (t["direction"] == "short" and v8 >= SHORT_V8_THRESHOLD) else 1.0
        mult = base * calc_mult(t, consec, consec_loss_mult=cl_mult)

        capped = False
        if max_loss is not None:
            pos_usd = t.get("position_usd", 0)
            sl_pct = t.get("signal_sl_pct", 0)
            lev = t.get("leverage", 3)
            est_risk = pos_usd * sl_pct * lev * mult
            if est_risk > max_loss:
                mult *= max_loss / est_risk
                capped = True

        pnl = t.get("pnl_usd", 0) * mult

        balance += pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            max_dd_peak = peak
            max_dd_trough = balance

        if pnl > 0:
            gross_profit += pnl
            if capped:
                capped_win_count += 1
        else:
            gross_loss += abs(pnl)
            if capped:
                capped_loss_count += 1

        at = {**t}
        at["pnl_usd"] = round(pnl, 2)
        at["position_mult"] = round(mult, 3)
        at["running_balance"] = round(balance, 2)
        at["capped"] = capped
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
    roi_pct = (balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100

    return {
        "profile": profile_name,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "total_pnl": round(balance - INITIAL_BALANCE, 2),
        "final_balance": round(balance, 2),
        "gross_profit_after_cap": round(gross_profit, 2),
        "gross_loss_after_cap": round(gross_loss, 2),
        "capped_win_count": capped_win_count,
        "capped_loss_count": capped_loss_count,
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_dd, 2),
        "max_dd_peak": round(max_dd_peak, 2),
        "max_dd_trough": round(max_dd_trough, 2),
        "profit_months": profit_months,
        "total_months": total_months,
        "monthly_win_rate": round(profit_months / total_months * 100, 1) if total_months else 0,
        "max_consec_loss": max_consec_loss,
        "max_single_loss": round(max_single_loss, 2),
        "roi": round(roi_pct, 1),
        "roi_dd_ratio": round(roi_pct / max_dd, 2) if max_dd > 0 else 0,
        "monthly_pnl": {k: round(v, 2) for k, v in monthly_pnl.items()},
    }


def main():
    if not V10_PATH.exists():
        print(f"❌ 数据文件不存在: {V10_PATH}")
        sys.exit(1)

    with open(V10_PATH) as f:
        raw = json.load(f)
    all_trades = raw if isinstance(raw, list) else raw.get("trades", [])
    print(f"v10原始数据: {len(all_trades)}笔")

    filtered = apply_v11_base_filter(all_trades)
    print(f"v11i过滤后: {len(filtered)}笔")

    # 时间分割
    one_year_cutoff = "2025-05-09"
    trades_1yr = [t for t in filtered if t.get("entry_time", "") >= one_year_cutoff]

    print(f"  近1年: {len(trades_1yr)}笔 | 全期: {len(filtered)}笔")

    # ═══ 四个 Profile 全期对比 ═══
    print(f"\n{'='*120}")
    print("v11j Profile 全期对比 (1000天)")
    print(f"{'='*120}")
    print(f"{'Profile':<8} {'笔数':>5} {'WR%':>6} {'PnL($)':>10} {'ROI%':>8} {'DD%':>7} {'PF':>5} {'月胜率':>6} {'ROI/DD':>7} {'最大连亏':>7} {'单笔最大亏':>10}")
    print("-" * 100)

    full_results = {}
    for pname in PROFILES:
        r = simulate(filtered, pname)
        if r:
            full_results[pname] = r
            print(
                f"{pname:<8} {r['total_trades']:>5} {r['win_rate']:>5.1f}% "
                f"{r['total_pnl']:>+10.0f} {r['roi']:>+8.1f} {r['max_drawdown']:>6.1f}% "
                f"{r['profit_factor']:>5.2f} {r['monthly_win_rate']:>5.1f}% "
                f"{r['roi_dd_ratio']:>7.2f} {r['max_consec_loss']:>7} {r['max_single_loss']:>+10.1f}"
            )

    # ═══ 四个 Profile 1年对比 ═══
    print(f"\n{'='*120}")
    print("v11j Profile 近1年对比")
    print(f"{'='*120}")
    print(f"{'Profile':<8} {'笔数':>5} {'WR%':>6} {'PnL($)':>10} {'ROI%':>8} {'DD%':>7} {'PF':>5} {'月胜率':>6} {'ROI/DD':>7} {'最大连亏':>7} {'单笔最大亏':>10}")
    print("-" * 100)

    yr_results = {}
    for pname in PROFILES:
        r = simulate(trades_1yr, pname)
        if r:
            yr_results[pname] = r
            print(
                f"{pname:<8} {r['total_trades']:>5} {r['win_rate']:>5.1f}% "
                f"{r['total_pnl']:>+10.0f} {r['roi']:>+8.1f} {r['max_drawdown']:>6.1f}% "
                f"{r['profit_factor']:>5.2f} {r['monthly_win_rate']:>5.1f}% "
                f"{r['roi_dd_ratio']:>7.2f} {r['max_consec_loss']:>7} {r['max_single_loss']:>+10.1f}"
            )

    # ═══ G60 vs D60 验证 ═══
    if "G60" in full_results and "D60" in full_results:
        g, d = full_results["G60"], full_results["D60"]
        print(f"\n📊 G60 vs D60 差异 (连亏×0.5贡献):")
        print(f"   PnL差: ${g['total_pnl']-d['total_pnl']:+.0f} | DD差: {g['max_drawdown']-d['max_drawdown']:+.1f}% | 连亏差: {g['max_consec_loss']-d['max_consec_loss']:+d}笔")

    # ═══ 保存 ═══
    out = {
        "version": "v11j-profile-compare",
        "full_period": full_results,
        "1year": yr_results,
    }
    out_path = DATA_DIR / "backtest_v11j_profiles.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 已保存: {out_path}")


if __name__ == "__main__":
    main()
