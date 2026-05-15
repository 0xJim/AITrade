#!/usr/bin/env python3
"""
回测脚本 v18 — v6微调优化版
基于v6回测数据(56笔/64.3%胜率/+$688)深度分析后微调:

v6核心优势:
- 极端负费率做多(45笔/64%/+$462) + 极端正费率做空(10笔/70%/+$296)
- 低SL(<5%)交易78%胜率赚$719是主力
- 仓位$200-600区间胜率75-100%

v6核心问题:
- SL>8%的交易44%胜率亏$104 (DASH高ATR宽SL)
- DASH 9笔亏$223(4.5%初始资金), 无单币亏损上限
- SL<5%高胜率区间仓位太小(因RISK固定1.2%)

v18微调(3项, 不改v6核心逻辑):
1. MAX_SL_PCT = 8%: 过滤SL>8%的宽止损交易
2. SYMBOL_LOSS_CAP = -100U: 单币累计亏$100后暂停该币2周
3. SL<5% 仓位×1.5: 低SL高胜率区间加大投入
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

# === v18 交易参数 (基于v6核心) ===
INITIAL_BALANCE = 5000.0
LEVERAGE = 3
RISK_PER_TRADE = 0.012       # v6原始: 1.2%风险定仓
MAX_POSITIONS = 3             # 保持3
COOLDOWN_HOURS = 12           # 保持12h
COOLDOWN_CONSECUTIVE_LOSS = 96  # 保持96h(4天)
DAILY_MAX_LOSS_PCT = 4        # 日亏保护4%
MAX_TOTAL_LOSS_PER_SYMBOL = 999  # v18: 不用次数限制，改用金额限制

# === v18 微调参数 ===
MAX_SL_PCT_STRICT = 0.08      # 微调1: SL上限8% (v6无上限，导致SL=15%宽止损)
SYMBOL_LOSS_CAP_USD = 100     # 微调2: 单币累计亏$100后暂停该币
SYMBOL_LOSS_COOLDOWN_H = 336  # 暂停2周(336h)而非永久
POSITION_BOOST_LOW_SL = 1.5   # 微调3: SL<5%仓位加大1.5倍
POSITION_BOOST_SL_THRESHOLD = 0.05  # SL<5%触发加仓

# === v9 策略阈值 ===
EXTREME_NEG_FUNDING = -0.10
EXTREME_POS_FUNDING = 0.15
MIN_ENV_SCORE = 4             # 基础门槛保持4
MIN_RR_RATIO = 2.0            # v9: 2.5→2.0 — 放宽RR让更多信号通过

# === v9 ATR动态止损 ===
# 低ATR(<3%): 75%胜率赚钱主力 → 宽SL+大仓位
# 中ATR(3-6%): 中间区不跳过但要求更高env
# 高ATR(>6%): 短SL快波段
ATR_SL_MULTIPLIER_LOW = 3.0   # 低ATR用3.0倍(保持)
ATR_SL_MULTIPLIER_HIGH = 1.5  # 高ATR用1.5倍(保持)
ATR_LOW_THRESHOLD = 0.03      # 低ATR区分界线
ATR_DANGER_LOW = 0.03         # v9: 不再跳过中间区
ATR_DANGER_HIGH = 0.06
ATR_DANGER_SKIP = False       # v9: 关闭ATR危险区跳过
ATR_MAX_ALLOWED = 0.10        # v9b: ATR>10%视为异常，跳过(20%太宽松)
MIN_SL_PCT = 0.025            # v9: 3%→2.5% — 低ATR可以更紧
MAX_SL_PCT = 0.08             # 保持8%
DEFAULT_SL_PCT = 0.04
DEFAULT_TP_PCT = 0.10
ATR_PERIOD = 14
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14

# === v9 动态仓位 ===
# 注意: 低ATR本身因SL窄而自动获得更大仓位，不需要额外加成
# 只保留强信号(env≥7)的加成
POSITION_BOOST_HIGH_ENV = 1.2  # v9b: env_score≥7 → 仓位×1.2 (从1.3降低)
POSITION_MAX_PCT = 0.20        # v9b: 仓位上限25%→20% (低ATR仓位已经够大)
MAX_LOSS_PER_TRADE = 0.02      # v9b: 每笔最大亏损 = 余额的2% ($100)

# === v9 连亏处理 ===
REVERSAL_CONFIRM_ENABLED = True
REVERSAL_CONFIRM_LOOKBACK = 24
REVERSAL_MIN_REVERSAL_CANDLES = 5

# === v9 趋势跟随止损 ===
TREND_TRAIL_ENABLED = True
TREND_TRAIL_TRIGGER = 0.05    # v9: 2%→5% — 让利润多跑一段再跟踪

# === v9 分批止盈 ===
PARTIAL_TP_ENABLED = True
PARTIAL_TP_RATIO = 0.50       # 到TP先平50%
PARTIAL_TP_MOVE_TO_BREAKEVEN = True

# === 移动止盈 ===
TRAILING_TP_TRIGGER = 0.05    # 盈利5%启动
TRAILING_TP_STEP = 0.04       # 回撤4%锁定

# === 时间止损 ===
MAX_HOLD_HOURS = 72            # v10c: 48→72h (延长持仓，>48h胜率88%)
TIME_DECAY_START = 24
GRACE_PERIOD_HOURS = 4         # v10c: 入场后4h宽限期，不扫SL

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
def calc_position_size(balance, sl_pct, leverage, env_score=0, atr_pct=0):
    """
    v9b: 动态风险定仓 — 带最大亏损封顶
    基础: risk = balance × RISK_PER_TRADE
    强信号(env≥7): 仓位×1.2
    最大亏损封顶: 每笔最多亏 balance × MAX_LOSS_PER_TRADE
    """
    risk_usd = balance * RISK_PER_TRADE
    position_usd = risk_usd / (sl_pct * leverage)
    
    # v9b: 只有强信号才加成
    if env_score >= 7:
        position_usd *= POSITION_BOOST_HIGH_ENV
    
    # v9b: 最大亏损封顶 — 确保即使止损也不超过限额
    max_loss_usd = balance * MAX_LOSS_PER_TRADE
    max_pos_by_loss = max_loss_usd / (sl_pct * leverage)
    position_usd = min(position_usd, max_pos_by_loss)
    
    # 上限: 不超过余额的20%
    max_pos = balance * POSITION_MAX_PCT
    position_usd = min(position_usd, max_pos)
    # 下限: 至少$50
    position_usd = max(position_usd, 50)
    return round(position_usd, 2)


# === v6: 信号扫描 ===
def _calc_sl_tp(atr, is_high_atr=False):
    """v9: 统一SL/TP计算 — 按ATR区间动态调整"""
    if atr > 0:
        # v9: ATR异常值过滤
        if atr > ATR_MAX_ALLOWED:
            return None, None  # 跳过异常ATR
        
        if atr >= ATR_DANGER_HIGH:
            # 高ATR(>6%): 短SL快波段
            atr_mult = ATR_SL_MULTIPLIER_HIGH  # 1.5x
            rr_target = 3.0
        elif atr <= ATR_LOW_THRESHOLD:
            # 低ATR(<3%): 宽SL安全区(75%胜率)
            atr_mult = ATR_SL_MULTIPLIER_LOW  # 3.0x
            rr_target = MIN_RR_RATIO  # 2.0
        else:
            # 中间区(3-6%): v9不再跳过，但用折中参数
            atr_mult = 2.0
            rr_target = 2.0
        sl_pct = min(atr * atr_mult, MAX_SL_PCT)
        sl_pct = max(sl_pct, MIN_SL_PCT)
        tp_pct = sl_pct * rr_target
    else:
        sl_pct = DEFAULT_SL_PCT
        tp_pct = sl_pct * MIN_RR_RATIO
    return round(sl_pct, 4), round(tp_pct, 4)


def scan_extreme_neg_funding(funding_at_time, tech):
    if len(funding_at_time) < 3:
        return None
    recent = funding_at_time[-8:]
    current = recent[-1]["rate"]
    if current >= EXTREME_NEG_FUNDING:
        return None
    neg_count = sum(1 for r in recent if r["rate"] < -0.03)
    if neg_count < 3:
        return None
    avg = sum(r["rate"] for r in recent) / len(recent)
    
    if tech["trend"] == "down":
        return None
    if tech["rsi"] > 65:
        return None
    
    strength = "S" if avg < -0.20 else "A" if avg < -0.10 else "B"
    if strength == "B":
        return None
    
    # v9: ATR异常值过滤 + SL/TP计算
    atr = tech["atr_pct"]
    sl_pct, tp_pct = _calc_sl_tp(atr)
    if sl_pct is None:
        return None
    
    if tech["trend"] == "up" and strength != "S":
        strength = "S"
    if avg < -0.30:
        strength = "S"
    
    return {
        "type": "extreme_neg_funding", "direction": "long", "strength": strength,
        "sl_pct": sl_pct, "tp_pct": tp_pct,
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
    if pos_count < 3:
        return None
    avg = sum(r["rate"] for r in recent) / len(recent)
    
    if tech["trend"] == "up":
        return None
    if tech["rsi"] < 35:
        return None
    
    strength = "S" if avg > 0.25 else "A" if avg > 0.15 else "B"
    if strength == "B":
        return None
    
    atr = tech["atr_pct"]
    sl_pct, tp_pct = _calc_sl_tp(atr)
    if sl_pct is None:
        return None
    
    if tech["trend"] == "down" and strength != "S":
        strength = "S"
    if avg > 0.30:
        strength = "S"
    
    return {
        "type": "extreme_pos_funding", "direction": "short", "strength": strength,
        "sl_pct": sl_pct, "tp_pct": tp_pct,
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
    if change_pct >= -30:
        return None
    recent3 = last24[-3:]
    if recent3[-1]["close"] >= recent3[-2]["close"]:
        if tech["trend"] == "down":
            return None
        if tech["rsi"] > 65:
            return None
        strength = "A" if change_pct < -40 else "B"
        if strength == "B":
            return None
        atr = tech["atr_pct"]
        sl_pct, tp_pct = _calc_sl_tp(atr)
        if sl_pct is None:
            return None
        return {
            "type": "crash_bounce", "direction": "long", "strength": strength,
            "sl_pct": sl_pct, "tp_pct": tp_pct,
            "rr": round(tp_pct / sl_pct, 2),
            "reason": f"24h暴跌{change_pct:+.1f}%后企稳 | ATR={atr*100:.1f}%",
            "tech": tech,
        }
    return None


def scan_coiling_breakout(klines_hist, ts, tech):
    """
    v10 蓄势突破信号 — 抓"快要启动"的币
    逻辑: 价格横盘收缩 → 量能突然放大 → 准备突破
    """
    k_before = [k for k in klines_hist if k["time"] <= ts]
    if len(k_before) < 48:  # 至少48小时数据
        return None
    
    recent = k_before[-48:]  # 最近48小时
    closes = [k["close"] for k in recent]
    volumes = [k["volume"] for k in recent]
    current_price = closes[-1]
    
    # === 1. 收缩检测: 最近24h价格波动率 ===
    recent24_closes = closes[-24:]
    recent24_range = (max(recent24_closes) - min(recent24_closes)) / min(recent24_closes)
    if recent24_range > 0.10:  # 10天内波动>10%不算收缩
        return None
    
    # 对比前24h: 波动率在收窄
    prev24_closes = closes[:24]
    prev24_range = (max(prev24_closes) - min(prev24_closes)) / min(prev24_closes) if min(prev24_closes) > 0 else 999
    if prev24_range <= recent24_range:  # 没有在收窄
        return None
    
    # === 2. 量能突破: 最近1h成交量 > 20h均量×3 (v10b: 2x→3x) ===
    if len(volumes) < 21:
        return None
    avg_vol_20 = sum(volumes[-21:-1]) / 20
    if avg_vol_20 <= 0:
        return None
    vol_surge = volumes[-1] / avg_vol_20
    if vol_surge < 3.0:  # v10b: 2x→3x 量能必须更强
        return None
    
    # === 3. 方向判断 ===
    ema21 = calc_ema(closes, EMA_SLOW)
    ema9 = calc_ema(closes, EMA_FAST)
    rsi = calc_rsi(closes, RSI_PERIOD)
    
    # 决定方向: 价格在EMA21上方 → 做多; 下方 → 做空
    if ema21 and current_price > ema21 * 1.002:
        direction = "long"
        # 做多确认: RSI不能超买
        if rsi > 65:
            return None
    elif ema21 and current_price < ema21 * 0.998:
        direction = "short"
        # 做空确认: RSI不能超卖
        if rsi < 35:
            return None
    else:
        return None  # 价格在EMA21附近震荡，方向不明
    
    # === 4. 评分 ===
    score = 0
    
    # 收缩程度(越窄越好)
    if recent24_range < 0.03:  # 波动<3%非常窄
        score += 3
    elif recent24_range < 0.06:  # 波动<6%
        score += 2
    else:
        score += 1
    
    # 收窄趋势明显
    if prev24_range > recent24_range * 2:  # 前期是现在的2倍以上
        score += 2
    elif prev24_range > recent24_range * 1.5:
        score += 1
    
    # 量能爆发程度
    if vol_surge > 5.0:  # 5倍量
        score += 3
    elif vol_surge > 3.0:  # 3倍量
        score += 2
    else:
        score += 1
    
    # 趋势方向一致
    if direction == "long" and ema9 and ema9 > ema21:
        score += 1
    elif direction == "short" and ema9 and ema9 < ema21:
        score += 1
    
    # RSI在有利区间
    if direction == "long" and 45 <= rsi <= 60:
        score += 1
    elif direction == "short" and 40 <= rsi <= 55:
        score += 1
    
    if score < 6:  # 至少6分(满分10)
        return None
    
    strength = "S" if score >= 9 else "A" if score >= 7 else "B"
    if strength == "B":
        return None
    
    # SL/TP — 蓄势突破用窄SL(波动本来就小)
    # v10c: 保持v10b参数，不做SL区间限制(数据证明强制限制反而增加止损)
    atr = tech["atr_pct"]
    if atr <= 0 or atr > 0.06:  # 蓄势期ATR应该很低
        return None
    
    sl_pct = max(atr * 3.5, 0.025)  # 3.5倍ATR，至少2.5%
    sl_pct = min(sl_pct, 0.06)      # 上限6%
    tp_pct = sl_pct * 2.5           # RR=2.5
    
    return {
        "type": "coiling_breakout",
        "direction": direction,
        "strength": strength,
        "sl_pct": round(sl_pct, 4),
        "tp_pct": round(tp_pct, 4),
        "rr": round(tp_pct / sl_pct, 2),
        "reason": f"蓄势突破 {direction=='long' and '↑' or '↓'} 收缩{recent24_range*100:.1f}%→{prev24_range*100:.1f}% 量能{vol_surge:.1f}x 评分{score}/10 | ATR={atr*100:.1f}%",
        "tech": tech,
    }


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
    if pullback < 8:
        return None
    if tech["trend"] == "up":
        return None
    if tech["rsi"] < 35:
        return None
    strength = "A" if pullback > 15 else "B"
    if strength == "B":
        return None
    atr = tech["atr_pct"]
    sl_pct, tp_pct = _calc_sl_tp(atr)
    if sl_pct is None:
        return None
    return {
        "type": "pump_short", "direction": "short", "strength": strength,
        "sl_pct": sl_pct, "tp_pct": tp_pct,
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
    print(f"📊 回测 v18 — v6微调优化版")
    print(f"时间: {START_TIME.strftime('%Y-%m-%d')} ~ {END_TIME.strftime('%Y-%m-%d')}")
    print(f"资金: ${INITIAL_BALANCE:.0f} | 杠杆: {LEVERAGE}x | 风险定仓: {RISK_PER_TRADE*100}%/笔")
    print(f"止损: 低ATR×{ATR_SL_MULTIPLIER_LOW}/高ATR×{ATR_SL_MULTIPLIER_HIGH}({MIN_SL_PCT*100}-{MAX_SL_PCT*100}%) | RR≥{MIN_RR_RATIO}")
    print(f"ATR异常过滤: >{ATR_MAX_ALLOWED*100}%跳过 | 最大亏损封顶: {MAX_LOSS_PER_TRADE*100}%/笔")
    print(f"同币种屏蔽: 累计亏{MAX_TOTAL_LOSS_PER_SYMBOL}次后永久不做")
    print(f"分批止盈: 到TP先平{PARTIAL_TP_RATIO*100:.0f}% | 移动止盈: {TRAILING_TP_TRIGGER*100}%→{TRAILING_TP_STEP*100}%回撤")
    print(f"趋势跟随: 盈利{TREND_TRAIL_TRIGGER*100}%后EMA9跟踪止损")
    print(f"冷却: 默认{COOLDOWN_HOURS}h | 连亏{COOLDOWN_CONSECUTIVE_LOSS}h(4天) | 日亏保护{DAILY_MAX_LOSS_PCT}%")
    print(f"反转确认: {'开启' if REVERSAL_CONFIRM_ENABLED else '关闭'}")
    print(f"v10c优化: 做空SL=3-4% | 4h宽限期 | MAX_HOLD=72h")
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
    symbol_total_losses = defaultdict(int)  # v8: symbol -> 累计亏损次数
    symbol_cum_pnl = defaultdict(float)     # v18: symbol -> 累计盈亏
    symbol_loss_cap_until = {}              # v18: symbol -> 暂停到此时间戳
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
            # v10c: 入场后GRACE_PERIOD_HOURS(4h)内不扫SL，避免被假突破扫掉
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
                        triggered = f"时间止损(超{MAX_HOLD_HOURS}h)"
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
                symbol_total_losses[sym] += 1  # v8: 累计亏损次数
            else:
                consecutive_losses[sym] = 0
            
            # v18微调2: 单币累计盈亏追踪 + 亏损上限
            symbol_cum_pnl[sym] += pnl_usd
            if symbol_cum_pnl[sym] <= -SYMBOL_LOSS_CAP_USD:
                symbol_loss_cap_until[sym] = ts + SYMBOL_LOSS_COOLDOWN_H * 3600 * 1000
            
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
        
        # v10: 蓄势突破每4小时扫; 费率策略只在费率时间扫
        should_scan_coiling = (dt_utc.hour % 4 == 0 and dt_utc.minute == 0) and len(positions) < MAX_POSITIONS and balance > 100
        
        if (is_funding_time or should_scan_coiling) and len(positions) < MAX_POSITIONS and balance > 100:
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
            
            # 费率策略 (只在费率时间)
            if is_funding_time:
                for sym in all_funding:
                    if sym in open_symbols:
                        continue
                    # v18: 单币亏损上限检查
                    cap_ts = symbol_loss_cap_until.get(sym, 0)
                    if ts < cap_ts:
                        continue
                    # v8: 累计亏3次后跳过
                    if symbol_total_losses.get(sym, 0) >= MAX_TOTAL_LOSS_PER_SYMBOL:
                        continue
                    
                    # v7: 连亏冷却+反转确认
                    consec = consecutive_losses.get(sym, 0)
                    cooldown_h = COOLDOWN_HOURS if consec < 2 else COOLDOWN_CONSECUTIVE_LOSS
                    if ts - cooldowns.get(sym, 0) < cooldown_h * 3600 * 1000:
                        continue
                    
                    # v8: 累计亏3次后永久屏蔽
                    total_losses = symbol_total_losses.get(sym, 0)
                    if total_losses >= MAX_TOTAL_LOSS_PER_SYMBOL:
                        continue
                    
                    f_hist = [f for f in all_funding[sym] if f["time"] <= ts]
                    if sym in all_klines:
                        tech = get_tech_at(all_klines[sym], ts)
                    else:
                        tech = {"trend": "neutral", "rsi": 50.0, "atr_pct": 0.0, "ema9": None, "ema21": None}
                    
                # v18: 开启极端负费率(v6核心信号, 45笔/64%/+$462)
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
            
            # K线策略 (含v10蓄势突破)
            for sym in all_klines:
                if sym in open_symbols:
                    continue
                # v18: 单币亏损上限检查
                cap_ts = symbol_loss_cap_until.get(sym, 0)
                if ts < cap_ts:
                    continue
                # v8: 累计亏3次后跳过
                if symbol_total_losses.get(sym, 0) >= MAX_TOTAL_LOSS_PER_SYMBOL:
                    continue
                consec = consecutive_losses.get(sym, 0)
                cooldown_h = COOLDOWN_HOURS if consec < 2 else COOLDOWN_CONSECUTIVE_LOSS
                if ts - cooldowns.get(sym, 0) < cooldown_h * 3600 * 1000:
                    continue
                
                # v8: 累计亏3次后永久屏蔽
                total_losses = symbol_total_losses.get(sym, 0)
                if total_losses >= MAX_TOTAL_LOSS_PER_SYMBOL:
                    continue
                
                tech = get_tech_at(all_klines[sym], ts)
                
                for scanner_fn in [scan_crash_bounce, scan_pump_short, scan_coiling_breakout]:
                    sig = scanner_fn(all_klines[sym], ts, tech)
                    if sig:
                        # v7: 连亏币种需反转确认
                        if consec >= 2:
                            if not check_reversal_confirm(all_klines[sym], ts, sig["direction"]):
                                continue
                        sig["symbol"] = sym
                        candidates.append(sig)
                        signals_found += 1
            
            # 过滤 (v10c: 做多env门槛+2, 因为做多胜率仅45%)
            valid = []
            for c in candidates:
                vol = vol_map.get(c["symbol"], 0)
                score = env_score_v6(c, btc_chg, vol)
                if score < 0:
                    signals_filtered += 1
                    continue
                # v8: 亏过的币种env_score门槛从4提到6
                min_score = MIN_ENV_SCORE
                # v18: 去掉亏过币种env门槛提高(v6无此限制，反而ENV<5胜率更高)
                if score >= min_score:
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
                
                # v18微调1: SL>8%直接跳过 (v6数据分析: SL>8%的44%胜率亏钱)
                if sl_pct > MAX_SL_PCT_STRICT:
                    signals_filtered += 1
                    continue
                
                # v9: 动态风险定仓 (传入env和ATR用于加成)
                tech = c.get("tech", {})
                atr_pct = tech.get("atr_pct", 0)
                env_score = c.get("env_score", 0)
                pos_usd = calc_position_size(balance, sl_pct, LEVERAGE, env_score, atr_pct)
                
                # v18微调3: SL<5%高胜率区间加大仓位 (v6数据: SL<5% 78%胜率)
                if sl_pct < POSITION_BOOST_SL_THRESHOLD:
                    pos_usd = pos_usd * POSITION_BOOST_LOW_SL
                
                # v8: 亏过的币种仓位减半
                sym_losses = symbol_total_losses.get(c["symbol"], 0)
                if sym_losses >= 1:
                    pos_usd = pos_usd * 0.5
                
                # v4: 做空高v8_score减仓 (与实盘cron_scan.py同步)
                v8_score = c.get("v8_score", 0)
                if c["direction"] == "short" and v8_score >= 5:
                    original_pos = pos_usd
                    pos_usd = round(pos_usd * 0.5, 2)
                
                # v18: 去掉静态黑名单(v6无黑名单，靠亏损上限管理)
                # bad_symbols = {}
                # if c["symbol"] in bad_symbols:
                #     continue
                
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
                    "v8_weighted_score": c.get("v8_score", 0),
                }
                positions.append(position)
                
                risk_usd = balance * RISK_PER_TRADE
                print(f"  开仓 #{position['id']} {c['symbol']} {'多' if c['direction']=='long' else '空'} @{price:.4f} "
                      f"仓位${pos_usd:.0f} SL={sl_pct*100:.1f}% TP={tp_pct*100:.1f}% RR={c['rr']:.1f} "
                      f"风险${risk_usd:.0f} ATR={tech.get('atr_pct',0)*100:.1f}%")
                break
        
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
    print("📊 回测 v10c 结果 — 数据驱动优化版")
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
        "coiling_breakout": "蓄势突破(多/空)",
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
    for reason in ["止盈", "分批止盈(50%)", "趋势跟随止损", "移动止盈", "止损", f"时间止损(超{MAX_HOLD_HOURS}h)", "回测结束"]:
        r_trades = [t for t in closed if t.get("exit_reason") == reason]
        if not r_trades: continue
        r_pnl = sum(t.get("pnl_usd", 0) for t in r_trades)
        r_wins = sum(1 for t in r_trades if (t.get("pnl_usd", 0) or 0) > 0)
        print(f"  {reason}: {len(r_trades)}笔 | 胜{r_wins}笔 | PnL {r_pnl:+.2f}U")
    
    # v10b vs v10c 对比
    print(f"\n📊 v10b vs v10c 对比:")
    print(f"  {'指标':<20} {'v10b':>12} {'v10c':>12}")
    print(f"  {'-'*44}")
    print(f"  {'SL区间':<20} {'2.5-6%':>12} {'2.5-6%(同)':>12}")
    print(f"  {'4h宽限期':<20} {'无':>12} {'有':>12}")
    print(f"  {'MAX_HOLD':<20} {'48h':>12} {'72h':>12}")
    if closed:
        wr_v10c = f"{len(wins)/len(closed)*100:.0f}%"
        pnl_v10c = f"{total_pnl:+.0f}U"
        dd_v10c = f"-{max_drawdown:.0f}%"
    else:
        wr_v10c = pnl_v10c = dd_v10c = "N/A"
    print(f"  {'胜率':<20} {'56%':>12} {wr_v10c:>12}")
    print(f"  {'总盈亏':<20} {'+$958':>12} {pnl_v10c:>12}")
    print(f"  {'最大回撤':<20} {'-7%':>12} {dd_v10c:>12}")
    
    # 保存
    result = {
        "version": "v10c",
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
    out_path = Path(__file__).parent / "data" / "backtest_v18_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 保存到: {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    run_backtest()
