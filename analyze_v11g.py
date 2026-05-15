#!/usr/bin/env python3
"""
v11g回测数据深度分析 — 1000U动态余额模拟
找出可优化模式，输出12项分析 + 优化建议
"""
import json
import sys
from collections import defaultdict
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent / "data"
V10_PATH = DATA_DIR / "backtest_v10_result.json"
PYTHON = "/home/ubuntu/.hermes/hermes-agent/venv/bin/python3"

# ═══ v11g参数 (from backtest_v11_compare_1000u.py) ═══
STATIC_BLACKLIST = {
    "ZECUSDT", "DUSDT", "NILUSDT", "SOLUSDT", "DOGEUSDT",
    "CLUSDT", "KSMUSDT", "HYPEUSDT", "TAOUSDT", "PUMPUSDT",
    "BZUSDT", "FILUSDT", "WLFIUSDT", "ONDOUSDT", "ENAUSDT",
}
V11_MIN_V8_SCORE = 4
SHORT_V8_THRESHOLD = 5
SHORT_POSITION_FACTOR = 0.5

V11G_PARAMS = {
    "v8_low_threshold": 4.0, "v8_low_mult": 1.3,
    "v8_high_threshold": 6.5, "v8_high_mult_long": 0.6, "v8_high_mult_short": 0.6,
    "rsi_weak": 50, "rsi_weak_mult": 0.7,
    "rsi_mid_low": None, "rsi_mid_high": None, "rsi_mid_mult": None,
    "rsi_strong_low": 65, "rsi_strong_high": 75, "rsi_strong_mult": 1.2,
    "rsi_very_strong": 75, "rsi_very_strong_mult": 1.1,
    "sl_medium_low": 4.0, "sl_medium_high": 6.0, "sl_medium_mult": 0.65,
    "sl_wide_low": 8.0, "sl_wide_high": 10.0, "sl_wide_mult": 1.2,
    "max_sl_pct": 10.0, "max_atr_pct": 5.0, "filter_v8_rsi": True,
    "consec_loss_threshold": 2, "consec_loss_mult": 0.7,
}


def get_rsi(t):
    ts = t.get("tech_snapshot", {})
    return ts.get("rsi") if isinstance(ts, dict) else None

def get_atr_pct(t):
    ts = t.get("tech_snapshot", {})
    return ts.get("atr_pct") if isinstance(ts, dict) else None

def get_v8(t):
    return t.get("v8_score", 0) or t.get("v8_quality", 0)


def calc_mult_v11g(trade, consec_losses):
    """复制backtest_v11_compare_1000u.py中的calc_mult逻辑"""
    params = V11G_PARAMS
    m = 1.0
    v8 = get_v8(trade)
    rsi = get_rsi(trade)
    sl_pct = trade.get("signal_sl_pct", 0) * 100
    d = trade["direction"]

    if v8 <= params["v8_low_threshold"]:
        m *= params["v8_low_mult"]
    elif v8 >= params["v8_high_threshold"]:
        m *= params["v8_high_mult_long"] if d == "long" else params["v8_high_mult_short"]

    if d == "long" and rsi is not None:
        if rsi < params["rsi_weak"]:
            m *= params["rsi_weak_mult"]
        elif params.get("rsi_mid_low") is not None and params["rsi_mid_low"] <= rsi < params["rsi_mid_high"]:
            m *= params["rsi_mid_mult"]
        elif params["rsi_strong_low"] <= rsi <= params["rsi_strong_high"]:
            m *= params["rsi_strong_mult"]
        elif rsi >= params["rsi_very_strong"]:
            m *= params["rsi_very_strong_mult"]

    if params["sl_medium_low"] <= sl_pct <= params["sl_medium_high"]:
        m *= params["sl_medium_mult"]
    elif params["sl_wide_low"] <= sl_pct <= params["sl_wide_high"]:
        m *= params["sl_wide_mult"]

    if consec_losses >= params["consec_loss_threshold"]:
        m *= params["consec_loss_mult"]

    return m


def apply_v11_base_filter(trades):
    return [t for t in trades
            if t["symbol"] not in STATIC_BLACKLIST
            and get_v8(t) >= V11_MIN_V8_SCORE]


def apply_hard_filters_v11g(trades):
    params = V11G_PARAMS
    kept = []
    for t in trades:
        sl_pct = t.get("signal_sl_pct", 0) * 100
        if sl_pct > params["max_sl_pct"]:
            continue
        atr = get_atr_pct(t)
        if atr is not None and atr * 100 > params["max_atr_pct"]:
            continue
        if params["filter_v8_rsi"] and t["direction"] == "long":
            v8 = get_v8(t)
            rsi = get_rsi(t)
            if v8 >= params["v8_high_threshold"] and rsi is not None and rsi < 55:
                continue
        kept.append(t)
    return kept


def simulate_v11g_detailed(trades):
    """
    模拟v11g并收集每笔交易的详细信息用于分析
    """
    bal_5000 = 5000.0
    bal_1000 = 1000.0
    consec = 0
    
    detailed_trades = []
    
    for t in trades:
        v8 = get_v8(t)
        base = SHORT_POSITION_FACTOR if (t["direction"] == "short" and v8 >= SHORT_V8_THRESHOLD) else 1.0
        mult = base * calc_mult_v11g(t, consec)
        
        orig_pnl_5000 = t.get("pnl_usd", 0)
        scale = bal_1000 / bal_5000 if bal_5000 > 0 else 0.2
        
        new_pos_usd = t.get("position_usd", 0) * scale
        new_pnl_raw = orig_pnl_5000 * scale
        new_pnl = new_pnl_raw * mult
        
        # 计算持仓时间（小时）
        entry_time = t.get("entry_time", "")
        exit_time = t.get("exit_time", "")
        hold_hours = None
        if entry_time and exit_time:
            try:
                et = datetime.fromisoformat(entry_time.replace("Z", ""))
                xt = datetime.fromisoformat(exit_time.replace("Z", ""))
                hold_hours = (xt - et).total_seconds() / 3600
            except:
                pass
        
        detail = {
            "id": t.get("id"),
            "symbol": t.get("symbol"),
            "direction": t.get("direction"),
            "signal_type": t.get("signal_type"),
            "entry_time": entry_time,
            "exit_time": exit_time,
            "hold_hours": hold_hours,
            "v8_score": v8,
            "rsi": get_rsi(t),
            "atr_pct": get_atr_pct(t),
            "sl_pct": t.get("signal_sl_pct", 0) * 100,
            "pnl_usd": round(new_pnl, 4),
            "pnl_pct": t.get("pnl_pct", 0),
            "position_usd": round(new_pos_usd, 4),
            "mult": round(mult, 4),
            "base_mult": base,
            "balance_after": round(bal_1000 + new_pnl, 4),
            "consec_losses_before": consec,
        }
        detailed_trades.append(detail)
        
        bal_5000 += orig_pnl_5000
        bal_1000 += new_pnl
        
        consec = consec + 1 if new_pnl < 0 else 0
    
    return detailed_trades


def calc_stats(trades_list):
    """计算一组交易的统计指标"""
    if not trades_list:
        return {"count": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "avg_pnl": 0, "total_pnl": 0, "pf": 0, "gp": 0, "gl": 0}
    n = len(trades_list)
    wins = sum(1 for t in trades_list if t["pnl_usd"] > 0)
    losses = sum(1 for t in trades_list if t["pnl_usd"] < 0)
    be = n - wins - losses
    gp = sum(t["pnl_usd"] for t in trades_list if t["pnl_usd"] > 0)
    gl = sum(abs(t["pnl_usd"]) for t in trades_list if t["pnl_usd"] < 0)
    total = sum(t["pnl_usd"] for t in trades_list)
    avg = total / n if n else 0
    wr = wins / n * 100 if n else 0
    pf = gp / gl if gl > 0 else 99.0
    return {"count": n, "wins": wins, "losses": losses, "be": be,
            "win_rate": round(wr, 1), "avg_pnl": round(avg, 4),
            "total_pnl": round(total, 4), "pf": round(pf, 2),
            "gp": round(gp, 2), "gl": round(gl, 2)}


def print_section(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def print_stats_table(stats_dict, key_name="分组"):
    """打印统计表格"""
    print(f"  {key_name:<20} {'笔数':>5} {'胜率':>7} {'总PnL':>10} {'平均PnL':>9} {'PF':>6}")
    print(f"  {'─'*20} {'─'*5} {'─'*7} {'─'*10} {'─'*9} {'─'*6}")
    for k, s in stats_dict.items():
        label = str(k)
        print(f"  {label:<20} {s['count']:>5} {s['win_rate']:>6.1f}% {s['total_pnl']:>+9.2f}U {s['avg_pnl']:>+8.4f}U {s['pf']:>6.2f}")


def main():
    if not V10_PATH.exists():
        print(f"❌ 未找到v10数据: {V10_PATH}")
        sys.exit(1)

    with open(V10_PATH) as f:
        raw = json.load(f).get("trades", [])

    print(f"v10原始数据: {len(raw)}笔")

    # 应用v11g过滤
    base_filtered = apply_v11_base_filter(raw)
    filtered = apply_hard_filters_v11g(base_filtered)
    print(f"v11g过滤后: {len(filtered)}笔\n")

    # 模拟
    trades = simulate_v11g_detailed(filtered)

    # ═══ 总体统计 ═══
    overall = calc_stats(trades)
    print_section("总体统计")
    print(f"  总交易: {overall['count']}笔 | 胜率: {overall['win_rate']}% | 总PnL: {overall['total_pnl']:+.2f}U | PF: {overall['pf']}")

    # ═══ 1. 亏损集中度分析 ═══
    print_section("1. 亏损集中度分析（月度）")
    monthly = defaultdict(list)
    for t in trades:
        m = t["entry_time"][:7]
        monthly[m].append(t)
    
    monthly_stats = {}
    for m in sorted(monthly.keys()):
        monthly_stats[m] = calc_stats(monthly[m])
    
    print(f"  {'月份':<10} {'笔数':>5} {'胜率':>7} {'总PnL':>10} {'PF':>6}")
    print(f"  {'─'*10} {'─'*5} {'─'*7} {'─'*10} {'─'*6}")
    for m, s in sorted(monthly_stats.items()):
        icon = "📈" if s['total_pnl'] > 0 else "📉"
        print(f"  {icon} {m:<8} {s['count']:>5} {s['win_rate']:>6.1f}% {s['total_pnl']:>+9.2f}U {s['pf']:>6.2f}")
    
    # 亏损月特征
    loss_months = {m: s for m, s in monthly_stats.items() if s['total_pnl'] < 0}
    if loss_months:
        print(f"\n  亏损月({len(loss_months)}个)特征分析:")
        # 亏损月中的方向分布
        lm_long = [t for m in loss_months for t in monthly[m] if t["direction"] == "long"]
        lm_short = [t for m in loss_months for t in monthly[m] if t["direction"] == "short"]
        lm_long_s = calc_stats(lm_long)
        lm_short_s = calc_stats(lm_short)
        print(f"    做多: {lm_long_s['count']}笔 胜率{lm_long_s['win_rate']}% PnL{lm_long_s['total_pnl']:+.2f}U PF={lm_long_s['pf']}")
        print(f"    做空: {lm_short_s['count']}笔 胜率{lm_short_s['win_rate']}% PnL{lm_short_s['total_pnl']:+.2f}U PF={lm_short_s['pf']}")
        
        # 亏损月V8分布
        lm_v8 = [t["v8_score"] for m in loss_months for t in monthly[m]]
        if lm_v8:
            print(f"    V8均值: {sum(lm_v8)/len(lm_v8):.2f} (整体: {sum(t['v8_score'] for t in trades)/len(trades):.2f})")
        # 亏损月RSI分布
        lm_rsi = [t["rsi"] for m in loss_months for t in monthly[m] if t["rsi"] is not None]
        all_rsi = [t["rsi"] for t in trades if t["rsi"] is not None]
        if lm_rsi:
            print(f"    RSI均值: {sum(lm_rsi)/len(lm_rsi):.1f} (整体: {sum(all_rsi)/len(all_rsi):.1f})")

    # ═══ 2. 做空vs做多 ═══
    print_section("2. 做空 vs 做多")
    longs = [t for t in trades if t["direction"] == "long"]
    shorts = [t for t in trades if t["direction"] == "short"]
    long_s = calc_stats(longs)
    short_s = calc_stats(shorts)
    stats_d = {"做多 long": long_s, "做空 short": short_s}
    print_stats_table(stats_d, "方向")

    # ═══ 3. 信号类型分析 ═══
    print_section("3. 信号类型分析")
    signal_types = defaultdict(list)
    for t in trades:
        signal_types[t.get("signal_type", "unknown")].append(t)
    sig_stats = {k: calc_stats(v) for k, v in signal_types.items()}
    print_stats_table(sig_stats, "信号类型")

    # ═══ 4. V8评分分段分析 ═══
    print_section("4. V8评分分段分析")
    v8_ranges = [
        ("V8=4-5", 4, 5),
        ("V8=5-6", 5, 6),
        ("V8=6-7", 6, 7),
        ("V8=7+", 7, 999),
    ]
    v8_stats = {}
    for label, lo, hi in v8_ranges:
        group = [t for t in trades if lo <= t["v8_score"] < hi]
        v8_stats[label] = calc_stats(group)
    print_stats_table(v8_stats, "V8分段")
    
    # 更细的分段
    print("\n  V8细分（0.5步进）:")
    v8_fine = defaultdict(list)
    for t in trades:
        bucket = f"V8={t['v8_score']:.1f}"
        v8_fine[bucket].append(t)
    print(f"  {'V8值':<10} {'笔数':>5} {'胜率':>7} {'总PnL':>10} {'平均PnL':>9} {'PF':>6}")
    for v8v in sorted(v8_fine.keys(), key=lambda x: float(x.split("=")[1])):
        s = calc_stats(v8_fine[v8v])
        print(f"  {v8v:<10} {s['count']:>5} {s['win_rate']:>6.1f}% {s['total_pnl']:>+9.2f}U {s['avg_pnl']:>+8.4f}U {s['pf']:>6.2f}")

    # ═══ 5. RSI区间分析 ═══
    print_section("5. RSI区间分析")
    rsi_ranges = [
        ("RSI<40", 0, 40),
        ("RSI=40-50", 40, 50),
        ("RSI=50-60", 50, 60),
        ("RSI=60-70", 60, 70),
        ("RSI=70-80", 70, 80),
        ("RSI>80", 80, 999),
    ]
    print("  === 做多 ===")
    rsi_long_stats = {}
    for label, lo, hi in rsi_ranges:
        group = [t for t in longs if t["rsi"] is not None and lo <= t["rsi"] < hi]
        rsi_long_stats[label] = calc_stats(group)
    print_stats_table(rsi_long_stats, "RSI区间(做多)")
    
    print("\n  === 做空 ===")
    rsi_short_stats = {}
    for label, lo, hi in rsi_ranges:
        group = [t for t in shorts if t["rsi"] is not None and lo <= t["rsi"] < hi]
        rsi_short_stats[label] = calc_stats(group)
    print_stats_table(rsi_short_stats, "RSI区间(做空)")

    # ═══ 6. ATR区间分析 ═══
    print_section("6. ATR区间分析")
    atr_ranges = [
        ("ATR<2%", 0, 0.02),
        ("ATR=2-3%", 0.02, 0.03),
        ("ATR=3-4%", 0.03, 0.04),
        ("ATR=4-5%", 0.04, 0.05),
        ("ATR>5%", 0.05, 999),
    ]
    atr_stats = {}
    for label, lo, hi in atr_ranges:
        group = [t for t in trades if t["atr_pct"] is not None and lo <= t["atr_pct"] < hi]
        atr_stats[label] = calc_stats(group)
    print_stats_table(atr_stats, "ATR区间")

    # ═══ 7. 持仓时间分析 ═══
    print_section("7. 持仓时间分析")
    with_hold = [t for t in trades if t["hold_hours"] is not None]
    hold_ranges = [
        ("<2h", 0, 2),
        ("2-6h", 2, 6),
        ("6-12h", 2, 12),
        ("12-24h", 12, 24),
        ("24-48h", 24, 48),
        ("48-72h", 48, 72),
        (">72h", 72, 9999),
    ]
    hold_stats = {}
    for label, lo, hi in hold_ranges:
        group = [t for t in with_hold if lo <= t["hold_hours"] < hi]
        hold_stats[label] = calc_stats(group)
    print_stats_table(hold_stats, "持仓时间")
    
    # 盈利单 vs 亏损单持仓时间
    win_hold = [t["hold_hours"] for t in with_hold if t["pnl_usd"] > 0 and t["hold_hours"] is not None]
    loss_hold = [t["hold_hours"] for t in with_hold if t["pnl_usd"] < 0 and t["hold_hours"] is not None]
    if win_hold:
        print(f"\n  盈利单平均持仓: {sum(win_hold)/len(win_hold):.1f}h (中位数: {sorted(win_hold)[len(win_hold)//2]:.1f}h)")
    if loss_hold:
        print(f"  亏损单平均持仓: {sum(loss_hold)/len(loss_hold):.1f}h (中位数: {sorted(loss_hold)[len(loss_hold)//2]:.1f}h)")

    # ═══ 8. 连续亏损分析 ═══
    print_section("8. 连续亏损分析")
    max_consec = 0
    cur_consec = 0
    consec_runs = []  # (start_idx, length)
    run_start = None
    
    for i, t in enumerate(trades):
        if t["pnl_usd"] < 0:
            cur_consec += 1
            if run_start is None:
                run_start = i
            if cur_consec > max_consec:
                max_consec = cur_consec
        else:
            if cur_consec > 0:
                consec_runs.append((run_start, cur_consec))
            cur_consec = 0
            run_start = None
    if cur_consec > 0:
        consec_runs.append((run_start, cur_consec))
    
    print(f"  最大连续亏损: {max_consec}次")
    
    # 连亏分布
    consec_dist = defaultdict(int)
    for _, length in consec_runs:
        consec_dist[length] += 1
    print(f"  连亏次数分布:")
    for k in sorted(consec_dist.keys()):
        print(f"    连亏{k}次: {consec_dist[k]}次发生")
    
    # 连亏后恢复情况
    print(f"\n  连亏后恢复情况:")
    for threshold in [2, 3, 4, 5]:
        after_runs = []
        for start_idx, length in consec_runs:
            if length >= threshold:
                end_idx = start_idx + length
                # 恢复期: 连亏结束后接下来5笔
                recovery = trades[end_idx:end_idx + 5]
                if recovery:
                    recovery_pnl = sum(t["pnl_usd"] for t in recovery)
                    recovery_wr = sum(1 for t in recovery if t["pnl_usd"] > 0) / len(recovery) * 100
                    after_runs.append((recovery_pnl, recovery_wr, len(recovery)))
        if after_runs:
            avg_pnl = sum(r[0] for r in after_runs) / len(after_runs)
            avg_wr = sum(r[1] for r in after_runs) / len(after_runs)
            print(f"    连亏≥{threshold}次后(5笔内): 平均PnL {avg_pnl:+.2f}U, 平均胜率 {avg_wr:.1f}% ({len(after_runs)}次)")

    # ═══ 9. 单笔最大亏损/盈利 Top10 ═══
    print_section("9. 单笔最大盈利/亏损 Top10")
    sorted_by_pnl = sorted(trades, key=lambda t: t["pnl_usd"], reverse=True)
    
    print("\n  === Top10 盈利 ===")
    print(f"  {'#':>3} {'PnL':>9} {'币种':<18} {'方向':<6} {'V8':>4} {'RSI':>6} {'ATR%':>6} {'SL%':>6} {'持仓h':>7} {'mult':>6}")
    for i, t in enumerate(sorted_by_pnl[:10]):
        atr_s = f"{t['atr_pct']*100:.1f}" if t['atr_pct'] else "N/A"
        rsi_s = f"{t['rsi']:.1f}" if t['rsi'] else "N/A"
        hold_s = f"{t['hold_hours']:.1f}" if t['hold_hours'] else "N/A"
        print(f"  {i+1:>3} {t['pnl_usd']:>+8.2f}U {t['symbol']:<18} {t['direction']:<6} {t['v8_score']:>4} {rsi_s:>6} {atr_s:>6} {t['sl_pct']:>5.1f} {hold_s:>7} {t['mult']:>6.3f}")
    
    print("\n  === Top10 亏损 ===")
    for i, t in enumerate(sorted_by_pnl[-10:]):
        atr_s = f"{t['atr_pct']*100:.1f}" if t['atr_pct'] else "N/A"
        rsi_s = f"{t['rsi']:.1f}" if t['rsi'] else "N/A"
        hold_s = f"{t['hold_hours']:.1f}" if t['hold_hours'] else "N/A"
        print(f"  {i+1:>3} {t['pnl_usd']:>+8.2f}U {t['symbol']:<18} {t['direction']:<6} {t['v8_score']:>4} {rsi_s:>6} {atr_s:>6} {t['sl_pct']:>5.1f} {hold_s:>7} {t['mult']:>6.3f}")
    
    # Top10盈利和亏损的特征对比
    top10_win = sorted_by_pnl[:10]
    top10_loss = sorted_by_pnl[-10:]
    print(f"\n  盈利Top10特征: 平均V8={sum(t['v8_score'] for t in top10_win)/10:.1f} | 平均SL%={sum(t['sl_pct'] for t in top10_win)/10:.1f} | 做多占比={sum(1 for t in top10_win if t['direction']=='long')}/10")
    print(f"  亏损Top10特征: 平均V8={sum(t['v8_score'] for t in top10_loss)/10:.1f} | 平均SL%={sum(t['sl_pct'] for t in top10_loss)/10:.1f} | 做多占比={sum(1 for t in top10_loss if t['direction']=='long')}/10")

    # ═══ 10. 周内效应 ═══
    print_section("10. 周内效应")
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday_trades = defaultdict(list)
    for t in trades:
        try:
            dt = datetime.fromisoformat(t["entry_time"].replace("Z", ""))
            weekday_trades[dt.weekday()].append(t)
        except:
            pass
    
    wd_stats = {}
    for wd in range(7):
        wd_stats[weekday_names[wd]] = calc_stats(weekday_trades.get(wd, []))
    print_stats_table(wd_stats, "星期")

    # ═══ 11. 同币种重复交易分析 ═══
    print_section("11. 同币种重复交易分析")
    symbol_occurrence = defaultdict(int)
    symbol_nth = defaultdict(list)  # symbol -> [(nth, trade)]
    
    for t in trades:
        sym = t["symbol"]
        symbol_occurrence[sym] += 1
        nth = symbol_occurrence[sym]
        symbol_nth[sym].append((nth, t))
    
    # 第N次交易的表现
    nth_stats = defaultdict(list)
    for sym, trades_list in symbol_nth.items():
        for nth, t in trades_list:
            nth_stats[nth].append(t)
    
    print(f"  {'第N次':>6} {'笔数':>5} {'胜率':>7} {'总PnL':>10} {'平均PnL':>9} {'PF':>6}")
    print(f"  {'─'*6} {'─'*5} {'─'*7} {'─'*10} {'─'*9} {'─'*6}")
    for n in sorted(nth_stats.keys())[:8]:
        s = calc_stats(nth_stats[n])
        print(f"  第{n}次 {s['count']:>5} {s['win_rate']:>6.1f}% {s['total_pnl']:>+9.2f}U {s['avg_pnl']:>+8.4f}U {s['pf']:>6.2f}")
    
    # 高频币种表现
    print(f"\n  高频交易币种(≥20次):")
    print(f"  {'币种':<18} {'次数':>5} {'胜率':>7} {'总PnL':>10} {'PF':>6}")
    print(f"  {'─'*18} {'─'*5} {'─'*7} {'─'*10} {'─'*6}")
    freq_symbols = {sym: cnt for sym, cnt in symbol_occurrence.items() if cnt >= 20}
    for sym in sorted(freq_symbols.keys(), key=lambda s: calc_stats([t for _, t in symbol_nth[s]])["total_pnl"]):
        sym_trades = [t for _, t in symbol_nth[sym]]
        s = calc_stats(sym_trades)
        print(f"  {sym:<18} {s['count']:>5} {s['win_rate']:>6.1f}% {s['total_pnl']:>+9.2f}U {s['pf']:>6.2f}")

    # ═══ 12. 仓位调整系数mult分布 ═══
    print_section("12. 仓位调整系数mult分布")
    mult_ranges = [
        ("mult<0.5", 0, 0.5),
        ("0.5-0.7", 0.5, 0.7),
        ("0.7-1.0", 0.7, 1.0),
        ("1.0-1.3", 1.0, 1.3),
        ("1.3+", 1.3, 999),
    ]
    mult_stats = {}
    for label, lo, hi in mult_ranges:
        group = [t for t in trades if lo <= t["mult"] < hi]
        mult_stats[label] = calc_stats(group)
    print_stats_table(mult_stats, "mult区间")
    
    # mult细分布
    print("\n  mult精确分布:")
    mult_dist = defaultdict(int)
    for t in trades:
        m = t["mult"]
        if m < 0.3: mult_dist["<0.3"] += 1
        elif m < 0.5: mult_dist["0.3-0.5"] += 1
        elif m < 0.7: mult_dist["0.5-0.7"] += 1
        elif m < 0.85: mult_dist["0.7-0.85"] += 1
        elif m < 1.0: mult_dist["0.85-1.0"] += 1
        elif m < 1.1: mult_dist["1.0-1.1"] += 1
        elif m < 1.3: mult_dist["1.1-1.3"] += 1
        else: mult_dist["1.3+"] += 1
    for k in ["<0.3", "0.3-0.5", "0.5-0.7", "0.7-0.85", "0.85-1.0", "1.0-1.1", "1.1-1.3", "1.3+"]:
        pct = mult_dist[k] / len(trades) * 100 if len(trades) else 0
        bar = "█" * int(pct / 2)
        print(f"    {k:<12} {mult_dist[k]:>5}笔 ({pct:>5.1f}%) {bar}")

    # ═══ 附加分析: SL%区间 ═══
    print_section("附加: SL%区间分析")
    sl_ranges = [
        ("SL<2%", 0, 2),
        ("SL=2-4%", 2, 4),
        ("SL=4-6%", 4, 6),
        ("SL=6-8%", 6, 8),
        ("SL=8-10%", 8, 10),
    ]
    sl_stats = {}
    for label, lo, hi in sl_ranges:
        group = [t for t in trades if lo <= t["sl_pct"] < hi]
        sl_stats[label] = calc_stats(group)
    print_stats_table(sl_stats, "SL区间")

    # ═══ 附加分析: V8+RSI交叉 ═══
    print_section("附加: V8高+RSI低 组合分析")
    v8_rsi_groups = {
        "V8≥6.5+RSI<50": [t for t in longs if t["v8_score"] >= 6.5 and t["rsi"] is not None and t["rsi"] < 50],
        "V8≥6.5+RSI≥50": [t for t in longs if t["v8_score"] >= 6.5 and t["rsi"] is not None and t["rsi"] >= 50],
        "V8<6.5+RSI<50": [t for t in longs if t["v8_score"] < 6.5 and t["rsi"] is not None and t["rsi"] < 50],
        "V8<6.5+RSI≥50": [t for t in longs if t["v8_score"] < 6.5 and t["rsi"] is not None and t["rsi"] >= 50],
    }
    vrs = {k: calc_stats(v) for k, v in v8_rsi_groups.items()}
    print_stats_table(vrs, "V8+RSI组合(做多)")

    # ═══ 综合优化建议 ═══
    print_section("综合优化建议")
    
    # 收集数据驱动的建议
    suggestions = []
    
    # 建议1: 基于V8高+RSI低
    v8hi_rsi_lo = calc_stats([t for t in longs if t["v8_score"] >= 6.5 and t["rsi"] is not None and t["rsi"] < 50])
    v8hi_rsi_hi = calc_stats([t for t in longs if t["v8_score"] >= 6.5 and t["rsi"] is not None and t["rsi"] >= 50])
    if v8hi_rsi_lo["count"] > 10:
        print(f"\n  💡 建议1: V8≥6.5+RSI<50做多目前PF={v8hi_rsi_lo['pf']}，共{v8hi_rsi_lo['count']}笔")
        print(f"     而V8≥6.5+RSI≥50做多PF={v8hi_rsi_hi['pf']}，共{v8hi_rsi_hi['count']}笔")
        if v8hi_rsi_lo["pf"] < v8hi_rsi_hi["pf"]:
            print(f"     → 考虑加强V8高+RSI低的过滤或进一步减小仓位")
            suggestions.append(("V8高RSI低加强过滤", v8hi_rsi_lo))
    
    # 建议2: 基于做空表现
    if short_s["count"] > 10:
        print(f"\n  💡 建议2: 做空{short_s['count']}笔，胜率{short_s['win_rate']}%，PF={short_s['pf']}")
        if short_s["pf"] < 1.0:
            print(f"     → 做空PF<1，考虑进一步收紧做空条件或增大做空V8阈值")
            suggestions.append(("做空收紧", short_s))
    
    # 建议3: 基于ATR
    high_atr = calc_stats([t for t in trades if t["atr_pct"] is not None and t["atr_pct"] >= 0.04])
    low_atr = calc_stats([t for t in trades if t["atr_pct"] is not None and t["atr_pct"] < 0.03])
    print(f"\n  💡 建议3: 高ATR(≥4%) PnL={high_atr['total_pnl']:+.2f}U PF={high_atr['pf']} vs 低ATR(<3%) PnL={low_atr['total_pnl']:+.2f}U PF={low_atr['pf']}")
    if high_atr["pf"] < low_atr["pf"]:
        print(f"     → 高ATR表现差，考虑降低max_atr_pct从5.0%到4.5%")
        suggestions.append(("ATR收紧", high_atr))
    
    # 建议4: 基于连亏后冷却
    print(f"\n  💡 建议4: 最大连亏{max_consec}次，当前consec_loss_threshold=2")
    print(f"     → 考虑连亏≥3时更激进减仓(mult=0.5)")
    
    # 建议5: 基于持仓时间
    long_hold = calc_stats([t for t in with_hold if t["hold_hours"] is not None and t["hold_hours"] > 48])
    short_hold = calc_stats([t for t in with_hold if t["hold_hours"] is not None and t["hold_hours"] <= 12])
    print(f"\n  💡 建议5: 长持仓(>48h) {long_hold['count']}笔 PF={long_hold['pf']} vs 短持仓(≤12h) {short_hold['count']}笔 PF={short_hold['pf']}")
    if long_hold["pf"] < short_hold["pf"]:
        print(f"     → 长持仓表现差，考虑更紧的止损或时间止损")
        suggestions.append(("长持仓优化", long_hold))

    # ═══ 最终汇总 ═══
    print_section("最终汇总")
    print(f"  v11g 1000U模拟:")
    print(f"  总交易: {overall['count']}笔")
    print(f"  胜率: {overall['win_rate']}%")
    print(f"  总PnL: {overall['total_pnl']:+.2f}U")
    print(f"  PF: {overall['pf']}")
    print(f"  做多: {long_s['count']}笔 PF={long_s['pf']}")
    print(f"  做空: {short_s['count']}笔 PF={short_s['pf']}")
    print(f"  最大连亏: {max_consec}次")
    
    # 保存详细结果
    out = {
        "version": "v11g_analysis",
        "overall": overall,
        "monthly": {k: v for k, v in monthly_stats.items()},
        "long_stats": long_s,
        "short_stats": short_s,
        "v8_stats": v8_stats,
    }
    out_path = DATA_DIR / "v11g_analysis_result.json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\n  💾 分析结果已保存: {out_path}")


if __name__ == "__main__":
    main()
