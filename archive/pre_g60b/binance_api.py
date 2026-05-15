"""
币安API数据层 — 行情、费率、OI、K线、下单、平仓
支持模拟盘(testnet)和实盘
"""
import time
import hmac
import hashlib
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

from config import FAPI, MIN_VOLUME_M, BINANCE_API_KEY, BINANCE_API_SECRET

TZ_UTC8 = timezone(timedelta(hours=8))


def _sign(params: dict) -> dict:
    """为请求参数添加 HMAC SHA256 签名"""
    params["timestamp"] = int(time.time() * 1000)
    query = urlencode(params)
    signature = hmac.new(
        BINANCE_API_SECRET.encode(), query.encode(), hashlib.sha256
    ).hexdigest()
    params["signature"] = signature
    return params


def _headers() -> dict:
    """带API Key的请求头"""
    return {"X-MBX-APIKEY": BINANCE_API_KEY}


def api_get(endpoint: str, params: dict = None, retries: int = 3) -> dict | list | None:
    """币安合约API GET请求（公开，无需签名）"""
    url = f"{FAPI}{endpoint}"
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                time.sleep(2)
            else:
                return None
        except Exception:
            time.sleep(1)
    return None


def signed_get(endpoint: str, params: dict = None, retries: int = 3) -> dict | list | None:
    """币安合约API 签名GET请求"""
    if params is None:
        params = {}
    url = f"{FAPI}{endpoint}"
    for attempt in range(retries):
        try:
            signed_params = _sign(dict(params))
            resp = requests.get(url, params=signed_params, headers=_headers(), timeout=10)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                time.sleep(2)
            else:
                return {"error": resp.status_code, "msg": resp.text}
        except Exception as e:
            time.sleep(1)
    return None


def signed_post(endpoint: str, params: dict = None, retries: int = 3) -> dict | None:
    """币安合约API 签名POST请求"""
    if params is None:
        params = {}
    url = f"{FAPI}{endpoint}"
    for attempt in range(retries):
        try:
            signed_params = _sign(dict(params))
            resp = requests.post(url, params=signed_params, headers=_headers(), timeout=10)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                time.sleep(2)
            else:
                return {"error": resp.status_code, "msg": resp.text}
        except Exception as e:
            time.sleep(1)
    return None


def signed_delete(endpoint: str, params: dict = None, retries: int = 3) -> dict | None:
    """币安合约API 签名DELETE请求"""
    if params is None:
        params = {}
    url = f"{FAPI}{endpoint}"
    for attempt in range(retries):
        try:
            signed_params = _sign(dict(params))
            resp = requests.delete(url, params=signed_params, headers=_headers(), timeout=10)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                time.sleep(2)
            else:
                return {"error": resp.status_code, "msg": resp.text}
        except Exception as e:
            time.sleep(1)
    return None


# === 账户 ===

def get_balance() -> dict:
    """获取账户余额信息"""
    return signed_get("/fapi/v2/balance")


def get_positions(symbol: str = None) -> list:
    """获取当前持仓，可选指定symbol"""
    params = {}
    if symbol:
        params["symbol"] = symbol
    data = signed_get("/fapi/v2/positionRisk", params)
    if data and isinstance(data, list):
        # 只返回有持仓的
        return [p for p in data if float(p.get("positionAmt", 0)) != 0]
    return data or []


def get_account_info() -> dict:
    """获取完整账户信息"""
    return signed_get("/fapi/v2/account")


# === 交易 ===

def set_leverage(symbol: str, leverage: int) -> dict:
    """设置杠杆"""
    return signed_post("/fapi/v1/leverage", {
        "symbol": symbol,
        "leverage": leverage,
    })


def place_order(symbol: str, side: str, quantity: float,
                order_type: str = "MARKET", price: float = None,
                stop_price: float = None, reduce_only: bool = False) -> dict:
    """
    下单
    side: BUY / SELL
    order_type: MARKET / LIMIT / STOP_MARKET / TAKE_PROFIT_MARKET
    """
    params = {
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "quantity": quantity,
    }
    if price and order_type == "LIMIT":
        params["price"] = price
        params["timeInForce"] = "GTC"
    if stop_price:
        params["stopPrice"] = stop_price
    if reduce_only:
        params["reduceOnly"] = "true"
    return signed_post("/fapi/v1/order", params)


def open_long(symbol: str, quantity: float, leverage: int = 5) -> dict:
    """开多仓"""
    set_leverage(symbol, leverage)
    return place_order(symbol, "BUY", quantity)


def open_short(symbol: str, quantity: float, leverage: int = 5) -> dict:
    """开空仓"""
    set_leverage(symbol, leverage)
    return place_order(symbol, "SELL", quantity)


def close_position(symbol: str, quantity: float, direction: str) -> dict:
    """平仓 direction: 'long' / 'short'"""
    side = "SELL" if direction == "long" else "BUY"
    return place_order(symbol, side, quantity, reduce_only=True)


def cancel_all_orders(symbol: str) -> dict:
    """取消某币种所有挂单"""
    return signed_delete("/fapi/v1/allOpenOrders", {"symbol": symbol})


def get_exchange_info(symbol: str = None) -> dict:
    """获取交易规则（精度等）"""
    params = {}
    if symbol:
        params["symbol"] = symbol
    return api_get("/fapi/v1/exchangeInfo", params)


def get_symbol_precision(symbol: str) -> dict:
    """获取某交易对的数量精度和价格精度"""
    data = get_exchange_info(symbol)
    if not data or "symbols" not in data:
        return {"quantity_precision": 3, "price_precision": 6}
    for s in data["symbols"]:
        if s["symbol"] == symbol:
            return {
                "quantity_precision": s["quantityPrecision"],
                "price_precision": s["pricePrecision"],
            }
    return {"quantity_precision": 3, "price_precision": 6}


# === 行情数据 ===

def get_all_tickers() -> list:
    """获取所有合约24h行情"""
    return api_get("/fapi/v1/ticker/24hr") or []


def get_funding_rates() -> dict:
    """获取所有币种最新费率 {symbol: rate_pct}"""
    data = api_get("/fapi/v1/premiumIndex") or []
    return {item["symbol"]: float(item["lastFundingRate"]) * 100 for item in data}


def get_funding_history(symbol: str, limit: int = 8) -> list:
    """获取费率历史 [rate_pct, ...]"""
    data = api_get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": limit})
    if data:
        return [float(item["fundingRate"]) * 100 for item in data]
    return []


def get_open_interest(symbol: str) -> float:
    """获取当前持仓量(OI)"""
    data = api_get("/fapi/v1/openInterest", {"symbol": symbol})
    if data:
        return float(data.get("openInterest", 0))
    return 0


def get_oi_history(symbol: str, period: str = "1h", limit: int = 6) -> list:
    """获取OI历史"""
    return api_get("/futures/data/openInterestHist", {
        "symbol": symbol, "period": period, "limit": limit
    }) or []


def get_klines(symbol: str, interval: str = "4h", limit: int = 6) -> list:
    """获取K线数据"""
    return api_get("/fapi/v1/klines", {
        "symbol": symbol, "interval": interval, "limit": limit
    }) or []


def get_price(symbol: str) -> float:
    """获取最新价格"""
    data = api_get("/fapi/v1/ticker/price", {"symbol": symbol})
    if data:
        return float(data.get("price", 0))
    return 0


# === 市场情绪 ===

def get_btc_trend() -> dict:
    """获取BTC 24h变化"""
    data = api_get("/fapi/v1/ticker/24hr", {"symbol": "BTCUSDT"})
    if data:
        return {
            "change_pct": float(data.get("priceChangePercent", 0)),
            "price": float(data.get("lastPrice", 0)),
            "volume": float(data.get("quoteVolume", 0)),
        }
    return {"change_pct": 0, "price": 0, "volume": 0}


def get_fear_greed() -> int:
    """获取恐惧贪婪指数"""
    try:
        resp = requests.get("https://api.alternative.me/fng/", timeout=5)
        if resp.status_code == 200:
            return int(resp.json()["data"][0]["value"])
    except Exception:
        pass
    return 50  # 默认中性


# === 数据处理 ===

def get_qualified_symbols(tickers: list = None) -> list:
    """
    获取合格的交易对
    过滤: USDT合约 + 排除稳定币 + 最小成交额
    """
    if tickers is None:
        tickers = get_all_tickers()
    
    exclude = {"BTCUSDT", "ETHUSDT", "USDCUSDT", "FDUSDUSDT", "BTCDOMUSDT", "BTCSTUSDT"}
    
    qualified = []
    for t in tickers:
        sym = t.get("symbol", "")
        vol = float(t.get("quoteVolume", 0))
        if (sym.endswith("USDT") 
            and sym not in exclude 
            and vol > MIN_VOLUME_M * 1e6):
            qualified.append(t)
    
    return qualified


def format_usd(v: float) -> str:
    """格式化USD金额"""
    if v >= 1e9:
        return f"${v/1e9:.1f}B"
    if v >= 1e6:
        return f"${v/1e6:.1f}M"
    if v >= 1e3:
        return f"${v/1e3:.0f}K"
    return f"${v:.0f}"


def now_str() -> str:
    """当前时间字符串(UTC+8)"""
    return datetime.now(TZ_UTC8).strftime("%Y-%m-%dT%H:%M:%S")


# === 技术指标计算 (v2 新增) ===

def calc_ema(closes: list, period: int) -> float:
    """计算EMA值"""
    if len(closes) < period:
        return closes[-1] if closes else 0
    multiplier = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calc_rsi(closes: list, period: int = 14) -> float:
    """计算RSI值"""
    if len(closes) < period + 1:
        return 50.0  # 数据不足返回中性
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    
    if len(gains) < period:
        return 50.0
    
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_atr(klines: list, period: int = 14) -> float:
    """计算ATR (Average True Range)"""
    if len(klines) < period + 1:
        return 0
    
    true_ranges = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i-1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    
    if len(true_ranges) < period:
        return sum(true_ranges) / len(true_ranges) if true_ranges else 0
    
    return sum(true_ranges[-period:]) / period


def get_technical_indicators(symbol: str) -> dict:
    """
    获取完整技术指标
    返回: {ema_fast, ema_slow, rsi, atr, atr_pct, trend, closes}
    """
    # 获取足够多的K线来计算指标
    klines_4h = get_klines(symbol, "4h", 30)  # 4h K线用于EMA趋势
    klines_1h = get_klines(symbol, "1h", 20)  # 1h K线用于RSI和ATR
    
    result = {
        "ema_fast": 0, "ema_slow": 0,
        "rsi": 50, "atr": 0, "atr_pct": 0,
        "trend": "neutral",  # up / down / neutral
        "closes_4h": [], "closes_1h": [],
        "price": 0,
    }
    
    if klines_4h and len(klines_4h) >= 21:
        closes_4h = [float(k[4]) for k in klines_4h]
        result["closes_4h"] = closes_4h
        result["ema_fast"] = calc_ema(closes_4h, 9)
        result["ema_slow"] = calc_ema(closes_4h, 21)
        result["price"] = closes_4h[-1]
        
        # 趋势判断
        if result["ema_fast"] > result["ema_slow"] * 1.005:
            result["trend"] = "up"
        elif result["ema_fast"] < result["ema_slow"] * 0.995:
            result["trend"] = "down"
        else:
            result["trend"] = "neutral"
    
    if klines_1h and len(klines_1h) >= 15:
        closes_1h = [float(k[4]) for k in klines_1h]
        result["closes_1h"] = closes_1h
        result["rsi"] = calc_rsi(closes_1h, 14)
        
        if not result["price"]:
            result["price"] = closes_1h[-1]
    
    # ATR计算
    if klines_1h and len(klines_1h) >= 15:
        result["atr"] = calc_atr(klines_1h, 14)
        if result["price"] > 0:
            result["atr_pct"] = result["atr"] / result["price"] * 100
    
    return result


def get_technical_indicators_v8(symbol: str) -> dict:
    """
    v8多时间框架技术指标分析
    同时获取1h和4h数据，独立计算各时间框架趋势
    
    返回:
    {
        "1h": {"trend": "up"|"down"|"neutral", "rsi": float, "ema_fast": float, "ema_slow": float},
        "4h": {"trend": "up"|"down"|"neutral", "rsi": float, "ema_fast": float, "ema_slow": float},
        "tf_aligned": bool (两框架趋势一致),
        "tf_bias": "up"|"down"|"neutral" (一致时方向，不一致时"neutral"),
    }
    """
    result = {"1h": {}, "4h": {}, "tf_aligned": False, "tf_bias": "neutral"}
    
    # 1h时间框架
    klines_1h = get_klines(symbol, "1h", 30)
    if klines_1h and len(klines_1h) >= 21:
        closes_1h = [float(k[4]) for k in klines_1h]
        ema9 = calc_ema(closes_1h, 9)
        ema21 = calc_ema(closes_1h, 21)
        rsi_val = calc_rsi(closes_1h, 14)
        result["1h"] = {
            "trend": "up" if ema9 and ema21 and ema9 > ema21 * 1.005 else ("down" if ema9 and ema21 and ema9 < ema21 * 0.995 else "neutral"),
            "rsi": round(rsi_val, 1) if rsi_val else 50,
            "ema_fast": round(ema9, 2) if ema9 else 0,
            "ema_slow": round(ema21, 2) if ema21 else 0,
            "price": closes_1h[-1],
        }
    
    # 4h时间框架
    klines_4h = get_klines(symbol, "4h", 50)
    if klines_4h and len(klines_4h) >= 21:
        closes_4h = [float(k[4]) for k in klines_4h]
        ema9 = calc_ema(closes_4h, 9)
        ema21 = calc_ema(closes_4h, 21)
        rsi_val = calc_rsi(closes_4h, 14)
        result["4h"] = {
            "trend": "up" if ema9 and ema21 and ema9 > ema21 * 1.005 else ("down" if ema9 and ema21 and ema9 < ema21 * 0.995 else "neutral"),
            "rsi": round(rsi_val, 1) if rsi_val else 50,
            "ema_fast": round(ema9, 2) if ema9 else 0,
            "ema_slow": round(ema21, 2) if ema21 else 0,
            "price": closes_4h[-1],
        }
    
    # 框架一致性
    if result["1h"].get("trend") and result["4h"].get("trend"):
        if result["1h"]["trend"] == result["4h"]["trend"]:
            result["tf_aligned"] = True
            result["tf_bias"] = result["1h"]["trend"]
    
    return result
