#!/usr/bin/env python3
"""
v11j Profile 回测对比
基于v10原始数据 → v11i参数 → 4个策略Profile

Profile定位:
- M40 = 保守风控挡 (默认，适合 testnet 冷启动)
- G60 = 下一阶段 testnet 主测方案 (收益/风控平衡)
- L7  = 研究基准 (SL 过滤因子，不直接裸上)
- D60 = 对照组 (判断连亏减仓是否有效)

下一步: testnet 跑 G60 至少 7 天，验证执行质量后再考虑实盘。
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
MAX_ATR_PCT = 5.0
FILTER_V8_RSI = True
CONSEC_LOSS_THRESHOLD = 2

# ═══ 4个策略 Profile ═══
STRATEGY_PROFILES = {
    "M40": {
        "max_sl_pct": 10.0,
        "consec_loss_mult": 0.7,
        "max_loss_per_trade": 40.0,
        "desc": "保守风控挡",
    },
    "D60": {
        "max_sl_pct": 10.0,
        "consec_loss_mult": 0.7,
        "max_loss_per_trade": 60.0,
        "desc": "对照组(仅$60上限)",
    },
    "G60": {
        "max_sl_pct": 10.0,
        "consec_loss_mult": 0.5,
        "max_loss_per_trade": 60.0,
        "desc": "收益风控平衡挡 ★testnet主测",
    },
    "L7": {
        "max_sl_pct": 7.0,
        "consec_loss_mult": 0.7,
        "max_loss_per_trade": None,
        "desc": "研究基准(SL≤7%)",
    },
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

    # V8区间
    if v8 <= 4:
        m *= V8_LOW_MULT_LONG if d == "long" else V8_LOW_MULT_SHORT
    elif v8 >= V8_HIGH_THRESHOLD:
        m *= V8_HIGH_MULT_LONG if d == "long" else 0.6

    # RSI (仅做多)
    if d == "long" and rsi is not None:
        if rsi < RSI_WEAK:
            m *= RSI_WEAK_MULT
        elif RSI_MID_LOW <= rsi < RSI_MID_HIGH:
            m *= RSI_MID_MULT
        elif RSI_STRONG_LOW <= rsi <= RSI_STRONG_HIGH:
            m *= RSI_STRONG_MULT
        elif rsi >= RSI_VERY_STRONG:
            m *= RSI_VERY_STRONG_MULT

    # SL%
    if SL_MEDIUM_LOW <= sl_pct <= SL_MEDIUM_HIGH:
        m *= SL_MEDIUM_MULT
    elif SL_WIDE_LOW <= sl_pct <= SL_WIDE_HIGH:
        m *= SL_WIDE_MULT

    # 连续亏损
    if consec_losses >= CONSEC_LOSS_THRESHOLD:
        m *= consec_loss_mult

    return m


def simulate(trades, config):
    """
    config: {
        max_sl_pct: float,        # SL硬过滤上限
        consec_loss_mult: float,  # 连亏减仓倍率
        max_loss_per_trade: float,# 单笔亏损上限($)
    }
    """
    max_sl = config.get("max_sl_pct", 10.0)
    cl_mult = config.get("consec_loss_mult", 0.7)
    max_loss = config.get("max_loss_per_trade", None)

    # 先做硬过滤
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
    skipped_by_loss_cap = 0
    loss_cap_savings = 0
    capped_win_count = 0
    capped_loss_count = 0

    for t in filtered:
        v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
        base = SHORT_POSITION_FACTOR if (t["direction"] == "short" and v8 >= SHORT_V8_THRESHOLD) else 1.0
        mult = base * calc_mult(t, consec, consec_loss_mult=cl_mult)

        raw_pnl = t.get("pnl_usd", 0)

        # 开仓前缩仓: 根据开仓时已知风险估算缩放仓位，盈亏同倍数缩放。
        if max_loss is not None:
            pos_usd = t.get("position_usd", 0)
            sl_pct = t.get("signal_sl_pct", 0)
            lev = t.get("leverage", 3)
            est_risk = pos_usd * sl_pct * lev * mult
            if est_risk > max_loss:
                shrink_ratio = max_loss / est_risk
                loss_cap_savings += est_risk - max_loss
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

    # 月度统计
    monthly_pnl = {}
    for m, ts in sorted(monthly.items()):
        mp = sum(t["pnl_usd"] for t in ts)
        monthly_pnl[m] = mp

    profit_months = sum(1 for p in monthly_pnl.values() if p > 0)
    total_months = len(monthly_pnl)

    # 最大连续亏损
    max_consec_loss = 0
    current_consec = 0
    for t in adj:
        if t["pnl_usd"] < 0:
            current_consec += 1
            max_consec_loss = max(max_consec_loss, current_consec)
        else:
            current_consec = 0

    # 单笔最大亏损
    max_single_loss = min(t["pnl_usd"] for t in adj) if adj else 0

    # 前10大亏损
    top_losses = sorted([t["pnl_usd"] for t in adj])[:10]

    # 按季度统计
    quarterly = defaultdict(list)
    for t in adj:
        q = t["entry_time"][:7]
        year, month = q.split("-")
        qkey = f"{year}-Q{(int(month)-1)//3+1}"
        quarterly[qkey].append(t["pnl_usd"])

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
        "monthly_win_rate": round(profit_months / total_months * 100, 1) if total_months else 0,
        "max_consec_loss": max_consec_loss,
        "max_single_loss": round(max_single_loss, 2),
        "top_10_losses": [round(x, 2) for x in top_losses],
        "roi": round((balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100, 1),
        "roi_dd_ratio": round(((balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100) / max_dd, 2) if max_dd > 0 else 0,
        "monthly_pnl": {m: round(p, 2) for m, p in monthly_pnl.items()},
        "skipped_by_loss_cap": skipped_by_loss_cap,
        "loss_cap_savings": round(loss_cap_savings, 2),
        "capped_win_count": capped_win_count,
        "capped_loss_count": capped_loss_count,
        "gross_profit_after_cap": round(gross_profit, 2),
        "gross_loss_after_cap": round(gross_loss, 2),
    }


def main():
    with open(V10_PATH) as f:
        raw = json.load(f).get("trades", [])

    print(f"v10原始数据: {len(raw)}笔")

    # v11基础过滤
    base = apply_v11_base_filter(raw)
    print(f"v11基础过滤后: {len(base)}笔")

    # ═══ 运行4个Profile ═══
    results = {}
    for name, profile in STRATEGY_PROFILES.items():
        print(f"\n运行 Profile {name}: {profile['desc']}")
        r = simulate(base, profile)
        if r:
            results[name] = {**r, "desc": profile["desc"]}
        else:
            results[name] = None

    # ═══ 输出对比表 ═══
    print(f"\n{'='*120}")
    print(f"v11j 策略 Profile 回测对比 (基于v11i参数, 初始$1000)")
    print(f"{'='*120}")
    print(f"定位: M40=保守风控 | G60=testnet主测 | L7=研究基准 | D60=对照组")
    print(f"{'='*120}")

    # 表头
    print(f"{'Profile':<10} {'说明':<22} {'笔数':>5} {'WR%':>6} {'PnL($)':>10} {'ROI%':>8} {'DD%':>7} {'PF':>5} {'月胜率':>6} {'ROI/DD':>7} {'最大连亏':>7} {'单笔最大亏':>10}")
    print("-" * 120)

    for name, profile in STRATEGY_PROFILES.items():
        r = results[name]
        if r is None:
            print(f"{name:<10} {profile['desc']:<22} {'N/A':>5}")
            continue

        print(
            f"{name:<10} {profile['desc']:<22} "
            f"{r['total_trades']:>5} "
            f"{r['win_rate']:>5.1f}% "
            f"{r['total_pnl']:>+10.0f} "
            f"{r['roi']:>+8.1f} "
            f"{r['max_drawdown']:>6.1f}% "
            f"{r['profit_factor']:>5.2f} "
            f"{r['monthly_win_rate']:>5.1f}% "
            f"{r['roi_dd_ratio']:>7.2f} "
            f"{r['max_consec_loss']:>7} "
            f"{r['max_single_loss']:>+10.1f}"
        )

    # ═══ 详细分析 ═══
    print(f"\n{'='*120}")
    print("4个Profile 详细对比")
    print(f"{'='*120}")

    for name, r in results.items():
        if r is None:
            continue
        profile = STRATEGY_PROFILES[name]
        print(f"\n  {name} - {profile['desc']}")
        print(f"      配置: SL≤{profile['max_sl_pct']}% | 连亏×{profile['consec_loss_mult']} | 上限${profile.get('max_loss_per_trade', 'N/A')}")
        print(f"      PnL: +${r['total_pnl']:.0f} | ROI: {r['roi']:.1f}% | DD: {r['max_drawdown']:.1f}% | ROI/DD: {r['roi_dd_ratio']:.2f}")
        print(f"      WR: {r['win_rate']:.1f}% ({r['wins']}W/{r['losses']}L) | PF: {r['profit_factor']:.2f}")
        print(f"      月胜率: {r['monthly_win_rate']:.1f}% ({r['profit_months']}/{r['total_months']}) | 最大连亏: {r['max_consec_loss']}笔")
        print(f"      单笔最大亏: ${r['max_single_loss']:.1f} | 缩仓统计: 盈{r['capped_win_count']}笔/亏{r['capped_loss_count']}笔")
        print(f"      亏损上限节省: ${r['loss_cap_savings']:.2f} | 毛利${r['gross_profit_after_cap']:.0f} | 毛亏${r['gross_loss_after_cap']:.0f}")

    # ═══ 排序对比 ═══
    print(f"\n{'='*120}")
    print("按不同指标排序")
    print(f"{'='*120}")

    # 按ROI/DD排序
    by_roi_dd = sorted(results.items(), key=lambda x: x[1]["roi_dd_ratio"] if x[1] else 0, reverse=True)
    print(f"\n按 ROI/DD 排序:")
    for i, (name, r) in enumerate(by_roi_dd, 1):
        if r:
            print(f"  #{i} {name}: {r['roi_dd_ratio']:.2f}")

    # 按PF排序
    by_pf = sorted(results.items(), key=lambda x: x[1]["profit_factor"] if x[1] else 0, reverse=True)
    print(f"\n按盈利因子PF排序:")
    for i, (name, r) in enumerate(by_pf, 1):
        if r:
            print(f"  #{i} {name}: {r['profit_factor']:.2f}")

    # 按月胜率排序
    by_monthly_wr = sorted(results.items(), key=lambda x: x[1]["monthly_win_rate"] if x[1] else 0, reverse=True)
    print(f"\n按月胜率排序:")
    for i, (name, r) in enumerate(by_monthly_wr, 1):
        if r:
            print(f"  #{i} {name}: {r['monthly_win_rate']:.1f}%")

    # 按最大单亏排序(越小越好)
    by_max_loss = sorted(results.items(), key=lambda x: x[1]["max_single_loss"] if x[1] else 0)
    print(f"\n按最大单亏排序(越小越好):")
    for i, (name, r) in enumerate(by_max_loss, 1):
        if r:
            print(f"  #{i} {name}: ${r['max_single_loss']:.1f}")

    # ═══ G60 vs D60 对比 (判断连亏减仓效果) ═══
    print(f"\n{'='*120}")
    print("G60 vs D60 对比 (判断连亏减仓×0.5是否有效)")
    print(f"{'='*120}")
    if results.get("G60") and results.get("D60"):
        g60 = results["G60"]
        d60 = results["D60"]
        print(f"\n{'指标':<20} {'G60':>15} {'D60':>15} {'差异':>15}")
        print("-" * 70)
        print(f"{'PnL ($)':<20} {g60['total_pnl']:>+15.0f} {d60['total_pnl']:>+15.0f} {g60['total_pnl']-d60['total_pnl']:>+15.0f}")
        print(f"{'ROI (%)':<20} {g60['roi']:>+15.1f} {d60['roi']:>+15.1f} {g60['roi']-d60['roi']:>+15.1f}")
        print(f"{'DD (%)':<20} {g60['max_drawdown']:>15.1f} {d60['max_drawdown']:>15.1f} {g60['max_drawdown']-d60['max_drawdown']:>+15.1f}")
        print(f"{'ROI/DD':<20} {g60['roi_dd_ratio']:>15.2f} {d60['roi_dd_ratio']:>15.2f} {g60['roi_dd_ratio']-d60['roi_dd_ratio']:>+15.2f}")
        print(f"{'PF':<20} {g60['profit_factor']:>15.2f} {d60['profit_factor']:>15.2f} {g60['profit_factor']-d60['profit_factor']:>+15.2f}")
        print(f"{'月胜率(%)':<20} {g60['monthly_win_rate']:>15.1f} {d60['monthly_win_rate']:>15.1f} {g60['monthly_win_rate']-d60['monthly_win_rate']:>+15.1f}")
        print(f"{'最大连亏':<20} {g60['max_consec_loss']:>15} {d60['max_consec_loss']:>15} {g60['max_consec_loss']-d60['max_consec_loss']:>+15}")
        print(f"\n结论: G60 相比 D60 (连亏×0.5 vs ×0.7):")
        if g60['roi_dd_ratio'] > d60['roi_dd_ratio']:
            print(f"  ✅ 连亏减仓×0.5有效，ROI/DD提升 {g60['roi_dd_ratio']-d60['roi_dd_ratio']:.2f}")
        else:
            print(f"  ⚠️ 连亏减仓×0.5未带来明显优势")

    # ═══ 保存结果 ═══
    out = {
        "version": "v11j-profile-compare",
        "description": "v11j策略Profile对比: M40/D60/G60/L7",
        "profiles": {},
    }
    for name, profile in STRATEGY_PROFILES.items():
        r = results[name]
        if r:
            out["profiles"][name] = {
                "desc": profile["desc"],
                "config": profile,
                "result": {k: v for k, v in r.items() if k not in ("monthly_pnl", "top_10_losses")},
            }

    out_path = Path(__file__).parent / "backtest_v11j_profiles.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 已保存: {out_path}")
    print("=" * 120)


if __name__ == "__main__":
    main()
