#!/usr/bin/env python3
"""
回测脚本 v8 — 六维加权评分版 (v3减法优化)
基于从OKX 3个Skill整合的完整交易系统:
- 六维加权评分(-100~+100)替代原10维加法评分
- 信号质量评分(0-100)+三要素入场条件
- Kelly动态仓位(2-20%)替代固定仓位
- K线形态识别(12种)+多时间框架(1h/4h)
- SQLite复盘数据库

v3减法优化:
- 移除MAX_HOLD_HOURS/TIME_DECAY_START — 无回测支撑
- 移除DAILY_MAX_LOSS_PCT — 无回测支撑
- 移除B级信号封杀 — 让评分系统自然过滤
- 移除趋势/RSI硬拒绝 — 交由六维评分软处理
- 移除ATR SL 5%上限 — 完全交给ATR动态
- 放宽扫描阈值 — EXTREME_NEG/POS_FUNDING同步config.py
"""
import sys
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

# === 从params.yaml加载参数（面板调参用）===
BASE_DIR = Path(__file__).parent
PARAMS_FILE = BASE_DIR / "params.yaml"
_PARAMS = None

def load_params():
    """从params.yaml加载参数"""
    global _PARAMS
    try:
        import yaml
        with open(PARAMS_FILE, "r", encoding="utf-8") as f:
            _PARAMS = yaml.safe_load(f)
    except Exception:
        _PARAMS = None
    return _PARAMS

def P(section, key, default=None):
    """获取参数: P('基础', '杠杆倍数', 3)"""
    if _PARAMS is None:
        load_params()
    if _PARAMS and section in _PARAMS and key in _PARAMS[section]:
        return _PARAMS[section][key]
    return default

# 面板调用时自动加载
if "--from-params" in sys.argv:
    load_params()

FAPI_LIVE = "https://fapi.binance.com"
TZ_UTC8 = timezone(timedelta(hours=8))

# === v8 交易参数（从yaml读取，有默认值）===
INITIAL_BALANCE = P("基础", "初始资金", 5000.0)
LEVERAGE = P("基础", "杠杆倍数", 3)
MAX_POSITIONS = P("基础", "最大持仓数", 2)
COOLDOWN_HOURS = P("基础", "冷却时间_小时", 24)

# === v8 风险定仓（保留旧参数兼容，v8用Kelly）===
RISK_PER_TRADE = P("基础", "每笔风险比例", 0.01)

# === v8 策略阈值 ===
EXTREME_NEG_FUNDING = P("信号触发", "极端负费率阈值", -0.08)
EXTREME_POS_FUNDING = P("信号触发", "极端正费率阈值", 0.10)
ATR_SL_MULTIPLIER = P("止损止盈", "ATR止损乘数", 1.5)
MIN_SL_PCT = P("止损止盈", "最小止损百分比", 0.03)
DEFAULT_SL_PCT = P("止损止盈", "默认止损", 0.05)
DEFAULT_TP_PCT = P("止损止盈", "默认止盈", 0.10)
ATR_PERIOD = P("技术指标", "ATR周期", 14)
EMA_FAST = P("技术指标", "EMA快线", 9)
EMA_SLOW = P("技术指标", "EMA慢线", 21)
RSI_PERIOD = P("技术指标", "RSI周期", 14)

# === v8 入场条件 ===
V8_SIGNAL_QUALITY_MIN = P("入场过滤", "信号质量最低分", 0)
V8_RR_MIN = P("入场过滤", "RR比最低", 0)
V8_TREND_FILTER = P("入场过滤", "趋势过滤", False)

# === v8 Kelly仓位参数 ===
V8_KELLY_FRACTION = P("Kelly仓位", "Kelly保守系数", 0.25)
V8_DEFAULT_WIN_RATE = P("Kelly仓位", "默认胜率", 0.55)

# === v8 六维权重 ===
V8_SIGNAL_WEIGHTS = {
    "oi_trend": P("六维权重", "OI趋势", 0.20),
    "funding_rate": P("六维权重", "资金费率", 0.15),
    "price_volume": P("六维权重", "量价因子", 0.25),
    "macro_environment": P("六维权重", "宏观环境", 0.15),
    "liquidation": P("六维权重", "清算数据", 0.10),
    "smart_money": P("六维权重", "聪明钱", 0.15),
}

# === v8 K线形态权重 ===
V8_PATTERN_WEIGHTS = {
    "A+": 1.0, "A": 0.85, "B+": 0.75, "B": 0.65, "C": 0.5,
}

# === 趋势跟随止损 ===
TREND_TRAIL_ENABLED = P("止盈策略", "趋势跟随开关", True)
TREND_TRAIL_TRIGGER = P("止盈策略", "趋势跟随激活", 0.02)

# === 分批止盈 ===
PARTIAL_TP_ENABLED = P("止盈策略", "分批止盈开关", True)
PARTIAL_TP_RATIO = P("止盈策略", "分批止盈比例", 0.50)
PARTIAL_TP_MOVE_TO_BREAKEVEN = P("止盈策略", "分批后保本", True)
TRAILING_TP_ENABLED = P("止盈策略", "移动止盈开关", True)
TRAILING_TP_TRIGGER = P("止盈策略", "移动止盈激活", 0.04)
TRAILING_TP_STEP = P("止盈策略", "移动止盈回撤", 0.02)

# === 回测时间 ===
backtest_days = P("回测", "回测天数", 1000)
END_TIME = datetime.now(TZ_UTC8)
START_TIME = END_TIME - timedelta(days=backtest_days)


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


# ============================
# v8 核心函数 — 六维加权评分系统
# ============================
def v8_calc_weights(cand: dict, tech: dict, btc_chg: float, fgi: float) -> tuple:
    """
    v8六维加权评分 -100~+100
    基于 trading-plan-generator 六维评分 + kline-indicator 三支柱框架
    """
    direction = cand["direction"]
    d = 1 if direction == "long" else -1
    scores = {}
    details = []

    # 1. OI趋势 (20%) — 每维评分±20
    oi_chg = cand.get("oi_chg", 0)
    price_chg = cand.get("change", 0)
    if abs(oi_chg) > 5:
        if oi_chg > 5 and price_chg > 0:
            scores["oi"] = 20 * d
        elif oi_chg > 5 and price_chg < 0:
            scores["oi"] = -20 * d
        elif oi_chg < -5 and price_chg > 0:
            scores["oi"] = 8 * d
        elif oi_chg < -5 and price_chg < 0:
            scores["oi"] = -8 * d
        else:
            scores["oi"] = 0
    else:
        scores["oi"] = 0
    details.append(f"OI:{scores['oi']:+d}")

    # 2. 资金费率 (15%) — 每维评分±15
    fr = cand.get("fr", 0)
    if d == 1:  # 做多: 负费率好
        if fr < -0.05: scores["funding"] = 15
        elif fr < -0.01: scores["funding"] = 8
        elif fr > 0.10: scores["funding"] = -15
        elif fr > 0.05: scores["funding"] = -8
        else: scores["funding"] = 0
    else:  # 做空: 正费率好
        if fr > 0.10: scores["funding"] = 15
        elif fr > 0.05: scores["funding"] = 8
        elif fr < -0.10: scores["funding"] = -15
        elif fr < -0.05: scores["funding"] = -8
        else: scores["funding"] = 0
    details.append(f"费率:{scores['funding']:+d}")

    # 3. 量价因子 (25%) — 每维评分±25
    rsi = tech.get("rsi", 50)
    trend = tech.get("trend", "neutral")
    atr_pct = tech.get("atr_pct", 0)
    vol = cand.get("vol", 0)
    pv_score = 0
    if (d == 1 and trend == "up") or (d == -1 and trend == "down"):
        pv_score += 3
    elif (d == -1 and trend == "up") or (d == 1 and trend == "down"):
        pv_score -= 2
    if d == 1:
        if rsi < 30: pv_score += 2
        elif rsi < 45: pv_score += 1
        elif rsi > 65: pv_score -= 2
    else:
        if rsi > 70: pv_score += 2
        elif rsi > 55: pv_score += 1
        elif rsi < 35: pv_score -= 2
    if vol > 200_000_000: pv_score += 1
    elif vol < 50_000_000: pv_score -= 1
    if 0.005 < atr_pct < 0.05: pv_score += 1
    scores["pv"] = max(-25, min(25, int(pv_score * 5)))
    details.append(f"量价:{scores['pv']:+d}")

    # 4. 宏观环境 (15%) — 每维评分±15
    macro = 0
    if (d == 1 and btc_chg > -1) or (d == -1 and btc_chg < 1):
        macro += 5
    elif (d == 1 and btc_chg < -5) or (d == -1 and btc_chg > 5):
        macro -= 5
    if (d == 1 and fgi <= 30) or (d == -1 and fgi >= 70):
        macro += 5
    elif (d == 1 and fgi >= 70) or (d == -1 and fgi <= 30):
        macro -= 3
    scores["macro"] = max(-15, min(15, macro))
    details.append(f"宏观:{scores['macro']:+d}")

    # 5. 清算数据 (10%) — 每维评分±10
    liq_score = 0
    if abs(fr) > 0.15:
        liq_score -= 3
    scores["liquidation"] = max(-10, min(10, liq_score))
    details.append(f"清算:{scores['liquidation']:+d}")

    # 6. 聪明钱信号 (15%) — 每维评分±15
    # 简化版: 用费率方向推断聪明钱方向
    sm_score = 0
    if d == 1 and fr < -0.05:
        sm_score = 8  # 做多且负费率=资金流入做多
    elif d == -1 and fr > 0.05:
        sm_score = 8  # 做空且正费率=资金流入做空
    scores["smart_money"] = max(-15, min(15, sm_score))
    details.append(f"聪明钱:{scores['smart_money']:+d}")

    # 加权总分
    w = V8_SIGNAL_WEIGHTS
    total = (scores.get("oi", 0) * w["oi_trend"] +
             scores.get("funding", 0) * w["funding_rate"] +
             scores.get("pv", 0) * w["price_volume"] +
             scores.get("macro", 0) * w["macro_environment"] +
             scores.get("liquidation", 0) * w["liquidation"] +
             scores.get("smart_money", 0) * w["smart_money"])

    detail_str = " | ".join(details) + f" | 总分={int(total)}"
    return total, scores, detail_str


def v8_check_trend_direction(direction: str, tech_1h: dict, k4h_list: list) -> bool:
    """
    检查交易方向是否与EMA趋势一致
    1h趋势+4h趋势双框架确认:
    - 做多: 1h和4h都不能是down趋势
    - 做空: 1h和4h都不能是up趋势
    返回True表示方向与趋势一致（可以入场）
    """
    # 1h趋势
    trend_1h = tech_1h.get("trend", "neutral")
    
    # 4h趋势
    trend_4h = "neutral"
    if k4h_list and len(k4h_list) >= 25:
        closes_4h = [k["close"] for k in k4h_list[-50:]]
        ema9_4h = calc_ema(closes_4h, 9)
        ema21_4h = calc_ema(closes_4h, 21)
        if ema9_4h and ema21_4h:
            if ema9_4h > ema21_4h * 1.001:
                trend_4h = "up"
            elif ema9_4h < ema21_4h * 0.999:
                trend_4h = "down"
    
    if direction == "long":
        # 做多: 1h和4h都不能是down趋势（可以都neutral,或up,或一up一neutral）
        ok = trend_1h != "down" and trend_4h != "down"
    else:
        # 做空: 1h和4h都不能是up趋势
        ok = trend_1h != "up" and trend_4h != "up"
    
    return ok


def v8_signal_quality(cand: dict, tech: dict, patterns_1h: list, patterns_4h: list, btc_chg: float, fgi: float) -> float:
    """
    v8信号质量评分 0-100
    四维加权: 量价40% + 形态30% + 订单流20% + 宏观10%
    三要素之一: quality >= 65
    """
    d = cand["direction"]
    rsi = tech.get("rsi", 50)
    trend = tech.get("trend", "neutral")
    atr_pct = tech.get("atr_pct", 0)
    vol = cand.get("vol", 0)
    fr = cand.get("fr", 0)

    # 1. 量价因子 (40分)
    pv = 0
    if d == "long":
        if trend == "up": pv += 15
        elif trend == "neutral": pv += 5
        else: pv -= 5
        if rsi < 30: pv += 10
        elif rsi < 45: pv += 8
        elif rsi > 65: pv -= 5
    else:
        if trend == "down": pv += 15
        elif trend == "neutral": pv += 5
        else: pv -= 5
        if rsi > 70: pv += 10
        elif rsi > 55: pv += 8
        elif rsi < 35: pv -= 5
    if vol > 200_000_000: pv += 10
    elif vol > 100_000_000: pv += 5
    elif vol < 50_000_000: pv -= 5
    if 0.005 < atr_pct < 0.05: pv += 5
    pv = max(0, min(40, pv))

    # 2. 形态识别 (30分)
    pattern_score = v8_pattern_score(patterns_1h, patterns_4h, d)

    # 3. 订单流 (20分)
    of_score = 10
    if d == "long" and fr < -0.03: of_score += 5
    if d == "long" and fr < -0.10: of_score += 5
    if d == "short" and fr > 0.05: of_score += 5
    if d == "short" and fr > 0.10: of_score += 5
    of_score = max(0, min(20, of_score))

    # 4. 宏观 (10分)
    macro_score = 5
    if (d == "long" and btc_chg > -2) or (d == "short" and btc_chg < 2):
        macro_score += 3
    if (d == "long" and btc_chg < -5) or (d == "short" and btc_chg > 5):
        macro_score -= 3
    if (d == "long" and fgi <= 30) or (d == "short" and fgi >= 70):
        macro_score += 2
    macro_score = max(0, min(10, macro_score))

    final = pv + pattern_score + of_score + macro_score
    return max(0, min(100, final))


def v8_kelly_position(balance: float, win_rate: float, rr: float,
                       signal_quality: float, macro_score: float) -> float:
    """
    Kelly动态仓位计算
    f* = (p*b - q) / b
    保守使用25%, 信号质量调整, 宏观调整
    """
    q = 1 - win_rate
    if rr <= 0:
        kelly = 0
    else:
        kelly = (win_rate * rr - q) / rr
    kelly = max(0, min(0.20, kelly))

    quality_factor = max(0.5, min(1.5, signal_quality / V8_SIGNAL_QUALITY_MIN))
    macro_factor = max(0.5, 1.0 - abs(macro_score - 50) / 100)

    pos_pct = kelly * V8_KELLY_FRACTION * quality_factor * macro_factor * 100
    pos_pct = max(2, min(20, pos_pct))
    return round(balance * pos_pct / 100, 2)


def v8_recognize_patterns(klines_raw: list) -> list:
    """
    识别K线形态
    12种形态: 锤子线/射击之星/吞没/晨星/黄昏星/穿刺线/乌云盖顶/十字星/三白兵/三黑鸦
    返回 [{"name": str, "direction": str, "grade": str}, ...]
    """
    if not klines_raw or len(klines_raw) < 3:
        return []

    patterns = []
    k3 = klines_raw[-3:]

    def extract(k): return [float(k[i]) for i in range(5)]  # o,h,l,c,vol
    o1, h1, l1, c1, _ = extract(k3[0]) if len(k3[0]) >= 5 else [0]*5
    o2, h2, l2, c2, _ = extract(k3[1]) if len(k3[1]) >= 5 else [0]*5
    o3, h3, l3, c3, _ = extract(k3[2]) if len(k3[2]) >= 5 else [0]*5

    def body(o, c): return abs(c - o)
    def upper_shadow(h, o, c): return h - max(o, c)
    def lower_shadow(l, o, c): return min(o, c) - l
    def is_green(o, c): return c > o
    def is_red(o, c): return c < o

    b1, b2, b3 = body(o1, c1), body(o2, c2), body(o3, c3)
    total_range = max(h1, h2, h3, l1, l2, l3, o1, o2, o3, c1, c2, c3) - \
                  min(l1, l2, l3, o1, o2, o3, c1, c2, c3)
    if total_range == 0:
        return []

    us3 = upper_shadow(h3, o3, c3)
    ls3 = lower_shadow(l3, o3, c3)

    # 锤子线
    if b3 > 0 and ls3 > b3 * 2 and us3 < b3 * 0.3 and is_red(o3, c3):
        patterns.append({"name": "Hammer", "direction": "long", "grade": "A+"})
    # 射击之星
    if b3 > 0 and us3 > b3 * 2 and ls3 < b3 * 0.3 and is_green(o3, c3):
        patterns.append({"name": "Shooting_Star", "direction": "short", "grade": "A+"})
    # 看涨吞没
    if is_red(o2, c2) and is_green(o3, c3) and c3 > o2 and o3 < c2:
        patterns.append({"name": "Bullish_Engulfing", "direction": "long", "grade": "A+"})
    # 看跌吞没
    if is_green(o2, c2) and is_red(o3, c3) and c3 < o2 and o3 > c2:
        patterns.append({"name": "Bearish_Engulfing", "direction": "short", "grade": "A+"})
    # 晨星
    if len(klines_raw) >= 4 and is_red(o2, c2) and b2 > total_range * 0.1:
        k4 = klines_raw[-4]
        o4, c4 = float(k4[1]), float(k4[4]) if len(k4) > 4 else [0, 0]
        if b3 < b2 * 0.3 and is_green(o4, c4):
            patterns.append({"name": "Morning_Star", "direction": "long", "grade": "A+"})
    # 黄昏星
    if len(klines_raw) >= 4 and is_green(o2, c2) and b2 > total_range * 0.1:
        k4 = klines_raw[-4]
        o4, c4 = float(k4[1]), float(k4[4]) if len(k4) > 4 else [0, 0]
        if b3 < b2 * 0.3 and is_red(o4, c4):
            patterns.append({"name": "Evening_Star", "direction": "short", "grade": "A+"})
    # 穿刺线
    if is_red(o2, c2) and is_green(o3, c3) and o3 < l2 and c3 > (o2 + c2) / 2 and c3 < o2:
        patterns.append({"name": "Piercing", "direction": "long", "grade": "A"})
    # 乌云盖顶
    if is_green(o2, c2) and is_red(o3, c3) and o3 > h2 and c3 < (o2 + c2) / 2 and c3 > o2:
        patterns.append({"name": "Dark_Cloud_Cover", "direction": "short", "grade": "A"})
    # 十字星
    if b3 < total_range * 0.1 and total_range > 0:
        if ls3 > b3 * 2 and us3 < b3 * 0.3:
            patterns.append({"name": "Dragonfly_Doji", "direction": "long", "grade": "B+"})
        elif us3 > b3 * 2 and ls3 < b3 * 0.3:
            patterns.append({"name": "Gravestone_Doji", "direction": "short", "grade": "B+"})
        else:
            patterns.append({"name": "Doji", "direction": "neutral", "grade": "C"})
    # 三白兵
    if len(klines_raw) >= 4:
        k4 = klines_raw[-4]
        o4, c4 = float(k4[1]), float(k4[4])
        if is_green(o4, c4) and is_green(o3, c3) and is_green(o2, c2) and \
           c4 > o4 and c3 > c4 and c2 > c3:
            patterns.append({"name": "Three_White_Soldiers", "direction": "long", "grade": "A"})
    # 三黑鸦
    if len(klines_raw) >= 4:
        k4 = klines_raw[-4]
        o4, c4 = float(k4[1]), float(k4[4])
        if is_red(o4, c4) and is_red(o3, c3) and is_red(o2, c2) and \
           c4 < o4 and c3 < c4 and c2 < c3:
            patterns.append({"name": "Three_Black_Crows", "direction": "short", "grade": "A"})

    return patterns


def v8_pattern_score(patterns_1h: list, patterns_4h: list, direction: str) -> int:
    """形态评分 0-30分 用于signal_quality"""
    score = 10
    all_patterns = patterns_1h + patterns_4h
    for p in all_patterns:
        w = V8_PATTERN_WEIGHTS.get(p["grade"], 0.5)
        if p["direction"] == direction:
            score += w * 10
        elif p["direction"] == "neutral" and direction in ("long", "short"):
            score += 3
        else:
            score -= w * 5
    # 跨框架确认加分
    for p1 in patterns_1h:
        for p4 in patterns_4h:
            if p1["direction"] == p4["direction"] == direction and \
               p1["grade"] in ("A+", "A") and p4["grade"] in ("A+", "A"):
                score += 10
    if any(p["direction"] == direction for p in patterns_1h) and \
       any(p["direction"] == direction for p in patterns_4h):
        score += 5
    return max(0, min(30, score))


# ============================
# v8 宏观评分函数（回测简化版）
# ============================
def v8_macro_normalized(fgi: float) -> float:
    """将FGI映射到0-100的宏观评分，50=中性"""
    if fgi <= 25: return 25   # 极度恐惧
    if fgi <= 45: return 40   # 恐惧
    if fgi <= 55: return 50   # 中性
    if fgi <= 75: return 60   # 贪婪
    return 75                  # 极度贪婪


def calc_fgi_from_btc(klines, current_price):
    """用BTC价格替代FGI: 21日SMA判断恐惧/贪婪"""
    k_before = [k for k in klines if k["close"] > 0]
    if len(k_before) < 21:
        return 50.0
    sma21 = sum(k["close"] for k in k_before[-21:]) / 21
    ratio = current_price / sma21
    if ratio < 0.85: return 15  # 极度恐惧
    if ratio < 0.92: return 25  # 恐惧
    if ratio < 0.97: return 35  # 偏恐惧
    if ratio < 1.03: return 50  # 中性
    if ratio < 1.10: return 65  # 偏贪婪
    if ratio < 1.20: return 75  # 贪婪
    return 85  # 极度贪婪


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
    if neg_count < 3:
        return None
    avg = sum(r["rate"] for r in recent) / len(recent)
    
    # v3: 移除趋势/RSI硬拒绝 — 交由v8六维评分软处理
    
    strength = "S" if avg < -0.20 else "A" if avg < -0.10 else "B"
    # v3: 移除B级信号封杀 — 让评分系统自然过滤
    
    # v7: 动态ATR止损 — 低ATR币用3.0倍，高ATR币用2.5倍
    atr = tech["atr_pct"]
    if atr > 0:
        atr_mult = ATR_SL_MULTIPLIER if atr < 0.02 else ATR_SL_MULTIPLIER - 0.5
        sl_pct = atr * atr_mult  # v3: 移除ATR SL 5%上限
        sl_pct = max(sl_pct, MIN_SL_PCT)
    else:
        sl_pct = DEFAULT_SL_PCT
    tp_pct = sl_pct * max(V8_RR_MIN, 2.5)
    
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
    if pos_count < 3:
        return None
    avg = sum(r["rate"] for r in recent) / len(recent)
    
    # v3: 移除趋势/RSI硬拒绝 — 交由v8六维评分软处理
    
    strength = "S" if avg > 0.25 else "A" if avg > 0.15 else "B"
    # v3: 移除B级信号封杀 — 让评分系统自然过滤
    
    atr = tech["atr_pct"]
    if atr > 0:
        atr_mult = ATR_SL_MULTIPLIER if atr < 0.02 else ATR_SL_MULTIPLIER - 0.5
        sl_pct = atr * atr_mult  # v3: 移除ATR SL 5%上限
        sl_pct = max(sl_pct, MIN_SL_PCT)
    else:
        sl_pct = DEFAULT_SL_PCT
    tp_pct = sl_pct * max(V8_RR_MIN, 2.5)
    
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
    if change_pct >= -30:
        return None
    recent3 = last24[-3:]
    if recent3[-1]["close"] >= recent3[-2]["close"]:
        # v3: 移除趋势/RSI硬拒绝 — 交由v8六维评分软处理
        strength = "A" if change_pct < -40 else "B"
        # v3: 移除B级信号封杀 — 让评分系统自然过滤
        atr = tech["atr_pct"]
        if atr > 0:
            atr_mult = ATR_SL_MULTIPLIER if atr < 0.02 else ATR_SL_MULTIPLIER - 0.5
            sl_pct = atr * atr_mult  # v3: 移除ATR SL 5%上限
            sl_pct = max(sl_pct, MIN_SL_PCT)
        else:
            sl_pct = DEFAULT_SL_PCT
        tp_pct = sl_pct * max(V8_RR_MIN, 2.5)
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
    if pullback < 8:
        return None
    # v3: 移除趋势/RSI硬拒绝 — 交由v8六维评分软处理
    strength = "A" if pullback > 15 else "B"
    # v3: 移除B级信号封杀 — 让评分系统自然过滤
    atr = tech["atr_pct"]
    if atr > 0:
        atr_mult = ATR_SL_MULTIPLIER if atr < 0.02 else ATR_SL_MULTIPLIER - 0.5
        sl_pct = atr * atr_mult  # v3: 移除ATR SL 5%上限
        sl_pct = max(sl_pct, MIN_SL_PCT)
    else:
        sl_pct = DEFAULT_SL_PCT
    tp_pct = sl_pct * max(V8_RR_MIN, 2.5)
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
    if rr < V8_RR_MIN:
        return -999
    
    return score


def check_reversal_confirm(klines, ts, direction):
    """v7: 连亏后需趋势反转确认 — 检查最近N根K线是否确认趋势反转"""
    # v3: 简化 — 反转确认已由v8六维评分替代
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
    print("📊 v8 半年回测 — 六维加权评分版")
    print(f"时间: {START_TIME.strftime('%Y-%m-%d')} ~ {END_TIME.strftime('%Y-%m-%d')}")
    print(f"资金: ${INITIAL_BALANCE:.0f} | 杠杆: {LEVERAGE}x | 评分: 六维加权(-100~+100)")
    print(f"入场: 质量≥{V8_SIGNAL_QUALITY_MIN} + RR≥{V8_RR_MIN} + {'' if not V8_TREND_FILTER else 'EMA趋势'}")
    print(f"仓位: Kelly动态({V8_KELLY_FRACTION*100}%保守) 2-20%范围")
    print(f"止损: ATR×{ATR_SL_MULTIPLIER}动态 | 形态: 12种K线+1h/4h双框架")
    print(f"冷却: {COOLDOWN_HOURS}h | v3减法优化版")
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
            
            # v3: 移除时间止损(MAX_HOLD_HOURS/TIME_DECAY_START) — 无回测支撑

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
        
                # --- 扫描新信号 (v8) ---
        dt_utc = datetime.utcfromtimestamp(ts / 1000)
        is_funding_time = dt_utc.hour % 8 == 0 and dt_utc.minute == 0
        
        if is_funding_time and len(positions) < MAX_POSITIONS and balance > 100:
            # v3: 移除DAILY_MAX_LOSS_PCT每日亏损保护 — 无回测支撑
            
            # BTC环境数据
            btc_before = [k for k in btc_klines if k["time"] <= ts]
            btc_chg = 0
            if len(btc_before) >= 24:
                btc_chg = (btc_before[-1]["close"] - btc_before[-24]["close"]) / btc_before[-24]["close"] * 100
            btc_price = btc_before[-1]["close"] if btc_before else 50000
            fgi = calc_fgi_from_btc(btc_before, btc_price)
            
            candidates = []
            open_symbols = set(p["symbol"] for p in positions)
            
            # 费率策略 (v8)
            for sym in all_funding:
                if sym in open_symbols:
                    continue
                consec = consecutive_losses.get(sym, 0)
                if ts - cooldowns.get(sym, 0) < COOLDOWN_HOURS * 3600 * 1000:
                    continue
                
                f_hist = [f for f in all_funding[sym] if f["time"] <= ts]
                if sym in all_klines:
                    tech = get_tech_at(all_klines[sym], ts)
                else:
                    tech = {"trend": "neutral", "rsi": 50.0, "atr_pct": 0.0, "ema9": None, "ema21": None}
                
                for scanner_fn in [scan_extreme_neg_funding, scan_extreme_pos_funding]:
                    sig = scanner_fn(f_hist, tech)
                    if sig:
                        sig["symbol"] = sym
                        sig["vol"] = vol_map.get(sym, 0)
                        sig["fr"] = f_hist[-1]["rate"] / 100 if f_hist else 0
                        sig["change"] = ((tech.get("ema9", btc_price) or btc_price) - 
                                          (tech.get("ema21", btc_price) or btc_price)) /                                          (tech.get("ema21", btc_price) or 1) * 100
                        candidates.append(sig)
                        signals_found += 1
            
            # K线策略 (v8)
            for sym in all_klines:
                if sym in open_symbols:
                    continue
                consec = consecutive_losses.get(sym, 0)
                if ts - cooldowns.get(sym, 0) < COOLDOWN_HOURS * 3600 * 1000:
                    continue
                
                tech = get_tech_at(all_klines[sym], ts)
                
                for scanner_fn in [scan_crash_bounce, scan_pump_short]:
                    sig = scanner_fn(all_klines[sym], ts, tech)
                    if sig:
                        sig["symbol"] = sym
                        sig["vol"] = vol_map.get(sym, 0)
                        fr_hist = all_funding.get(sym, [])
                        sig["fr"] = fr_hist[-1]["rate"] / 100 if fr_hist else 0
                        sig["change"] = sig.get("change", 0)
                        candidates.append(sig)
                        signals_found += 1
            
            # ===== v8 过滤+开仓 =====
            valid = []
            # 预处理: 获取4h K线（多时间框架形态识别）
            klines_4h_cache = {}
            for c in candidates:
                sym = c["symbol"]
                if sym not in klines_4h_cache and sym in all_klines:
                    k1h = all_klines[sym]
                    k4h = []
                    for i in range(0, len(k1h), 4):
                        chunk = k1h[i:i+4]
                        if len(chunk) == 4:
                            k4h.append({
                                "time": chunk[0]["time"],
                                "open": chunk[0]["open"],
                                "high": max(k["high"] for k in chunk),
                                "low": min(k["low"] for k in chunk),
                                "close": chunk[-1]["close"],
                                "volume": sum(k["volume"] for k in chunk),
                            })
                    klines_4h_cache[sym] = k4h
            
            for c in candidates:
                sym = c["symbol"]
                tech = c.get("tech", {})
                fr = c.get("fr", 0)
                
                # v8: 六维加权评分
                v8_score, v8_scores, v8_detail = v8_calc_weights(c, tech, btc_chg, fgi)
                c["v8_score"] = v8_score
                c["v8_scores"] = v8_scores
                c["v8_detail"] = v8_detail
                
                # v8: 多时间框架形态识别
                k1h = all_klines.get(sym, [])
                k1h_raw = [[k["time"], k["open"], k["high"], k["low"], k["close"], k["volume"]] for k in k1h]
                k4h = klines_4h_cache.get(sym, [])
                k4h_raw = [[k["time"], k["open"], k["high"], k["low"], k["close"], k["volume"]] for k in k4h]
                patterns_1h = v8_recognize_patterns(k1h_raw)
                patterns_4h = v8_recognize_patterns(k4h_raw)
                
                # v8: 信号质量评分
                quality = v8_signal_quality(c, tech, patterns_1h, patterns_4h, btc_chg, fgi)
                c["quality"] = quality
                
                # v8: 宏观评分归一化
                macro_score = v8_macro_normalized(fgi)
                
                # v8: EMA趋势方向过滤
                trend_ok = v8_check_trend_direction(c["direction"], tech, k4h) if V8_TREND_FILTER else True

                # v8: 双要素入场条件
                rr = c.get("rr", 0)
                meets_quality = quality >= V8_SIGNAL_QUALITY_MIN
                meets_rr = rr >= V8_RR_MIN

                if not (meets_quality and meets_rr and trend_ok):
                    signals_filtered += 1
                    continue
                
                c["macro_normalized"] = macro_score
                c["patterns_1h"] = [p["name"] for p in patterns_1h]
                c["patterns_4h"] = [p["name"] for p in patterns_4h]
                valid.append(c)
            
            # v8: 按加权评分+质量评分排序
            valid.sort(key=lambda x: (x["v8_score"], x["quality"]), reverse=True)
            
            for c in valid:
                if len(positions) >= MAX_POSITIONS:
                    break
                pk_close = price_cache.get(c["symbol"])
                if not pk_close or pk_close <= 0:
                    continue
                
                price = pk_close
                sl_pct = c["sl_pct"]
                tp_pct = c["tp_pct"]
                
                # v8: Kelly动态仓位
                quality = c["quality"]
                rr = c.get("rr", 0)
                macro_norm = c.get("macro_normalized", 50)
                pos_usd = v8_kelly_position(balance, V8_DEFAULT_WIN_RATE, rr, quality, macro_norm)
                if pos_usd <= 0 or pos_usd > balance * 0.20:
                    pos_usd = balance * 0.10
                
                if c["direction"] == "long":
                    sl = price * (1 - sl_pct)
                    tp = price * (1 + tp_pct)
                else:
                    sl = price * (1 + sl_pct)
                    tp = price * (1 - tp_pct)
                
                tech = c.get("tech", {})
                kelly_pct = pos_usd / balance * 100
                position = {
                    "id": f"{len(all_trades) + len(positions) + 1:03d}",
                    "symbol": c["symbol"], "direction": c["direction"], "leverage": LEVERAGE,
                    "position_pct": round(kelly_pct, 1),
                    "position_usd": round(pos_usd, 2), "notional_usd": round(pos_usd * LEVERAGE, 2),
                    "entry_price": price, "stop_loss": round(sl, 8), "take_profit": round(tp, 8),
                    "original_sl": round(sl, 8),
                    "entry_time": dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "signal_type": c["type"], "signal_strength": c["strength"],
                    "signal_reason": c.get("reason", ""),
                    "v8_score": c.get("v8_score", 0),
                    "v8_quality": round(quality, 1),
                    "v8_detail": c.get("v8_detail", ""),
                    "signal_rr": c.get("rr", 0),
                    "signal_sl_pct": round(sl_pct * 100, 2),
                    "signal_tp_pct": round(tp_pct * 100, 2),
                    "tech_snapshot": {
                        "ema_trend": tech.get("trend", "N/A"),
                        "rsi": round(tech.get("rsi", 50), 1),
                        "atr_pct": round(tech.get("atr_pct", 0), 3),
                    },
                    "patterns_1h": c.get("patterns_1h", []),
                    "patterns_4h": c.get("patterns_4h", []),
                    "status": "open",
                    "remaining_qty": round(pos_usd, 2),
                    "partial_done": False,
                    "trail_active": False, "trail_high": 0, "trail_low": float("inf"),
                    "trend_trail_active": False,
                }
                positions.append(position)
                
                print(f"  v8开仓 #{position['id']} {c['symbol']} {'多' if c['direction']=='long' else '空'} @{price:.4f} "
                      f"Kelly${pos_usd:.0f}({kelly_pct:.1f}%) SL={sl_pct*100:.1f}% TP={tp_pct*100:.1f}% RR={c['rr']:.1f} "
                      f"评分={int(v8_score):+d} 质量={quality:.0f} ATR={tech.get('atr_pct',0)*100:.1f}% | {v8_detail}")
                break
        
        # 净值和回撤# 净值和回撤
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
    print("📊 v8 半年回测结果")
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
    for reason in ["止盈", "分批止盈(50%)", "趋势跟随止损", "移动止盈", "止损", "回测结束"]:
        r_trades = [t for t in closed if t.get("exit_reason") == reason]
        if not r_trades: continue
        r_pnl = sum(t.get("pnl_usd", 0) for t in r_trades)
        r_wins = sum(1 for t in r_trades if (t.get("pnl_usd", 0) or 0) > 0)
        print(f"  {reason}: {len(r_trades)}笔 | 胜{r_wins}笔 | PnL {r_pnl:+.2f}U")
    
    # v8 特有指标
    print(f"\n📊 v8 六维评分分布:")
    v8_scores_all = [t.get("v8_score", 0) for t in closed if "v8_score" in t]
    if v8_scores_all:
        print(f"  平均评分: {sum(v8_scores_all)/len(v8_scores_all):+.1f}")
        print(f"  评分范围: {int(min(v8_scores_all)):+d} ~ {int(max(v8_scores_all)):+d}")
        wins_with_score = [t.get("v8_score", 0) for t in wins if "v8_score" in t]
        losses_with_score = [t.get("v8_score", 0) for t in losses if "v8_score" in t]
        if wins_with_score:
            print(f"  盈利笔平均评分: {sum(wins_with_score)/len(wins_with_score):+.1f}")
        if losses_with_score:
            print(f"  亏损笔平均评分: {sum(losses_with_score)/len(losses_with_score):+.1f}")
    
    v8_qualities = [t.get("v8_quality", 0) for t in closed if "v8_quality" in t]
    if v8_qualities:
        print(f"  平均信号质量: {sum(v8_qualities)/len(v8_qualities):.1f}")
    
    # 每月汇总
    print(f"\n📅 每月收益:")
    monthly = defaultdict(lambda: {"pnl": 0.0, "count": 0, "wins": 0})
    for t in closed:
        et = t.get("exit_time", t.get("entry_time", ""))
        month = et[:7]
        pnl = t.get("pnl_usd", 0) or 0
        monthly[month]["pnl"] += pnl
        monthly[month]["count"] += 1
        if pnl > 0: monthly[month]["wins"] += 1
    for m in sorted(monthly.keys()):
        d = monthly[m]
        emoji = "📈" if d["pnl"] >= 0 else "📉"
        print(f"  {emoji} {m}: {d['pnl']:+.1f}U ({d['count']}笔 {d['wins']}盈)")
    
    # 保存
    result = {
        "version": "v8",
        "description": "六维加权评分+三要素入场+Kelly动态仓位+K线形态识别+多时间框架",
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
    out_path = Path(__file__).parent / "data" / "backtest_v8_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 保存到: {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    run_backtest()
