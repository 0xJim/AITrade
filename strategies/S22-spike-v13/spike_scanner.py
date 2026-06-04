#!/usr/bin/env python3
"""
15分钟异动(Spike)信号扫描器 v2 — 数据驱动优化版
基于26笔历史订单分析结论优化:
  1. 只做多 (做空0胜率, -9U)
  2. EMA必须上升 (顺趋势+254U vs 逆趋势-9U)
  3. RSI≥50 (RSI<50全亏, RSI>70胜率60%)
  4. 信号强度B→A级 (S级无EMA确认)
  5. 持仓时间上限8h (>6h表现急剧下降)

K线格式: [timestamp, open, high, low, close, volume, close_time, quote_vol, trades, ...]
"""

import sys
sys.path.insert(0, '/home/ubuntu/.hermes/trading')

from datetime import datetime, timezone, timedelta
from config import *
from binance_api import get_klines, get_qualified_symbols, get_funding_rates, now_str

TZ_UTC8 = timezone(timedelta(hours=8))

# === Spike 信号参数 ===
SPIKE_THRESHOLD = 0.01       # 15m K线涨跌幅阈值 1%
SPIKE_MIN_ATR = 0.005        # 最小ATR 0.5%
SPIKE_COOLDOWN_SECS = 3600   # 同向冷却1小时


def calc_atr_pct(klines_15m, period=14):
    """从15m K线数组计算ATR%"""
    if len(klines_15m) < period + 1:
        return 0
    atrs = []
    for i in range(max(1, len(klines_15m) - period), len(klines_15m)):
        hi = float(klines_15m[i][2])
        lo = float(klines_15m[i][3])
        prev_c = float(klines_15m[i - 1][4])
        tr = max(hi - lo, abs(hi - prev_c), abs(lo - prev_c))
        atrs.append(tr)
    if not atrs:
        return 0
    atr = sum(atrs) / len(atrs)
    close = float(klines_15m[-1][4])
    return atr / close if close > 0 else 0


def calc_ema(prices, period):
    """计算EMA"""
    if len(prices) < period:
        return None
    mult = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = (p - ema) * mult + ema
    return ema


def calc_rsi(klines, period=14):
    """从K线计算RSI"""
    closes = [float(k[4]) for k in klines]
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    # 取最后period个
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def scan_spike_signals(open_symbols, cooldowns, spike_cooldowns=None):
    """
    扫描15分钟异动信号 — 数据驱动优化版

    新增过滤链 (基于26笔历史数据):
    1. SPIKE_LONG_ONLY: 只做多 (做空全亏)
    2. SPIKE_REQUIRE_EMA_UP: 1h EMA必须多头排列
    3. SPIKE_MIN_RSI: RSI≥50才做多

    Returns: list of candidate dicts
    """
    if spike_cooldowns is None:
        spike_cooldowns = {}

    tickers = get_qualified_symbols()
    funding = get_funding_rates()
    now = datetime.now(TZ_UTC8)
    candidates = []

    for t in tickers:
        sym = t["symbol"]
        if sym in open_symbols:
            continue

        # 冷却检查
        last = cooldowns.get(sym)
        if last:
            try:
                ld = datetime.fromisoformat(last)
                if ld.tzinfo is None:
                    ld = ld.replace(tzinfo=TZ_UTC8)
                if (now - ld).total_seconds() < COOLDOWN_HOURS * 3600:
                    continue
            except:
                pass

        try:
            # 获取30根15m K线
            klines_15m = get_klines(sym, "15m", 30)
            if not klines_15m or len(klines_15m) < 15:
                continue

            # 最后一根已完成的15m K线
            last_closed = klines_15m[-2]
            o = float(last_closed[1])
            c = float(last_closed[4])

            # 15m K线涨跌幅
            chg_pct_15m = (c - o) / o if o > 0 else 0

            # 异动阈值
            if abs(chg_pct_15m) < SPIKE_THRESHOLD:
                continue

            # ★ 新过滤1: 只做多
            if SPIKE_LONG_ONLY and chg_pct_15m < 0:
                continue

            # ATR过滤
            atr_pct = calc_atr_pct(klines_15m, 14)
            if atr_pct < SPIKE_MIN_ATR:
                continue

            # 方向
            direction = "long" if chg_pct_15m > 0 else "short"

            # 异动冷却检查
            sc = spike_cooldowns.get(sym, {})
            last_spike_time = sc.get(direction)
            if last_spike_time:
                try:
                    lt = datetime.fromisoformat(last_spike_time)
                    if lt.tzinfo is None:
                        lt = lt.replace(tzinfo=TZ_UTC8)
                    if (now - lt).total_seconds() < SPIKE_COOLDOWN_SECS:
                        continue
                except:
                    pass

            # ★ 新过滤2: 1h EMA趋势确认
            ema_trend = "neutral"
            if SPIKE_REQUIRE_EMA_UP and direction == "long":
                klines_1h = get_klines(sym, "1h", 25)
                if klines_1h and len(klines_1h) >= 21:
                    closes_1h = [float(k[4]) for k in klines_1h]
                    ema9 = calc_ema(closes_1h, 9)
                    ema21 = calc_ema(closes_1h, 21)
                    if ema9 and ema21:
                        if ema9 > ema21:
                            ema_trend = "up"
                        elif ema9 < ema21:
                            ema_trend = "down"
                        else:
                            ema_trend = "neutral"
                    # 做多必须EMA上升
                    if ema_trend != "up":
                        continue
                else:
                    # 拿不到1h数据则跳过
                    continue

            # ★ 新过滤3: RSI门槛
            rsi_val = calc_rsi(klines_15m, 14)
            if SPIKE_MIN_RSI and direction == "long":
                if rsi_val is None or rsi_val < SPIKE_MIN_RSI:
                    continue

            # 信号强度 (只保留A和B, S级需要更强确认)
            abs_chg = abs(chg_pct_15m)
            strength = "S" if abs_chg >= 0.03 else "A" if abs_chg >= 0.02 else "B"

            # 动态止损: 基于ATR
            sl_pct = max(atr_pct * 1.5, 0.03)  # 最小3%
            tp_pct = sl_pct * 2.5
            rr = round(tp_pct / sl_pct, 2)

            price = float(t.get("lastPrice", 0))
            vol = float(t.get("quoteVolume", 0))
            fr = funding.get(sym, 0)
            chg_24h = float(t.get("priceChangePercent", 0))

            reason_parts = [f"15m异动+{chg_pct_15m*100:.1f}%"]
            reason_parts.append(f"ATR={atr_pct*100:.1f}%")
            if ema_trend == "up":
                reason_parts.append("EMA多头排列")
            if rsi_val:
                if rsi_val > 70:
                    reason_parts.append(f"RSI={rsi_val:.0f}强势")
                elif rsi_val < 35:
                    reason_parts.append(f"RSI={rsi_val:.0f}超卖")

            candidates.append({
                "symbol": sym,
                "type": "spike",
                "direction": direction,
                "strength": strength,
                "price": price,
                "fr": fr,
                "change": chg_24h,
                "vol": vol,
                "sl_pct": sl_pct,
                "tp_pct": tp_pct,
                "rr": rr,
                "reason": " | ".join(reason_parts),
                "chg_pct_15m": chg_pct_15m,
                "atr_pct": atr_pct,
                "rsi_15m": rsi_val,
                "ema_trend_1h": ema_trend,
            })
        except Exception:
            continue

    return candidates


if __name__ == "__main__":
    print(f"=== Spike信号扫描测试 v2 [{now_str()}] ===")
    results = scan_spike_signals(set(), {})
    print(f"发现 {len(results)} 个spike信号:")
    for r in results[:15]:
        rsi_str = f"RSI={r.get('rsi_15m',0):.0f}" if r.get('rsi_15m') else ""
        ema_str = f"EMA={r.get('ema_trend_1h','?')}" if r.get('ema_trend_1h') else ""
        print(f"  {r['symbol']:20s} {r['direction']:5s} {r['strength']}"
              f" | {r['reason']:50s} | SL={r['sl_pct']*100:.1f}% TP={r['tp_pct']*100:.1f}% RR={r['rr']} {rsi_str} {ema_str}")
