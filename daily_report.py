#!/usr/bin/env python3
"""
daily_report.py — 每日交易复盘报告
每天定时(23:50 UTC+8)生成并发送微信+Telegram

报告内容:
1. 每日总览 (盈亏/胜率/交易数)
2. G60B主仓表现
3. Spike进攻仓表现
4. 各交易明细 (开平仓逻辑)
5. 连亏/连胜状态
6. 持仓过夜风险
7. 风控检查 (最大回撤/日亏损)
8. 策略优化建议
"""
import json, os, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

TZ_UTC8 = timezone(timedelta(hours=8))

# ═══════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════

def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except:
        return default or {}

def load_g60b():
    return load_json(BASE_DIR / "data" / "trades.json", {"initial_balance": 600, "trades": []})

def load_spike():
    return load_json(BASE_DIR / "data_spike" / "trades.json", {"initial_balance": 300, "trades": []})


# ═══════════════════════════════════════
# 分析引擎
# ═══════════════════════════════════════

def filter_today(trades, now):
    """过滤今日(UTC+8)平仓的交易"""
    today = now.strftime("%Y-%m-%d")
    result = []
    for t in trades:
        if t.get("status") != "closed":
            continue
        exit_time = t.get("exit_time", "")
        if exit_time.startswith(today):
            result.append(t)
    return result

def filter_all_closed(trades):
    return [t for t in trades if t.get("status") == "closed"]

def calc_stats(closed_trades):
    """计算统计指标"""
    if not closed_trades:
        return {
            "count": 0, "wins": 0, "losses": 0,
            "win_rate": 0, "total_pnl": 0,
            "avg_pnl": 0, "max_win": 0, "max_loss": 0,
            "profit_factor": 0, "avg_rr": 0,
            "streak": "", "max_consecutive_loss": 0,
        }
    
    wins = [t for t in closed_trades if t.get("pnl_usd", 0) > 0]
    losses = [t for t in closed_trades if t.get("pnl_usd", 0) <= 0]
    total_pnl = sum(t.get("pnl_usd", 0) for t in closed_trades)
    gross_profit = sum(t.get("pnl_usd", 0) for t in wins)
    gross_loss = abs(sum(t.get("pnl_usd", 0) for t in losses))
    
    # 连胜/连亏
    streak_type = ""
    streak_count = 0
    max_consec_loss = 0
    current_loss_streak = 0
    for t in closed_trades:
        if t.get("pnl_usd", 0) > 0:
            current_loss_streak = 0
        else:
            current_loss_streak += 1
            max_consec_loss = max(max_consec_loss, current_loss_streak)
    
    # 当前连击
    if closed_trades:
        last_pnl = closed_trades[-1].get("pnl_usd", 0)
        for t in reversed(closed_trades):
            is_win = t.get("pnl_usd", 0) > 0
            if (last_pnl > 0 and is_win) or (last_pnl <= 0 and not is_win):
                streak_count += 1
            else:
                break
        if last_pnl > 0:
            streak_type = f"连胜{streak_count}"
        else:
            streak_type = f"连亏{streak_count}"
    
    # 平均RR
    rrs = [t.get("rr", 0) for t in closed_trades if t.get("rr")]
    avg_rr = sum(rrs) / len(rrs) if rrs else 0
    
    return {
        "count": len(closed_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(closed_trades) * 100 if closed_trades else 0,
        "total_pnl": total_pnl,
        "avg_pnl": total_pnl / len(closed_trades),
        "max_win": max((t.get("pnl_usd", 0) for t in wins), default=0),
        "max_loss": min((t.get("pnl_usd", 0) for t in losses), default=0),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        "avg_rr": avg_rr,
        "streak": streak_type,
        "max_consecutive_loss": max_consec_loss,
    }


def calc_drawdown(trades, initial_balance):
    """计算最大回撤"""
    equity = initial_balance
    peak = equity
    max_dd = 0
    max_dd_pct = 0
    
    for t in trades:
        if t.get("status") == "closed":
            equity += t.get("pnl_usd", 0)
            if equity > peak:
                peak = equity
            dd = peak - equity
            dd_pct = dd / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = dd_pct
    
    return max_dd, max_dd_pct, equity


# ═══════════════════════════════════════
# 报告生成
# ═══════════════════════════════════════

def generate_report(now=None):
    if now is None:
        now = datetime.now(TZ_UTC8)
    
    g60b = load_g60b()
    spike = load_spike()
    
    g60b_all = filter_all_closed(g60b["trades"])
    spike_all = filter_all_closed(spike["trades"])
    
    g60b_today = filter_today(g60b["trades"], now)
    spike_today = filter_today(spike["trades"], now)
    
    # 今日统计
    today_all = g60b_today + spike_today
    today_stats = calc_stats(today_all)
    
    # 全局统计
    g60b_stats = calc_stats(g60b_all)
    spike_stats = calc_stats(spike_all)
    all_closed = g60b_all + spike_all
    all_stats = calc_stats(all_closed)
    
    # 回撤
    g60b_dd, g60b_dd_pct, g60b_equity = calc_drawdown(g60b["trades"], g60b["initial_balance"])
    spike_dd, spike_dd_pct, spike_equity = calc_drawdown(spike["trades"], spike["initial_balance"])
    
    total_equity = g60b_equity + spike_equity + 100  # +100U现金
    total_initial = g60b["initial_balance"] + spike["initial_balance"] + 100
    total_return = (total_equity - total_initial) / total_initial * 100
    
    # 当前持仓
    g60b_open = [t for t in g60b["trades"] if t.get("status") == "open"]
    spike_open = [t for t in spike["trades"] if t.get("status") == "open"]
    all_open = g60b_open + spike_open
    
    # ═══ 组装报告 ═══
    date_str = now.strftime("%Y-%m-%d %A")
    lines = []
    lines.append(f"📊 **每日交易复盘报告**")
    lines.append(f"📅 {date_str}")
    lines.append("")
    
    # ── 1. 总览 ──
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📈 **今日总览**")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    pnl_emoji = "🟢" if today_stats["total_pnl"] >= 0 else "🔴"
    lines.append(f"{pnl_emoji} 今日盈亏: **{today_stats['total_pnl']:+.2f}U**")
    lines.append(f"📊 今日交易: {today_stats['count']}笔 ({today_stats['wins']}W/{today_stats['losses']}L)")
    if today_stats['count'] > 0:
        lines.append(f"🎯 今日胜率: **{today_stats['win_rate']:.0f}%**")
    lines.append(f"🔥 当前状态: {all_stats['streak'] or '无交易'}")
    lines.append("")
    
    # ── 2. 总资金 ──
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💰 **账户总览 (1000U分配)**")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"🏦 总权益: **{total_equity:.2f}U** (初始{total_initial}U)")
    ret_emoji = "📈" if total_return >= 0 else "📉"
    lines.append(f"{ret_emoji} 总收益率: **{total_return:+.2f}%**")
    lines.append(f"  G60B主仓: {g60b_equity:.2f}U / {g60b['initial_balance']}U "
                 f"({(g60b_equity - g60b['initial_balance']) / g60b['initial_balance'] * 100:+.2f}%)")
    lines.append(f"  Spike进攻仓: {spike_equity:.2f}U / {spike['initial_balance']}U "
                 f"({(spike_equity - spike['initial_balance']) / spike['initial_balance'] * 100:+.2f}%)")
    lines.append(f"  现金储备: 100U (不动)")
    lines.append("")
    
    # ── 3. G60B 主仓 ──
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🛡️ **G60B 主仓 (600U)**")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if g60b_stats["count"] > 0:
        lines.append(f"累计: {g60b_stats['count']}笔 | 胜率 **{g60b_stats['win_rate']:.0f}%** | "
                     f"盈亏 **{g60b_stats['total_pnl']:+.2f}U**")
        lines.append(f"最大盈利: {g60b_stats['max_win']:+.2f}U | 最大亏损: {g60b_stats['max_loss']:+.2f}U")
        lines.append(f"盈亏比: {g60b_stats['profit_factor']:.2f} | "
                     f"最大回撤: {g60b_dd:.2f}U ({g60b_dd_pct:.1f}%)")
        lines.append(f"平均RR: {g60b_stats['avg_rr']:.2f}")
    else:
        lines.append("暂无已平仓交易")
    
    # G60B今日明细
    if g60b_today:
        lines.append(f"\n**今日G60B交易 ({len(g60b_today)}笔):**")
        for t in g60b_today:
            d = "多🟢" if t["direction"] == "long" else "空🔴"
            pnl_e = "✅" if t.get("pnl_usd", 0) > 0 else "❌"
            lines.append(f"  {pnl_e} #{t['id']} {t['symbol']} {d} "
                         f"{t.get('pnl_usd', 0):+.2f}U ({t.get('pnl_pct', 0):+.1f}%)")
            lines.append(f"     原因: {t.get('signal_reason', 'N/A')[:50]}")
            lines.append(f"     入场: {t.get('entry_price', '?')} → 出场: {t.get('exit_price', '?')} "
                         f"| 退出: {t.get('exit_reason', '?')}")
    lines.append("")
    
    # ── 4. Spike 进攻仓 ──
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚡ **Spike-v13-P4 进攻仓 (300U)**")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if spike_stats["count"] > 0:
        lines.append(f"累计: {spike_stats['count']}笔 | 胜率 **{spike_stats['win_rate']:.0f}%** | "
                     f"盈亏 **{spike_stats['total_pnl']:+.2f}U**")
        lines.append(f"最大盈利: {spike_stats['max_win']:+.2f}U | 最大亏损: {spike_stats['max_loss']:+.2f}U")
        lines.append(f"盈亏比: {spike_stats['profit_factor']:.2f} | "
                     f"最大回撤: {spike_dd:.2f}U ({spike_dd_pct:.1f}%)")
    else:
        lines.append("暂无已平仓交易")
    
    if spike_today:
        lines.append(f"\n**今日Spike交易 ({len(spike_today)}笔):**")
        for t in spike_today:
            d = "多🟢" if t["direction"] == "long" else "空🔴"
            pnl_e = "✅" if t.get("pnl_usd", 0) > 0 else "❌"
            lines.append(f"  {pnl_e} #{t['id']} {t['symbol']} {d} "
                         f"{t.get('pnl_usd', 0):+.2f}U ({t.get('pnl_pct', 0):+.1f}%)")
            lines.append(f"     原因: {t.get('signal_reason', 'N/A')[:50]}")
            lines.append(f"     入场: {t.get('entry_price', '?')} → 出场: {t.get('exit_price', '?')} "
                         f"| 退出: {t.get('exit_reason', '?')}")
    lines.append("")
    
    # ── 5. 当前持仓 ──
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"📂 **当前持仓 ({len(all_open)}个)**")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if all_open:
        for t in all_open:
            d = "多🟢" if t["direction"] == "long" else "空🔴"
            src = "G60B" if t in g60b_open else "Spike"
            lines.append(f"  {d} #{t['id']} {t['symbol']} [{src}]")
            lines.append(f"    入场: {t.get('entry_price', '?')} | "
                         f"SL: {t.get('stop_loss', '?')} | TP: {t.get('take_profit', '?')}")
            lines.append(f"    原因: {t.get('signal_reason', 'N/A')[:60]}")
            # 计算浮动持仓时间
            entry_t = t.get("entry_time", "")
            if entry_t:
                try:
                    et = datetime.fromisoformat(entry_t)
                    if et.tzinfo is None:
                        et = et.replace(tzinfo=TZ_UTC8)
                    hold_h = (now - et).total_seconds() / 3600
                    lines.append(f"    已持仓: {hold_h:.1f}h")
                except:
                    pass
    else:
        lines.append("  无持仓，等待信号")
    lines.append("")
    
    # ── 6. 风控检查 ──
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("🛡️ **风控检查**")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    risk_warnings = []
    
    # 日亏损检查
    if today_stats["total_pnl"] < -30:
        risk_warnings.append(f"⚠️ 今日亏损 {today_stats['total_pnl']:+.2f}U 超过3%阈值")
    
    # 回撤检查
    if g60b_dd_pct > 15:
        risk_warnings.append(f"⚠️ G60B最大回撤 {g60b_dd_pct:.1f}% 超15%")
    if spike_dd_pct > 20:
        risk_warnings.append(f"⚠️ Spike最大回撤 {spike_dd_pct:.1f}% 超20%")
    
    # 连亏检查
    if all_stats["max_consecutive_loss"] >= 3:
        risk_warnings.append(f"⚠️ 最大连亏 {all_stats['max_consecutive_loss']} 次")
    
    # 持仓过夜
    if len(all_open) > 0:
        risk_warnings.append(f"🌙 持仓过夜: {len(all_open)}个仓位")
    
    if risk_warnings:
        for w in risk_warnings:
            lines.append(w)
    else:
        lines.append("✅ 风控正常，无告警")
    lines.append("")
    
    # ── 7. 策略建议 ──
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("💡 **策略建议**")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    suggestions = []
    
    if today_stats["count"] == 0:
        suggestions.append("• 今日无交易 — 信号质量低于阈值是正常的，宁缺毋滥")
    
    if all_stats["win_rate"] > 0 and all_stats["win_rate"] < 40 and all_stats["count"] >= 5:
        suggestions.append(f"• 胜率偏低({all_stats['win_rate']:.0f}%)，建议检查信号阈值是否过宽")
    
    if all_stats["profit_factor"] < 1.0 and all_stats["count"] >= 5:
        suggestions.append(f"• 盈亏比({all_stats['profit_factor']:.2f})<1，需要优化止盈或缩小止损")
    
    if all_stats["max_consecutive_loss"] >= 3:
        suggestions.append("• 连亏≥3次，建议下次入场仓位减半(G60 Profile逻辑)")
    
    if g60b_stats["count"] > spike_stats["count"] * 3 and spike_stats["count"] == 0:
        suggestions.append("• Spike信号过少，考虑放宽ATR/阈值参数")
    
    if not suggestions:
        if today_stats["total_pnl"] > 0:
            suggestions.append("• 今日盈利，继续保持纪律执行")
        else:
            suggestions.append("• 系统运行正常，继续观察信号质量")
    
    for s in suggestions:
        lines.append(s)
    lines.append("")
    
    # ── 8. 全局累计 ──
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("📊 **累计统计**")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    if all_stats["count"] > 0:
        lines.append(f"总交易: {all_stats['count']}笔 | "
                     f"胜率 {all_stats['win_rate']:.0f}% | "
                     f"总盈亏 {all_stats['total_pnl']:+.2f}U")
        lines.append(f"盈利因子: {all_stats['profit_factor']:.2f} | "
                     f"平均RR: {all_stats['avg_rr']:.2f}")
    
    lines.append("")
    lines.append(f"⏰ 报告生成: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("🤖 1000U G60B+Spike 双仓策略")
    
    return "\n".join(lines)


# ═══════════════════════════════════════
# 发送
# ═══════════════════════════════════════

def send_report(report_text):
    """通过 notifier 发送报告到微信+Telegram"""
    try:
        from notifier import notify
        notify(report_text)
        print(f"[DailyReport] ✅ 报告已发送 (微信+Telegram)")
    except Exception as e:
        print(f"[DailyReport] ❌ notifier发送失败: {e}")
        # 备用: 只发Telegram
        try:
            from notifier import send_telegram
            send_telegram(report_text)
            print(f"[DailyReport] ✅ 备用Telegram发送成功")
        except Exception as e2:
            print(f"[DailyReport] ❌ 备用发送也失败: {e2}")


def main():
    now = datetime.now(TZ_UTC8)
    print(f"[DailyReport] 生成报告 @ {now.strftime('%Y-%m-%d %H:%M')}")
    
    report = generate_report(now)
    print(report)
    print()
    
    # 发送
    send_report(report)
    
    # 同时保存到文件
    report_dir = BASE_DIR / "data" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"report_{now.strftime('%Y-%m-%d')}.txt"
    with open(report_file, "w") as f:
        f.write(report)
    print(f"[DailyReport] 已保存: {report_file}")


if __name__ == "__main__":
    main()
