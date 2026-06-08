#!/usr/bin/env python3
"""
S24-Ignition testnet trader — capped300 + dynamic
基于 s24_paper_trader.py 改造，接入币安testnet真实下单 + 微信/Telegram通知

Cron one-shot:
  python3 s24_trader.py --once

Loop mode:
  python3 s24_trader.py --loop --interval 60
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from config import *
from binance_api import (
    get_klines as api_get_klines,
    get_symbol_precision,
    set_leverage,
    open_long,
    close_position,
    place_stop_loss_order,
    place_order,
    cancel_all_orders,
    get_order,
    get_price,
    get_positions as api_get_positions,
    get_balance as api_get_balance,
    format_usd,
)
from notifier import notify, send_message_to_both

TZ_UTC8 = timezone(timedelta(hours=8))
MS_15M = 15 * 60 * 1000
MS_1H = 60 * 60 * 1000


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def now_ms() -> int:
    return int(time.time() * 1000)


def iso(ts: int | None = None) -> str:
    ts = now_ms() if ts is None else ts
    return datetime.fromtimestamp(ts / 1000, tz=TZ_UTC8).isoformat()


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


# ── K线获取（用binance_api的公开数据接口） ─────────────────────────
# ── K线获取（用binance_api的公开数据接口） ─────────────────────────

_last_req_ts = 0.0

def get_klines(symbol: str, interval: str, limit: int = 96) -> list[Candle]:
    """通过binance_api获取K线（公开接口，走正式API保证数据一致性）"""
    global _last_req_ts
    elapsed = time.time() - _last_req_ts
    if elapsed < 0.2:
        time.sleep(0.2 - elapsed)
    raw = api_get_klines(symbol, interval, limit=limit)
    _last_req_ts = time.time()
    if not raw:
        return []
    return [Candle(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[7])) for k in raw]


# ── 技术指标 ──────────────────────────────────────────────────────

def ema_series(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2 / (period + 1)
    val = sum(values[:period]) / period
    out[period - 1] = val
    for i in range(period, len(values)):
        val = values[i] * k + val * (1 - k)
        out[i] = val
    return out


def rsi(closes: list[float], period: int = RSI_PERIOD) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    return 100.0 if al == 0 else 100 - 100 / (1 + ag / al)


def atr(candles: list[Candle], period: int = ATR_PERIOD) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    return sum(trs[-period:]) / period


def signal_quality(chg_pct: float, atr_val: float, candle: Candle, history: list[Candle], threshold: float) -> float:
    score = 0.0
    score += min(35.0, (chg_pct - threshold) / threshold * 20 + 20)
    if len(history) >= 10:
        avg_vol = sum(c.volume for c in history[-10:]) / 10
        if avg_vol > 0:
            score += min(30.0, candle.volume / avg_vol * 10)
    if atr_val > 0 and candle.close > 0:
        atr_pct = atr_val / candle.close
        if 0.005 <= atr_pct <= 0.04:
            score += 20.0
        elif atr_pct < 0.005:
            score += 5.0
        else:
            score += max(0.0, 20.0 - (atr_pct - 0.04) * 200)
    rng = candle.high - candle.low
    if rng > 0:
        score += min(15.0, abs(candle.close - candle.open) / rng * 20)
    return round(min(100.0, max(0.0, score)), 1)


def pos_pct_for(q: float) -> float:
    if q >= 90: return POSITION_PCT_HIGH
    if q >= 80: return POSITION_PCT_MID
    return POSITION_PCT_LOW


def closed_candles(candles: list[Candle], interval_ms: int) -> list[Candle]:
    n = now_ms()
    return [c for c in candles if c.time + interval_ms <= n]


def prev_1h_bullish(k1h: list[Candle], signal_time: int) -> bool:
    closed = [c for c in k1h if c.time + MS_1H <= signal_time]
    if len(closed) < EMA_SLOW + 1:
        return False
    closes = [c.close for c in closed]
    e9 = ema_series(closes, EMA_FAST)[-1]
    e21 = ema_series(closes, EMA_SLOW)[-1]
    return bool(e9 is not None and e21 is not None and e9 > e21)


# ── 状态管理 ──────────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"balance": INITIAL_BALANCE, "positions": [], "cooldowns": {},
            "next_id": 1, "dyn_cooldowns": {}, "halt_until": 0, "entries": []}


def save_state(st: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2))
    tmp.replace(STATE_FILE)


def load_recent_trades() -> list[dict]:
    if not TRADES_FILE.exists():
        return []
    out = []
    for line in TRADES_FILE.read_text().splitlines():
        try: out.append(json.loads(line))
        except Exception: pass
    return out


def trade_exit_ms(t: dict) -> int:
    if t.get("exit_time_ms"):
        return int(t["exit_time_ms"])
    text = t.get("exit_time")
    if text:
        try:
            return int(datetime.fromisoformat(text).timestamp() * 1000)
        except Exception:
            return 0
    return 0


# ── 动态黑名单 ────────────────────────────────────────────────────

def refresh_dynamic_cooldowns(st: dict, trades: list[dict], enable: bool) -> None:
    if not enable:
        return
    ts = now_ms()
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_sym[t.get("symbol", "")].append(t)
    for sym, arr in by_sym.items():
        recent = arr[-DYN_WIN:]
        if len(recent) >= DYN_WIN:
            nloss = sum(1 for t in recent if t.get("pnl_usd", 0) <= 0)
            gp = sum(t.get("pnl_usd", 0) for t in recent if t.get("pnl_usd", 0) > 0)
            gl = abs(sum(t.get("pnl_usd", 0) for t in recent if t.get("pnl_usd", 0) <= 0))
            pf = gp / gl if gl > 0 else 99.0
            if nloss >= DYN_LOSS_N:
                st["dyn_cooldowns"][sym] = max(st["dyn_cooldowns"].get(sym, 0),
                                                ts + DYN_COOL1_DAYS * 24 * MS_1H)
            if pf < DYN_PF_MIN:
                st["dyn_cooldowns"][sym] = max(st["dyn_cooldowns"].get(sym, 0),
                                                ts + DYN_COOL2_DAYS * 24 * MS_1H)
        cutoff_7d = ts - 7 * 24 * MS_1H
        week_pnl = sum(t.get("pnl_usd", 0) for t in arr if trade_exit_ms(t) > cutoff_7d)
        if week_pnl < -st.get("balance", INITIAL_BALANCE) * DYN_SYM_LOSS_PCT:
            st["dyn_cooldowns"][sym] = max(st["dyn_cooldowns"].get(sym, 0),
                                            ts + DYN_COOL2_DAYS * 24 * MS_1H)
    day = datetime.fromtimestamp(ts / 1000, tz=TZ_UTC8).strftime("%Y-%m-%d")
    day_pnl = sum(t.get("pnl_usd", 0) for t in trades if str(t.get("exit_time", "")).startswith(day))
    if day_pnl < -st.get("balance", INITIAL_BALANCE) * DYN_DAILY_PCT:
        dt = datetime.fromtimestamp(ts / 1000, tz=TZ_UTC8)
        eod = dt.replace(hour=23, minute=59, second=59, microsecond=0)
        st["halt_until"] = max(st.get("halt_until", 0), int(eod.timestamp() * 1000))


def weekly_entry_count(st: dict, symbol: str) -> int:
    cutoff = now_ms() - 7 * 24 * MS_1H
    st["entries"] = [e for e in st.get("entries", []) if e.get("time", 0) > cutoff]
    return sum(1 for e in st["entries"] if e.get("symbol") == symbol)


# ── 通知 ──────────────────────────────────────────────────────────

def notify_open(pos: dict, signal: dict) -> None:
    """发送开仓通知"""
    msg = f"""🟢 S24 开仓
币种: {pos['symbol']}
方向: 做多
入场价: {pos['entry_price']}
保证金: {pos['margin_usd']:.2f}U ({pos['margin_usd']/pos['notional_usd']*100:.0f}% × {LEVERAGE}x)
名义值: {pos['notional_usd']:.2f}U
止损: {pos['stop_loss']} ({pos['sl_pct']*100:.1f}%)
止盈: {pos['take_profit']} ({pos['tp_pct']*100:.1f}%)
质量分: {pos['quality']}
涨幅: {pos.get('change_pct', 'N/A')}%
RSI: {pos.get('rsi', 'N/A')}
时间: {pos['entry_time']}"""
    send_message_to_both(msg)


def notify_close(pos: dict, exit_price: float, reason: str, pnl: float, balance: float) -> None:
    """发送平仓通知"""
    emoji = "✅" if pnl > 0 else "❌"
    hold_h = (now_ms() - pos.get("entry_time_ms", now_ms())) / MS_1H
    msg = f"""{emoji} S24 平仓
币种: {pos['symbol']}
入场: {pos['entry_price']}
平仓: {exit_price:.8f}
原因: {reason}
PnL: {pnl:+.2f}U
持仓时长: {hold_h:.1f}h
质量分: {pos['quality']}
当前余额: {balance:.2f}U"""
    send_message_to_both(msg)


# ── 信号扫描 ──────────────────────────────────────────────────────

def scan_symbol(sym: str, args: argparse.Namespace, st: dict) -> dict | None:
    k15 = closed_candles(get_klines(sym, "15m", 96), MS_15M)
    if len(k15) < ATR_PERIOD + RSI_PERIOD + 20:
        return None
    sig = k15[-1]
    if args.hour_only and sig.time % MS_1H != 0:
        return None
    chg = (sig.close - sig.open) / sig.open if sig.open > 0 else 0
    if chg < args.threshold:
        return None
    if not prev_1h_bullish(get_klines(sym, "1h", 80), sig.time + MS_15M):
        return None
    closes = [c.close for c in k15]
    r = rsi(closes[-(RSI_PERIOD + 6):])
    if r < args.rsi:
        return None
    atr_val = atr(k15[-(ATR_PERIOD + 6):-1])
    sl_pct = max(MIN_SL_PCT, min(MAX_SL_PCT, atr_val * ATR_SL_MULT / sig.close if sig.close > 0 else MIN_SL_PCT))
    q = signal_quality(chg, atr_val, sig, k15[-21:-1], args.threshold)
    if q < args.quality:
        return None
    expected_entry_time = sig.time + MS_15M
    lag_ms = now_ms() - expected_entry_time
    if lag_ms < 0:
        return None
    if lag_ms > args.max_entry_lag_sec * 1000:
        append_jsonl(DECISIONS_FILE, {
            "time": iso(), "symbol": sym, "status": "rejected",
            "reason": "entry_window_missed", "lag_sec": round(lag_ms / 1000, 1),
            "quality": q, "change_pct": round(chg * 100, 3), "rsi": round(r, 2),
        })
        notify_send(f"⏰ S24 信号被拒 {sym}\n原因: 入场窗口超时 ({lag_ms/1000:.0f}s > {args.max_entry_lag_sec}s)\n质量: {q} | 涨幅: {chg*100:.2f}% | RSI: {r:.1f}")
        return None
    # 获取预期入场K线开盘价；找不到则拒绝，避免错用后一根K线价格
    all15 = get_klines(sym, "15m", 3)
    entry_candle = next((c for c in all15 if c.time == expected_entry_time), None)
    if not entry_candle:
        append_jsonl(DECISIONS_FILE, {
            "time": iso(), "symbol": sym, "status": "rejected",
            "reason": "entry_candle_unavailable", "expected_entry_time_ms": expected_entry_time,
            "lag_sec": round(lag_ms / 1000, 1),
            "quality": q, "change_pct": round(chg * 100, 3), "rsi": round(r, 2),
        })
        notify_send(f"⏰ S24 信号被拒 {sym}\n原因: 入场K线不可用，拒绝错位入场\n质量: {q} | 涨幅: {chg*100:.2f}% | RSI: {r:.1f}")
        return None
    entry = entry_candle.open
    return {"symbol": sym, "signal_time_ms": sig.time, "entry_time_ms": expected_entry_time,
            "entry_price_raw": entry, "sl_pct": sl_pct, "tp_pct": sl_pct * args.tp_ratio,
            "quality": q, "change_pct": round(chg * 100, 3), "rsi": round(r, 2)}


# ── 真实下单 ──────────────────────────────────────────────────────


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def order_fill_price(sym: str, order: dict | None, fallback: float = 0.0) -> float:
    """尽量取真实成交均价；MARKET 返回 avgPrice=0 时回查 orderId。"""
    order = order or {}
    candidates: list[float] = []
    candidates.append(_float(order.get("avgPrice"), 0.0))
    cum_quote = _float(order.get("cumQuote"), 0.0)
    executed = _float(order.get("executedQty"), 0.0)
    if cum_quote > 0 and executed > 0:
        candidates.append(cum_quote / executed)
    oid = order.get("orderId")
    if oid:
        detail = get_order(sym, oid)
        candidates.append(_float(detail.get("avgPrice"), 0.0))
        dq = _float(detail.get("cumQuote"), 0.0)
        de = _float(detail.get("executedQty"), 0.0)
        if dq > 0 and de > 0:
            candidates.append(dq / de)
    candidates.append(fallback)
    candidates.append(get_price(sym))
    for x in candidates:
        if x and x > 0:
            return float(x)
    return 0.0


def market_close(sym: str, quantity: float, direction: str, fallback_price: float = 0.0) -> tuple[bool, float, dict | None]:
    """市价平仓，返回 (是否成功, 成交价, 原始结果)。"""
    if quantity <= 0:
        return False, 0.0, {"error": "quantity_zero"}
    result = close_position(sym, quantity, direction)
    if not result or "orderId" not in result:
        return False, fallback_price, result
    px = order_fill_price(sym, result, fallback_price)
    return px > 0, px, result


def record_emergency_pending(st: dict, sym: str, quantity: float, direction: str, reason: str,
                             entry_price: float = 0.0, notional_usd: float = 0.0,
                             source_order_id: Any = None) -> None:
    """应急平仓失败时，把仓位留在 state，下一轮优先继续平。"""
    pos = {
        "id": st.get("next_id", 1),
        "symbol": sym,
        "entry_time": iso(),
        "entry_time_ms": now_ms(),
        "signal_time_ms": 0,
        "entry_price": round(entry_price, 8),
        "margin_usd": round(notional_usd / LEVERAGE, 4) if notional_usd > 0 else 0.0,
        "notional_usd": round(notional_usd, 4),
        "quantity": quantity,
        "direction": direction,
        "stop_loss": 0.0,
        "take_profit": 0.0,
        "sl_pct": 0.0,
        "tp_pct": 0.0,
        "quality": 0.0,
        "change_pct": 0.0,
        "rsi": 0.0,
        "binance_order_id": source_order_id,
        "emergency_close_pending": True,
        "emergency_reason": reason,
    }
    st["next_id"] = pos["id"] + 1
    st.setdefault("positions", []).append(pos)
    append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "emergency_close_pending", "reason": reason, "position": pos})


def open_position_live(st: dict, cand: dict, args: argparse.Namespace) -> bool:
    """在testnet真实开仓 + 挂止损止盈；保护失败则立即平仓。"""
    balance = st.get("balance", INITIAL_BALANCE)
    margin = min(balance * pos_pct_for(cand["quality"]), args.margin_cap)
    margin = min(margin, balance)
    if margin <= 0:
        return False

    sym = cand["symbol"]
    entry_price = cand["entry_price_raw"]

    precision = get_symbol_precision(sym)
    qty_precision = precision.get("quantity_precision", 3)
    price_precision = precision.get("price_precision", 6)

    notional = margin * LEVERAGE
    quantity = round(notional / entry_price, qty_precision) if entry_price > 0 else 0
    if quantity <= 0:
        append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "rejected",
                                       "reason": "quantity_zero", "margin": margin, "price": entry_price})
        return False

    set_leverage(sym, LEVERAGE)
    result = open_long(sym, quantity, leverage=LEVERAGE)
    if not result or "orderId" not in result:
        err_msg = str(result)[:200] if result else "no response"
        append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "open_failed", "error": err_msg})
        notify_send(f"⚠️ S24 开仓失败 {sym}: {err_msg}")
        return False

    fill_price = order_fill_price(sym, result, entry_price)
    if fill_price <= 0:
        append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "invalid_fill_price",
                                       "order": result, "fallback_entry": entry_price})
        notify_send(f"🔴 S24 {sym} 开仓后无法确认成交价，立即应急平仓")
        cancel_all_orders(sym)
        ok, px, close_result = market_close(sym, quantity, "long", entry_price)
        if ok:
            append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "invalid_fill_closed", "exit_price": px})
        else:
            record_emergency_pending(st, sym, quantity, "long", "invalid_fill_price", 0.0, 0.0, result.get("orderId"))
            notify_send(f"🔴 S24 {sym} 成交价无效且应急平仓失败，请人工检查！错误: {str(close_result)[:120]}")
        return False

    fee = (quantity * fill_price) * 0.0004
    sl_price = round(fill_price * (1 - cand["sl_pct"]), price_precision)
    tp_price = round(fill_price * (1 + cand["tp_pct"]), price_precision)

    sl_result = place_stop_loss_order(sym, quantity, "long", sl_price)
    sl_ok = bool(sl_result and "orderId" in sl_result)
    if not sl_ok:
        append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "sl_failed",
                                       "sl_price": sl_price, "error": str(sl_result)[:200]})

    tp_result = place_order(sym, "SELL", quantity,
                            order_type="TAKE_PROFIT_MARKET",
                            stop_price=tp_price, reduce_only=True)
    tp_ok = bool(tp_result and "orderId" in tp_result)
    if not tp_ok:
        append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "tp_failed",
                                       "tp_price": tp_price, "error": str(tp_result)[:200]})

    if not sl_ok or not tp_ok:
        append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "protective_order_failed",
                                       "sl_ok": sl_ok, "tp_ok": tp_ok, "quantity": quantity,
                                       "entry_price": fill_price})
        notify_send(f"🔴 S24 {sym} 开仓成功但保护单失败，立即市价平仓\n止损: {'✅' if sl_ok else '❌'} | 止盈: {'✅' if tp_ok else '❌'}")
        cancel_all_orders(sym)
        ok, close_px, close_result = market_close(sym, quantity, "long", fill_price)
        if ok:
            exit_adj = close_px * (1 - 0.0005)
            actual_notional = quantity * fill_price
            gross = actual_notional * (exit_adj - fill_price) / fill_price
            net = gross - actual_notional * 0.0004
            append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "protective_failed_closed",
                                           "exit_price": round(exit_adj, 8), "pnl_usd": round(net, 4)})
            notify_send(f"✅ S24 {sym} 保护单失败后已应急平仓，PnL≈{net:+.2f}U")
        else:
            actual_notional = quantity * fill_price
            record_emergency_pending(st, sym, quantity, "long", "protective_order_failed", fill_price, actual_notional, result.get("orderId"))
            notify_send(f"🔴 S24 {sym} 保护单失败且应急平仓失败，请人工检查！错误: {str(close_result)[:120]}")
        return False

    actual_notional = quantity * fill_price
    actual_margin = actual_notional / LEVERAGE
    pos = {
        "id": st.get("next_id", 1), "symbol": sym,
        "entry_time": iso(), "entry_time_ms": now_ms(),
        "signal_time_ms": cand["signal_time_ms"],
        "entry_price": fill_price,
        "margin_usd": round(actual_margin, 4),
        "notional_usd": round(actual_notional, 4),
        "quantity": quantity,
        "direction": "long",
        "stop_loss": sl_price,
        "take_profit": tp_price,
        "sl_pct": cand["sl_pct"], "tp_pct": cand["tp_pct"],
        "quality": cand["quality"],
        "change_pct": cand["change_pct"], "rsi": cand["rsi"],
        "binance_order_id": result.get("orderId"),
        "sl_order_id": sl_result.get("orderId") if isinstance(sl_result, dict) else None,
        "tp_order_id": tp_result.get("orderId") if isinstance(tp_result, dict) else None,
    }
    st["next_id"] = pos["id"] + 1
    st.setdefault("positions", []).append(pos)
    st.setdefault("entries", []).append({"symbol": sym, "time": now_ms()})
    # testnet 本地 balance 按 paper 口径只记录已实现PnL，不扣占用保证金
    st["balance"] = max(0.0, balance - fee)

    append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "opened", "position": pos})
    notify_open(pos, cand)
    print(f"  [OPEN] {sym} qty={quantity} entry={fill_price} sl={sl_price} tp={tp_price} q={cand['quality']}")
    return True

def notify_send(text: str) -> None:
    """简易通知"""
    send_message_to_both(text)


# ── 平仓检查 ──────────────────────────────────────────────────────

def close_positions_live(st: dict, symbols: list[str]) -> None:
    """检查持仓是否触发平仓条件；应急/超时仓位优先市价平。"""
    positions = st.get("positions", [])
    if not positions:
        return
    still = []
    for p in positions:
        sym = p["symbol"]
        direction = p.get("direction", "long")
        quantity = float(p.get("quantity", 0) or 0)
        held_h = (now_ms() - int(p.get("entry_time_ms", now_ms()))) / MS_1H
        reason = None
        exit_price_hint = float(p.get("entry_price", 0) or 0)

        # 1) 应急待平仓位：不看K线，优先继续平
        if p.get("emergency_close_pending"):
            reason = "emergency_close_pending"
        # 2) 硬性兜底：4.5h 强制平仓，不依赖K线获取成功
        elif held_h >= MAX_HOLD_HOURS + 0.5:
            reason = "force_max_hold"
        else:
            try:
                k15 = get_klines(sym, "15m", 3)
                c = k15[-1]
            except Exception as e:
                append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "close_check_error", "error": str(e)})
                still.append(p); continue

            if held_h >= GRACE_HOURS and c.low <= p.get("stop_loss", 0):
                reason = "stop_loss"; exit_price_hint = p.get("stop_loss", c.close)
            elif c.high >= p.get("take_profit", 10**18):
                reason = "take_profit"; exit_price_hint = p.get("take_profit", c.close)
            elif held_h >= MAX_HOLD_HOURS:
                reason = "max_hold"; exit_price_hint = c.close

        if not reason:
            still.append(p); continue
        if quantity <= 0:
            append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "close_failed", "reason": "quantity_zero"})
            still.append(p); continue

        cancel_all_orders(sym)
        ok, fill_px, result = market_close(sym, quantity, direction, exit_price_hint)
        if not ok:
            p["emergency_close_pending"] = True
            p["emergency_reason"] = reason
            append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "emergency_close_failed",
                                           "reason": reason, "error": str(result)[:200]})
            notify_send(f"🔴 S24 {sym} 平仓失败，下一轮继续尝试\n原因: {reason}\n错误: {str(result)[:120]}")
            still.append(p); continue

        exit_price_adj = fill_px * (1 - 0.0005)
        entry_px = float(p.get("entry_price", 0) or 0)
        notional_usd = float(p.get("notional_usd", 0) or 0)
        if entry_px > 0 and notional_usd > 0:
            gross = notional_usd * (exit_price_adj - entry_px) / entry_px
            fee = notional_usd * 0.0004
            net = gross - fee
        else:
            net = 0.0

        st["balance"] = max(0.0, st.get("balance", INITIAL_BALANCE) + net)
        st.setdefault("cooldowns", {})[sym] = now_ms() + int(COOLDOWN_HOURS * MS_1H)

        tr = {**p, "exit_time": iso(), "exit_time_ms": now_ms(),
              "exit_price": round(exit_price_adj, 8), "exit_reason": reason,
              "pnl_usd": round(net, 4), "balance_after": round(st["balance"], 4)}
        append_jsonl(TRADES_FILE, tr)
        append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "closed",
                                       "reason": reason, "pnl_usd": round(net, 4)})
        notify_close(p, exit_price_adj, reason, net, st["balance"])
        print(f"  [CLOSE] {sym} {reason} pnl={net:+.2f}U")

    st["positions"] = still


def sync_orphan_positions(st: dict) -> None:
    """同步交易所真实持仓；本地未追踪的 orphan 仓位立即平仓。"""
    local_syms = {p.get("symbol") for p in st.get("positions", [])}
    try:
        exchange_positions = api_get_positions()
    except Exception as e:
        append_jsonl(DECISIONS_FILE, {"time": iso(), "status": "position_sync_error", "error": str(e)})
        notify_send(f"🔴 S24 持仓同步失败\n错误: {str(e)[:150]}")
        return
    if not isinstance(exchange_positions, list):
        append_jsonl(DECISIONS_FILE, {"time": iso(), "status": "position_sync_error", "error": str(exchange_positions)[:200]})
        return

    for ep in exchange_positions:
        sym = ep.get("symbol")
        amt = _float(ep.get("positionAmt"), 0.0)
        if not sym or abs(amt) <= 0:
            continue
        if sym in local_syms:
            continue

        quantity = abs(amt)
        direction = "long" if amt > 0 else "short"
        entry_px = _float(ep.get("entryPrice"), 0.0)
        notional = abs(_float(ep.get("notional"), 0.0))
        append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "orphan_position",
                                       "positionAmt": amt, "direction": direction,
                                       "entry_price": entry_px, "notional": notional})
        notify_send(f"🔴 S24 检测到 orphan 仓位 {sym}\n数量: {amt}\n处理: 立即市价平仓")
        cancel_all_orders(sym)
        ok, px, result = market_close(sym, quantity, direction, entry_px)
        if ok:
            append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "orphan_closed",
                                           "exit_price": round(px, 8), "quantity": quantity})
            notify_send(f"✅ S24 orphan 仓位已平仓 {sym}\n价格: {px}")
        else:
            append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "orphan_close_failed",
                                           "error": str(result)[:200]})
            record_emergency_pending(st, sym, quantity, direction, "orphan_position", entry_px, notional, None)
            notify_send(f"🔴 S24 orphan 平仓失败 {sym}，已加入应急待平 state\n错误: {str(result)[:120]}")


# ── 主循环 ────────────────────────────────────────────────────────

def run_once(args: argparse.Namespace) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    st = load_state()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or COMMON_SYMBOLS
    exclude = {s.strip().upper() for s in args.exclude.split(",") if s.strip()} if args.exclude else set(DEFAULT_EXCLUDE)
    symbols = [s for s in symbols if s not in exclude]

    # 先同步交易所真实仓位，再检查本地持仓平仓
    sync_orphan_positions(st)
    close_positions_live(st, symbols)

    # 动态黑名单
    trades = load_recent_trades()
    refresh_dynamic_cooldowns(st, trades, args.dynamic)

    # 日熔断检查
    if now_ms() < st.get("halt_until", 0):
        append_jsonl(DECISIONS_FILE, {"time": iso(), "status": "halted", "until": iso(st["halt_until"])})
        save_state(st); return

    # 扫描开仓
    open_syms = {p["symbol"] for p in st.get("positions", [])}
    for sym in symbols:
        if len(st.get("positions", [])) >= MAX_POSITIONS:
            break
        if sym in open_syms:
            continue
        if st.get("cooldowns", {}).get(sym, 0) > now_ms():
            continue
        if args.dynamic and st.get("dyn_cooldowns", {}).get(sym, 0) > now_ms():
            continue
        if args.sym_cap > 0 and weekly_entry_count(st, sym) >= args.sym_cap:
            continue
        try:
            cand = scan_symbol(sym, args, st)
        except Exception as e:
            append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "scan_error", "error": str(e)})
            notify_send(f"🔴 S24 扫描异常 {sym}\n错误: {str(e)[:150]}")
            continue
        if not cand:
            continue
        open_position_live(st, cand, args)
        open_syms.add(sym)

    st["updated_at"] = iso()
    save_state(st)
    n_pos = len(st.get("positions", []))
    bal = round(st.get("balance", INITIAL_BALANCE), 2)
    print(json.dumps({"time": iso(), "balance": bal, "positions": n_pos}, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="S24-Ignition testnet trader")
    p.add_argument("--once", action="store_true")
    p.add_argument("--loop", action="store_true")
    p.add_argument("--interval", type=int, default=SCAN_INTERVAL)
    p.add_argument("--symbols", default="")
    p.add_argument("--exclude", default=",".join(sorted(DEFAULT_EXCLUDE)))
    p.add_argument("--threshold", type=float, default=SPIKE_THRESHOLD)
    p.add_argument("--quality", type=float, default=MIN_QUALITY)
    p.add_argument("--rsi", type=float, default=MIN_RSI)
    p.add_argument("--tp-ratio", type=float, default=TP_SL_RATIO)
    p.add_argument("--sym-cap", type=int, default=SYM_WEEKLY_CAP)
    p.add_argument("--margin-cap", type=float, default=MARGIN_CAP)
    p.add_argument("--max-entry-lag-sec", type=int, default=MAX_ENTRY_LAG_SEC)
    p.add_argument("--dynamic", action="store_true", default=True)
    p.add_argument("--no-dynamic", action="store_false", dest="dynamic")
    p.add_argument("--hour-only", action="store_true", default=True)
    p.add_argument("--no-hour-only", action="store_false", dest="hour_only")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.once and not args.loop:
        args.once = True
    while True:
        try:
            run_once(args)
        except Exception as e:
            print(f"[ERROR] {e}")
            append_jsonl(DECISIONS_FILE, {"time": iso(), "status": "run_error", "error": str(e)})
            try:
                notify_send(f"🔴 S24 运行崩溃\n错误: {str(e)[:200]}")
            except Exception:
                pass
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
