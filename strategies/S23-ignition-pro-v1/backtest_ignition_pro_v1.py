#!/usr/bin/env python3
"""Strict backtest for S23 Ignition-Pro v1.

This is a launch/markup-start strategy, not a late trend follower.

Backtest discipline:
- Closed 1h candles generate setup and ignition scores.
- Entry happens at the next 1h open.
- No future candles are used for signal construction.
- Fees, slippage, max positions, and pre-trade stop-risk caps are applied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "strategies" / "S23-ignition-pro-v1" / "data"
LOCAL_CACHE_DIR = ROOT / "data" / "ignition_pro_v1_cache"
UNIVERSAL_CACHE_DIR = ROOT / "trading-system" / "data" / "backtest_cache"
TZ_UTC8 = timezone(timedelta(hours=8))

END = datetime(2026, 5, 14, 10, 0, 0, tzinfo=TZ_UTC8)
INITIAL_BALANCE = 1000.0
LEVERAGE = 3.0
FEE_RATE = 0.0004
SLIPPAGE_RATE = 0.0005
MAX_POSITIONS = 3
MAX_LOSS_PER_TRADE = 25.0
POSITION_PCT = 0.04
COOLDOWN_HOURS = 12
MAX_HOLD_HOURS = 72

MS_1H = 60 * 60 * 1000
MS_4H = 4 * MS_1H
MS_1D = 24 * MS_1H

EMA_FAST = 9
EMA_MID = 21
EMA_SLOW = 55
RSI_PERIOD = 14
ATR_PERIOD = 14
ADX_PERIOD = 14
BB_PERIOD = 20
VOL_LOOKBACK = 24
BREAKOUT_LOOKBACK = 24

MIN_SCORE = 78.0
SCORE_STANDARD = 85.0
SCORE_STRONG = 92.0
MIN_ATR_PCT = 0.006
MAX_ATR_PCT = 0.05
MAX_EXTENSION_4H_EMA21 = 0.11
MAX_RSI = 78.0
MIN_VOLUME_RATIO = 1.55
MIN_BODY_RATIO = 0.52
MAX_UPPER_WICK_RATIO = 0.42
MIN_BTC_OK_SCORE = 4.0

MIN_SL_PCT = 0.025
MAX_SL_PCT = 0.075
ATR_SL_MULT = 1.9
TP1_R = 1.6
TRAIL_R = 1.15

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
    score: float
    setup_score: float
    ignition_score: float
    confirmation_score: float
    sl_pct: float
    breakout_level: float
    stats: dict[str, Any]
    reason: str


@dataclass
class Position:
    id: int
    symbol: str
    direction: str
    entry_time: int
    entry_price: float
    margin_usd: float
    notional_usd: float
    original_margin_usd: float
    original_notional_usd: float
    stop_loss: float
    initial_risk_pct: float
    best_price: float
    score: float
    reason: str
    tp1_done: bool = False


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

    full_url = f"https://fapi.binance.com{endpoint}?{urlencode(params)}"
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
    best: list[Candle] | None = None
    best_end = 0
    for path in sorted(UNIVERSAL_CACHE_DIR.glob(f"klines_{symbol}_{interval}_*.json")):
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
    rows = cached[:] if cached else []
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
        time.sleep(0.03)

    unique = {c.time: c for c in rows if start_ms <= c.time <= end_ms}
    return [unique[t] for t in sorted(unique)]


def resample(candles: list[Candle], period_ms: int) -> list[Candle]:
    buckets: dict[int, list[Candle]] = {}
    for candle in candles:
        buckets.setdefault((candle.time // period_ms) * period_ms, []).append(candle)
    return [
        Candle(
            time=ts,
            open=rows[0].open,
            high=max(c.high for c in rows),
            low=min(c.low for c in rows),
            close=rows[-1].close,
            volume=sum(c.volume for c in rows),
        )
        for ts, rows in sorted(buckets.items())
    ]


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
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
        if i >= period:
            avg_gain = sum(gains[-period:]) / period
            avg_loss = sum(losses[-period:]) / period
            out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
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
    trs = [0.0]
    pdm = [0.0]
    ndm = [0.0]
    dx = [0.0] * len(candles)
    for i in range(1, len(candles)):
        cur = candles[i]
        prev = candles[i - 1]
        up = cur.high - prev.high
        down = prev.low - cur.low
        pdm.append(up if up > down and up > 0 else 0.0)
        ndm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close)))
        if i >= period:
            tr = sum(trs[i - period + 1:i + 1])
            if tr > 0:
                pdi = 100 * sum(pdm[i - period + 1:i + 1]) / tr
                ndi = 100 * sum(ndm[i - period + 1:i + 1]) / tr
                dx[i] = 100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi else 0.0
        if i >= period * 2:
            out[i] = sum(dx[i - period + 1:i + 1]) / period
    return out


def sma_series(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = sum(values[i - period + 1:i + 1]) / period
    return out


def bb_width_series(closes: list[float], period: int = BB_PERIOD) -> list[float]:
    out = [0.0] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1:i + 1]
        mean = sum(window) / period
        stdev = statistics.pstdev(window)
        out[i] = (4 * stdev / mean) if mean > 0 else 0.0
    return out


def obv_series(candles: list[Candle]) -> list[float]:
    out = [0.0] * len(candles)
    for i in range(1, len(candles)):
        if candles[i].close > candles[i - 1].close:
            out[i] = out[i - 1] + candles[i].volume
        elif candles[i].close < candles[i - 1].close:
            out[i] = out[i - 1] - candles[i].volume
        else:
            out[i] = out[i - 1]
    return out


def indicators(candles: list[Candle]) -> dict[str, list[Any]]:
    closes = [c.close for c in candles]
    volumes = [c.volume for c in candles]
    return {
        "ema9": ema_series(closes, EMA_FAST),
        "ema21": ema_series(closes, EMA_MID),
        "ema55": ema_series(closes, EMA_SLOW),
        "rsi": rsi_series(closes),
        "atr": atr_series(candles),
        "adx": adx_series(candles),
        "vol_sma24": sma_series(volumes, VOL_LOOKBACK),
        "bb_width": bb_width_series(closes),
        "obv": obv_series(candles),
    }


def direction_pnl_pct(direction: str, entry: float, exit_price: float) -> float:
    return (exit_price - entry) / entry if direction == "long" else (entry - exit_price) / entry


def candle_quality(candle: Candle) -> tuple[float, float]:
    rng = max(candle.high - candle.low, 1e-12)
    body = abs(candle.close - candle.open) / rng
    upper = (candle.high - max(candle.close, candle.open)) / rng
    return body, upper


def btc_environment_score(btc4: list[Candle], btc4_ind: dict[str, list[Any]], ts: int) -> float:
    idx = lookup_index(btc4, ts)
    if idx < 60:
        return 5.0
    e21 = btc4_ind["ema21"][idx]
    e55 = btc4_ind["ema55"][idx]
    rsi = btc4_ind["rsi"][idx]
    adx = btc4_ind["adx"][idx]
    close = btc4[idx].close
    if not e21 or not e55:
        return 5.0
    score = 5.0
    if close > e21:
        score += 1.3
    if e21 > e55:
        score += 1.2
    if 45 <= rsi <= 72:
        score += 1.0
    elif rsi < 38:
        score -= 2.0
    if adx >= 18 and close > e21:
        score += 0.8
    if close < e55 and e21 < e55:
        score -= 2.5
    return max(0.0, min(10.0, score))


def relative_strength(symbol_closes: list[float], btc_closes: list[float]) -> float:
    if len(symbol_closes) < 25 or len(btc_closes) < 25:
        return 0.0
    sym = symbol_closes[-1] / symbol_closes[-24] - 1
    btc = btc_closes[-1] / btc_closes[-24] - 1
    return (sym - btc) * 100


def score_signal(
    symbol: str,
    candles: list[Candle],
    ind: dict[str, list[Any]],
    candles4: list[Candle],
    ind4: dict[str, list[Any]],
    btc4: list[Candle],
    btc4_ind: dict[str, list[Any]],
    btc1: list[Candle],
    i: int,
    confirm_next_candle: bool = False,
) -> Signal | None:
    entry_offset = 2 if confirm_next_candle else 1
    if i + entry_offset >= len(candles) or i < 140:
        return None
    candle = candles[i]
    prev = candles[i - 1]
    ts = candle.time
    idx4 = lookup_index(candles4, ts)
    if idx4 < 70:
        return None

    e9 = ind["ema9"][i]
    e21 = ind["ema21"][i]
    e55 = ind["ema55"][i]
    e21_4 = ind4["ema21"][idx4]
    e55_4 = ind4["ema55"][idx4]
    if not all([e9, e21, e55, e21_4, e55_4]):
        return None

    atr_pct = float(ind["atr"][i])
    rsi = float(ind["rsi"][i])
    adx_4 = float(ind4["adx"][idx4])
    vol_sma = ind["vol_sma24"][i]
    if not vol_sma or vol_sma <= 0:
        return None

    if atr_pct < MIN_ATR_PCT or atr_pct > MAX_ATR_PCT or rsi > MAX_RSI:
        return None

    recent_24 = candles[i - 24:i]
    recent_72 = candles[i - 72:i]
    if len(recent_24) < 24 or len(recent_72) < 72:
        return None

    breakout_high = max(c.high for c in candles[i - BREAKOUT_LOOKBACK:i])
    range24 = (max(c.high for c in recent_24) - min(c.low for c in recent_24)) / prev.close
    range72 = (max(c.high for c in recent_72) - min(c.low for c in recent_72)) / prev.close
    bb_now = float(ind["bb_width"][i])
    bb_prev_avg = sum(ind["bb_width"][i - 72:i - 24]) / 48
    vol_ratio = candle.volume / vol_sma
    body_ratio, upper_wick = candle_quality(candle)
    close_strength = (candle.close - candle.low) / max(candle.high - candle.low, 1e-12)
    extension_4h = abs(candle.close / e21_4 - 1)

    if extension_4h > MAX_EXTENSION_4H_EMA21:
        return None
    if candle.close <= breakout_high:
        return None
    if vol_ratio < MIN_VOLUME_RATIO or body_ratio < MIN_BODY_RATIO or upper_wick > MAX_UPPER_WICK_RATIO:
        return None
    if close_strength < 0.62:
        return None
    if not (e9 >= e21 * 0.998 and candle.close > e21 and e21 > e55 * 0.985):
        return None

    btc_score = btc_environment_score(btc4, btc4_ind, ts)
    if btc_score < MIN_BTC_OK_SCORE:
        return None

    btc_idx = lookup_index(btc1, ts)
    btc_window = btc1[max(0, btc_idx - 24):btc_idx + 1]
    rs = relative_strength([c.close for c in candles[i - 24:i + 1]], [c.close for c in btc_window])

    obv = ind["obv"]
    obv_up = obv[i] > obv[i - 12] and obv[i - 6] > obv[i - 24]
    lows_up = min(c.low for c in candles[i - 12:i]) > min(c.low for c in candles[i - 36:i - 12])
    no_prior_overheat = (candle.close / candles[i - 24].close - 1) < 0.18

    setup_score = 0.0
    setup_score += 5.0 if range24 < range72 * 0.58 else 2.0 if range24 < range72 * 0.75 else 0.0
    setup_score += 5.0 if bb_now < bb_prev_avg * 0.82 else 2.0 if bb_now < bb_prev_avg else 0.0
    setup_score += 4.0 if abs(prev.close / e21 - 1) < 0.035 else 1.5
    setup_score += 3.0 if lows_up else 0.0
    setup_score += 3.0 if no_prior_overheat else -4.0
    setup_score = max(0.0, min(20.0, setup_score))

    money_score = 0.0
    money_score += min(8.0, max(0.0, (vol_ratio - 1.0) * 5.0))
    money_score += 5.0 if obv_up else 0.0
    money_score += 4.0 if rs > 1.0 else 2.0 if rs > 0.0 else -2.0
    money_score += 3.0 if candle.volume > max(c.volume for c in candles[i - 6:i]) else 0.0
    money_score = max(0.0, min(20.0, money_score))

    ignition_score = 0.0
    ignition_score += 8.0 if candle.close > breakout_high * 1.004 else 5.0
    ignition_score += 5.0 if body_ratio >= 0.68 else 3.0
    ignition_score += 5.0 if close_strength >= 0.78 else 3.0
    ignition_score += 4.0 if e9 > e21 and e9 > ind["ema9"][i - 3] else 1.5
    ignition_score += 3.0 if 58 <= rsi <= 72 else 1.0 if 52 <= rsi < 58 else -3.0
    ignition_score = max(0.0, min(25.0, ignition_score))

    mtf_score = 0.0
    mtf_score += 5.0 if e21_4 > e55_4 * 0.998 and candles4[idx4].close > e21_4 else 0.0
    mtf_score += 4.0 if adx_4 >= 16 else 1.5
    mtf_score += min(4.0, btc_score * 0.4)
    mtf_score += 2.0 if e21 > e55 else 0.0
    mtf_score = max(0.0, min(15.0, mtf_score))

    risk_score = 0.0
    risk_score += 4.0 if extension_4h < 0.055 else 1.5 if extension_4h < 0.085 else 0.0
    risk_score += 3.0 if MIN_ATR_PCT <= atr_pct <= 0.028 else 1.0
    risk_score += 3.0 if rsi <= 72 else 0.0
    risk_score = max(0.0, min(10.0, risk_score))

    confirmation_score = mtf_score + risk_score
    total = setup_score + money_score + ignition_score + confirmation_score
    if total < MIN_SCORE:
        return None

    signal_time = ts
    entry_time = candles[i + 1].time
    entry_open = candles[i + 1].open
    if confirm_next_candle:
        confirm = candles[i + 1]
        confirm_body, confirm_upper = candle_quality(confirm)
        if confirm.close <= breakout_high:
            return None
        if confirm.close < candle.close * 0.985:
            return None
        if confirm.close < confirm.open and confirm_body > 0.62:
            return None
        if confirm_upper > 0.55 and confirm.close < candle.close:
            return None
        signal_time = confirm.time
        entry_time = candles[i + 2].time
        entry_open = candles[i + 2].open

    sl_pct = max(MIN_SL_PCT, min(MAX_SL_PCT, atr_pct * ATR_SL_MULT))
    return Signal(
        symbol=symbol,
        signal_time=signal_time,
        entry_time=entry_time,
        entry_open=entry_open,
        direction="long",
        score=round(total, 3),
        setup_score=round(setup_score, 3),
        ignition_score=round(ignition_score, 3),
        confirmation_score=round(confirmation_score, 3),
        sl_pct=sl_pct,
        breakout_level=breakout_high,
        stats={
            "atr_pct": round(atr_pct * 100, 3),
            "rsi": round(rsi, 2),
            "vol_ratio": round(vol_ratio, 3),
            "body_ratio": round(body_ratio, 3),
            "upper_wick": round(upper_wick, 3),
            "range24_pct": round(range24 * 100, 3),
            "range72_pct": round(range72 * 100, 3),
            "bb_width": round(bb_now * 100, 3),
            "btc_score": round(btc_score, 3),
            "relative_strength_24h": round(rs, 3),
            "extension_4h_ema21_pct": round(extension_4h * 100, 3),
            "adx_4h": round(adx_4, 2),
        },
        reason="compression + money-inflow + closed-breakout ignition" + (" + next-candle confirmation" if confirm_next_candle else ""),
    )


def close_position(ts: int, pos: Position, exit_price: float, reason: str, balance: float) -> tuple[dict[str, Any], float]:
    pnl_pct = direction_pnl_pct(pos.direction, pos.entry_price, exit_price)
    gross = pos.notional_usd * pnl_pct
    exit_fee = pos.notional_usd * FEE_RATE
    net = gross - exit_fee
    balance += net
    return {
        "id": pos.id,
        "symbol": pos.symbol,
        "direction": pos.direction,
        "signal_type": "ignition_long",
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
    }, balance


def close_partial(
    ts: int,
    pos: Position,
    exit_price: float,
    fraction: float,
    reason: str,
    balance: float,
) -> tuple[dict[str, Any], float]:
    fraction = max(0.0, min(1.0, fraction))
    partial_notional = pos.notional_usd * fraction
    partial_margin = pos.margin_usd * fraction
    pnl_pct = direction_pnl_pct(pos.direction, pos.entry_price, exit_price)
    gross = partial_notional * pnl_pct
    exit_fee = partial_notional * FEE_RATE
    net = gross - exit_fee
    balance += net
    pos.notional_usd -= partial_notional
    pos.margin_usd -= partial_margin
    trade = {
        "id": pos.id,
        "symbol": pos.symbol,
        "direction": pos.direction,
        "signal_type": "ignition_long",
        "entry_time": text(pos.entry_time),
        "exit_time": text(ts),
        "entry_price": round(pos.entry_price, 8),
        "exit_price": round(exit_price, 8),
        "margin_usd": round(partial_margin, 4),
        "notional_usd": round(partial_notional, 4),
        "pnl_usd": round(net, 4),
        "pnl_pct_on_margin": round(net / partial_margin * 100 if partial_margin else 0, 4),
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
            "win_rate_pct": round(bucket["wins"] / trades * 100, 2) if trades else 0.0,
            "pnl_usd": round(bucket["pnl_usd"], 4),
        }
    return dict(sorted(out.items(), key=lambda item: item[1]["pnl_usd"], reverse=True))


def max_drawdown(values: list[float]) -> float:
    peak = values[0] if values else 0.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def summary_report(
    trades: list[dict[str, Any]],
    equity_curve: list[dict[str, Any]],
    final_balance: float,
    start_ms: int,
    end_ms: int,
    days: int,
    symbols: list[str],
) -> dict[str, Any]:
    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    gross_profit = sum(t["pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["pnl_usd"] for t in losses))
    dd = max_drawdown([p["equity"] for p in equity_curve])
    return {
        "strategy": "S23-ignition-pro-v1",
        "variant": "strict_launch_detection",
        "start": text(start_ms),
        "end": text(end_ms),
        "days": days,
        "symbols": symbols,
        "initial_balance": round(INITIAL_BALANCE, 4),
        "final_balance": round(final_balance, 4),
        "pnl_usd": round(final_balance - INITIAL_BALANCE, 4),
        "roi_pct": round((final_balance / INITIAL_BALANCE - 1) * 100, 4),
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "max_drawdown_pct": round(dd * 100, 4),
        "avg_trade_usd": round(sum(t["pnl_usd"] for t in trades) / len(trades), 4) if trades else 0.0,
        "max_single_win": round(max((t["pnl_usd"] for t in trades), default=0.0), 4),
        "max_single_loss": round(min((t["pnl_usd"] for t in trades), default=0.0), 4),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    end_dt = parse_time(args.end)
    start_dt = end_dt - timedelta(days=args.days)
    warmup_dt = start_dt - timedelta(days=args.warmup_days)
    start_ms = ms(start_dt)
    warmup_ms = ms(warmup_dt)
    end_ms = ms(end_dt)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    print(f"Window: {text(start_ms)} -> {text(end_ms)} | warmup {text(warmup_ms)}")
    print(f"Symbols: {len(symbols)}")

    btc1 = fetch_klines("BTCUSDT", "1h", warmup_ms, end_ms)
    btc4 = resample(btc1, MS_4H)
    btc4_ind = indicators(btc4)

    candles_by_symbol: dict[str, list[Candle]] = {}
    ind_by_symbol: dict[str, dict[str, list[Any]]] = {}
    candles4_by_symbol: dict[str, list[Candle]] = {}
    ind4_by_symbol: dict[str, dict[str, list[Any]]] = {}
    for symbol in symbols:
        rows = fetch_klines(symbol, "1h", warmup_ms, end_ms)
        if len(rows) < 300:
            print(f"skip {symbol}: insufficient candles={len(rows)}")
            continue
        candles_by_symbol[symbol] = rows
        ind_by_symbol[symbol] = indicators(rows)
        candles4_by_symbol[symbol] = resample(rows, MS_4H)
        ind4_by_symbol[symbol] = indicators(candles4_by_symbol[symbol])
        print(f"loaded {symbol}: 1h={len(rows)}")

    events: dict[int, list[Signal]] = {}
    for symbol, rows in candles_by_symbol.items():
        for i, candle in enumerate(rows):
            if candle.time < start_ms or candle.time > end_ms:
                continue
            signal = score_signal(
                symbol,
                rows,
                ind_by_symbol[symbol],
                candles4_by_symbol[symbol],
                ind4_by_symbol[symbol],
                btc4,
                btc4_ind,
                btc1,
                i,
                args.confirm_next_candle,
            )
            if signal and start_ms <= signal.entry_time <= end_ms:
                events.setdefault(signal.entry_time, []).append(signal)

    candle_map = {symbol: {c.time: c for c in rows} for symbol, rows in candles_by_symbol.items()}
    all_times = sorted({c.time for rows in candles_by_symbol.values() for c in rows if start_ms <= c.time <= end_ms})

    balance = INITIAL_BALANCE
    open_positions: list[Position] = []
    trades: list[dict[str, Any]] = []
    equity_curve = [{"time": text(start_ms), "equity": round(balance, 6)}]
    cooldown_until: dict[str, int] = {}
    next_id = 1

    for ts in all_times:
        still_open = []
        for pos in open_positions:
            candle = candle_map.get(pos.symbol, {}).get(ts)
            if not candle:
                still_open.append(pos)
                continue

            exit_price = None
            exit_reason = None
            held_hours = (ts - pos.entry_time) / MS_1H
            pos.best_price = max(pos.best_price, candle.high)
            gain_r = (pos.best_price / pos.entry_price - 1) / pos.initial_risk_pct
            if not pos.tp1_done and gain_r >= args.tp1_r:
                tp1_price = pos.entry_price * (1 + pos.initial_risk_pct * args.tp1_r)
                trade, balance = close_partial(
                    ts,
                    pos,
                    tp1_price * (1 - SLIPPAGE_RATE),
                    args.tp1_fraction,
                    "partial_tp1",
                    balance,
                )
                trades.append(trade)
                pos.tp1_done = True
                pos.stop_loss = max(pos.stop_loss, pos.entry_price * 1.001)
            if gain_r >= 1.0:
                pos.stop_loss = max(pos.stop_loss, pos.entry_price * 1.001)
            if pos.tp1_done:
                trail = pos.best_price * (1 - pos.initial_risk_pct * args.trail_r)
                pos.stop_loss = max(pos.stop_loss, trail)

            idx = lookup_index(candles_by_symbol[pos.symbol], ts)
            ind = ind_by_symbol[pos.symbol]
            e9 = ind["ema9"][idx]
            rsi = ind["rsi"][idx]
            vol_sma = ind["vol_sma24"][idx]
            vol_ratio = candle.volume / vol_sma if vol_sma else 0.0
            body, upper = candle_quality(candle)
            failed_momentum = (
                pos.tp1_done
                and e9
                and candle.close < e9
                and vol_ratio > 1.15
            )
            blowoff = rsi > 82 and upper > 0.48 and body < 0.45

            if candle.low <= pos.stop_loss:
                exit_price = pos.stop_loss * (1 - SLIPPAGE_RATE)
                exit_reason = "stop_or_trailing"
            elif failed_momentum:
                exit_price = candle.close * (1 - SLIPPAGE_RATE)
                exit_reason = "momentum_failed"
            elif blowoff:
                exit_price = candle.close * (1 - SLIPPAGE_RATE)
                exit_reason = "blowoff_exit"
            elif held_hours >= args.max_hold_hours:
                exit_price = candle.close * (1 - SLIPPAGE_RATE)
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
            candle = candle_map.get(pos.symbol, {}).get(ts)
            if candle:
                floating += pos.notional_usd * direction_pnl_pct(pos.direction, pos.entry_price, candle.close)
        equity_curve.append({"time": text(ts), "equity": round(balance + floating, 6)})

        ranked = sorted(events.get(ts, []), key=lambda s: s.score, reverse=True)
        open_symbols = {p.symbol for p in open_positions}
        for signal in ranked:
            if len(open_positions) >= MAX_POSITIONS:
                break
            if signal.symbol in open_symbols or cooldown_until.get(signal.symbol, 0) > ts:
                continue
            mult = 0.65 if signal.score < SCORE_STANDARD else 1.0 if signal.score < SCORE_STRONG else 1.2
            margin = balance * args.position_pct * mult
            loss_at_stop = margin * LEVERAGE * signal.sl_pct
            if loss_at_stop > args.max_loss_per_trade:
                margin = args.max_loss_per_trade / (LEVERAGE * signal.sl_pct)
            if margin <= 0:
                continue
            entry_price = signal.entry_open * (1 + SLIPPAGE_RATE)
            notional = margin * LEVERAGE
            entry_fee = notional * FEE_RATE
            if balance - entry_fee <= 0:
                continue
            balance -= entry_fee
            open_positions.append(Position(
                id=next_id,
                symbol=signal.symbol,
                direction="long",
                entry_time=ts,
                entry_price=entry_price,
                margin_usd=margin,
                notional_usd=notional,
                original_margin_usd=margin,
                original_notional_usd=notional,
                stop_loss=entry_price * (1 - signal.sl_pct),
                initial_risk_pct=signal.sl_pct,
                best_price=entry_price,
                score=signal.score,
                reason=f"{signal.reason}; stats={json.dumps(signal.stats, ensure_ascii=False)}",
            ))
            open_symbols.add(signal.symbol)
            next_id += 1

    if open_positions:
        for pos in open_positions:
            candle = candles_by_symbol[pos.symbol][-1]
            trade, balance = close_position(end_ms, pos, candle.close * (1 - SLIPPAGE_RATE), "end_of_backtest", balance)
            trades.append(trade)
        equity_curve.append({"time": text(end_ms), "equity": round(balance, 6)})

    by_symbol: dict[str, dict[str, Any]] = {}
    by_exit: dict[str, dict[str, Any]] = {}
    by_month: dict[str, dict[str, Any]] = {}
    for trade in trades:
        pnl = float(trade["pnl_usd"])
        add_bucket(by_symbol, trade["symbol"], pnl)
        add_bucket(by_exit, trade["exit_reason"], pnl)
        add_bucket(by_month, trade["exit_time"][:7], pnl)

    return {
        "summary": summary_report(trades, equity_curve, balance, start_ms, end_ms, args.days, list(candles_by_symbol)),
        "by_symbol": finish_buckets(by_symbol),
        "by_exit_reason": finish_buckets(by_exit),
        "monthly": finish_buckets(by_month),
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
            "min_score": MIN_SCORE,
            "score_standard": SCORE_STANDARD,
            "score_strong": SCORE_STRONG,
            "filters": {
                "min_volume_ratio": MIN_VOLUME_RATIO,
                "min_body_ratio": MIN_BODY_RATIO,
                "max_upper_wick_ratio": MAX_UPPER_WICK_RATIO,
                "min_atr_pct": MIN_ATR_PCT,
                "max_atr_pct": MAX_ATR_PCT,
                "max_extension_4h_ema21": MAX_EXTENSION_4H_EMA21,
                "max_rsi": MAX_RSI,
            },
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run S23 Ignition-Pro v1 strict backtest.")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--warmup-days", type=int, default=160)
    parser.add_argument("--end", default=END.isoformat())
    parser.add_argument("--symbols", default=",".join(COMMON_SYMBOLS))
    parser.add_argument("--position-pct", type=float, default=POSITION_PCT)
    parser.add_argument("--max-loss-per-trade", type=float, default=MAX_LOSS_PER_TRADE)
    parser.add_argument("--max-hold-hours", type=float, default=MAX_HOLD_HOURS)
    parser.add_argument("--min-score", type=float, default=MIN_SCORE)
    parser.add_argument("--min-volume-ratio", type=float, default=MIN_VOLUME_RATIO)
    parser.add_argument("--min-body-ratio", type=float, default=MIN_BODY_RATIO)
    parser.add_argument("--max-upper-wick-ratio", type=float, default=MAX_UPPER_WICK_RATIO)
    parser.add_argument("--max-extension-4h-ema21", type=float, default=MAX_EXTENSION_4H_EMA21)
    parser.add_argument("--confirm-next-candle", action="store_true")
    parser.add_argument("--tp1-r", type=float, default=TP1_R)
    parser.add_argument("--tp1-fraction", type=float, default=0.45)
    parser.add_argument("--trail-r", type=float, default=TRAIL_R)
    parser.add_argument("--label", default="base")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    global MIN_SCORE, MIN_VOLUME_RATIO, MIN_BODY_RATIO, MAX_UPPER_WICK_RATIO, MAX_EXTENSION_4H_EMA21
    MIN_SCORE = args.min_score
    MIN_VOLUME_RATIO = args.min_volume_ratio
    MIN_BODY_RATIO = args.min_body_ratio
    MAX_UPPER_WICK_RATIO = args.max_upper_wick_ratio
    MAX_EXTENSION_4H_EMA21 = args.max_extension_4h_ema21
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = run(args)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    out = OUT_DIR / f"strict_backtest_ignition_pro_v1_{args.days}d_{args.label}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
