#!/usr/bin/env python3
"""
v12j 回测 — 基于v11g深度数据分析的优化版本

核心发现:
  V8>=6.5的897笔共亏-2044U，V8<6.5的618笔净赚+5076U
  → V8高分是虚假信号，趋势可能已近末端

优化策略:
  1. V8上限硬过滤: V8>=6.5直接跳过（最大改进，+2044U）
  2. 做多SL>5%减仓×0.6（高V8已过滤后，低V8+大SL仍有微亏）
  3. 做空V8>=6.5也硬过滤（122笔亏-829U）
  4. 连亏≥3时×0.5（更激进冷却）
  5. 保留v11g的其他有效调整（RSI弱减仓等）

对比基线: v11g (1274笔, +1798U, 1000U→2798U)
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
V10_PATH = DATA_DIR / "backtest_v10_result.json"
INITIAL_BALANCE = 1000.0

# ═══ 共用参数 ═══
STATIC_BLACKLIST = {
    "ZECUSDT", "DUSDT", "NILUSDT", "SOLUSDT", "DOGEUSDT",
    "CLUSDT", "KSMUSDT", "HYPEUSDT", "TAOUSDT", "PUMPUSDT",
    "BZUSDT", "FILUSDT", "WLFIUSDT", "ONDOUSDT", "ENAUSDT",
}
V11_MIN_V8_SCORE = 4
SHORT_POSITION_FACTOR = 0.5

def get_v8(t):
    return t.get("v8_score", 0) or t.get("v8_quality", 0)

def get_rsi(t):
    ts = t.get("tech_snapshot", {})
    return ts.get("rsi") if isinstance(ts, dict) else None

def get_atr_pct(t):
    ts = t.get("tech_snapshot", {})
    return ts.get("atr_pct") if isinstance(ts, dict) else None


# ═══ 版本定义 ═══
VERSIONS = {
    "v11g(基线)": {
        "desc": "v11g基线: 黑名单+V8≥4+做空减半+V8反转+RSI+SL+连亏冷却",
        "v8_max": None,  # 无上限
        "hard_filters": {"max_sl_pct": 10.0, "max_atr_pct": 5.0},
        "use_mult": True,
        "mult_params": {
            "v8_low_threshold": 4.0, "v8_low_mult": 1.3,
            "v8_high_threshold": 6.5, "v8_high_mult_long": 0.6, "v8_high_mult_short": 0.6,
            "rsi_weak": 50, "rsi_weak_mult": 0.7,
            "rsi_strong_low": 65, "rsi_strong_high": 75, "rsi_strong_mult": 1.2,
            "rsi_very_strong": 75, "rsi_very_strong_mult": 1.1,
            "sl_medium_low": 4.0, "sl_medium_high": 6.0, "sl_medium_mult": 0.65,
            "sl_wide_low": 8.0, "sl_wide_high": 10.0, "sl_wide_mult": 1.2,
            "consec_loss_threshold": 2, "consec_loss_mult": 0.7,
        },
    },
    "v12j-A": {
        "desc": "A: 仅V8<6.5硬过滤（最大单一改进）",
        "v8_max": 6.5,
        "hard_filters": {},
        "use_mult": False,  # 不用v11g的mult，纯看V8过滤的效果
        "mult_params": {},
    },
    "v12j-B": {
        "desc": "B: V8<6.5 + 做空减半",
        "v8_max": 6.5,
        "hard_filters": {},
        "use_mult": False,
        "mult_params": {},
        "short_factor": 0.5,
    },
    "v12j-C": {
        "desc": "C: V8<6.5 + v11g基础mult（RSI弱+SL中+连亏）",
        "v8_max": 6.5,
        "hard_filters": {},
        "use_mult": True,
        "mult_params": {
            "v8_low_threshold": 4.0, "v8_low_mult": 1.3,
            "rsi_weak": 50, "rsi_weak_mult": 0.7,
            "rsi_strong_low": 65, "rsi_strong_high": 75, "rsi_strong_mult": 1.2,
            "rsi_very_strong": 75, "rsi_very_strong_mult": 1.1,
            "sl_medium_low": 4.0, "sl_medium_high": 6.0, "sl_medium_mult": 0.65,
            "sl_wide_low": 8.0, "sl_wide_high": 10.0, "sl_wide_mult": 1.2,
            "consec_loss_threshold": 2, "consec_loss_mult": 0.7,
        },
    },
    "v12j-D": {
        "desc": "D: V8<6.5 + v11g mult + 做多SL>5%×0.6 + 连亏≥3×0.5",
        "v8_max": 6.5,
        "hard_filters": {},
        "use_mult": True,
        "mult_params": {
            "v8_low_threshold": 4.0, "v8_low_mult": 1.3,
            "rsi_weak": 50, "rsi_weak_mult": 0.7,
            "rsi_strong_low": 65, "rsi_strong_high": 75, "rsi_strong_mult": 1.2,
            "rsi_very_strong": 75, "rsi_very_strong_mult": 1.1,
            "sl_medium_low": 4.0, "sl_medium_high": 6.0, "sl_medium_mult": 0.65,
            "sl_wide_low": 8.0, "sl_wide_high": 10.0, "sl_wide_mult": 1.2,
            "consec_loss_threshold": 3, "consec_loss_mult": 0.5,
            "long_sl_wide_mult": 0.6,  # 新增: 做多SL>5%额外减仓
        },
    },
    "v12j-E": {
        "desc": "E: V8<6.5 + 做多SL>5%×0.6 + RSI<50×0.7 + 连亏≥3×0.5（精简版）",
        "v8_max": 6.5,
        "hard_filters": {},
        "use_mult": True,
        "mult_params": {
            "rsi_weak": 50, "rsi_weak_mult": 0.7,
            "sl_medium_low": 5.0, "sl_medium_high": 10.0, "sl_medium_mult": 0.6,
            "consec_loss_threshold": 3, "consec_loss_mult": 0.5,
        },
    },
    "v12j-F": {
        "desc": "F: V8<6.5硬过滤 + 做空不减半（验证做空减半是否有效）",
        "v8_max": 6.5,
        "hard_filters": {},
        "use_mult": False,
        "mult_params": {},
        "short_factor": 1.0,  # 做空不减半
    },
}


def apply_base_filter(trades):
    return [t for t in trades
            if t["symbol"] not in STATIC_BLACKLIST
            and get_v8(t) >= V11_MIN_V8_SCORE]


def apply_hard_filters(trades, filters):
    kept = []
    for t in trades:
        sl_pct = t.get("signal_sl_pct", 0) * 100
        if "max_sl_pct" in filters and sl_pct > filters["max_sl_pct"]:
            continue
        atr = get_atr_pct(t)
        if "max_atr_pct" in filters and atr is not None and atr * 100 > filters["max_atr_pct"]:
            continue
        kept.append(t)
    return kept


def calc_mult(trade, consec_losses, params):
    m = 1.0
    v8 = get_v8(trade)
    rsi = get_rsi(trade)
    sl_pct = trade.get("signal_sl_pct", 0) * 100
    d = trade["direction"]

    # V8低位加仓
    if "v8_low_threshold" in params and v8 <= params["v8_low_threshold"]:
        m *= params["v8_low_mult"]

    # RSI弱减仓（仅做多）
    if d == "long" and rsi is not None and "rsi_weak" in params:
        if rsi < params["rsi_weak"]:
            m *= params["rsi_weak_mult"]
        elif "rsi_strong_low" in params and params["rsi_strong_low"] <= rsi <= params.get("rsi_strong_high", 999):
            m *= params.get("rsi_strong_mult", 1.0)
        elif "rsi_very_strong" in params and rsi >= params["rsi_very_strong"]:
            m *= params.get("rsi_very_strong_mult", 1.0)

    # SL中等减仓
    if "sl_medium_low" in params and params["sl_medium_low"] <= sl_pct < params["sl_medium_high"]:
        m *= params["sl_medium_mult"]

    # SL宽幅加仓
    if "sl_wide_low" in params and params["sl_wide_low"] <= sl_pct < params["sl_wide_high"]:
        m *= params["sl_wide_mult"]

    # 新增: 做多SL>5%额外减仓
    if "long_sl_wide_mult" in params and d == "long" and sl_pct > 5:
        m *= params["long_sl_wide_mult"]

    # 连续亏损冷却
    if "consec_loss_threshold" in params and consec_losses >= params["consec_loss_threshold"]:
        m *= params["consec_loss_mult"]

    return m


def simulate(trades, version_cfg):
    bal_5000 = 5000.0
    bal = INITIAL_BALANCE
    peak = INITIAL_BALANCE
    max_dd = 0
    consec = 0
    gp = gl = 0.0
    monthly = defaultdict(list)
    long_stats = {"wins": 0, "losses": 0, "pnl": 0.0, "count": 0}
    short_stats = {"wins": 0, "losses": 0, "pnl": 0.0, "count": 0}
    v8_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0, "count": 0})

    for t in trades:
        v8 = get_v8(t)
        
        # V8上限过滤
        if version_cfg.get("v8_max") is not None and v8 >= version_cfg["v8_max"]:
            continue

        # 做空基础减仓
        short_factor = version_cfg.get("short_factor", SHORT_POSITION_FACTOR)
        base = short_factor if t["direction"] == "short" else 1.0

        if version_cfg["use_mult"]:
            mult = base * calc_mult(t, consec, version_cfg.get("mult_params", {}))
        else:
            mult = base

        scale = bal / bal_5000 if bal_5000 > 0 else 0.2
        orig_pnl = t.get("pnl_usd", 0)
        new_pnl = orig_pnl * scale * mult
        new_pos = t.get("position_usd", 0) * scale

        bal_5000 += orig_pnl
        bal += new_pnl
        peak = max(peak, bal)
        dd = (peak - bal) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

        if new_pnl > 0:
            gp += new_pnl
            consec = 0
        else:
            gl += abs(new_pnl)
            consec += 1 if new_pnl < 0 else 0

        month = t["entry_time"][:7]
        monthly[month].append({"pnl_usd": round(new_pnl, 2)})

        # 方向统计
        dkey = "long" if t["direction"] == "long" else "short"
        stats = long_stats if dkey == "long" else short_stats
        stats["count"] += 1
        stats["pnl"] += new_pnl
        if new_pnl > 0:
            stats["wins"] += 1
        else:
            stats["losses"] += 1

        # V8分段统计
        v8_key = f"V8={v8:.1f}"
        v8_stats[v8_key]["count"] += 1
        v8_stats[v8_key]["pnl"] += new_pnl
        if new_pnl > 0:
            v8_stats[v8_key]["wins"] += 1
        else:
            v8_stats[v8_key]["losses"] += 1

    total = long_stats["count"] + short_stats["count"]
    wins = long_stats["wins"] + short_stats["wins"]
    wr = wins / total * 100 if total else 0
    pf = gp / gl if gl > 0 else 99
    profit_months = sum(1 for ts in monthly.values() if sum(t["pnl_usd"] for t in ts) > 0)

    return {
        "total_trades": total,
        "wins": wins,
        "win_rate": round(wr, 1),
        "total_pnl": round(bal - INITIAL_BALANCE, 2),
        "final_balance": round(bal, 2),
        "return_pct": round((bal - INITIAL_BALANCE) / INITIAL_BALANCE * 100, 1),
        "max_drawdown": round(max_dd, 2),
        "profit_factor": round(pf, 2),
        "profit_months": profit_months,
        "total_months": len(monthly),
        "long": long_stats,
        "short": short_stats,
        "monthly": dict(monthly),
    }


def main():
    with open(V10_PATH) as f:
        raw = json.load(f)["trades"]

    print(f"{'='*75}")
    print(f"📊 v12j 优化回测 — 1000U动态余额模拟")
    print(f"{'='*75}")
    print(f"v10原始: {len(raw)}笔 | 目标余额: {INITIAL_BALANCE}U\n")

    base = apply_base_filter(raw)
    print(f"v11基础过滤后: {len(base)}笔\n")

    results = {}
    for ver_name, ver_cfg in VERSIONS.items():
        if ver_cfg.get("hard_filters"):
            filtered = apply_hard_filters(base, ver_cfg["hard_filters"])
        else:
            filtered = base

        r = simulate(filtered, ver_cfg)
        results[ver_name] = r

        print(f"{'─'*75}")
        print(f"📦 {ver_name}: {ver_cfg['desc']}")
        print(f"  交易: {r['total_trades']}笔 | 胜率: {r['win_rate']}% | PnL: {r['total_pnl']:+.1f}U")
        print(f"  收益: {r['return_pct']:+.1f}% | 回撤: {r['max_drawdown']:.1f}% | PF: {r['profit_factor']:.2f}")
        print(f"  盈利月: {r['profit_months']}/{r['total_months']}")
        l = r['long']
        s = r['short']
        l_wr = l['wins']/l['count']*100 if l['count'] else 0
        s_wr = s['wins']/s['count']*100 if s['count'] else 0
        print(f"  做多: {l['count']}笔 WR={l_wr:.1f}% PnL={l['pnl']:+.1f}U")
        print(f"  做空: {s['count']}笔 WR={s_wr:.1f}% PnL={s['pnl']:+.1f}U")

        # 月度
        print(f"  月度明细:")
        for m in sorted(r["monthly"].keys()):
            ts = r["monthly"][m]
            mpnl = sum(t["pnl_usd"] for t in ts)
            mw = sum(1 for t in ts if t["pnl_usd"] > 0)
            icon = "📈" if mpnl > 0 else "📉"
            print(f"    {icon} {m}: {len(ts):>3}笔 {mpnl:>+8.1f}U WR={mw/len(ts)*100:>4.0f}%")
        print()

    # ═══ 对比表 ═══
    print(f"\n{'='*75}")
    print(f"📊 版本对比汇总 — 1000U")
    print(f"{'='*75}")
    print(f"{'版本':<16} {'笔数':<6} {'胜率':<8} {'PnL':<11} {'收益':<9} {'回撤':<7} {'PF':<6} {'月盈/总'}")
    print(f"{'─'*16} {'─'*6} {'─'*8} {'─'*11} {'─'*9} {'─'*7} {'─'*6} {'─'*8}")
    for ver in VERSIONS:
        r = results[ver]
        print(f"{ver:<16} {r['total_trades']:<6} {r['win_rate']:>5.1f}%  "
              f"{r['total_pnl']:>+8.1f}U  {r['return_pct']:>+6.1f}%  "
              f"{r['max_drawdown']:>5.1f}%  {r['profit_factor']:<6.2f} "
              f"{r['profit_months']}/{r['total_months']}")

    # 增量
    print(f"\n{'='*75}")
    print(f"📊 vs v11g基线增量")
    print(f"{'='*75}")
    baseline = results["v11g(基线)"]
    for ver in VERSIONS:
        if ver == "v11g(基线)":
            continue
        r = results[ver]
        dpnl = r['total_pnl'] - baseline['total_pnl']
        ddd = r['max_drawdown'] - baseline['max_drawdown']
        dpf = r['profit_factor'] - baseline['profit_factor']
        print(f"  {ver}: PnL {dpnl:>+8.1f}U | 回撤 {ddd:>+5.1f}% | PF {dpf:>+5.2f} | 笔数 {r['total_trades']-baseline['total_trades']:>+5d}")

    # 保存
    out = {"version": "v12j_comparison", "results": {ver: {k: v for k, v in r.items() if k != "monthly"} for ver, r in results.items()}}
    with open(DATA_DIR / "backtest_v12j_result.json", "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\n💾 保存: {DATA_DIR / 'backtest_v12j_result.json'}")


if __name__ == "__main__":
    main()
