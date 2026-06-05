#!/usr/bin/env python3
"""
S24-Ignition — Altcoin 小时级点火信号策略回测

信号逻辑（4 个条件全部满足）:
  1. 15m 收盘涨幅 >= SPIKE_THRESHOLD (默认 1.2%)
  2. 1h EMA9 > EMA21  (使用前一根已收盘的 1h K 线，无前视)
  3. 15m RSI >= MIN_RSI (默认 50)
  4. 信号质量分 >= MIN_QUALITY (默认 70)
  入场: 下一根 15m 开盘价

整点过滤 (--hour-only，生产推荐):
  仅交易每小时第一根 15m K 线 (开盘时间为 XX:00)。
  整点开盘时机构算法重置挂单，成交量有系统性峰值，信号延续概率更高。

仓位 (质量分加权):
  quality 70-79 → 7%   (×3 杠杆)
  quality 80-89 → 10%
  quality 90+   → 15%

退出:
  止损: ATR × 1.5，区间 [3%, 9%]
  止盈: 止损 × 2.5
  超时: MAX_HOLD_HOURS 后按市价平

生产配置:
  python3 backtest_ignition.py --days 365 --hour-only --sym-cap 5 \\
    --exclude BUSDT,BILLUSDT,BNBUSDT,LINKUSDT,SAGAUSDT

动态黑名单 (--dynamic，实验性):
  Rule 1: 近 10 笔亏 >= 8 笔         → 冷却 7 天
  Rule 2: 近 10 笔 PF < 0.8         → 冷却 14 天
  Rule 3: 近 7 天单币亏损 > -3% 余额  → 冷却 14 天
  Rule 4: 当日亏损 > -3% 余额        → 当天停止
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT      = Path(__file__).resolve().parents[2]
OUT_DIR   = ROOT / "strategies" / "S24-ignition" / "data"
CACHE_DIR = ROOT / "data" / "final_true_one_year_backtests" / "api_cache"
TZ_UTC8   = timezone(timedelta(hours=8))

# ── 默认参数 ──────────────────────────────────────────────────────────
INITIAL_BALANCE  = 1000.0
LEVERAGE         = 3.0
FEE_RATE         = 0.0004
SLIPPAGE_RATE    = 0.0005
MAX_POSITIONS    = 3
COOLDOWN_HOURS   = 4       # 同 symbol 冷却

SPIKE_THRESHOLD  = 0.012   # 15m 涨幅门槛 (1.2%)
MIN_RSI          = 50.0
MIN_QUALITY      = 70.0
ATR_PERIOD       = 14
RSI_PERIOD       = 14
EMA_FAST         = 9       # 1h EMA 快线
EMA_SLOW         = 21      # 1h EMA 慢线
ATR_SL_MULT      = 1.5
MIN_SL_PCT       = 0.030   # 最小止损 3%
MAX_SL_PCT       = 0.090   # 最大止损 9%（防止 ATR 过宽）
TP_SL_RATIO      = 2.5     # 止盈 = 止损 × 2.5

POSITION_PCT_LOW  = 0.07   # quality 70-79
POSITION_PCT_MID  = 0.10   # quality 80-89
POSITION_PCT_HIGH = 0.15   # quality 90+

# 固定保证金口径：与 INITIAL_BALANCE 解耦，避免改本金时 fixed 模式变形
FIXED_MARGIN_LOW  = 70.0
FIXED_MARGIN_MID  = 100.0
FIXED_MARGIN_HIGH = 150.0
DEFAULT_MARGIN_CAP = 300.0

MAX_HOLD_HOURS   = 4.0
GRACE_HOURS      = 0.5     # 入场后 30 分钟内不触发止损
SYM_WEEKLY_CAP   = 4       # 单 symbol 每 7 天最多开仓次数（0=不限）

# ── 动态黑名单参数 ──────────────────────────────────────────────────────
DYN_WIN          = 10      # Rule 1&2 滑动窗口大小
DYN_LOSS_N       = 8       # Rule 1: 窗口内亏损笔数触发阈值 (5.5% random trigger at 50% WR)
DYN_COOL1        = 7       # Rule 1 冷却天数
DYN_PF_MIN       = 0.8     # Rule 2: PF 下限
DYN_COOL2        = 14      # Rule 2&3 冷却天数
DYN_SYM_LOSS_PCT = 0.03    # Rule 3: 单币 7 天亏损占余额比例触发
DYN_DAILY_PCT    = 0.03    # Rule 4: 当日总亏损占余额比例触发

# ── 数据层 ────────────────────────────────────────────────────────────
COMMON_SYMBOLS = [
    "XAGUSDT","XAUUSDT","LABUSDT","SUIUSDT","XRPUSDT","BUSDT","CRCLUSDT",
    "BILLUSDT","BNBUSDT","SNDKUSDT","TONUSDT","GTCUSDT","1000PEPEUSDT",
    "SKYAIUSDT","VVVUSDT","SAGAUSDT","MUUSDT","ADAUSDT","INTCUSDT","LDOUSDT",
    "AVAXUSDT","LINKUSDT","PAXGUSDT","AAVEUSDT",
]

MS_15M = 15 * 60 * 1000
MS_1H  = 60 * 60 * 1000


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class CachedAPI:
    def __init__(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._si = [
            {"symbol": s, "quoteVolume": str(10_000_000_000 - i * 10_000_000), "lastPrice": "1"}
            for i, s in enumerate(COMMON_SYMBOLS)
        ]
        self._hits = self._misses = 0

    def get(self, endpoint: str, params: dict) -> Any:
        if endpoint == "/fapi/v1/ticker/24hr":
            return self._si
        key  = hashlib.sha1(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()
        path = CACHE_DIR / f"{key}.json"
        if path.exists():
            self._hits += 1
            return json.loads(path.read_text())
        self._misses += 1
        url = "https://fapi.binance.com" + endpoint + "?" + urlencode(params)
        with urlopen(url, timeout=20) as r:
            data = json.loads(r.read())
        path.write_text(json.dumps(data, ensure_ascii=False))
        return data

    def report(self):
        print(f"[cache] hits={self._hits} misses={self._misses}")

    def klines(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Candle]:
        all_raw: list[list] = []
        cur = start_ms
        while cur < end_ms:
            raw = self.get("/fapi/v1/klines", {
                "symbol": symbol, "interval": interval,
                "startTime": cur, "endTime": end_ms, "limit": 1500,
            })
            if not raw:
                break
            all_raw.extend(raw)
            if len(raw) < 1500:
                break
            cur = int(raw[-1][0]) + 1
            time.sleep(0.05)
        return [Candle(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[7])) for k in all_raw]


# ── 技术指标 ──────────────────────────────────────────────────────────

def ema_series(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    k = 2 / (period + 1)
    val = sum(values[:period]) / period
    result[period - 1] = val
    for i in range(period, len(values)):
        val = values[i] * k + val * (1 - k)
        result[i] = val
    return result


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


def signal_quality(
    chg_pct: float,
    atr_val: float,
    candle: Candle,
    history: list[Candle],
    threshold: float = SPIKE_THRESHOLD,
) -> float:
    """0-100 品质分，基于动量强度 / 成交量 / ATR 适中性"""
    score = 0.0
    # 涨幅强度 (0-35)
    score += min(35.0, (chg_pct - threshold) / threshold * 20 + 20)
    # 成交量 vs 近期均量 (0-30)
    if len(history) >= 10:
        avg_vol = sum(c.volume for c in history[-10:]) / 10
        if avg_vol > 0:
            vr = candle.volume / avg_vol
            score += min(30.0, vr * 10)
    # ATR 适中 (0-20): 太小（无波动）或太大（风险高）都扣分
    if atr_val > 0 and candle.close > 0:
        atr_pct = atr_val / candle.close
        if 0.005 <= atr_pct <= 0.04:
            score += 20.0
        elif atr_pct < 0.005:
            score += 5.0
        else:
            score += max(0.0, 20.0 - (atr_pct - 0.04) * 200)
    # K 线形态：实体比例 (0-15)
    body = abs(candle.close - candle.open)
    rng  = candle.high - candle.low
    if rng > 0:
        score += min(15.0, body / rng * 20)
    return round(min(100.0, max(0.0, score)), 1)


# ── 信号生成 ──────────────────────────────────────────────────────────

@dataclass
class Signal:
    symbol:      str
    signal_time: int    # 信号 K 线收盘时间 (ms)
    entry_time:  int    # 下一根 15m 开盘时间 (ms)
    entry_price: float
    sl_pct:      float
    tp_pct:      float
    quality:     float
    position_pct: float
    reason:      str


def position_pct_for(quality: float) -> float:
    if quality >= 90:
        return POSITION_PCT_HIGH
    elif quality >= 80:
        return POSITION_PCT_MID
    return POSITION_PCT_LOW


def fixed_margin_for(quality: float) -> float:
    """固定保证金口径，用于消除复利放大幻觉；不随 INITIAL_BALANCE 改变。"""
    if quality >= 90:
        return FIXED_MARGIN_HIGH
    elif quality >= 80:
        return FIXED_MARGIN_MID
    return FIXED_MARGIN_LOW


def generate_signals(
    symbol: str,
    klines_15m: list[Candle],
    ema_bullish_at: dict[int, bool],   # ts_ms → True if 1h EMA9 > EMA21
    threshold: float,
    min_rsi: float,
    min_quality: float,
    hour_only: bool = False,
    tp_ratio: float = TP_SL_RATIO,
) -> list[Signal]:
    signals = []
    closes = [c.close for c in klines_15m]
    for i in range(ATR_PERIOD + RSI_PERIOD + 10, len(klines_15m) - 1):
        candle = klines_15m[i]
        if candle.open <= 0:
            continue
        chg = (candle.close - candle.open) / candle.open
        if chg < threshold:
            continue
        # 整点过滤：只取每小时第一根 15m K 线
        if hour_only and (candle.time % MS_1H) != 0:
            continue
        # 1h EMA 趋势确认：使用上一根已收盘的 1h K 线
        # floor(candle.time / 1h) * 1h - 1h = 当前 15m 所在小时的上一小时
        prev_1h = (candle.time // MS_1H) * MS_1H - MS_1H
        bullish = ema_bullish_at.get(prev_1h)
        if not bullish:
            continue
        # RSI
        rsi_val = rsi(closes[max(0, i - RSI_PERIOD - 5):i + 1])
        if rsi_val < min_rsi:
            continue
        # ATR 和止损
        # 只用信号K线之前的历史波动，避免 spike 当根放大 ATR。
        atr_val = atr(klines_15m[max(0, i - ATR_PERIOD - 5):i])
        sl_raw  = atr_val * ATR_SL_MULT / candle.close if candle.close > 0 else MIN_SL_PCT
        sl_pct  = max(MIN_SL_PCT, min(MAX_SL_PCT, sl_raw))
        # 质量分
        quality = signal_quality(chg, atr_val, candle, klines_15m[max(0, i - 20):i], threshold)
        if quality < min_quality:
            continue
        next_c = klines_15m[i + 1]
        signals.append(Signal(
            symbol=symbol,
            signal_time=candle.time,
            entry_time=next_c.time,
            entry_price=next_c.open,
            sl_pct=sl_pct,
            tp_pct=sl_pct * tp_ratio,
            quality=quality,
            position_pct=position_pct_for(quality),
            reason=f"spike {chg*100:+.2f}% rsi={rsi_val:.0f} q={quality:.0f}",
        ))
    return signals


def build_ema_lookup(klines_1h: list[Candle]) -> dict[int, bool]:
    closes = [c.close for c in klines_1h]
    e9  = ema_series(closes, EMA_FAST)
    e21 = ema_series(closes, EMA_SLOW)
    result = {}
    for i, (f, s) in enumerate(zip(e9, e21)):
        if f is not None and s is not None:
            result[klines_1h[i].time] = f > s
    return result


# ── 模拟引擎 ──────────────────────────────────────────────────────────

@dataclass
class Position:
    id:           int
    symbol:       str
    entry_time:   int
    entry_price:  float
    margin_usd:   float
    notional_usd: float
    stop_loss:    float
    take_profit:  float
    sl_pct:       float
    tp_pct:       float
    quality:      float


def simulate(
    signals: list[Signal],
    klines_15m_by_symbol: dict[str, list[Candle]],
    initial_balance: float = INITIAL_BALANCE,
    max_hold_hours: float = MAX_HOLD_HOURS,
    grace_hours: float = GRACE_HOURS,
    sym_weekly_cap: int = SYM_WEEKLY_CAP,
    use_dynamic: bool = False,
    margin_mode: str = "percent",
    margin_cap: float = 0.0,
) -> dict:
    signals = sorted(signals, key=lambda s: s.entry_time)
    all_times = sorted({c.time for cs in klines_15m_by_symbol.values() for c in cs})
    idx_by_sym: dict[str, int] = {sym: 0 for sym in klines_15m_by_symbol}

    MS_7D = 7 * 24 * MS_1H
    balance   = initial_balance
    peak      = balance
    max_dd    = 0.0
    positions: list[Position] = []
    trades:    list[dict]     = []
    cooldown:  dict[str, int] = {}   # symbol → until_ms (4h 平仓冷却)
    # symbol → list of entry_time_ms for rolling weekly count
    sym_entry_log: dict[str, list[int]] = defaultdict(list)
    sig_queue = list(signals)
    next_sig_idx = 0
    next_id = 1
    monthly: dict[str, float] = {}
    by_sym:  dict[str, dict]  = defaultdict(lambda: {"trades":0,"wins":0,"pnl_usd":0.0})

    # ── 动态黑名单状态 ──────────────────────────────────────────────
    dyn_cooldown:  dict[str, int]       = {}   # symbol → until_ms (动态冷却)
    dyn_reason:    dict[str, str]       = {}   # symbol → 触发原因（调试用）
    sym_pnl_hist:  dict[str, deque]     = defaultdict(lambda: deque(maxlen=DYN_WIN))
    sym_7d_log:    dict[str, list]      = defaultdict(list)  # [(ts_ms, pnl)]
    daily_pnl:     dict[str, float]     = {}   # date_str → 当日 PnL
    halt_until:    int                  = 0    # Rule 4 全局停止到
    dyn_triggers:  dict[str, int]       = defaultdict(int)   # 规则触发计数

    for ts in all_times:
        current: dict[str, Candle] = {}
        for sym, cs in klines_15m_by_symbol.items():
            idx = idx_by_sym[sym]
            while idx + 1 < len(cs) and cs[idx + 1].time <= ts:
                idx += 1
            idx_by_sym[sym] = idx
            if cs[idx].time == ts:
                current[sym] = cs[idx]

        # 平仓检查
        still_open = []
        for pos in positions:
            candle = current.get(pos.symbol)
            if not candle:
                still_open.append(pos); continue
            held_h = (ts - pos.entry_time) / (MS_1H)
            grace  = held_h < grace_hours
            ep, ex, reason = None, None, None

            if not grace and candle.low <= pos.stop_loss:
                ep = pos.stop_loss; reason = "stop_loss"
            elif candle.high >= pos.take_profit:
                ep = pos.take_profit; reason = "take_profit"
            elif held_h >= max_hold_hours:
                ep = candle.close; reason = "max_hold"

            if ep is None:
                still_open.append(pos); continue

            exit_price = ep * (1 - SLIPPAGE_RATE)
            gross  = pos.notional_usd * (exit_price - pos.entry_price) / pos.entry_price
            fee    = pos.notional_usd * FEE_RATE
            net    = gross - fee
            balance = max(0.0, balance + net)
            peak = max(peak, balance)
            if peak > 0:
                max_dd = max(max_dd, (peak - balance) / peak * 100)
            month = datetime.fromtimestamp(ts / 1000, tz=TZ_UTC8).strftime("%Y-%m")
            monthly[month] = monthly.get(month, 0) + net
            by_sym[pos.symbol]["trades"] += 1
            by_sym[pos.symbol]["pnl_usd"] += net
            if net > 0:
                by_sym[pos.symbol]["wins"] += 1
            trades.append({
                "id": pos.id, "symbol": pos.symbol,
                "entry_time": datetime.fromtimestamp(pos.entry_time/1000, tz=TZ_UTC8).isoformat(),
                "exit_time":  datetime.fromtimestamp(ts/1000, tz=TZ_UTC8).isoformat(),
                "entry_price": round(pos.entry_price, 8),
                "exit_price":  round(exit_price, 8),
                "exit_reason": reason,
                "margin_usd":  round(pos.margin_usd, 4),
                "quality":     pos.quality,
                "sl_pct":      round(pos.sl_pct * 100, 3),
                "tp_pct":      round(pos.tp_pct * 100, 3),
                "pnl_usd":     round(net, 4),
                "direction":   "long",
            })
            cooldown[pos.symbol] = ts + int(COOLDOWN_HOURS * MS_1H)

            # ── 动态黑名单评估 ──────────────────────────────────────
            if use_dynamic:
                sym = pos.symbol
                sym_pnl_hist[sym].append(net)
                sym_7d_log[sym].append((ts, net))
                sym_7d_log[sym] = [(t, p) for t, p in sym_7d_log[sym] if t > ts - MS_7D]
                date_str = datetime.fromtimestamp(ts / 1000, tz=TZ_UTC8).strftime("%Y-%m-%d")
                daily_pnl[date_str] = daily_pnl.get(date_str, 0.0) + net

                hist = list(sym_pnl_hist[sym])
                cur_dyn = dyn_cooldown.get(sym, 0)

                # Rule 1: 近 DYN_WIN 笔亏损 >= DYN_LOSS_N → 冷却 DYN_COOL1 天
                if len(hist) >= DYN_WIN:
                    n_loss = sum(1 for p in hist if p <= 0)
                    if n_loss >= DYN_LOSS_N:
                        until = ts + DYN_COOL1 * 24 * MS_1H
                        if until > cur_dyn:
                            dyn_cooldown[sym] = until
                            dyn_reason[sym] = f"rule1:{n_loss}/{DYN_WIN}losses"
                            dyn_triggers["rule1"] += 1
                            cur_dyn = until

                # Rule 2: 近 DYN_WIN 笔 PF < DYN_PF_MIN → 冷却 DYN_COOL2 天
                if len(hist) >= DYN_WIN:
                    gp = sum(p for p in hist if p > 0)
                    gl = abs(sum(p for p in hist if p <= 0))
                    pf_win = gp / gl if gl > 0 else 99.0
                    if pf_win < DYN_PF_MIN:
                        until = ts + DYN_COOL2 * 24 * MS_1H
                        if until > cur_dyn:
                            dyn_cooldown[sym] = until
                            dyn_reason[sym] = f"rule2:pf={pf_win:.2f}"
                            dyn_triggers["rule2"] += 1
                            cur_dyn = until

                # Rule 3: 近 7 天单币亏损 > DYN_SYM_LOSS_PCT * balance → 冷却 DYN_COOL2 天
                week_pnl = sum(p for _, p in sym_7d_log[sym])
                if week_pnl < -balance * DYN_SYM_LOSS_PCT:
                    until = ts + DYN_COOL2 * 24 * MS_1H
                    if until > cur_dyn:
                        dyn_cooldown[sym] = until
                        dyn_reason[sym] = f"rule3:7d_pnl={week_pnl:.1f}"
                        dyn_triggers["rule3"] += 1

                # Rule 4: 当日总亏损 > DYN_DAILY_PCT * balance → 今天停止
                if daily_pnl.get(date_str, 0.0) < -balance * DYN_DAILY_PCT:
                    # 停到当天 UTC+8 23:59:59
                    dt = datetime.fromtimestamp(ts / 1000, tz=TZ_UTC8)
                    eod = dt.replace(hour=23, minute=59, second=59, microsecond=0)
                    halt_until = max(halt_until, int(eod.timestamp() * 1000))
                    dyn_triggers["rule4"] += 1

        positions = still_open

        # 开仓：处理此时间点到期的信号
        while next_sig_idx < len(sig_queue) and sig_queue[next_sig_idx].entry_time <= ts:
            sig = sig_queue[next_sig_idx]; next_sig_idx += 1
            if sig.entry_time != ts:
                continue
            if sig.symbol in {p.symbol for p in positions}:
                continue
            if cooldown.get(sig.symbol, 0) > ts:
                continue
            if len(positions) >= MAX_POSITIONS:
                continue
            # 动态黑名单检查
            if use_dynamic:
                if ts < halt_until:
                    continue
                if dyn_cooldown.get(sig.symbol, 0) > ts:
                    continue
            # 单 symbol 每周上限
            if sym_weekly_cap > 0:
                recent = sym_entry_log[sig.symbol]
                # 清理 7 天以前的记录
                sym_entry_log[sig.symbol] = [t for t in recent if t > ts - MS_7D]
                if len(sym_entry_log[sig.symbol]) >= sym_weekly_cap:
                    continue
            if margin_mode == "fixed":
                margin = fixed_margin_for(sig.quality)
            else:
                margin = balance * sig.position_pct
                if margin_mode == "capped" and margin_cap > 0:
                    margin = min(margin, margin_cap)
            margin = min(margin, balance)
            if margin <= 0:
                continue
            notional = margin * LEVERAGE
            entry = sig.entry_price * (1 + SLIPPAGE_RATE)
            fee   = notional * FEE_RATE
            balance = max(0.0, balance - fee)
            sl = entry * (1 - sig.sl_pct)
            tp = entry * (1 + sig.tp_pct)
            positions.append(Position(
                id=next_id, symbol=sig.symbol, entry_time=ts, entry_price=entry,
                margin_usd=margin, notional_usd=notional, stop_loss=sl, take_profit=tp,
                sl_pct=sig.sl_pct, tp_pct=sig.tp_pct, quality=sig.quality,
            ))
            sym_entry_log[sig.symbol].append(ts)
            next_id += 1

    # 未平仓：按最后收盘价强平
    for pos in positions:
        cs = klines_15m_by_symbol.get(pos.symbol, [])
        if cs:
            ep = cs[-1].close * (1 - SLIPPAGE_RATE)
            gross = pos.notional_usd * (ep - pos.entry_price) / pos.entry_price
            fee   = pos.notional_usd * FEE_RATE
            net   = gross - fee
            balance = max(0.0, balance + net)
            trades.append({
                "id": pos.id, "symbol": pos.symbol, "exit_reason": "end_of_backtest",
                "pnl_usd": round(net, 4), "quality": pos.quality, "direction": "long",
                "entry_time": datetime.fromtimestamp(pos.entry_time/1000, tz=TZ_UTC8).isoformat(),
            })

    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    gp = sum(t["pnl_usd"] for t in wins)
    gl = abs(sum(t["pnl_usd"] for t in losses))
    pf = round(gp / gl, 4) if gl else None
    roi = round((balance / initial_balance - 1) * 100, 4)
    rd  = round(roi / max_dd, 2) if max_dd > 0 else 0

    by_sym_out = {}
    for sym, d in by_sym.items():
        wr = round(d["wins"] / d["trades"] * 100, 2) if d["trades"] else 0
        by_sym_out[sym] = {**d, "win_rate_pct": wr, "pnl_usd": round(d["pnl_usd"], 4)}

    result: dict = {
        "summary": {
            "initial_balance": initial_balance,
            "final_balance":   round(balance, 4),
            "pnl_usd":         round(balance - initial_balance, 4),
            "roi_pct":         roi,
            "trades":          len(trades),
            "wins":            len(wins),
            "losses":          len(losses),
            "win_rate_pct":    round(len(wins) / len(trades) * 100, 2) if trades else 0,
            "profit_factor":   pf,
            "max_drawdown_pct": round(max_dd, 4),
            "roi_dd_ratio":    rd,
            "profit_months":   sum(1 for v in monthly.values() if v > 0),
            "total_months":    len(monthly),
            "monthly_pnl":     {m: round(v, 2) for m, v in sorted(monthly.items())},
        },
        "by_symbol": by_sym_out,
        "trades": trades,
    }
    if use_dynamic:
        result["dynamic_stats"] = {
            "rule1_triggers": dyn_triggers["rule1"],
            "rule2_triggers": dyn_triggers["rule2"],
            "rule3_triggers": dyn_triggers["rule3"],
            "rule4_triggers": dyn_triggers["rule4"],
            "sym_reasons": {k: v for k, v in dyn_reason.items()},
        }
    return result


# ── 主入口 ────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="S24-Ignition backtest")
    p.add_argument("--days",      type=int,   default=365)
    p.add_argument("--threshold", type=float, default=SPIKE_THRESHOLD, help="15m spike threshold (default 0.012 = 1.2%)")
    p.add_argument("--quality",   type=float, default=MIN_QUALITY,     help="Min signal quality (default 70)")
    p.add_argument("--rsi",       type=float, default=MIN_RSI,         help="Min RSI (default 50)")
    p.add_argument("--hold",      type=float, default=MAX_HOLD_HOURS,  help="Max hold hours (default 4)")
    p.add_argument("--tp-ratio",  type=float, default=TP_SL_RATIO,     help="TP/SL ratio (default 2.5)")
    p.add_argument("--symbols",   default="",  help="Comma-separated symbols (default: all COMMON)")
    p.add_argument("--exclude",   default="",  help="Symbols to exclude")
    p.add_argument("--label",     default="baseline")
    p.add_argument("--sym-cap",   type=int,   default=SYM_WEEKLY_CAP, help="Max trades per symbol per 7 days (0=off)")
    p.add_argument("--dynamic",    action="store_true", help="Enable dynamic blacklist rules")
    p.add_argument("--hour-only",  action="store_true", help="Only trade first 15m candle of each hour (:00)")
    p.add_argument("--margin-mode", choices=["percent", "fixed", "capped"], default="percent",
                   help="Position sizing: percent=compound, fixed=70/100/150U, capped=percent but cap margin")
    p.add_argument("--margin-cap", type=float, default=DEFAULT_MARGIN_CAP,
                   help="Max margin per trade when --margin-mode capped (default 300U)")
    p.add_argument("--no-save",    action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    api  = CachedAPI()

    END   = datetime(2026, 5, 14, 10, 0, 0, tzinfo=TZ_UTC8)
    START = END - timedelta(days=args.days)
    start_ms = int(START.timestamp() * 1000)
    end_ms   = int(END.timestamp() * 1000)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or COMMON_SYMBOLS
    exclude = {s.strip().upper() for s in args.exclude.split(",") if s.strip()}
    symbols = [s for s in symbols if s not in exclude]

    print(f"S24-Ignition  {START.date()} ~ {END.date()}  ({args.days}d)")
    dyn_flag = "ON" if args.dynamic else "off"
    hr_flag  = "ON" if args.hour_only else "off"
    margin_desc = args.margin_mode if args.margin_mode != "capped" else f"capped@{args.margin_cap:g}U"
    print(f"threshold={args.threshold:.3f}  quality>={args.quality}  rsi>={args.rsi}  hold={args.hold}h  tp={args.tp_ratio}x  sym_cap={args.sym_cap or 'off'}  dynamic={dyn_flag}  hour_only={hr_flag}  margin={margin_desc}")
    print(f"symbols={len(symbols)}  exclude={exclude or 'none'}")
    print()

    # 获取数据
    print("获取 15m K 线...")
    k15_by_sym: dict[str, list[Candle]] = {}
    for i, sym in enumerate(symbols, 1):
        cs = api.klines(sym, "15m", start_ms, end_ms)
        if cs:
            k15_by_sym[sym] = cs
        print(f"  {i}/{len(symbols)} {sym}: {len(cs)}根", end="\r")
    print(f"\n  完成: {len(k15_by_sym)} 个 symbol 有数据")

    print("获取 1h K 线（EMA 趋势）...")
    all_sigs: list[Signal] = []
    for sym, k15 in k15_by_sym.items():
        k1h = api.klines(sym, "1h", start_ms - MS_1H * 50, end_ms)
        ema_lookup = build_ema_lookup(k1h) if k1h else {}
        sigs = generate_signals(sym, k15, ema_lookup, args.threshold, args.rsi, args.quality,
                                hour_only=args.hour_only, tp_ratio=args.tp_ratio)
        all_sigs.extend(sigs)

    print(f"  信号总数: {len(all_sigs)}")
    print()

    # 模拟
    print("运行模拟...")
    result = simulate(all_sigs, k15_by_sym, max_hold_hours=args.hold,
                      sym_weekly_cap=args.sym_cap, use_dynamic=args.dynamic,
                      margin_mode=args.margin_mode, margin_cap=args.margin_cap)
    api.report()

    s = result["summary"]
    print()
    print("=" * 56)
    print(f"  S24-Ignition  [{args.label}]")
    print("=" * 56)
    print(f"  交易数:    {s['trades']}")
    print(f"  胜率:      {s['win_rate_pct']:.1f}%")
    print(f"  PnL:       {s['pnl_usd']:+.2f}U  (ROI {s['roi_pct']:.1f}%)")
    print(f"  最大回撤:  {s['max_drawdown_pct']:.2f}%")
    print(f"  PF:        {s['profit_factor']}")
    print(f"  ROI/DD:    {s['roi_dd_ratio']}")
    print(f"  盈利月:    {s['profit_months']}/{s['total_months']}")
    print()
    print("月度 PnL:")
    for m, v in s["monthly_pnl"].items():
        bar = ("▓" if v >= 0 else "░") * min(int(abs(v) / 30), 20)
        print(f"  {m}  {v:>+8.1f}U  {bar}")

    if args.dynamic and "dynamic_stats" in result:
        ds = result["dynamic_stats"]
        print()
        print("动态黑名单触发统计:")
        print(f"  Rule1(亏损率):  {ds['rule1_triggers']} 次")
        print(f"  Rule2(低PF):    {ds['rule2_triggers']} 次")
        print(f"  Rule3(7天亏损): {ds['rule3_triggers']} 次")
        print(f"  Rule4(日熔断):  {ds['rule4_triggers']} 次")
        if ds["sym_reasons"]:
            print(f"  最终冷却状态: {ds['sym_reasons']}")

    print()
    print("Top symbols:")
    by_sym = result["by_symbol"]
    for sym, d in sorted(by_sym.items(), key=lambda x: x[1]["pnl_usd"], reverse=True)[:8]:
        print(f"  {sym:<16} {d['trades']:>4}笔  wr={d['win_rate_pct']:.0f}%  pnl={d['pnl_usd']:>+8.1f}U")

    if not args.no_save:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"ignition_{args.days}d_{args.label}.json"
        out = {
            "strategy": "S24-Ignition",
            "params": {
                "days": args.days, "threshold": args.threshold,
                "min_quality": args.quality, "min_rsi": args.rsi,
                "max_hold_hours": args.hold, "tp_sl_ratio": args.tp_ratio,
                "sym_weekly_cap": args.sym_cap, "dynamic": args.dynamic,
                "hour_only": args.hour_only, "margin_mode": args.margin_mode,
                "margin_cap": args.margin_cap,
                "symbols": symbols, "exclude": list(exclude),
                "position_pct": {"low": POSITION_PCT_LOW, "mid": POSITION_PCT_MID, "high": POSITION_PCT_HIGH},
            },
            "window": {"start": START.isoformat(), "end": END.isoformat()},
            **result,
        }
        (OUT_DIR / fname).write_text(json.dumps(out, ensure_ascii=False, indent=2))
        print(f"\n保存到: {OUT_DIR / fname}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
