#!/usr/bin/env python3
"""精选信号分析 — 深入查看最强候选"""
import sys
sys.path.insert(0, '.')

from binance_api import (
    get_funding_history, get_klines, get_price, get_open_interest,
    get_btc_trend, get_fear_greed, format_usd, get_all_tickers
)
from config import EXTREME_NEG_FUNDING, EXTREME_POS_FUNDING

btc = get_btc_trend()
fgi = get_fear_greed()
print(f"{'='*60}")
print(f"BTC: ${btc['price']:.0f} | 24h: {btc['change_pct']:+.1f}%")
print(f"恐惧贪婪: {fgi}")
print(f"{'='*60}")

# 候选: 按费率极端程度+24h变化综合筛选
candidates = [
    # (symbol, reason, direction)
    ("STEEMUSDT", "极端负费率-1.12% 已反弹+5.8%", "long"),
    ("ANKRUSDT", "极端负费率-0.95% 已反弹+11.1%", "long"),
    ("STORJUSDT", "负费率+暴涨45% 双重信号", "short"),
    ("SLERFUSDT", "极端正费率+5.9% 做空", "short"),
    ("FXSUSDT", "极端正费率+3.75% 做空", "short"),
    ("4USDT", "暴跌27% 超跌反弹?", "long"),
    ("LABUSDT", "暴涨58% 回调做空?", "short"),
    ("FHEUSDT", "暴涨43% 回调做空?", "short"),
]

for symbol, reason, direction in candidates:
    print(f"\n{'─'*50}")
    print(f"📌 {symbol} | {reason}")
    
    # 费率历史(8期)
    fr_hist = get_funding_history(symbol, 8)
    if fr_hist:
        print(f"  费率历史(8期): {[f'{r:+.4f}%' for r in fr_hist]}")
        neg_cnt = sum(1 for r in fr_hist if r < -0.03)
        pos_cnt = sum(1 for r in fr_hist if r > 0.05)
        avg_fr = sum(fr_hist) / len(fr_hist)
        print(f"  均值: {avg_fr:+.4f}% | 负期:{neg_cnt}/8 | 正期:{pos_cnt}/8")
    
    # 近6根1h K线
    klines = get_klines(symbol, "1h", 6)
    if klines and len(klines) >= 2:
        closes = [float(k[4]) for k in klines]
        opens = [float(k[1]) for k in klines]
        vols = [float(k[5]) for k in klines]
        last_chg = (closes[-1] - opens[-1]) / opens[-1] * 100
        trend = "📈" if closes[-1] > closes[0] else "📉"
        print(f"  K线趋势{trend}: 最近1h={last_chg:+.1f}%")
        print(f"  收盘序列: {[f'{c:.6g}' for c in closes]}")
    
    # OI
    oi = get_open_interest(symbol)
    price = get_price(symbol)
    oi_usd = oi * price
    print(f"  OI: {format_usd(oi_usd)} | 价格: {price}")
    
    # 环境评分(简化版)
    score = 0
    notes = []
    
    if direction == "long":
        if btc["change_pct"] > -2:
            score += 1; notes.append("BTC环境OK+1")
        if fgi <= 25:
            score += 1; notes.append("极度恐惧+1")
        if fgi >= 75:
            score -= 1; notes.append("极度贪婪-1")
        if fr_hist and sum(1 for r in fr_hist if r < -0.03) >= 4:
            score += 2; notes.append("连续深负+2")
        elif fr_hist and sum(1 for r in fr_hist if r < -0.03) >= 2:
            score += 1; notes.append("部分深负+1")
    else:
        if btc["change_pct"] < 2:
            score += 1; notes.append("BTC环境OK+1")
        if fgi >= 75:
            score += 1; notes.append("极度贪婪+1")
        if fgi <= 25:
            score -= 1; notes.append("极度恐惧-1")
        if fr_hist and sum(1 for r in fr_hist if r > 0.05) >= 4:
            score += 2; notes.append("连续高正+2")
        elif fr_hist and sum(1 for r in fr_hist if r > 0.05) >= 2:
            score += 1; notes.append("部分高正+1")
    
    if oi_usd > 5_000_000:
        score += 1; notes.append(f"OI关注度高+1")
    
    verdict = "✅可开仓" if score >= 3 else "⚠️观望" if score >= 1 else "❌不合适"
    print(f"  评分: {score}/7 | {' | '.join(notes)}")
    print(f"  判定: {verdict}")

print(f"\n{'='*60}")
print("分析完毕")
