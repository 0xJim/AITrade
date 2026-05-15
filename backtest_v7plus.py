#!/usr/bin/env python3
"""
回测脚本 v7 — 精准狙击版
基于半年29笔回测数据:
- DASH亏$121(7笔反复止损) → 同币种连亏冷却7天+趋势反转确认
- HIVE亏$109(0胜3负) → 同上
- 盈亏比1.05太低 → 低ATR币SL放宽3倍，止盈不封顶用趋势跟随
- 止损笔一半是"SL太窄被扫" → ATR<2%的币SL=ATR×3.0
v6→v7改动:
1. ATR_SL_MULTIPLIER: 2.0→3.0 (低ATR币用3倍)
2. COOLDOWN_CONSECUTIVE_LOSS: 72h→168h(7天)
3. 连亏后需趋势反转确认(reversal_confirm)
4. 趋势跟随触发降低: 2%(更容易启动跟随吃肉)
5. 分批止盈后剩余用EMA9跟随(不设TP上限)
6. TRAILING_TP_STEP: 3%→4%(给更多空间)
"""
import sys
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

FAPI_LIVE = "https://fapi.binance.com"
TZ_UTC8 = timezone(timedelta(hours=8))

# === v7+ 交易参数 ===
INITIAL_BALANCE = 5000.0
LEVERAGE = 3
RISK_PER_TRADE = 0.012       # v7+: 1.2%/笔 (原1%，提高单笔利润空间)
MAX_POSITIONS = 3             # v7+: 3个仓位同时持有 (原2)
COOLDOWN_HOURS = 12           # v7+: 12h (原24h，增加频次)
COOLDOWN_CONSECUTIVE_LOSS = 96   # v7+: 4天 (原7天，不太久)
DAILY_MAX_LOSS_PCT = 3

# === v7+ 策略阈值 ===
EXTREME_NEG_FUNDING = -0.05   # v7+: -0.05 (原-0.10，更灵敏捕捉负费率)
EXTREME_POS_FUNDING = 0.08    # v7+: 0.08 (原0.15)
MIN_ENV_SCORE = 3             # v7+: 3 (原4，降低过滤门槛)
MIN_RR_RATIO = 2.0
ATR_SL_MULTIPLIER = 3.0
ATR_SL_LOW_THRESHOLD = 0.02
MIN_SL_PCT = 0.03
MAX_SL_PCT = 0.15             # v7: 保持15%，高ATR币需要宽SL吃肉
DEFAULT_SL_PCT = 0.04
DEFAULT_TP_PCT = 0.10
ATR_PERIOD = 14
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14

# === v7+ 连亏趋势反转确认 ===
REVERSAL_CONFIRM_ENABLED = False  # v7+: 关闭反转确认 (减少信号过滤)

# === v7+ 趋势跟随止损 ===
TREND_TRAIL_ENABLED = True
TREND_TRAIL_TRIGGER = 0.015   # v7+: 1.5% (原2%，更早进入跟随)

# === v7+ 分批止盈 ===
PARTIAL_TP_ENABLED = True
PARTIAL_TP_RATIO = 0.50
PARTIAL_TP_MOVE_TO_BREAKEVEN = True

# === 移动止盈 ===
TRAILING_TP_TRIGGER = 0.05
TRAILING_TP_STEP = 0.04

# === 时间止损 ===
MAX_HOLD_HOURS = 72            # v7+: 72h (原48h，给趋势更多时间)
TIME_DECAY_START = 36          # v7+: 36h (原24h)

# === 回测时间 ===
END_TIME = datetime.now(TZ_UTC8)
START_TIME = END_TIME - timedelta(days=1000)


def api_get(endpoint, params=None):
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
    if len(closes) < period:
        return None
    ema = [closes[0]]
    k = 2 / (period + 1)
    for c in closes[1:]:
        ema.append(c * k + ema[-1] * (1 - k))
    return ema[-1]


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


def calc_atr(klines, period=14):
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


# === v6: 风险定仓 ===
def calc_position_size(balance, sl_pct, leverage):
    """
    风险定仓: 固定每笔风险 = balance × RISK_PER_TRADE
    仓位 = 风险金额 / (止损% × 杠杆)
    """
    risk_usd = balance * RISK_PER_TRADE
    position_usd = risk_usd / (sl_pct * leverage)
    # 上限: 不超过余额的15%（安全阀）
    max_pos = balance * 0.15
    position_usd = min(position_usd, max_pos)
    # 下限: 至少$50
    position_usd = max(position_usd, 50)
    return round(position_usd, 2)


# === v6: 信号扫描 ===
def scan_extreme_neg_funding(funding_at_time, tech):
    if len(funding_at_time) < 3:
        return None
    recent = funding_at_time[-8:]
    current = recent[-1]["rate"]
    if current >= EXTREME_NEG_FUNDING:
        return None
    neg_count = sum(1 for r in recent if r["rate"] < -0.03)
    if neg_count < 2:  # v7+: 2期 (原3期)
        return None
    avg = sum(r["rate"] for r in recent) / len(recent)
    
    if tech["trend"] == "down":
        return None
    if tech["rsi"] > 70:  # v7+: 70 (原65，不卡得太严)
        return None
    
    strength = "S" if avg < -0.20 else "A" if avg < -0.08 else "B"  # v7+: -0.08 (原-0.10)
    if strength == "B":
        return None
    
    # v7: 动态ATR止损 — 低ATR币用3.0倍，高ATR币用2.5倍
    atr = tech["atr_pct"]
    if atr > 0:
        atr_mult = ATR_SL_MULTIPLIER if atr < ATR_SL_LOW_THRESHOLD else ATR_SL_MULTIPLIER - 0.5
        sl_pct = min(atr * atr_mult, MAX_SL_PCT)
        sl_pct = max(sl_pct, MIN_SL_PCT)
    else:
        sl_pct = DEFAULT_SL_PCT
    tp_pct = sl_pct * max(MIN_RR_RATIO, 2.5)
    
    if tech["trend"] == "up" and strength != "S":
        strength = "S"  # 趋势配合升级
    
    # v7: 费率越极端，信号越强
    # avg < -0.30 → 超级逼空信号
    if avg < -0.30:
        strength = "S"
    
    return {
        "type": "extreme_neg_funding", "direction": "long", "strength": strength,
        "sl_pct": round(sl_pct, 4), "tp_pct": round(tp_pct, 4),
        "rr": round(tp_pct / sl_pct, 2),
        "reason": f"极端负费率 avg:{avg:+.4f}% 连续{neg_count}/8期 | ATR={atr*100:.1f}%",
        "tech": tech,
    }


def scan_extreme_pos_funding(funding_at_time, tech):
    if len(funding_at_time) < 3:
        return None
    recent = funding_at_time[-8:]
    current = recent[-1]["rate"]
    if current <= EXTREME_POS_FUNDING:
        return None
    pos_count = sum(1 for r in recent if r["rate"] > 0.05)
    if pos_count < 2:  # v7+: 2期 (原3期)
        return None
    avg = sum(r["rate"] for r in recent) / len(recent)
    
    if tech["trend"] == "up":
        return None
    if tech["rsi"] < 30:  # v7+: 30 (原35)
        return None
    
    strength = "S" if avg > 0.25 else "A" if avg > 0.12 else "B"  # v7+: 0.12 (原0.15)
    if strength == "B":
        return None
    
    atr = tech["atr_pct"]
    if atr > 0:
        atr_mult = ATR_SL_MULTIPLIER if atr < ATR_SL_LOW_THRESHOLD else ATR_SL_MULTIPLIER - 0.5
        sl_pct = min(atr * atr_mult, MAX_SL_PCT)
        sl_pct = max(sl_pct, MIN_SL_PCT)
    else:
        sl_pct = DEFAULT_SL_PCT
    tp_pct = sl_pct * max(MIN_RR_RATIO, 2.5)
    
    if tech["trend"] == "down" and strength != "S":
        strength = "S"
    if avg > 0.30:
        strength = "S"
    
    return {
        "type": "extreme_pos_funding", "direction": "short", "strength": strength,
        "sl_pct": round(sl_pct, 4), "tp_pct": round(tp_pct, 4),
        "rr": round(tp_pct / sl_pct, 2),
        "reason": f"极端正费率 avg:{avg:+.4f}% 连续{pos_count}/8期 | ATR={atr*100:.1f}%",
        "tech": tech,
    }


def scan_crash_bounce(klines_hist, ts, tech):
    k_before = [k for k in klines_hist if k["time"] <= ts]
    if len(k_before) < 24:
        return None
    last24 = k_before[-24:]
    old_close = last24[0]["close"]
    current = last24[-1]["close"]
    change_pct = (current - old_close) / old_close * 100
    if change_pct >= -20:  # v7+: -20% (原-30%，更灵敏捕捉暴跌)
        return None
    recent3 = last24[-3:]
    if recent3[-1]["close"] >= recent3[-2]["close"]:
        if tech["trend"] == "down":
            return None
        if tech["rsi"] > 70:  # v7+: 70 (原65)
            return None
        strength = "A" if change_pct < -25 else "B"  # v7+: -25% (原-40%)
        if strength == "B":
            return None
        atr = tech["atr_pct"]
        if atr > 0:
            atr_mult = ATR_SL_MULTIPLIER if atr < ATR_SL_LOW_THRESHOLD else ATR_SL_MULTIPLIER - 0.5
            sl_pct = min(atr * atr_mult, MAX_SL_PCT)
            sl_pct = max(sl_pct, MIN_SL_PCT)
        else:
            sl_pct = DEFAULT_SL_PCT
        tp_pct = sl_pct * max(MIN_RR_RATIO, 2.5)
        return {
            "type": "crash_bounce", "direction": "long", "strength": strength,
            "sl_pct": round(sl_pct, 4), "tp_pct": round(tp_pct, 4),
            "rr": round(tp_pct / sl_pct, 2),
            "reason": f"24h暴跌{change_pct:+.1f}%后企稳 | ATR={atr*100:.1f}%",
            "tech": tech,
        }
    return None


def scan_pump_short(klines_hist, ts, tech):
    k_before = [k for k in klines_hist if k["time"] <= ts]
    if len(k_before) < 24:
        return None
    last24 = k_before[-24:]
    old_close = last24[0]["close"]
    current = last24[-1]["close"]
    change_pct = (current - old_close) / old_close * 100
    if change_pct <= 50:
        return None
    recent6 = last24[-6:]
    peak = max(k["high"] for k in recent6)
    pullback = (peak - current) / peak * 100
    if pullback < 5:  # v7+: 5% (原8%)
        return None
    if tech["trend"] == "up":
        return None
    if tech["rsi"] < 30:  # v7+: 30 (原35)
        return None
    strength = "A" if pullback > 10 else "B"  # v7+: 10% (原15%)
    if strength == "B":
        return None
    atr = tech["atr_pct"]
    if atr > 0:
        atr_mult = ATR_SL_MULTIPLIER if atr < ATR_SL_LOW_THRESHOLD else ATR_SL_MULTIPLIER - 0.5
        sl_pct = min(atr * atr_mult, MAX_SL_PCT)
        sl_pct = max(sl_pct, MIN_SL_PCT)
    else:
        sl_pct = DEFAULT_SL_PCT
    tp_pct = sl_pct * max(MIN_RR_RATIO, 2.5)
    return {
        "type": "pump_short", "direction": "short", "strength": strength,
        "sl_pct": round(sl_pct, 4), "tp_pct": round(tp_pct, 4),
        "rr": round(tp_pct / sl_pct, 2),
        "reason": f"24h暴涨{change_pct:+.1f}%后回落{pullback:.0f}% | ATR={atr*100:.1f}%",
        "tech": tech,
    }


def env_score_v6(signal, btc_chg, volume):
    score = 0
    d = signal["direction"]
    tech = signal.get("tech", {})
    trend = tech.get("trend", "neutral")
    rsi = tech.get("rsi", 50)
    
    if d == "long" and btc_chg > -2: score += 1
    elif d == "long" and btc_chg < -5: score -= 2
    elif d == "short" and btc_chg < 2: score += 1
    elif d == "short" and btc_chg > 5: score -= 2
    
    if volume > 100_000_000: score += 1
    elif volume < 50_000_000: score -= 1
    
    if signal["strength"] == "S": score += 2
    elif signal["strength"] == "A": score += 1
    
    if d == "long" and trend == "up": score += 1
    elif d == "short" and trend == "down": score += 1
    
    if d == "long" and rsi < 35: score += 1
    elif d == "short" and rsi > 65: score += 1
    
    rr = signal.get("rr", 0)
    if rr < MIN_RR_RATIO:
        return -999
    
    return score


def check_reversal_confirm(klines, ts, direction):
    """v7: 连亏后需趋势反转确认 — 检查最近N根K线是否确认趋势反转"""
    if not REVERSAL_CONFIRM_ENABLED:
        return True
    
    # 找到ts之前的K线
    recent = [k for k in klines if k["time"] <= ts]
    if len(recent) < REVERSAL_CONFIRM_LOOKBACK:
        return True  # 数据不够时放行
    
    recent = recent[-REVERSAL_CONFIRM_LOOKBACK:]
    
    if direction == "long":
        # 做多确认: 需要EMA9上穿EMA21 + 最近有上涨K线
        ema9_vals, ema21_vals = [], []
        for k in recent:
            ema9_vals.append(k.get("ema9", 0))
            ema21_vals.append(k.get("ema21", 0))
        
        # EMA9 > EMA21 (趋势转多)
        ema_bullish = ema9_vals[-1] > ema21_vals[-1]
        
        # 最近N根里有足够的上涨K线(收盘>开盘)
        bullish_candles = sum(1 for k in recent[-REVERSAL_MIN_REVERSAL_CANDLES:] 
                             if k["close"] > k["open"])
        
        # 价格在EMA9之上
        price_above_ema = recent[-1]["close"] > ema9_vals[-1]
        
        return ema_bullish and bullish_candles >= 3 and price_above_ema
    
    else:  # short
        ema9_vals, ema21_vals = [], []
        for k in recent:
            ema9_vals.append(k.get("ema9", 0))
            ema21_vals.append(k.get("ema21", 0))
        
        # EMA9 < EMA21 (趋势转空)
        ema_bearish = ema9_vals[-1] < ema21_vals[-1]
        
        # 最近N根里有足够的下跌K线(收盘<开盘)
        bearish_candles = sum(1 for k in recent[-REVERSAL_MIN_REVERSAL_CANDLES:] 
                             if k["close"] < k["open"])
        
        # 价格在EMA9之下
        price_below_ema = recent[-1]["close"] < ema9_vals[-1]
        
        return ema_bearish and bearish_candles >= 3 and price_below_ema


def run_backtest():
    print("=" * 60)
    print("📊 回测 v7+ — 高频高收益版")
    print(f"时间: {START_TIME.strftime('%Y-%m-%d')} ~ {END_TIME.strftime('%Y-%m-%d')}")
    print(f"资金: ${INITIAL_BALANCE:.0f} | 杠杆: {LEVERAGE}x | 风险定仓: {RISK_PER_TRADE*100}%/笔")
    print(f"止损: ATR动态×{ATR_SL_MULTIPLIER}/{ATR_SL_MULTIPLIER-0.5}({MIN_SL_PCT*100}-{MAX_SL_PCT*100}%) | RR≥{MIN_RR_RATIO}")
    print(f"分批止盈: 到TP先平{PARTIAL_TP_RATIO*100:.0f}% | 移动止盈: {TRAILING_TP_TRIGGER*100}%→{TRAILING_TP_STEP*100}%回撤")
    print(f"趋势跟随: 盈利{TREND_TRAIL_TRIGGER*100}%后EMA9跟踪止损")
    print(f"冷却: 默认{COOLDOWN_HOURS}h | 连亏{COOLDOWN_CONSECUTIVE_LOSS}h(7天) | 日亏保护{DAILY_MAX_LOSS_PCT}%")
    print(f"反转确认: {'开启' if REVERSAL_CONFIRM_ENABLED else '关闭'}")
    print("=" * 60)
    
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
    consecutive_losses = defaultdict(int)  # symbol -> 连亏次数
    max_equity = INITIAL_BALANCE
    max_drawdown = 0
    daily_pnl = {}
    signals_found = 0
    signals_filtered = 0
    
    for step_i, ts in enumerate(all_times):
        dt = datetime.fromtimestamp(ts / 1000, tz=TZ_UTC8)
        today_str = dt.strftime("%Y-%m-%d")
        
        # 获取此时间点价格
        price_cache = {}
        kline_cache = {}  # sym -> 当前K线（含完整OHLCV）
        for sym, klines in all_klines.items():
            for k in reversed(klines):
                if k["time"] <= ts:
                    price_cache[sym] = k["close"]
                    kline_cache[sym] = k
                    break
        
        # --- 检查持仓 ---
        to_close = []
        for pos in positions:
            sym = pos["symbol"]
            pk = kline_cache.get(sym)
            if not pk:
                continue
            
            triggered = None
            fill_price = pk["close"]
            close_qty = pos.get("remaining_qty", pos["position_usd"])  # 平仓金额
            is_partial = False  # 是否分批平仓
            
            # 当前浮动盈亏%
            entry = pos["entry_price"]
            if pos["direction"] == "long":
                pnl_raw = (pk["close"] - entry) / entry
            else:
                pnl_raw = (entry - pk["close"]) / entry
            
            # 1. 止损检查（用K线low/high）
            if pos["direction"] == "long":
                if pk["low"] <= pos["stop_loss"]:
                    triggered = "止损"
                    fill_price = pos["stop_loss"]
            else:
                if pk["high"] >= pos["stop_loss"]:
                    triggered = "止损"
                    fill_price = pos["stop_loss"]
            
            # 2. 止盈检查
            if not triggered:
                if pos["direction"] == "long":
                    if pk["high"] >= pos["take_profit"]:
                        if PARTIAL_TP_ENABLED and not pos.get("partial_done"):
                            # 分批: 先平50%
                            triggered = "分批止盈(50%)"
                            fill_price = pos["take_profit"]
                            is_partial = True
                        else:
                            triggered = "止盈"
                            fill_price = pos["take_profit"]
                else:
                    if pk["low"] <= pos["take_profit"]:
                        if PARTIAL_TP_ENABLED and not pos.get("partial_done"):
                            triggered = "分批止盈(50%)"
                            fill_price = pos["take_profit"]
                            is_partial = True
                        else:
                            triggered = "止盈"
                            fill_price = pos["take_profit"]
            
            # 3. 趋势跟随止损（v6核心新增）
            if not triggered and TREND_TRAIL_ENABLED and pos.get("trend_trail_active"):
                # 用EMA9做移动止损
                if sym in all_klines:
                    tech_now = get_tech_at(all_klines[sym], ts)
                    ema9 = tech_now.get("ema9")
                    if ema9:
                        if pos["direction"] == "long":
                            trail_sl = ema9 * 0.995  # 略低于EMA9
                            if pk["low"] <= trail_sl and trail_sl > pos["stop_loss"]:
                                pos["stop_loss"] = trail_sl  # 只升不降
                                if pk["close"] <= trail_sl:
                                    triggered = "趋势跟随止损"
                                    fill_price = trail_sl
                        else:
                            trail_sl = ema9 * 1.005  # 略高于EMA9
                            if pk["high"] >= trail_sl and trail_sl < pos["stop_loss"]:
                                pos["stop_loss"] = trail_sl  # 只降不升
                                if pk["close"] >= trail_sl:
                                    triggered = "趋势跟随止损"
                                    fill_price = trail_sl
            
            # 启动趋势跟随
            if not triggered and not pos.get("trend_trail_active") and TREND_TRAIL_ENABLED:
                if pnl_raw >= TREND_TRAIL_TRIGGER:
                    pos["trend_trail_active"] = True
            
            # 4. 移动止盈
            if not triggered and pos.get("trail_active"):
                if pos["direction"] == "long":
                    if pk["high"] > pos.get("trail_high", 0):
                        pos["trail_high"] = pk["high"]
                    pullback = (pos["trail_high"] - pk["close"]) / pos["trail_high"]
                    if pullback >= TRAILING_TP_STEP:
                        triggered = "移动止盈"
                        fill_price = pk["close"]
                else:
                    if pk["low"] < pos.get("trail_low", float("inf")):
                        pos["trail_low"] = pk["low"]
                    bounce = (pk["close"] - pos["trail_low"]) / pos["trail_low"]
                    if bounce >= TRAILING_TP_STEP:
                        triggered = "移动止盈"
                        fill_price = pk["close"]
            
            if not triggered and not pos.get("trail_active"):
                if pnl_raw >= TRAILING_TP_TRIGGER:
                    pos["trail_active"] = True
                    pos["trail_high"] = pk["high"]
                    pos["trail_low"] = pk["low"]
            
            # 5. 时间止损
            if not triggered:
                try:
                    et = datetime.fromisoformat(pos["entry_time"])
                    if et.tzinfo is None:
                        et = et.replace(tzinfo=TZ_UTC8)
                    hours = (dt - et).total_seconds() / 3600
                    if hours >= MAX_HOLD_HOURS:
                        triggered = "时间止损(超48h)"
                        fill_price = pk["close"]
                    elif hours >= TIME_DECAY_START:
                        # 收紧止损到50%
                        if pos["direction"] == "long":
                            orig_dist = entry - pos.get("original_sl", pos["stop_loss"])
                            new_sl = entry - orig_dist * 0.5
                            if new_sl > pos["stop_loss"]:
                                pos["stop_loss"] = new_sl
                        else:
                            orig_dist = pos.get("original_sl", pos["stop_loss"]) - entry
                            new_sl = entry + orig_dist * 0.5
                            if new_sl < pos["stop_loss"]:
                                pos["stop_loss"] = new_sl
                except:
                    pass
            
            if triggered:
                if is_partial:
                    # 分批平仓: 只平50%，剩余移止损到成本
                    half_usd = pos["remaining_qty"] * PARTIAL_TP_RATIO
                    lev = pos["leverage"]
                    if pos["direction"] == "long":
                        half_pnl_pct = (fill_price - entry) / entry * 100 * lev
                    else:
                        half_pnl_pct = (entry - fill_price) / entry * 100 * lev
                    half_pnl_usd = half_pnl_pct / 100 * half_usd
                    
                    balance += half_pnl_usd
                    daily_pnl[today_str] = daily_pnl.get(today_str, 0) + half_pnl_usd
                    
                    # 记录这笔分批止盈
                    all_trades.append({
                        **{k: v for k, v in pos.items() if k != "remaining_qty"},
                        "exit_price": round(fill_price, 8),
                        "exit_time": dt.strftime("%Y-%m-%dT%H:%M:%S"),
                        "exit_reason": triggered,
                        "pnl_pct": round(half_pnl_pct, 2),
                        "pnl_usd": round(half_pnl_usd, 2),
                        "position_usd": round(half_usd, 2),
                        "status": "closed",
                    })
                    
                    # 剩余仓位
                    pos["remaining_qty"] -= half_usd
                    pos["partial_done"] = True
                    
                    # 剩余止损移到成本价（保本）
                    if PARTIAL_TP_MOVE_TO_BREAKEVEN:
                        pos["stop_loss"] = entry
                    
                    print(f"  分批止盈 #{pos['id']} {sym} 平50% @{fill_price:.4f} +{half_pnl_usd:.1f}U")
                else:
                    to_close.append((pos, fill_price, triggered))
        
        # 执行全仓平仓
        for pos, price, reason in to_close:
            entry = pos["entry_price"]
            lev = pos["leverage"]
            qty = pos.get("remaining_qty", pos["position_usd"])
            if pos["direction"] == "long":
                pnl_pct = (price - entry) / entry * 100 * lev
            else:
                pnl_pct = (entry - price) / entry * 100 * lev
            pnl_usd = pnl_pct / 100 * qty
            
            max_loss = -qty * 2
            if pnl_usd < max_loss:
                pnl_usd = max_loss
                pnl_pct = max_loss / qty * 100
            
            balance += pnl_usd
            daily_pnl[today_str] = daily_pnl.get(today_str, 0) + pnl_usd
            
            # 连亏跟踪
            sym = pos["symbol"]
            if pnl_usd < 0:
                consecutive_losses[sym] = consecutive_losses.get(sym, 0) + 1
            else:
                consecutive_losses[sym] = 0
            
            all_trades.append({
                **pos, "exit_price": round(price, 8), "exit_time": dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "exit_reason": reason, "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 2),
                "status": "closed",
            })
            positions.remove(pos)
            cooldowns[sym] = ts
        
        # --- 扫描新信号 ---
        dt_utc = datetime.utcfromtimestamp(ts / 1000)
        is_funding_time = dt_utc.hour % 8 == 0 and dt_utc.minute == 0
        
        if is_funding_time and len(positions) < MAX_POSITIONS and balance > 100:
            # 每日亏损保护
            today_loss = daily_pnl.get(today_str, 0)
            max_daily_loss = balance * DAILY_MAX_LOSS_PCT / 100
            if today_loss < -max_daily_loss:
                continue
            
            btc_before = [k for k in btc_klines if k["time"] <= ts]
            btc_chg = 0
            if len(btc_before) >= 24:
                btc_chg = (btc_before[-1]["close"] - btc_before[-24]["close"]) / btc_before[-24]["close"] * 100
            
            candidates = []
            open_symbols = set(p["symbol"] for p in positions)
            
            # 费率策略
            for sym in all_funding:
                if sym in open_symbols:
                    continue
                
                # v7: 连亏冷却+反转确认
                consec = consecutive_losses.get(sym, 0)
                cooldown_h = COOLDOWN_HOURS if consec < 2 else COOLDOWN_CONSECUTIVE_LOSS
                if ts - cooldowns.get(sym, 0) < cooldown_h * 3600 * 1000:
                    continue
                
                f_hist = [f for f in all_funding[sym] if f["time"] <= ts]
                if sym in all_klines:
                    tech = get_tech_at(all_klines[sym], ts)
                else:
                    tech = {"trend": "neutral", "rsi": 50.0, "atr_pct": 0.0, "ema9": None, "ema21": None}
                
                for scanner_fn in [scan_extreme_neg_funding, scan_extreme_pos_funding]:
                    sig = scanner_fn(f_hist, tech)
                    if sig:
                        # v7: 连亏币种需反转确认
                        if consec >= 2:
                            if sym not in all_klines or not check_reversal_confirm(all_klines[sym], ts, sig["direction"]):
                                continue
                        sig["symbol"] = sym
                        candidates.append(sig)
                        signals_found += 1
            
            # K线策略
            for sym in all_klines:
                if sym in open_symbols:
                    continue
                consec = consecutive_losses.get(sym, 0)
                cooldown_h = COOLDOWN_HOURS if consec < 2 else COOLDOWN_CONSECUTIVE_LOSS
                if ts - cooldowns.get(sym, 0) < cooldown_h * 3600 * 1000:
                    continue
                
                tech = get_tech_at(all_klines[sym], ts)
                
                for scanner_fn in [scan_crash_bounce, scan_pump_short]:
                    sig = scanner_fn(all_klines[sym], ts, tech)
                    if sig:
                        # v7: 连亏币种需反转确认
                        if consec >= 2:
                            if not check_reversal_confirm(all_klines[sym], ts, sig["direction"]):
                                continue
                        sig["symbol"] = sym
                        candidates.append(sig)
                        signals_found += 1
            
            # 过滤
            valid = []
            for c in candidates:
                vol = vol_map.get(c["symbol"], 0)
                score = env_score_v6(c, btc_chg, vol)
                if score < 0:
                    signals_filtered += 1
                    continue
                if score >= MIN_ENV_SCORE:
                    c["env_score"] = score
                    valid.append(c)
                else:
                    signals_filtered += 1
            
            valid.sort(key=lambda x: ({"S": 0, "A": 1}.get(x["strength"], 2), -x.get("env_score", 0)))
            
            for c in valid:
                if len(positions) >= MAX_POSITIONS:
                    break
                pk_close = price_cache.get(c["symbol"])
                if not pk_close or pk_close <= 0:
                    continue
                
                price = pk_close
                sl_pct = c["sl_pct"]
                tp_pct = c["tp_pct"]
                
                # v6: 风险定仓
                pos_usd = calc_position_size(balance, sl_pct, LEVERAGE)
                
                if c["direction"] == "long":
                    sl = price * (1 - sl_pct)
                    tp = price * (1 + tp_pct)
                else:
                    sl = price * (1 + sl_pct)
                    tp = price * (1 - tp_pct)
                
                tech = c.get("tech", {})
                position = {
                    "id": f"{len(all_trades) + len(positions) + 1:03d}",
                    "symbol": c["symbol"], "direction": c["direction"], "leverage": LEVERAGE,
                    "position_pct": round(pos_usd / balance * 100, 1),
                    "position_usd": round(pos_usd, 2), "notional_usd": round(pos_usd * LEVERAGE, 2),
                    "entry_price": price, "stop_loss": round(sl, 8), "take_profit": round(tp, 8),
                    "original_sl": round(sl, 8),  # 保存原始止损
                    "entry_time": dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "signal_type": c["type"], "signal_strength": c["strength"],
                    "signal_reason": c.get("reason", ""), "env_score": c.get("env_score", 0),
                    "signal_rr": c.get("rr", 0),
                    "signal_sl_pct": round(sl_pct * 100, 2),
                    "signal_tp_pct": round(tp_pct * 100, 2),
                    "tech_snapshot": {
                        "ema_trend": tech.get("trend", "N/A"),
                        "rsi": round(tech.get("rsi", 50), 1),
                        "atr_pct": round(tech.get("atr_pct", 0), 3),
                    },
                    "status": "open",
                    "remaining_qty": round(pos_usd, 2),
                    "partial_done": False,
                    "trail_active": False, "trail_high": 0, "trail_low": float("inf"),
                    "trend_trail_active": False,
                }
                positions.append(position)
                
                risk_usd = balance * RISK_PER_TRADE
                print(f"  开仓 #{position['id']} {c['symbol']} {'多' if c['direction']=='long' else '空'} @{price:.4f} "
                      f"仓位${pos_usd:.0f} SL={sl_pct*100:.1f}% TP={tp_pct*100:.1f}% RR={c['rr']:.1f} "
                      f"风险${risk_usd:.0f} ATR={tech.get('atr_pct',0)*100:.1f}%")
                # v7+: 不break，允许一轮开多个仓位(到MAX_POSITIONS限制)
        
        # 净值和回撤
        unrealized = 0
        for pos in positions:
            sym = pos["symbol"]
            cp = price_cache.get(sym)
            if cp:
                entry = pos["entry_price"]
                qty = pos.get("remaining_qty", pos["position_usd"])
                if pos["direction"] == "long":
                    raw = (cp - entry) / entry
                else:
                    raw = (entry - cp) / entry
                unrealized += raw * qty * pos["leverage"]
        
        equity = balance + unrealized
        if equity > max_equity:
            max_equity = equity
        dd = (max_equity - equity) / max_equity * 100 if max_equity > 0 else 0
        if dd > max_drawdown:
            max_drawdown = dd
        
        if (step_i + 1) % 200 == 0:
            print(f"  [{step_i+1}/{len(all_times)}] {dt.strftime('%m-%d %H:%M')} 余额=${balance:.0f} 持仓{len(positions)} 交易{len(all_trades)}笔")
    
    # 强制平仓
    for pos in positions[:]:
        sym = pos["symbol"]
        cp = price_cache.get(sym, pos["entry_price"])
        entry = pos["entry_price"]
        qty = pos.get("remaining_qty", pos["position_usd"])
        if pos["direction"] == "long":
            pnl_pct = (cp - entry) / entry * 100 * pos["leverage"]
        else:
            pnl_pct = (entry - cp) / entry * 100 * pos["leverage"]
        pnl_usd = pnl_pct / 100 * qty
        balance += pnl_usd
        all_trades.append({
            **pos, "exit_price": round(cp, 8), "exit_time": "回测结束",
            "exit_reason": "回测结束", "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 2),
            "status": "closed",
        })
    
    # ============================================
    # 统计
    # ============================================
    print("\n" + "=" * 60)
    print("📊 回测 v7+ 结果 — 高频高收益版")
    print("=" * 60)
    
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
    print(f"  最大回撤:   -{max_drawdown:.1f}%")
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
    
    # 每笔实际风险
    if closed:
        risks = [abs(t.get("pnl_usd", 0)) for t in losses]
        if risks:
            print(f"  平均每笔亏损: -{sum(risks)/len(risks):.2f}U (目标${INITIAL_BALANCE*RISK_PER_TRADE:.0f})")
    
    print(f"\n📊 过滤统计:")
    print(f"  发现信号:   {signals_found}")
    print(f"  被过滤:     {signals_filtered}")
    print(f"  实际交易:   {len(closed)}")
    if signals_found > 0:
        print(f"  信号通过率: {len(closed)/signals_found*100:.1f}%")
    
    # 按策略
    print(f"\n📋 各策略表现:")
    by_type = defaultdict(lambda: {"win": 0, "loss": 0, "pnl": 0.0, "trades": []})
    for t in closed:
        st = t.get("signal_type", "unknown")
        pnl = t.get("pnl_usd", 0) or 0
        by_type[st]["trades"].append(t)
        if pnl > 0: by_type[st]["win"] += 1
        else: by_type[st]["loss"] += 1
        by_type[st]["pnl"] += pnl
    
    type_names = {
        "extreme_neg_funding": "极端负费率(做多)",
        "extreme_pos_funding": "极端正费率(做空)",
        "crash_bounce": "暴跌反弹(做多)",
        "pump_short": "暴涨回落(做空)",
    }
    
    for st, s in sorted(by_type.items(), key=lambda x: x[1]["pnl"], reverse=True):
        total = s["win"] + s["loss"]
        wr = s["win"] / total * 100 if total > 0 else 0
        emoji = "✅" if s["pnl"] > 0 else "❌"
        name = type_names.get(st, st)
        print(f"\n  {emoji} {name}: {s['win']}W/{s['loss']}L | 胜率{wr:.0f}% | PnL {s['pnl']:+.2f}U")
        for t in s["trades"][:15]:
            d = "多" if t["direction"] == "long" else "空"
            pnl = t.get("pnl_usd", 0)
            rr = t.get("signal_rr", 0)
            tech = t.get("tech_snapshot", {})
            sl_info = f"SL={t.get('signal_sl_pct',0):.1f}%"
            pos_usd = t.get("position_usd", 0)
            print(f"     #{t['id']} {t['symbol']} {d} [{t.get('signal_strength','')}] {t.get('entry_time','')[:10]} "
                  f"${pos_usd:.0f} {sl_info} ATR={tech.get('atr_pct',0)*100:.1f}% → {pnl:+.1f}U ({t.get('exit_reason','')})")
        if len(s["trades"]) > 15:
            print(f"     ... 还有 {len(s['trades'])-15} 笔")
    
    # 按平仓原因
    print(f"\n📊 按平仓原因:")
    for reason in ["止盈", "分批止盈(50%)", "趋势跟随止损", "移动止盈", "止损", "时间止损(超48h)", "回测结束"]:
        r_trades = [t for t in closed if t.get("exit_reason") == reason]
        if not r_trades: continue
        r_pnl = sum(t.get("pnl_usd", 0) for t in r_trades)
        r_wins = sum(1 for t in r_trades if (t.get("pnl_usd", 0) or 0) > 0)
        print(f"  {reason}: {len(r_trades)}笔 | 胜{r_wins}笔 | PnL {r_pnl:+.2f}U")
    
    # v5 vs v6 对比
    print(f"\n📊 v5 vs v6 对比:")
    print(f"  {'指标':<20} {'v5':>12} {'v6':>12}")
    print(f"  {'-'*44}")
    print(f"  {'仓位计算':<20} {'固定10%':>12} {'风险定仓':>12}")
    print(f"  {'ATR止损倍数':<20} {'1.5':>12} {'2.0':>12}")
    print(f"  {'最大止损':<20} {'5%':>12} {'15%':>12}")
    print(f"  {'趋势跟随止损':<20} {'无':>12} {'EMA9跟踪':>12}")
    print(f"  {'分批止盈':<20} {'无':>12} {'50%+保本':>12}")
    print(f"  {'连亏冷却':<20} {'24h':>12} {'72h':>12}")
    if closed:
        wr_v6 = f"{len(wins)/len(closed)*100:.0f}%"
        pnl_v6 = f"{total_pnl:+.0f}U"
        dd_v6 = f"-{max_drawdown:.0f}%"
    else:
        wr_v6 = pnl_v6 = dd_v6 = "N/A"
    print(f"  {'胜率':<20} {'18%':>12} {wr_v6:>12}")
    print(f"  {'总盈亏':<20} {'-$176':>12} {pnl_v6:>12}")
    print(f"  {'最大回撤':<20} {'-7.1%':>12} {dd_v6:>12}")
    
    # 保存
    result = {
        "version": "v6",
        "start_time": START_TIME.strftime("%Y-%m-%d"),
        "end_time": END_TIME.strftime("%Y-%m-%d"),
        "initial_balance": INITIAL_BALANCE,
        "final_balance": round(balance, 2),
        "total_pnl": round(total_pnl, 2),
        "total_trades": len(closed),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins)/len(closed)*100, 1) if closed else 0,
        "max_drawdown": round(max_drawdown, 1),
        "signals_found": signals_found,
        "signals_filtered": signals_filtered,
        "trades": all_trades,
    }
    out_path = Path(__file__).parent / "data" / "backtest_v7plus_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 保存到: {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    run_backtest()
