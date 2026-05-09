#!/usr/bin/env python3
"""
自动化交易主循环 — 优化版
使用快速扫描(批量API)避免超时，逐个深度检查候选
"""
import json
import time
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/home/ubuntu/.hermes/trading')
from config import *
from binance_api import (
    get_qualified_symbols, get_funding_rates, get_funding_history,
    get_klines, get_price, get_open_interest, get_btc_trend,
    get_fear_greed, format_usd, now_str, api_get,
)
from trader import (
    load_trades, save_trades, load_state, save_state,
    get_balance, next_id, log,
    monitor_positions, close_trade,
)
from notifier import notify, send_open_notification, send_close_and_review

TZ_UTC8 = timezone(timedelta(hours=8))


def quick_scan(open_symbols: set, cooldowns: dict) -> list:
    """
    快速扫描 — 只用批量API筛选候选
    返回: [(symbol, signal_type, direction, strength, detail), ...]
    """
    tickers = get_qualified_symbols()
    funding = get_funding_rates()
    now = datetime.now(TZ_UTC8)
    
    candidates = []
    
    for t in tickers:
        sym = t["symbol"]
        if sym in open_symbols:
            continue
        
        # 冷却检查
        last_open = cooldowns.get(sym)
        if last_open:
            try:
                last_dt = datetime.fromisoformat(last_open)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=TZ_UTC8)
                if (now - last_dt).total_seconds() < COOLDOWN_HOURS * 3600:
                    continue
            except Exception:
                pass
        
        fr = funding.get(sym, 0)
        change_pct = float(t.get("priceChangePercent", 0))
        price = float(t.get("lastPrice", 0))
        vol = float(t.get("quoteVolume", 0))
        
        # 策略1: 极端负费率 → 做多
        if fr < EXTREME_NEG_FUNDING:
            candidates.append({
                "symbol": sym, "type": "extreme_neg_funding",
                "direction": "long", "strength": "B",
                "price": price, "fr": fr, "change": change_pct, "vol": vol,
                "reason": f"极端负费率{fr:+.4f}%",
                "sl_pct": 0.08, "tp_pct": 0.12,
            })
        
        # 策略2: 极端正费率 → 做空
        if fr > EXTREME_POS_FUNDING:
            candidates.append({
                "symbol": sym, "type": "extreme_pos_funding",
                "direction": "short", "strength": "B",
                "price": price, "fr": fr, "change": change_pct, "vol": vol,
                "reason": f"极端正费率{fr:+.4f}%",
                "sl_pct": 0.10, "tp_pct": 0.15,
            })
        
        # 策略3: 暴跌反弹 → 做多
        if change_pct < -25:
            candidates.append({
                "symbol": sym, "type": "crash_bounce",
                "direction": "long", "strength": "B",
                "price": price, "fr": fr, "change": change_pct, "vol": vol,
                "reason": f"24h暴跌{change_pct:+.1f}%",
                "sl_pct": 0.10, "tp_pct": 0.15,
            })
        
        # 策略4: 暴涨做空
        if change_pct > 40:
            candidates.append({
                "symbol": sym, "type": "pump_short",
                "direction": "short", "strength": "B",
                "price": price, "fr": fr, "change": change_pct, "vol": vol,
                "reason": f"24h暴涨{change_pct:+.1f}%",
                "sl_pct": 0.15, "tp_pct": 0.20,
            })
    
    return candidates


def deep_check(candidate: dict) -> dict | None:
    """
    对候选进行深度检查 — 验证费率连续性
    通过返回增强信号，失败返回None
    """
    sym = candidate["symbol"]
    signal_type = candidate["type"]
    direction = candidate["direction"]
    
    # 获取费率历史
    fr_hist = get_funding_history(sym, 8)
    if not fr_hist:
        return None
    
    avg_fr = sum(fr_hist) / len(fr_hist)
    
    if signal_type == "extreme_neg_funding":
        neg_count = sum(1 for r in fr_hist if r < -0.03)
        if neg_count < 3:
            return None
        strength = "S" if avg_fr < -0.15 else "A" if avg_fr < -0.08 else "B"
        candidate["strength"] = strength
        candidate["reason"] = f"极端负费率 avg:{avg_fr:+.4f}% 连续{neg_count}/8期为负"
        candidate["sl_pct"] = 0.08 if strength == "S" else 0.10
        candidate["tp_pct"] = 0.12 if strength == "S" else 0.15
    
    elif signal_type == "extreme_pos_funding":
        pos_count = sum(1 for r in fr_hist if r > 0.05)
        if pos_count < 3:
            return None
        strength = "S" if avg_fr > 0.20 else "A" if avg_fr > 0.10 else "B"
        candidate["strength"] = strength
        candidate["reason"] = f"极端正费率 avg:{avg_fr:+.4f}% 连续{pos_count}/8期高正"
        candidate["sl_pct"] = 0.08 if strength == "S" else 0.10
        candidate["tp_pct"] = 0.15 if strength == "S" else 0.20
    
    elif signal_type == "crash_bounce":
        # 检查最近K线是否企稳
        klines = get_klines(sym, "1h", 3)
        if not klines or len(klines) < 2:
            return None
        closes = [float(k[4]) for k in klines]
        if closes[-1] < closes[-2]:
            return None  # 还在跌
        candidate["reason"] = f"24h暴跌{candidate['change']:+.1f}% 后企稳反弹"
        candidate["strength"] = "A" if candidate["change"] < -35 else "B"
    
    elif signal_type == "pump_short":
        # 检查是否已从高点回落
        klines = get_klines(sym, "1h", 6)
        if not klines:
            return None
        highs = [float(k[2]) for k in klines]
        closes = [float(k[4]) for k in klines]
        peak = max(highs)
        pullback = (peak - closes[-1]) / peak * 100
        if pullback < 5:
            return None  # 还没回落
        candidate["reason"] = f"24h暴涨{candidate['change']:+.1f}% 后回落{pullback:.0f}%"
        candidate["strength"] = "A" if pullback > 15 else "B"
    
    return candidate


def env_score(candidate: dict) -> tuple:
    """
    环境评分（快速版） — 返回 (score, details)
    """
    score = 0
    details = []
    
    btc = get_btc_trend()
    btc_chg = btc.get("change_pct", 0)
    fgi = get_fear_greed()
    direction = candidate["direction"]
    
    # BTC环境
    if direction == "long" and btc_chg > -2:
        score += 1; details.append(f"BTC{btc_chg:+.1f}% OK")
    elif direction == "long" and btc_chg < -5:
        score -= 1; details.append(f"BTC{btc_chg:+.1f}% 危险")
    elif direction == "short" and btc_chg < 2:
        score += 1; details.append(f"BTC{btc_chg:+.1f}% OK")
    elif direction == "short" and btc_chg > 5:
        score -= 1; details.append(f"BTC{btc_chg:+.1f}% 危险")
    else:
        details.append(f"BTC{btc_chg:+.1f}%")
    
    # FGI
    if direction == "long" and fgi <= 30:
        score += 1; details.append(f"FGI={fgi}恐惧")
    elif direction == "long" and fgi >= 70:
        score -= 1; details.append(f"FGI={fgi}贪婪")
    elif direction == "short" and fgi >= 70:
        score += 1; details.append(f"FGI={fgi}贪婪")
    elif direction == "short" and fgi <= 30:
        score -= 1; details.append(f"FGI={fgi}恐惧")
    else:
        details.append(f"FGI={fgi}")
    
    # 成交量
    vol = candidate.get("vol", 0)
    if vol > 50_000_000:
        score += 1; details.append("量大")
    elif vol < 20_000_000:
        score -= 1; details.append("量小")
    
    # 信号强度
    if candidate["strength"] == "S":
        score += 2; details.append("S级+2")
    elif candidate["strength"] == "A":
        score += 1; details.append("A级+1")
    
    return score, " | ".join(details)


def execute_open_v2(data: dict, state: dict, signal: dict) -> dict | None:
    """执行虚拟开仓"""
    symbol = signal["symbol"]
    price = signal["price"]
    direction = signal["direction"]
    
    balance = get_balance(data)
    position_usd = round(balance * POSITION_PCT / 100, 4)
    
    sl_pct = signal.get("sl_pct", 0.05)
    tp_pct = signal.get("tp_pct", 0.10)
    
    if direction == "long":
        sl = round(price * (1 - sl_pct), 6)
        tp = round(price * (1 + tp_pct), 6)
    else:
        sl = round(price * (1 + sl_pct), 6)
        tp = round(price * (1 - tp_pct), 6)
    
    trade = {
        "id": next_id(data),
        "symbol": symbol,
        "direction": direction,
        "leverage": LEVERAGE,
        "position_pct": POSITION_PCT,
        "position_usd": position_usd,
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
        "signal_reason": f"[{signal['strength']}] {signal.get('reason', '')}",
        "signal_sl_pct": round(sl_pct * 100),
        "signal_tp_pct": round(tp_pct * 100),
        "post_review": None,
    }
    
    data["trades"].append(trade)
    save_trades(data)
    
    state["last_opens"][symbol] = now_str()
    save_state(state)
    
    # 发送开仓通知（包含完整交易逻辑）
    env_detail = signal.get("env_detail", "")
    send_open_notification(trade, env_detail)
    
    d_cn = "做多" if direction == "long" else "做空"
    log(f"开仓 #{trade['id']} {symbol} {d_cn} @ {price} | SL:{sl} TP:{tp} | {signal.get('reason', '')}")
    return trade


def run_cycle():
    """单次扫描+监控循环"""
    data = load_trades()
    state = load_state()
    
    # 1. 监控现有持仓
    open_positions = [t for t in data["trades"] if t["status"] == "open"]
    closed = monitor_positions(data)
    if closed:
        for t in closed:
            # 平仓通知 + 自动复盘（发送到微信）
            send_close_and_review(t)
        save_trades(data)
        open_positions = [t for t in data["trades"] if t["status"] == "open"]
    
    # 2. 检查空位
    if len(open_positions) >= MAX_OPEN_POSITIONS:
        log(f"满仓 {len(open_positions)}/{MAX_OPEN_POSITIONS}，跳过扫描")
        return
    
    # 3. 快速扫描
    open_symbols = set(t["symbol"] for t in open_positions)
    candidates = quick_scan(open_symbols, state.get("last_opens", {}))
    
    if not candidates:
        return
    
    # 4. 按费率极端程度排序
    candidates.sort(key=lambda x: abs(x.get("fr", 0)), reverse=True)
    
    # 5. 逐个深度检查，取第一个通过的
    for cand in candidates[:15]:  # 最多检查15个
        # 深度验证
        verified = deep_check(cand)
        if not verified:
            continue
        
        # v3: 移除B级信号封杀 — 交给评分系统自然过滤
        
        # 环境评分
        score, env_detail = env_score(verified)
        log(f"候选 {verified['symbol']} [{verified['strength']}] 评分:{score}/7 | {env_detail}")
        
        if score < MIN_ENV_SCORE:
            continue
        
        # 把环境详情带入信号
        verified["env_detail"] = env_detail
        
        # 开仓！
        execute_open_v2(data, state, verified)
        break  # 每轮只开1笔


def main():
    log("=" * 50)
    log("🤖 自动交易系统启动 (优化版)")
    log(f"模式: 模拟盘 | 资金: ${INITIAL_BALANCE}")
    log(f"杠杆: {LEVERAGE}x | 最大持仓: {MAX_OPEN_POSITIONS}")
    log(f"扫描间隔: {SCAN_INTERVAL}s | 只开S/A级信号")
    log("=" * 50)
    
    notify(
        f"🤖 自动交易已启动\n"
        f"模拟盘 | ${INITIAL_BALANCE} | {LEVERAGE}x\n"
        f"策略: 费率极端+暴跌反弹+暴涨做空\n"
        f"只开S/A级信号 | 最低环境分{MIN_ENV_SCORE}/7"
    )
    
    cycle = 0
    while True:
        try:
            cycle += 1
            run_cycle()
            
            if cycle % 30 == 0:
                data = load_trades()
                closed = [t for t in data["trades"] if t["status"] == "closed"]
                open_pos = [t for t in data["trades"] if t["status"] == "open"]
                total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
                bal = get_balance(data)
                log(f"📊 第{cycle}轮: 余额${bal:.2f} | 持仓{len(open_pos)} | 已平{len(closed)} | 盈亏{total_pnl:+.2f}U")
                
                if cycle % 120 == 0:
                    from trader import generate_report
                    report = generate_report(data)
                    notify(report)
        
        except Exception as e:
            log(f"❌ 异常: {e}")
            import traceback
            traceback.print_exc()
        
        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "once":
            run_cycle()
        elif sys.argv[1] == "report":
            data = load_trades()
            print(generate_report(data))
    else:
        main()
