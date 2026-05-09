#!/usr/bin/env python3
"""
v11h 独立回测 — v11g归档后优化版
基于v11g回测数据拆解分析的4个优化点:
  1. RSI 55-60 做多减仓×0.7 (v11g: 50笔WR=58% PnL=-99U)
  2. ATR过滤边界从5.0%放宽到5.5% (v11g过滤掉几笔赚钱的ATR≈5%交易)
  3. SKYAIUSDT减仓×0.8 (50笔WR=56%, 胜率低但量大)
  4. 做空V8≥6.5减仓×0.6 (v11g仅1笔亏-19U, 加固逻辑)

归档基线 v11g: 234笔/68.4%/+$2881/DD4.6%/PF2.08/月6/7
"""
import json
import sys
from collections import defaultdict, Counter
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
V10_PATH = DATA_DIR / "backtest_v10_result.json"

# ═══ v11 基础参数 (同v11g) ═══
STATIC_BLACKLIST = {
    "ZECUSDT", "DUSDT", "NILUSDT", "SOLUSDT", "DOGEUSDT",
    "CLUSDT", "KSMUSDT", "HYPEUSDT", "TAOUSDT", "PUMPUSDT",
    "BZUSDT", "FILUSDT", "WLFIUSDT", "ONDOUSDT", "ENAUSDT",
}
V11_MIN_V8_SCORE = 4
SHORT_V8_THRESHOLD = 5
SHORT_POSITION_FACTOR = 0.5

# ═══ v11g 仓位调整参数 (不变) ═══
V8_LOW_THRESHOLD = 4.0
V8_LOW_MULT = 1.3
V8_HIGH_THRESHOLD = 6.5
V8_HIGH_MULT = 0.6

RSI_WEAK = 50
RSI_WEAK_MULT = 0.7
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

CONSEC_LOSS_THRESHOLD = 2
CONSEC_LOSS_MULT = 0.7

# ═══ v11h 新增/调整参数 ═══
# 优化1: RSI 55-60 做多减仓×0.7
V11H_RSI_MID_LOW = 55
V11H_RSI_MID_HIGH = 60
V11H_RSI_MID_MULT = 0.7

# 优化2: ATR过滤从5.0%放宽到5.5%
MAX_ATR_PCT = 5.5  # v11g=5.0

# 优化3: SKYAIUSDT减仓×0.8
V11H_LOW_WR_SYMBOLS = {"SKYAIUSDT"}
V11H_LOW_WR_MULT = 0.8

# 优化4: 做空V8≥6.5减仓×0.6
V11H_SHORT_V8HIGH_THRESHOLD = 6.5
V11H_SHORT_V8HIGH_MULT = 0.6

# 保留不变
MAX_SL_PCT = 10.0
FILTER_V8_RSI = True

INITIAL_BALANCE = 5000.0


def get_rsi(trade):
    ts = trade.get("tech_snapshot", {})
    return ts.get("rsi") if isinstance(ts, dict) else None


def get_atr_pct(trade):
    ts = trade.get("tech_snapshot", {})
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


def apply_v11h_filters(trades):
    """v11h过滤: SL>10%, ATR>5.5%, V8≥6.5+RSI<55做多跳过"""
    kept = []
    skipped_sl = skipped_atr = skipped_v8rsi = 0
    
    for t in trades:
        sl_pct = t.get("signal_sl_pct", 0) * 100
        
        if sl_pct > MAX_SL_PCT:
            skipped_sl += 1
            continue
        
        atr_pct = get_atr_pct(t)
        if atr_pct is not None and atr_pct * 100 > MAX_ATR_PCT:
            skipped_atr += 1
            continue
        
        if FILTER_V8_RSI and t["direction"] == "long":
            v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
            rsi = get_rsi(t)
            if v8 >= V8_HIGH_THRESHOLD and rsi is not None and rsi < 55:
                skipped_v8rsi += 1
                continue
        
        kept.append(t)
    
    print(f"  v11h过滤跳过: SL>{MAX_SL_PCT}%={skipped_sl}, ATR>{MAX_ATR_PCT}%={skipped_atr}, V8+RSI={skipped_v8rsi}")
    return kept


def calc_position_multiplier(trade, consec_losses):
    mult = 1.0
    v8 = trade.get("v8_score", 0) or trade.get("v8_quality", 0)
    rsi = get_rsi(trade)
    sl_pct = trade.get("signal_sl_pct", 0) * 100
    
    # v11g: V8反转
    if v8 <= V8_LOW_THRESHOLD:
        mult *= V8_LOW_MULT
    elif v8 >= V8_HIGH_THRESHOLD:
        mult *= V8_HIGH_MULT
    
    # v11g: RSI区间 (仅做多)
    if trade["direction"] == "long" and rsi is not None:
        if rsi < RSI_WEAK:
            mult *= RSI_WEAK_MULT
        # v11h新增: RSI 55-60 减仓
        elif V11H_RSI_MID_LOW <= rsi < V11H_RSI_MID_HIGH:
            mult *= V11H_RSI_MID_MULT
        elif RSI_STRONG_LOW <= rsi <= RSI_STRONG_HIGH:
            mult *= RSI_STRONG_MULT
        elif rsi >= RSI_VERY_STRONG:
            mult *= RSI_VERY_STRONG_MULT
    
    # v11g: SL%区间
    if SL_MEDIUM_LOW <= sl_pct <= SL_MEDIUM_HIGH:
        mult *= SL_MEDIUM_MULT
    elif SL_WIDE_LOW <= sl_pct <= SL_WIDE_HIGH:
        mult *= SL_WIDE_MULT
    
    # v11g: 连续亏损冷却
    if consec_losses >= CONSEC_LOSS_THRESHOLD:
        mult *= CONSEC_LOSS_MULT
    
    # v11h: 做空V8≥6.5减仓
    if trade["direction"] == "short" and v8 >= V11H_SHORT_V8HIGH_THRESHOLD:
        mult *= V11H_SHORT_V8HIGH_MULT
    
    # v11h: 低胜率币种减仓
    if trade["symbol"] in V11H_LOW_WR_SYMBOLS:
        mult *= V11H_LOW_WR_MULT
    
    return mult


def simulate(trades):
    balance = INITIAL_BALANCE
    peak = balance
    max_dd = 0
    monthly = defaultdict(list)
    consec_losses = 0
    adjusted_trades = []
    
    for t in trades:
        direction = t["direction"]
        v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
        
        # v11: 做空v8≥5减半
        base_mult = SHORT_POSITION_FACTOR if (direction == "short" and v8 >= SHORT_V8_THRESHOLD) else 1.0
        
        # v11h仓位调整
        v11h_mult = calc_position_multiplier(t, consec_losses)
        total_mult = base_mult * v11h_mult
        
        orig_pnl = t.get("pnl_usd", 0)
        adj_pnl = orig_pnl * total_mult
        
        balance += adj_pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
        
        adj_t = {**t}
        adj_t["pnl_usd"] = adj_pnl
        adj_t["position_mult"] = round(total_mult, 3)
        adj_t["running_balance"] = round(balance, 2)
        adj_t["drawdown"] = round(dd, 2)
        adjusted_trades.append(adj_t)
        monthly[t["entry_time"][:7]].append(adj_t)
        
        consec_losses = consec_losses + 1 if adj_pnl < 0 else 0
    
    return {
        "final_balance": balance,
        "total_pnl": balance - INITIAL_BALANCE,
        "max_drawdown": round(max_dd, 2),
        "trades": adjusted_trades,
        "monthly": dict(monthly),
    }


def print_results(result, trades, label="v11h"):
    total_pnl = result["total_pnl"]
    final = result["final_balance"]
    max_dd = result["max_drawdown"]
    wins = sum(1 for t in trades if t.get("pnl_usd", 0) > 0)
    losses = len(trades) - wins
    wr = wins / len(trades) * 100 if trades else 0
    
    gross_profit = sum(t.get("pnl_usd", 0) for t in trades if t.get("pnl_usd", 0) > 0)
    gross_loss = abs(sum(t.get("pnl_usd", 0) for t in trades if t.get("pnl_usd", 0) < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    avg_win = gross_profit / wins if wins > 0 else 0
    avg_loss = gross_loss / losses if losses > 0 else 0
    
    print(f"\n{'='*60}")
    print(f"📊 {label} 回测结果")
    print(f"{'='*60}")
    print(f"交易: {len(trades)}笔 | 胜: {wins} 负: {losses} | 胜率: {wr:.1f}%")
    print(f"PnL: {total_pnl:+.2f}U | 余额: {final:.2f}U | 收益: {total_pnl/INITIAL_BALANCE*100:+.1f}%")
    print(f"最大回撤: {max_dd:.1f}% | 盈亏比PF: {pf:.2f}")
    print(f"平均赢: {avg_win:+.2f}U | 平均亏: -{avg_loss:.2f}U")
    
    for lbl, d_list in [("做多", [t for t in trades if t["direction"]=="long"]),
                         ("做空", [t for t in trades if t["direction"]=="short"])]:
        if not d_list: continue
        dpnl = sum(t.get("pnl_usd", 0) for t in d_list)
        dw = sum(1 for t in d_list if t.get("pnl_usd", 0) > 0)
        print(f"  {lbl}: {len(d_list)}笔 WR={dw/len(d_list)*100:.1f}% PnL={dpnl:+.2f}U")
    
    print(f"\n📅 月度:")
    profit_months = total_months = 0
    for m in sorted(result["monthly"].keys()):
        ts = result["monthly"][m]
        mpnl = sum(t.get("pnl_usd", 0) for t in ts)
        mw = sum(1 for t in ts if t.get("pnl_usd", 0) > 0)
        icon = "📈" if mpnl > 0 else "📉"
        print(f"  {icon} {m}: {len(ts)}笔 {mpnl:+.2f}U WR={mw/len(ts)*100:.0f}%")
        total_months += 1
        if mpnl > 0: profit_months += 1
    print(f"  盈利月: {profit_months}/{total_months}")
    
    # 仓位倍率分布
    mults = [t.get("position_mult", 1.0) for t in trades]
    mult_dist = Counter(round(m, 2) for m in mults)
    print(f"\n📊 仓位倍率分布:")
    for m_val in sorted(mult_dist.keys()):
        cnt = mult_dist[m_val]
        mpnl = sum(t.get("pnl_usd", 0) for t in trades if round(t.get("position_mult", 1.0), 2) == m_val)
        print(f"  ×{m_val:.2f}: {cnt}笔 PnL={mpnl:+.2f}U")
    
    return {
        "total_trades": len(trades), "wins": wins, "losses": losses,
        "win_rate": round(wr, 1), "total_pnl": round(total_pnl, 2),
        "max_drawdown": round(max_dd, 1), "profit_factor": round(pf, 2),
        "profit_months": profit_months, "total_months": total_months,
        "final_balance": round(final, 2),
    }


def main():
    if not V10_PATH.exists():
        print(f"❌ 未找到v10数据: {V10_PATH}")
        sys.exit(1)
    
    with open(V10_PATH) as f:
        v10_data = json.load(f)
    raw_trades = v10_data.get("trades", [])
    
    print(f"📊 v10 原始: {len(raw_trades)}笔 PnL={sum(t.get('pnl_usd',0) for t in raw_trades):+.0f}U")
    
    # Step 1: v11基础过滤
    v11_trades = apply_v11_base_filter(raw_trades)
    print(f"📊 v11 基础过滤后: {len(v11_trades)}笔")
    
    # Step 2: v11h硬过滤
    v11h_trades = apply_v11h_filters(v11_trades)
    print(f"📊 v11h 过滤后: {len(v11h_trades)}笔")
    
    # Step 3: 模拟
    result = simulate(v11h_trades)
    stats = print_results(result, result["trades"], "v11h")
    
    # v11h特有分析: RSI 55-60效果
    rsi55_60 = [t for t in result["trades"] if t["direction"]=="long"]
    rsi55_60 = [t for t in rsi55_60 if get_rsi(t) is not None and V11H_RSI_MID_LOW <= get_rsi(t) < V11H_RSI_MID_HIGH]
    if rsi55_60:
        rpnl = sum(t["pnl_usd"] for t in rsi55_60)
        rw = sum(1 for t in rsi55_60 if t["pnl_usd"] > 0)
        print(f"\n📊 v11h新RSI55-60区: {len(rsi55_60)}笔 WR={rw/len(rsi55_60)*100:.0f}% PnL={rpnl:+.0f}U (v11g=-99U)")
    
    # SKYAI效果
    sky = [t for t in result["trades"] if t["symbol"] == "SKYAIUSDT"]
    if sky:
        spnl = sum(t["pnl_usd"] for t in sky)
        sw = sum(1 for t in sky if t["pnl_usd"] > 0)
        print(f"📊 SKYAIUSDT: {len(sky)}笔 WR={sw/len(sky)*100:.0f}% PnL={spnl:+.0f}U (v11g=+601U)")
    
    # 保存
    out = {
        "version": "v11h",
        "baseline": "v11g: 234笔/68.4%/+$2881/DD4.6%/PF2.08/月6/7",
        "changes": [
            "RSI 55-60 做多减仓×0.7 (v11g: -99U)",
            "ATR过滤 5.0%→5.5%",
            "SKYAIUSDT减仓×0.8",
            "做空V8≥6.5减仓×0.6",
        ],
        "initial_balance": INITIAL_BALANCE,
        **stats,
        "trades": result["trades"],
    }
    
    out_path = DATA_DIR / "backtest_v11h_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 保存到: {out_path}")
    
    # 对比v11g基线
    print(f"\n📊 v11g → v11h 对比:")
    print(f"  交易: 234 → {len(v11h_trades)}")
    print(f"  PnL: +2881 → {stats['total_pnl']:+.0f} ({stats['total_pnl']-2881:+.0f})")
    print(f"  WR: 68.4% → {stats['win_rate']:.1f}%")
    print(f"  DD: 4.6% → {stats['max_drawdown']:.1f}%")
    print(f"  PF: 2.08 → {stats['profit_factor']:.2f}")
    print(f"  月: 6/7 → {stats['profit_months']}/{stats['total_months']}")


if __name__ == "__main__":
    main()
