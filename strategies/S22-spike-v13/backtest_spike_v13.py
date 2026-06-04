#!/usr/bin/env python3
"""
Spike v13 回测对比脚本
A) 原始Spike（无EMA/RSI/质量门槛过滤）— 做多+做空
B) v13优化Spike（只做多+EMA多头+RSI≥50+质量≥70+8h上限+高质量加仓）

基于backtest_1year.py框架，拉取币安正式API数据
初始资金1000U, 3倍杠杆, 回测180天
"""

import json
import time
import bisect
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

FAPI_LIVE = "https://fapi.binance.com"
TZ_UTC8 = timezone(timedelta(hours=8))

# === 回测参数 ===
INITIAL_BALANCE = 1000.0
LEVERAGE = 3
RISK_PER_TRADE = 0.02       # 每笔风险2%
MAX_POSITIONS = 3
GRACE_PERIOD_HOURS = 4       # 入场4h宽限期不扫止损

# === Spike 信号参数 ===
SPIKE_THRESHOLD = 0.01       # 15m涨跌幅≥1%

# v13 优化参数
SPIKE_MIN_RSI = 50           # RSI≥50才做多
SPIKE_MIN_QUALITY = 70       # 信号质量≥70
SPIKE_MAX_HOLD_HOURS = 8     # 持仓上限8h
SPIKE_BOOST_QUALITY = 80     # 质量≥80加仓
SPIKE_BOOST_MULT = 1.3       # 加仓倍数

# 冷却
SYMBOL_COOLDOWN_HOURS = 4    # 同币种冷却4h
SPIKE_COOLDOWN_HOURS = 1     # Spike冷却1h

# 止损止盈
ATR_SL_MULT = 1.5            # ATR×1.5
MIN_SL_PCT = 0.03            # 最小止损3%
TP_SL_RATIO = 2.5            # 止盈=止损×2.5

# 技术指标
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
ATR_PERIOD = 14

# 回测时间: 180天
END_TIME = datetime(2026, 5, 14, 23, 59, tzinfo=TZ_UTC8)
START_TIME = END_TIME - timedelta(days=180)

NUM_SYMBOLS = 20  # Top 20币种

OUTPUT_DIR = Path.home() / ".hermes" / "trading"


# ============================================================
# API functions
# ============================================================
def api_get(endpoint, params=None):
    url = FAPI_LIVE + endpoint
    for _ in range(3):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                time.sleep(2)
                continue
        except Exception:
            time.sleep(1)
    return None


def get_klines_ts(symbol, interval, start_ts, end_ts, limit=1500):
    all_data = []
    cur_start = start_ts
    while cur_start < end_ts:
        data = api_get("/fapi/v1/klines", {
            "symbol": symbol, "interval": interval,
            "startTime": cur_start, "endTime": end_ts, "limit": limit,
        })
        if not data or not isinstance(data, list):
            break
        all_data.extend(data)
        if len(data) < limit:
            break
        cur_start = int(data[-1][0]) + 1
        time.sleep(0.15)
    return [{
        "time": int(k[0]), "open": float(k[1]), "high": float(k[2]),
        "low": float(k[3]), "close": float(k[4]), "volume": float(k[7]),
    } for k in all_data]


def get_qualified_symbols(n=NUM_SYMBOLS):
    tickers = api_get("/fapi/v1/ticker/24hr") or []
    exclude = {"BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT", "BTCDOMUSDT", "BTCSTUSDT"}
    qualified = []
    for t in tickers:
        sym = t.get("symbol", "")
        vol = float(t.get("quoteVolume", 0))
        price = float(t.get("lastPrice", 0))
        if sym.endswith("USDT") and sym not in exclude and vol > 100_000_000 and price > 0.001:
            qualified.append({"symbol": sym, "volume": vol, "price": price})
    qualified.sort(key=lambda x: x["volume"], reverse=True)
    return qualified[:n]


# ============================================================
# Technical indicators
# ============================================================
def calc_ema_series(closes, period):
    """返回EMA序列，与closes等长，前period-1个为None"""
    if len(closes) < period:
        return [None] * len(closes)
    k = 2 / (period + 1)
    ema = [None] * (period - 1)
    first = sum(closes[:period]) / period
    ema.append(first)
    for c in closes[period:]:
        ema.append(c * k + ema[-1] * (1 - k))
    return ema


def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [max(0, d) for d in deltas]
    losses = [max(0, -d) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        return 100.0
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def calc_atr_pct(klines, period=14):
    """从kline列表计算ATR% (使用最后period个TR)"""
    if len(klines) < period + 1:
        return 0.0
    trs = []
    for i in range(max(1, len(klines) - period), len(klines)):
        h = klines[i]["high"]
        l = klines[i]["low"]
        prev_c = klines[i-1]["close"]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    if not trs:
        return 0.0
    atr = sum(trs) / len(trs)
    close = klines[-1]["close"]
    return atr / close if close > 0 else 0.0


# ============================================================
# Signal quality scoring
# ============================================================
def calc_signal_quality(chg_pct, atr_pct, klines_before, current_vol, btc_bullish):
    """
    信号质量评分 (满分100):
    - 量价 (0-40): 15m涨跌幅×1000/ATR, 归一化
    - 形态 (0-30): 连续K线同向数量, 归一化
    - 订单流 (0-20): 成交量vs MA20量比, 归一化
    - 宏观 (0-10): BTC当日涨跌方向一致+10
    """
    score = 0.0

    # 1. 量价得分 (0-40)
    if atr_pct > 0:
        vp_raw = abs(chg_pct) * 1000 / atr_pct  # e.g. 0.01 * 1000 / 0.02 = 500
        vp_score = min(vp_raw / 1000.0, 1.0) * 40
    else:
        vp_score = 0
    score += vp_score

    # 2. 形态得分 (0-30): 看连续K线同向数量
    consecutive = 0
    for i in range(len(klines_before) - 1, -1, -1):
        k = klines_before[i]
        is_bullish = k["close"] > k["open"]
        if (chg_pct > 0 and is_bullish) or (chg_pct < 0 and not is_bullish):
            consecutive += 1
        else:
            break
    pattern_score = min(consecutive / 5.0, 1.0) * 30
    score += pattern_score

    # 3. 订单流得分 (0-20): 成交量 vs MA20量比
    if len(klines_before) >= 20:
        vol_ma20 = sum(k["volume"] for k in klines_before[-20:]) / 20
    elif len(klines_before) > 0:
        vol_ma20 = sum(k["volume"] for k in klines_before) / len(klines_before)
    else:
        vol_ma20 = 1
    if vol_ma20 > 0:
        vol_ratio = current_vol / vol_ma20
        of_score = min(vol_ratio / 5.0, 1.0) * 20
    else:
        of_score = 0
    score += of_score

    # 4. 宏观得分 (0-10): BTC当日涨跌方向一致
    if (btc_bullish and chg_pct > 0) or (not btc_bullish and chg_pct < 0):
        score += 10

    return round(score, 1)


# ============================================================
# Data fetching
# ============================================================
def fetch_all_data():
    print("=" * 70)
    print("📊 Spike v13 回测对比")
    print(f"时间: {START_TIME.strftime('%Y-%m-%d')} ~ {END_TIME.strftime('%Y-%m-%d')} ({(END_TIME-START_TIME).days}天)")
    print(f"资金: ${INITIAL_BALANCE:.0f} | 杠杆: {LEVERAGE}x | Top {NUM_SYMBOLS}币种")
    print("=" * 70)

    start_ts = int(START_TIME.timestamp() * 1000)
    end_ts = int(END_TIME.timestamp() * 1000)
    # 预热: 15m需要约50根（12.5h）
    warmup_ts = start_ts - 50 * 15 * 60 * 1000

    # 1. 获取币种列表
    print("\n🔍 获取活跃合约列表...")
    symbols_info = get_qualified_symbols(NUM_SYMBOLS)
    symbols = [s["symbol"] for s in symbols_info]
    print(f"  {len(symbols)} 币种: {', '.join(symbols[:8])}...")

    # 2. BTC 1h数据 (用于宏观方向)
    print("\n📈 获取BTC 1h历史...")
    btc_klines = get_klines_ts("BTCUSDT", "1h", start_ts - 48*3600*1000, end_ts)
    print(f"  BTC 1h: {len(btc_klines)} 根")

    # BTC每日方向
    btc_daily_dir = {}
    btc_by_date = defaultdict(list)
    for k in btc_klines:
        dt = datetime.fromtimestamp(k["time"]/1000, tz=TZ_UTC8)
        btc_by_date[dt.strftime("%Y-%m-%d")].append(k)
    for date_str, klist in btc_by_date.items():
        if len(klist) >= 2:
            chg = (klist[-1]["close"] - klist[0]["open"]) / klist[0]["open"]
            btc_daily_dir[date_str] = chg >= 0
    print(f"  BTC日方向: {len(btc_daily_dir)}天")

    # 3. 各币种K线数据
    print(f"\n📉 获取{len(symbols)}币种K线(15m+1h)...")
    all_klines_15m = {}
    all_klines_1h = {}
    for i, sym in enumerate(symbols):
        kl15 = get_klines_ts(sym, "15m", warmup_ts, end_ts)
        if kl15 and len(kl15) > 100:
            all_klines_15m[sym] = kl15
        time.sleep(0.1)
        kl1 = get_klines_ts(sym, "1h", start_ts - 48*3600*1000, end_ts)
        if kl1 and len(kl1) > 30:
            all_klines_1h[sym] = kl1
        time.sleep(0.1)
        if (i + 1) % 5 == 0:
            print(f"  [{i+1}/{len(symbols)}] 15m={len(all_klines_15m)} 1h={len(all_klines_1h)}")

    common_syms = sorted(set(all_klines_15m.keys()) & set(all_klines_1h.keys()))
    print(f"\n  有效币种: {len(common_syms)}")

    # 4. 预计算1h EMA序列
    print("\n⚙️ 预计算1h EMA...")
    ema_lookup = {}  # sym -> [(ts, ema9, ema21), ...]
    for sym in common_syms:
        closes_1h = [k["close"] for k in all_klines_1h[sym]]
        ema9_series = calc_ema_series(closes_1h, EMA_FAST)
        ema21_series = calc_ema_series(closes_1h, EMA_SLOW)
        entries = []
        for j, k in enumerate(all_klines_1h[sym]):
            e9 = ema9_series[j]
            e21 = ema21_series[j]
            if e9 is not None and e21 is not None:
                entries.append((k["time"], e9 > e21, e9, e21))
            else:
                entries.append((k["time"], False, e9, e21))
        ema_lookup[sym] = entries

    return {
        "symbols": common_syms,
        "all_klines_15m": all_klines_15m,
        "all_klines_1h": all_klines_1h,
        "ema_lookup": ema_lookup,
        "btc_daily_dir": btc_daily_dir,
        "start_ts": start_ts,
        "end_ts": end_ts,
    }


# ============================================================
# Signal generation
# ============================================================
def generate_signals(data):
    """为每个币种扫描15m K线生成spike信号"""
    symbols = data["symbols"]
    all_klines_15m = data["all_klines_15m"]
    ema_lookup = data["ema_lookup"]
    btc_daily_dir = data["btc_daily_dir"]
    start_ts = data["start_ts"]
    end_ts = data["end_ts"]

    raw_signals = []    # Mode A: 原始(无v13过滤)
    v13_signals = []    # Mode B: v13优化

    print("\n⚡ 扫描Spike信号...")
    total_candles = 0

    for sym in symbols:
        klines = all_klines_15m[sym]
        # 预计算1h EMA查询用的时间列表
        ema_times = [e[0] for e in ema_lookup[sym]]
        ema_entries = ema_lookup[sym]

        for i in range(ATR_PERIOD + 1, len(klines)):
            k = klines[i]
            ts = k["time"]
            if ts < start_ts or ts > end_ts:
                continue
            total_candles += 1

            o = k["open"]
            c = k["close"]
            if o <= 0:
                continue

            chg_pct = (c - o) / o
            if abs(chg_pct) < SPIKE_THRESHOLD:
                continue

            direction = "long" if chg_pct > 0 else "short"

            # ATR
            atr_pct = calc_atr_pct(klines[:i+1], ATR_PERIOD)
            if atr_pct <= 0:
                continue

            # 15m RSI
            closes = [kk["close"] for kk in klines[:i+1]]
            rsi_15m = calc_rsi(closes, RSI_PERIOD)

            # 1h EMA多头排列: 二分查找最新1h数据
            ema_bullish = False
            idx = bisect.bisect_right(ema_times, ts) - 1
            if idx >= 0:
                ema_bullish = ema_entries[idx][1]  # (ts, is_bullish, e9, e21)

            # BTC方向
            dt = datetime.fromtimestamp(ts / 1000, tz=TZ_UTC8)
            date_str = dt.strftime("%Y-%m-%d")
            btc_bullish = btc_daily_dir.get(date_str, True)

            # 信号质量
            quality = calc_signal_quality(
                chg_pct, atr_pct, klines[:i], k["volume"], btc_bullish
            )

            # 止损止盈
            sl_pct = max(atr_pct * ATR_SL_MULT, MIN_SL_PCT)
            tp_pct = sl_pct * TP_SL_RATIO

            signal = {
                "ts": ts,
                "dt": dt,
                "symbol": sym,
                "direction": direction,
                "price": c,
                "chg_pct": chg_pct,
                "atr_pct": atr_pct,
                "rsi_15m": round(rsi_15m, 1),
                "ema_bullish": ema_bullish,
                "quality": quality,
                "sl_pct": round(sl_pct, 4),
                "tp_pct": round(tp_pct, 4),
            }

            # Mode A: 原始信号 (只要≥1%就触发, 多空都做)
            raw_signals.append(signal)

            # Mode B: v13过滤 (只做多 + EMA多头 + RSI≥50 + 质量≥70)
            if (direction == "long"
                    and ema_bullish
                    and rsi_15m >= SPIKE_MIN_RSI
                    and quality >= SPIKE_MIN_QUALITY):
                v13_signals.append(signal)

    print(f"  扫描{total_candles}根15m K线")
    print(f"  原始Spike信号: {len(raw_signals)}")
    print(f"  v13过滤后信号: {len(v13_signals)}")

    # 打印一些v13信号示例
    if v13_signals:
        print(f"\n  v13信号示例 (前5个):")
        for sig in v13_signals[:5]:
            print(f"    {sig['dt'].strftime('%m-%d %H:%M')} {sig['symbol']:15s} "
                  f"+{sig['chg_pct']*100:.1f}% RSI={sig['rsi_15m']:.0f} "
                  f"Q={sig['quality']:.0f} ATR={sig['atr_pct']*100:.1f}%")

    return raw_signals, v13_signals


# ============================================================
# Trading simulation
# ============================================================
def simulate(signals, all_klines_15m, mode_name, max_hold_hours, boost_quality, boost_mult):
    """
    模拟交易
    signals: sorted list of signal dicts
    max_hold_hours: 最大持仓时间
    boost_quality: 质量门槛(≥此值加仓), 999=不启用
    boost_mult: 加仓倍数
    """
    print(f"\n{'='*70}")
    print(f"📊 模拟: {mode_name}")
    print(f"  最大持仓: {max_hold_hours}h | 加仓门槛: {boost_quality} (×{boost_mult})")
    print(f"{'='*70}")

    # 按时间排序信号
    signals_sorted = sorted(signals, key=lambda x: x["ts"])

    # 按时间索引信号
    signals_by_ts = defaultdict(list)
    for sig in signals_sorted:
        signals_by_ts[sig["ts"]].append(sig)

    # 收集所有15m时间戳
    all_ts = sorted(set(
        k["time"] for klist in all_klines_15m.values() for k in klist
        if data["start_ts"] <= k["time"] <= data["end_ts"]
    ))

    # 为每个symbol建时间索引用于快速查找
    sym_kline_map = {}
    for sym, klines in all_klines_15m.items():
        sym_kline_map[sym] = {k["time"]: k for k in klines}

    # 状态变量
    balance = INITIAL_BALANCE
    positions = []
    trades = []
    cooldowns = {}          # sym -> last_close_ts
    spike_cooldowns = {}    # sym -> last_signal_ts
    max_equity = INITIAL_BALANCE
    max_drawdown = 0.0
    monthly_pnl = defaultdict(float)

    for step_i, ts in enumerate(all_ts):
        dt = datetime.fromtimestamp(ts / 1000, tz=TZ_UTC8)
        month_str = dt.strftime("%Y-%m")

        # === 1. 检查持仓 ===
        to_close = []
        for pos in positions:
            sym = pos["symbol"]
            kline = sym_kline_map.get(sym, {}).get(ts)
            if not kline:
                continue

            fill_price = kline["close"]
            triggered = None
            entry = pos["entry_price"]
            hours_held = (ts - pos["entry_ts"]) / 3600 / 1000

            # 止损 (宽限期外)
            if hours_held >= GRACE_PERIOD_HOURS:
                if pos["direction"] == "long":
                    if kline["low"] <= pos["stop_loss"]:
                        triggered = "止损"
                        fill_price = pos["stop_loss"]
                else:
                    if kline["high"] >= pos["stop_loss"]:
                        triggered = "止损"
                        fill_price = pos["stop_loss"]

            # 止盈
            if not triggered:
                if pos["direction"] == "long":
                    if kline["high"] >= pos["take_profit"]:
                        triggered = "止盈"
                        fill_price = pos["take_profit"]
                else:
                    if kline["low"] <= pos["take_profit"]:
                        triggered = "止盈"
                        fill_price = pos["take_profit"]

            # 超时平仓
            if not triggered and hours_held >= max_hold_hours:
                triggered = f"超时{hours_held:.0f}h"
                fill_price = kline["close"]

            if triggered:
                to_close.append((pos, fill_price, triggered, dt, month_str))

        # 执行平仓
        for pos, price, reason, close_dt, close_month in to_close:
            entry = pos["entry_price"]
            qty = pos["position_usd"]
            if pos["direction"] == "long":
                pnl_pct = (price - entry) / entry * 100 * LEVERAGE
            else:
                pnl_pct = (entry - price) / entry * 100 * LEVERAGE
            pnl_usd = pnl_pct / 100 * qty

            balance += pnl_usd
            cooldowns[pos["symbol"]] = ts
            monthly_pnl[close_month] += pnl_usd

            trades.append({
                "id": pos["id"],
                "symbol": pos["symbol"],
                "direction": pos["direction"],
                "entry_price": round(entry, 6),
                "exit_price": round(price, 6),
                "entry_time": pos["entry_dt"].strftime("%Y-%m-%d %H:%M"),
                "exit_time": close_dt.strftime("%Y-%m-%d %H:%M"),
                "exit_reason": reason,
                "pnl_usd": round(pnl_usd, 2),
                "pnl_pct": round(pnl_pct, 2),
                "position_usd": qty,
                "quality": pos.get("quality", 0),
                "hours_held": round((ts - pos["entry_ts"]) / 3600 / 1000, 1),
            })
            positions.remove(pos)

        # === 2. 开新仓 ===
        for sig in signals_by_ts.get(ts, []):
            if balance < 50:
                break
            sym = sig["symbol"]

            # 已有持仓?
            if any(p["symbol"] == sym for p in positions):
                continue

            # 最大持仓
            if len(positions) >= MAX_POSITIONS:
                break

            # 同币种冷却
            if sym in cooldowns:
                if (ts - cooldowns[sym]) / 3600 / 1000 < SYMBOL_COOLDOWN_HOURS:
                    continue

            # Spike冷却
            if sym in spike_cooldowns:
                if (ts - spike_cooldowns[sym]) / 3600 / 1000 < SPIKE_COOLDOWN_HOURS:
                    continue

            # 仓位计算
            sl_pct = sig["sl_pct"]
            risk_usd = balance * RISK_PER_TRADE
            pos_usd = risk_usd / (sl_pct * LEVERAGE)
            pos_usd = min(pos_usd, balance * 0.25)  # 最大25%
            pos_usd = max(pos_usd, 20)               # 最小$20

            # 高质量加仓
            if sig.get("quality", 0) >= boost_quality:
                pos_usd *= boost_mult
                pos_usd = min(pos_usd, balance * 0.35)

            price = sig["price"]
            if sig["direction"] == "long":
                sl_price = price * (1 - sl_pct)
                tp_price = price * (1 + sig["tp_pct"])
            else:
                sl_price = price * (1 + sl_pct)
                tp_price = price * (1 - sig["tp_pct"])

            position = {
                "id": f"{len(trades)+1:04d}",
                "symbol": sym,
                "direction": sig["direction"],
                "entry_price": price,
                "entry_ts": ts,
                "entry_dt": sig["dt"],
                "position_usd": round(pos_usd, 2),
                "stop_loss": round(sl_price, 8),
                "take_profit": round(tp_price, 8),
                "quality": sig.get("quality", 0),
            }
            positions.append(position)
            spike_cooldowns[sym] = ts

        # === 3. 净值和回撤 ===
        unrealized = 0
        for pos in positions:
            sym = pos["symbol"]
            kline = sym_kline_map.get(sym, {}).get(ts)
            if kline:
                entry = pos["entry_price"]
                if pos["direction"] == "long":
                    raw = (kline["close"] - entry) / entry
                else:
                    raw = (entry - kline["close"]) / entry
                unrealized += raw * pos["position_usd"] * LEVERAGE

        equity = balance + unrealized
        if equity > max_equity:
            max_equity = equity
        dd = (max_equity - equity) / max_equity * 100 if max_equity > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

        if (step_i + 1) % 3000 == 0:
            print(f"  [{step_i+1}/{len(all_ts)}] {dt.strftime('%m-%d %H:%M')} "
                  f"余额=${balance:.0f} 持仓{len(positions)} 交易{len(trades)}笔")

    # 回测结束强制平仓
    for pos in positions[:]:
        sym = pos["symbol"]
        last_kl = all_klines_15m.get(sym, [{}])[-1]
        price = last_kl.get("close", pos["entry_price"])
        entry = pos["entry_price"]
        qty = pos["position_usd"]
        if pos["direction"] == "long":
            pnl_pct = (price - entry) / entry * 100 * LEVERAGE
        else:
            pnl_pct = (entry - price) / entry * 100 * LEVERAGE
        pnl_usd = pnl_pct / 100 * qty
        balance += pnl_usd
        trades.append({
            "id": pos["id"], "symbol": sym, "direction": pos["direction"],
            "entry_price": round(entry, 6), "exit_price": round(price, 6),
            "entry_time": pos["entry_dt"].strftime("%Y-%m-%d %H:%M"),
            "exit_time": "回测结束", "exit_reason": "回测结束",
            "pnl_usd": round(pnl_usd, 2), "pnl_pct": round(pnl_pct, 2),
            "position_usd": qty, "quality": pos.get("quality", 0),
        })

    return {
        "mode": mode_name,
        "trades": trades,
        "balance": round(balance, 2),
        "max_drawdown": round(max_drawdown, 1),
        "monthly_pnl": dict(monthly_pnl),
    }


# ============================================================
# Report
# ============================================================
def print_report(results, label):
    """打印单个模式的结果"""
    trades = results["trades"]
    balance = results["balance"]
    max_dd = results["max_drawdown"]
    monthly = results["monthly_pnl"]

    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    total_pnl = sum(t["pnl_usd"] for t in trades)
    win_pnl = sum(t["pnl_usd"] for t in wins) if wins else 0
    loss_pnl = sum(t["pnl_usd"] for t in losses) if losses else -1

    print(f"\n{'='*70}")
    print(f"📊 {label}")
    print(f"{'='*70}")
    print(f"  交易笔数:   {len(trades)}")
    print(f"  盈利/亏损:  {len(wins)}/{len(losses)}")
    if trades:
        print(f"  胜率:       {len(wins)/len(trades)*100:.1f}%")
    print(f"  总PnL:      {total_pnl:+.2f} USDT ({total_pnl/INITIAL_BALANCE*100:+.1f}%)")
    print(f"  最终余额:   ${balance:.2f}")
    print(f"  最大回撤:   -{max_dd:.1f}%")

    if wins and losses:
        avg_win = win_pnl / len(wins)
        avg_loss = abs(loss_pnl / len(losses))
        if avg_loss > 0:
            print(f"  盈亏比:     {avg_win/avg_loss:.2f}")
        print(f"  平均盈利:   +{avg_win:.2f}U")
        print(f"  平均亏损:   {loss_pnl/len(losses):.2f}U")

    # 月胜率
    if monthly:
        months_profit = sum(1 for v in monthly.values() if v > 0)
        months_total = len(monthly)
        print(f"\n  月度统计 ({months_total}个月):")
        print(f"  月胜率:     {months_profit}/{months_total} = {months_profit/months_total*100:.1f}%")
        for m in sorted(monthly.keys()):
            v = monthly[m]
            emoji = "✅" if v > 0 else "❌"
            print(f"    {m}: {emoji} {v:+.2f}U")

    # 按方向统计
    by_dir = defaultdict(lambda: {"win": 0, "loss": 0, "pnl": 0})
    for t in trades:
        d = t["direction"]
        if t["pnl_usd"] > 0:
            by_dir[d]["win"] += 1
        else:
            by_dir[d]["loss"] += 1
        by_dir[d]["pnl"] += t["pnl_usd"]
    print(f"\n  按方向:")
    for d in ["long", "short"]:
        s = by_dir[d]
        total_d = s["win"] + s["loss"]
        if total_d > 0:
            wr = s["win"] / total_d * 100
            print(f"    {d:5s}: {total_d}笔 胜率{wr:.0f}% PnL {s['pnl']:+.2f}U")

    # 按平仓原因
    by_reason = defaultdict(lambda: {"count": 0, "pnl": 0, "wins": 0})
    for t in trades:
        r = t["exit_reason"]
        if r.startswith("超时"):
            r = "超时平仓"
        by_reason[r]["count"] += 1
        by_reason[r]["pnl"] += t["pnl_usd"]
        if t["pnl_usd"] > 0:
            by_reason[r]["wins"] += 1
    print(f"\n  按平仓原因:")
    for r, s in sorted(by_reason.items(), key=lambda x: x[1]["pnl"], reverse=True):
        wr = s["wins"]/s["count"]*100 if s["count"] > 0 else 0
        print(f"    {r:12s}: {s['count']:3d}笔 胜{wr:.0f}% PnL {s['pnl']:+.2f}U")

    # 最近10笔交易
    print(f"\n  最近10笔交易:")
    for t in trades[-10:]:
        d = "多" if t["direction"] == "long" else "空"
        q = t.get("quality", 0)
        print(f"    #{t['id']} {t['symbol']:15s} {d} | {t['entry_time']} → {t.get('exit_time','')[:10]} "
              f"Q={q:.0f} | {t['pnl_usd']:+.1f}U ({t['exit_reason']})")

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins)/len(trades)*100, 1) if trades else 0,
        "pnl": round(total_pnl, 2),
        "max_dd": max_dd,
        "monthly_win_rate": round(months_profit/months_total*100, 1) if monthly else 0,
    }


def print_comparison(summary_a, summary_b):
    """打印对比表"""
    print(f"\n{'='*70}")
    print(f"📊 对比: 原始Spike vs v13优化")
    print(f"{'='*70}")
    print(f"  {'指标':<15} {'A) 原始':>15} {'B) v13':>15} {'变化':>15}")
    print(f"  {'-'*60}")

    def fmt_change(a, b, higher_better=True):
        diff = b - a
        if abs(a) < 0.001:
            return "-"
        arrow = "↑" if (diff > 0) == higher_better else "↓"
        return f"{arrow} {diff:+.1f}"

    print(f"  {'交易笔数':<15} {summary_a['trades']:>15} {summary_b['trades']:>15}")
    print(f"  {'胜率':<15} {summary_a['win_rate']:>14.1f}% {summary_b['win_rate']:>14.1f}% "
          f"{fmt_change(summary_a['win_rate'], summary_b['win_rate']):>15}")
    print(f"  {'PnL':<15} {summary_a['pnl']:>+14.1f}U {summary_b['pnl']:>+14.1f}U "
          f"{fmt_change(summary_a['pnl'], summary_b['pnl']):>15}")
    print(f"  {'最大回撤':<15} {summary_a['max_dd']:>14.1f}% {summary_b['max_dd']:>14.1f}% "
          f"{fmt_change(summary_a['max_dd'], summary_b['max_dd'], False):>15}")
    print(f"  {'月胜率':<15} {summary_a['monthly_win_rate']:>14.1f}% {summary_b['monthly_win_rate']:>14.1f}% "
          f"{fmt_change(summary_a['monthly_win_rate'], summary_b['monthly_win_rate']):>15}")
    print()


# ============================================================
# Main
# ============================================================
# global data reference for simulate()
data = None


def main():
    global data

    # 1. 获取数据
    data = fetch_all_data()

    # 2. 生成信号
    raw_signals, v13_signals = generate_signals(data)

    if not raw_signals:
        print("\n❌ 没有找到任何Spike信号!")
        return

    # 3. 模拟交易
    # Mode A: 原始Spike — 无v13过滤, 72h持仓上限, 无质量加仓
    results_a = simulate(
        raw_signals, data["all_klines_15m"],
        mode_name="A) 原始Spike (无EMA/RSI/质量过滤)",
        max_hold_hours=72,        # 无特殊限制
        boost_quality=999,        # 不启用质量加仓
        boost_mult=1.0,
    )

    # Mode B: v13优化 — EMA+RSI+质量≥70+8h上限+质量≥80加仓×1.3
    results_b = simulate(
        v13_signals, data["all_klines_15m"],
        mode_name="B) v13优化 (EMA+RSI+质量≥70+8h+加仓)",
        max_hold_hours=SPIKE_MAX_HOLD_HOURS,
        boost_quality=SPIKE_BOOST_QUALITY,
        boost_mult=SPIKE_BOOST_MULT,
    )

    # 4. 输出报告
    summary_a = print_report(results_a, "A) 原始Spike")
    summary_b = print_report(results_b, "B) v13优化Spike")
    print_comparison(summary_a, summary_b)

    # 5. 保存结果
    output = {
        "version": "spike_v13_backtest",
        "start_time": START_TIME.strftime("%Y-%m-%d"),
        "end_time": END_TIME.strftime("%Y-%m-%d"),
        "initial_balance": INITIAL_BALANCE,
        "leverage": LEVERAGE,
        "symbols": data["symbols"],
        "raw_signal_count": len(raw_signals),
        "v13_signal_count": len(v13_signals),
        "mode_a": {
            "summary": summary_a,
            "final_balance": results_a["balance"],
            "max_drawdown": results_a["max_drawdown"],
            "trades": results_a["trades"],
        },
        "mode_b": {
            "summary": summary_b,
            "final_balance": results_b["balance"],
            "max_drawdown": results_b["max_drawdown"],
            "trades": results_b["trades"],
        },
    }

    out_path = OUTPUT_DIR / "data" / "backtest_spike_v13_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 保存到: {out_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
