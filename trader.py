"""
交易管理器 — 开仓、平仓、持仓监控、复盘
"""
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from config import (
    BASE_DIR, DATA_DIR, TRADES_FILE, SCANNER_STATE_FILE, SCANNER_LOG_FILE,
    INITIAL_BALANCE, MAX_OPEN_POSITIONS, POSITION_PCT, LEVERAGE,
    COOLDOWN_HOURS, SCAN_INTERVAL, MONITOR_INTERVAL,
)
from binance_api import get_price, now_str, format_usd
from signals import check_environment, scan_all_signals
from notifier import notify, format_trade_message

TZ_UTC8 = timezone(timedelta(hours=8))


# === 数据持久化 ===

def load_trades() -> dict:
    """加载交易记录"""
    if TRADES_FILE.exists():
        with open(TRADES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"initial_balance": INITIAL_BALANCE, "trades": []}


def save_trades(data: dict):
    """保存交易记录"""
    TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_state() -> dict:
    """加载扫描器状态"""
    if SCANNER_STATE_FILE.exists():
        with open(SCANNER_STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_opens": {}, "signals_seen": {}}


def save_state(state: dict):
    """保存扫描器状态"""
    SCANNER_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SCANNER_STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_balance(data: dict) -> float:
    """计算当前余额"""
    balance = data.get("initial_balance", INITIAL_BALANCE)
    for t in data["trades"]:
        if t["status"] == "closed" and t.get("pnl_usd") is not None:
            balance += t["pnl_usd"]
    return balance


def next_id(data: dict) -> str:
    """生成下一个交易ID"""
    if not data["trades"]:
        return "001"
    max_id = max(int(t["id"]) for t in data["trades"])
    return f"{max_id + 1:03d}"


def log(msg: str):
    """写日志"""
    ts = datetime.now(TZ_UTC8).strftime("%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    SCANNER_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SCANNER_LOG_FILE, "a") as f:
        f.write(line + "\n")


# === 开仓 ===

def execute_open(data: dict, state: dict, signal: dict) -> dict | None:
    """
    执行虚拟开仓
    1. 先过综合环境检查
    2. 计算仓位/止损/止盈
    3. 记录并发通知
    """
    symbol = signal["symbol"]
    price = signal["price"]
    
    # 综合环境检查
    passed, env_analysis, strength = check_environment(symbol, signal)
    env_summary = " | ".join(v for v in env_analysis.values() if v)
    
    if not passed:
        log(f"环境检查未通过 {symbol}: {env_summary}")
        return None
    
    log(f"环境检查通过 {symbol}: {env_summary}")
    
    # B级信号需要更严格过滤
    if signal["strength"] == "B" and signal.get("type") not in ("crash_bounce", "funding_flip_neg", "funding_flip_pos"):
        log(f"B级信号跳过: {symbol} {signal['reason']}")
        return None
    
    # 计算仓位
    balance = get_balance(data)
    position_usd = balance * POSITION_PCT / 100
    
    # 计算止损止盈
    sl_pct = signal.get("sl_pct", 0.05)
    tp_pct = signal.get("tp_pct", 0.10)
    
    if signal["direction"] == "long":
        sl = round(price * (1 - sl_pct), 6)
        tp = round(price * (1 + tp_pct), 6)
    else:
        sl = round(price * (1 + sl_pct), 6)
        tp = round(price * (1 - tp_pct), 6)
    
    trade = {
        "id": next_id(data),
        "symbol": symbol,
        "direction": signal["direction"],
        "leverage": LEVERAGE,
        "position_pct": POSITION_PCT,
        "position_usd": round(position_usd, 4),
        "notional_usd": round(position_usd * LEVERAGE, 4),
        "entry_price": price,
        "stop_loss": sl,
        "take_profit": tp,
        "entry_time": now_str(),
        "exit_price": None,
        "exit_time": None,
        "exit_reason": None,
        "pnl_pct": None,
        "pnl_usd": None,
        "status": "open",
        "signal_type": signal["type"],
        "signal_strength": signal["strength"],
        "signal_reason": f"[{signal['strength']}] {signal['reason']}",
        "env_check": env_analysis,
        "post_review": None,
    }
    
    data["trades"].append(trade)
    save_trades(data)
    
    # 冷却记录
    state["last_opens"][symbol] = now_str()
    save_state(state)
    
    # 通知
    msg = format_trade_message(trade, "open")
    notify(msg)
    
    log(f"开仓 #{trade['id']} {symbol} {'做多' if signal['direction']=='long' else '做空'} @ {price} | {signal['reason']}")
    return trade


# === 持仓监控 ===

def monitor_positions(data: dict) -> list:
    """
    检查所有持仓是否触发止损/止盈
    返回已平仓的交易列表
    """
    closed = []
    open_positions = [t for t in data["trades"] if t["status"] == "open"]
    
    if not open_positions:
        return closed
    
    for trade in open_positions:
        symbol = trade["symbol"]
        current_price = get_price(symbol)
        
        if current_price <= 0:
            continue
        
        sl = trade["stop_loss"]
        tp = trade["take_profit"]
        direction = trade["direction"]
        
        triggered = None
        
        if direction == "long":
            if current_price <= sl:
                triggered = "止损"
            elif current_price >= tp:
                triggered = "止盈"
        else:
            if current_price >= sl:
                triggered = "止损"
            elif current_price <= tp:
                triggered = "止盈"
        
        if triggered:
            close_trade(data, trade, current_price, triggered)
            closed.append(trade)
    
    if closed:
        save_trades(data)
    
    return closed


def close_trade(data: dict, trade: dict, exit_price: float, reason: str):
    """平仓并计算盈亏"""
    entry = trade["entry_price"]
    direction = trade["direction"]
    lev = trade.get("leverage", LEVERAGE)
    pos_usd = trade.get("position_usd", 0)
    
    if direction == "long":
        pnl_pct = (exit_price - entry) / entry * 100 * lev
    else:
        pnl_pct = (entry - exit_price) / entry * 100 * lev
    
    pnl_usd = round(pnl_pct / 100 * pos_usd, 4)
    
    trade["exit_price"] = exit_price
    trade["exit_time"] = now_str()
    trade["exit_reason"] = reason
    trade["pnl_pct"] = round(pnl_pct, 2)
    trade["pnl_usd"] = pnl_usd
    trade["status"] = "closed"
    
    # 通知
    msg = format_trade_message(trade, "close")
    notify(msg)
    
    direction_cn = "多" if direction == "long" else "空"
    log(f"平仓 #{trade['id']} {trade['symbol']} {direction_cn} | {reason} | {pnl_usd:+.2f}U ({pnl_pct:+.1f}%)")


# === 复盘 ===

def review_trade(trade: dict) -> str:
    """
    单笔交易复盘 — 分析盈亏原因
    """
    entry = trade.get("entry_price", 0)
    exit_p = trade.get("exit_price", 0)
    pnl = trade.get("pnl_usd", 0)
    direction = trade.get("direction", "?")
    signal_type = trade.get("signal_type", "?")
    signal_strength = trade.get("signal_strength", "?")
    reason = trade.get("exit_reason", "?")
    
    direction_cn = "做多" if direction == "long" else "做空"
    result_emoji = "✅盈" if pnl > 0 else "❌亏"
    
    # 分析盈亏原因
    if pnl > 0:
        if reason == "止盈":
            analysis = "策略正确，信号有效，按计划止盈"
        else:
            analysis = "盈利但未到止盈点被止损回撤，可能止盈目标太远"
    else:
        if signal_type in ("extreme_neg_funding", "extreme_pos_funding"):
            analysis = "费率策略失败，市场极端持续超预期"
        elif signal_type == "crash_bounce":
            analysis = "反弹失败，下跌趋势未结束"
        elif signal_type == "pump_short":
            analysis = "回调延迟，暴涨惯性超预期"
        elif signal_type == "oi_surge":
            analysis = "OI信号假突破，资金方向判断错误"
        else:
            analysis = "信号失效，需检查市场环境变化"
    
    review = (
        f"📋 [复盘] #{trade['id']} {trade['symbol']}\n"
        f"方向: {direction_cn} [{signal_strength}] {signal_type}\n"
        f"入场: {entry} → 出场: {exit_p}\n"
        f"结果: {result_emoji} {pnl:+.2f}U ({trade.get('pnl_pct', 0):+.1f}%)\n"
        f"平仓原因: {reason}\n"
        f"分析: {analysis}\n"
    )
    
    # 保存复盘到交易记录
    trade["post_review"] = {
        "result": "win" if pnl > 0 else "loss",
        "analysis": analysis,
        "reviewed_at": now_str(),
    }
    
    return review


def generate_report(data: dict) -> str:
    """生成整体交易报告"""
    trades = data.get("trades", [])
    closed = [t for t in trades if t["status"] == "closed"]
    open_pos = [t for t in trades if t["status"] == "open"]
    
    if not closed:
        return "暂无已平仓交易"
    
    total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
    wins = [t for t in closed if t.get("pnl_usd", 0) > 0]
    losses = [t for t in closed if t.get("pnl_usd", 0) <= 0]
    win_rate = len(wins) / len(closed) * 100 if closed else 0
    
    # 最大盈利/亏损
    best = max(closed, key=lambda x: x.get("pnl_usd", 0))
    worst = min(closed, key=lambda x: x.get("pnl_usd", 0))
    
    # 按策略统计
    strategy_stats = {}
    for t in closed:
        stype = t.get("signal_type", "unknown")
        if stype not in strategy_stats:
            strategy_stats[stype] = {"count": 0, "wins": 0, "pnl": 0}
        strategy_stats[stype]["count"] += 1
        if t.get("pnl_usd", 0) > 0:
            strategy_stats[stype]["wins"] += 1
        strategy_stats[stype]["pnl"] += t.get("pnl_usd", 0)
    
    balance = get_balance(data)
    
    report = (
        f"📊 交易报告\n"
        f"══════════════\n"
        f"初始资金: ${INITIAL_BALANCE:.0f}\n"
        f"当前余额: ${balance:.2f}\n"
        f"总盈亏: {total_pnl:+.2f}U ({total_pnl/INITIAL_BALANCE*100:+.1f}%)\n"
        f"══════════════\n"
        f"总交易: {len(closed)}笔 | 持仓: {len(open_pos)}笔\n"
        f"胜率: {win_rate:.0f}% ({len(wins)}胜/{len(losses)}负)\n"
        f"最佳: {best['symbol']} {best.get('pnl_usd',0):+.2f}U\n"
        f"最差: {worst['symbol']} {worst.get('pnl_usd',0):+.2f}U\n"
        f"══════════════\n"
    )
    
    # 策略明细
    report += "策略表现:\n"
    for stype, stats in sorted(strategy_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
        wr = stats["wins"] / stats["count"] * 100 if stats["count"] > 0 else 0
        report += f"  {stype}: {stats['count']}笔 胜率{wr:.0f}% 盈亏{stats['pnl']:+.2f}U\n"
    
    return report


# === 主循环 ===

def run_scan_cycle():
    """单次扫描+监控循环"""
    data = load_trades()
    state = load_state()
    
    open_positions = [t for t in data["trades"] if t["status"] == "open"]
    open_symbols = set(t["symbol"] for t in open_positions)
    
    # 1. 监控现有持仓
    closed = monitor_positions(data)
    if closed:
        for t in closed:
            review = review_trade(t)
            notify(review)
        save_trades(data)
    
    # 2. 检查是否还有空位
    if len(open_positions) - len(closed) >= MAX_OPEN_POSITIONS:
        log(f"满仓 ({len(open_positions) - len(closed)}/{MAX_OPEN_POSITIONS})，跳过扫描")
        return
    
    # 3. 扫描新信号
    signals = scan_all_signals(open_symbols, state.get("last_opens", {}))
    
    if not signals:
        return
    
    log(f"发现 {len(signals)} 个信号")
    
    # 4. 取最强信号开仓（只开1笔）
    best = signals[0]
    
    # B级只开OI和费率翻转的
    if best["strength"] == "B" and best.get("type") not in ("oi_surge", "funding_flip_neg", "funding_flip_pos"):
        log(f"B级信号跳过: {best['symbol']} {best['reason']}")
        return
    
    execute_open(data, state, best)


def main_loop():
    """主循环 — 交替扫描和监控"""
    log("=" * 50)
    log("交易系统启动")
    log(f"模式: {'模拟盘' if True else '实盘'}")
    log(f"初始资金: ${INITIAL_BALANCE}")
    log(f"最大持仓: {MAX_OPEN_POSITIONS}")
    log(f"杠杆: {LEVERAGE}x")
    log(f"扫描间隔: {SCAN_INTERVAL}s")
    log("=" * 50)
    
    # 启动通知
    notify(
        f"🟢 交易系统已启动\n"
        f"模式: 模拟盘\n"
        f"资金: ${INITIAL_BALANCE}\n"
        f"策略: 6个(费率极端×2+暴跌反弹+暴涨做空+OI异动+费率翻转)\n"
        f"杠杆: {LEVERAGE}x | 最大持仓: {MAX_OPEN_POSITIONS}"
    )
    
    scan_count = 0
    
    while True:
        try:
            scan_count += 1
            run_scan_cycle()
            
            if scan_count % 20 == 0:
                data = load_trades()
                closed = [t for t in data["trades"] if t["status"] == "closed"]
                open_pos = [t for t in data["trades"] if t["status"] == "open"]
                total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
                log(f"第{scan_count}轮: 持仓{len(open_pos)}笔 已平{len(closed)}笔 总盈亏{total_pnl:+.2f}U")
        
        except Exception as e:
            log(f"扫描异常: {e}")
        
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "report":
        data = load_trades()
        report = generate_report(data)
        print(report)
        notify(report)
    elif len(sys.argv) > 1 and sys.argv[1] == "once":
        run_scan_cycle()
    else:
        main_loop()
