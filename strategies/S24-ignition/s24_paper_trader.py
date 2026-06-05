#!/usr/bin/env python3
"""
S24-Ignition paper trader / testnet simulator.

Cron one-shot:
  python3 strategies/S24-ignition/s24_paper_trader.py --once

Loop mode:
  python3 strategies/S24-ignition/s24_paper_trader.py --loop --interval 60

This is paper trading only: no API keys, no real orders.
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

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "strategies" / "S24-ignition" / "data"
STATE_FILE = DATA_DIR / "s24_paper_state.json"
TRADES_FILE = DATA_DIR / "s24_paper_trades.jsonl"
DECISIONS_FILE = DATA_DIR / "s24_paper_decisions.jsonl"
TZ_UTC8 = timezone(timedelta(hours=8))

INITIAL_BALANCE = 1000.0
LEVERAGE = 3.0
FEE_RATE = 0.0004
SLIPPAGE_RATE = 0.0005
MAX_POSITIONS = 3
COOLDOWN_HOURS = 4

SPIKE_THRESHOLD = 0.012
MIN_RSI = 50.0
MIN_QUALITY = 70.0
ATR_PERIOD = 14
RSI_PERIOD = 14
EMA_FAST = 9
EMA_SLOW = 21
ATR_SL_MULT = 1.5
MIN_SL_PCT = 0.030
MAX_SL_PCT = 0.090
TP_SL_RATIO = 2.5
MAX_HOLD_HOURS = 4.0
GRACE_HOURS = 0.5
SYM_WEEKLY_CAP = 5
MARGIN_CAP = 300.0

POSITION_PCT_LOW = 0.07
POSITION_PCT_MID = 0.10
POSITION_PCT_HIGH = 0.15

DYN_WIN = 10
DYN_LOSS_N = 8
DYN_PF_MIN = 0.8
DYN_SYM_LOSS_PCT = 0.03
DYN_COOL1_DAYS = 7
DYN_COOL2_DAYS = 14
DYN_DAILY_PCT = 0.03

COMMON_SYMBOLS = [
    "XAGUSDT","XAUUSDT","LABUSDT","SUIUSDT","XRPUSDT","BUSDT","CRCLUSDT",
    "BILLUSDT","BNBUSDT","SNDKUSDT","TONUSDT","GTCUSDT","1000PEPEUSDT",
    "SKYAIUSDT","VVVUSDT","SAGAUSDT","MUUSDT","ADAUSDT","INTCUSDT","LDOUSDT",
    "AVAXUSDT","LINKUSDT","PAXGUSDT","AAVEUSDT",
]
DEFAULT_EXCLUDE = {"BUSDT", "BILLUSDT", "BNBUSDT", "LINKUSDT", "SAGAUSDT"}

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


def api_get(endpoint: str, params: dict[str, Any]) -> Any:
    url = "https://fapi.binance.com" + endpoint + "?" + urlencode(params)
    with urlopen(url, timeout=15) as r:
        return json.loads(r.read())


# 请求间延迟，防止突发触发封禁
_last_request_ts = 0.0
API_DELAY_SEC = 0.2  # 每个请求间至少200ms


def api_get_throttled(endpoint: str, params: dict[str, Any]) -> Any:
    global _last_request_ts
    elapsed = time.time() - _last_request_ts
    if elapsed < API_DELAY_SEC:
        time.sleep(API_DELAY_SEC - elapsed)
    result = api_get(endpoint, params)
    _last_request_ts = time.time()
    return result


def get_klines(symbol: str, interval: str, limit: int = 96) -> list[Candle]:
    raw = api_get_throttled("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    return [Candle(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[7])) for k in raw]


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


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"balance": INITIAL_BALANCE, "positions": [], "cooldowns": {}, "next_id": 1,
            "dyn_cooldowns": {}, "halt_until": 0, "entries": []}


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
                st["dyn_cooldowns"][sym] = max(st["dyn_cooldowns"].get(sym, 0), ts + DYN_COOL1_DAYS * 24 * MS_1H)
            if pf < DYN_PF_MIN:
                st["dyn_cooldowns"][sym] = max(st["dyn_cooldowns"].get(sym, 0), ts + DYN_COOL2_DAYS * 24 * MS_1H)
        # Rule 3: 近 7 天单币亏损超过余额 3% → 冷却 14 天
        cutoff_7d = ts - 7 * 24 * MS_1H
        week_pnl = sum(t.get("pnl_usd", 0) for t in arr if trade_exit_ms(t) > cutoff_7d)
        if week_pnl < -st.get("balance", INITIAL_BALANCE) * DYN_SYM_LOSS_PCT:
            st["dyn_cooldowns"][sym] = max(st["dyn_cooldowns"].get(sym, 0), ts + DYN_COOL2_DAYS * 24 * MS_1H)
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


def close_positions(st: dict, symbols: list[str]) -> None:
    positions = st.get("positions", [])
    if not positions:
        return
    still = []
    for p in positions:
        sym = p["symbol"]
        try:
            k15 = get_klines(sym, "15m", 3)
            c = k15[-1]
        except Exception as e:
            append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "close_check_error", "error": str(e)})
            still.append(p); continue
        held_h = (now_ms() - p["entry_time_ms"]) / MS_1H
        reason = None; exit_price = None
        if held_h >= GRACE_HOURS and c.low <= p["stop_loss"]:
            reason = "stop_loss"; exit_price = p["stop_loss"]
        elif c.high >= p["take_profit"]:
            reason = "take_profit"; exit_price = p["take_profit"]
        elif held_h >= MAX_HOLD_HOURS:
            reason = "max_hold"; exit_price = c.close
        if not reason:
            still.append(p); continue
        exit_price *= (1 - SLIPPAGE_RATE)
        gross = p["notional_usd"] * (exit_price - p["entry_price"]) / p["entry_price"]
        fee = p["notional_usd"] * FEE_RATE
        net = gross - fee
        st["balance"] = max(0.0, st.get("balance", INITIAL_BALANCE) + net)
        st.setdefault("cooldowns", {})[sym] = now_ms() + COOLDOWN_HOURS * MS_1H
        tr = {**p, "exit_time": iso(), "exit_time_ms": now_ms(), "exit_price": round(exit_price, 8), "exit_reason": reason,
              "pnl_usd": round(net, 4), "balance_after": round(st["balance"], 4)}
        append_jsonl(TRADES_FILE, tr)
        append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "closed", "reason": reason, "pnl_usd": round(net, 4)})
    st["positions"] = still


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
    if not prev_1h_bullish(get_klines(sym, "1h", 48), sig.time + MS_15M):
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
            "reason": "entry_window_missed", "signal_time": iso(sig.time),
            "expected_entry_time": iso(expected_entry_time), "lag_sec": round(lag_ms / 1000, 1),
        })
        return None
    all15 = get_klines(sym, "15m", 3)
    entry_candle = next((c for c in all15 if c.time == expected_entry_time), None)
    if not entry_candle:
        append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": sym, "status": "rejected", "reason": "entry_candle_unavailable"})
        return None
    entry = entry_candle.open
    return {"symbol": sym, "signal_time_ms": sig.time, "entry_time_ms": expected_entry_time,
            "entry_price_raw": entry, "sl_pct": sl_pct, "tp_pct": sl_pct * args.tp_ratio,
            "quality": q, "change_pct": round(chg * 100, 3), "rsi": round(r, 2)}


def open_position(st: dict, cand: dict, args: argparse.Namespace) -> None:
    balance = st.get("balance", INITIAL_BALANCE)
    margin = min(balance * pos_pct_for(cand["quality"]), args.margin_cap)
    margin = min(margin, balance)
    if margin <= 0:
        return
    entry = cand["entry_price_raw"] * (1 + SLIPPAGE_RATE)
    notional = margin * LEVERAGE
    fee = notional * FEE_RATE
    st["balance"] = max(0.0, balance - fee)
    pos = {"id": st.get("next_id", 1), "symbol": cand["symbol"], "entry_time": iso(),
           "entry_time_ms": cand["entry_time_ms"], "signal_time_ms": cand["signal_time_ms"],
           "entry_price": round(entry, 8), "margin_usd": round(margin, 4), "notional_usd": round(notional, 4),
           "stop_loss": round(entry * (1 - cand["sl_pct"]), 8),
           "take_profit": round(entry * (1 + cand["tp_pct"]), 8),
           "sl_pct": cand["sl_pct"], "tp_pct": cand["tp_pct"], "quality": cand["quality"],
           "change_pct": cand["change_pct"], "rsi": cand["rsi"]}
    st["next_id"] = pos["id"] + 1
    st.setdefault("positions", []).append(pos)
    st.setdefault("entries", []).append({"symbol": cand["symbol"], "time": now_ms()})
    append_jsonl(DECISIONS_FILE, {"time": iso(), "symbol": cand["symbol"], "status": "opened", "position": pos})


def run_once(args: argparse.Namespace) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    st = load_state()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or COMMON_SYMBOLS
    exclude = {s.strip().upper() for s in args.exclude.split(",") if s.strip()} if args.exclude else set(DEFAULT_EXCLUDE)
    symbols = [s for s in symbols if s not in exclude]

    close_positions(st, symbols)
    trades = load_recent_trades()
    refresh_dynamic_cooldowns(st, trades, args.dynamic)

    if now_ms() < st.get("halt_until", 0):
        append_jsonl(DECISIONS_FILE, {"time": iso(), "status": "halted", "until": iso(st["halt_until"])})
        save_state(st); return

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
            continue
        if not cand:
            continue
        open_position(st, cand, args)
        open_syms.add(sym)

    st["updated_at"] = iso()
    save_state(st)
    print(json.dumps({"time": iso(), "balance": round(st.get("balance", INITIAL_BALANCE), 4),
                      "positions": len(st.get("positions", [])), "state": str(STATE_FILE)}, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="S24-Ignition paper trader")
    p.add_argument("--once", action="store_true", help="Run one scan cycle")
    p.add_argument("--loop", action="store_true", help="Run forever")
    p.add_argument("--interval", type=int, default=60, help="Loop interval seconds")
    p.add_argument("--symbols", default="", help="Comma-separated symbols; default COMMON_SYMBOLS")
    p.add_argument("--exclude", default=",".join(sorted(DEFAULT_EXCLUDE)), help="Comma-separated fixed exclude symbols")
    p.add_argument("--threshold", type=float, default=SPIKE_THRESHOLD)
    p.add_argument("--quality", type=float, default=MIN_QUALITY)
    p.add_argument("--rsi", type=float, default=MIN_RSI)
    p.add_argument("--tp-ratio", type=float, default=TP_SL_RATIO)
    p.add_argument("--sym-cap", type=int, default=SYM_WEEKLY_CAP)
    p.add_argument("--margin-cap", type=float, default=MARGIN_CAP)
    p.add_argument("--max-entry-lag-sec", type=int, default=120, help="Reject signal if next-candle entry window is older than this")
    p.add_argument("--dynamic", action="store_true", default=True, help="Enable dynamic blacklist (default on)")
    p.add_argument("--no-dynamic", action="store_false", dest="dynamic", help="Disable dynamic blacklist")
    p.add_argument("--hour-only", action="store_true", default=True, help="Only trade :00 15m candle (default on)")
    p.add_argument("--no-hour-only", action="store_false", dest="hour_only", help="Disable hour-only filter")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.once and not args.loop:
        args.once = True
    while True:
        run_once(args)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
