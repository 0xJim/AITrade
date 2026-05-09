#!/usr/bin/env python3
"""
v11j Profile 系统回测 — M40/D60/G60/L7 四组对比
基于v10原始535笔数据 → v11i参数 → Profile化参数集

Profile 定义:
  M40: 保守安全挡(testnet/小资金/冷启动) — MAX_LOSS=$40, CONSEC×0.7, SL≤10%
  D60: 对照组(验证连亏0.5贡献) — MAX_LOSS=$60, CONSEC×0.7, SL≤10%
  G60: 下一阶段主测(收益弹性+风控平衡) — MAX_LOSS=$60, CONSEC×0.5, SL≤10%
  L7:  研究基准(SL过滤因子) — 无硬帽, CONSEC×0.7, SL≤7%
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
MAX_ATR_PCT = 5.0
FILTER_V8_RSI = True
CONSEC_LOSS_THRESHOLD = 2

# ═══ Profile 定义 ═══
PROFILES = {
    "M40": {
        "desc": "保守安全挡(testnet/冷启动)",
        "role": "风控底座",
        "max_loss_per_trade": 40,
        "consec_loss_mult": 0.7,
        "max_sl_pct": 10.0,
    },
    "D60": {
        "desc": "对照组(验证连亏×0.5贡献)",
        "role": "对照",
        "max_loss_per_trade": 60,
        "consec_loss_mult": 0.7,
        "max_sl_pct": 10.0,
    },
    "G60": {
        "desc": "下一阶段主测(收益弹性+风控平衡)",
        "role": "主测 ★",
        "max_loss_per_trade": 60,
        "consec_loss_mult": 0.5,
        "max_sl_pct": 10.0,
    },
    "L7": {
        "desc": "研究基准(SL过滤因子)",
        "role": "研究",
        "max_loss_per_trade": None,
        "consec_loss_mult": 0.7,
        "max_sl_pct": 7.0,
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


def simulate(trades, profile_name, config):
    """
    config: profile 参数集
    返回完整统计含 capped_win/loss_count, gross_profit/loss_after_cap
    """
    max_sl = config["max_sl_pct"]
    cl_mult = config["consec_loss_mult"]
    max_loss = config.get("max_loss_per_trade", None)

    filtered = apply_hard_filters(trades, max_sl_pct=max_sl)

    balance = INITIAL_BALANCE
    peak = balance
    max_dd = 0
    max_dd_peak = balance
    max_dd_trough = balance
    consec = 0
    adj = []
    monthly = defaultdict(list)

    # Profile 特有统计
    gross_profit_after_cap = 0
    gross_loss_after_cap = 0
    capped_win_count = 0
    capped_loss_count = 0

    for t in filtered:
        v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
        base = SHORT_POSITION_FACTOR if (t["direction"] == "short" and v8 >= SHORT_V8_THRESHOLD) else 1.0
        mult = base * calc_mult(t, consec, consec_loss_mult=cl_mult)

        # 开仓前缩仓: est_risk = position_usd × signal_sl_pct × leverage × mult
        # 不看 pnl 正负
        capped = False
        if max_loss is not None:
            pos_usd = t.get("position_usd", 0)
            sl_pct = t.get("signal_sl_pct", 0)
            lev = t.get("leverage", 3)
            est_risk = pos_usd * sl_pct * lev * mult
            if est_risk > max_loss:
                shrink_ratio = max_loss / est_risk
                mult *= shrink_ratio
                capped = True

        raw_pnl = t.get("pnl_usd", 0)
        pnl = raw_pnl * mult

        balance += pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100 if peak > 0 else 0

        if dd > max_dd:
            max_dd = dd
            max_dd_peak = peak
            max_dd_trough = balance

        if pnl > 0:
            gross_profit_after_cap += pnl
            if capped:
                capped_win_count += 1
        else:
            gross_loss_after_cap += abs(pnl)
            if capped:
                capped_loss_count += 1

        at = {**t}
        at["pnl_usd"] = round(pnl, 2)
        at["pnl_raw"] = round(raw_pnl, 2)
        at["position_mult"] = round(mult, 3)
        at["capped"] = capped
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
    profit_factor = gross_profit_after_cap / gross_loss_after_cap if gross_loss_after_cap > 0 else 99

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
    max_single_loss = min(t["pnl_usd"] for t in adj)

    # Top 10 亏损
    top_losses = sorted([t["pnl_usd"] for t in adj])[:10]

    roi_pct = (balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100

    return {
        "profile": profile_name,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "total_pnl": round(balance - INITIAL_BALANCE, 2),
        "final_balance": round(balance, 2),
        "gross_profit_after_cap": round(gross_profit_after_cap, 2),
        "gross_loss_after_cap": round(gross_loss_after_cap, 2),
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
        "top_10_losses": [round(x, 2) for x in top_losses],
        "roi": round(roi_pct, 1),
        "roi_dd_ratio": round(roi_pct / max_dd, 2) if max_dd > 0 else 0,
        "monthly_pnl": {m: round(p, 2) for m, p in monthly_pnl.items()},
    }


def main():
    if not V10_PATH.exists():
        print(f"❌ 数据文件不存在: {V10_PATH}")
        print("请先运行 python strategies/S10-v10/backtest.py 生成原始数据")
        sys.exit(1)

    with open(V10_PATH) as f:
        raw = json.load(f).get("trades", [])

    print(f"v10原始数据: {len(raw)}笔")

    base = apply_v11_base_filter(raw)
    print(f"v11基础过滤后: {len(base)}笔")

    # ═══ 运行四个 Profile ═══
    results = {}
    for pname, pconf in PROFILES.items():
        r = simulate(base, pname, pconf)
        if r:
            results[pname] = {**r, "desc": pconf["desc"], "role": pconf["role"]}
        else:
            results[pname] = None

    # ═══ 输出对比表 ═══
    print(f"\n{'='*130}")
    print(f"v11j Profile 回测对比 (初始$1000, 基于v11i信号)")
    print(f"{'='*130}")

    print(f"{'Profile':<8} {'定位':<10} {'说明':<28} {'笔数':>5} {'WR%':>6} {'PnL($)':>10} {'ROI%':>8} {'DD%':>7} {'PF':>5} {'月胜率':>6} {'ROI/DD':>7} {'最大连亏':>7} {'单笔最大亏':>10} {'缩仓胜':>5} {'缩仓亏':>5}")
    print("-" * 130)

    for pname in ["M40", "D60", "G60", "L7"]:
        r = results[pname]
        if r is None:
            print(f"{pname:<8} {PROFILES[pname]['role']:<10} {PROFILES[pname]['desc']:<28} {'N/A':>5}")
            continue

        print(
            f"{pname:<8} {r['role']:<10} {r['desc']:<28} "
            f"{r['total_trades']:>5} "
            f"{r['win_rate']:>5.1f}% "
            f"{r['total_pnl']:>+10.0f} "
            f"{r['roi']:>+8.1f} "
            f"{r['max_drawdown']:>6.1f}% "
            f"{r['profit_factor']:>5.2f} "
            f"{r['monthly_win_rate']:>5.1f}% "
            f"{r['roi_dd_ratio']:>7.2f} "
            f"{r['max_consec_loss']:>7} "
            f"{r['max_single_loss']:>+10.1f} "
            f"{r['capped_win_count']:>5} "
            f"{r['capped_loss_count']:>5}"
        )

    # ═══ 关键对比: G60 vs D60 (验证连亏×0.5贡献) ═══
    print(f"\n{'='*130}")
    print("📊 关键对比: G60 vs D60 — 验证连亏减仓×0.5的真实贡献")
    print(f"{'='*130}")

    if results["G60"] and results["D60"]:
        g, d = results["G60"], results["D60"]
        pnl_diff = g["total_pnl"] - d["total_pnl"]
        dd_diff = g["max_drawdown"] - d["max_drawdown"]
        rr_diff = g["roi_dd_ratio"] - d["roi_dd_ratio"]
        cl_diff = g["max_consec_loss"] - d["max_consec_loss"]

        print(f"  G60 PnL: ${g['total_pnl']:+.0f} vs D60 PnL: ${d['total_pnl']:+.0f} → 差异: ${pnl_diff:+.0f}")
        print(f"  G60 DD:  {g['max_drawdown']:.1f}% vs D60 DD:  {d['max_drawdown']:.1f}% → 差异: {dd_diff:+.1f}%")
        print(f"  G60 ROI/DD: {g['roi_dd_ratio']:.2f} vs D60 ROI/DD: {d['roi_dd_ratio']:.2f} → 差异: {rr_diff:+.2f}")
        print(f"  G60 最大连亏: {g['max_consec_loss']} vs D60 最大连亏: {d['max_consec_loss']} → 差异: {cl_diff:+d}")

        if pnl_diff > 0 and dd_diff < 0:
            print(f"  ✅ 连亏×0.5 确实贡献了「收益增+DD降」的双赢效果")
        elif pnl_diff > 0 and dd_diff > 0:
            print(f"  ⚠️ 连亏×0.5 增加了收益，但DD也略增")
        elif pnl_diff < 0:
            print(f"  📉 连亏×0.5 反而减少了收益（缩仓过度）")
        else:
            print(f"  ➡️ 连亏×0.5 效果中性")

    # ═══ 关键对比: M40 vs G60 (安全底座 vs 主测) ═══
    print(f"\n{'='*130}")
    print("📊 关键对比: M40(风控底座) vs G60(主测) — 安全换收益的边际")
    print(f"{'='*130}")

    if results["M40"] and results["G60"]:
        m, g = results["M40"], results["G60"]
        pnl_diff = g["total_pnl"] - m["total_pnl"]
        dd_diff = g["max_drawdown"] - m["max_drawdown"]

        print(f"  G60比M40多赚: ${pnl_diff:+.0f} ({pnl_diff/max(abs(m['total_pnl']),1)*100:+.1f}%)")
        print(f"  G60比M40 DD变化: {dd_diff:+.1f}%")
        print(f"  M40 单笔最大亏: ${m['max_single_loss']:.1f} | G60 单笔最大亏: ${g['max_single_loss']:.1f}")
        print(f"  M40 最大连亏: {m['max_consec_loss']}笔 | G60 最大连亏: {g['max_consec_loss']}笔")

    # ═══ 月度对比 ═══
    print(f"\n{'='*130}")
    print("月度PnL对比 (四个Profile)")
    print(f"{'='*130}")

    all_months = set()
    for pname in PROFILES:
        if results[pname]:
            all_months.update(results[pname]["monthly_pnl"].keys())
    all_months = sorted(all_months)

    header = f"{'月份':<10}"
    for pname in PROFILES:
        header += f" {pname:>12}"
    print(header)
    print("-" * (10 + 13 * len(PROFILES)))

    for mo in all_months:
        row = f"{mo:<10}"
        for pname in PROFILES:
            if results[pname] and mo in results[pname]["monthly_pnl"]:
                p = results[pname]["monthly_pnl"][mo]
                row += f" {p:>+12.0f}"
            else:
                row += f" {'—':>12}"
        print(row)

    # ═══ 保存结果 ═══
    out = {
        "version": "v11j-profile-comparison",
        "description": "v11j Profile系统 M40/D60/G60/L7 四组对比",
        "profiles": {},
    }
    for pname, pconf in PROFILES.items():
        r = results[pname]
        if r:
            out["profiles"][pname] = {
                "desc": pconf["desc"],
                "role": pconf["role"],
                "config": {
                    "max_loss_per_trade": pconf["max_loss_per_trade"],
                    "consec_loss_mult": pconf["consec_loss_mult"],
                    "max_sl_pct": pconf["max_sl_pct"],
                },
                "result": {k: v for k, v in r.items() if k not in ("monthly_pnl",)},
            }

    out_path = DATA_DIR / "backtest_v11j_profiles.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 已保存: {out_path}")


if __name__ == "__main__":
    main()
