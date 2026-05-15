#!/usr/bin/env python3
"""
v12j-v2: 最终优化回测 — 基于V8分档精确优化

核心发现:
  V8只有3个值: 4.0(618笔+5076U), 6.5(884笔-2038U), 7.8(13笔-6U)
  
  v11g的策略: V8=4.0×1.3 + V8=6.5×0.6 = 总赚约+5560U(粗估)
  但V8=6.5贡献-881U(含mult), 是拖累

  优化方向:
  - G: V8=6.5×0.3 (更激进减仓, 保留少量参与)
  - H: V8=6.5直接过滤 (完全不做)
  - I: V8=6.5做多×0.3, 做空×0.0(过滤做空V8=6.5)
  - J: V8=6.5×0.3 + 做多RSI<50×0.5 + 连亏≥3×0.4
  - K: V8=6.5仅保留做空×0.6(做空V8=6.5虽亏但WR=62.8%)
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
V10_PATH = DATA_DIR / "backtest_v10_result.json"
INITIAL_BALANCE = 1000.0

BLACKLIST = {
    "ZECUSDT", "DUSDT", "NILUSDT", "SOLUSDT", "DOGEUSDT",
    "CLUSDT", "KSMUSDT", "HYPEUSDT", "TAOUSDT", "PUMPUSDT",
    "BZUSDT", "FILUSDT", "WLFIUSDT", "ONDOUSDT", "ENAUSDT",
}

def get_v8(t):
    return t.get("v8_score", 0) or t.get("v8_quality", 0)

def get_rsi(t):
    ts = t.get("tech_snapshot", {})
    return ts.get("rsi") if isinstance(ts, dict) else None

def get_atr(t):
    ts = t.get("tech_snapshot", {})
    return ts.get("atr_pct") if isinstance(ts, dict) else None


VERSIONS = {
    "v11g(基线)": {
        "desc": "v11g: V8=4.0×1.3 + V8=6.5×0.6 + hard_filter",
        "hard_filter": {"max_sl": 10, "max_atr": 5},
        "v8_mults": {4.0: 1.3, 6.5: 0.6, 7.8: 0.6},
        "short_base": 0.5,
        "extra": {},  # RSI/SL/连亏
    },
    "v12j-G": {
        "desc": "G: V8=4.0×1.3 + V8=6.5×0.3(更激进减仓)",
        "hard_filter": {"max_sl": 10, "max_atr": 5},
        "v8_mults": {4.0: 1.3, 6.5: 0.3, 7.8: 0.3},
        "short_base": 0.5,
        "extra": {},
    },
    "v12j-H": {
        "desc": "H: V8=4.0×1.3 + V8=6.5过滤(完全不做)",
        "hard_filter": {},
        "v8_mults": {4.0: 1.3, 6.5: 0.0, 7.8: 0.0},
        "short_base": 0.5,
        "extra": {},
    },
    "v12j-I": {
        "desc": "I: V8=4.0×1.3 + V8=6.5做多×0.2做空×0.0",
        "hard_filter": {"max_sl": 10, "max_atr": 5},
        "v8_mults": {4.0: 1.3, 6.5: 0.2, 7.8: 0.2},
        "short_base": 0.5,
        "v8_65_short_mult": 0.0,  # V8=6.5做空直接过滤
        "extra": {},
    },
    "v12j-J": {
        "desc": "J: G基础上 + RSI<50做多×0.5 + 连亏≥3×0.4",
        "hard_filter": {"max_sl": 10, "max_atr": 5},
        "v8_mults": {4.0: 1.3, 6.5: 0.3, 7.8: 0.3},
        "short_base": 0.5,
        "extra": {"rsi_weak": 50, "rsi_weak_mult": 0.5, "consec_threshold": 3, "consec_mult": 0.4},
    },
    "v12j-K": {
        "desc": "K: V8=4.0×1.3 + V8=6.5做多×0.0做空×0.6",
        "hard_filter": {"max_sl": 10, "max_atr": 5},
        "v8_mults": {4.0: 1.3, 6.5: 0.0, 7.8: 0.0},
        "short_base": 0.5,
        "v8_65_short_mult": 0.6,  # V8=6.5做空保留
        "extra": {},
    },
    "v12j-L": {
        "desc": "L: V8=4.0×1.5(更激进加仓) + V8=6.5×0.0",
        "hard_filter": {},
        "v8_mults": {4.0: 1.5, 6.5: 0.0, 7.8: 0.0},
        "short_base": 0.5,
        "extra": {},
    },
    "v12j-M": {
        "desc": "M: V8=4.0×1.3 + V8=6.5×0.0 + RSI<50×0.6 + 连亏≥3×0.5",
        "hard_filter": {},
        "v8_mults": {4.0: 1.3, 6.5: 0.0, 7.8: 0.0},
        "short_base": 0.5,
        "extra": {"rsi_weak": 50, "rsi_weak_mult": 0.6, "consec_threshold": 3, "consec_mult": 0.5},
    },
    "v12j-N": {
        "desc": "N: V8=4.0×1.3 + V8=6.5×0.0 + 做空不减半",
        "hard_filter": {},
        "v8_mults": {4.0: 1.3, 6.5: 0.0, 7.8: 0.0},
        "short_base": 1.0,
        "extra": {},
    },
}


def simulate(trades, ver_cfg):
    bal_5000 = 5000.0
    bal = INITIAL_BALANCE
    peak = INITIAL_BALANCE
    max_dd = 0
    consec = 0
    gp = gl = 0.0
    monthly = defaultdict(list)
    stats_by_v8 = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0.0})
    long_s = {"count": 0, "wins": 0, "pnl": 0.0}
    short_s = {"count": 0, "wins": 0, "pnl": 0.0}
    
    for t in trades:
        v8 = get_v8(t)
        if v8 < 4:
            continue
        
        # hard filter
        hf = ver_cfg.get("hard_filter", {})
        sl_pct = t.get("signal_sl_pct", 0) * 100
        atr = get_atr(t)
        if "max_sl" in hf and sl_pct > hf["max_sl"]:
            continue
        if "max_atr" in hf and atr is not None and atr * 100 > hf["max_atr"]:
            continue
        
        d = t["direction"]
        v8_mult = ver_cfg["v8_mults"].get(v8, 0.0)
        
        # 特殊: V8=6.5做空用不同mult
        if "v8_65_short_mult" in ver_cfg and v8 >= 6.5 and d == "short":
            v8_mult = ver_cfg["v8_65_short_mult"]
        
        if v8_mult == 0:
            continue
        
        # 做空基础
        base = ver_cfg["short_base"] if d == "short" else 1.0
        
        mult = base * v8_mult
        
        # 额外mult
        ex = ver_cfg.get("extra", {})
        rsi = get_rsi(t)
        if d == "long" and rsi is not None and "rsi_weak" in ex:
            if rsi < ex["rsi_weak"]:
                mult *= ex["rsi_weak_mult"]
        
        if "consec_threshold" in ex and consec >= ex["consec_threshold"]:
            mult *= ex["consec_mult"]
        
        # 计算PnL
        scale = bal / bal_5000 if bal_5000 > 0 else 0.2
        orig_pnl = t.get("pnl_usd", 0)
        new_pnl = orig_pnl * scale * mult
        
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
        
        # 统计
        v8_key = f"V8={v8:.1f}"
        stats_by_v8[v8_key]["count"] += 1
        stats_by_v8[v8_key]["pnl"] += new_pnl
        if new_pnl > 0:
            stats_by_v8[v8_key]["wins"] += 1
        
        s = long_s if d == "long" else short_s
        s["count"] += 1
        s["pnl"] += new_pnl
        if new_pnl > 0:
            s["wins"] += 1

    total = long_s["count"] + short_s["count"]
    wins = long_s["wins"] + short_s["wins"]
    wr = wins / total * 100 if total else 0
    pf = gp / gl if gl > 0 else 99
    profit_m = sum(1 for ts in monthly.values() if sum(t["pnl_usd"] for t in ts) > 0)
    
    return {
        "total": total, "wins": wins, "win_rate": round(wr, 1),
        "pnl": round(bal - INITIAL_BALANCE, 2),
        "balance": round(bal, 2),
        "ret": round((bal - INITIAL_BALANCE) / INITIAL_BALANCE * 100, 1),
        "dd": round(max_dd, 2),
        "pf": round(pf, 2),
        "profit_months": profit_m,
        "total_months": len(monthly),
        "long": dict(long_s),
        "short": dict(short_s),
        "v8_stats": {k: dict(v) for k, v in stats_by_v8.items()},
        "monthly": {m: sum(t["pnl_usd"] for t in ts) for m, ts in monthly.items()},
    }


def main():
    with open(V10_PATH) as f:
        raw = json.load(f)["trades"]
    
    base = [t for t in raw if t["symbol"] not in BLACKLIST and get_v8(t) >= 4]
    print(f"{'='*75}")
    print(f"📊 v12j-v2 精确优化 — V8分档策略 (1000U)")
    print(f"{'='*75}")
    print(f"基础过滤: {len(base)}笔 (V8=4.0:618 V8=6.5:884 V8=7.8:13)\n")
    
    results = {}
    for ver, cfg in VERSIONS.items():
        r = simulate(base, cfg)
        results[ver] = r
        
        l, s = r["long"], r["short"]
        l_wr = l["wins"]/l["count"]*100 if l["count"] else 0
        s_wr = s["wins"]/s["count"]*100 if s["count"] else 0
        
        print(f"{'─'*75}")
        print(f"📦 {ver}: {cfg['desc']}")
        print(f"  总: {r['total']}笔 WR={r['win_rate']}% PnL={r['pnl']:+.1f}U "
              f"Ret={r['ret']:+.1f}% DD={r['dd']:.1f}% PF={r['pf']:.2f} "
              f"月盈={r['profit_months']}/{r['total_months']}")
        print(f"  做多: {l['count']}笔 WR={l_wr:.1f}% PnL={l['pnl']:+.1f}U | "
              f"做空: {s['count']}笔 WR={s_wr:.1f}% PnL={s['pnl']:+.1f}U")
        
        for vk, vs in sorted(r["v8_stats"].items()):
            v_wr = vs["wins"]/vs["count"]*100 if vs["count"] else 0
            print(f"  {vk}: {vs['count']}笔 PnL={vs['pnl']:+.1f}U WR={v_wr:.1f}%")
        print()
    
    # 对比表
    print(f"\n{'='*75}")
    print(f"📊 汇总对比 — 1000U")
    print(f"{'='*75}")
    print(f"{'版本':<16} {'笔数':<6} {'WR':<7} {'PnL':<10} {'收益':<8} {'DD':<7} {'PF':<6} {'月'}")
    print(f"{'─'*16} {'─'*6} {'─'*7} {'─'*10} {'─'*8} {'─'*7} {'─'*6} {'─'*6}")
    baseline_pnl = results["v11g(基线)"]["pnl"]
    for ver in VERSIONS:
        r = results[ver]
        delta = r["pnl"] - baseline_pnl
        tag = f"({delta:+.0f})" if ver != "v11g(基线)" else ""
        print(f"{ver:<16} {r['total']:<6} {r['win_rate']:>5.1f}% {r['pnl']:>+8.1f}U "
              f"{r['ret']:>+6.1f}% {r['dd']:>5.1f}% {r['pf']:<6.2f} "
              f"{r['profit_months']}/{r['total_months']} {tag}")

    # 保存
    out = {ver: {k: v for k, v in r.items() if k != "monthly"} for ver, r in results.items()}
    with open(DATA_DIR / "backtest_v12j_v2_result.json", "w") as f:
        json.dump({"version": "v12j-v2", "results": out}, f, indent=2, ensure_ascii=False)
    print(f"\n💾 保存: {DATA_DIR / 'backtest_v12j_v2_result.json'}")


if __name__ == "__main__":
    main()
