#!/usr/bin/env python3
"""
v11g 独立回测 — 加载 v10 数据 + v11基础过滤 + v11g仓位调整
回测维度:
  1. v11基础: 静态黑名单15币 + v8≥4 + 做空v8≥5减半
  2. v11g仓位调整:
     - V8反转: V8≤4 ×1.3(加仓), V8≥6.5 ×0.6(减仓)
     - RSI区间: 做多RSI65-75 ×1.2, RSI<50 ×0.7
     - SL区间: SL 4-6% ×0.65, SL 8-10% ×1.2
  3. v11g过滤: SL>10%跳过, ATR>5%跳过, V8≥6.5+RSI<55做多跳过
  4. 连续亏损冷却: 连续2+亏损后 ×0.7
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
V10_PATH = DATA_DIR / "backtest_v10_result.json"

# ═══ v11 基础参数 ═══
STATIC_BLACKLIST = {
    "ZECUSDT", "DUSDT", "NILUSDT", "SOLUSDT", "DOGEUSDT",
    "CLUSDT", "KSMUSDT", "HYPEUSDT", "TAOUSDT", "PUMPUSDT",
    "BZUSDT", "FILUSDT", "WLFIUSDT", "ONDOUSDT", "ENAUSDT",
}
V11_MIN_V8_SCORE = 4
SHORT_V8_THRESHOLD = 5
SHORT_POSITION_FACTOR = 0.5

# ═══ v11g 仓位调整参数 (from config.py) ═══
# V8反转
V8_LOW_THRESHOLD = 4.0
V8_LOW_MULT = 1.3
V8_HIGH_THRESHOLD = 6.5
V8_HIGH_MULT = 0.6

# RSI区间 (仅做多)
RSI_WEAK = 50
RSI_WEAK_MULT = 0.7
RSI_STRONG_LOW = 65
RSI_STRONG_HIGH = 75
RSI_STRONG_MULT = 1.2
RSI_VERY_STRONG = 75
RSI_VERY_STRONG_MULT = 1.1

# SL%区间
SL_MEDIUM_LOW = 4.0
SL_MEDIUM_HIGH = 6.0
SL_MEDIUM_MULT = 0.65
SL_WIDE_LOW = 8.0
SL_WIDE_HIGH = 10.0
SL_WIDE_MULT = 1.2

# 过滤规则
MAX_SL_PCT = 10.0
MAX_ATR_PCT = 5.0
FILTER_V8_RSI = True

# 连续亏损冷却
CONSEC_LOSS_THRESHOLD = 2
CONSEC_LOSS_MULT = 0.7

INITIAL_BALANCE = 5000.0


def get_rsi(trade):
    """从tech_snapshot提取RSI"""
    ts = trade.get("tech_snapshot", {})
    if isinstance(ts, dict):
        return ts.get("rsi", None)
    return None


def get_atr_pct(trade):
    """从tech_snapshot提取ATR%"""
    ts = trade.get("tech_snapshot", {})
    if isinstance(ts, dict):
        return ts.get("atr_pct", None)
    return None


def apply_v11_base_filter(trades):
    """v11基础过滤: 黑名单 + v8≥4"""
    kept = []
    for t in trades:
        if t["symbol"] in STATIC_BLACKLIST:
            continue
        v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
        if v8 < V11_MIN_V8_SCORE:
            continue
        kept.append(t)
    return kept


def apply_v11g_filters(trades):
    """v11g硬过滤: SL>10%, ATR>5%, V8≥6.5+RSI<55做多跳过"""
    kept = []
    skipped_sl = skipped_atr = skipped_v8rsi = 0
    
    for t in trades:
        sl_pct = t.get("signal_sl_pct", 0) * 100  # 转百分比
        
        # SL>10%跳过
        if sl_pct > MAX_SL_PCT:
            skipped_sl += 1
            continue
        
        # ATR>5%跳过
        atr_pct = get_atr_pct(t)
        if atr_pct is not None and atr_pct * 100 > MAX_ATR_PCT:
            skipped_atr += 1
            continue
        
        # V8≥6.5 + RSI<55 做多跳过
        if FILTER_V8_RSI and t["direction"] == "long":
            v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
            rsi = get_rsi(t)
            if v8 >= V8_HIGH_THRESHOLD and rsi is not None and rsi < 55:
                skipped_v8rsi += 1
                continue
        
        kept.append(t)
    
    print(f"  v11g过滤跳过: SL>{MAX_SL_PCT}%={skipped_sl}, ATR>{MAX_ATR_PCT}%={skipped_atr}, V8+RSI={skipped_v8rsi}")
    return kept


def calc_position_multiplier(trade, consec_losses):
    """计算v11g综合仓位倍率"""
    mult = 1.0
    v8 = trade.get("v8_score", 0) or trade.get("v8_quality", 0)
    rsi = get_rsi(trade)
    sl_pct = trade.get("signal_sl_pct", 0) * 100  # 百分比
    
    # 1. V8反转
    if v8 <= V8_LOW_THRESHOLD:
        mult *= V8_LOW_MULT
    elif v8 >= V8_HIGH_THRESHOLD:
        mult *= V8_HIGH_MULT
    
    # 2. RSI区间 (仅做多)
    if trade["direction"] == "long" and rsi is not None:
        if rsi < RSI_WEAK:
            mult *= RSI_WEAK_MULT
        elif RSI_STRONG_LOW <= rsi <= RSI_STRONG_HIGH:
            mult *= RSI_STRONG_MULT
        elif rsi >= RSI_VERY_STRONG:
            mult *= RSI_VERY_STRONG_MULT
    
    # 3. SL%区间
    if SL_MEDIUM_LOW <= sl_pct <= SL_MEDIUM_HIGH:
        mult *= SL_MEDIUM_MULT
    elif SL_WIDE_LOW <= sl_pct <= SL_WIDE_HIGH:
        mult *= SL_WIDE_MULT
    
    # 4. 连续亏损冷却
    if consec_losses >= CONSEC_LOSS_THRESHOLD:
        mult *= CONSEC_LOSS_MULT
    
    return mult


def simulate(trades):
    """模拟余额曲线，带v11g仓位调整"""
    balance = INITIAL_BALANCE
    peak = balance
    max_dd = 0
    monthly = defaultdict(list)
    consec_losses = 0
    
    adjusted_trades = []
    
    for t in trades:
        direction = t["direction"]
        v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
        
        # 做空v8≥5减半
        if direction == "short" and v8 >= SHORT_V8_THRESHOLD:
            base_mult = SHORT_POSITION_FACTOR
        else:
            base_mult = 1.0
        
        # v11g仓位调整
        v11g_mult = calc_position_multiplier(t, consec_losses)
        
        total_mult = base_mult * v11g_mult
        
        # 调整PnL
        orig_pnl = t.get("pnl_usd", 0)
        orig_pct = t.get("pnl_pct", 0)
        adj_pnl = orig_pnl * total_mult
        adj_pct = orig_pct * total_mult
        
        balance += adj_pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
        
        month = t["entry_time"][:7]
        
        adj_t = {**t}
        adj_t["pnl_usd"] = adj_pnl
        adj_t["pnl_pct"] = adj_pct
        adj_t["position_mult"] = round(total_mult, 3)
        adj_t["base_mult"] = base_mult
        adj_t["v11g_mult"] = round(v11g_mult, 3)
        adj_t["running_balance"] = round(balance, 2)
        adj_t["drawdown"] = round(dd, 2)
        
        adjusted_trades.append(adj_t)
        monthly[month].append(adj_t)
        
        # 更新连续亏损
        if adj_pnl < 0:
            consec_losses += 1
        else:
            consec_losses = 0
    
    return {
        "final_balance": balance,
        "total_pnl": balance - INITIAL_BALANCE,
        "max_drawdown": round(max_dd, 2),
        "trades": adjusted_trades,
        "monthly": dict(monthly),
    }


def print_results(result, trades, label="v11g"):
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
    avg_rr = avg_win / avg_loss if avg_loss > 0 else float('inf')
    
    print(f"\n{'='*60}")
    print(f"📊 {label} 回测结果")
    print(f"{'='*60}")
    print(f"交易: {len(trades)}笔 | 胜: {wins} 负: {losses} | 胜率: {wr:.1f}%")
    print(f"PnL: {total_pnl:+.2f}U | 余额: {final:.2f}U | 收益: {total_pnl/INITIAL_BALANCE*100:+.1f}%")
    print(f"最大回撤: {max_dd:.1f}% | 盈亏比PF: {pf:.2f} | 平均RR: {avg_rr:.2f}")
    print(f"平均赢: {avg_win:+.2f}U | 平均亏: {avg_loss:.2f}U")
    
    # 方向统计
    longs = [t for t in trades if t["direction"] == "long"]
    shorts = [t for t in trades if t["direction"] == "short"]
    for label_d, d_list in [("做多", longs), ("做空", shorts)]:
        if not d_list:
            continue
        dpnl = sum(t.get("pnl_usd", 0) for t in d_list)
        dw = sum(1 for t in d_list if t.get("pnl_usd", 0) > 0)
        dwr = dw / len(d_list) * 100
        print(f"  {label_d}: {len(d_list)}笔 WR={dwr:.1f}% PnL={dpnl:+.2f}U")
    
    # 月度
    print(f"\n📅 月度表现:")
    profit_months = 0
    total_months = 0
    for m in sorted(result["monthly"].keys()):
        ts = result["monthly"][m]
        mpnl = sum(t.get("pnl_usd", 0) for t in ts)
        mw = sum(1 for t in ts if t.get("pnl_usd", 0) > 0)
        mwr = mw / len(ts) * 100 if ts else 0
        icon = "📈" if mpnl > 0 else "📉"
        print(f"  {icon} {m}: {len(ts)}笔 {mpnl:+.2f}U WR={mwr:.0f}%")
        total_months += 1
        if mpnl > 0:
            profit_months += 1
    print(f"  盈利月: {profit_months}/{total_months}")
    
    # 仓位倍率分布
    mults = [t.get("position_mult", 1.0) for t in trades]
    from collections import Counter
    mult_dist = Counter(round(m, 2) for m in mults)
    print(f"\n📊 仓位倍率分布:")
    for m_val in sorted(mult_dist.keys()):
        cnt = mult_dist[m_val]
        mpnl = sum(t.get("pnl_usd", 0) for t in trades if round(t.get("position_mult", 1.0), 2) == m_val)
        print(f"  ×{m_val:.2f}: {cnt}笔 PnL={mpnl:+.2f}U")
    
    return {
        "total_trades": len(trades),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr, 1),
        "total_pnl": round(total_pnl, 2),
        "max_drawdown": round(max_dd, 1),
        "profit_factor": round(pf, 2),
        "profit_months": profit_months,
        "total_months": total_months,
        "final_balance": round(final, 2),
    }


def main():
    if not V10_PATH.exists():
        print(f"❌ 未找到v10数据: {V10_PATH}")
        print("先运行: python3 backtest_v10.py --days 180")
        sys.exit(1)
    
    with open(V10_PATH) as f:
        v10_data = json.load(f)
    raw_trades = v10_data.get("trades", [])
    
    print(f"📊 v10 原始数据: {len(raw_trades)}笔")
    raw_pnl = sum(t.get("pnl_usd", 0) for t in raw_trades)
    raw_wins = sum(1 for t in raw_trades if t.get("pnl_usd", 0) > 0)
    print(f"   PnL={raw_pnl:+.2f}U WR={raw_wins/len(raw_trades)*100:.1f}%")
    
    # Step 1: v11基础过滤
    print(f"\n📊 Step 1: v11基础过滤 (黑名单+v8≥4)")
    v11_trades = apply_v11_base_filter(raw_trades)
    v11_pnl = sum(t.get("pnl_usd", 0) for t in v11_trades)
    print(f"   → {len(v11_trades)}笔 PnL={v11_pnl:+.2f}U")
    
    # Step 2: v11g硬过滤
    print(f"\n📊 Step 2: v11g过滤 (SL>10%/ATR>5%/V8+RSI)")
    v11g_trades = apply_v11g_filters(v11_trades)
    print(f"   → {len(v11g_trades)}笔")
    
    # Step 3: 模拟+仓位调整
    print(f"\n📊 Step 3: 模拟交易 (含v11g仓位调整)")
    result = simulate(v11g_trades)
    
    # 打印结果
    stats = print_results(result, result["trades"], "v11g")
    
    # 保存
    out = {
        "version": "v11g",
        "source": "backtest_v10_result.json",
        "params": {
            "static_blacklist": sorted(STATIC_BLACKLIST),
            "min_v8_score": V11_MIN_V8_SCORE,
            "short_v8_threshold": SHORT_V8_THRESHOLD,
            "short_position_factor": SHORT_POSITION_FACTOR,
            "v8_low_threshold": V8_LOW_THRESHOLD,
            "v8_low_mult": V8_LOW_MULT,
            "v8_high_threshold": V8_HIGH_THRESHOLD,
            "v8_high_mult": V8_HIGH_MULT,
            "rsi_strong_range": [RSI_STRONG_LOW, RSI_STRONG_HIGH],
            "rsi_strong_mult": RSI_STRONG_MULT,
            "sl_medium_range": [SL_MEDIUM_LOW, SL_MEDIUM_HIGH],
            "sl_medium_mult": SL_MEDIUM_MULT,
            "sl_wide_range": [SL_WIDE_LOW, SL_WIDE_HIGH],
            "sl_wide_mult": SL_WIDE_MULT,
            "max_sl_pct": MAX_SL_PCT,
            "max_atr_pct": MAX_ATR_PCT,
            "consec_loss_threshold": CONSEC_LOSS_THRESHOLD,
            "consec_loss_mult": CONSEC_LOSS_MULT,
        },
        "initial_balance": INITIAL_BALANCE,
        **stats,
        "trades": result["trades"],
    }
    
    out_path = DATA_DIR / "backtest_v11g_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 保存到: {out_path}")
    
    # 对比v10基线
    print(f"\n📊 v10 → v11g 对比:")
    print(f"  交易: {len(raw_trades)} → {len(v11g_trades)} (-{len(raw_trades)-len(v11g_trades)})")
    print(f"  PnL: {raw_pnl:+.2f} → {stats['total_pnl']:+.2f} ({stats['total_pnl']-raw_pnl:+.2f})")
    v10_wr = raw_wins/len(raw_trades)*100
    print(f"  WR: {v10_wr:.1f}% → {stats['win_rate']:.1f}%")


if __name__ == "__main__":
    main()
