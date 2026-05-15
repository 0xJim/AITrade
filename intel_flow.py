"""
情报流模块 — 整合BlockBeats/聪明钱/6维评分/技术分析大师到交易系统

函数兼容 cron_scan.py 和 notifier.py 的导入需求:
- intel_macro_score()          → env_score() 调用，返回 0/1
- intel_smart_money_confirm()  → env_score() 调用，返回 0/1
- intel_quick_macro()          → notifier 调用，返回 dict 用于通知渲染
- get_tf_scores()             → notifier 调用
- get_smart_money_analysis()  → notifier 调用
"""
import json
import os
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path

# === 导入币安API数据函数（需要时由调用方注入，或直接import） ===
try:
    sys.path.insert(0, str(Path(__file__).parent))
    from binance_api import get_funding_rates, get_price, get_klines, get_open_interest, get_technical_indicators
    from config import OI_SURGE_PCT
except Exception:
    # 降级: 导入失败时函数返回安全默认值
    pass

TZ_UTC8 = timezone(timedelta(hours=8))

# BlockBeats API 配置
BLOCKBEATS_API_KEY = os.environ.get("BLOCKBEATS_API_KEY", "")
BLOCKBEATS_BASE = "https://api-pro.theblockbeats.info/v1"

# 币安Web3 API 配置
SMART_MONEY_URL = "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/web/signal/smart-money/ai"
RANK_URL = "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/buw/wallet/market/token/pulse/unified/rank/list/ai"
INFLOW_URL = "https://web3.binance.com/bapi/defi/v1/public/wallet-direct/tracker/wallet/token/inflow/rank/query/ai"
SM_HEADERS = {
    "Content-Type": "application/json",
    "Accept-Encoding": "identity",
    "User-Agent": "binance-web3/1.1 (Skill)",
}

CACHE_DIR = Path.home() / ".hermes" / "trading" / "data"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════

def _save_cache(name: str, data: dict):
    """保存缓存到文件"""
    try:
        (CACHE_DIR / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

def _load_cache(name: str, max_age_minutes: int = 10) -> dict | None:
    """读取缓存，超时返回None"""
    try:
        fp = CACHE_DIR / name
        if not fp.exists():
            return None
        age = (datetime.now().timestamp() - fp.stat().st_mtime) / 60
        if age > max_age_minutes:
            return None
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return None

def _bb_get(endpoint: str, params: dict = None) -> dict | None:
    """BlockBeats API GET"""
    if not BLOCKBEATS_API_KEY:
        return None
    try:
        headers = {"api-key": BLOCKBEATS_API_KEY}
        resp = requests.get(f"{BLOCKBEATS_BASE}{endpoint}", headers=headers, params=params, timeout=10)
        if resp.status_code == 200:
            return resp.json().get("data", {})
    except Exception:
        pass
    return None

def _smart_money_post(url: str, payload: dict) -> dict | None:
    """币安Web3 POST请求"""
    try:
        resp = requests.post(url, json=payload, headers=SM_HEADERS, timeout=10)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════
# 1. intel_macro_score() — 宏观环境评分
# ═══════════════════════════════════════════

def intel_macro_score() -> int:
    """
    宏观环境评分 (0 或 1)
    
    评分逻辑:
    - BlockBeats情绪指数 <30 + BTC ETF连续正流入 → +1
    - 仅BlockBeats情绪 <30 → +1
    - 仅稳定币市值扩大 → +1
    - 没有BlockBeats API Key时，基于BTC趋势+ETF数据推断
    """
    try:
        # 1. 尝试从BlockBeats获取情绪指数
        sentiment = _bb_get("/data/bottom_top_indicator")
        if sentiment:
            indicator = sentiment.get("indicator", 50) or sentiment.get("value", 50)
            if isinstance(indicator, (int, float)) and indicator < 30:
                # 极端恐惧 → 做多机会
                return 1
            if isinstance(indicator, (int, float)) and indicator > 80:
                # 极端贪婪 → 做空机会（但调用方已基于方向判断）
                return 1
        
        # 2. 尝试获取BTC ETF净流入
        etf = _bb_get("/data/btc_etf")
        if etf:
            inflow = etf.get("net_inflow", 0) or etf.get("total_net_inflow", 0)
            if isinstance(inflow, (int, float)) and inflow > 300_000_000:
                return 1
        
        # 3. 稳定币市值扩大
        stablecoin = _bb_get("/data/stablecoin_marketcap")
        if stablecoin:
            trend = stablecoin.get("trend", "") or stablecoin.get("direction", "")
            if trend in ("up", "expand", "增加", "扩大"):
                return 1
        
        # 4. 降级: 无BlockBeats Key时，基于BTC 24h趋势判断
        try:
            from binance_api import get_btc_trend
            btc = get_btc_trend()
            chg = abs(btc.get("change_pct", 0))
            # BTC波动在合理范围(<5%)视为宏观稳定→+1
            if chg < 3:
                return 1
        except Exception:
            pass
        
        return 0
    except Exception:
        return 0


# ═══════════════════════════════════════════
# 2. intel_smart_money_confirm() — 聪明钱信号验证
# ═══════════════════════════════════════════

def intel_smart_money_confirm(cand: dict) -> int:
    """
    聪明钱信号确认 (0 或 1)
    
    检查逻辑:
    1. 从币安Web3 API获取聪明钱买入/卖出信号列表
    2. 看候选币的symbol是否出现在聪明钱信号中
    3. 方向与候选方向一致则+1
    
    降级策略:
    - 币安API不可用时，用基差/资金费率变化推断
    """
    symbol = cand.get("symbol", "")
    direction = cand.get("direction", "")

    if not symbol:
        return 0

    try:
        # 1. 币安Web3 聪明钱信号 (Solana链为主)
        sm_result = _smart_money_post(SMART_MONEY_URL, {
            "smartSignalType": "", "page": 1, "pageSize": 100, "chainId": "CT_501"
        })
        if sm_result:
            signals = []
            if isinstance(sm_result, dict):
                signals = sm_result.get("data", {}).get("list", [])
            elif isinstance(sm_result, list):
                signals = sm_result
            
            base_symbol = symbol.replace("USDT", "").upper()
            for s in signals:
                ticker = (s.get("ticker", "") or "").upper()
                sm_dir = s.get("direction", "")  # "buy" 或 "sell"
                if base_symbol in ticker or ticker in base_symbol:
                    if direction == "long" and sm_dir == "buy":
                        return 1
                    elif direction == "short" and sm_dir == "sell":
                        return 1
                    # 方向不同但聪明钱在活动也加分（流动性确认）
                    if s.get("smartMoneyCount", 0) > 3:
                        return 1
        
        # 2. 降级: 检查资金费率变化（极端费率持续→逼空信号=聪明钱活动）
        try:
            from binance_api import get_funding_history
            fr_hist = get_funding_history(symbol)
            if fr_hist and direction == "long":
                neg_count = sum(1 for r in fr_hist if r < -0.05)
                if neg_count >= 4:
                    return 1  # 持续负费率=空头埋单=聪明钱在做多
            elif fr_hist and direction == "short":
                pos_count = sum(1 for r in fr_hist if r > 0.08)
                if pos_count >= 4:
                    return 1  # 持续正费率=多头拥挤=聪明钱在做空
        except Exception:
            pass
        
        # 3. 降级: 检查OI变化（OI激增+价格方向→大资金活动）
        try:
            from binance_api import get_oi_history
            oi_hist = get_oi_history(symbol, "1h", 5)
            if oi_hist and len(oi_hist) >= 2:
                oi_first = float(oi_hist[0].get("sumOpenInterest", 0))
                oi_last = float(oi_hist[-1].get("sumOpenInterest", 0))
                if oi_first > 0 and oi_last > 0:
                    oi_chg_pct = (oi_last - oi_first) / oi_first * 100
                    if abs(oi_chg_pct) > 15:
                        return 1  # OI大幅变化=聪明钱参与
        except Exception:
            pass
        
        return 0
    except Exception:
        return 0


# ═══════════════════════════════════════════
# 3. intel_quick_macro() — 宏观快照 (用于通知)
# ═══════════════════════════════════════════

def intel_quick_macro() -> dict:
    """
    宏观快照: BTC趋势+FGI+ETF流入等
    返回结构化数据，供通知系统渲染
    """
    result = {
        "btc_trend": "N/A", "btc_price": 0, "btc_24h_change": 0,
        "fgi": 50, "fgi_label": "中性",
        "eth_inflow": 0, "total_mcap_change": 0,
        "dominance": 0, "spot_volume_24h": 0, "funding_avg": 0,
    }

    try:
        # BTC趋势
        try:
            from binance_api import get_btc_trend, get_price, get_fear_greed
            btc = get_btc_trend()
            result["btc_trend"] = btc.get("trend", "N/A")
            result["btc_24h_change"] = btc.get("change_pct", 0)
            result["btc_price"] = get_price("BTCUSDT")
        except Exception:
            pass

        # FGI
        try:
            fgi = get_fear_greed()
            result["fgi"] = fgi
            if fgi <= 20: result["fgi_label"] = "极度恐惧"
            elif fgi <= 40: result["fgi_label"] = "恐惧"
            elif fgi <= 60: result["fgi_label"] = "中性"
            elif fgi <= 80: result["fgi_label"] = "贪婪"
            else: result["fgi_label"] = "极度贪婪"
        except Exception:
            pass

        # BlockBeats数据增强（如果有API Key）
        if BLOCKBEATS_API_KEY:
            etf = _bb_get("/data/btc_etf")
            if etf:
                result["eth_inflow"] = etf.get("net_inflow", 0) or etf.get("total_net_inflow", 0)
            
            stablecoin = _bb_get("/data/stablecoin_marketcap")
            if stablecoin:
                result["total_mcap_change"] = stablecoin.get("change_pct", 0) or stablecoin.get("change", 0)
            
            dominance = _bb_get("/data/exchanges", {"page": 1, "size": 1})
            if dominance:
                result["dominance"] = dominance.get("dominance", 0) or dominance.get("btc_dominance", 0)

        # 资金费率平均值
        try:
            from binance_api import get_funding_rates
            rates = get_funding_rates()
            if rates:
                vals = [abs(v) for v in rates.values() if isinstance(v, (int, float))]
                result["funding_avg"] = round(sum(vals) / len(vals), 4) if vals else 0
        except Exception:
            pass

        # 缓存起来
        _cache_path = CACHE_DIR / "macro_cache.json"
        _cache_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    return result


# ═══════════════════════════════════════════
# 4. get_tf_scores() — 多时间框架评分
# ═══════════════════════════════════════════

def get_tf_scores(symbol: str = "BTCUSDT") -> dict:
    """
    多时间框架评分 (1h/4h)
    返回: {1h: {trend, score, rsi}, 4h: {trend, score, rsi}, summary}
    """
    try:
        from binance_api import get_klines

        def _analyze_tf(klines, period="1h"):
            if not klines or len(klines) < 50:
                return {"trend": "neutral", "score": 0, "rsi": 50, "ema_aligned": False}
            closes = [float(k[4]) for k in klines]
            # Mean Reversion RSI
            gains, losses = 0, 0
            for i in range(1, min(15, len(closes))):
                diff = closes[-i] - closes[-i-1]
                if diff > 0: gains += diff
                else: losses += abs(diff)
            rsi = 50
            if losses > 0:
                rs = (gains / min(14, len(closes)-1)) / (losses / min(14, len(closes)-1))
                rsi = 100 - (100 / (1 + rs))
            
            # EMA趋势
            ema_short = sum(closes[-min(9, len(closes)):]) / min(9, len(closes))
            ema_long = sum(closes[-min(21, len(closes)):]) / min(21, len(closes))
            trend = "up" if ema_short > ema_long else "down"
            
            # 评分: -5~+5
            price = closes[-1]
            pct_chg = (price - closes[-min(20, len(closes))]) / closes[-min(20, len(closes))] * 100 if len(closes) >= 20 else 0
            score = max(-5, min(5, int(pct_chg)))
            
            return {
                "trend": trend,
                "score": score,
                "rsi": round(rsi, 1),
                "ema_aligned": abs(ema_short - ema_long) / ema_long > 0.002 if ema_long > 0 else False,
            }

        klines_1h = get_klines(symbol, "1h", 50)
        klines_4h = get_klines(symbol, "4h", 50)
        tf1h = _analyze_tf(klines_1h, "1h")
        tf4h = _analyze_tf(klines_4h, "4h")

        # summary
        if tf1h["trend"] == tf4h["trend"]:
            if tf1h["trend"] == "up":
                summary = "多周期共振向上 ✅"
            elif tf1h["trend"] == "down":
                summary = "多周期共振向下 ❌"
            else:
                summary = "方向不明，观望为主"
        elif tf1h["trend"] == "up" and tf4h["trend"] == "down":
            summary = "短多长空，注意风险"
        elif tf1h["trend"] == "down" and tf4h["trend"] == "up":
            summary = "短空长多，逢低布局"
        else:
            summary = "方向不明，观望为主"

        result = {"1h": tf1h, "4h": tf4h, "summary": summary}
        _save_cache(f"tf_scores_{symbol}.json", result)
        return result

    except Exception:
        return {
            "1h": {"trend": "neutral", "score": 0, "rsi": 50, "ema_aligned": False},
            "4h": {"trend": "neutral", "score": 0, "rsi": 50, "ema_aligned": False},
            "summary": "数据不足",
        }


# ═══════════════════════════════════════════
# 5. get_smart_money_analysis() — 聪明钱分析
# ═══════════════════════════════════════════

def get_smart_money_analysis(symbol: str = "BTCUSDT") -> dict:
    """
    聪明钱参与情况分析
    返回: {participation, large_trades_ratio, taker_buy_ratio, oi_change_24h, ...}
    """
    try:
        base_symbol = symbol.replace("USDT", "").upper()
        participation = "weak"
        oi_change = 0
        
        # 尝试从币安Web3获取聪明钱信号
        sm_result = _smart_money_post(SMART_MONEY_URL, {
            "smartSignalType": "", "page": 1, "pageSize": 100, "chainId": "CT_501"
        })
        
        smart_money_found = False
        if sm_result:
            signals = sm_result.get("data", {}).get("list", []) if isinstance(sm_result, dict) else []
            for s in signals:
                ticker = (s.get("ticker", "") or "").upper()
                if base_symbol in ticker or ticker in base_symbol:
                    if s.get("smartMoneyCount", 0) > 3:
                        smart_money_found = True
                        break
        
        # OI变化分析
        try:
            from binance_api import get_oi_history
            oi_hist = get_oi_history(symbol, "1h", 5)
            if oi_hist and len(oi_hist) >= 2:
                oi_first = float(oi_hist[0].get("sumOpenInterest", 0))
                oi_last = float(oi_hist[-1].get("sumOpenInterest", 0))
                if oi_first > 0:
                    oi_change = (oi_last - oi_first) / oi_first * 100
        except Exception:
            pass
        
        # 综合判断
        if smart_money_found and abs(oi_change) > 10:
            participation = "strong"
        elif smart_money_found or abs(oi_change) > 15:
            participation = "moderate"
        
        interpretation = {
            "strong": "聪明钱大举进场，市场有主导力量",
            "moderate": "聪明钱适度参与，未出现极端信号",
            "weak": "聪明钱参与度低，多为散户博弈",
        }.get(participation, "数据不足")
        
        result = {
            "participation": participation,
            "large_trades_ratio": 0,
            "taker_buy_ratio": 0,
            "oi_change_24h": round(oi_change, 2),
            "liquidation_dominance": "balanced",
            "interpretation": interpretation,
        }
        
        _save_cache(f"smart_money_{symbol}.json", result)
        return result

    except Exception:
        return {
            "participation": "weak", "large_trades_ratio": 0,
            "taker_buy_ratio": 0, "oi_change_24h": 0,
            "liquidation_dominance": "balanced", "interpretation": "数据不足",
        }


# ═══════════════════════════════════════════
# 6. intel_advanced_tech() — 高级技术指标
# ═══════════════════════════════════════════

def intel_advanced_tech(symbol: str) -> dict:
    """
    高级技术分析: RSI精确+MACD+BB位置+成交量变化+多EMA
    
    返回:
    {
        "rsi": float,
        "macd": {"hist": float, "signal": "bullish/bearish"},
        "bb": {"position": float (0-1), "width": float},
        "ema": {"ema9": float, "ema21": float, "trend": str},
        "volume_change_24h": float (%)
    }
    """
    try:
        from binance_api import get_klines
        klines = get_klines(symbol, "1h", 100)
        if not klines or len(klines) < 50:
            return {}

        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[1]) for k in klines]
        volumes = [float(k[5]) for k in klines]
        price = closes[-1]

        # === RSI(14) ===
        gains, losses = 0, 0
        for i in range(1, len(closes)):
            diff = closes[-i] - closes[-i-1]
            if diff > 0: gains += diff
            else: losses += abs(diff)
        rsi = 50
        if losses > 0:
            rs = (gains / 14) / (losses / 14)
            rsi = 100 - (100 / (1 + rs))

        # === MACD ===
        def _ema(data, period):
            if len(data) < period:
                return data[-1] if data else 0
            mult = 2 / (period + 1)
            ema = sum(data[:period]) / period
            for p in data[period:]:
                ema = (p - ema) * mult + ema
            return ema
        
        macd_line = _ema(closes, 12) - _ema(closes, 26)
        signal_line = _ema([macd_line] * 9 + [macd_line], 9)
        macd_hist = macd_line - signal_line

        # === Bollinger Bands ===
        bb_period = 20
        sma = sum(closes[-bb_period:]) / bb_period
        variance = sum((c - sma)**2 for c in closes[-bb_period:]) / bb_period
        std = variance ** 0.5
        bb_upper = sma + 2 * std
        bb_lower = sma - 2 * std
        bb_position = (price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5
        bb_width = (bb_upper - bb_lower) / sma if sma > 0 else 0

        # === EMA趋势 ===
        ema9 = _ema(closes, 9)
        ema21 = _ema(closes, 21)
        ema_trend = "up" if ema9 > ema21 else "down"

        # === 成交量变化 ===
        vol_now = sum(volumes[-24:])
        vol_before = sum(volumes[-48:-24]) if len(volumes) >= 48 else sum(volumes)//2
        vol_change = (vol_now - vol_before) / vol_before * 100 if vol_before > 0 else 0

        return {
            "rsi": round(rsi, 1),
            "macd": {
                "hist": round(macd_hist, 4),
                "signal": "bullish" if macd_hist > 0 else "bearish",
            },
            "bb": {
                "position": round(bb_position, 3),
                "width": round(bb_width, 4),
                "interpretation": "上轨" if bb_position > 0.8 else ("下轨" if bb_position < 0.2 else "中轨"),
            },
            "ema": {
                "ema9": round(ema9, 2),
                "ema21": round(ema21, 2),
                "trend": ema_trend,
            },
            "volume_change_24h_avg": round(vol_change, 1),
        }
    except Exception:
        return {}


# ═══════════════════════════════════════════
# 7. intel_trade_plan_score() — 6维评分
# ═══════════════════════════════════════════

def intel_trade_plan_score(cand: dict, market_data: dict = None) -> dict:
    """
    6维交易计划评分 (仿OKX交易计划生成器)
    
    权重:
    - 动量 25% | 费率 15% | OI 15% | 情绪 15% | 成交量 15% | 宏观 15%
    
    返回: {total_score, direction, scores: {...}}
    总分范围: -100 ~ +100
    方向: long/neutral/short
    """
    try:
        symbol = cand.get("symbol", "")
        d = cand.get("direction", "long")
        fr = cand.get("fr", 0)
        chg = cand.get("change", 0)
        vol = cand.get("vol", 0)
        
        # 1. 动量评分 (-100 ~ +100)
        momentum = max(-100, min(100, int(chg * 2)))
        if d == "short":
            momentum = -momentum  # 做空角度看
        
        # 2. 费率评分 (-100 ~ +100)
        if fr < -0.10:
            funding = 80
        elif fr < -0.05:
            funding = 50
        elif fr > 0.15:
            funding = -80
        elif fr > 0.08:
            funding = -50
        else:
            funding = 0
        
        # 3. OI评分
        oi_score = 0
        try:
            from binance_api import get_oi_history
            oi_hist = get_oi_history(symbol, "1h", 5)
            if oi_hist and len(oi_hist) >= 2:
                oi_first = float(oi_hist[0].get("sumOpenInterest", 0))
                oi_last = float(oi_hist[-1].get("sumOpenInterest", 0))
                if oi_first > 0:
                    oi_chg = (oi_last - oi_first) / oi_first * 100
                    if oi_chg > 20: oi_score = 60
                    elif oi_chg > 10: oi_score = 30
                    elif oi_chg > 0: oi_score = 15
                    elif oi_chg < -20: oi_score = -30
                    elif oi_chg < -10: oi_score = -15
        except Exception:
            pass
        
        # 4. 情绪评分
        try:
            from binance_api import get_fear_greed
            fgi = get_fear_greed()
            if fgi < 20: sentiment = 70
            elif fgi < 40: sentiment = 30
            elif fgi > 80: sentiment = -70
            elif fgi > 60: sentiment = -30
            else: sentiment = 0
        except Exception:
            sentiment = 0
        
        # 5. 成交量评分
        vol_score = 0
        if vol > 500_000_000: vol_score = 60
        elif vol > 200_000_000: vol_score = 40
        elif vol > 100_000_000: vol_score = 20
        elif vol < 30_000_000: vol_score = -30
        
        # 6. 宏观评分
        try:
            from binance_api import get_btc_trend
            btc = get_btc_trend()
            btc_chg = btc.get("change_pct", 0)
            macro = 50 if abs(btc_chg) < 3 else (-30 if abs(btc_chg) > 6 else 0)
        except Exception:
            macro = 0
        
        # 加权总分
        weights = {"momentum": 0.25, "funding": 0.15, "oi": 0.15,
                   "sentiment": 0.15, "volume": 0.15, "macro": 0.15}
        total = (momentum * weights["momentum"] + 
                 funding * weights["funding"] +
                 oi_score * weights["oi"] +
                 sentiment * weights["sentiment"] +
                 vol_score * weights["volume"] +
                 macro * weights["macro"])
        
        direction = "long" if total > 30 else ("short" if total < -30 else "neutral")
        
        return {
            "total_score": round(total, 1),
            "direction": direction,
            "scores": {
                "momentum": round(momentum, 1),
                "funding": round(funding, 1),
                "oi": round(oi_score, 1),
                "sentiment": round(sentiment, 1),
                "volume": round(vol_score, 1),
                "macro": round(macro, 1),
            },
            "weights": weights,
        }
    except Exception:
        return {"total_score": 0, "direction": "neutral", "scores": {}}


# ═══════════════════════════════════════════
# 8. intel_signal_rank() — 多维度候选排名
# ═══════════════════════════════════════════

def intel_signal_rank(candidates: list) -> list:
    """
    对所有候选进行多维度综合排名
    
    评分维度: 费率极端程度(30%) + 技术面(25%) + 聪明钱(15%) + 宏观(15%) + 成交量(15%)
    返回排序后的list，每项增加rank_score字段
    """
    if not candidates:
        return candidates
    
    for cand in candidates:
        try:
            rank_score = 0
            details = []
            
            # 费率极端程度 (0-30)
            fr = abs(cand.get("fr", 0))
            fr_score = min(30, int(fr * 100))
            rank_score += fr_score
            
            # 信号强度 (0-20)
            strength = cand.get("strength", "B")
            if strength == "S": rank_score += 20
            elif strength == "A": rank_score += 15
            elif strength == "B": rank_score += 8
            
            # 成交量 (0-15)
            vol = cand.get("vol", 0)
            if vol > 500_000_000: rank_score += 15
            elif vol > 200_000_000: rank_score += 12
            elif vol > 100_000_000: rank_score += 8
            elif vol > 50_000_000: rank_score += 4
            
            # 聪明钱确认 (0-15)
            try:
                sm = intel_smart_money_confirm(cand)
                if sm > 0:
                    rank_score += 15
            except Exception:
                pass
            
            # 宏观 (0-10)
            try:
                macro = intel_macro_score()
                if macro > 0:
                    rank_score += 10
            except Exception:
                pass
            
            # 价格变化幅度加分 (0-10)
            chg = abs(cand.get("change", 0))
            if chg > 50: rank_score += 10
            elif chg > 30: rank_score += 5
            
            # ATR稳定加分 (0-10)
            atr = cand.get("atr_pct", 0)
            if 0.5 < atr < 3:  # 合适波动范围
                rank_score += 5
            
            cand["rank_score"] = min(100, rank_score)
        except Exception:
            cand["rank_score"] = 0
    
    return sorted(candidates, key=lambda x: x.get("rank_score", 0), reverse=True)
