#!/usr/bin/env python3
"""
v11i 回撤优化 — 全方案回测对比
基于v10原始535笔数据 → v11i参数 → 不同回撤控制方案

方案列表:
  A: v11i基线(无改动)
  B: SL≤8% (砍掉宽止损)
  C: 连亏2笔×0.5
  D: 单笔亏损上限$60
  E: B+C组合 (SL≤8% + 连亏×0.5)
  F: B+D组合 (SL≤8% + 单笔上限$60)
  G: C+D组合 (连亏×0.5 + 单笔上限$60)
  H: B+C+D全组合 = 方案⑥
  I: H加强版 (SL≤7% + 连亏×0.4 + 上限$50)
  J: H宽松版 (SL≤9% + 连亏×0.6 + 上限$80)
  K: v11new风格 (只保留做多RSI>55信号 + 方案H)
  L: 仅SL≤7%
  M: 仅上限$40
  N: 仅上限$80
  O: 仅上限$100
  P: SL≤7% + 日亏$12停 + 周亏$25停   ← 动态风控
  Q: 上限$40 + 日亏$12/周亏$25停 + 连亏5×0.3  ← 动态风控
  R: 连亏×0.5+上限$60 + 日亏$12/周亏$25停  ← 动态风控
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
CONSEC_LOSS_MULT_BASE = 0.7


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
        max_sl_pct: float,          # SL硬过滤上限(%)
        consec_loss_mult: float,    # 连亏减仓倍率
        max_loss_per_trade: float,  # 单笔亏损上限($)
        max_daily_loss: float,      # 当日累计亏损超过此值则跳过当天剩余交易
        max_weekly_loss: float,     # 本周累计亏损超过此值则跳过本周剩余交易
    }
    """
    max_sl = config.get("max_sl_pct", 10.0)
    cl_mult = config.get("consec_loss_mult", 0.7)
    max_loss = config.get("max_loss_per_trade", None)
    max_daily_loss = config.get("max_daily_loss", None)
    max_weekly_loss = config.get("max_weekly_loss", None)

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
    skipped_by_daily_loss = 0      # P1: 被日亏损暂停跳过的笔数
    skipped_by_weekly_loss = 0     # P1: 被周亏损暂停跳过的笔数

    # P1: 动态风控 — 按日/周跟踪累计亏损
    current_day = None
    daily_pnl = 0.0
    current_week = None
    weekly_pnl = 0.0

    def _get_week(trade_day):
        """从日期字符串获取"年-周"标识"""
        from datetime import datetime
        dt = datetime.strptime(trade_day, "%Y-%m-%d")
        iso = dt.isocalendar()
        return f"{iso[0]}-W{iso[1]:02d}"

    for t in filtered:
        trade_day = t["entry_time"][:10]
        trade_week = _get_week(trade_day)

        # 新的一天/一周 → 重置累计亏损
        if trade_day != current_day:
            current_day = trade_day
            daily_pnl = 0.0
        if trade_week != current_week:
            current_week = trade_week
            weekly_pnl = 0.0

        # ═══ P1: 检查是否触发暂停 ═══
        if max_daily_loss is not None and daily_pnl < -max_daily_loss:
            skipped_by_daily_loss += 1
            continue
        if max_weekly_loss is not None and weekly_pnl < -max_weekly_loss:
            skipped_by_weekly_loss += 1
            continue

        v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
        v8 = t.get("v8_score", 0) or t.get("v8_quality", 0)
        base = SHORT_POSITION_FACTOR if (t["direction"] == "short" and v8 >= SHORT_V8_THRESHOLD) else 1.0
        mult = base * calc_mult(t, consec, consec_loss_mult=cl_mult)

        raw_pnl = t.get("pnl_usd", 0) * mult  # 原始盈亏(v11i仓位下)

        # ★ 修正: 单笔亏损上限 — 开仓前缩仓，盈亏同步缩小
        # 在实盘中: 开仓时计算 est_max_loss = position_usd × sl_pct × leverage
        # 如果 est_max_loss > max_loss 则缩小仓位
        # 这里用 v10原始position_usd × mult 估算 v11i的position_usd
        if max_loss is not None:
            orig_pos = t.get("position_usd", 0)
            sl_pct_val = t.get("signal_sl_pct", 0)
            leverage = t.get("leverage", 3)
            if orig_pos > 0 and sl_pct_val > 0:
                est_max_loss = orig_pos * mult * sl_pct_val * leverage
                if est_max_loss > max_loss:
                    # 开仓前缩仓: shrink = max_loss / est_max_loss
                    shrink = max_loss / est_max_loss
                    pnl = raw_pnl * shrink
                    loss_cap_savings += abs(raw_pnl) * (1 - shrink)
                else:
                    pnl = raw_pnl
            else:
                pnl = raw_pnl
        else:
            pnl = raw_pnl

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

        # P1: 更新日/周累计亏损（用于后续交易的暂停判断）
        daily_pnl += pnl
        weekly_pnl += pnl

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
        "skipped_by_daily_loss": skipped_by_daily_loss,   # P1
        "skipped_by_weekly_loss": skipped_by_weekly_loss,  # P1
    }


def main():
    with open(V10_PATH) as f:
        raw = json.load(f).get("trades", [])

    print(f"v10原始数据: {len(raw)}笔")

    # v11基础过滤
    base = apply_v11_base_filter(raw)
    print(f"v11基础过滤后: {len(base)}笔")

    # ═══ 定义所有方案 ═══
    schemes = {
        "A-基线v11i": {
            "desc": "v11i无改动",
            "config": {"max_sl_pct": 10.0, "consec_loss_mult": 0.7, "max_loss_per_trade": None},
        },
        "B-SL≤8%": {
            "desc": "仅收紧SL上限",
            "config": {"max_sl_pct": 8.0, "consec_loss_mult": 0.7, "max_loss_per_trade": None},
        },
        "C-连亏×0.5": {
            "desc": "仅加强连亏减仓",
            "config": {"max_sl_pct": 10.0, "consec_loss_mult": 0.5, "max_loss_per_trade": None},
        },
        "D-单笔上限$60": {
            "desc": "仅加单笔亏损上限",
            "config": {"max_sl_pct": 10.0, "consec_loss_mult": 0.7, "max_loss_per_trade": 60},
        },
        "E-B+C": {
            "desc": "SL≤8% + 连亏×0.5",
            "config": {"max_sl_pct": 8.0, "consec_loss_mult": 0.5, "max_loss_per_trade": None},
        },
        "F-B+D": {
            "desc": "SL≤8% + 上限$60",
            "config": {"max_sl_pct": 8.0, "consec_loss_mult": 0.7, "max_loss_per_trade": 60},
        },
        "G-C+D": {
            "desc": "连亏×0.5 + 上限$60",
            "config": {"max_sl_pct": 10.0, "consec_loss_mult": 0.5, "max_loss_per_trade": 60},
        },
        "H-方案⑥(BCD)": {
            "desc": "SL≤8% + 连亏×0.5 + 上限$60 ★主方案",
            "config": {"max_sl_pct": 8.0, "consec_loss_mult": 0.5, "max_loss_per_trade": 60},
        },
        "I-加强版": {
            "desc": "SL≤7% + 连亏×0.4 + 上限$50",
            "config": {"max_sl_pct": 7.0, "consec_loss_mult": 0.4, "max_loss_per_trade": 50},
        },
        "J-宽松版": {
            "desc": "SL≤9% + 连亏×0.6 + 上限$80",
            "config": {"max_sl_pct": 9.0, "consec_loss_mult": 0.6, "max_loss_per_trade": 80},
        },
        "K-超保守": {
            "desc": "SL≤6% + 连亏×0.3 + 上限$40",
            "config": {"max_sl_pct": 6.0, "consec_loss_mult": 0.3, "max_loss_per_trade": 40},
        },
        "L-仅SL≤7%": {
            "desc": "单独测SL=7%",
            "config": {"max_sl_pct": 7.0, "consec_loss_mult": 0.7, "max_loss_per_trade": None},
        },
        "M-仅上限$40": {
            "desc": "单独测上限$40",
            "config": {"max_sl_pct": 10.0, "consec_loss_mult": 0.7, "max_loss_per_trade": 40},
        },
        "N-仅上限$80": {
            "desc": "单独测上限$80",
            "config": {"max_sl_pct": 10.0, "consec_loss_mult": 0.7, "max_loss_per_trade": 80},
        },
        "O-上限$100": {
            "desc": "单独测上限$100",
            "config": {"max_sl_pct": 10.0, "consec_loss_mult": 0.7, "max_loss_per_trade": 100},
        },
        # ══════════════════════════════════════════════════
        # P1: 动态风控方案
        # ══════════════════════════════════════════════════
        "P-动态风控(L+):": {
            "desc": "SL≤7% + 日亏$12停+周亏$25停",
            "config": {"max_sl_pct": 7.0, "consec_loss_mult": 0.7,
                       "max_loss_per_trade": None,
                       "max_daily_loss": 12, "max_weekly_loss": 25},
        },
        "Q-动态风控(M+):": {
            "desc": "上限$40 + 日亏$12停+周亏$25停+连亏5×0.3",
            "config": {"max_sl_pct": 10.0, "consec_loss_mult": 0.3,
                       "max_loss_per_trade": 40,
                       "max_daily_loss": 12, "max_weekly_loss": 25},
        },
        "R-动态风控(G+):": {
            "desc": "连亏×0.5+上限$60 + 日亏$12停+周亏$25停",
            "config": {"max_sl_pct": 10.0, "consec_loss_mult": 0.5,
                       "max_loss_per_trade": 60,
                       "max_daily_loss": 12, "max_weekly_loss": 25},
        },
    }

    # ═══ 运行所有方案 ═══
    results = {}
    for name, scheme in schemes.items():
        r = simulate(base, scheme["config"])
        if r:
            results[name] = {**r, "desc": scheme["desc"]}
        else:
            results[name] = None

    # ═══ 输出对比表 ═══
    print(f"\n{'='*120}")
    print(f"全方案回测对比 (基于v11i参数, 初始$1000)")
    print(f"{'='*120}")

    # 表头
    print(f"{'方案':<18} {'说明':<22} {'笔数':>5} {'WR%':>6} {'PnL($)':>10} {'ROI%':>8} {'DD%':>7} {'PF':>5} {'月胜率':>6} {'ROI/DD':>7} {'最大连亏':>7} {'单笔最大亏':>10}")
    print("-" * 120)

    for name, scheme in schemes.items():
        r = results[name]
        if r is None:
            print(f"{name:<18} {scheme['desc']:<22} {'N/A':>5}")
            continue

        print(
            f"{name:<18} {scheme['desc']:<22} "
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
    print("Top 5 最优方案 (按ROI/DD排序)")
    print(f"{'='*120}")

    valid_results = [(n, r) for n, r in results.items() if r is not None]
    valid_results.sort(key=lambda x: x[1]["roi_dd_ratio"], reverse=True)

    for i, (name, r) in enumerate(valid_results[:5], 1):
        print(f"\n  #{i} {name} ({r['desc']})")
        print(f"      PnL: +${r['total_pnl']:.0f} | ROI: {r['roi']:.1f}% | DD: {r['max_drawdown']:.1f}% | ROI/DD: {r['roi_dd_ratio']:.2f}")
        print(f"      WR: {r['win_rate']:.1f}% ({r['wins']}W/{r['losses']}L) | PF: {r['profit_factor']:.2f}")
        print(f"      月胜率: {r['monthly_win_rate']:.1f}% ({r['profit_months']}/{r['total_months']}) | 最大连亏: {r['max_consec_loss']}笔")
        print(f"      单笔最大亏: ${r['max_single_loss']:.1f} | DD区间: ${r['max_dd_peak']:.0f} → ${r['max_dd_trough']:.0f}")
        print(f"      Top10亏损: {r['top_10_losses']}")

    # ═══ 月度对比 (基线 vs Top3) ═══
    print(f"\n{'='*120}")
    print("月度PnL对比 (基线A vs Top3)")
    print(f"{'='*120}")

    top3_names = [n for n, _ in valid_results[:3]]
    compare_names = ["A-基线v11i"] + top3_names

    # 收集所有月份
    all_months = set()
    for n in compare_names:
        if results[n]:
            all_months.update(results[n]["monthly_pnl"].keys())
    all_months = sorted(all_months)

    header = f"{'月份':<10}"
    for n in compare_names:
        header += f" {n[:12]:>13}"
    print(header)
    print("-" * (10 + 14 * len(compare_names)))

    for m in all_months:
        row = f"{m:<10}"
        for n in compare_names:
            if results[n] and m in results[n]["monthly_pnl"]:
                p = results[n]["monthly_pnl"][m]
                row += f" {p:>+13.0f}"
            else:
                row += f" {'—':>13}"
        print(row)

    # ═══ 敏感性分析 ═══
    print(f"\n{'='*120}")
    print("敏感性分析: 各参数对PnL和DD的边际影响")
    print(f"{'='*120}")

    baseline_pnl = results["A-基线v11i"]["total_pnl"]
    baseline_dd = results["A-基线v11i"]["max_drawdown"]

    print(f"\n基线(A): PnL={baseline_pnl:+.0f} DD={baseline_dd:.1f}%\n")

    singles = {
        "SL≤8%": "B-SL≤8%",
        "SL≤7%": "L-仅SL≤7%",
        "连亏×0.5": "C-连亏×0.5",
        "上限$40": "M-仅上限$40",
        "上限$60": "D-单笔上限$60",
        "上限$80": "N-仅上限$80",
        "上限$100": "O-上限$100",
    }

    print(f"{'改动':<12} {'PnL变化':>10} {'DD变化':>10} {'PnL/DD':>10} {'评价':<20}")
    print("-" * 65)
    for label, name in singles.items():
        r = results[name]
        pnl_delta = r["total_pnl"] - baseline_pnl
        dd_delta = r["max_drawdown"] - baseline_dd
        # 改善评分: PnL增加+DD减少=好
        score = pnl_delta / abs(dd_delta) if dd_delta != 0 else 0
        if pnl_delta >= 0 and dd_delta <= 0:
            verdict = "✅ 双赢"
        elif pnl_delta >= 0 and dd_delta > 0:
            verdict = "⚠️ 赚更多但DD也增"
        elif pnl_delta < 0 and dd_delta < 0:
            verdict = f"📉 亏${abs(pnl_delta):.0f}降DD"
        else:
            verdict = "❌ 双亏"

        print(f"{label:<12} {pnl_delta:>+10.0f} {dd_delta:>+10.1f}% {score:>10.1f} {verdict:<20}")

    # ═══ 保存结果 ═══
    out = {
        "version": "v11i-optimization-sweep",
        "description": "v11i回撤优化全方案对比",
        "baseline": "v11i: 1274笔/62.7%/+$6983/DD43.1%/PF1.42",
        "schemes": {},
    }
    for name, scheme in schemes.items():
        r = results[name]
        if r:
            out["schemes"][name] = {
                "desc": scheme["desc"],
                "config": scheme["config"],
                "result": {k: v for k, v in r.items() if k != "monthly_pnl"},
            }

    out_path = DATA_DIR / "backtest_v11i_optimization_sweep.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 已保存: {out_path}")


if __name__ == "__main__":
    main()
