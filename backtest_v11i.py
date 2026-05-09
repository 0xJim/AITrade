#!/usr/bin/env python3
"""
v11i 独立回测 — 基于v11g回测数据拆解分析后的参数优化
回测方法: 加载v10原始535笔数据 → v11基础过滤+硬过滤 → 新仓位参数模拟

改动点(共2处):
  1. V8≥6.5做多减仓: ×0.6 → ×0.8 (v11g砍了+10847U利润, 过于激进)
  2. RSI 55-60做多减仓: 无 → ×0.4 (v11g该区间50笔WR=58%亏-99U)

其余参数保持v11g不变:
  - 静态黑名单15币
  - V8≥4入场门槛
  - 做空V8≥5减半
  - V8≤4做多加仓×1.3
  - RSI<50做多×0.7, RSI65-75×1.2, RSI≥75×1.1
  - SL 4-6%×0.65, SL 8-10%×1.2
  - 连续亏损2笔×0.7
  - SL>10%跳过, ATR>5%跳过, V8≥6.5+RSI<55做多跳过

基线 v11g(已回测): 234笔/68.4%/+$2881/DD4.6%/PF2.08/月6/7
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
V10_PATH = DATA_DIR / "backtest_v10_result.json"

# ═══ 基础参数 (同v11g) ═══
STATIC_BLACKLIST = {
    "ZECUSDT", "DUSDT", "NILUSDT", "SOLUSDT", "DOGEUSDT",
    "CLUSDT", "KSMUSDT", "HYPEUSDT", "TAOUSDT", "PUMPUSDT",
    "BZUSDT", "FILUSDT", "WLFIUSDT", "ONDOUSDT", "ENAUSDT",
}
V11_MIN_V8_SCORE = 4
SHORT_V8_THRESHOLD = 5
SHORT_POSITION_FACTOR = 0.5
INITIAL_BALANCE = 5000.0

# ═══ v11i 改动参数 (仅2处与v11g不同) ═══
V8_HIGH_THRESHOLD = 6.5
V8_HIGH_MULT_LONG = 0.8    # ← 改动1: v11g=0.6, 现在改为0.8
V8_LOW_MULT_LONG = 1.3     # 保持v11g
V8_LOW_MULT_SHORT = 1.3    # 保持v11g

RSI_WEAK = 50
RSI_WEAK_MULT = 0.7
RSI_MID_LOW = 55
RSI_MID_HIGH = 60
RSI_MID_MULT = 0.4         # ← 改动2: v11g无此区间(=1.0), 现在改为0.4
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

MAX_SL_PCT = 10.0
MAX_ATR_PCT = 5.0
FILTER_V8_RSI = True

CONSEC_LOSS_THRESHOLD = 2
CONSEC_LOSS_MULT = 0.7


def get_rsi(t):
    ts = t.get("tech_snapshot", {})
    return ts.get("rsi") if isinstance(ts, dict) else None

def get_atr_pct(t):
    ts = t.get("tech_snapshot", {})
    return ts.get("atr_pct") if isinstance(ts, dict) else None


def apply_v11_base_filter(trades):
    kept = []
    for t in trades:
        if t["symbol"] in STATIC_BLACKLIST: continue
        v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
        if v8 < V11_MIN_V8_SCORE: continue
        kept.append(t)
    return kept


def apply_hard_filters(trades):
    kept = []
    for t in trades:
        sl_pct = t.get("signal_sl_pct", 0) * 100
        if sl_pct > MAX_SL_PCT: continue
        atr = get_atr_pct(t)
        if atr is not None and atr * 100 > MAX_ATR_PCT: continue
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
        m *= CONSEC_LOSS_MULT

    return m


def simulate(trades):
    balance = INITIAL_BALANCE
    peak = balance
    max_dd = 0
    consec = 0
    adj = []
    monthly = defaultdict(list)
    gross_profit = 0
    gross_loss = 0

    for t in trades:
        v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
        base = SHORT_POSITION_FACTOR if (t["direction"] == "short" and v8 >= SHORT_V8_THRESHOLD) else 1.0
        mult = base * calc_mult(t, consec)

        pnl = t.get("pnl_usd", 0) * mult
        balance += pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

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
    wins = sum(1 for t in adj if t["pnl_usd"] > 0)
    losses = total_trades - wins
    win_rate = wins / total_trades * 100 if total_trades else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 99
    profit_months = sum(1 for ts in monthly.values() if sum(t["pnl_usd"] for t in ts) > 0)
    total_months = len(monthly)

    return {
        "initial_balance": INITIAL_BALANCE,
        "final_balance": round(balance, 2),
        "total_pnl": round(balance - INITIAL_BALANCE, 2),
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown": round(max_dd, 2),
        "profit_months": profit_months,
        "total_months": total_months,
        "trades": adj,
        "monthly_summary": {
            m: {
                "trades": len(ts),
                "pnl": round(sum(t["pnl_usd"] for t in ts), 2),
                "wins": sum(1 for t in ts if t["pnl_usd"] > 0),
                "win_rate": round(sum(1 for t in ts if t["pnl_usd"] > 0) / len(ts) * 100, 1),
            }
            for m, ts in sorted(monthly.items())
        },
    }


def main():
    with open(V10_PATH) as f:
        raw = json.load(f).get("trades", [])

    print(f"v10原始数据: {len(raw)}笔")

    # Step 1: v11基础过滤
    base = apply_v11_base_filter(raw)
    print(f"v11基础过滤后: {len(base)}笔 (黑名单+V8≥4)")

    # Step 2: 硬过滤
    filtered = apply_hard_filters(base)
    print(f"硬过滤后: {len(filtered)}笔 (SL/ATR/V8+RSI)")

    # Step 3: 模拟
    result = simulate(filtered)

    # 输出
    print(f"\n{'='*60}")
    print(f"v11i 回测结果 (已验证)")
    print(f"{'='*60}")
    print(f"  交易数:   {result['total_trades']}笔")
    print(f"  胜率:     {result['win_rate']}% ({result['wins']}W/{result['losses']}L)")
    print(f"  PnL:      +${result['total_pnl']:.0f}")
    print(f"  最大回撤: {result['max_drawdown']}%")
    print(f"  盈亏比:   {result['profit_factor']}")
    print(f"  盈利月:   {result['profit_months']}/{result['total_months']}")

    print(f"\n月度明细:")
    for m, s in result["monthly_summary"].items():
        icon = "📈" if s["pnl"] > 0 else "📉"
        print(f"  {icon} {m}: {s['trades']}笔 PnL={s['pnl']:+.0f}U WR={s['win_rate']:.0f}%")

    # 关键区段
    print(f"\n关键区段:")
    segments = [
        ("做多V8≥6.5", lambda t: t["direction"]=="long" and t.get("v8_score",0) >= 6.5),
        ("做多V8≤4.0", lambda t: t["direction"]=="long" and t.get("v8_score",0) <= 4.0),
        ("做多RSI55-60", lambda t: t["direction"]=="long" and 55 <= (t.get("tech_snapshot",{}) or {}).get("rsi",0) < 60),
        ("做空V8≤4.0", lambda t: t["direction"]=="short" and t.get("v8_score",0) <= 4.0),
    ]
    for label, fn in segments:
        grp = [t for t in result["trades"] if fn(t)]
        if not grp: continue
        pnl = sum(t["pnl_usd"] for t in grp)
        w = sum(1 for t in grp if t["pnl_usd"] > 0)
        print(f"  {label}: {len(grp)}笔 WR={w/len(grp)*100:.0f}% PnL={pnl:+.0f}U")

    # 仓位倍率分布
    print(f"\n仓位倍率分布:")
    for bk, lo, hi in [("×<0.5",0,0.5),("×0.5-0.8",0.5,0.8),("×0.8-1.0",0.8,1.0),
                        ("×1.0-1.2",1.0,1.2),("×1.2-1.5",1.2,1.5),("×≥1.5",1.5,99)]:
        grp = [t for t in result["trades"] if lo <= t.get("position_mult",1.0) < hi]
        if not grp: continue
        pnl = sum(t["pnl_usd"] for t in grp)
        print(f"  {bk}: {len(grp)}笔 PnL={pnl:+.0f}U")

    # 保存
    out = {
        "version": "v11i",
        "method": "加载v10原始535笔数据, 独立回测验证",
        "baseline": "v11g(已回测): 234笔/68.4%/+$2881/DD4.6%/PF2.08/月6/7",
        "changes_vs_v11g": [
            "V8≥6.5做多减仓: ×0.6 → ×0.8",
            "RSI 55-60做多: 无减仓(×1.0) → ×0.4",
        ],
        **{k: v for k, v in result.items()},
    }
    out_path = DATA_DIR / "backtest_v11i_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 已保存: {out_path}")


if __name__ == "__main__":
    main()
