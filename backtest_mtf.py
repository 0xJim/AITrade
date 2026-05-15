#!/usr/bin/env python3
"""
回测脚本 v9 — 15分钟异动扫描 + 多时间框架分析
核心变化:
1. 触发: 15分钟收线涨跌幅 > 阈值(默认1%) → 进入候选池
2. 分析: 30m/1h/4h/6h/8h/12h/1d/3d/1w/1M 全时间框架方向判断
3. 评分: 六维加权 + 多框架一致性评分
4. 开仓: 信号质量 + RR + 多框架趋势共振
"""
import sys, json, time, math
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

# === 参数加载 ===
BASE_DIR = Path(__file__).parent
PARAMS_FILE = BASE_DIR / "params.yaml"
_PARAMS = None

def load_params():
    global _PARAMS
    try:
        import yaml
        with open(PARAMS_FILE, "r", encoding="utf-8") as f:
            _PARAMS = yaml.safe_load(f)
    except Exception:
        _PARAMS = None
    return _PARAMS

def P(section, key, default=None):
    if _PARAMS is None:
        load_params()
    if _PARAMS and section in _PARAMS and key in _PARAMS[section]:
        return _PARAMS[section][key]
    return default

if "--from-params" in sys.argv:
    load_params()

FAPI_LIVE = "https://fapi.binance.com"
TZ_UTC8 = timezone(timedelta(hours=8))

# === 基础参数 ===
INITIAL_BALANCE = P("基础", "初始资金", 5000.0)
LEVERAGE = P("基础", "杠杆倍数", 3)
MAX_POSITIONS = P("基础", "最大持仓数", 2)
COOLDOWN_HOURS = P("基础", "冷却时间_小时", 24)
RISK_PER_TRADE = P("基础", "每笔风险比例", 0.01)

# === 异动扫描 ===
SPIKE_THRESHOLD = P("异动扫描", "异动阈值", 0.01)      # 1%
SPIKE_INTERVAL = P("异动扫描", "扫描周期", 15)           # 15分钟
MIN_ATR_FILTER = P("异动扫描", "最小ATR过滤", 0.005)
SPIKE_COOLDOWN = P("异动扫描", "冷却期_同向", 4)

# === 多时间框架 ===
MTF_FRAMES = P("多时间框架", "框架列表", ["30m", "1h", "4h", "6h", "8h", "12h", "1d", "3d", "1w", "1M"])
MTF_CONSISTENCY_WEIGHT = P("多时间框架", "一致性权重", 0.30)
MTF_LARGE_WEIGHT = P("多时间框架", "大框架权重", 0.15)
MTF_SMALL_WEIGHT = P("多时间框架", "小框架权重", 0.10)
MTF_MIN_AGREE = P("多时间框架", "趋势一致最低", 3)

# === 止损止盈 ===
ATR_SL_MULTIPLIER = P("止损止盈", "ATR止损乘数", 1.5)
MIN_SL_PCT = P("止损止盈", "最小止损百分比", 0.03)
DEFAULT_SL_PCT = P("止损止盈", "默认止损", 0.05)
ATR_PERIOD = P("技术指标", "ATR周期", 14)
EMA_FAST = P("技术指标", "EMA快线", 9)
EMA_SLOW = P("技术指标", "EMA慢线", 21)
RSI_PERIOD = P("技术指标", "RSI周期", 14)

# === 入场 ===
V8_SIGNAL_QUALITY_MIN = P("入场过滤", "信号质量最低分", 0)
V8_RR_MIN = P("入场过滤", "RR比最低", 0)

# === Kelly ===
V8_KELLY_FRACTION = P("Kelly仓位", "Kelly保守系数", 0.25)
V8_DEFAULT_WIN_RATE = P("Kelly仓位", "默认胜率", 0.55)

# === 止盈策略 ===
TREND_TRAIL_ENABLED = P("止盈策略", "趋势跟随开关", True)
TREND_TRAIL_TRIGGER = P("止盈策略", "趋势跟随激活", 0.02)
PARTIAL_TP_ENABLED = P("止盈策略", "分批止盈开关", True)
PARTIAL_TP_RATIO = P("止盈策略", "分批止盈比例", 0.50)
PARTIAL_TP_MOVE_TO_BREAKEVEN = P("止盈策略", "分批后保本", True)
TRAILING_TP_ENABLED = P("止盈策略", "移动止盈开关", True)
TRAILING_TP_TRIGGER = P("止盈策略", "移动止盈激活", 0.04)
TRAILING_TP_STEP = P("止盈策略", "移动止盈回撤", 0.02)

# === 回测时间 ===
backtest_days = P("回测", "回测天数", 180)
END_TIME = datetime.now(TZ_UTC8)
START_TIME = END_TIME - timedelta(days=backtest_days)

# ============================================================
# API
# ============================================================
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

def get_klines_ts(symbol, interval, start_ts, end_ts, limit=1500):
    """获取历史K线"""
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
        time.sleep(0.1)
    return [{
        "time": int(k[0]), "open": float(k[1]), "high": float(k[2]),
        "low": float(k[3]), "close": float(k[4]), "volume": float(k[7]),
    } for k in all_data]

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

def get_qualified_symbols():
    tickers = api_get("/fapi/v1/ticker/24hr") or []
    exclude = {"BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT", "BTCDOMUSDT", "BTCSTUSDT",
               "BNBUSDT", "BUSDUSDT", "TUSDUSDT", "DAIUSDT"}
    max_syms = P("币种", "最大币种数", 50)
    min_vol = P("币种", "最低成交量", 50000000)
    qualified = []
    for t in tickers:
        sym = t.get("symbol", "")
        vol = float(t.get("quoteVolume", 0))
        price = float(t.get("lastPrice", 0))
        if sym.endswith("USDT") and sym not in exclude and vol > min_vol and price > 0.001:
            qualified.append({"symbol": sym, "volume": vol, "price": price})
    qualified.sort(key=lambda x: x["volume"], reverse=True)
    return qualified[:max_syms]

# ============================================================
# 技术指标
# ============================================================
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
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)

def calc_atr(klines, period=14):
    if len(klines) < period + 1:
        return 0
    trs = []
    for i in range(1, len(klines)):
        h, l, pc = klines[i]["high"], klines[i]["low"], klines[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period

def get_tech(klines):
    """从K线提取技术指标"""
    if len(klines) < EMA_SLOW + 5:
        return {"trend": "neutral", "rsi": 50, "atr_pct": 0, "ema9": None, "ema21": None,
                "vol_sma": 0, "price": 0}
    closes = [k["close"] for k in klines]
    ema9 = calc_ema(closes, EMA_FAST)
    ema21 = calc_ema(closes, EMA_SLOW)
    rsi = calc_rsi(closes, RSI_PERIOD)
    atr = calc_atr(klines, ATR_PERIOD)
    price = closes[-1]
    atr_pct = atr / price if price > 0 else 0
    vols = [k["volume"] for k in klines[-20:]]
    vol_sma = sum(vols) / len(vols) if vols else 0

    ema_dev = 0.001
    if ema9 and ema21:
        if ema9 > ema21 * (1 + ema_dev):
            trend = "up"
        elif ema9 < ema21 * (1 - ema_dev):
            trend = "down"
        else:
            trend = "neutral"
    else:
        trend = "neutral"

    return {"trend": trend, "rsi": rsi, "atr_pct": atr_pct, "ema9": ema9, "ema21": ema21,
            "vol_sma": vol_sma, "price": price}

# ============================================================
# 多时间框架分析（核心新功能）
# ============================================================
# 时间框架 → 每根K线对应的分钟数
FRAME_MINUTES = {
    "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240,
    "6h": 360, "8h": 480, "12h": 720, "1d": 1440,
    "3d": 4320, "1w": 10080, "1M": 43200,
}

# 大框架 = 日线及以上
LARGE_FRAMES = {"1d", "3d", "1w", "1M"}

def mtf_trend_from_klines(klines, direction):
    """
    从单时间框架K线判断趋势方向
    返回: "up" / "down" / "neutral"
    """
    if len(klines) < EMA_SLOW + 5:
        return "neutral"
    closes = [k["close"] for k in klines]
    ema9 = calc_ema(closes, EMA_FAST)
    ema21 = calc_ema(closes, EMA_SLOW)
    if ema9 is None or ema21 is None:
        return "neutral"
    if ema9 > ema21 * 1.001:
        return "up"
    elif ema9 < ema21 * 0.999:
        return "down"
    return "neutral"

def mtf_analyze(klines_by_frame, direction):
    """
    多时间框架分析
    返回: {
        "frames": {frame: trend},   # 每个框架的趋势
        "agree_count": N,           # 同向框架数
        "total_frames": N,          # 有效框架数
        "consistency_score": 0~100, # 一致性评分
        "large_agree": N,           # 大框架同向数
        "small_agree": N,           # 小框架同向数
    }
    """
    frame_trends = {}
    agree_count = 0
    large_agree = 0
    large_total = 0
    small_agree = 0
    small_total = 0
    valid_frames = 0

    want = "up" if direction == "long" else "down"

    for frame, klines in klines_by_frame.items():
        if not klines or len(klines) < EMA_SLOW + 5:
            frame_trends[frame] = "neutral"
            continue
        trend = mtf_trend_from_klines(klines, direction)
        frame_trends[frame] = trend
        valid_frames += 1

        if trend == want:
            agree_count += 1
            if frame in LARGE_FRAMES:
                large_agree += 1
            else:
                small_agree += 1
        if frame in LARGE_FRAMES:
            large_total += 1
        else:
            small_total += 1

    # 一致性评分 (0-100)
    if valid_frames == 0:
        consistency_score = 0
    else:
        base_score = agree_count / valid_frames * 100
        # 大框架同向额外加分
        large_bonus = (large_agree / large_total * MTF_LARGE_WEIGHT * 100) if large_total > 0 else 0
        # 小框架同向额外加分
        small_bonus = (small_agree / small_total * MTF_SMALL_WEIGHT * 100) if small_total > 0 else 0
        consistency_score = min(100, base_score * MTF_CONSISTENCY_WEIGHT + large_bonus + small_bonus)

    return {
        "frames": frame_trends,
        "agree_count": agree_count,
        "total_frames": valid_frames,
        "consistency_score": round(consistency_score, 1),
        "large_agree": large_agree,
        "large_total": large_total,
        "small_agree": small_agree,
        "small_total": small_total,
        "meets_threshold": agree_count >= MTF_MIN_AGREE,
    }

# ============================================================
# 六维评分 (沿用v8逻辑，简化版)
# ============================================================
V8_SIGNAL_WEIGHTS = {
    "oi_trend": P("六维权重", "OI趋势", 0.20),
    "funding_rate": P("六维权重", "资金费率", 0.15),
    "price_volume": P("六维权重", "量价因子", 0.25),
    "macro_environment": P("六维权重", "宏观环境", 0.15),
    "liquidation": P("六维权重", "清算数据", 0.10),
    "smart_money": P("六维权重", "聪明钱", 0.15),
}

def v8_calc_weights_simple(signal, tech, btc_chg, fgi):
    """简化版六维评分（回测用，不需要OI/清算等实时数据）"""
    direction = signal["direction"]
    fr = signal.get("fr", 0)
    scores = {}
    
    # OI趋势 — 回测中无法获取历史OI，用趋势替代
    scores["oi_trend"] = 10 if tech["trend"] == ("up" if direction == "long" else "down") else -5
    
    # 费率
    fr_score = 0
    if direction == "long":
        if fr < -0.05: fr_score = 15
        elif fr < -0.01: fr_score = 8
        elif fr > 0.10: fr_score = -10
        elif fr > 0.05: fr_score = -5
    else:
        if fr > 0.05: fr_score = 15
        elif fr > 0.01: fr_score = 8
        elif fr < -0.10: fr_score = -10
        elif fr < -0.05: fr_score = -5
    scores["funding_rate"] = fr_score
    
    # 量价
    pv = 0
    if tech["trend"] == ("up" if direction == "long" else "down"):
        pv += 3
    elif tech["trend"] != "neutral":
        pv -= 2
    rsi = tech["rsi"]
    if direction == "long":
        if rsi < 30: pv += 2
        elif rsi < 45: pv += 1
        elif rsi > 65: pv -= 2
    else:
        if rsi > 70: pv += 2
        elif rsi > 55: pv += 1
        elif rsi < 35: pv -= 2
    scores["price_volume"] = max(-25, min(25, pv * 5))
    
    # 宏观
    macro = 0
    if direction == "long":
        if btc_chg > -1: macro += 5
        if btc_chg < -5: macro -= 5
        if fgi <= 30: macro += 5
        elif fgi >= 70: macro -= 3
    else:
        if btc_chg < 1: macro += 5
        if btc_chg > 5: macro -= 5
        if fgi >= 70: macro += 5
        elif fgi <= 30: macro -= 3
    scores["macro_environment"] = max(-15, min(15, macro))
    
    # 清算 — 回测中简化
    scores["liquidation"] = -3 if abs(fr) > 0.15 else 0
    
    # 聪明钱
    sm = 0
    if direction == "long" and fr < -0.05:
        sm = 8
    elif direction == "short" and fr > 0.05:
        sm = 8
    scores["smart_money"] = max(-15, min(15, sm))
    
    # 加权
    total = sum(scores[k] * V8_SIGNAL_WEIGHTS[k] for k in scores)
    return total, scores

def calc_fgi_from_btc(klines, current_price):
    if not klines or len(klines) < 21:
        return 50.0
    sma21 = sum(k["close"] for k in klines[-21:]) / 21
    ratio = current_price / sma21
    if ratio < 0.85: return 15
    if ratio < 0.92: return 25
    if ratio < 0.97: return 35
    if ratio < 1.03: return 50
    if ratio < 1.10: return 65
    if ratio < 1.20: return 75
    return 85

def v8_signal_quality_simple(signal, tech, mtf_result):
    """简化版信号质量评分 (0-100)"""
    score = 50  # 基础分
    direction = signal["direction"]
    trend = tech["trend"]
    
    # 量价因子 (40分)
    want = "up" if direction == "long" else "down"
    if trend == want:
        score += 15
    elif trend == "neutral":
        score += 5
    else:
        score -= 5
    
    rsi = tech["rsi"]
    if direction == "long" and rsi < 35:
        score += 10
    elif direction == "short" and rsi > 65:
        score += 10
    
    vol = signal.get("vol", 0)
    if vol > 200_000_000: score += 10
    elif vol > 100_000_000: score += 5
    elif vol < 50_000_000: score -= 5
    
    # 多框架一致性 (30分)
    if mtf_result:
        consistency = mtf_result["consistency_score"]
        if consistency > 70: score += 20
        elif consistency > 50: score += 10
        elif consistency < 30: score -= 10
        
        if mtf_result["meets_threshold"]:
            score += 10
        if mtf_result["large_agree"] >= 2:
            score += 5
    
    # 费率/订单流 (20分)
    fr = signal.get("fr", 0)
    if direction == "long" and fr < -0.03: score += 10
    elif direction == "short" and fr > 0.03: score += 10
    
    return max(0, min(100, score))

def v8_kelly_position(balance, win_rate, rr, quality, macro_score):
    """Kelly仓位"""
    if rr <= 0: rr = 1
    kelly = win_rate - (1 - win_rate) / rr
    kelly = max(0, min(0.20, kelly)) * V8_KELLY_FRACTION
    
    quality_factor = 0.5 + (quality / 100)
    quality_factor = max(0.5, min(1.5, quality_factor))
    
    macro_factor = 1.0 - abs(macro_score - 50) / 100
    macro_factor = max(0.5, macro_factor)
    
    pos = balance * kelly * quality_factor * macro_factor
    pos = max(balance * 0.02, min(balance * 0.20, pos))
    return round(pos, 2)

# ============================================================
# 从1h K线聚合多时间框架K线
# ============================================================
def aggregate_klines(klines_1h, target_minutes):
    """将1h K线聚合为更大时间框架"""
    if not klines_1h:
        return []
    
    interval_ms = target_minutes * 60 * 1000
    
    groups = defaultdict(list)
    for k in klines_1h:
        group_key = k["time"] // interval_ms
        groups[group_key].append(k)
    
    result = []
    for key in sorted(groups.keys()):
        chunk = groups[key]
        if not chunk:
            continue
        result.append({
            "time": chunk[0]["time"],
            "open": chunk[0]["open"],
            "high": max(k["high"] for k in chunk),
            "low": min(k["low"] for k in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(k["volume"] for k in chunk),
        })
    return result

# ============================================================
# 回测主函数
# ============================================================
def run_backtest():
    print("=" * 60)
    print("📊 v9 回测 — 15分钟异动 + 多时间框架分析")
    print(f"时间: {START_TIME.strftime('%Y-%m-%d')} ~ {END_TIME.strftime('%Y-%m-%d')}")
    print(f"资金: ${INITIAL_BALANCE:.0f} | 杠杆: {LEVERAGE}x")
    print(f"异动阈值: {SPIKE_THRESHOLD*100}% | 扫描: {SPIKE_INTERVAL}分钟")
    print(f"多时间框架: {MTF_FRAMES}")
    print(f"一致性要求: ≥{MTF_MIN_AGREE}框架同向")
    print("=" * 60)
    
    start_ts = int(START_TIME.timestamp() * 1000)
    end_ts = int(END_TIME.timestamp() * 1000)
    
    # 获取币种
    print("\n🔍 获取活跃合约...")
    symbols_info = get_qualified_symbols()
    symbols = [s["symbol"] for s in symbols_info]
    vol_map = {s["symbol"]: s["volume"] for s in symbols_info}
    print(f"  {len(symbols)} 个币种")
    
    # 获取BTC 1h K线
    print("\n📈 获取BTC历史...")
    btc_klines = get_klines_ts("BTCUSDT", "1h", start_ts, end_ts)
    print(f"  BTC 1h: {len(btc_klines)} 根")
    
    # 获取各币种 1h K线 + 费率
    print("\n📉 获取K线+费率...")
    all_klines_1h = {}
    all_funding = {}
    for i, sym in enumerate(symbols):
        kl = get_klines_ts(sym, "1h", start_ts - 50*3600*1000, end_ts)
        if kl:
            all_klines_1h[sym] = kl
        fh = get_funding_history_ts(sym, start_ts, end_ts)
        if fh:
            all_funding[sym] = fh
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(symbols)}...")
        time.sleep(0.3)
    
    print(f"  有K线: {len(all_klines_1h)} | 有费率: {len(all_funding)}")
    
    # 预计算15分钟K线时间点
    interval_ms = SPIKE_INTERVAL * 60 * 1000
    all_15m_times = list(range(start_ts // interval_ms * interval_ms,
                                end_ts // interval_ms * interval_ms + interval_ms,
                                interval_ms))
    print(f"\n⏱️ 15分钟时间点: {len(all_15m_times)}")
    
    # 预聚合多时间框架K线
    print("\n🔄 预聚合多时间框架...")
    mtf_cache = {}  # sym -> {frame: klines}
    frame_minutes = {
        "30m": 30, "1h": 60, "4h": 240, "6h": 360, "8h": 480,
        "12h": 720, "1d": 1440, "3d": 4320, "1w": 10080, "1M": 43200,
    }
    for sym, kl_1h in all_klines_1h.items():
        mtf_cache[sym] = {}
        mtf_cache[sym]["1h"] = kl_1h
        for frame, mins in frame_minutes.items():
            if frame == "1h":
                continue
            mtf_cache[sym][frame] = aggregate_klines(kl_1h, mins)
    print("  完成")
    
    # 模拟交易
    print("\n🚀 开始模拟交易...\n")
    
    balance = INITIAL_BALANCE
    positions = []
    all_trades = []
    cooldowns = {}
    spike_cooldowns = {}  # sym -> {direction: last_spike_ts}
    consecutive_losses = defaultdict(int)
    max_equity = INITIAL_BALANCE
    max_drawdown = 0
    trade_id = 0
    signals_found = 0
    signals_filtered = 0
    
    for step_i, ts_15m in enumerate(all_15m_times):
        dt = datetime.fromtimestamp(ts_15m / 1000, tz=TZ_UTC8)
        
        # --- 获取此时间点各币种价格 ---
        price_cache = {}
        kline_at = {}
        for sym, kl_1h in all_klines_1h.items():
            for k in reversed(kl_1h):
                if k["time"] <= ts_15m:
                    price_cache[sym] = k["close"]
                    kline_at[sym] = k
                    break
        
        # --- 检查持仓 ---
        to_close = []
        for pos in positions:
            sym = pos["symbol"]
            pk = kline_at.get(sym)
            if not pk:
                continue
            
            entry = pos["entry_price"]
            if pos["direction"] == "long":
                pnl_raw = (pk["close"] - entry) / entry
            else:
                pnl_raw = (entry - pk["close"]) / entry
            
            triggered = None
            fill_price = pk["close"]
            close_qty = pos.get("remaining_qty", pos["position_usd"])
            is_partial = False
            
            # 止损
            if pos["direction"] == "long" and pk["low"] <= pos["stop_loss"]:
                triggered = "止损"
                fill_price = pos["stop_loss"]
            elif pos["direction"] == "short" and pk["high"] >= pos["stop_loss"]:
                triggered = "止损"
                fill_price = pos["stop_loss"]
            
            # 止盈
            if not triggered:
                if pos["direction"] == "long" and pk["high"] >= pos["take_profit"]:
                    triggered = "止盈"
                    fill_price = pos["take_profit"]
                elif pos["direction"] == "short" and pk["low"] <= pos["take_profit"]:
                    triggered = "止盈"
                    fill_price = pos["take_profit"]
            
            # 趋势跟随止损
            if not triggered and TREND_TRAIL_ENABLED and pnl_raw >= TREND_TRAIL_TRIGGER:
                tech = get_tech([k for k in all_klines_1h.get(sym, []) if k["time"] <= ts_15m][-30:])
                ema9 = tech.get("ema9")
                if ema9:
                    if pos["direction"] == "long" and pk["low"] <= ema9 * 0.995:
                        triggered = "趋势跟随止损"
                        fill_price = ema9 * 0.995
                    elif pos["direction"] == "short" and pk["high"] >= ema9 * 1.005:
                        triggered = "趋势跟随止损"
                        fill_price = ema9 * 1.005
            
            # 移动止盈
            if not triggered and TRAILING_TP_ENABLED:
                high_water = pos.get("high_water", entry)
                if pos["direction"] == "long":
                    high_water = max(high_water, pk["high"])
                else:
                    high_water = min(high_water, pk["low"]) if high_water < entry else pk["low"]
                    high_water = min(high_water, pk["low"])
                pos["high_water"] = high_water
                
                if pnl_raw >= TRAILING_TP_TRIGGER:
                    if pos["direction"] == "long":
                        pullback = (high_water - pk["close"]) / high_water
                    else:
                        pullback = (pk["close"] - high_water) / high_water if high_water > 0 else 0
                    if pullback >= TRAILING_TP_STEP:
                        triggered = "移动止盈"
            
            # 分批止盈
            if not triggered and PARTIAL_TP_ENABLED and not pos.get("partial_done") and pnl_raw >= 0.05:
                half_usd = pos.get("remaining_qty", pos["position_usd"]) * PARTIAL_TP_RATIO
                half_pnl_pct = pnl_raw * 100 * LEVERAGE
                half_pnl_usd = half_pnl_pct / 100 * half_usd
                balance += half_pnl_usd
                
                all_trades.append({
                    **pos, "exit_price": round(fill_price, 8),
                    "exit_time": dt.strftime("%Y-%m-%dT%H:%M:%S"),
                    "exit_reason": "分批止盈(50%)",
                    "pnl_pct": round(half_pnl_pct, 2),
                    "pnl_usd": round(half_pnl_usd, 2),
                    "status": "partial",
                })
                pos["remaining_qty"] -= half_usd
                pos["partial_done"] = True
                if PARTIAL_TP_MOVE_TO_BREAKEVEN:
                    pos["stop_loss"] = entry
                print(f"  分批止盈 #{pos['id']} {sym} +{half_pnl_usd:.1f}U")
                continue
            
            if triggered:
                to_close.append((pos, fill_price, triggered))
        
        # 执行平仓
        for pos, price, reason in to_close:
            entry = pos["entry_price"]
            qty = pos.get("remaining_qty", pos["position_usd"])
            if pos["direction"] == "long":
                pnl_pct = (price - entry) / entry * 100 * LEVERAGE
            else:
                pnl_pct = (entry - price) / entry * 100 * LEVERAGE
            pnl_usd = pnl_pct / 100 * qty
            
            max_loss = -qty * 2
            if pnl_usd < max_loss:
                pnl_usd = max_loss
                pnl_pct = max_loss / qty * 100
            
            balance += pnl_usd
            
            sym = pos["symbol"]
            if pnl_usd < 0:
                consecutive_losses[sym] = consecutive_losses.get(sym, 0) + 1
            else:
                consecutive_losses[sym] = 0
            
            all_trades.append({
                **pos, "exit_price": round(price, 8),
                "exit_time": dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "exit_reason": reason,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_usd": round(pnl_usd, 2),
                "status": "closed",
            })
            positions.remove(pos)
            cooldowns[sym] = ts_15m
            emoji = "✅" if pnl_usd > 0 else "❌"
            print(f"  {emoji} 平仓 #{pos['id']} {sym} {reason} {pnl_usd:+.1f}U")
        
        # --- 15分钟异动扫描 ---
        if len(positions) >= MAX_POSITIONS or balance <= 100:
            continue
        
        # BTC环境
        btc_before = [k for k in btc_klines if k["time"] <= ts_15m]
        btc_chg = 0
        if len(btc_before) >= 24:
            btc_chg = (btc_before[-1]["close"] - btc_before[-24]["close"]) / btc_before[-24]["close"] * 100
        btc_price = btc_before[-1]["close"] if btc_before else 50000
        fgi = calc_fgi_from_btc(btc_before, btc_price)
        
        candidates = []
        open_symbols = set(p["symbol"] for p in positions)
        
        for sym, kl_1h in all_klines_1h.items():
            if sym in open_symbols:
                continue
            if ts_15m - cooldowns.get(sym, 0) < COOLDOWN_HOURS * 3600 * 1000:
                continue
            
            # 找到最近的K线
            recent_1h = [k for k in kl_1h if k["time"] <= ts_15m]
            if len(recent_1h) < 3:
                continue
            
            # 模拟15分钟异动检测: 用最近1根1h K线的涨跌幅
            # 注意: 真实扫描用15m K线，回测用1h近似
            last_k = recent_1h[-1]
            prev_k = recent_1h[-2]
            
            # 计算最近收盘vs上一根收盘的涨跌幅
            chg_pct = (last_k["close"] - prev_k["close"]) / prev_k["close"]
            
            if abs(chg_pct) < SPIKE_THRESHOLD:
                continue
            
            # ATR过滤
            tech = get_tech(recent_1h[-30:])
            if tech["atr_pct"] < MIN_ATR_FILTER:
                continue
            
            # 异动冷却
            sc = spike_cooldowns.get(sym, {})
            direction = "long" if chg_pct > 0 else "short"
            last_spike = sc.get(direction, 0)
            # 转换为K线根数 (1h K线)
            candles_since = (ts_15m - last_spike) / (3600 * 1000)
            if candles_since < SPIKE_COOLDOWN:
                continue
            spike_cooldowns[sym] = {**sc, direction: ts_15m}
            
            # 确定方向: 涨了做多，跌了做空
            # 但如果涨幅已经很大（>5%），可能是追高，降低优先级
            
            # 动态止损
            atr = tech["atr_pct"]
            if atr > 0:
                sl_pct = atr * ATR_SL_MULTIPLIER
                sl_pct = max(sl_pct, MIN_SL_PCT)
            else:
                sl_pct = DEFAULT_SL_PCT
            tp_pct = sl_pct * 2.5
            rr = round(tp_pct / sl_pct, 2)
            
            # 费率
            fr_hist = all_funding.get(sym, [])
            fr = fr_hist[-1]["rate"] / 100 if fr_hist else 0
            
            # 多时间框架分析
            sym_mtf = mtf_cache.get(sym, {})
            klines_by_frame = {}
            for frame in MTF_FRAMES:
                frame_kl = sym_mtf.get(frame, [])
                # 过滤到此时间点
                frame_kl_before = [k for k in frame_kl if k["time"] <= ts_15m]
                if frame_kl_before:
                    klines_by_frame[frame] = frame_kl_before
            
            mtf_result = mtf_analyze(klines_by_frame, direction)
            
            # 六维评分
            signal = {
                "direction": direction,
                "fr": fr,
                "vol": vol_map.get(sym, 0),
                "change_pct": chg_pct,
                "type": f"spike_{'up' if chg_pct > 0 else 'down'}",
            }
            v8_score, v8_scores = v8_calc_weights_simple(signal, tech, btc_chg, fgi)
            
            # 信号质量
            quality = v8_signal_quality_simple(signal, tech, mtf_result)
            
            # 过滤
            if quality < V8_SIGNAL_QUALITY_MIN:
                signals_filtered += 1
                continue
            if rr < V8_RR_MIN:
                signals_filtered += 1
                continue
            
            signals_found += 1
            
            # 宏观评分
            macro_score = 50
            if fgi <= 25: macro_score = 25
            elif fgi <= 45: macro_score = 40
            elif fgi <= 55: macro_score = 50
            elif fgi <= 75: macro_score = 60
            else: macro_score = 75
            
            candidates.append({
                "symbol": sym,
                "direction": direction,
                "price": last_k["close"],
                "sl_pct": round(sl_pct, 4),
                "tp_pct": round(tp_pct, 4),
                "rr": rr,
                "v8_score": v8_score,
                "v8_scores": v8_scores,
                "quality": quality,
                "macro_normalized": macro_score,
                "mtf_result": mtf_result,
                "signal_type": signal["type"],
                "tech": tech,
                "chg_pct": chg_pct,
                "fr": fr,
            })
        
        # 排序: 多框架一致性 > v8评分 > 信号质量
        candidates.sort(key=lambda c: (
            c["mtf_result"]["consistency_score"],
            c["v8_score"],
            c["quality"],
        ), reverse=True)
        
        # 开仓
        for c in candidates:
            if len(positions) >= MAX_POSITIONS:
                break
            
            sym = c["symbol"]
            price = c["price"]
            
            # Kelly仓位
            pos_usd = v8_kelly_position(balance, V8_DEFAULT_WIN_RATE,
                                         c["rr"], c["quality"], c["macro_normalized"])
            if pos_usd <= 0 or pos_usd > balance * 0.20:
                pos_usd = balance * 0.10
            
            trade_id += 1
            
            if c["direction"] == "long":
                stop_loss = price * (1 - c["sl_pct"])
                take_profit = price * (1 + c["tp_pct"])
            else:
                stop_loss = price * (1 + c["sl_pct"])
                take_profit = price * (1 - c["tp_pct"])
            
            pos = {
                "id": trade_id,
                "symbol": sym,
                "direction": c["direction"],
                "entry_price": round(price, 8),
                "entry_time": dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "stop_loss": round(stop_loss, 8),
                "take_profit": round(take_profit, 8),
                "position_usd": pos_usd,
                "remaining_qty": pos_usd,
                "leverage": LEVERAGE,
                "signal_type": c["signal_type"],
                "signal_strength": "S" if abs(c["chg_pct"]) > 0.03 else "A" if abs(c["chg_pct"]) > 0.02 else "B",
                "signal_sl_pct": c["sl_pct"],
                "signal_rr": c["rr"],
                "v8_score": c["v8_score"],
                "v8_quality": c["quality"],
                "mtf_agree": c["mtf_result"]["agree_count"],
                "mtf_total": c["mtf_result"]["total_frames"],
                "mtf_consistency": c["mtf_result"]["consistency_score"],
                "tech_snapshot": c["tech"],
                "high_water": price,
                "partial_done": False,
            }
            positions.append(pos)
            
            d = "多" if c["direction"] == "long" else "空"
            mtf_info = f"MTF:{c['mtf_result']['agree_count']}/{c['mtf_result']['total_frames']}"
            print(f"  🔔 开仓 #{trade_id} {sym} {d} ${pos_usd:.0f} "
                  f"SL={c['sl_pct']*100:.1f}% 评分={c['v8_score']:+.0f} "
                  f"质量={c['quality']:.0f} {mtf_info} "
                  f"涨跌={c['chg_pct']*100:+.1f}%")
    
    # --- 回测结束，强制平仓 ---
    for pos in positions:
        price = price_cache.get(pos["symbol"], pos["entry_price"])
        entry = pos["entry_price"]
        qty = pos.get("remaining_qty", pos["position_usd"])
        if pos["direction"] == "long":
            pnl_pct = (price - entry) / entry * 100 * LEVERAGE
        else:
            pnl_pct = (entry - price) / entry * 100 * LEVERAGE
        pnl_usd = pnl_pct / 100 * qty
        balance += pnl_usd
        all_trades.append({
            **pos, "exit_price": round(price, 8),
            "exit_time": END_TIME.strftime("%Y-%m-%dT%H:%M:%S"),
            "exit_reason": "回测结束",
            "pnl_pct": round(pnl_pct, 2),
            "pnl_usd": round(pnl_usd, 2),
            "status": "closed",
        })
    
    positions.clear()
    
    # === 统计 ===
    closed = [t for t in all_trades if t.get("status") == "closed"]
    wins = [t for t in closed if (t.get("pnl_usd", 0) or 0) > 0]
    losses = [t for t in closed if (t.get("pnl_usd", 0) or 0) <= 0]
    total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
    
    print("\n" + "=" * 60)
    print("📊 回测结果")
    print("=" * 60)
    print(f"  初始: ${INITIAL_BALANCE:.0f} → ${balance:.2f}")
    print(f"  总盈亏: {total_pnl:+.2f}U ({total_pnl/INITIAL_BALANCE*100:+.1f}%)")
    print(f"  总交易: {len(closed)}笔 ({len(wins)}W/{len(losses)}L)")
    if closed:
        print(f"  胜率: {len(wins)/len(closed)*100:.1f}%")
        avg_win = sum(t["pnl_usd"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl_usd"] for t in losses) / len(losses) if losses else 0
        print(f"  平均盈利: +{avg_win:.2f}U | 平均亏损: {avg_loss:.2f}U")
        if avg_loss != 0:
            print(f"  盈亏比: {abs(avg_win/avg_loss):.2f}")
    
    # 按平仓原因
    print(f"\n📊 按平仓原因:")
    by_reason = defaultdict(list)
    for t in closed:
        by_reason[t.get("exit_reason", "?")].append(t)
    for reason, trades in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        pnl = sum(t.get("pnl_usd", 0) for t in trades)
        wins_r = sum(1 for t in trades if (t.get("pnl_usd", 0) or 0) > 0)
        print(f"  {reason}: {len(trades)}笔 | 胜{wins_r} | PnL {pnl:+.1f}U")
    
    # 多框架一致性 vs 胜率
    print(f"\n📊 多框架一致性分析:")
    consistency_buckets = defaultdict(lambda: {"win": 0, "loss": 0, "pnl": 0})
    for t in closed:
        agree = t.get("mtf_agree", 0)
        total_f = t.get("mtf_total", 1)
        ratio = agree / total_f if total_f > 0 else 0
        bucket = f"{int(ratio*100)}%"
        pnl = t.get("pnl_usd", 0) or 0
        consistency_buckets[bucket]["pnl"] += pnl
        if pnl > 0:
            consistency_buckets[bucket]["win"] += 1
        else:
            consistency_buckets[bucket]["loss"] += 1
    for bucket in sorted(consistency_buckets.keys()):
        d = consistency_buckets[bucket]
        total = d["win"] + d["loss"]
        wr = d["win"] / total * 100 if total > 0 else 0
        print(f"  一致性{bucket}: {total}笔 胜率{wr:.0f}% PnL{d['pnl']:+.1f}U")
    
    # 每月
    print(f"\n📅 每月收益:")
    monthly = defaultdict(lambda: {"pnl": 0, "count": 0, "wins": 0})
    for t in closed:
        month = t.get("exit_time", "")[:7]
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
        "version": "v9",
        "description": "15分钟异动扫描+多时间框架分析",
        "start_time": START_TIME.strftime("%Y-%m-%d"),
        "end_time": END_TIME.strftime("%Y-%m-%d"),
        "initial_balance": INITIAL_BALANCE,
        "final_balance": round(balance, 2),
        "total_pnl": round(total_pnl, 2),
        "total_trades": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins)/len(closed)*100, 1) if closed else 0,
        "max_drawdown": round(max_drawdown, 1),
        "signals_found": signals_found,
        "signals_filtered": signals_filtered,
        "trades": all_trades,
        "summary": {
            "total_pnl": round(total_pnl, 2),
            "total_trades": len(closed),
            "win_rate": round(len(wins)/len(closed), 3) if closed else 0,
            "avg_win": round(avg_win, 2) if wins else 0,
            "avg_loss": round(avg_loss, 2) if losses else 0,
            "profit_factor": round(abs(avg_win/avg_loss), 2) if avg_loss and losses else 0,
            "max_drawdown": round(max_drawdown, 1),
            "signals_found": signals_found,
            "signals_traded": len(closed),
        },
    }
    out_path = Path(__file__).parent / "data" / "backtest_v9_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📁 保存到: {out_path}")
    print("=" * 60)


if __name__ == "__main__":
    run_backtest()
