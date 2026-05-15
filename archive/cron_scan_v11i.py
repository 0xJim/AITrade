#!/usr/bin/env python3
"""
交易扫描脚本 v6 — 综合优化版
基于v10回测数据(118笔/60%胜率/+$357)深度分析的v4优化:
1. 动态黑名单: 近30天亏损>$50且胜率<40%的币种自动屏蔽
2. MTF≥3门槛: 多时间框架一致性≥3才入场
3. 做空高v8_score减仓: v8≥5做空仓位减半(41%胜率是最大亏损源)
4. 入场宽限期: 4h内不扫止损
5. 同步回测与实盘参数
"""
import sys
import json
import os
import time
from collections import defaultdict

sys.path.insert(0, '/home/ubuntu/.hermes/trading')

from datetime import datetime, timezone, timedelta
TZ_UTC8 = timezone(timedelta(hours=8))

from config import *
from binance_api import (
    get_qualified_symbols, get_funding_rates, get_funding_history,
    get_klines, get_price, get_btc_trend, get_fear_greed,
    get_open_interest, get_oi_history,
    format_usd, now_str,
    get_technical_indicators, get_technical_indicators_v8, calc_ema, calc_rsi, calc_atr,
    # 真实下单功能
    get_balance as api_get_balance,
    get_positions as api_get_positions,
    get_account_info,
    get_symbol_precision,
    set_leverage,
    place_order,
    open_long,
    open_short,
    close_position,
)
from notifier import format_open_message, format_close_message, format_review_message
from intel_flow import intel_macro_score, intel_smart_money_confirm, intel_quick_macro
from review_db import sync_trade, add_tag, add_note, get_stats, get_recent_trades

TRADES_FILE = DATA_DIR / "trades.json"
STATE_FILE = DATA_DIR / "scanner_state.json"


# === v4: 动态黑名单 ===
def update_dynamic_blacklist(data):
    """
    基于近30天交易数据自动更新黑名单
    条件: ≥BLACKLIST_MIN_TRADES笔 且 亏损>$BLACKLIST_MAX_LOSS_USD 且 胜率<BLACKLIST_MAX_WIN_RATE
    """
    try:
        from config import (BLACKLIST_LOOKBACK_DAYS, BLACKLIST_MIN_TRADES,
                          BLACKLIST_MAX_LOSS_USD, BLACKLIST_MAX_WIN_RATE, BLACKLIST_FILE)
    except ImportError:
        return set()
    
    now = datetime.now(TZ_UTC8)
    cutoff = now - timedelta(days=BLACKLIST_LOOKBACK_DAYS)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S")
    
    recent = [t for t in data["trades"] 
              if t["status"] == "closed" and t.get("exit_time","").startswith(cutoff_str[:10])]
    
    by_sym = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0})
    for t in recent:
        sym = t["symbol"]
        by_sym[sym]["n"] += 1
        if t.get("pnl_usd", 0) > 0:
            by_sym[sym]["wins"] += 1
        by_sym[sym]["pnl"] += t.get("pnl_usd", 0)
    
    blacklist = set()
    for sym, stats in by_sym.items():
        if (stats["n"] >= BLACKLIST_MIN_TRADES and 
            stats["pnl"] < -BLACKLIST_MAX_LOSS_USD and
            stats["wins"] / stats["n"] < BLACKLIST_MAX_WIN_RATE):
            blacklist.add(sym)
    
    # 加载旧黑名单，合并保存
    old = set()
    if BLACKLIST_FILE.exists():
        try:
            old_data = json.loads(BLACKLIST_FILE.read_text())
            old = set(old_data.get("symbols", []))
        except Exception:
            pass
    all_black = old | blacklist
    
    BLACKLIST_FILE.write_text(json.dumps({
        "updated": now_str(),
        "symbols": sorted(all_black),
        "reasons": {sym: f"近{BLACKLIST_LOOKBACK_DAYS}天{by_sym[sym]['n']}笔,胜{by_sym[sym]['wins']/by_sym[sym]['n']*100:.0f}%,亏${by_sym[sym]['pnl']:.0f}" 
                    for sym in blacklist}
    }, ensure_ascii=False, indent=2))
    
    return all_black


def load_blacklist():
    """加载当前黑名单 = 静态黑名单 + 动态黑名单"""
    combined = set()
    try:
        from config import STATIC_BLACKLIST
        combined.update(STATIC_BLACKLIST)
    except ImportError:
        pass
    try:
        from config import BLACKLIST_FILE
        if BLACKLIST_FILE.exists():
            data = json.loads(BLACKLIST_FILE.read_text())
            combined.update(data.get("symbols", []))
    except Exception:
        pass
    return combined


# === 数据 ===
def load_json(path, default):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_local_balance(data):
    """本地记账余额"""
    balance = data.get("initial_balance", INITIAL_BALANCE)
    for t in data["trades"]:
        if t["status"] == "closed" and t.get("pnl_usd") is not None:
            balance += t["pnl_usd"]
    return balance

def next_id(data):
    if not data["trades"]:
        return "001"
    return f"{max(int(t['id']) for t in data['trades']) + 1:03d}"

def log(msg: str):
    ts = datetime.now(TZ_UTC8).strftime("%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, file=sys.stderr)

def notify(msg: str):
    """有交易动作时发通知（开仓/平仓/复盘）"""
    log(msg.replace('\n', ' | ')[:200])
    try:
        import requests as _req
        _req.post(
            "http://localhost:8787/api/message",
            json={"platform": "weixin", "target": "o9cq8027klx228XLCADsYPoUwiY8@im.wechat", "text": msg},
            timeout=5,
        )
    except Exception:
        pass

def get_api_usdt_balance():
    """从币安API获取USDT可用余额"""
    bal = api_get_balance()
    if isinstance(bal, list):
        for b in bal:
            if b.get("asset") == "USDT":
                return float(b.get("availableBalance", 0)), float(b.get("balance", 0))
    elif isinstance(bal, dict) and "error" in bal:
        log(f"API余额查询失败: {bal}")
    return None, None

def execute_open(symbol: str, direction: str, usd_amount: float, leverage: int) -> dict:
    """在币安真实下单开仓"""
    result = {"success": False, "order": None, "quantity": 0, "error": ""}
    
    try:
        prec = get_symbol_precision(symbol)
        qty_prec = prec.get("quantity_precision", 3)
        
        price = get_price(symbol)
        if price <= 0:
            result["error"] = f"无法获取{symbol}价格"
            return result
        
        quantity = round(usd_amount * leverage / price, qty_prec)
        if quantity <= 0:
            result["error"] = f"计算数量为0: usd={usd_amount} price={price}"
            return result
        
        log(f"下单 {symbol} {direction} qty={quantity} ({usd_amount}U×{leverage}x)")
        
        lev_result = set_leverage(symbol, leverage)
        if isinstance(lev_result, dict) and "error" in lev_result:
            log(f"杠杆设置返回: {lev_result}")
        
        if direction == "long":
            order = open_long(symbol, quantity, leverage)
        else:
            order = open_short(symbol, quantity, leverage)
        
        if isinstance(order, dict) and "error" in order:
            result["error"] = f"下单失败: {order}"
            log(f"下单失败: {order}")
            return result
        
        if order and order.get("status") in ("NEW", "FILLED", "PARTIALLY_FILLED"):
            result["success"] = True
            result["order"] = order
            result["quantity"] = quantity
            result["fill_price"] = float(order.get("avgPrice", 0)) or float(order.get("price", 0)) or price
            log(f"下单成功: orderId={order.get('orderId')} status={order.get('status')} fill={result['fill_price']}")
        else:
            result["error"] = f"未知下单结果: {order}"
            log(f"未知下单结果: {order}")
        
    except Exception as e:
        result["error"] = str(e)
        log(f"下单异常: {e}")
    
    return result

def execute_close(symbol: str, direction: str, quantity: float) -> dict:
    """在币安真实平仓"""
    result = {"success": False, "order": None, "error": ""}
    
    try:
        log(f"平仓 {symbol} {direction} qty={quantity}")
        order = close_position(symbol, quantity, direction)
        
        if isinstance(order, dict) and "error" in order:
            result["error"] = f"平仓失败: {order}"
            log(f"平仓失败: {order}")
            return result
        
        if order and order.get("status") in ("NEW", "FILLED", "PARTIALLY_FILLED"):
            result["success"] = True
            result["order"] = order
            result["fill_price"] = float(order.get("avgPrice", 0)) or float(order.get("price", 0)) or 0
            log(f"平仓成功: orderId={order.get('orderId')} fill={result['fill_price']}")
        else:
            result["error"] = f"未知平仓结果: {order}"
            log(f"未知平仓结果: {order}")
    
    except Exception as e:
        result["error"] = str(e)
        log(f"平仓异常: {e}")
    
    return result


# ═══════════════════════════════════════════
# v5: 信号扫描 — 三重确认 (费率+趋势+RSI)
# ═══════════════════════════════════════════

def quick_scan(open_symbols, cooldowns):
    """
    快速扫描: 筛选极端费率/暴涨暴跌候选
    v5: 阈值收紧，只保留高质量信号
    """
    tickers = get_qualified_symbols()
    funding = get_funding_rates()
    now = datetime.now(TZ_UTC8)
    candidates = []
    
    for t in tickers:
        sym = t["symbol"]
        if sym in open_symbols:
            continue
        last = cooldowns.get(sym)
        if last:
            try:
                ld = datetime.fromisoformat(last)
                if ld.tzinfo is None:
                    ld = ld.replace(tzinfo=TZ_UTC8)
                if (now - ld).total_seconds() < COOLDOWN_HOURS * 3600:
                    continue
            except: pass
        
        fr = funding.get(sym, 0)
        chg = float(t.get("priceChangePercent", 0))
        price = float(t.get("lastPrice", 0))
        vol = float(t.get("quoteVolume", 0))
        
        if fr < EXTREME_NEG_FUNDING:
            # v5: 负费率做多需要BTC不暴跌 + RSI超卖确认
            candidates.append({"symbol": sym, "type": "extreme_neg_funding", "direction": "long",
                "strength": "B", "price": price, "fr": fr, "change": chg, "vol": vol,
                "reason": f"极端负费率{fr:+.4f}%"})
        elif fr > EXTREME_POS_FUNDING:
            candidates.append({"symbol": sym, "type": "extreme_pos_funding", "direction": "short",
                "strength": "B", "price": price, "fr": fr, "change": chg, "vol": vol,
                "reason": f"极端正费率{fr:+.4f}%"})
        elif chg < -25:
            # v3: 恢复暴跌阈值 -30→-25，与signals.py统一
            candidates.append({"symbol": sym, "type": "crash_bounce", "direction": "long",
                "strength": "B", "price": price, "fr": fr, "change": chg, "vol": vol,
                "reason": f"24h暴跌{chg:+.1f}%"})
        elif chg > 40:
            # v3: 恢复暴涨阈值 50→40，与signals.py统一
            candidates.append({"symbol": sym, "type": "pump_short", "direction": "short",
                "strength": "B", "price": price, "fr": fr, "change": chg, "vol": vol,
                "reason": f"24h暴涨{chg:+.1f}%"})
        else:
            # 没有触发以上4类 → 检查额外信号: OI异动 + 费率翻转
            # 只在初始候选不满足时尝试，避免不必要的API调用
            pass  # 额外信号放在循环外单独检测，避免每个币都调API
    
    # === 额外信号: OI异动 + 费率翻转（每轮最多扫20个币减少API负载）===
    extra_checked = 0
    for t in tickers:
        if extra_checked >= 20:
            break
        sym = t["symbol"]
        # 跳过已在候选中的币
        if any(c["symbol"] == sym for c in candidates):
            continue
        # 跳过已持仓
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
            except: pass
        
        fr = funding.get(sym, 0)
        chg = float(t.get("priceChangePercent", 0))
        price = float(t.get("lastPrice", 0))
        vol = float(t.get("quoteVolume", 0))
        extra_checked += 1
        
        # 5) OI异动（需API调用）
        try:
            oi_hist = get_oi_history(sym, "1h", 6)
            if oi_hist and len(oi_hist) >= 2:
                curr_oi = float(oi_hist[-1].get("sumOpenInterestValue", 0))
                prev_oi = float(oi_hist[-2].get("sumOpenInterestValue", 0))
                if prev_oi > 0 and curr_oi >= MIN_OI_USD:
                    oi_chg_pct = (curr_oi - prev_oi) / prev_oi * 100
                    if oi_chg_pct >= OI_SURGE_PCT:
                        # OI增加+价格涨=做多, OI+价格跌=做空
                        oi_direction = "long" if chg > 0 else "short"
                        oi_strength = "A" if oi_chg_pct >= 10 else "B"
                        candidates.append({"symbol": sym, "type": "oi_surge",
                            "direction": oi_direction, "strength": oi_strength,
                            "price": price, "fr": fr, "change": chg, "vol": vol,
                            "reason": f"OI异动{oi_chg_pct:+.1f}% (${curr_oi/1e6:.1f}M) 价格{chg:+.1f}% 大资金进场"})
        except Exception:
            pass
        
        # 6) 费率翻转（需API调用：获取费率历史）
        try:
            fr_hist = get_funding_history(sym, 5)
            if fr_hist and len(fr_hist) >= 3:
                recent = fr_hist[-3:]
                if recent[0] > 0 and recent[1] > 0 and recent[-1] < -0.01:
                    # 正→负: 做多信号
                    flip_strength = "A" if abs(recent[-1]) > 0.05 else "B"
                    candidates.append({"symbol": sym, "type": "funding_flip_neg",
                        "direction": "long", "strength": flip_strength,
                        "price": price, "fr": fr, "change": chg, "vol": vol,
                        "reason": f"费率翻转: {recent[0]:+.3f}→{recent[1]:+.3f}→{recent[-1]:+.3f} 正转负"})
                elif recent[0] < 0 and recent[1] < 0 and recent[-1] > 0.01:
                    # 负→正: 做多信号（趋势确认）
                    flip_strength = "A" if recent[-1] > 0.05 else "B"
                    candidates.append({"symbol": sym, "type": "funding_flip_pos",
                        "direction": "long", "strength": flip_strength,
                        "price": price, "fr": fr, "change": chg, "vol": vol,
                        "reason": f"费率翻转: {recent[0]:+.3f}→{recent[1]:+.3f}→{recent[-1]:+.3f} 负转正"})
        except Exception:
            pass
    
    return candidates


# ═══════════════════════════════════════════
# v8: 六维加权评分系统
# ═══════════════════════════════════════════

def v8_calc_weights(cand: dict, tech: dict, btc_trend: dict) -> tuple:
    """
    v8六维加权评分系统
    基于trading-plan-generator的六维评分(-100~+100) + kline-indicator三支柱框架
    
    Args:
        cand: 候选信号字典
        tech: 技术指标字典(get_technical_indicators返回值)
        btc_trend: BTC趋势字典
        
    Returns:
        (total_score, scores_dict, detail_string)
        total_score: -100~+100, >+30看多, <-30看空
    """
    symbol = cand["symbol"]
    direction = cand["direction"]
    d = 1 if direction == "long" else -1
    scores = {}
    details = []
    
    # 1. OI变化趋势 (20%) — 每维评分±20
    try:
        oi_hist = get_oi_history(symbol, "1h", 5)
        if oi_hist and len(oi_hist) >= 2:
            curr_oi = float(oi_hist[-1].get("sumOpenInterestValue", 0))
            prev_oi = float(oi_hist[-2].get("sumOpenInterestValue", 0))
            price_chg = cand.get("change", 0)
            if prev_oi > 0:
                oi_chg = (curr_oi - prev_oi) / prev_oi * 100
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
    except Exception:
        scores["oi"] = 0
    if "oi" not in scores:
        scores["oi"] = 0
    details.append(f"OI:{scores['oi']:+d}")
    
    # 2. 资金费率 (15%) — 每维评分±15
    fr = cand.get("fr", 0)
    if d == 1:  # 做多: 负费率好
        if fr < -0.05: scores["funding"] = 15
        elif fr < -0.01: scores["funding"] = 8
        elif fr > 0.1: scores["funding"] = -15
        elif fr > 0.05: scores["funding"] = -8
        else: scores["funding"] = 0
    else:  # 做空: 正费率好
        if fr > 0.1: scores["funding"] = 15
        elif fr > 0.05: scores["funding"] = 8
        elif fr < -0.1: scores["funding"] = -15
        elif fr < -0.05: scores["funding"] = -8
        else: scores["funding"] = 0
    details.append(f"费率:{scores['funding']:+d}")
    
    # 3. 量价因子 (25%) — 每维评分±25
    rsi = tech.get("rsi", 50)
    trend = tech.get("trend", "neutral")
    atr_pct = tech.get("atr_pct", 0)
    vol = cand.get("vol", 0)
    
    pv_score = 0  # -5~+5基础分
    # EMA趋势
    if (d == 1 and trend == "up") or (d == -1 and trend == "down"):
        pv_score += 3
    elif (d == -1 and trend == "up") or (d == 1 and trend == "down"):
        pv_score -= 2
    # RSI
    if d == 1:
        if rsi < 30: pv_score += 2
        elif rsi < 45: pv_score += 1
        elif rsi > 65: pv_score -= 2
    else:
        if rsi > 70: pv_score += 2
        elif rsi > 55: pv_score += 1
        elif rsi < 35: pv_score -= 2
    # 成交量确认
    if vol > 200_000_000: pv_score += 1
    elif vol < 50_000_000: pv_score -= 1
    # ATR适中加分
    if 0.5 < atr_pct < 5: pv_score += 1
    # 映射到±25
    scores["pv"] = max(-25, min(25, int(pv_score * 5)))
    details.append(f"量价:{scores['pv']:+d}")
    
    # 4. 宏观环境 (15%) — 每维评分±15
    macro = 0
    btc_chg = btc_trend.get("change_pct", 0)
    fgi = get_fear_greed()
    # BTC方向
    if (d == 1 and btc_chg > -1) or (d == -1 and btc_chg < 1):
        macro += 5
    elif (d == 1 and btc_chg < -5) or (d == -1 and btc_chg > 5):
        macro -= 5
    # FGI情绪
    if (d == 1 and fgi <= 30) or (d == -1 and fgi >= 70):
        macro += 5
    elif (d == 1 and fgi >= 70) or (d == -1 and fgi <= 30):
        macro -= 3
    # intel宏观评分(0~5)
    try:
        im = intel_macro_score()
        if im > 0: macro += im * 2
    except Exception:
        pass
    scores["macro"] = max(-15, min(15, macro))
    details.append(f"宏观:{scores['macro']:+d}")
    
    # 5. 清算数据 (10%) — 每维评分±10
    liq_score = 0
    # 通过聪明钱信号推断清算压力
    try:
        sm = intel_smart_money_confirm(cand)
        if sm > 0:
            liq_score = 5 if d == 1 else -5  # 聪明钱支持=正向
    except Exception:
        pass
    # 费率极端=清算风险大
    if abs(fr) > 0.15:
        liq_score -= 3
    scores["liquidation"] = max(-10, min(10, liq_score))
    details.append(f"清算:{scores['liquidation']:+d}")
    
    # 6. 聪明钱信号 (15%) — 每维评分±15
    try:
        sm_score = intel_smart_money_confirm(cand)
        if sm_score > 0:
            scores["smart_money"] = sm_score * 8  # 0~2 × 8 = 0~16
        else:
            scores["smart_money"] = 0
    except Exception:
        scores["smart_money"] = 0
    scores["smart_money"] = max(-15, min(15, scores.get("smart_money", 0)))
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


def v8_signal_quality(cand: dict, tech: dict) -> float:
    """
    v8信号质量评分 0-100
    四维加权: 量价40% + 形态30% + 订单流20% + 宏观10%
    源自kline-indicator trading.md Signal Quality (Weighted Score ≥ 65)
    
    返回: 0-100分
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
    # 成交量确认
    if vol > 200_000_000: pv += 10
    elif vol > 100_000_000: pv += 5
    elif vol < 50_000_000: pv -= 5
    # ATR适中加分
    if 0.5 < atr_pct < 5: pv += 5
    pv = max(0, min(40, pv))
    
    # 2. 形态识别 (30分) — v8完整形态识别
    pattern_score = 10
    patterns_1h = cand.get("patterns_1h", [])
    patterns_4h = cand.get("patterns_4h", [])
    if patterns_1h or patterns_4h:
        pattern_score = v8_pattern_score(patterns_1h, patterns_4h, d)
    else:
        pattern_score = 10  # 形态数据不可用时默认
    
    # 3. 订单流 (20分) — 费率+聪明钱推断市场压力
    of_score = 10
    if d == "long" and fr < -0.03: of_score += 5
    if d == "long" and fr < -0.10: of_score += 5
    if d == "short" and fr > 0.05: of_score += 5
    if d == "short" and fr > 0.10: of_score += 5
    # OI配合
    try:
        oi_hist = get_oi_history(cand["symbol"], "1h", 3)
        if oi_hist and len(oi_hist) >= 2:
            oi_chg = (float(oi_hist[-1].get("sumOpenInterestValue", 0)) - 
                      float(oi_hist[-2].get("sumOpenInterestValue", 0)))
            if (d == "long" and oi_chg > 0) or (d == "short" and oi_chg < 0):
                of_score += 3
    except Exception:
        pass
    of_score = max(0, min(20, of_score))
    
    # 4. 宏观 (10分)
    macro_score = 5  # 中性基础分
    try:
        btc = get_btc_trend()
        btc_chg = btc.get("change_pct", 0)
        if (d == "long" and btc_chg > -2) or (d == "short" and btc_chg < 2):
            macro_score += 3
        if (d == "long" and btc_chg < -5) or (d == "short" and btc_chg > 5):
            macro_score -= 3
        fgi = get_fear_greed()
        if (d == "long" and fgi <= 30) or (d == "short" and fgi >= 70):
            macro_score += 2
    except Exception:
        pass
    macro_score = max(0, min(10, macro_score))
    
    final = pv + pattern_score + of_score + macro_score
    return max(0, min(100, final))


def v8_kelly_position(balance: float, win_rate: float, rr: float,
                       signal_quality: float, macro_normalized: float) -> float:
    """
    Kelly动态仓位计算
    源自kline-indicator trading.md Position Sizing
    
    f* = (p*b - q) / b
    p=胜率, q=1-p, b=RR(风险回报比)
    
    v8调整:
    - Kelly分数 × V8_KELLY_FRACTION(25%) 保守使用
    - 信号质量因子: quality/65 调整倍率
    - 宏观因子: 宏观极端折半
    """
    q = 1 - win_rate
    if rr <= 0:
        kelly = 0
    else:
        kelly = (win_rate * rr - q) / rr
    kelly = max(0, min(0.20, kelly))  # 限制不超过20%
    
    # 信号质量调整因子
    quality_factor = max(0.5, min(1.5, signal_quality / V8_SIGNAL_QUALITY_MIN))
    
    # 宏观调整: 远离50=折半 (v3: 不再依赖V8_MACRO_ACCEPTABLE)
    macro_dist = abs(macro_normalized - 50) / 50
    macro_factor = max(0.5, 1.0 - macro_dist * 0.5)
    
    pos_pct = kelly * V8_KELLY_FRACTION * quality_factor * macro_factor * 100
    pos_pct = max(2, min(20, pos_pct))  # 2%-20%范围
    
    return round(balance * pos_pct / 100, 2)


def deep_check(cand):
    """
    深度验证: 费率历史 + 技术指标三重确认
    v5核心优化: 加入EMA趋势 + RSI + ATR动态止损
    """
    sym = cand["symbol"]
    
    # === 1. 费率历史验证 ===
    fr_hist = get_funding_history(sym, 8)
    if not fr_hist:
        return None
    avg = sum(fr_hist) / len(fr_hist)
    
    if cand["type"] == "extreme_neg_funding":
        neg = sum(1 for r in fr_hist if r < -0.03)
        if neg < 3: return None  # v3: 保持≥3/8期（cron_scan主用，比signals.py宽松）
        s = "S" if avg < -0.15 else "A" if avg < -0.08 else "B"  # v3: 与signals.py统一
        cand["strength"] = s
        cand["reason"] = f"极端负费率 avg:{avg:+.4f}% 连续{neg}/8期为负"
    elif cand["type"] == "extreme_pos_funding":
        pos = sum(1 for r in fr_hist if r > 0.05)
        if pos < 3: return None
        s = "S" if avg > 0.20 else "A" if avg > 0.12 else "B"  # v3: 与signals.py统一
        cand["strength"] = s
        cand["reason"] = f"极端正费率 avg:{avg:+.4f}% 连续{pos}/8期高正"
    elif cand["type"] == "crash_bounce":
        klines = get_klines(sym, "1h", 3)
        if not klines or len(klines) < 2: return None
        closes = [float(k[4]) for k in klines]
        if closes[-1] < closes[-2]: return None  # 必须开始反弹
        cand["reason"] = f"24h暴跌{cand['change']:+.1f}% 后企稳反弹"
        cand["strength"] = "A" if cand["change"] < -35 else "B"  # v3: 与signals.py统一(-35)
    elif cand["type"] == "pump_short":
        klines = get_klines(sym, "1h", 6)
        if not klines: return None
        highs = [float(k[2]) for k in klines]
        closes = [float(k[4]) for k in klines]
        pb = (max(highs) - closes[-1]) / max(highs) * 100
        if pb < 5: return None  # v3: 恢复回落阈值 8→5（与signals.py统一）
        cand["reason"] = f"24h暴涨{cand['change']:+.1f}% 后回落{pb:.0f}%"
        cand["strength"] = "A" if pb > 10 else "B"  # v3: 与signals.py统一
    elif cand["type"] in ("oi_surge", "funding_flip_neg", "funding_flip_pos"):
        # OI异动和费率翻转: 不需要费率历史验证(quick_scan已确认)
        # 但在deep_check中校验OI是否仍然有效(对于oi_surge)
        if cand["type"] == "oi_surge":
            # 再查一次OI确认信号强度
            try:
                oi_hist = get_oi_history(sym, "1h", 2)
                if oi_hist and len(oi_hist) >= 2:
                    curr_oi = float(oi_hist[-1].get("sumOpenInterestValue", 0))
                    prev_oi = float(oi_hist[-2].get("sumOpenInterestValue", 0))
                    if prev_oi > 0:
                        oi_chg = (curr_oi - prev_oi) / prev_oi * 100
                        if oi_chg < OI_SURGE_PCT:
                            cand["strength"] = "B"
                            cand["reason"] += " | OI续涨{oi_chg:+.1f}%"
                        else:
                            cand["strength"] = "A" if oi_chg >= 10 else cand["strength"]
            except Exception:
                pass
        # 费率翻转统一评估
        if cand["type"].startswith("funding_flip"):
            flip_strength = "A" if abs(cand.get("fr", 0)) > 0.03 else "B"
            cand["strength"] = flip_strength
    
    # === 2. 技术指标确认 (v5核心新增) ===
    tech = get_technical_indicators(sym)
    cand["tech"] = tech
    
    # v8: 多时间框架分析
    try:
        tech_v8 = get_technical_indicators_v8(sym)
        cand["tech_v8"] = tech_v8
        cand["tf_aligned"] = tech_v8.get("tf_aligned", False)
        cand["tf_bias"] = tech_v8.get("tf_bias", "neutral")
        cand["patterns_1h"] = v8_recognize_patterns(get_klines(sym, "1h", 10))
        cand["patterns_4h"] = v8_recognize_patterns(get_klines(sym, "4h", 10))
        cand["pattern_info"] = v8_pattern_score(
            cand.get("patterns_1h", []),
            cand.get("patterns_4h", []),
            cand["direction"]
        )
        # 多框架一致则加分
        if cand["tf_aligned"] and cand["tf_bias"] == cand["direction"]:
            cand["reason"] += " | 1h/4h一致"
    except Exception as e:
        pass
    
    d = cand["direction"]
    trend = tech["trend"]
    rsi = tech["rsi"]
    
    # v3: 趋势改为软评分(加在信号质量分中)，不再硬拒绝
    # 逆势交易降低env_score但允许进入评分系统
    # deep_check中只做标记，main()中的v8_signal_quality会自然处理
    
    # 顺势加分
    if d == "long" and trend == "up":
        if cand["strength"] == "B":
            cand["strength"] = "A"  # 趋势配合升级
            cand["reason"] += " | EMA多头排列"
    if d == "short" and trend == "down":
        if cand["strength"] == "B":
            cand["strength"] = "A"
            cand["reason"] += " | EMA空头排列"
    
    # RSI极值加分（保留信号质量参考）
    if d == "long" and rsi < 35:
        cand["reason"] += f" | RSI={rsi:.0f}超卖"
    if d == "short" and rsi > 65:
        cand["reason"] += f" | RSI={rsi:.0f}超买"
    
    # 记录趋势方向供v8使用
    cand["trend_aligned"] = (d == "long" and trend != "down") or (d == "short" and trend != "up")
    cand["rsi_extreme"] = (d == "long" and rsi > 75) or (d == "short" and rsi < 25)
    
    # === 3. ATR动态止损 ===
    atr_pct = tech.get("atr_pct", 0)
    if atr_pct > 0:
        # ATR止损 = 1.5倍ATR
        sl_atr = atr_pct * ATR_SL_MULTIPLIER
        # 取ATR止损和固定最小止损的较大值
        sl_pct = max(sl_atr, DEFAULT_SL_PCT)
        # v3: 移除5%上限 — 回测证明宽SL(7-15%)也有46%胜率，强制压SL只触发更多止损
        # TP = SL × RR比 (动态)
        tp_pct = sl_pct * 2.5
    else:
        sl_pct = DEFAULT_SL_PCT
        tp_pct = DEFAULT_TP_PCT
    
    cand["sl_pct"] = round(sl_pct, 4)
    cand["tp_pct"] = round(tp_pct, 4)
    cand["atr_pct"] = round(atr_pct, 3)
    cand["rsi"] = round(rsi, 1)
    cand["ema_trend"] = trend
    
    return cand


# ═══════════════════════════════════════════
# v8: K线形态识别 (30+种)
# ═══════════════════════════════════════════

def v8_recognize_patterns(klines: list) -> list:
    """
    识别K线形态
    源自kline-indicator indicators.md
    
    Args:
        klines: 标准币安K线数组 [[ts,o,h,l,c,vol,...], ...]
        
    Returns:
        [{"name": str, "direction": str, "grade": str}, ...]
        grade: A+(强反转) / A(突破) / B+(持续) / C(中性)
    """
    if not klines or len(klines) < 3:
        return []
    
    patterns = []
    
    # 取最近3根K线
    k3 = klines[-3:]
    o1, h1, l1, c1 = [float(k3[0][i]) for i in [1,2,3,4]]
    o2, h2, l2, c2 = [float(k3[1][i]) for i in [1,2,3,4]]
    o3, h3, l3, c3 = [float(k3[2][i]) for i in [1,2,3,4]]
    
    # 辅助函数
    def body(o, c): return abs(c - o)
    def upper_shadow(h, o, c): return h - max(o, c)
    def lower_shadow(l, o, c): return min(o, c) - l
    def is_green(o, c): return c > o
    def is_red(o, c): return c < o
    
    b1 = body(o1, c1)
    b2 = body(o2, c2)
    b3 = body(o3, c3)
    total_range = max(h1,l1,h2,l2,h3,l3) - min(l1,l2,l3,o1,o2,o3,c1,c2,c3)
    
    if total_range == 0:
        return []
    
    # === 锤子线 (Hammer) — 看涨反转 ===
    # 下影线≥2倍实体，上影线<实体30%
    us3 = upper_shadow(h3, o3, c3)
    ls3 = lower_shadow(l3, o3, c3)
    if b3 > 0 and ls3 > b3 * 2 and us3 < b3 * 0.3 and is_red(o3, c3):
        patterns.append({"name": "Hammer", "direction": "long", "grade": "A+"})
    
    # === 射击之星 (Shooting Star) — 看跌反转 ===
    if b3 > 0 and us3 > b3 * 2 and ls3 < b3 * 0.3 and is_green(o3, c3):
        patterns.append({"name": "Shooting_Star", "direction": "short", "grade": "A+"})
    
    # === 看涨吞没 (Bullish Engulfing) ===
    if is_red(o2, c2) and is_green(o3, c3) and c3 > o2 and o3 < c2:
        patterns.append({"name": "Bullish_Engulfing", "direction": "long", "grade": "A+"})
    
    # === 看跌吞没 (Bearish Engulfing) ===
    if is_green(o2, c2) and is_red(o3, c3) and c3 < o2 and o3 > c2:
        patterns.append({"name": "Bearish_Engulfing", "direction": "short", "grade": "A+"})
    
    # === 晨星 (Morning Star) — 需要第4根确认 ===
    if len(klines) >= 4 and is_red(o2, c2) and b2 > total_range * 0.1:
        k4 = klines[-4]
        o4, c4 = float(k4[1]), float(k4[4])
        if b3 < b2 * 0.3:  # 星线小实体
            if is_green(o4, c4):  # 确认阳线
                patterns.append({"name": "Morning_Star", "direction": "long", "grade": "A+"})
    
    # === 黄昏星 (Evening Star) ===
    if len(klines) >= 4 and is_green(o2, c2) and b2 > total_range * 0.1:
        k4 = klines[-4]
        o4, c4 = float(k4[1]), float(k4[4])
        if b3 < b2 * 0.3 and is_red(o4, c4):
            patterns.append({"name": "Evening_Star", "direction": "short", "grade": "A+"})
    
    # === 穿刺线 (Piercing) — 看涨 ===
    if is_red(o2, c2) and is_green(o3, c3) and o3 < l2 and c3 > (o2 + c2) / 2 and c3 < o2:
        patterns.append({"name": "Piercing", "direction": "long", "grade": "A"})
    
    # === 乌云盖顶 (Dark_Cloud_Cover) — 看跌 ===
    if is_green(o2, c2) and is_red(o3, c3) and o3 > h2 and c3 < (o2 + c2) / 2 and c3 > o2:
        patterns.append({"name": "Dark_Cloud_Cover", "direction": "short", "grade": "A"})
    
    # === 十字星 (Doji) ===
    if b3 < total_range * 0.1 and total_range > 0:
        if ls3 > b3 * 2 and us3 < b3 * 0.3:
            patterns.append({"name": "Dragonfly_Doji", "direction": "long", "grade": "B+"})
        elif us3 > b3 * 2 and ls3 < b3 * 0.3:
            patterns.append({"name": "Gravestone_Doji", "direction": "short", "grade": "B+"})
        else:
            patterns.append({"name": "Doji", "direction": "neutral", "grade": "C"})
    
    # === 三白兵 (Three_White_Soldiers) — 需要3根阳线 ===
    if len(klines) >= 4:
        k4 = klines[-4]
        o4, c4 = float(k4[1]), float(k4[4])
        if (is_green(o4, c4) and is_green(o3, c3) and is_green(o2, c2) and
            c4 > o4 and c3 > c4 and c2 > c3):
            patterns.append({"name": "Three_White_Soldiers", "direction": "long", "grade": "A"})
    
    # === 三黑鸦 (Three_Black_Crows) ===
    if len(klines) >= 4:
        k4 = klines[-4]
        o4, c4 = float(k4[1]), float(k4[4])
        if (is_red(o4, c4) and is_red(o3, c3) and is_red(o2, c2) and
            c4 < o4 and c3 < c4 and c2 < c3):
            patterns.append({"name": "Three_Black_Crows", "direction": "short", "grade": "A"})
    
    return patterns


def v8_pattern_score(patterns_1h: list, patterns_4h: list, direction: str) -> int:
    """
    根据识别的K线形态计算形态评分(0-30分)
    用于 v8_signal_quality() 中的形态维度
    
    Args:
        patterns_1h: 1h框架识别出的形态列表
        patterns_4h: 4h框架识别出的形态列表
        direction: 交易方向 "long" / "short"
    
    Returns:
        0-30分
    """
    from config import V8_PATTERN_WEIGHTS
    
    score = 10  # 基础分
    all_patterns = patterns_1h + patterns_4h
    
    for p in all_patterns:
        w = V8_PATTERN_WEIGHTS.get(p["grade"], 0.5)
        if p["direction"] == direction:
            score += w * 10  # 同向加分
        elif p["direction"] == "neutral" and direction in ("long", "short"):
            score += 3     # 中性偏正向
        else:
            score -= w * 5  # 反向扣分
    
    # 跨框架确认加分
    for p1 in patterns_1h:
        for p4 in patterns_4h:
            if (p1["direction"] == p4["direction"] == direction and
                p1["grade"] in ("A+", "A") and p4["grade"] in ("A+", "A")):
                score += 10  # 跨框架强信号确认
    # 如果有跨框架同向任何形态
    if any(p["direction"] == direction for p in patterns_1h) and \
       any(p["direction"] == direction for p in patterns_4h):
        score += 5
    
    return max(0, min(30, score))


def env_score(cand):
    """
    环境评分 (v6: 多维评分，满分12分，最低-4分)
    维度:
    - BTC方向(2分)       — +2方向一致, -2严重反向
    - FGI情绪(1分)
    - 成交量/流动性(1分)
    - 信号强度(1-2分)
    - EMA趋势(1分)
    - RSI极值(1分)
    - OI确认(1分)
    - 聪明钱信号(1分)    — intel_smart_money_confirm
    - 宏观环境(1分)      — intel_macro_score
    - 多时间框架趋势(1分) — 1h/4h趋势一致性
    """
    score = 0
    details = []
    btc = get_btc_trend()
    btc_chg = btc.get("change_pct", 0)
    fgi = get_fear_greed()
    d = cand["direction"]
    
    # 1. BTC方向 (2分) — +2方向一致, -2严重反向
    if d == "long":
        if btc_chg > -1:
            score += 2; details.append(f"BTC{btc_chg:+.1f}%+2")
        elif btc_chg > -3:
            score += 1; details.append(f"BTC{btc_chg:+.1f}%+1")
        elif btc_chg < -6:
            score -= 2; details.append(f"BTC{btc_chg:+.1f}%危险-2")
        else:
            details.append(f"BTC{btc_chg:+.1f}%")
    elif d == "short":
        if btc_chg < 1:
            score += 2; details.append(f"BTC{btc_chg:+.1f}%+2")
        elif btc_chg < 3:
            score += 1; details.append(f"BTC{btc_chg:+.1f}%+1")
        elif btc_chg > 6:
            score -= 2; details.append(f"BTC{btc_chg:+.1f}%危险-2")
        else:
            details.append(f"BTC{btc_chg:+.1f}%")
    
    # 2. FGI (1分)
    if d == "long" and fgi <= 30: score += 1; details.append(f"FGI={fgi}恐惧+1")
    elif d == "long" and fgi >= 70: score -= 1; details.append(f"FGI={fgi}贪婪-1")
    elif d == "short" and fgi >= 70: score += 1; details.append(f"FGI={fgi}贪婪+1")
    elif d == "short" and fgi <= 30: score -= 1; details.append(f"FGI={fgi}恐惧-1")
    else: details.append(f"FGI={fgi}")
    
    # 3. 成交量/流动性 (1分)
    vol = cand.get("vol", 0)
    if vol > 200_000_000: score += 1; details.append("量大+1")
    elif vol > 100_000_000: details.append("量中")
    elif vol < 50_000_000: score -= 1; details.append("量小-1")
    else: details.append("量一般")
    
    # 4. 信号强度 (1-2分)
    if cand["strength"] == "S": score += 2; details.append("S级+2")
    elif cand["strength"] == "A": score += 1; details.append("A级+1")
    
    # 5. EMA趋势 (1分)
    trend = cand.get("ema_trend", "neutral")
    if d == "long" and trend == "up": score += 1; details.append("EMA↑+1")
    elif d == "short" and trend == "down": score += 1; details.append("EMA↓+1")
    
    # 6. RSI极值 (1分)
    rsi = cand.get("rsi", 50)
    if d == "long" and rsi < 35: score += 1; details.append(f"RSI={rsi:.0f}超卖+1")
    elif d == "short" and rsi > 65: score += 1; details.append(f"RSI={rsi:.0f}超买+1")
    
    # 7. OI确认 (1分) — 同方向OI增加 = 大资金在推
    try:
        oi_hist = get_oi_history(cand["symbol"], "1h", 5)
        if oi_hist and len(oi_hist) >= 2:
            oi_first = float(oi_hist[0].get("sumOpenInterest", 0))
            oi_last = float(oi_hist[-1].get("sumOpenInterest", 0))
            if oi_first > 0:
                oi_chg = (oi_last - oi_first) / oi_first * 100
                if oi_chg > OI_SURGE_PCT:
                    score += 1; details.append(f"OI↑{oi_chg:.0f}%+1")
                elif oi_chg < -OI_SURGE_PCT:
                    details.append(f"OI↓{oi_chg:.0f}%")
    except Exception:
        pass
    
    # 8. 聪明钱信号 (1分) — intel_smart_money_confirm
    try:
        sm_score = intel_smart_money_confirm(cand)
        if sm_score > 0:
            score += sm_score; details.append("聪明钱+1")
    except Exception:
        pass
    
    # 9. 宏观环境 (1分) — intel_macro_score
    try:
        macro = intel_macro_score()
        if macro > 0:
            score += macro; details.append("宏观+1")
    except Exception:
        pass
    
    # 10. 多时间框架趋势 (1分) — 1h/4h趋势一致性
    try:
        tech = cand.get("tech", {})
        # 尝试从技术指标获取多框架趋势
        ema_4h = tech.get("ema_trend_4h", tech.get("trend_4h", ""))
        ema_1h = tech.get("ema_trend_1h", tech.get("ema_trend", ""))
        if not ema_4h or not ema_1h:
            # 退而求其次：用同一趋势但检查趋势强度
            trend_rsi_aligned = (d == "long" and trend == "up" and rsi < 55) or \
                                (d == "short" and trend == "down" and rsi > 45)
            if trend_rsi_aligned:
                score += 1; details.append("多框架一致+1")
        elif d == "long" and ema_1h == "up" and ema_4h == "up":
            score += 1; details.append("1h/4h↑+1")
        elif d == "short" and ema_1h == "down" and ema_4h == "down":
            score += 1; details.append("1h/4h↓+1")
        else:
            details.append("框架分歧")
    except Exception:
        pass
    
    return score, " | ".join(details)


def check_daily_loss(data):
    """检查当日累计亏损 (v3: 保留但默认不限制)"""
    # v3: 每日亏损保护已移除，此函数保留兼容但永远返回True
    daily_pnl = 0
    for t in data["trades"]:
        if t["status"] == "closed" and t.get("pnl_usd") is not None:
            if t.get("exit_time", "").startswith(datetime.now(TZ_UTC8).strftime("%Y-%m-%d")):
                daily_pnl += t["pnl_usd"]
    return True, daily_pnl


# ═══════════════════════════════════════════
# v5: 移动止盈 + 时间止损
# ═══════════════════════════════════════════

def check_trailing_stop(trade, price):
    """
    移动止盈检查
    逻辑: 盈利达TRAILING_TP_TRIGGER后，从最高/最低点回撤TRAILING_TP_STEP即平仓
    """
    if not TRAILING_TP_ENABLED:
        return None
    
    entry = trade["entry_price"]
    d = trade["direction"]
    
    if d == "long":
        pnl_pct = (price - entry) / entry
    else:
        pnl_pct = (entry - price) / entry
    
    # 还没到启动阈值
    if pnl_pct < TRAILING_TP_TRIGGER:
        return None
    
    # 获取或初始化跟踪的最高/最低价
    if "trail_high" not in trade:
        trade["trail_high"] = price
        trade["trail_low"] = price
        return None
    
    # 更新极值
    if price > trade["trail_high"]:
        trade["trail_high"] = price
    if price < trade["trail_low"]:
        trade["trail_low"] = price
    
    # 检查回撤
    if d == "long":
        pullback = (trade["trail_high"] - price) / trade["trail_high"]
        if pullback >= TRAILING_TP_STEP:
            return f"移动止盈(高点回撤{pullback*100:.1f}%)"
    else:
        bounce = (price - trade["trail_low"]) / trade["trail_low"]
        if bounce >= TRAILING_TP_STEP:
            return f"移动止盈(低点反弹{bounce*100:.1f}%)"
    
    return None


def check_time_stop(trade, now):
    """时间止损 (v3: 已禁用，返回None)"""
    # v3: 移除MAX_HOLD和TIME_DECAY — 回测证明无效果
    return None, None


# === 主流程 ===
def main():
    data = load_json(TRADES_FILE, {"initial_balance": INITIAL_BALANCE, "trades": []})
    state = load_json(STATE_FILE, {"last_opens": {}, "signals_seen": {}})
    
    open_positions = [t for t in data["trades"] if t["status"] == "open"]
    now = datetime.now(TZ_UTC8)
    
    # 0. 检查API连通性
    avail_bal, total_bal = get_api_usdt_balance()
    if avail_bal is None:
        log("⚠️ 币安API未连通，本次跳过下单（仅监控）")
        api_ok = False
    else:
        log(f"币安余额: 可用${avail_bal:.2f} 总${total_bal:.2f}")
        api_ok = True
    
    # 1. 监控持仓 → 检查止损/止盈/移动止盈/时间止损
    for trade in open_positions:
        price = get_price(trade["symbol"])
        if price <= 0:
            continue
        
        sl = trade["stop_loss"]
        tp = trade["take_profit"]
        triggered = None
        sl_adjusted = None
        
        # v4: 入场宽限期 — 入场后GRACE_PERIOD_HOURS内不扫止损
        try:
            from config import GRACE_PERIOD_HOURS
            entry_dt = datetime.fromisoformat(trade["entry_time"])
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=TZ_UTC8)
            in_grace = (now - entry_dt).total_seconds() < GRACE_PERIOD_HOURS * 3600
        except Exception:
            in_grace = False
        
        # 常规止损止盈 (v4: 宽限期内不触发止损，止盈正常触发)
        if trade["direction"] == "long":
            if in_grace:
                if price >= tp: triggered = "止盈"
                # 宽限期内止损仅记录，不触发
                if price <= sl:
                    log(f"  #{trade['id']} 宽限期内价格触及SL({sl})但不平仓")
            else:
                if price <= sl: triggered = "止损"
                elif price >= tp: triggered = "止盈"
        else:
            if in_grace:
                if price <= tp: triggered = "止盈"
                if price >= sl:
                    log(f"  #{trade['id']} 宽限期内价格触及SL({sl})但不平仓")
            else:
                if price >= sl: triggered = "止损"
                elif price <= tp: triggered = "止盈"
        
        # v5: 移动止盈检查
        if not triggered:
            trail_result = check_trailing_stop(trade, price)
            if trail_result:
                triggered = trail_result
        
        # v3: 移除时间止损(MAX_HOLD/TIME_DECAY) — 无回测支撑，强制平仓反而干扰
        
        if triggered:
            entry = trade["entry_price"]
            lev = trade.get("leverage", LEVERAGE)
            pos_usd = trade.get("position_usd", 0)
            
            if trade["direction"] == "long":
                pnl_pct = (price - entry) / entry * 100 * lev
            else:
                pnl_pct = (entry - price) / entry * 100 * lev
            
            trade["exit_price"] = price
            trade["exit_time"] = now_str()
            trade["exit_reason"] = triggered
            trade["pnl_pct"] = round(pnl_pct, 2)
            trade["pnl_usd"] = round(pnl_pct / 100 * pos_usd, 4)
            trade["status"] = "closed"
            
            # 真实平仓
            if api_ok and trade.get("quantity"):
                close_result = execute_close(trade["symbol"], trade["direction"], trade["quantity"])
                if close_result["success"]:
                    trade["exit_price"] = close_result.get("fill_price", price)
                    log(f"真实平仓成功 @{trade['exit_price']}")
                else:
                    notify(f"⚠️ 真实平仓失败 #{trade['id']} {trade['symbol']}: {close_result['error']}")
                    log(f"真实平仓失败: {close_result['error']}")
            
            notify(format_close_message(trade))
            notify(format_review_message(trade))
            # v8: 同步到复盘数据库
            try:
                sync_trade(trade)
            except Exception:
                pass
            
            d_cn = "多" if trade["direction"] == "long" else "空"
            log(f"平仓 #{trade['id']} {trade['symbol']} {d_cn} {triggered} {trade['pnl_usd']:+.2f}U")
    
    save_json(TRADES_FILE, data)
    
    # 重新计算持仓
    open_positions = [t for t in data["trades"] if t["status"] == "open"]
    
    if len(open_positions) >= MAX_OPEN_POSITIONS:
        log(f"满仓 {len(open_positions)}/{MAX_OPEN_POSITIONS}")
        print(f"📊 满仓 {len(open_positions)}/{MAX_OPEN_POSITIONS}")
        for t in open_positions:
            d = "多" if t["direction"] == "long" else "空"
            cur = get_price(t["symbol"])
            raw = (cur - t["entry_price"]) / t["entry_price"] * 100
            if t["direction"] == "short": raw = -raw
            lev_pnl = raw * t.get("leverage", LEVERAGE)
            print(f"  #{t['id']} {t['symbol']} {d} | 浮动{lev_pnl:+.1f}% | SL:{t['stop_loss']} TP:{t['take_profit']}")
        return
    
    # v3: 移除每日亏损保护 — Kelly仓位管理已内置风险控制
    # 旧逻辑: can_trade, daily_pnl = check_daily_loss(data); if not can_trade: return
    
    # 2. 扫描新信号
    open_symbols = set(t["symbol"] for t in open_positions)
    
    # v4: 更新和加载动态黑名单
    blacklist = update_dynamic_blacklist(data)
    if blacklist:
        log(f"⚠️ 动态黑名单({len(blacklist)}): {', '.join(list(blacklist)[:5])}")
    
    candidates = quick_scan(open_symbols, state.get("last_opens", {}))
    
    # v4: 过滤黑名单币种
    if blacklist:
        before_count = len(candidates)
        candidates = [c for c in candidates if c["symbol"] not in blacklist]
        if before_count != len(candidates):
            log(f"  黑名单过滤: {before_count}→{len(candidates)}")
    
    if not candidates:
        log("无信号")
        print(f"📊 持仓{len(open_positions)}/{MAX_OPEN_POSITIONS} | 无新信号 | 余额${get_local_balance(data):.2f}")
        return
    
    # 按费率极端程度排序
    candidates.sort(key=lambda x: abs(x.get("fr", 0)), reverse=True)
    
    for cand in candidates[:15]:  # v3: 放宽检查数量 10→15
        verified = deep_check(cand)
        if not verified:
            continue
        # v3: 移除B级信号封杀 — 交给信号质量评分系统自然过滤
        
        if V8_ENABLED:
            # === v8 入场条件 (v3: 两要素: 信号质量 + EMA趋势) ===
            tech = verified.get("tech", {})
            
            # 条件1: 信号质量 ≥ V8_SIGNAL_QUALITY_MIN (v3: 默认55)
            signal_quality = v8_signal_quality(verified, tech)
            # v3: 逆势/RSI极端降低信号质量分(软惩罚)
            if not verified.get("trend_aligned", True):
                signal_quality -= 15  # 逆势扣15分
            if verified.get("rsi_extreme", False):
                signal_quality -= 10  # RSI极端扣10分
            if signal_quality < V8_SIGNAL_QUALITY_MIN:
                log(f"  v8信号质量{signal_quality:.0f}<{V8_SIGNAL_QUALITY_MIN}，跳过")
                continue
            
            # 条件2: RR比 ≥ V8_RR_MIN (v3: 默认1.3)
            sl_pct = verified.get("sl_pct", DEFAULT_SL_PCT)
            tp_pct = verified.get("tp_pct", DEFAULT_TP_PCT)
            rr = tp_pct / sl_pct if sl_pct > 0 else 0
            if rr < V8_RR_MIN:
                log(f"  v8 RR比{rr:.1f}<{V8_RR_MIN}，跳过")
                continue
            
            # v3: 移除宏观评分30-70区间门槛
            # 改为EMA趋势方向确认（与backtest_v8一致）
            # 获取多时间框架数据
            btc_trend_data = get_btc_trend()
            weighted_score, scores_detail, score_detail = v8_calc_weights(verified, tech, btc_trend_data)
            macro_normalized = max(0, min(100, (weighted_score + 100) / 2))
            
            # v4: MTF一致性门槛 — 替代简单EMA趋势检查
            tech_v8 = verified.get("tech_v8", {})
            tf_1h = tech_v8.get("tf_1h", {})
            tf_4h = tech_v8.get("tf_4h", {})
            trend_1h = tf_1h.get("trend", "neutral")
            trend_4h = tf_4h.get("trend", "neutral")
            direction = verified["direction"]
            
            # v4: 计算MTF一致性得分(0-7)
            mtf_agree = 0
            if direction == "long":
                if trend_1h == "up": mtf_agree += 2
                elif trend_1h == "neutral": mtf_agree += 1
                if trend_4h == "up": mtf_agree += 2
                elif trend_4h == "neutral": mtf_agree += 1
            else:  # short
                if trend_1h == "down": mtf_agree += 2
                elif trend_1h == "neutral": mtf_agree += 1
                if trend_4h == "down": mtf_agree += 2
                elif trend_4h == "neutral": mtf_agree += 1
            # K线形态确认+2
            patterns_1h = verified.get("patterns_1h", [])
            patterns_4h = verified.get("patterns_4h", [])
            if any(p["direction"] == direction for p in patterns_1h): mtf_agree += 1
            if any(p["direction"] == direction for p in patterns_4h): mtf_agree += 1
            # OI方向确认+1
            try:
                oi_hist = get_oi_history(verified["symbol"], "1h", 3)
                if oi_hist and len(oi_hist) >= 2:
                    oi_chg = (float(oi_hist[-1].get("sumOpenInterestValue", 0)) -
                              float(oi_hist[-2].get("sumOpenInterestValue", 0)))
                    if (direction == "long" and oi_chg > 0) or (direction == "short" and oi_chg < 0):
                        mtf_agree += 1
            except Exception:
                pass
            
            verified["mtf_agree"] = mtf_agree
            
            # v4: MTF一致性门槛
            try:
                from config import MTF_AGREE_MIN
                if mtf_agree < MTF_AGREE_MIN:
                    log(f"  v8 MTF一致性{mtf_agree}<{MTF_AGREE_MIN}，跳过")
                    continue
            except ImportError:
                pass
            
            trend_ok = (direction == "long" and trend_1h != "down" and trend_4h != "down") or \
                       (direction == "short" and trend_1h != "up" and trend_4h != "up")
            if not trend_ok:
                log(f"  v8 EMA趋势不符(1h={trend_1h},4h={trend_4h})，跳过")
                continue
            
            log(f"  ✅ v8通过: 信号={signal_quality:.0f}/100 RR={rr:.1f} 宏观={macro_normalized:.0f} MTF={mtf_agree} 趋势=✓")
            log(f"  v8加权评分: {score_detail}")
            
            # v11: v8_score最低门槛
            try:
                from config import V11_MIN_V8_SCORE
                v8_score = int(weighted_score)
                if v8_score < V11_MIN_V8_SCORE:
                    log(f"  v11 v8_score={v8_score}<{V11_MIN_V8_SCORE}，跳过")
                    continue
            except ImportError:
                pass
            
            # v8 Kelly动态仓位
            balance = get_local_balance(data)
            pos_usd = v8_kelly_position(balance, V8_DEFAULT_WIN_RATE, rr, signal_quality, macro_normalized)
            log(f"  v8 Kelly仓位: ${pos_usd:.2f} ({pos_usd/balance*100:.1f}%)")
            
            # v4: 做空高v8_score减仓(数据:32笔41%胜率亏$436)
            try:
                from config import (BLACKLIST_SHORT_V8_SCORE_THRESHOLD, 
                                  BLACKLIST_SHORT_POSITION_FACTOR)
                v8_score_int = int(weighted_score)
                if (direction == "short" and 
                    v8_score_int >= BLACKLIST_SHORT_V8_SCORE_THRESHOLD):
                    original_pos = pos_usd
                    pos_usd = round(pos_usd * BLACKLIST_SHORT_POSITION_FACTOR, 2)
                    log(f"  v4 做空v8={v8_score_int}≥{BLACKLIST_SHORT_V8_SCORE_THRESHOLD}减仓: ${original_pos:.0f}→${pos_usd:.0f}")
            except ImportError:
                v8_score_int = int(weighted_score)
            
            # ═══ V11I: 硬过滤 + 仓位调整 (基于v11g修复3处bug) ═══
            # 回测1000U/1年: 525笔/63.8%/+$913(+91%)/DD6.1%/月10/13盈利
            try:
                from config import (V11I_SHORT_V8_THRESHOLD, V11I_SHORT_V8_MULT,
                    V11I_V8_LOW_THRESHOLD, V11I_V8_LOW_MULT,
                    V11I_V8_HIGH_THRESHOLD, V11I_V8_HIGH_MULT_LONG, V11I_V8_HIGH_MULT_SHORT,
                    V11I_RSI_WEAK, V11I_RSI_WEAK_MULT,
                    V11I_RSI_MID_LOW, V11I_RSI_MID_HIGH, V11I_RSI_MID_MULT,
                    V11I_RSI_STRONG_LOW, V11I_RSI_STRONG_HIGH, V11I_RSI_STRONG_MULT,
                    V11I_RSI_VERY_STRONG, V11I_RSI_VERY_STRONG_MULT,
                    V11I_SL_MEDIUM_LOW, V11I_SL_MEDIUM_HIGH, V11I_SL_MEDIUM_MULT,
                    V11I_SL_WIDE_LOW, V11I_SL_WIDE_HIGH, V11I_SL_WIDE_MULT,
                    V11I_MAX_SL_PCT, V11I_MAX_ATR_PCT,
                    V11I_FILTER_V8_RSI,
                    V11I_CONSEC_LOSS_THRESHOLD, V11I_CONSEC_LOSS_MULT)
                
                v8_score_int = int(weighted_score)
                sl_pct_val = verified.get("sl_pct", DEFAULT_SL_PCT) * 100  # 转百分比
                rsi_val = verified.get("rsi") or (verified.get("tech_snapshot", {}) or {}).get("rsi")
                atr_pct_val = verified.get("atr_pct") or (verified.get("tech_snapshot", {}) or {}).get("atr_pct")
                
                # ── V11I硬过滤 ──
                # SL>10%跳过
                if sl_pct_val > V11I_MAX_SL_PCT:
                    log(f"  v11i SL={sl_pct_val:.1f}%>{V11I_MAX_SL_PCT}%，跳过")
                    continue
                
                # ATR>5%跳过
                if atr_pct_val is not None and atr_pct_val * 100 > V11I_MAX_ATR_PCT:
                    log(f"  v11i ATR={atr_pct_val*100:.1f}%>{V11I_MAX_ATR_PCT}%，跳过")
                    continue
                
                # V8≥6.5 + RSI<55 做多跳过
                if V11I_FILTER_V8_RSI and direction == "long":
                    if v8_score_int >= V11I_V8_HIGH_THRESHOLD and rsi_val is not None and rsi_val < 55:
                        log(f"  v11i V8={v8_score_int}≥{V11I_V8_HIGH_THRESHOLD}+RSI={rsi_val:.0f}<55做多跳过")
                        continue
                
                # ── V11I仓位调整 ──
                v11i_mult = 1.0
                
                # 0. 做空额外惩罚: V8≥5减半
                if direction == "short" and v8_score_int >= V11I_SHORT_V8_THRESHOLD:
                    v11i_mult *= V11I_SHORT_V8_MULT
                    log(f"  v11i 做空V8={v8_score_int}≥{V11I_SHORT_V8_THRESHOLD} 减半×{V11I_SHORT_V8_MULT}")
                
                # 1. V8反转: V8≤4加仓, V8≥6.5减仓(多空不同)
                if v8_score_int <= V11I_V8_LOW_THRESHOLD:
                    v11i_mult *= V11I_V8_LOW_MULT
                elif v8_score_int >= V11I_V8_HIGH_THRESHOLD:
                    mult = V11I_V8_HIGH_MULT_LONG if direction == "long" else V11I_V8_HIGH_MULT_SHORT
                    v11i_mult *= mult
                
                # 2. RSI区间 (仅做多) — v11i新增55-60区间
                if direction == "long" and rsi_val is not None:
                    if rsi_val < V11I_RSI_WEAK:
                        v11i_mult *= V11I_RSI_WEAK_MULT
                    elif V11I_RSI_MID_LOW <= rsi_val < V11I_RSI_MID_HIGH:
                        v11i_mult *= V11I_RSI_MID_MULT
                    elif V11I_RSI_STRONG_LOW <= rsi_val <= V11I_RSI_STRONG_HIGH:
                        v11i_mult *= V11I_RSI_STRONG_MULT
                    elif rsi_val >= V11I_RSI_VERY_STRONG:
                        v11i_mult *= V11I_RSI_VERY_STRONG_MULT
                
                # 3. SL%区间 — v11i修复: SL≤4%不减仓
                if V11I_SL_MEDIUM_LOW <= sl_pct_val <= V11I_SL_MEDIUM_HIGH:
                    v11i_mult *= V11I_SL_MEDIUM_MULT
                elif V11I_SL_WIDE_LOW <= sl_pct_val <= V11I_SL_WIDE_HIGH:
                    v11i_mult *= V11I_SL_WIDE_MULT
                
                # 4. 连续亏损冷却
                closed_trades = [t for t in data["trades"] if t["status"] == "closed"]
                consec_losses = 0
                for ct in reversed(closed_trades):
                    if (ct.get("pnl_usd") or 0) < 0:
                        consec_losses += 1
                    else:
                        break
                if consec_losses >= V11I_CONSEC_LOSS_THRESHOLD:
                    v11i_mult *= V11I_CONSEC_LOSS_MULT
                    log(f"  v11i 连续亏损{consec_losses}笔≥{V11I_CONSEC_LOSS_THRESHOLD} 冷却×{V11I_CONSEC_LOSS_MULT}")
                
                # 应用V11I仓位调整
                if v11i_mult != 1.0:
                    original_pos = pos_usd
                    pos_usd = round(pos_usd * v11i_mult, 2)
                    log(f"  v11i 仓位调整 ×{v11i_mult:.3f}: ${original_pos:.0f}→${pos_usd:.0f} (V8={v8_score_int} RSI={rsi_val} SL={sl_pct_val:.1f}%)")
                
            except ImportError as e:
                log(f"  v11i 参数未配置({e})，跳过V11I调整")
            except Exception as e:
                log(f"  v11i 异常: {e}")
        else:
            score, env_detail = env_score(verified)
            log(f"候选 {verified['symbol']} [{verified['strength']}] {score}/12 | {env_detail}")
            
            if score < MIN_ENV_SCORE:
                log(f"  评分{score}<{MIN_ENV_SCORE}，跳过")
                continue
            
            # v3: RR比检查(使用V8_RR_MIN)
            sl_pct = verified.get("sl_pct", DEFAULT_SL_PCT)
            tp_pct = verified.get("tp_pct", DEFAULT_TP_PCT)
            rr = tp_pct / sl_pct if sl_pct > 0 else 0
            if rr < V8_RR_MIN:
                log(f"  RR比{rr:.1f}<{V8_RR_MIN}，跳过")
                continue
            
            # 开仓参数
            balance = get_local_balance(data)
            pos_usd = round(balance * POSITION_PCT / 100, 4)
        price = verified["price"]
        
        if verified["direction"] == "long":
            sl = round(price * (1 - sl_pct), 6)
            tp = round(price * (1 + tp_pct), 6)
        else:
            sl = round(price * (1 + sl_pct), 6)
            tp = round(price * (1 - tp_pct), 6)
        
        trade = {
            "id": next_id(data),
            "symbol": verified["symbol"],
            "direction": verified["direction"],
            "leverage": LEVERAGE,
            "position_pct": POSITION_PCT,
            "position_usd": pos_usd,
            "notional_usd": round(pos_usd * LEVERAGE, 4),
            "entry_price": price,
            "stop_loss": sl,
            "take_profit": tp,
            "entry_time": now_str(),
            "exit_price": None, "exit_time": None, "exit_reason": None,
            "pnl_pct": None, "pnl_usd": None,
            "status": "open",
            "signal_type": verified["type"],
            "signal_strength": verified["strength"],
            "signal_reason": f"[{verified['strength']}] {verified.get('reason', '')}",
            "signal_sl_pct": round(sl_pct * 100, 2),
            "signal_tp_pct": round(tp_pct * 100, 2),
            "signal_rr": round(rr, 2),
            # v5新增: 技术指标快照
            "tech_snapshot": {
                "ema_trend": verified.get("ema_trend", "N/A"),
                "rsi": verified.get("rsi", 50),
                "atr_pct": verified.get("atr_pct", 0),
            },
            "post_review": None,
            "quantity": None,
            "binance_order_id": None,
            # 移动止盈跟踪
            "trail_high": None,
            "trail_low": None,
        }
        
        # 真实下单
        if api_ok:
            open_result = execute_open(verified["symbol"], verified["direction"], pos_usd, LEVERAGE)
            if open_result["success"]:
                trade["quantity"] = open_result["quantity"]
                trade["binance_order_id"] = open_result["order"].get("orderId")
                trade["entry_price"] = open_result.get("fill_price", price)
                # 用实际成交价重算SL/TP
                if verified["direction"] == "long":
                    trade["stop_loss"] = round(trade["entry_price"] * (1 - sl_pct), 6)
                    trade["take_profit"] = round(trade["entry_price"] * (1 + tp_pct), 6)
                else:
                    trade["stop_loss"] = round(trade["entry_price"] * (1 + sl_pct), 6)
                    trade["take_profit"] = round(trade["entry_price"] * (1 - tp_pct), 6)
                log(f"✅ 真实下单成功 orderId={trade['binance_order_id']}")
            else:
                notify(f"⚠️ 真实下单失败 {verified['symbol']}: {open_result['error']}")
                log(f"❌ 真实下单失败: {open_result['error']}")
                trade["quantity"] = 0
                trade["_simulated"] = True
        else:
            trade["_simulated"] = True
            log(f"API未连通，仅模拟开仓")
        
        data["trades"].append(trade)
        # v8: 同步到复盘数据库
        try:
            trade["v8_signal_quality"] = signal_quality if V8_ENABLED else None
            trade["v8_macro_score"] = macro_normalized if V8_ENABLED else None
            trade["mtf_agree"] = verified.get("mtf_agree") if V8_ENABLED else None
            trade["v8_weighted_score"] = int(weighted_score) if V8_ENABLED else None
            sync_trade(trade)
        except Exception:
            pass
        state["last_opens"][verified["symbol"]] = now_str()
        save_json(TRADES_FILE, data)
        save_json(STATE_FILE, state)
        
        # 在env_detail尾部附加宏观快照
        macro_snapshot = intel_quick_macro()
        notify_detail = env_detail if not V8_ENABLED else score_detail
        notify(format_open_message(trade, notify_detail + f"\n📡 {macro_snapshot}"))
        
        d_cn = "多" if verified["direction"] == "long" else "空"
        log(f"开仓 #{trade['id']} {verified['symbol']} {d_cn} @ {trade['entry_price']} RR={rr:.1f}")
        break
    
    # 输出状态摘要
    closed = [t for t in data["trades"] if t["status"] == "closed"]
    total_pnl = sum(t.get("pnl_usd", 0) for t in closed)
    wins = sum(1 for t in closed if (t.get("pnl_usd", 0) or 0) > 0)
    sim_tag = " (模拟)" if not api_ok else ""
    wr = f"{wins/len(closed)*100:.0f}%" if closed else "N/A"
    print(f"📊 余额${get_local_balance(data):.2f}{sim_tag} | 持仓{len(open_positions)+1} | 已平{len(closed)}W{wins}L{len(closed)-wins} | 胜率{wr} | 总{total_pnl:+.2f}U")


if __name__ == "__main__":
    main()
