#!/usr/bin/env python3
"""Strict one-year backtest for S23 Trend-v1.

Design intent:
- Capture the middle of a 2-7 day trend, not the first spike.
- Build signals only from closed candles.
- Enter on the next 1h open.
- Include explicit fees and slippage.
- Use pre-trade risk sizing, so the stop risk is capped before PnL is known.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "strategies" / "S23-trend-v1" / "data"
LOCAL_CACHE_DIR = ROOT / "data" / "trend_v1_cache"
UNIVERSAL_CACHE_DIR = ROOT / "trading-system" / "data" / "backtest_cache"
TZ_UTC8 = timezone(timedelta(hours=8))

END = datetime(2026, 5, 14, 10, 0, 0, tzinfo=TZ_UTC8)
INITIAL_BALANCE = 1000.0
LEVERAGE = 3.0
FEE_RATE = 0.0004
SLIPPAGE_RATE = 0.0005
POSITION_PCT = 0.05
MAX_POSITIONS = 3
MAX_LOSS_PER_TRADE = 30.0
MAX_HOLD_HOURS = 168
COOLDOWN_HOURS = 12

MS_1H = 60 * 60 * 1000
MS_4H = 4 * MS_1H
MS_1D = 24 * MS_1H

EMA_FAST = 9
EMA_ENTRY = 21
EMA_TREND = 55
EMA_DAILY = 20
ATR_PERIOD = 14
ADX_PERIOD = 14
RSI_PERIOD = 14

MIN_ADX_LONG = 18.0
MIN_ADX_SHORT = 20.0
MIN_ATR_PCT = 0.006
MAX_ATR_PCT = 0.045
MIN_SL_PCT = 0.025
MAX_SL_PCT = 0.08
ATR_SL_MULT = 2.2
TRAIL_ATR_MULT = 2.5
BREAKEVEN_R = 1.25

COMMON_SYMBOLS = [
    "XAGUSDT", "XAUUSDT", "LABUSDT", "SUIUSDT", "XRPUSDT", "BUSDT",
    "CRCLUSDT", "BILLUSDT", "BNBUSDT", "SNDKUSDT", "TONUSDT", "GTCUSDT",
    "1000PEPEUSDT", "SKYAIUSDT", "VVVUSDT", "SAGAUSDT", "MUUSDT",
    "ADAUSDT", "INTCUSDT", "LDOUSDT", "AVAXUSDT", "LINKUSDT",
    "PAXGUSDT", "AAVEUSDT",
]


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    symbol: str
    direction: str
    signal_type: str
    signal_time: int
    entry_time: int
    entry_open: float
    score: float
    sl_pct: float
    atr_pct: float
    adx_4h: float
    rsi_1h: float
    reason: str


@dataclass
class Position:
    id: int
    symbol: str
    direction: str
    signal_type: str
    entry_time: int
    entry_price: float
    margin_usd: float
    notional_usd: float
    stop_loss: float
    initial_stop_loss: float
    initial_risk_pct: float
    best_price: float
    score: float
    reason: str
    breakeven_armed: bool = False


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def text(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, TZ_UTC8).strftime("%Y-%m-%d %H:%M:%S")


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ_UTC8)
    return parsed


def api_get(endpoint: str, params: dict[str, Any]) -> Any:
    LOCAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()
    path = LOCAL_CACHE_DIR / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())

    url = "https://fapi.binance.com" + endpoint
    full_url = f"{url}?{urlencode(params)}"
    last_error: Exception | None = None
    for attempt in range(6):
        try:
            with urlopen(full_url, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            path.write_text(json.dumps(data, ensure_ascii=False))
            return data
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"API failed {endpoint} {params}: {last_error}")


def from_any_cache(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Candle] | None:
    candidates = sorted(UNIVERSAL_CACHE_DIR.glob(f"klines_{symbol}_{interval}_*.json"))
    best: list[Candle] | None = None
    best_end = 0
    for path in candidates:
        try:
            rows = json.loads(path.read_text())
        except Exception:
            continue
        candles = [Candle(**row) for row in rows if start_ms <= int(row["time"]) <= end_ms]
        if candles and candles[-1].time > best_end:
            best = candles
            best_end = candles[-1].time
    return best


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Candle]:
    cached = from_any_cache(symbol, interval, start_ms, end_ms)
    rows: list[Candle] = cached[:] if cached else []
    cur = start_ms if not rows else rows[-1].time + 1

    while cur < end_ms:
        data = api_get("/fapi/v1/klines", {
            "symbol": symbol,
            "interval": interval,
            "startTime": cur,
            "endTime": end_ms,
            "limit": 1500,
        })
        if not data:
            break
        rows.extend([
            Candle(
                time=int(k[0]),
                open=float(k[1]),
                high=float(k[2]),
                low=float(k[3]),
                close=float(k[4]),
                volume=float(k[7]),
            )
            for k in data
        ])
        cur = int(data[-1][0]) + 1
        if len(data) < 1500:
            break
        time.sleep(0.04)

    unique = {c.time: c for c in rows if start_ms <= c.time <= end_ms}
    return [unique[t] for t in sorted(unique)]


def ema_series(values: list[float], period: int) -> list[float | None]:
    if len(values) < period:
        return [None] * len(values)
    out: list[float | None] = [None] * (period - 1)
    current = sum(values[:period]) / period
    out.append(current)
    k = 2 / (period + 1)
    for value in values[period:]:
        current = value * k + current * (1 - k)
        out.append(current)
    return out


def rsi_series(closes: list[float], period: int = RSI_PERIOD) -> list[float]:
    out = [50.0] * len(closes)
    if len(closes) < period + 1:
        return out
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
        if i >= period:
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            if avg_loss == 0:
                out[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                out[i] = 100 - 100 / (1 + rs)
    return out


def atr_series(candles: list[Candle], period: int = ATR_PERIOD) -> list[float]:
    out = [0.0] * len(candles)
    trs = [0.0]
    for i in range(1, len(candles)):
        cur = candles[i]
        prev = candles[i - 1]
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
        if i >= period:
            atr = sum(trs[i - period + 1:i + 1]) / period
            out[i] = atr / cur.close if cur.close > 0 else 0.0
    return out


def adx_series(candles: list[Candle], period: int = ADX_PERIOD) -> list[float]:
    out = [0.0] * len(candles)
    if len(candles) < period * 2:
        return out

    trs = [0.0]
    pdm = [0.0]
    ndm = [0.0]
    for i in range(1, len(candles)):
        cur = candles[i]
        prev = candles[i - 1]
        up = cur.high - prev.high
        down = prev.low - cur.low
        pdm.append(up if up > down and up > 0 else 0.0)
        ndm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))

    dx = [0.0] * len(candles)
    for i in range(period, len(candles)):
        tr = sum(trs[i - period + 1:i + 1])
        if tr <= 0:
            continue
        pdi = 100 * sum(pdm[i - period + 1:i + 1]) / tr
        ndi = 100 * sum(ndm[i - period + 1:i + 1]) / tr
        if pdi + ndi > 0:
            dx[i] = 100 * abs(pdi - ndi) / (pdi + ndi)

    for i in range(period * 2, len(candles)):
        out[i] = sum(dx[i - period + 1:i + 1]) / period
    return out


def resample(candles: list[Candle], period_ms: int) -> list[Candle]:
    buckets: dict[int, list[Candle]] = {}
    for candle in candles:
        bucket = (candle.time // period_ms) * period_ms
        buckets.setdefault(bucket, []).append(candle)

    out = []
    for ts in sorted(buckets):
        rows = buckets[ts]
        out.append(Candle(
            time=ts,
            open=rows[0].open,
            high=max(c.high for c in rows),
            low=min(c.low for c in rows),
            close=rows[-1].close,
            volume=sum(c.volume for c in rows),
        ))
    return out


def build_indicators(candles: list[Candle]) -> dict[str, list[Any]]:
    closes = [c.close for c in candles]
    return {
        "ema9": ema_series(closes, EMA_FAST),
        "ema21": ema_series(closes, EMA_ENTRY),
        "ema55": ema_series(closes, EMA_TREND),
        "ema20": ema_series(closes, EMA_DAILY),
        "rsi": rsi_series(closes),
        "atr": atr_series(candles),
        "adx": adx_series(candles),
    }


def lookup_index(candles: list[Candle], ts: int) -> int:
    lo, hi = 0, len(candles) - 1
    ans = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if candles[mid].time <= ts:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def direction_pnl_pct(direction: str, entry: float, exit_price: float) -> float:
    if direction == "long":
        return (exit_price - entry) / entry
    return (entry - exit_price) / entry


def is_btc_strong_up(btc4: list[Candle], ind4: dict[str, list[Any]], ts: int) -> bool:
    idx = lookup_index(btc4, ts)
    if idx < EMA_TREND:
        return False
    e21 = ind4["ema21"][idx]
    e55 = ind4["ema55"][idx]
    return bool(e21 and e55 and e21 > e55 * 1.003 and btc4[idx].close > e21)


def is_btc_down(btc4: list[Candle], ind4: dict[str, list[Any]], ts: int) -> bool:
    idx = lookup_index(btc4, ts)
    if idx < EMA_TREND:
        return False
    e21 = ind4["ema21"][idx]
    e55 = ind4["ema55"][idx]
    return bool(e21 and e55 and e21 < e55 * 0.997 and btc4[idx].close < e21)


def build_signal(
    symbol: str,
    candles_1h: list[Candle],
    ind_1h: dict[str, list[Any]],
    candles_4h: list[Candle],
    ind_4h: dict[str, list[Any]],
    candles_1d: list[Candle],
    ind_1d: dict[str, list[Any]],
    btc4: list[Candle],
    btc4_ind: dict[str, list[Any]],
    i: int,
) -> Signal | None:
    if i + 1 >= len(candles_1h) or i < 80:
        return None

    candle = candles_1h[i]
    ts = candle.time
    idx4 = lookup_index(candles_4h, ts)
    idx1d = lookup_index(candles_1d, ts)
    if idx4 < EMA_TREND or idx1d < EMA_DAILY:
        return None

    ema9 = ind_1h["ema9"][i]
    ema21 = ind_1h["ema21"][i]
    rsi_1h = float(ind_1h["rsi"][i])
    atr_pct = float(ind_1h["atr"][i])
    e21_4 = ind_4h["ema21"][idx4]
    e55_4 = ind_4h["ema55"][idx4]
    adx_4 = float(ind_4h["adx"][idx4])
    e20_d = ind_1d["ema20"][idx1d]

    if not all([ema9, ema21, e21_4, e55_4, e20_d]):
        return None
    if atr_pct < MIN_ATR_PCT or atr_pct > MAX_ATR_PCT:
        return None

    prev_high_20 = max(c.high for c in candles_1h[max(0, i - 20):i])
    prev_low_20 = min(c.low for c in candles_1h[max(0, i - 20):i])
    recent_lows = [c.low for c in candles_1h[max(0, i - 6):i + 1]]
    recent_highs = [c.high for c in candles_1h[max(0, i - 6):i + 1]]

    sl_pct = max(MIN_SL_PCT, min(MAX_SL_PCT, atr_pct * ATR_SL_MULT))
    entry_time = candles_1h[i + 1].time
    entry_open = candles_1h[i + 1].open

    long_trend = (
        e21_4 > e55_4 * 1.003
        and candles_4h[idx4].close > e21_4
        and candles_1d[idx1d].close > e20_d
        and adx_4 >= MIN_ADX_LONG
        and not is_btc_down(btc4, btc4_ind, ts)
    )
    long_pullback = min(recent_lows) <= ema21 * 1.006 and candle.close > ema21 and candle.close > candle.open
    long_breakout = candle.close > prev_high_20 and (candle.close / ema21 - 1) < 0.08

    if long_trend and 48 <= rsi_1h <= 72 and (long_pullback or long_breakout):
        score = 5.0
        score += min(2.0, (adx_4 - MIN_ADX_LONG) / 10)
        score += 1.0 if candle.close > ema9 else 0.0
        score += 0.7 if long_breakout else 0.4
        return Signal(
            symbol=symbol,
            direction="long",
            signal_type="trend_pullback_long" if long_pullback else "trend_breakout_long",
            signal_time=ts,
            entry_time=entry_time,
            entry_open=entry_open,
            score=round(score, 3),
            sl_pct=sl_pct,
            atr_pct=atr_pct,
            adx_4h=adx_4,
            rsi_1h=rsi_1h,
            reason=f"4h uptrend, 1d above EMA20, {'pullback reclaim' if long_pullback else '20h breakout'}",
        )

    short_trend = (
        e21_4 < e55_4 * 0.997
        and candles_4h[idx4].close < e21_4
        and candles_1d[idx1d].close < e20_d
        and adx_4 >= MIN_ADX_SHORT
        and not is_btc_strong_up(btc4, btc4_ind, ts)
    )
    short_pullback = max(recent_highs) >= ema21 * 0.994 and candle.close < ema21 and candle.close < candle.open
    short_breakout = candle.close < prev_low_20 and (ema21 / candle.close - 1) < 0.08

    if short_trend and 28 <= rsi_1h <= 52 and (short_pullback or short_breakout):
        score = 5.0
        score += min(2.0, (adx_4 - MIN_ADX_SHORT) / 10)
        score += 1.0 if candle.close < ema9 else 0.0
        score += 0.7 if short_breakout else 0.4
        return Signal(
            symbol=symbol,
            direction="short",
            signal_type="trend_pullback_short" if short_pullback else "trend_breakout_short",
            signal_time=ts,
            entry_time=entry_time,
            entry_open=entry_open,
            score=round(score, 3),
            sl_pct=sl_pct,
            atr_pct=atr_pct,
            adx_4h=adx_4,
            rsi_1h=rsi_1h,
            reason=f"4h downtrend, 1d below EMA20, {'pullback reject' if short_pullback else '20h breakdown'}",
        )

    return None


def close_position(
    ts: int,
    pos: Position,
    exit_price: float,
    reason: str,
    balance: float,
) -> tuple[dict[str, Any], float]:
    pnl_pct = direction_pnl_pct(pos.direction, pos.entry_price, exit_price)
    gross = pos.notional_usd * pnl_pct
    exit_fee = pos.notional_usd * FEE_RATE
    net = gross - exit_fee
    balance += net
    trade = {
        "id": pos.id,
        "symbol": pos.symbol,
        "direction": pos.direction,
        "signal_type": pos.signal_type,
        "entry_time": text(pos.entry_time),
        "exit_time": text(ts),
        "entry_price": round(pos.entry_price, 8),
        "exit_price": round(exit_price, 8),
        "margin_usd": round(pos.margin_usd, 4),
        "notional_usd": round(pos.notional_usd, 4),
        "pnl_usd": round(net, 4),
        "pnl_pct_on_margin": round(net / pos.margin_usd * 100 if pos.margin_usd else 0, 4),
        "exit_reason": reason,
        "score": pos.score,
        "reason": pos.reason,
    }
    return trade, balance


def add_bucket(buckets: dict[str, dict[str, Any]], key: str, pnl: float) -> None:
    bucket = buckets.setdefault(key, {"trades": 0, "wins": 0, "pnl_usd": 0.0})
    bucket["trades"] += 1
    bucket["wins"] += 1 if pnl > 0 else 0
    bucket["pnl_usd"] += pnl


def finish_buckets(buckets: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for key, bucket in buckets.items():
        trades = bucket["trades"]
        out[key] = {
            "trades": trades,
            "wins": bucket["wins"],
            "win_rate_pct": round(bucket["wins"] / trades * 100, 2) if trades else 0,
            "pnl_usd": round(bucket["pnl_usd"], 4),
        }
    return dict(sorted(out.items(), key=lambda item: item[1]["pnl_usd"], reverse=True))


def max_drawdown(equity_values: list[float]) -> float:
    peak = equity_values[0] if equity_values else 0.0
    worst = 0.0
    for value in equity_values:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def monthly_stats(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for trade in trades:
        month = trade["exit_time"][:7]
        add_bucket(buckets, month, float(trade["pnl_usd"]))
    return finish_buckets(buckets)


def summarize(
    trades: list[dict[str, Any]],
    equity_curve: list[dict[str, Any]],
    initial_balance: float,
    final_balance: float,
    days: int,
    symbols: list[str],
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    gross_profit = sum(t["pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["pnl_usd"] for t in losses))
    max_dd = max_drawdown([p["equity"] for p in equity_curve])
    return {
        "strategy": "S23-trend-v1",
        "variant": "strict_trend_middle",
        "start": text(start_ms),
        "end": text(end_ms),
        "days": days,
        "symbols": symbols,
        "initial_balance": round(initial_balance, 4),
        "final_balance": round(final_balance, 4),
        "pnl_usd": round(final_balance - initial_balance, 4),
        "roi_pct": round((final_balance / initial_balance - 1) * 100, 4),
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "max_drawdown_pct": round(max_dd * 100, 4),
        "avg_trade_usd": round(sum(t["pnl_usd"] for t in trades) / len(trades), 4) if trades else 0.0,
        "max_single_win": round(max((t["pnl_usd"] for t in trades), default=0), 4),
        "max_single_loss": round(min((t["pnl_usd"] for t in trades), default=0), 4),
    }


def run_backtest(args: argparse.Namespace) -> dict[str, Any]:
    end_dt = parse_time(args.end) if args.end else END
    start_dt = end_dt - timedelta(days=args.days)
    warmup_dt = start_dt - timedelta(days=args.warmup_days)
    start_ms = ms(start_dt)
    warmup_ms = ms(warmup_dt)
    end_ms = ms(end_dt)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    print(f"Window: {text(start_ms)} -> {text(end_ms)} | warmup from {text(warmup_ms)}")
    print(f"Symbols: {len(symbols)}")

    candles_1h_by_symbol: dict[str, list[Candle]] = {}
    candles_4h_by_symbol: dict[str, list[Candle]] = {}
    candles_1d_by_symbol: dict[str, list[Candle]] = {}
    ind_1h_by_symbol: dict[str, dict[str, list[Any]]] = {}
    ind_4h_by_symbol: dict[str, dict[str, list[Any]]] = {}
    ind_1d_by_symbol: dict[str, dict[str, list[Any]]] = {}

    btc_1h = fetch_klines("BTCUSDT", "1h", warmup_ms, end_ms)
    btc_4h = resample(btc_1h, MS_4H)
    btc_4h_ind = build_indicators(btc_4h)

    for symbol in symbols:
        klines = fetch_klines(symbol, "1h", warmup_ms, end_ms)
        if len(klines) < 300:
            print(f"skip {symbol}: insufficient 1h candles={len(klines)}")
            continue
        candles_1h_by_symbol[symbol] = klines
        candles_4h_by_symbol[symbol] = resample(klines, MS_4H)
        candles_1d_by_symbol[symbol] = resample(klines, MS_1D)
        ind_1h_by_symbol[symbol] = build_indicators(candles_1h_by_symbol[symbol])
        ind_4h_by_symbol[symbol] = build_indicators(candles_4h_by_symbol[symbol])
        ind_1d_by_symbol[symbol] = build_indicators(candles_1d_by_symbol[symbol])
        print(f"loaded {symbol}: 1h={len(klines)}")

    events: dict[int, list[Signal]] = {}
    for symbol, candles in candles_1h_by_symbol.items():
        for i, candle in enumerate(candles):
            if candle.time < start_ms or candle.time > end_ms:
                continue
            signal = build_signal(
                symbol,
                candles,
                ind_1h_by_symbol[symbol],
                candles_4h_by_symbol[symbol],
                ind_4h_by_symbol[symbol],
                candles_1d_by_symbol[symbol],
                ind_1d_by_symbol[symbol],
                btc_4h,
                btc_4h_ind,
                i,
            )
            if signal and start_ms <= signal.entry_time <= end_ms:
                events.setdefault(signal.entry_time, []).append(signal)

    candle_by_time = {
        symbol: {c.time: c for c in candles}
        for symbol, candles in candles_1h_by_symbol.items()
    }
    all_times = sorted({c.time for rows in candles_1h_by_symbol.values() for c in rows if start_ms <= c.time <= end_ms})
    balance = INITIAL_BALANCE
    open_positions: list[Position] = []
    trades: list[dict[str, Any]] = []
    equity_curve = [{"time": text(start_ms), "equity": round(balance, 6)}]
    cooldown_until: dict[str, int] = {}
    next_id = 1

    for ts in all_times:
        still_open = []
        for pos in open_positions:
            candle = candle_by_time.get(pos.symbol, {}).get(ts)
            if not candle:
                still_open.append(pos)
                continue

            exit_price = None
            exit_reason = None
            held_hours = (ts - pos.entry_time) / MS_1H

            if pos.direction == "long":
                pos.best_price = max(pos.best_price, candle.high)
                gain_pct = (pos.best_price - pos.entry_price) / pos.entry_price
                if gain_pct >= pos.initial_risk_pct * BREAKEVEN_R:
                    pos.breakeven_armed = True
                    pos.stop_loss = max(pos.stop_loss, pos.entry_price * 1.001)
                trail = pos.best_price * (1 - pos.initial_risk_pct * TRAIL_ATR_MULT / ATR_SL_MULT)
                if pos.breakeven_armed:
                    pos.stop_loss = max(pos.stop_loss, trail)
                if candle.low <= pos.stop_loss:
                    exit_price = pos.stop_loss * (1 - SLIPPAGE_RATE)
                    exit_reason = "stop_or_trailing"
            else:
                pos.best_price = min(pos.best_price, candle.low)
                gain_pct = (pos.entry_price - pos.best_price) / pos.entry_price
                if gain_pct >= pos.initial_risk_pct * BREAKEVEN_R:
                    pos.breakeven_armed = True
                    pos.stop_loss = min(pos.stop_loss, pos.entry_price * 0.999)
                trail = pos.best_price * (1 + pos.initial_risk_pct * TRAIL_ATR_MULT / ATR_SL_MULT)
                if pos.breakeven_armed:
                    pos.stop_loss = min(pos.stop_loss, trail)
                if candle.high >= pos.stop_loss:
                    exit_price = pos.stop_loss * (1 + SLIPPAGE_RATE)
                    exit_reason = "stop_or_trailing"

            if exit_price is None and held_hours >= args.max_hold_hours:
                exit_price = candle.close * (1 - SLIPPAGE_RATE if pos.direction == "long" else 1 + SLIPPAGE_RATE)
                exit_reason = "max_hold"

            if exit_price is None:
                still_open.append(pos)
                continue

            trade, balance = close_position(ts, pos, exit_price, exit_reason or "exit", balance)
            trades.append(trade)
            cooldown_until[pos.symbol] = ts + int(COOLDOWN_HOURS * MS_1H)
        open_positions = still_open

        floating = 0.0
        for pos in open_positions:
            candle = candle_by_time.get(pos.symbol, {}).get(ts)
            if candle:
                floating += pos.notional_usd * direction_pnl_pct(pos.direction, pos.entry_price, candle.close)
        equity_curve.append({"time": text(ts), "equity": round(balance + floating, 6)})

        if not events.get(ts):
            continue

        open_symbols = {p.symbol for p in open_positions}
        ranked = sorted(events[ts], key=lambda s: s.score, reverse=True)
        for signal in ranked:
            if len(open_positions) >= MAX_POSITIONS:
                break
            if signal.symbol in open_symbols or cooldown_until.get(signal.symbol, 0) > ts:
                continue
            margin = balance * args.position_pct
            loss_at_stop = margin * LEVERAGE * signal.sl_pct
            if loss_at_stop > args.max_loss_per_trade:
                margin = args.max_loss_per_trade / (LEVERAGE * signal.sl_pct)
            if margin <= 0:
                continue
            entry_price = signal.entry_open * (1 + SLIPPAGE_RATE if signal.direction == "long" else 1 - SLIPPAGE_RATE)
            notional = margin * LEVERAGE
            entry_fee = notional * FEE_RATE
            if balance - entry_fee <= 0:
                continue
            balance -= entry_fee
            stop_loss = entry_price * (1 - signal.sl_pct if signal.direction == "long" else 1 + signal.sl_pct)
            open_positions.append(Position(
                id=next_id,
                symbol=signal.symbol,
                direction=signal.direction,
                signal_type=signal.signal_type,
                entry_time=ts,
                entry_price=entry_price,
                margin_usd=margin,
                notional_usd=notional,
                stop_loss=stop_loss,
                initial_stop_loss=stop_loss,
                initial_risk_pct=signal.sl_pct,
                best_price=entry_price,
                score=signal.score,
                reason=signal.reason,
            ))
            open_symbols.add(signal.symbol)
            next_id += 1

    if open_positions:
        for pos in open_positions:
            candle = candles_1h_by_symbol[pos.symbol][-1]
            exit_price = candle.close * (1 - SLIPPAGE_RATE if pos.direction == "long" else 1 + SLIPPAGE_RATE)
            trade, balance = close_position(end_ms, pos, exit_price, "end_of_backtest", balance)
            trades.append(trade)
        equity_curve.append({"time": text(end_ms), "equity": round(balance, 6)})

    by_symbol: dict[str, dict[str, Any]] = {}
    by_signal_type: dict[str, dict[str, Any]] = {}
    for trade in trades:
        add_bucket(by_symbol, trade["symbol"], float(trade["pnl_usd"]))
        add_bucket(by_signal_type, trade["signal_type"], float(trade["pnl_usd"]))

    summary = summarize(trades, equity_curve, INITIAL_BALANCE, balance, args.days, list(candles_1h_by_symbol), start_ms, end_ms)
    return {
        "summary": summary,
        "monthly": monthly_stats(trades),
        "by_symbol": finish_buckets(by_symbol),
        "by_signal_type": finish_buckets(by_signal_type),
        "equity_curve": equity_curve,
        "trades": trades,
        "config": {
            "initial_balance": INITIAL_BALANCE,
            "leverage": LEVERAGE,
            "fee_rate": FEE_RATE,
            "slippage_rate": SLIPPAGE_RATE,
            "position_pct": args.position_pct,
            "max_loss_per_trade": args.max_loss_per_trade,
            "max_positions": MAX_POSITIONS,
            "max_hold_hours": args.max_hold_hours,
            "cooldown_hours": COOLDOWN_HOURS,
            "risk": {
                "min_sl_pct": MIN_SL_PCT,
                "max_sl_pct": MAX_SL_PCT,
                "atr_sl_mult": ATR_SL_MULT,
                "breakeven_r": BREAKEVEN_R,
                "trail_atr_mult": TRAIL_ATR_MULT,
            },
            "filters": {
                "min_adx_long": MIN_ADX_LONG,
                "min_adx_short": MIN_ADX_SHORT,
                "min_atr_pct": MIN_ATR_PCT,
                "max_atr_pct": MAX_ATR_PCT,
                "btc_macro_filter": True,
            },
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run S23 Trend-v1 strict backtest.")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--warmup-days", type=int, default=160)
    parser.add_argument("--end", default=END.isoformat())
    parser.add_argument("--symbols", default=",".join(COMMON_SYMBOLS))
    parser.add_argument("--position-pct", type=float, default=POSITION_PCT)
    parser.add_argument("--max-loss-per-trade", type=float, default=MAX_LOSS_PER_TRADE)
    parser.add_argument("--max-hold-hours", type=float, default=MAX_HOLD_HOURS)
    parser.add_argument("--label", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = run_backtest(args)
    summary = report["summary"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    suffix = f"_{args.label}" if args.label else ""
    out = OUT_DIR / f"strict_backtest_trend_v1_{args.days}d{suffix}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
