#!/usr/bin/env python3
"""
Spike-v13-P4 循环扫描器 — 300U进攻仓
按 1000u-g60b-spike-package 方案运行:
  - 单笔保证金 ~40U (4% of 1000U)
  - 最大持仓 3
  - 8h超时平仓
  - 只做多 + EMA多头 + RSI≥50
  - 硬止损规则
"""
from __future__ import annotations

import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data_spike"
DATA_DIR.mkdir(exist_ok=True)

# Spike-v13-P4 参数
SPIKE_POSITION_PCT = float(os.environ.get("SPIKE_POSITION_PCT", "0.04"))  # 4% of total account
SPIKE_ALLOCATED_BALANCE = float(os.environ.get("SPIKE_ALLOCATED_BALANCE", "300"))
SPIKE_MAX_POSITIONS = int(os.environ.get("SPIKE_MAX_POSITIONS", "3"))
SPIKE_MAX_HOLD_HOURS = float(os.environ.get("SPIKE_MAX_HOLD_HOURS", "8"))
SPIKE_DAILY_LOSS_LIMIT = float(os.environ.get("SPIKE_DAILY_LOSS_LIMIT", "25"))
SPIKE_CONSEC_LOSS_PAUSE = int(os.environ.get("SPIKE_CONSEC_LOSS_PAUSE", "3"))
SPIKE_LOOP_INTERVAL = int(os.environ.get("SPIKE_LOOP_INTERVAL", "60"))

# 导入trading-system模块
sys.path.insert(0, str(BASE_DIR))
from spike_scanner import scan_spike_signals
from binance_api import (
    get_price, place_order, cancel_all_orders,
    get_positions, get_klines, place_stop_loss_order,
    get_symbol_precision
)
from config import (
    LEVERAGE, BINANCE_API_KEY, BINANCE_TESTNET,
    COOLDOWN_HOURS
)

TZ_UTC8 = timezone(timedelta(hours=8))
TRADES_FILE = DATA_DIR / "trades.json"
STATE_FILE = DATA_DIR / "spike_state.json"

# === 风控状态 ===
def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"consec_losses": 0, "daily_loss": 0, "daily_loss_date": None, "paused_until": None}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def load_trades():
    if TRADES_FILE.exists():
        with open(TRADES_FILE) as f:
            return json.load(f)
    return {"initial_balance": SPIKE_ALLOCATED_BALANCE, "trades": []}

def save_trades(data):
    with open(TRADES_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def next_id(data):
    if not data["trades"]:
        return 1
    return max(t["id"] for t in data["trades"]) + 1

def get_local_balance(data):
    balance = data.get("initial_balance", SPIKE_ALLOCATED_BALANCE)
    for t in data["trades"]:
        if t["status"] == "closed" and t.get("pnl_usd") is not None:
            balance += t["pnl_usd"]
    return balance

def now_str():
    return datetime.now(TZ_UTC8).strftime("%Y-%m-%d %H:%M:%S")


# === 主循环 ===
def spike_tick():
    """单次扫描+监控循环"""
    data = load_trades()
    state = load_state()
    now = datetime.now(TZ_UTC8)
    today_str = now.strftime("%Y-%m-%d")

    # 重置日亏损
    if state.get("daily_loss_date") != today_str:
        state["daily_loss"] = 0
        state["daily_loss_date"] = today_str

    # 检查暂停
    if state.get("paused_until"):
        try:
            paused_dt = datetime.fromisoformat(state["paused_until"])
            if paused_dt.tzinfo is None:
                paused_dt = paused_dt.replace(tzinfo=TZ_UTC8)
            if now < paused_dt:
                print(f"[Spike] 暂停中，直到 {state['paused_until']}")
                save_state(state)
                return
            else:
                state["paused_until"] = None
                state["consec_losses"] = 0
                print("[Spike] 暂停结束，恢复扫描")
        except:
            state["paused_until"] = None

    open_positions = [t for t in data["trades"] if t["status"] == "open"]
    open_symbols = set(t["symbol"] for t in open_positions)

    # === 1. 监控持仓: 止损/止盈/超时 ===
    for trade in open_positions:
        price = get_price(trade["symbol"])
        if price <= 0:
            continue

        sl = trade["stop_loss"]
        tp = trade["take_profit"]
        triggered = None
        entry_dt = datetime.fromisoformat(trade["entry_time"])
        if entry_dt.tzinfo is None:
            entry_dt = entry_dt.replace(tzinfo=TZ_UTC8)
        hold_hours = (now - entry_dt).total_seconds() / 3600

        # 止损止盈
        if trade["direction"] == "long":
            if price <= sl:
                triggered = "止损"
            elif price >= tp:
                triggered = "止盈"
        else:
            if price >= sl:
                triggered = "止损"
            elif price <= tp:
                triggered = "止盈"

        # 8h超时平仓
        if not triggered and hold_hours >= SPIKE_MAX_HOLD_HOURS:
            triggered = "超时平仓(8h)"

        if triggered:
            # 下单平仓
            side = "SELL" if trade["direction"] == "long" else "BUY"
            try:
                close_result = place_order(trade["symbol"], side, abs(trade["quantity"]))
                close_success = close_result and "orderId" in str(close_result)
            except:
                close_success = False

            if close_success or True:  # 即使API失败也本地平仓
                exit_price = price
                entry = trade["entry_price"]
                lev = trade.get("leverage", LEVERAGE)
                pos_usd = trade.get("position_usd", 0)
                if trade["direction"] == "long":
                    pnl_pct = (exit_price - entry) / entry * 100 * lev if entry else 0
                else:
                    pnl_pct = (entry - exit_price) / entry * 100 * lev if entry else 0

                trade["exit_price"] = exit_price
                trade["exit_time"] = now_str()
                trade["exit_reason"] = triggered
                trade["pnl_pct"] = round(pnl_pct, 2)
                trade["pnl_usd"] = round(pnl_pct / 100 * pos_usd, 4)
                trade["status"] = "closed"

                # 更新风控状态
                pnl = trade["pnl_usd"]
                if pnl < 0:
                    state["consec_losses"] = state.get("consec_losses", 0) + 1
                    state["daily_loss"] = state.get("daily_loss", 0) + abs(pnl)
                else:
                    state["consec_losses"] = 0

                # 检查暂停规则
                if state["consec_losses"] >= SPIKE_CONSEC_LOSS_PAUSE:
                    pause_until = now + timedelta(hours=24)
                    state["paused_until"] = pause_until.strftime("%Y-%m-%d %H:%M:%S")
                    print(f"[Spike] ⚠️ 连续亏损{state['consec_losses']}笔，暂停24h")

                if state["daily_loss"] >= SPIKE_DAILY_LOSS_LIMIT:
                    # 暂停到明天0点
                    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0)
                    state["paused_until"] = tomorrow.strftime("%Y-%m-%d %H:%M:%S")
                    print(f"[Spike] ⚠️ 日亏损{state['daily_loss']:.1f}U≥{SPIKE_DAILY_LOSS_LIMIT}U，暂停到明日")

                symbol = trade["symbol"]
                d_cn = "多" if trade["direction"] == "long" else "空"
                print(f"[Spike] 平仓 #{trade['id']} {symbol} {d_cn} {triggered} {pnl:+.2f}U")

                # 通知
                try:
                    from notifier import notify
                    notify(f"🔴 [Spike] 平仓 #{trade['id']} {symbol} {d_cn} {triggered}\n"
                           f"PnL: {pnl:+.2f}U | 连亏: {state['consec_losses']} | 日亏: {state['daily_loss']:.1f}U")
                except:
                    pass

                try:
                    cancel_all_orders(trade["symbol"])
                except:
                    pass

    save_trades(data)
    save_state(state)

    # === 2. 扫描新信号 ===
    open_positions = [t for t in data["trades"] if t["status"] == "open"]
    open_symbols = set(t["symbol"] for t in open_positions)

    if len(open_positions) >= SPIKE_MAX_POSITIONS:
        print(f"[Spike] 持仓{len(open_positions)}/{SPIKE_MAX_POSITIONS}，跳过扫描")
        return

    # 加载冷却
    cooldowns = {}
    for t in data["trades"]:
        if t["status"] == "closed" and t.get("exit_time"):
            try:
                et = datetime.fromisoformat(t["exit_time"])
                if (now - et).total_seconds() < COOLDOWN_HOURS * 3600:
                    cooldowns[t["symbol"]] = t["exit_time"]
            except:
                pass

    spike_cooldowns = {}
    # TODO: 从state中加载spike冷却

    candidates = scan_spike_signals(open_symbols, cooldowns, spike_cooldowns)

    if not candidates:
        print(f"[Spike] 无符合条件的信号")
        return

    # 按强度排序
    strength_order = {"S": 3, "A": 2, "B": 1}
    candidates.sort(key=lambda c: (strength_order.get(c["strength"], 0), c.get("rr", 0)), reverse=True)

    for cand in candidates[:SPIKE_MAX_POSITIONS - len(open_positions)]:
        balance = get_local_balance(data)
        # 单笔保证金: 4% of 1000U = 40U
        pos_usd = min(balance * SPIKE_POSITION_PCT, 52)  # 最高52U
        if pos_usd < 10:
            print(f"[Spike] 余额不足 ({balance:.1f}U)，跳过")
            break

        price = cand["price"]
        sl_pct = cand["sl_pct"]
        tp_pct = cand["tp_pct"]
        sl = round(price * (1 - sl_pct), 6)
        tp = round(price * (1 + tp_pct), 6)
        rr = cand["rr"]

        trade = {
            "id": next_id(data),
            "symbol": cand["symbol"],
            "direction": cand["direction"],
            "leverage": LEVERAGE,
            "position_usd": round(pos_usd, 4),
            "entry_price": price,
            "stop_loss": sl,
            "take_profit": tp,
            "entry_time": now_str(),
            "exit_price": None, "exit_time": None, "exit_reason": None,
            "pnl_pct": None, "pnl_usd": None,
            "status": "open",
            "signal_type": "spike",
            "signal_strength": cand["strength"],
            "signal_reason": cand["reason"],
            "strategy": "Spike-v13-P4",
            "quantity": None,
            "binance_order_id": None,
        }

        # 下单
        try:
            side = "BUY"  # SPIKE_LONG_ONLY
            prec_info = get_symbol_precision(cand["symbol"])
            qty_prec = prec_info.get("quantity_precision", 3)
            qty = round(pos_usd / price, qty_prec)
            open_result = place_order(cand["symbol"], side, qty)
            if open_result and "orderId" in str(open_result):
                trade["quantity"] = qty
                trade["binance_order_id"] = open_result.get("orderId")
                trade["entry_price"] = price
                # 重算SL/TP
                fp = trade["entry_price"]
                trade["stop_loss"] = round(fp * (1 - sl_pct), 6)
                trade["take_profit"] = round(fp * (1 + tp_pct), 6)

                # 挂交易所止损
                try:
                    place_stop_loss_order(cand["symbol"], trade["quantity"], "long", trade["stop_loss"])
                except Exception as e:
                    print(f"[Spike] 挂止损失败: {e}")
            else:
                print(f"[Spike] 下单失败: {open_result}")
                continue
        except Exception as e:
            print(f"[Spike] 下单异常: {e}")
            continue

        data["trades"].append(trade)
        save_trades(data)

        d_cn = "多" if cand["direction"] == "long" else "空"
        print(f"[Spike] ✅ 开仓 #{trade['id']} {cand['symbol']} {d_cn}")
        print(f"  价格={price} SL={sl:.6f} TP={tp:.6f} RR={rr} 仓位={pos_usd:.1f}U")
        print(f"  原因: {cand['reason']}")

        # 通知
        try:
            from notifier import notify
            notify(f"🟢 [Spike] 开仓 #{trade['id']} {cand['symbol']} {d_cn}\n"
                   f"价格: {price} | SL: {sl:.4f} | TP: {tp:.4f}\n"
                   f"RR: {rr} | 仓位: {pos_usd:.1f}U\n"
                   f"原因: {cand['reason']}")
        except:
            pass


def main():
    print(f"=== Spike-v13-P4 启动 [{now_str()}] ===")
    print(f"  余额: {SPIKE_ALLOCATED_BALANCE}U")
    print(f"  单笔: {SPIKE_POSITION_PCT*100}% (~{SPIKE_POSITION_PCT*1000:.0f}U)")
    print(f"  最大持仓: {SPIKE_MAX_POSITIONS}")
    print(f"  超时: {SPIKE_MAX_HOLD_HOURS}h")
    print(f"  扫描间隔: {SPIKE_LOOP_INTERVAL}s")
    print()

    while True:
        try:
            spike_tick()
        except Exception as e:
            print(f"[Spike] 异常: {e}")
        time.sleep(SPIKE_LOOP_INTERVAL)


if __name__ == "__main__":
    raise SystemExit(main())
