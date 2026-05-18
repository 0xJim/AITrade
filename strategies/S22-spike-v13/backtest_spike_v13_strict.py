#!/usr/bin/env python3
"""Strict Spike v13 backtest under the AITrade common one-year basis.

Fixes the optimistic assumptions in backtest_spike_v13.py:
- fixed historical window, not "now minus 180 days"
- common symbol pool from the final true all-strategy rerun
- signal candle must be closed, entry happens on the next 15m open
- no same-day BTC lookahead in the signal-quality macro score
- explicit fee and slippage
- conservative 10% margin sizing, with the v13 quality boost capped at 1.3x
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "strategies" / "S22-spike-v13" / "data"
CACHE_DIR = ROOT / "data" / "strict_spike_v13_cache"
TZ_UTC8 = timezone(timedelta(hours=8))

END = datetime(2026, 5, 14, 10, 0, 0, tzinfo=TZ_UTC8)
INITIAL_BALANCE = 1000.0
LEVERAGE = 3.0
FEE_RATE = 0.0004
SLIPPAGE_RATE = 0.0005
MAX_POSITIONS = 3
POSITION_PCT = 0.10
QUALITY_BOOST_THRESHOLD = 80.0
QUALITY_BOOST_MULT = 1.3
COOLDOWN_HOURS = 4
SPIKE_COOLDOWN_HOURS = 1
GRACE_HOURS = 4
MAX_HOLD_HOURS = 8
RUNNER_ENABLED = False
RUNNER_MAX_HOLD_HOURS = 24.0
RUNNER_MIN_QUALITY = 80.0
RUNNER_MIN_PNL_PCT = 0.018
RUNNER_MA_CANDLES = 8
RUNNER_MAX_GIVEBACK_PCT = 0.012
RUNNER_TRAILING_PCT = 0.025
RUNNER_BREAKEVEN_BUFFER_PCT = 0.002
RUNNER_REMAINING_PCT = 0.30
EARLY_DOWNTREND_EXIT = False
EARLY_DOWNTREND_MIN_HOURS = 1.0
ALLOW_SHORTS = False

SPIKE_THRESHOLD = 0.01
SPIKE_VOLUME_RATIO_MIN = 0.0
SPIKE_BODY_RATIO_MIN = 0.0
SPIKE_CLOSE_POSITION_MIN = 0.0
SPIKE_MIN_ATR = 0.005
SPIKE_MIN_RSI = 50.0
SPIKE_MIN_QUALITY = 70.0
ATR_PERIOD = 14
RSI_PERIOD = 14
EMA_FAST = 9
EMA_SLOW = 21
ATR_SL_MULT = 1.5
MIN_SL_PCT = 0.03
TP_SL_RATIO = 2.5

MS_15M = 15 * 60 * 1000
MS_1H = 60 * 60 * 1000

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
    signal_time: int
    entry_time: int
    entry_open: float
    direction: str
    chg_pct: float
    atr_pct: float
    rsi_15m: float
    quality: float
    sl_pct: float
    tp_pct: float
    ema_bullish: bool


@dataclass
class Position:
    id: int
    symbol: str
    direction: str
    entry_time: int
    entry_price: float
    margin_usd: float
    notional_usd: float
    stop_loss: float
    take_profit: float
    quality: float
    sl_pct: float
    tp_pct: float
    runner_qualified: bool = False
    best_price: float = 0.0


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def text(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000, TZ_UTC8).strftime("%Y-%m-%d %H:%M:%S")


def api_get(endpoint: str, params: dict[str, Any]) -> Any:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())

    url = "https://fapi.binance.com" + endpoint
    query = urlencode(params)
    full_url = f"{url}?{query}" if query else url
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


def fetch_klines(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Candle]:
    rows: list[list[Any]] = []
    cur = start_ms
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
        rows.extend(data)
        cur = int(data[-1][0]) + 1
        if len(data) < 1500:
            break
        time.sleep(0.03)
    return [
        Candle(
            time=int(k[0]),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            volume=float(k[7]),
        )
        for k in rows
    ]


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


def rsi(closes: list[float], period: int = RSI_PERIOD) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def atr_pct(candles: list[Candle], period: int = ATR_PERIOD) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(max(1, len(candles) - period), len(candles)):
        cur = candles[i]
        prev = candles[i - 1]
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
    atr = sum(trs) / len(trs)
    return atr / candles[-1].close if candles[-1].close > 0 else 0.0


def build_ema_lookup(klines_1h: list[Candle]) -> tuple[list[int], list[bool]]:
    closes = [k.close for k in klines_1h]
    e9 = ema_series(closes, EMA_FAST)
    e21 = ema_series(closes, EMA_SLOW)
    close_times: list[int] = []
    bullish: list[bool] = []
    for idx, candle in enumerate(klines_1h):
        close_times.append(candle.time + MS_1H)
        bullish.append(bool(e9[idx] is not None and e21[idx] is not None and e9[idx] > e21[idx]))
    return close_times, bullish


def latest_bool(times: list[int], values: list[bool], ts: int) -> bool:
    idx = bisect.bisect_right(times, ts) - 1
    return values[idx] if idx >= 0 else False


def btc_bullish_at(btc_times: list[int], btc_closes: list[float], ts: int) -> bool:
    idx = bisect.bisect_right(btc_times, ts) - 1
    prev = bisect.bisect_right(btc_times, ts - 24 * MS_1H) - 1
    if idx < 0 or prev < 0:
        return True
    return btc_closes[idx] >= btc_closes[prev]


def signal_quality(chg_pct: float, atr: float, history: list[Candle], current_vol: float, btc_bullish: bool) -> float:
    score = 0.0
    if atr > 0:
        score += min((abs(chg_pct) * 1000 / atr) / 1000.0, 1.0) * 40

    consecutive = 0
    for candle in reversed(history):
        bullish = candle.close > candle.open
        if (chg_pct > 0 and bullish) or (chg_pct < 0 and not bullish):
            consecutive += 1
        else:
            break
    score += min(consecutive / 5.0, 1.0) * 30

    if len(history) >= 20:
        vol_ma = sum(k.volume for k in history[-20:]) / 20
    elif history:
        vol_ma = sum(k.volume for k in history) / len(history)
    else:
        vol_ma = 0
    if vol_ma > 0:
        score += min((current_vol / vol_ma) / 5.0, 1.0) * 20

    if (btc_bullish and chg_pct > 0) or ((not btc_bullish) and chg_pct < 0):
        score += 10
    return round(score, 1)


def spike_shape_metrics(candle: Candle, history: list[Candle]) -> dict[str, float]:
    candle_range = candle.high - candle.low
    body = abs(candle.close - candle.open)
    body_ratio = body / candle_range if candle_range > 0 else 0.0
    if candle.close >= candle.open:
        close_position = (candle.close - candle.low) / candle_range if candle_range > 0 else 0.0
    else:
        close_position = (candle.high - candle.close) / candle_range if candle_range > 0 else 0.0

    recent = history[-20:] if len(history) >= 20 else history
    avg_volume = sum(k.volume for k in recent) / len(recent) if recent else 0.0
    volume_ratio = candle.volume / avg_volume if avg_volume > 0 else 0.0
    return {
        "body_ratio": body_ratio,
        "close_position": close_position,
        "volume_ratio": volume_ratio,
    }


def spike_shape_passes(candle: Candle, history: list[Candle]) -> bool:
    metrics = spike_shape_metrics(candle, history)
    if SPIKE_BODY_RATIO_MIN > 0 and metrics["body_ratio"] < SPIKE_BODY_RATIO_MIN:
        return False
    if SPIKE_CLOSE_POSITION_MIN > 0 and metrics["close_position"] < SPIKE_CLOSE_POSITION_MIN:
        return False
    if SPIKE_VOLUME_RATIO_MIN > 0 and metrics["volume_ratio"] < SPIKE_VOLUME_RATIO_MIN:
        return False
    return True


def signal_direction(chg: float) -> str | None:
    if chg >= SPIKE_THRESHOLD:
        return "long"
    if ALLOW_SHORTS and chg <= -SPIKE_THRESHOLD:
        return "short"
    return None


def generate_signals(
    symbols: list[str],
    data_15m: dict[str, list[Candle]],
    ema_lookup: dict[str, tuple[list[int], list[bool]]],
    btc_times: list[int],
    btc_closes: list[float],
    start_ms: int,
    end_ms: int,
) -> list[Signal]:
    signals: list[Signal] = []
    for symbol in symbols:
        klines = data_15m.get(symbol, [])
        if len(klines) < 80:
            continue
        ema_times, ema_values = ema_lookup[symbol]
        closes: list[float] = []
        for i, candle in enumerate(klines[:-1]):
            closes.append(candle.close)
            signal_close_time = candle.time + MS_15M
            entry = klines[i + 1]
            if signal_close_time < start_ms or entry.time > end_ms:
                continue
            if i < max(ATR_PERIOD + 1, RSI_PERIOD + 1, 30):
                continue
            if candle.open <= 0:
                continue
            chg = (candle.close - candle.open) / candle.open
            direction = signal_direction(chg)
            if direction is None:
                continue
            if not spike_shape_passes(candle, klines[:i]):
                continue
            atr = atr_pct(klines[:i + 1], ATR_PERIOD)
            if atr < SPIKE_MIN_ATR:
                continue
            ema_bullish = latest_bool(ema_times, ema_values, signal_close_time)
            if direction == "long" and not ema_bullish:
                continue
            if direction == "short" and ema_bullish:
                continue
            rsi_15m = rsi(closes, RSI_PERIOD)
            if direction == "long" and rsi_15m < SPIKE_MIN_RSI:
                continue
            if direction == "short" and rsi_15m > 100 - SPIKE_MIN_RSI:
                continue
            quality = signal_quality(
                chg,
                atr,
                klines[:i],
                candle.volume,
                btc_bullish_at(btc_times, btc_closes, signal_close_time),
            )
            if quality < SPIKE_MIN_QUALITY:
                continue
            sl_pct = max(atr * ATR_SL_MULT, MIN_SL_PCT)
            signals.append(Signal(
                symbol=symbol,
                signal_time=signal_close_time,
                entry_time=entry.time,
                entry_open=entry.open,
                direction=direction,
                chg_pct=chg,
                atr_pct=atr,
                rsi_15m=rsi_15m,
                quality=quality,
                sl_pct=sl_pct,
                tp_pct=sl_pct * TP_SL_RATIO,
                ema_bullish=ema_bullish,
            ))
    return sorted(signals, key=lambda s: s.entry_time)


def realize_trade(
    pos: Position,
    ts: int,
    exit_price: float,
    reason: str,
    balance: float,
    notional_usd: float,
    margin_usd: float,
) -> tuple[dict[str, Any], float]:
    direction_mult = 1 if pos.direction == "long" else -1
    gross = notional_usd * direction_mult * ((exit_price - pos.entry_price) / pos.entry_price)
    exit_fee = notional_usd * FEE_RATE
    net = gross - exit_fee
    balance += net
    trade = {
        "id": pos.id,
        "symbol": pos.symbol,
        "direction": pos.direction,
        "entry_time": text(pos.entry_time),
        "exit_time": text(ts),
        "entry_price": round(pos.entry_price, 8),
        "exit_price": round(exit_price, 8),
        "exit_reason": reason,
        "margin_usd": round(margin_usd, 4),
        "notional_usd": round(notional_usd, 4),
        "quality": pos.quality,
        "sl_pct": round(pos.sl_pct, 6),
        "tp_pct": round(pos.tp_pct, 6),
        "runner_qualified": pos.runner_qualified,
        "gross_pnl_usd": round(gross, 6),
        "fee_usd": round(exit_fee, 6),
        "pnl_usd": round(net, 6),
    }
    return trade, balance


def close_trade(pos: Position, ts: int, exit_price: float, reason: str, balance: float) -> tuple[dict[str, Any], float]:
    return realize_trade(pos, ts, exit_price, reason, balance, pos.notional_usd, pos.margin_usd)


def moving_average_close(candles: list[Candle], idx: int, lookback: int) -> float | None:
    if lookback <= 0 or idx + 1 < lookback:
        return None
    return sum(k.close for k in candles[idx + 1 - lookback:idx + 1]) / lookback


def lower_close_streak(candles: list[Candle], idx: int, count: int = 3) -> bool:
    if idx < count:
        return False
    recent = candles[idx - count:idx + 1]
    return all(recent[i].close < recent[i - 1].close for i in range(1, len(recent)))


def runner_filter_passes(pos: Position, candles: list[Candle], idx: int) -> bool:
    if pos.runner_qualified:
        return True
    if pos.quality < RUNNER_MIN_QUALITY:
        return False

    candle = candles[idx]
    direction_mult = 1 if pos.direction == "long" else -1
    pnl_pct = direction_mult * ((candle.close - pos.entry_price) / pos.entry_price)
    if pnl_pct < RUNNER_MIN_PNL_PCT:
        return False

    close_ma = moving_average_close(candles, idx, int(RUNNER_MA_CANDLES))
    if close_ma is None or candle.close < close_ma:
        return False

    if pos.direction == "long":
        best_price = max(pos.best_price or pos.entry_price, candle.high)
        gave_back = best_price > 0 and (best_price - candle.close) / best_price > RUNNER_MAX_GIVEBACK_PCT
    else:
        best_price = min(pos.best_price or pos.entry_price, candle.low)
        gave_back = best_price > 0 and (candle.close - best_price) / best_price > RUNNER_MAX_GIVEBACK_PCT
    if gave_back:
        return False

    return True


def has_downtrend_exit(pos: Position, candles: list[Candle], idx: int) -> bool:
    if pos.direction != "long":
        return False
    candle = candles[idx]
    close_ma = moving_average_close(candles, idx, int(RUNNER_MA_CANDLES))
    if close_ma is None:
        return False
    gave_back = pos.best_price > 0 and (pos.best_price - candle.close) / pos.best_price > RUNNER_MAX_GIVEBACK_PCT
    broke_ma = candle.close < close_ma
    lower_streak = lower_close_streak(candles, idx, 3)
    return (gave_back and broke_ma) or (broke_ma and lower_streak and candle.close < pos.entry_price)


def run_simulation(signals: list[Signal], data_15m: dict[str, list[Candle]], mode: str) -> dict[str, Any]:
    fixed_base = mode == "fixed_1000u"
    balance = INITIAL_BALANCE
    peak = INITIAL_BALANCE
    max_dd = 0.0
    positions: list[Position] = []
    trades: list[dict[str, Any]] = []
    cooldown_until: dict[str, int] = {}
    spike_until: dict[str, int] = {}
    next_id = 1

    signal_idx = 0
    signals_by_ts: dict[int, list[Signal]] = defaultdict(list)
    for signal in signals:
        signals_by_ts[signal.entry_time].append(signal)
    all_times = sorted({k.time for rows in data_15m.values() for k in rows})
    candle_map = {symbol: {k.time: k for k in rows} for symbol, rows in data_15m.items()}
    candle_index_map = {symbol: {k.time: idx for idx, k in enumerate(rows)} for symbol, rows in data_15m.items()}

    for ts in all_times:
        # 1. Close existing positions on this completed bar.
        still_open: list[Position] = []
        for pos in positions:
            candle = candle_map.get(pos.symbol, {}).get(ts)
            if not candle:
                still_open.append(pos)
                continue
            held_hours = (ts - pos.entry_time) / MS_1H
            if pos.direction == "long":
                pos.best_price = max(pos.best_price or pos.entry_price, candle.high)
                stop_hit = candle.low <= pos.stop_loss
                take_profit_hit = candle.high >= pos.take_profit
                stop_exit_price = pos.stop_loss * (1 - SLIPPAGE_RATE)
                take_profit_exit_price = pos.take_profit * (1 - SLIPPAGE_RATE)
                max_hold_exit_price = candle.close * (1 - SLIPPAGE_RATE)
            else:
                pos.best_price = min(pos.best_price or pos.entry_price, candle.low)
                stop_hit = candle.high >= pos.stop_loss
                take_profit_hit = candle.low <= pos.take_profit
                stop_exit_price = pos.stop_loss * (1 + SLIPPAGE_RATE)
                take_profit_exit_price = pos.take_profit * (1 + SLIPPAGE_RATE)
                max_hold_exit_price = candle.close * (1 + SLIPPAGE_RATE)
            candles = data_15m.get(pos.symbol, [])
            idx = candle_index_map.get(pos.symbol, {}).get(ts)
            exit_price = None
            reason = None
            if held_hours >= GRACE_HOURS and stop_hit:
                exit_price = stop_exit_price
                reason = "stop_loss"
            elif RUNNER_ENABLED and pos.runner_qualified and idx is not None and has_downtrend_exit(pos, candles, idx):
                exit_price = candle.close * (1 - SLIPPAGE_RATE)
                reason = "runner_downtrend_exit"
            elif (
                EARLY_DOWNTREND_EXIT
                and not pos.runner_qualified
                and held_hours >= EARLY_DOWNTREND_MIN_HOURS
                and idx is not None
                and has_downtrend_exit(pos, candles, idx)
            ):
                exit_price = candle.close * (1 - SLIPPAGE_RATE)
                reason = "early_downtrend_exit"
            elif take_profit_hit and not (RUNNER_ENABLED and pos.quality >= RUNNER_MIN_QUALITY):
                exit_price = take_profit_exit_price
                reason = "take_profit"
            elif RUNNER_ENABLED and take_profit_hit and pos.quality >= RUNNER_MIN_QUALITY and not pos.runner_qualified:
                close_pct = max(0.0, min(1.0, 1.0 - RUNNER_REMAINING_PCT))
                if close_pct > 0:
                    partial_notional = pos.notional_usd * close_pct
                    partial_margin = pos.margin_usd * close_pct
                    trade, balance = realize_trade(
                        pos,
                        ts,
                        take_profit_exit_price,
                        "partial_take_profit",
                        balance,
                        partial_notional,
                        partial_margin,
                    )
                    trades.append(trade)
                    pos.notional_usd -= partial_notional
                    pos.margin_usd -= partial_margin
                pos.runner_qualified = True
                if pos.direction == "long":
                    pos.stop_loss = max(
                        pos.stop_loss,
                        pos.entry_price * (1 + RUNNER_BREAKEVEN_BUFFER_PCT),
                        pos.best_price * (1 - RUNNER_TRAILING_PCT),
                    )
                else:
                    pos.stop_loss = min(
                        pos.stop_loss,
                        pos.entry_price * (1 - RUNNER_BREAKEVEN_BUFFER_PCT),
                        pos.best_price * (1 + RUNNER_TRAILING_PCT),
                    )
            elif held_hours >= MAX_HOLD_HOURS:
                if RUNNER_ENABLED and idx is not None and runner_filter_passes(pos, candles, idx):
                    pos.runner_qualified = True
                    if pos.direction == "long":
                        pos.stop_loss = max(
                            pos.stop_loss,
                            pos.entry_price * (1 + RUNNER_BREAKEVEN_BUFFER_PCT),
                            pos.best_price * (1 - RUNNER_TRAILING_PCT),
                        )
                    else:
                        pos.stop_loss = min(
                            pos.stop_loss,
                            pos.entry_price * (1 - RUNNER_BREAKEVEN_BUFFER_PCT),
                            pos.best_price * (1 + RUNNER_TRAILING_PCT),
                        )
                    if held_hours >= RUNNER_MAX_HOLD_HOURS:
                        exit_price = max_hold_exit_price
                        reason = "runner_max_hold"
                    elif stop_hit:
                        exit_price = stop_exit_price
                        reason = "runner_trailing_stop"
                else:
                    exit_price = max_hold_exit_price
                    reason = "runner_reject" if RUNNER_ENABLED else "max_hold"

            if exit_price is None:
                still_open.append(pos)
                continue
            trade, balance = close_trade(pos, ts, exit_price, reason or "exit", balance)
            trades.append(trade)
            cooldown_until[pos.symbol] = ts + int(COOLDOWN_HOURS * MS_1H)
        positions = still_open

        # 2. Open next-bar signals.
        open_symbols = {p.symbol for p in positions}
        for signal in signals_by_ts.get(ts, []):
            if len(positions) >= MAX_POSITIONS or signal.symbol in open_symbols:
                continue
            if cooldown_until.get(signal.symbol, 0) > ts or spike_until.get(signal.symbol, 0) > ts:
                continue
            base = INITIAL_BALANCE if fixed_base else max(balance, 0.0)
            margin = base * POSITION_PCT
            if signal.quality >= QUALITY_BOOST_THRESHOLD:
                margin *= QUALITY_BOOST_MULT
            margin = min(margin, balance * 0.35)
            if margin < 20:
                continue
            if signal.direction == "long":
                entry_price = signal.entry_open * (1 + SLIPPAGE_RATE)
                stop_loss = entry_price * (1 - signal.sl_pct)
                take_profit = entry_price * (1 + signal.tp_pct)
            else:
                entry_price = signal.entry_open * (1 - SLIPPAGE_RATE)
                stop_loss = entry_price * (1 + signal.sl_pct)
                take_profit = entry_price * (1 - signal.tp_pct)
            notional = margin * LEVERAGE
            entry_fee = notional * FEE_RATE
            if balance - entry_fee <= 0:
                continue
            balance -= entry_fee
            positions.append(Position(
                id=next_id,
                symbol=signal.symbol,
                direction=signal.direction,
                entry_time=ts,
                entry_price=entry_price,
                margin_usd=margin,
                notional_usd=notional,
                stop_loss=stop_loss,
                take_profit=take_profit,
                quality=signal.quality,
                sl_pct=signal.sl_pct,
                tp_pct=signal.tp_pct,
                best_price=entry_price,
            ))
            next_id += 1
            open_symbols.add(signal.symbol)
            spike_until[signal.symbol] = ts + int(SPIKE_COOLDOWN_HOURS * MS_1H)

        # 3. Mark-to-market equity.
        floating = 0.0
        for pos in positions:
            candle = candle_map.get(pos.symbol, {}).get(ts)
            if candle:
                direction_mult = 1 if pos.direction == "long" else -1
                floating += pos.notional_usd * direction_mult * ((candle.close - pos.entry_price) / pos.entry_price)
        equity = balance + floating
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100)

    # Force close at last close.
    for pos in positions:
        last = data_15m[pos.symbol][-1]
        exit_price = last.close * (1 - SLIPPAGE_RATE) if pos.direction == "long" else last.close * (1 + SLIPPAGE_RATE)
        trade, balance = close_trade(pos, last.time, exit_price, "end_of_backtest", balance)
        trades.append(trade)

    return summarize(mode, trades, balance, max_dd)


def summarize(mode: str, trades: list[dict[str, Any]], final_balance: float, max_dd: float) -> dict[str, Any]:
    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    gross_profit = sum(t["pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["pnl_usd"] for t in losses))
    monthly: dict[str, float] = defaultdict(float)
    by_symbol: dict[str, dict[str, Any]] = {}
    for trade in trades:
        month = trade["exit_time"][:7]
        monthly[month] += trade["pnl_usd"]
        row = by_symbol.setdefault(trade["symbol"], {"trades": 0, "wins": 0, "pnl_usd": 0.0})
        row["trades"] += 1
        row["wins"] += 1 if trade["pnl_usd"] > 0 else 0
        row["pnl_usd"] += trade["pnl_usd"]

    for row in by_symbol.values():
        row["win_rate_pct"] = round(row["wins"] / row["trades"] * 100, 2) if row["trades"] else 0
        row["pnl_usd"] = round(row["pnl_usd"], 4)

    pnl = final_balance - INITIAL_BALANCE
    profit_months = sum(1 for value in monthly.values() if value > 0)
    summary = {
        "mode": mode,
        "initial_balance": INITIAL_BALANCE,
        "final_balance": round(final_balance, 4),
        "pnl_usd": round(pnl, 4),
        "roi_pct": round(pnl / INITIAL_BALANCE * 100, 4),
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "max_drawdown_pct": round(max_dd, 4),
        "roi_dd_ratio": round((pnl / INITIAL_BALANCE * 100) / max_dd, 4) if max_dd else None,
        "profit_months": profit_months,
        "total_months": len(monthly),
        "monthly_win_rate_pct": round(profit_months / len(monthly) * 100, 2) if monthly else 0,
        "avg_margin_usd": round(sum(t["margin_usd"] for t in trades) / len(trades), 4) if trades else 0,
        "max_margin_usd": round(max((t["margin_usd"] for t in trades), default=0), 4),
        "max_single_win_usd": round(max((t["pnl_usd"] for t in trades), default=0), 4),
        "max_single_loss_usd": round(min((t["pnl_usd"] for t in trades), default=0), 4),
    }
    return {
        "summary": summary,
        "monthly_pnl": {k: round(v, 4) for k, v in sorted(monthly.items())},
        "by_symbol": dict(sorted(by_symbol.items(), key=lambda item: item[1]["pnl_usd"], reverse=True)),
        "trades": trades,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--symbols", default=",".join(COMMON_SYMBOLS))
    parser.add_argument("--spike-threshold", type=float, default=SPIKE_THRESHOLD)
    parser.add_argument("--volume-ratio-min", type=float, default=SPIKE_VOLUME_RATIO_MIN)
    parser.add_argument("--body-ratio-min", type=float, default=SPIKE_BODY_RATIO_MIN)
    parser.add_argument("--close-position-min", type=float, default=SPIKE_CLOSE_POSITION_MIN)
    parser.add_argument("--min-quality", type=float, default=SPIKE_MIN_QUALITY)
    parser.add_argument("--min-rsi", type=float, default=SPIKE_MIN_RSI)
    parser.add_argument("--max-hold-hours", type=float, default=MAX_HOLD_HOURS)
    parser.add_argument("--quality-boost-mult", type=float, default=QUALITY_BOOST_MULT)
    parser.add_argument("--position-pct", type=float, default=POSITION_PCT)
    parser.add_argument("--exclude-symbols", default="")
    parser.add_argument("--runner-enabled", action="store_true")
    parser.add_argument("--runner-max-hold-hours", type=float, default=RUNNER_MAX_HOLD_HOURS)
    parser.add_argument("--runner-min-quality", type=float, default=RUNNER_MIN_QUALITY)
    parser.add_argument("--runner-min-pnl-pct", type=float, default=RUNNER_MIN_PNL_PCT)
    parser.add_argument("--runner-ma-candles", type=int, default=RUNNER_MA_CANDLES)
    parser.add_argument("--runner-max-giveback-pct", type=float, default=RUNNER_MAX_GIVEBACK_PCT)
    parser.add_argument("--runner-trailing-pct", type=float, default=RUNNER_TRAILING_PCT)
    parser.add_argument("--runner-breakeven-buffer-pct", type=float, default=RUNNER_BREAKEVEN_BUFFER_PCT)
    parser.add_argument("--runner-remaining-pct", type=float, default=RUNNER_REMAINING_PCT)
    parser.add_argument("--early-downtrend-exit", action="store_true")
    parser.add_argument("--early-downtrend-min-hours", type=float, default=EARLY_DOWNTREND_MIN_HOURS)
    parser.add_argument("--allow-shorts", action="store_true")
    parser.add_argument("--label", default="")
    return parser.parse_args()


def main() -> int:
    global SPIKE_MIN_QUALITY, SPIKE_MIN_RSI, MAX_HOLD_HOURS, QUALITY_BOOST_MULT, POSITION_PCT
    global SPIKE_THRESHOLD, SPIKE_VOLUME_RATIO_MIN, SPIKE_BODY_RATIO_MIN, SPIKE_CLOSE_POSITION_MIN
    global RUNNER_ENABLED, RUNNER_MAX_HOLD_HOURS, RUNNER_MIN_QUALITY, RUNNER_MIN_PNL_PCT
    global RUNNER_MA_CANDLES, RUNNER_MAX_GIVEBACK_PCT, RUNNER_TRAILING_PCT, RUNNER_BREAKEVEN_BUFFER_PCT
    global RUNNER_REMAINING_PCT
    global EARLY_DOWNTREND_EXIT, EARLY_DOWNTREND_MIN_HOURS, ALLOW_SHORTS
    args = parse_args()
    SPIKE_THRESHOLD = args.spike_threshold
    SPIKE_VOLUME_RATIO_MIN = args.volume_ratio_min
    SPIKE_BODY_RATIO_MIN = args.body_ratio_min
    SPIKE_CLOSE_POSITION_MIN = args.close_position_min
    SPIKE_MIN_QUALITY = args.min_quality
    SPIKE_MIN_RSI = args.min_rsi
    MAX_HOLD_HOURS = args.max_hold_hours
    QUALITY_BOOST_MULT = args.quality_boost_mult
    POSITION_PCT = args.position_pct
    RUNNER_ENABLED = args.runner_enabled
    RUNNER_MAX_HOLD_HOURS = args.runner_max_hold_hours
    RUNNER_MIN_QUALITY = args.runner_min_quality
    RUNNER_MIN_PNL_PCT = args.runner_min_pnl_pct
    RUNNER_MA_CANDLES = args.runner_ma_candles
    RUNNER_MAX_GIVEBACK_PCT = args.runner_max_giveback_pct
    RUNNER_TRAILING_PCT = args.runner_trailing_pct
    RUNNER_BREAKEVEN_BUFFER_PCT = args.runner_breakeven_buffer_pct
    RUNNER_REMAINING_PCT = args.runner_remaining_pct
    EARLY_DOWNTREND_EXIT = args.early_downtrend_exit
    EARLY_DOWNTREND_MIN_HOURS = args.early_downtrend_min_hours
    ALLOW_SHORTS = args.allow_shorts
    start = END - timedelta(days=args.days)
    start_ms = ms(start)
    end_ms = ms(END)
    warmup_ms = start_ms - 7 * 24 * MS_1H
    excluded = {s.strip().upper() for s in args.exclude_symbols.split(",") if s.strip()}
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip() and s.strip().upper() not in excluded]

    print(f"Strict Spike v13 backtest: {start.isoformat()} -> {END.isoformat()}")
    print(f"basis: 1000U, fee={FEE_RATE}, slippage={SLIPPAGE_RATE}, symbols={len(symbols)}")

    btc_1h = fetch_klines("BTCUSDT", "1h", warmup_ms, end_ms)
    btc_times = [k.time + MS_1H for k in btc_1h]
    btc_closes = [k.close for k in btc_1h]

    data_15m: dict[str, list[Candle]] = {}
    ema_lookup: dict[str, tuple[list[int], list[bool]]] = {}
    skipped: dict[str, str] = {}
    for idx, symbol in enumerate(symbols, 1):
        kl15 = fetch_klines(symbol, "15m", warmup_ms, end_ms)
        kl1h = fetch_klines(symbol, "1h", warmup_ms, end_ms)
        if len(kl15) < 100 or len(kl1h) < 40:
            skipped[symbol] = f"insufficient_data 15m={len(kl15)} 1h={len(kl1h)}"
            continue
        data_15m[symbol] = kl15
        ema_lookup[symbol] = build_ema_lookup(kl1h)
        print(f"[{idx}/{len(symbols)}] {symbol}: 15m={len(kl15)} 1h={len(kl1h)}", flush=True)

    active_symbols = sorted(data_15m)
    signals = generate_signals(active_symbols, data_15m, ema_lookup, btc_times, btc_closes, start_ms, end_ms)
    print(f"signals={len(signals)} active_symbols={len(active_symbols)} skipped={len(skipped)}")

    fixed = run_simulation(signals, data_15m, "fixed_1000u")
    compound = run_simulation(signals, data_15m, "compound_10pct")

    payload = {
        "version": "spike_v13_strict",
        "window": {"start": start.isoformat(), "end": END.isoformat(), "days": args.days},
        "basis": {
            "initial_balance": INITIAL_BALANCE,
            "leverage": LEVERAGE,
            "fee_rate": FEE_RATE,
            "slippage_rate": SLIPPAGE_RATE,
            "spike_threshold": SPIKE_THRESHOLD,
            "volume_ratio_min": SPIKE_VOLUME_RATIO_MIN,
            "body_ratio_min": SPIKE_BODY_RATIO_MIN,
            "close_position_min": SPIKE_CLOSE_POSITION_MIN,
            "min_quality": SPIKE_MIN_QUALITY,
            "min_rsi": SPIKE_MIN_RSI,
            "max_hold_hours": MAX_HOLD_HOURS,
            "quality_boost_mult": QUALITY_BOOST_MULT,
            "position_pct": POSITION_PCT,
            "runner_enabled": RUNNER_ENABLED,
            "runner_max_hold_hours": RUNNER_MAX_HOLD_HOURS,
            "runner_min_quality": RUNNER_MIN_QUALITY,
            "runner_min_pnl_pct": RUNNER_MIN_PNL_PCT,
            "runner_ma_candles": RUNNER_MA_CANDLES,
            "runner_max_giveback_pct": RUNNER_MAX_GIVEBACK_PCT,
            "runner_trailing_pct": RUNNER_TRAILING_PCT,
            "runner_breakeven_buffer_pct": RUNNER_BREAKEVEN_BUFFER_PCT,
            "runner_remaining_pct": RUNNER_REMAINING_PCT,
            "early_downtrend_exit": EARLY_DOWNTREND_EXIT,
            "early_downtrend_min_hours": EARLY_DOWNTREND_MIN_HOURS,
            "allow_shorts": ALLOW_SHORTS,
            "exclude_symbols": sorted(excluded),
            "symbols": active_symbols,
            "skipped": skipped,
            "method": "15m closed spike signal; next 15m open entry; optional shorts; no future BTC/day lookahead; fee and slippage included",
        },
        "signal_count": len(signals),
        "modes": {
            "fixed_1000u": fixed,
            "compound_10pct": compound,
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_{args.label}" if args.label else ""
    out = OUT_DIR / f"strict_backtest_spike_v13_{args.days}d{suffix}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "fixed_1000u": fixed["summary"],
        "compound_10pct": compound["summary"],
        "output": str(out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
