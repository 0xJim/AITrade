"""
信号检测器 — 4大策略 + 综合环境检查
来源: connectfarm1.com 蒸馏代码整合
"""
import time
from binance_api import (
    get_funding_history, get_open_interest, get_oi_history,
    get_klines, get_btc_trend, get_fear_greed, get_price,
    get_qualified_symbols, get_all_tickers, get_funding_rates,
    api_get, format_usd, now_str,
)
from config import (
    EXTREME_NEG_FUNDING, EXTREME_POS_FUNDING, OI_SURGE_PCT,
    MIN_OI_USD, MIN_ENV_SCORE, COOLDOWN_HOURS,
    MAX_OPEN_POSITIONS, LEVERAGE, POSITION_PCT,
    DEFAULT_SL_PCT, DEFAULT_TP_PCT,
)
from datetime import datetime, timezone, timedelta

TZ_UTC8 = timezone(timedelta(hours=8))


# ═══════════════════════════════════════
# 策略1: 极端负费率 → 做多(逼空)
# ═══════════════════════════════════════
def detect_extreme_neg_funding(symbol: str, funding_rate: float) -> dict | None:
    """
    费率极端深负 → 做多(逼空)
    条件: 当前费率 < -0.08% 且 连续多期为负
    """
    if funding_rate >= EXTREME_NEG_FUNDING:
        return None
    
    history = get_funding_history(symbol, 8)
    if not history:
        return None
    
    neg_count = sum(1 for r in history if r < -0.03)
    if neg_count < 4:
        return None
    
    avg_rate = sum(history) / len(history)
    strength = "S" if avg_rate < -0.15 else "A" if avg_rate < -0.10 else "B"
    
    return {
        "type": "extreme_neg_funding",
        "direction": "long",
        "strength": strength,
        "reason": f"费率极端深负 avg:{avg_rate:.4f}% 连续{neg_count}/8期为负 逼空概率高",
        "sl_pct": 0.08,
        "tp_pct": 0.12,
    }


# ═══════════════════════════════════════
# 策略2: 极端正费率 → 做空(多头拥挤)
# ═══════════════════════════════════════
def detect_extreme_pos_funding(symbol: str, funding_rate: float) -> dict | None:
    """
    费率极端正 → 做空(多头拥挤)
    条件: 当前费率 > 0.10% 且 连续多期高正
    """
    if funding_rate <= EXTREME_POS_FUNDING:
        return None
    
    history = get_funding_history(symbol, 8)
    if not history:
        return None
    
    pos_count = sum(1 for r in history if r > 0.05)
    if pos_count < 4:
        return None
    
    avg_rate = sum(history) / len(history)
    strength = "S" if avg_rate > 0.20 else "A" if avg_rate > 0.12 else "B"
    
    return {
        "type": "extreme_pos_funding",
        "direction": "short",
        "strength": strength,
        "reason": f"费率极端正 avg:{avg_rate:.4f}% 连续{pos_count}/8期高正 多头过度拥挤",
        "sl_pct": 0.10,
        "tp_pct": 0.15,
    }


# ═══════════════════════════════════════
# 策略3: 暴跌反弹(超跌反弹)
# ═══════════════════════════════════════
def detect_crash_bounce(symbol: str, change_pct: float) -> dict | None:
    """
    24h跌>25% 但最近K线企稳/反弹
    """
    if change_pct >= -25:
        return None
    
    klines = get_klines(symbol, "1h", 6)
    if not klines or len(klines) < 3:
        return None
    
    recent_closes = [float(k[4]) for k in klines[-3:]]
    if recent_closes[-1] >= recent_closes[-2]:
        return {
            "type": "crash_bounce",
            "direction": "long",
            "strength": "B",
            "reason": f"24h暴跌{change_pct:.1f}%后企稳 超跌反弹",
            "sl_pct": 0.10,
            "tp_pct": 0.15,
        }
    return None


# ═══════════════════════════════════════
# 策略4: 暴涨后做空(ATH回落)
# ═══════════════════════════════════════
def detect_pump_short(symbol: str, change_pct: float) -> dict | None:
    """
    24h涨>40% 且已从高点回落10%+
    """
    if change_pct <= 40:
        return None
    
    klines = get_klines(symbol, "1h", 6)
    if not klines:
        return None
    
    highs = [float(k[2]) for k in klines]
    closes = [float(k[4]) for k in klines]
    current = closes[-1]
    peak = max(highs)
    
    pullback = (peak - current) / peak * 100
    if pullback < 10:
        return None
    
    strength = "A" if change_pct > 80 else "B"
    
    return {
        "type": "pump_short",
        "direction": "short",
        "strength": strength,
        "reason": f"24h暴涨{change_pct:.1f}%后回落{pullback:.1f}% 历史回调概率>85%",
        "sl_pct": 0.15,
        "tp_pct": 0.20,
    }


# ═══════════════════════════════════════
# 策略5: OI异动突增（来自#03 OI Scanner）
# ═══════════════════════════════════════
def detect_oi_surge(symbol: str) -> dict | None:
    """
    OI 1h内暴涨>5% + 价格同向
    OI增加+价格上涨 = 大资金做多
    OI增加+价格下跌 = 大资金做空
    """
    oi_hist = get_oi_history(symbol, "1h", 6)
    if not oi_hist or len(oi_hist) < 2:
        return None
    
    curr_oi = float(oi_hist[-1]["sumOpenInterestValue"])
    prev_oi = float(oi_hist[-2]["sumOpenInterestValue"])
    
    if prev_oi <= 0 or curr_oi < MIN_OI_USD:
        return None
    
    oi_change_pct = (curr_oi - prev_oi) / prev_oi * 100
    if abs(oi_change_pct) < OI_SURGE_PCT:
        return None
    
    # 获取价格方向
    klines = get_klines(symbol, "1h", 2)
    if not klines or len(klines) < 2:
        return None
    
    price_chg = (float(klines[-1][4]) - float(klines[-2][4])) / float(klines[-2][4]) * 100
    
    # OI增+价格涨 = 做多; OI增+价格跌 = 做空
    if oi_change_pct > 0:
        direction = "long" if price_chg > 0 else "short"
    else:
        return None  # OI减少暂不交易
    
    strength = "A" if abs(oi_change_pct) > 10 else "B"
    
    return {
        "type": "oi_surge",
        "direction": direction,
        "strength": strength,
        "reason": f"OI 1h变化{oi_change_pct:+.1f}% (${curr_oi/1e6:.1f}M) 价格{price_chg:+.1f}% 大资金进场",
        "sl_pct": DEFAULT_SL_PCT,
        "tp_pct": DEFAULT_TP_PCT,
    }


# ═══════════════════════════════════════
# 策略6: 费率翻转（来自#03 OI Scanner）
# ═══════════════════════════════════════
def detect_funding_flip(symbol: str, funding_rate: float) -> dict | None:
    """
    费率从正转负 或 从负转正 (翻转信号)
    正→负: 空头变多，可能有反弹
    负→正: 多头变多，趋势确认
    """
    history = get_funding_history(symbol, 5)
    if not history or len(history) < 3:
        return None
    
    # 最近3期费率
    recent = history[-3:]
    
    # 检测翻转: 前2期同号，最近1期反转
    if recent[0] > 0 and recent[1] > 0 and recent[-1] < -0.01:
        # 正→负: 做多信号（空头过度→可能逼空）
        return {
            "type": "funding_flip_neg",
            "direction": "long",
            "strength": "B",
            "reason": f"费率翻转: {recent[0]:+.3f}%→{recent[1]:+.3f}%→{recent[-1]:+.3f}% 正转负",
            "sl_pct": DEFAULT_SL_PCT,
            "tp_pct": DEFAULT_TP_PCT,
        }
    elif recent[0] < 0 and recent[1] < 0 and recent[-1] > 0.01:
        # 负→正: 做多信号（趋势确认）
        return {
            "type": "funding_flip_pos",
            "direction": "long",
            "strength": "B",
            "reason": f"费率翻转: {recent[0]:+.3f}%→{recent[1]:+.3f}%→{recent[-1]:+.3f}% 负转正趋势确认",
            "sl_pct": DEFAULT_SL_PCT,
            "tp_pct": DEFAULT_TP_PCT,
        }
    
    return None


# ═══════════════════════════════════════
# 综合环境检查（开仓前必过）
# ═══════════════════════════════════════
def check_environment(symbol: str, signal: dict) -> tuple:
    """
    多维度环境检查，返回 (pass/fail, analysis, adjusted_strength)
    评分标准: >= MIN_ENV_SCORE 才开仓
    """
    analysis = {}
    score = 0
    
    # 1. BTC环境
    btc = get_btc_trend()
    btc_chg = btc.get("change_pct", 0)
    
    if signal["direction"] == "long":
        if btc_chg > -2:
            score += 1
            analysis["btc"] = f"BTC {btc_chg:+.1f}% 环境正常 +1"
        elif btc_chg < -5:
            score -= 1
            analysis["btc"] = f"BTC {btc_chg:+.1f}% 暴跌做多危险 -1"
        else:
            analysis["btc"] = f"BTC {btc_chg:+.1f}% 偏弱 0"
    else:
        if btc_chg < 2:
            score += 1
            analysis["btc"] = f"BTC {btc_chg:+.1f}% 环境正常 +1"
        elif btc_chg > 5:
            score -= 1
            analysis["btc"] = f"BTC {btc_chg:+.1f}% 暴涨做空危险 -1"
        else:
            analysis["btc"] = f"BTC {btc_chg:+.1f}% 偏强 0"
    
    # 2. 恐惧贪婪指数
    fgi = get_fear_greed()
    if signal["direction"] == "long":
        if fgi <= 25:
            score += 1
            analysis["sentiment"] = f"FGI={fgi} 极度恐惧 逆向做多 +1"
        elif fgi >= 75:
            score -= 1
            analysis["sentiment"] = f"FGI={fgi} 极度贪婪 做多风险 -1"
        else:
            analysis["sentiment"] = f"FGI={fgi} 中性 0"
    else:
        if fgi >= 75:
            score += 1
            analysis["sentiment"] = f"FGI={fgi} 极度贪婪 逆向做空 +1"
        elif fgi <= 25:
            score -= 1
            analysis["sentiment"] = f"FGI={fgi} 极度恐惧 做空风险 -1"
        else:
            analysis["sentiment"] = f"FGI={fgi} 中性 0"
    
    # 3. OI关注度
    try:
        oi = get_open_interest(symbol)
        price = get_price(symbol)
        oi_usd = oi * price
        if oi_usd > 5_000_000:
            score += 1
            analysis["oi"] = f"OI={format_usd(oi_usd)} 有关注度 +1"
        else:
            analysis["oi"] = f"OI={format_usd(oi_usd)} 关注度低 0"
    except Exception:
        analysis["oi"] = "OI获取失败 0"
    
    # 4. 成交量
    try:
        tickers = get_all_tickers()
        ticker = next((t for t in tickers if t["symbol"] == symbol), None)
        if ticker:
            vol = float(ticker.get("quoteVolume", 0))
            if vol > 50_000_000:
                score += 1
                analysis["volume"] = f"24h量={format_usd(vol)} 活跃 +1"
            elif vol > 20_000_000:
                analysis["volume"] = f"24h量={format_usd(vol)} 一般 0"
            else:
                score -= 1
                analysis["volume"] = f"24h量={format_usd(vol)} 冷清 -1"
    except Exception:
        analysis["volume"] = "量能获取失败 0"
    
    # 5. 信号强度加分
    if signal["strength"] == "S":
        score += 2
    elif signal["strength"] == "A":
        score += 1
    
    analysis["verdict"] = f"综合得分:{score}/7 (需≥{MIN_ENV_SCORE})"
    
    return score >= MIN_ENV_SCORE, analysis, signal["strength"]


# ═══════════════════════════════════════
# 主扫描: 运行所有策略
# ═══════════════════════════════════════
def scan_all_signals(open_symbols: set = None, cooldowns: dict = None) -> list:
    """
    扫描全市场，返回所有检测到的信号（按强度排序）
    """
    if open_symbols is None:
        open_symbols = set()
    if cooldowns is None:
        cooldowns = {}
    
    tickers = get_qualified_symbols()
    funding_rates = get_funding_rates()
    
    signals = []
    now = datetime.now(TZ_UTC8)
    
    for ticker in tickers:
        symbol = ticker["symbol"]
        
        # 跳过已持仓
        if symbol in open_symbols:
            continue
        
        # 冷却检查
        last_open = cooldowns.get(symbol)
        if last_open:
            try:
                last_dt = datetime.fromisoformat(last_open)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=TZ_UTC8)
                if (now - last_dt).total_seconds() < COOLDOWN_HOURS * 3600:
                    continue
            except Exception:
                pass
        
        fr = funding_rates.get(symbol, 0)
        change_pct = float(ticker.get("priceChangePercent", 0))
        price = float(ticker.get("lastPrice", 0))
        
        # 运行所有策略
        detectors = [
            lambda s=symbol, f=fr: detect_extreme_neg_funding(s, f),
            lambda s=symbol, f=fr: detect_extreme_pos_funding(s, f),
            lambda s=symbol, c=change_pct: detect_crash_bounce(s, c),
            lambda s=symbol, c=change_pct: detect_pump_short(s, c),
            lambda s=symbol: detect_oi_surge(s),
            lambda s=symbol, f=fr: detect_funding_flip(s, f),
        ]
        
        for detect_fn in detectors:
            try:
                signal = detect_fn()
                if signal:
                    signal["symbol"] = symbol
                    signal["price"] = price
                    signals.append(signal)
            except Exception:
                continue
    
    # 按强度排序 S > A > B
    strength_order = {"S": 0, "A": 1, "B": 2}
    signals.sort(key=lambda x: strength_order.get(x["strength"], 3))
    
    return signals
