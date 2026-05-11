#!/usr/bin/env python3
"""
回测脚本 v11j — 基于 v11i + 单笔亏损上限
读取 config.py 的 v11i/v11j 参数，复用 S22 规则进行回测

方案M(仅单笔风险上限$40): 1000天/1274笔 胜率62.7% PnL +$4011 DD 16.46%
"""
import sys
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

# 导入 v11i/v11j 配置
from config import (
    INITIAL_BALANCE, LEVERAGE, MAX_OPEN_POSITIONS, COOLDOWN_HOURS,
    STATIC_BLACKLIST, V11_MIN_V8_SCORE,
    # v11i 参数
    V11I_SHORT_V8_THRESHOLD, V11I_SHORT_V8_MULT,
    V11I_V8_LOW_THRESHOLD, V11I_V8_LOW_MULT,
    V11I_V8_HIGH_THRESHOLD, V11I_V8_HIGH_MULT_LONG, V11I_V8_HIGH_MULT_SHORT,
    V11I_RSI_WEAK, V11I_RSI_WEAK_MULT,
    V11I_RSI_MID_LOW, V11I_RSI_MID_HIGH, V11I_RSI_MID_MULT,
    V11I_RSI_STRONG_LOW, V11I_RSI_STRONG_HIGH, V11I_RSI_STRONG_MULT,
    V11I_RSI_VERY_STRONG, V11I_RSI_VERY_STRONG_MULT,
    V11I_SL_MEDIUM_LOW, V11I_SL_MEDIUM_HIGH, V11I_SL_MEDIUM_MULT,
    V11I_SL_WIDE_LOW, V11I_SL_WIDE_HIGH, V11I_SL_WIDE_MULT,
    V11I_MAX_SL_PCT, V11I_MAX_ATR_PCT, V11I_FILTER_V8_RSI,
    V11I_CONSEC_LOSS_THRESHOLD, V11I_CONSEC_LOSS_MULT,
    # v11j 参数
    MAX_LOSS_PER_TRADE,
    # 其他
    EXTREME_NEG_FUNDING, EXTREME_POS_FUNDING,
    EMA_FAST, EMA_SLOW, RSI_PERIOD, RSI_OVERBOUGHT, RSI_OVERSOLD,
    ATR_PERIOD, ATR_SL_MULTIPLIER,
    DEFAULT_SL_PCT, DEFAULT_TP_PCT,
    GRACE_PERIOD_HOURS,
)

FAPI_LIVE = "https://fapi.binance.com"
TZ_UTC8 = timezone(timedelta(hours=8))

# 回测时间设置
END_TIME = datetime.now(TZ_UTC8)
START_TIME = END_TIME - timedelta(days=1000)


def api_get(endpoint, params=None):
    """币安API请求"""
    url = FAPI_LIVE + endpoint
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 429:
            time.sleep(2)
            return api_get(endpoint, params)
    except Exception:
        pass
    return None


def get_funding_history_ts(symbol, start_ts, end_ts, limit=1000):
    """获取费率历史"""
    all_data = []
    cur_start = start_ts
    while cur_start < end_ts:
        data = api_get("/fapi/v1/fundingRate", {
            "symbol": symbol, "startTime": cur_start, "endTime": end_ts, "limit": limit,
        })
        if not data or not isinstance(data, list):
            break
        all_data.extend(data)
        if len(data) < limit:
            break
        cur_start = int(data[-1]["fundingTime"]) + 1
        time.sleep(0.1)
    return [{"time": int(d["fundingTime"]), "rate": float(d["fundingRate"]) * 100} for d in all_data]


def get_klines_ts(symbol, interval, start_ts, end_ts, limit=1500):
    """获取K线数据"""
    all_data = []
    cur_start = start_ts
    while cur_start < end_ts:
        data = api_get("/fapi/v1/klines", {
            "symbol": symbol, "interval": interval, "startTime": cur_start, "endTime": end_ts, "limit": limit,
        })
        if not data or not isinstance(data, list):
            break
        all_data.extend(data)
        if len(data) < limit:
            break
        cur_start = int(data[-1][0]) + 1
        time.sleep(0.1)
    return [{
        "time": int(k[0]), "open": float(k[1]), "high": float(k[2]),
        "low": float(k[3]), "close": float(k[4]), "volume": float(k[7]),
    } for k in all_data]


def get_qualified_symbols():
    """获取符合条件的交易对"""
    tickers = api_get("/fapi/v1/ticker/24hr") or []
    exclude = {"BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT", "BTCDOMUSDT", "BTCSTUSDT"}
    qualified = []
    for t in tickers:
        sym = t.get("symbol", "")
        vol = float(t.get("quoteVolume", 0))
        price = float(t.get("lastPrice", 0))
        if sym.endswith("USDT") and sym not in exclude and vol > 50_000_000 and price > 0.001:
            qualified.append({"symbol": sym, "volume": vol, "price": price})
    qualified.sort(key=lambda x: x["volume"], reverse=True)
    return qualified


# === 技术指标 ===
def calc_ema(closes, period):
    """计算EMA"""
    if len(closes) < period:
        return None
    ema = [closes[0]]
    k = 2 / (period + 1)
    for c in closes[1:]:
        ema.append(c * k + ema[-1] * (1 - k))
    return ema[-1]


def calc_rsi(closes, period=14):
    """计算RSI"""
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


def calc_atr(klines, period=14):
    """计算ATR"""
    if len(klines) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(klines)):
        h = klines[i]["high"]
        l = klines[i]["low"]
        prev_c = klines[i-1]["close"]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    if len(trs) < period:
        return 0.0
    atr = sum(trs[-period:]) / period
    close = klines[-1]["close"]
    return atr / close if close > 0 else 0.0


def get_tech_at(klines_hist, ts):
    """获取指定时间点的技术指标"""
    k_before = [k for k in klines_hist if k["time"] <= ts]
    if len(k_before) < 25:
        return {"trend": "neutral", "rsi": 50.0, "atr_pct": 0.0, "ema9": None, "ema21": None}

    closes = [k["close"] for k in k_before]
    ema9 = calc_ema(closes, EMA_FAST)
    ema21 = calc_ema(closes, EMA_SLOW)

    trend = "neutral"
    if ema9 and ema21:
        if ema9 > ema21 * 1.001:
            trend = "up"
        elif ema9 < ema21 * 0.999:
            trend = "down"

    rsi = calc_rsi(closes, RSI_PERIOD)
    atr = calc_atr(k_before, ATR_PERIOD)

    return {"trend": trend, "rsi": rsi, "atr_pct": atr, "ema9": ema9, "ema21": ema21}


# === v11i: 硬过滤规则 ===
def apply_v11i_hard_filters(candidates):
    """
    应用 v11i 硬过滤规则:
    - STATIC_BLACKLIST 跳过
    - V11_MIN_V8_SCORE = 4 最低门槛
    - ATR > 5% 跳过
    - SL > 10% 跳过
    - V8 >= 6.5 + RSI < 55 做多跳过
    """
    filtered = []
    for c in candidates:
        # 静态黑名单
        if c["symbol"] in STATIC_BLACKLIST:
            continue

        # v8_score 最低门槛
        v8_score = c.get("v8_score", 0)
        if v8_score < V11_MIN_V8_SCORE:
            continue

        # ATR > 5% 跳过
        atr_pct = c.get("atr_pct", 0)
        if atr_pct * 100 > V11I_MAX_ATR_PCT:
            continue

        # SL > 10% 跳过
        sl_pct = c.get("sl_pct", 0) * 100
        if sl_pct > V11I_MAX_SL_PCT:
            continue

        # V8 >= 6.5 + RSI < 55 做多跳过
        if V11I_FILTER_V8_RSI and c["direction"] == "long":
            rsi = c.get("rsi", 50)
            if v8_score >= V11I_V8_HIGH_THRESHOLD and rsi < 55:
                continue

        filtered.append(c)

    return filtered


# === v11i: 仓位倍率计算 ===
def calc_v11i_position_mult(candidate, consec_losses):
    """
    计算 v11i 仓位倍率
    - 做空额外惩罚: V8>=5 减半
    - V8反转仓位: V8<=4 加仓1.3倍, V8>=6.5 减仓
    - RSI区间仓位: 仅做多
    - SL%区间仓位
    """
    mult = 1.0
    v8_score = candidate.get("v8_score", 0)
    rsi = candidate.get("rsi", 50)
    sl_pct = candidate.get("sl_pct", 0) * 100
    direction = candidate["direction"]

    # 策略1: 做空额外惩罚
    if direction == "short" and v8_score >= V11I_SHORT_V8_THRESHOLD:
        mult *= V11I_SHORT_V8_MULT

    # 策略2: V8反转仓位
    if v8_score <= V11I_V8_LOW_THRESHOLD:
        mult *= V11I_V8_LOW_MULT
    elif v8_score >= V11I_V8_HIGH_THRESHOLD:
        if direction == "long":
            mult *= V11I_V8_HIGH_MULT_LONG
        else:
            mult *= V11I_V8_HIGH_MULT_SHORT

    # 策略3: RSI区间仓位 (仅做多)
    if direction == "long":
        if rsi < V11I_RSI_WEAK:
            mult *= V11I_RSI_WEAK_MULT
        elif V11I_RSI_MID_LOW <= rsi < V11I_RSI_MID_HIGH:
            mult *= V11I_RSI_MID_MULT
        elif V11I_RSI_STRONG_LOW <= rsi <= V11I_RSI_STRONG_HIGH:
            mult *= V11I_RSI_STRONG_MULT
        elif rsi >= V11I_RSI_VERY_STRONG:
            mult *= V11I_RSI_VERY_STRONG_MULT

    # 策略4: SL%区间仓位
    if V11I_SL_MEDIUM_LOW <= sl_pct <= V11I_SL_MEDIUM_HIGH:
        mult *= V11I_SL_MEDIUM_MULT
    elif V11I_SL_WIDE_LOW <= sl_pct <= V11I_SL_WIDE_HIGH:
        mult *= V11I_SL_WIDE_MULT

    # 策略6: 连续亏损冷却
    if consec_losses >= V11I_CONSEC_LOSS_THRESHOLD:
        mult *= V11I_CONSEC_LOSS_MULT

    return mult


# === v11j: 单笔亏损上限 ===
def apply_v11j_loss_cap(pos_usd, sl_pct, leverage):
    """
    应用 v11j 单笔亏损上限
    若 max_loss = pos_usd × sl_pct × leverage > MAX_LOSS_PER_TRADE
    则缩小 pos_usd 使 max_loss = MAX_LOSS_PER_TRADE

    当 MAX_LOSS_PER_TRADE 为 None 时（如 L7 profile），不应用上限
    """
    # L7 profile 使用 MAX_LOSS_PER_TRADE=None，直接返回
    if MAX_LOSS_PER_TRADE is None or MAX_LOSS_PER_TRADE <= 0:
        return pos_usd, 0

    max_loss = pos_usd * sl_pct * leverage
    if max_loss > MAX_LOSS_PER_TRADE:
        return MAX_LOSS_PER_TRADE / (sl_pct * leverage), max_loss - MAX_LOSS_PER_TRADE
    return pos_usd, 0


# === 信号扫描 ===
def scan_signals(all_funding, all_klines, price_cache, tech_cache, ts, open_symbols):
    """扫描交易信号"""
    candidates = []
    dt_utc = datetime.utcfromtimestamp(ts / 1000)
    is_funding_time = dt_utc.hour % 8 == 0 and dt_utc.minute == 0
    should_scan_coiling = (dt_utc.hour % 4 == 0 and dt_utc.minute == 0)

    # 极端正费率做空
    if is_funding_time:
        for sym, f_hist in all_funding.items():
            if sym in open_symbols or sym in STATIC_BLACKLIST:
                continue
            if len(f_hist) < 3:
                continue
            recent = f_hist[-8:]
            current = recent[-1]["rate"]
            if current <= EXTREME_POS_FUNDING:
                continue
            pos_count = sum(1 for r in recent if r["rate"] > 0.05)
            if pos_count < 3:
                continue
            avg = sum(r["rate"] for r in recent) / len(recent)

            tech = tech_cache.get(sym, {"trend": "neutral", "rsi": 50.0, "atr_pct": 0.0})
            if tech["trend"] == "up" or tech["rsi"] < 35:
                continue

            strength = "S" if avg > 0.25 else "A"
            if strength == "B":
                continue

            atr_pct = tech["atr_pct"]
            sl_pct = max(atr_pct * 1.5, DEFAULT_SL_PCT)
            sl_pct = min(sl_pct, 0.08)
            tp_pct = sl_pct * 2.5

            candidates.append({
                "symbol": sym, "type": "extreme_pos_funding", "direction": "short",
                "strength": strength, "sl_pct": sl_pct, "tp_pct": tp_pct,
                "rr": round(tp_pct / sl_pct, 2),
                "reason": f"极端正费率 avg:{avg:+.4f}%",
                "atr_pct": atr_pct, "rsi": tech["rsi"],
            })

    # 暴涨回落做空
    for sym, klines in all_klines.items():
        if sym in open_symbols or sym in STATIC_BLACKLIST:
            continue
        k_before = [k for k in klines if k["time"] <= ts]
        if len(k_before) < 24:
            continue
        last24 = k_before[-24:]
        old_close = last24[0]["close"]
        current = last24[-1]["close"]
        change_pct = (current - old_close) / old_close * 100
        if change_pct <= 50:
            continue
        recent6 = last24[-6:]
        peak = max(k["high"] for k in recent6)
        pullback = (peak - current) / peak * 100
        if pullback < 8:
            continue

        tech = tech_cache.get(sym, {"trend": "neutral", "rsi": 50.0, "atr_pct": 0.0})
        if tech["trend"] == "up" or tech["rsi"] < 35:
            continue

        strength = "A" if pullback > 15 else "B"
        if strength == "B":
            continue

        atr_pct = tech["atr_pct"]
        sl_pct = max(atr_pct * 1.5, DEFAULT_SL_PCT)
        sl_pct = min(sl_pct, 0.08)
        tp_pct = sl_pct * 2.5

        candidates.append({
            "symbol": sym, "type": "pump_short", "direction": "short",
            "strength": strength, "sl_pct": sl_pct, "tp_pct": tp_pct,
            "rr": round(tp_pct / sl_pct, 2),
            "reason": f"24h暴涨{change_pct:+.1f}%后回落{pullback:.0f}%",
            "atr_pct": atr_pct, "rsi": tech["rsi"],
        })

    # 蓄势突破
    if should_scan_coiling:
        for sym, klines in all_klines.items():
            if sym in open_symbols or sym in STATIC_BLACKLIST:
                continue
            k_before = [k for k in klines if k["time"] <= ts]
            if len(k_before) < 48:
                continue

            recent = k_before[-48:]
            closes = [k["close"] for k in recent]
            volumes = [k["volume"] for k in recent]
            current_price = closes[-1]

            # 收缩检测
            recent24_closes = closes[-24:]
            recent24_range = (max(recent24_closes) - min(recent24_closes)) / min(recent24_closes)
            if recent24_range > 0.10:
                continue

            prev24_closes = closes[:24]
            prev24_range = (max(prev24_closes) - min(prev24_closes)) / min(prev24_closes) if min(prev24_closes) > 0 else 999
            if prev24_range <= recent24_range:
                continue

            # 量能突破
            if len(volumes) < 21:
                continue
            avg_vol_20 = sum(volumes[-21:-1]) / 20
            if avg_vol_20 <= 0:
                continue
            vol_surge = volumes[-1] / avg_vol_20
            if vol_surge < 3.0:
                continue

            ema21 = calc_ema(closes, EMA_SLOW)
            ema9 = calc_ema(closes, EMA_FAST)
            rsi = calc_rsi(closes, RSI_PERIOD)

            if ema21 and current_price > ema21 * 1.002:
                direction = "long"
                if rsi > 65:
                    continue
            elif ema21 and current_price < ema21 * 0.998:
                direction = "short"
                if rsi < 35:
                    continue
            else:
                continue

            atr_pct = tech_cache.get(sym, {}).get("atr_pct", 0)
            if atr_pct <= 0 or atr_pct > 0.06:
                continue

            sl_pct = max(atr_pct * 3.5, 0.025)
            sl_pct = min(sl_pct, 0.06)
            tp_pct = sl_pct * 2.5

            candidates.append({
                "symbol": sym, "type": "coiling_breakout", "direction": direction,
                "strength": "A", "sl_pct": sl_pct, "tp_pct": tp_pct,
                "rr": round(tp_pct / sl_pct, 2),
                "reason": f"蓄势突破 {direction}",
                "atr_pct": atr_pct, "rsi": rsi,
            })

    return candidates


def calculate_v8_score(candidate, tech):
    """计算简化的 v8 评分 (用于筛选)"""
    score = 5  # 基础分
    direction = candidate["direction"]
    trend = tech.get("trend", "neutral")
    rsi = tech.get("rsi", 50)
    atr_pct = tech.get("atr_pct", 0)

    # 趋势一致性
    if (direction == "long" and trend == "up") or (direction == "short" and trend == "down"):
        score += 2
    elif (direction == "long" and trend == "down") or (direction == "short" and trend == "up"):
        score -= 1

    # RSI 极值
    if direction == "long":
        if rsi < 35:
            score += 2
        elif rsi > 65:
            score -= 1
    else:
        if rsi > 65:
            score += 2
        elif rsi < 35:
            score -= 1

    # ATR 适中
    if 0.5 < atr_pct < 5:
        score += 1

    # 信号强度
    if candidate.get("strength") == "S":
        score += 2
    elif candidate.get("strength") == "A":
        score += 1

    return max(0, min(10, score))


def run_backtest():
    """运行 v11j 回测"""
    print("=" * 70)
    print(f"📊 回测 v11j — 基于 v11i + 单笔亏损上限 ${MAX_LOSS_PER_TRADE}")
    print(f"时间: {START_TIME.strftime('%Y-%m-%d')} ~ {END_TIME.strftime('%Y-%m-%d')}")
    print(f"资金: ${INITIAL_BALANCE:.0f} | 杠杆: {LEVERAGE}x")
    print(f"V11I规则: SL>{V11I_MAX_SL_PCT}%跳过 ATR>{V11I_MAX_ATR_PCT}%跳过")
    print(f"         V8>={V11I_V8_HIGH_THRESHOLD}+RSI<55做多跳过")
    print(f"         连亏≥{V11I_CONSEC_LOSS_THRESHOLD}笔冷却×{V11I_CONSEC_LOSS_MULT}")
    print(f"V11J规则: 单笔亏损上限 ${MAX_LOSS_PER_TRADE}")
    print("=" * 70)

    start_ts = int(START_TIME.timestamp() * 1000)
    end_ts = int(END_TIME.timestamp() * 1000)

    print("\n🔍 获取活跃合约列表...")
    symbols_info = get_qualified_symbols()[:50]
    symbols = [s["symbol"] for s in symbols_info]
    vol_map = {s["symbol"]: s["volume"] for s in symbols_info}
    print(f"  {len(symbols)} 个币种")

    print("\n📈 获取BTC历史...")
    btc_klines = get_klines_ts("BTCUSDT", "1h", start_ts, end_ts)
    print(f"  BTC 1h K线: {len(btc_klines)} 根")

    print("\n💰 获取费率历史...")
    all_funding = {}
    for i, sym in enumerate(symbols):
        fh = get_funding_history_ts(sym, start_ts, end_ts)
        if fh:
            all_funding[sym] = fh
        if (i + 1) % 10 == 0:
            print(f"  费率: {i+1}/{len(symbols)}...")
        time.sleep(0.3)
    print(f"  有费率数据: {len(all_funding)} 币种")

    print("\n📉 获取K线历史(含预热)...")
    pre_start = start_ts - 50 * 3600 * 1000
    all_klines = {}
    for i, sym in enumerate(symbols):
        kl = get_klines_ts(sym, "1h", pre_start, end_ts)
        if kl:
            all_klines[sym] = kl
        if (i + 1) % 10 == 0:
            print(f"  K线: {i+1}/{len(symbols)}...")
        time.sleep(0.3)
    print(f"  有K线数据: {len(all_klines)} 币种")

    all_times = sorted(set(k["time"] for kl in all_klines.values() for k in kl))
    all_times = [t for t in all_times if start_ts <= t <= end_ts]
    print(f"\n⏱️ 回测步数: {len(all_times)}")

    print("\n🚀 开始模拟交易...\n")

    balance = INITIAL_BALANCE
    positions = []
    all_trades = []
    cooldowns = {}
    consecutive_losses = 0
    max_equity = INITIAL_BALANCE
    max_drawdown = 0
    daily_pnl = {}
    signals_found = 0
    signals_filtered = 0
    loss_cap_savings = 0  # v11j: 亏损上限节省的资金

    for step_i, ts in enumerate(all_times):
        dt = datetime.fromtimestamp(ts / 1000, tz=TZ_UTC8)
        today_str = dt.strftime("%Y-%m-%d")

        # 获取此时间点价格和技术指标
        price_cache = {}
        tech_cache = {}
        for sym, klines in all_klines.items():
            for k in reversed(klines):
                if k["time"] <= ts:
                    price_cache[sym] = k["close"]
                    break
            tech_cache[sym] = get_tech_at(all_klines[sym], ts)

        # === 检查持仓 ===
        to_close = []
        for pos in positions:
            sym = pos["symbol"]
            if sym not in price_cache:
                continue

            pk = all_klines[sym]
            pk_curr = [k for k in pk if k["time"] <= ts][-1]
            if not pk_curr:
                continue

            triggered = None
            fill_price = pk_curr["close"]
            is_partial = False

            entry = pos["entry_price"]
            if pos["direction"] == "long":
                pnl_raw = (pk_curr["close"] - entry) / entry
            else:
                pnl_raw = (entry - pk_curr["close"]) / entry

            # 止损检查 (宽限期内不扫)
            in_grace = False
            try:
                et = datetime.fromisoformat(pos["entry_time"])
                if et.tzinfo is None:
                    et = et.replace(tzinfo=TZ_UTC8)
                hours_held = (dt - et).total_seconds() / 3600
                if hours_held < GRACE_PERIOD_HOURS:
                    in_grace = True
            except:
                pass

            if not in_grace:
                if pos["direction"] == "long":
                    if pk_curr["low"] <= pos["stop_loss"]:
                        triggered = "止损"
                        fill_price = pos["stop_loss"]
                else:
                    if pk_curr["high"] >= pos["stop_loss"]:
                        triggered = "止损"
                        fill_price = pos["stop_loss"]

            # 止盈检查
            if not triggered:
                if pos["direction"] == "long":
                    if pk_curr["high"] >= pos["take_profit"]:
                        triggered = "止盈"
                        fill_price = pos["take_profit"]
                else:
                    if pk_curr["low"] <= pos["take_profit"]:
                        triggered = "止盈"
                        fill_price = pos["take_profit"]

            if triggered:
                to_close.append((pos, fill_price, triggered))

        # 执行平仓
        for pos, price, reason in to_close:
            entry = pos["entry_price"]
            lev = pos["leverage"]
            pos_usd = pos["position_usd"]
            if pos["direction"] == "long":
                pnl_pct = (price - entry) / entry * 100 * lev
            else:
                pnl_pct = (entry - price) / entry * 100 * lev
            pnl_usd = pnl_pct / 100 * pos_usd

            balance += pnl_usd
            daily_pnl[today_str] = daily_pnl.get(today_str, 0) + pnl_usd

            if pnl_usd < 0:
                consecutive_losses += 1
            else:
                consecutive_losses = 0

            all_trades.append({
                **pos,
                "exit_price": round(price, 8),
                "exit_time": dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "exit_reason": reason,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_usd": round(pnl_usd, 2),
                "status": "closed",
            })
            positions.remove(pos)
            cooldowns[pos["symbol"]] = ts

        # === 扫描新信号 ===
        if len(positions) < MAX_OPEN_POSITIONS and balance > 100:
            open_symbols = set(p["symbol"] for p in positions)

            candidates = scan_signals(all_funding, all_klines, price_cache, tech_cache, ts, open_symbols)
            signals_found += len(candidates)

            # 应用 v11i 硬过滤
            filtered_candidates = []
            for c in candidates:
                # 计算 v8_score
                tech = tech_cache.get(c["symbol"], {"trend": "neutral", "rsi": 50.0, "atr_pct": 0.0})
                c["v8_score"] = calculate_v8_score(c, tech)
                c["atr_pct"] = tech.get("atr_pct", 0)
                c["rsi"] = tech.get("rsi", 50)

                # 冷却检查
                last_ts = cooldowns.get(c["symbol"], 0)
                if ts - last_ts < COOLDOWN_HOURS * 3600 * 1000:
                    continue

                filtered_candidates.append(c)

            # 应用 v11i 硬过滤规则
            valid = apply_v11i_hard_filters(filtered_candidates)

            # 按强度排序
            valid.sort(key=lambda x: ({"S": 0, "A": 1}.get(x["strength"], 2), -x.get("v8_score", 0)))

            for c in valid[:1]:  # 每次最多开1仓
                if len(positions) >= MAX_OPEN_POSITIONS:
                    break

                price = price_cache.get(c["symbol"])
                if not price or price <= 0:
                    continue

                sl_pct = c["sl_pct"]
                tp_pct = c["tp_pct"]

                # 计算基础仓位 (基于风险的仓位)
                base_pos_usd = balance * 0.10  # 10% 基础仓位

                # 应用 v11i 仓位倍率
                v11i_mult = calc_v11i_position_mult(c, consecutive_losses)
                pos_usd = base_pos_usd * v11i_mult

                # 应用 v11j 单笔亏损上限
                pos_usd, savings = apply_v11j_loss_cap(pos_usd, sl_pct, LEVERAGE)
                loss_cap_savings += savings

                # 计算止损止盈价
                if c["direction"] == "long":
                    sl = price * (1 - sl_pct)
                    tp = price * (1 + tp_pct)
                else:
                    sl = price * (1 + sl_pct)
                    tp = price * (1 - tp_pct)

                position = {
                    "id": f"{len(all_trades) + len(positions) + 1:03d}",
                    "symbol": c["symbol"],
                    "direction": c["direction"],
                    "leverage": LEVERAGE,
                    "position_pct": round(pos_usd / balance * 100, 1),
                    "position_usd": round(pos_usd, 2),
                    "notional_usd": round(pos_usd * LEVERAGE, 2),
                    "entry_price": price,
                    "stop_loss": round(sl, 8),
                    "take_profit": round(tp, 8),
                    "entry_time": dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "signal_type": c["type"],
                    "signal_strength": c["strength"],
                    "signal_reason": c.get("reason", ""),
                    "v8_score": c.get("v8_score", 0),
                    "atr_pct": c.get("atr_pct", 0),
                    "rsi": c.get("rsi", 50),
                    "sl_pct": round(sl_pct * 100, 2),
                    "status": "open",
                }
                positions.append(position)

                print(f"  开仓 #{position['id']} {c['symbol']} {'多' if c['direction']=='long' else '空'} "
                      f"@{price:.4f} 仓${pos_usd:.0f} SL={sl_pct*100:.1f}% "
                      f"V8={c.get('v8_score',0)}")
                break

        # 净值和回撤
        unrealized = 0
        for pos in positions:
            sym = pos["symbol"]
            cp = price_cache.get(sym)
            if cp:
                entry = pos["entry_price"]
                if pos["direction"] == "long":
                    raw = (cp - entry) / entry
                else:
                    raw = (entry - cp) / entry
                unrealized += raw * pos["position_usd"] * pos["leverage"]

        equity = balance + unrealized
        if equity > max_equity:
            max_equity = equity
        dd = (max_equity - equity) / max_equity * 100 if max_equity > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd

        if (step_i + 1) % 200 == 0:
            print(f"  [{step_i+1}/{len(all_times)}] {dt.strftime('%m-%d %H:%M')} "
                  f"余额=${balance:.0f} 持仓{len(positions)} 交易{len(all_trades)}笔")

    # 强制平仓剩余持仓
    for pos in positions[:]:
        cp = price_cache.get(pos["symbol"], pos["entry_price"])
        entry = pos["entry_price"]
        if pos["direction"] == "long":
            pnl_pct = (cp - entry) / entry * 100 * pos["leverage"]
        else:
            pnl_pct = (entry - cp) / entry * 100 * pos["leverage"]
        pnl_usd = pnl_pct / 100 * pos["position_usd"]
        balance += pnl_usd
        all_trades.append({
            **pos,
            "exit_price": round(cp, 8),
            "exit_time": "回测结束",
            "exit_reason": "回测结束",
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": round(pnl_usd, 2),
            "status": "closed",
        })

    # === 统计结果 ===
    print("\n" + "=" * 70)
    print("📊 回测 v11j 结果")
    print("=" * 70)

    closed = [t for t in all_trades if t["status"] == "closed"]
    wins = [t for t in closed if (t.get("pnl_usd", 0) or 0) > 0]
    losses = [t for t in closed if (t.get("pnl_usd", 0) or 0) < 0]

    total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
    win_pnl = sum(t.get("pnl_usd", 0) for t in wins) if wins else 0
    loss_pnl = sum(t.get("pnl_usd", 0) for t in losses) if losses else 1

    print(f"\n💰 总体表现:")
    print(f"  初始资金:   ${INITIAL_BALANCE:.0f}")
    print(f"  最终余额:   ${balance:.2f}")
    print(f"  总盈亏:     {total_pnl:+.2f} USDT ({total_pnl/INITIAL_BALANCE*100:+.1f}%)")
    print(f"  最大回撤:   -{max_drawdown:.2f}%")
    print(f"  总交易:     {len(closed)} 笔")
    print(f"  盈利/亏损:  {len(wins)}/{len(losses)} 笔")
    if closed:
        print(f"  胜率:       {len(wins)/len(closed)*100:.1f}%")
    if wins and losses:
        avg_win = win_pnl / len(wins)
        avg_loss = abs(loss_pnl / len(losses))
        print(f"  盈亏比:     {avg_win/avg_loss:.2f}")
        print(f"  平均盈利:   +{avg_win:.2f}U")
        print(f"  平均亏损:   -{avg_loss:.2f}U")

    profit_factor = abs(win_pnl / loss_pnl) if loss_pnl != 0 else 0
    print(f"  盈利因子PF: {profit_factor:.2f}")

    # 月度统计
    monthly_pnl = defaultdict(list)
    for t in closed:
        month = t.get("entry_time", "")[:7]
        monthly_pnl[month].append(t.get("pnl_usd", 0))

    monthly_totals = {m: sum(p) for m, p in monthly_pnl.items()}
    profit_months = sum(1 for p in monthly_totals.values() if p > 0)
    total_months = len(monthly_totals)
    monthly_win_rate = profit_months / total_months * 100 if total_months > 0 else 0

    print(f"\n📅 月度表现:")
    print(f"  月胜率:     {monthly_win_rate:.1f}% ({profit_months}/{total_months})")

    # 最大连续亏损
    max_consec_loss = 0
    current_consec = 0
    for t in closed:
        if t.get("pnl_usd", 0) < 0:
            current_consec += 1
            max_consec_loss = max(max_consec_loss, current_consec)
        else:
            current_consec = 0

    # 最大单笔亏损
    max_single_loss = min((t.get("pnl_usd", 0) for t in closed), default=0)

    print(f"  最大连亏:   {max_consec_loss} 笔")
    print(f"  最大单亏:   ${max_single_loss:.2f}")

    # v11j 亏损上限节省
    print(f"\n🛡️ v11j 风险控制:")
    print(f"  亏损上限节省: ${loss_cap_savings:.2f}")

    # ROI/DD 比率
    roi = total_pnl / INITIAL_BALANCE * 100
    roi_dd_ratio = abs(roi / max_drawdown) if max_drawdown > 0 else 0
    print(f"  ROI/DD:     {roi_dd_ratio:.2f}")

    # 保存结果
    result = {
        "version": "v11j",
        "start_time": START_TIME.strftime("%Y-%m-%d"),
        "end_time": END_TIME.strftime("%Y-%m-%d"),
        "initial_balance": INITIAL_BALANCE,
        "final_balance": round(balance, 2),
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins)/len(closed)*100, 1) if closed else 0,
        "total_pnl": round(total_pnl, 2),
        "max_drawdown": round(max_drawdown, 2),
        "profit_factor": round(profit_factor, 2),
        "monthly_win_rate": round(monthly_win_rate, 1),
        "max_consec_loss": max_consec_loss,
        "max_single_loss": round(max_single_loss, 2),
        "loss_cap_savings": round(loss_cap_savings, 2),
        "roi": round(roi, 1),
        "roi_dd_ratio": round(roi_dd_ratio, 1),
        "monthly_pnl": {k: round(v, 2) for k, v in sorted(monthly_totals.items())},
        "trades": all_trades,
    }

    out_path = Path(__file__).parent / "data" / "backtest_v11j_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 保存到: {out_path}")

    # 与基准对比
    compare_path = Path(__file__).parent.parent / "strategies" / "S22-v11j" / "backtest_v11j_compare.json"
    if compare_path.exists():
        with open(compare_path) as f:
            compare_data = json.load(f)
        baseline = compare_data.get("1000days", {})
        print("\n" + "=" * 70)
        print("📊 与基准对比 (backtest_v11j_compare.json 1000天)")
        print("=" * 70)
        print(f"{'指标':<20} {'回测结果':>15} {'基准':>15} {'差异':>15}")
        print("-" * 70)
        print(f"{'交易笔数':<20} {len(closed):>15} {baseline.get('total_trades', 0):>15} "
              f"{len(closed) - baseline.get('total_trades', 0):>+15}")
        print(f"{'胜率%':<20} {result['win_rate']:>14.1f}% {baseline.get('win_rate', 0):>14.1f}% "
              f"{result['win_rate'] - baseline.get('win_rate', 0):>+14.1f}%")
        print(f"{'总盈亏':<20} ${result['total_pnl']:>+13.2f} ${baseline.get('total_pnl', 0):>+13.2f} "
              f"${result['total_pnl'] - baseline.get('total_pnl', 0):>+13.2f}")
        print(f"{'最大回撤%':<20} {result['max_drawdown']:>14.2f}% {baseline.get('max_drawdown', 0):>14.2f}% "
              f"{result['max_drawdown'] - baseline.get('max_drawdown', 0):>+14.2f}%")
        print(f"{'盈利因子PF':<20} {result['profit_factor']:>15.2f} {baseline.get('profit_factor', 0):>15.2f} "
              f"{result['profit_factor'] - baseline.get('profit_factor', 0):>+15.2f}")
        print(f"{'月胜率%':<20} {result['monthly_win_rate']:>14.1f}% {baseline.get('monthly_win_rate', 0):>14.1f}% "
              f"{result['monthly_win_rate'] - baseline.get('monthly_win_rate', 0):>+14.1f}%")
        print(f"{'最大连亏':<20} {result['max_consec_loss']:>15} {baseline.get('max_consec_loss', 0):>15} "
              f"{result['max_consec_loss'] - baseline.get('max_consec_loss', 0):>+15}")
        print(f"{'最大单亏':<20} ${result['max_single_loss']:>+14.2f} ${baseline.get('max_single_loss', 0):>+14.2f} "
              f"${result['max_single_loss'] - baseline.get('max_single_loss', 0):>+14.2f}")
        print("=" * 70)

    print("=" * 70)


if __name__ == "__main__":
    run_backtest()
