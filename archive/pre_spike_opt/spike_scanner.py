#!/usr/bin/env python3
"""
15分钟异动(Spike)信号扫描器
移植自 backtest_v10.py 的 spike 信号逻辑
这是回测中唯一盈利的信号类型（v11g: 1274笔, WR 62.7%, +$6,845）

K线格式: [timestamp, open, high, low, close, volume, close_time, quote_vol, trades, ...]
"""

import sys
sys.path.insert(0, '/home/ubuntu/.hermes/trading')

from datetime import datetime, timezone, timedelta
from config import *
from binance_api import get_klines, get_qualified_symbols, get_funding_rates, now_str

TZ_UTC8 = timezone(timedelta(hours=8))

# === Spike 信号参数 (来自 backtest_v10.py) ===
SPIKE_THRESHOLD = 0.01       # 15m K线涨跌幅阈值 1%
SPIKE_MIN_ATR = 0.005        # 最小ATR 0.5%
SPIKE_COOLDOWN_SECS = 3600   # 同向冷却1小时


def calc_atr_pct(klines_15m, period=14):
    """从15m K线数组计算ATR%"""
    if len(klines_15m) < period + 1:
        return 0
    atrs = []
    for i in range(max(1, len(klines_15m) - period), len(klines_15m)):
        hi = float(klines_15m[i][2])
        lo = float(klines_15m[i][3])
        prev_c = float(klines_15m[i - 1][4])
        tr = max(hi - lo, abs(hi - prev_c), abs(lo - prev_c))
        atrs.append(tr)
    if not atrs:
        return 0
    atr = sum(atrs) / len(atrs)
    close = float(klines_15m[-1][4])
    return atr / close if close > 0 else 0


def scan_spike_signals(open_symbols, cooldowns, spike_cooldowns=None):
    """
    扫描15分钟异动信号 — 回测验证的主力盈利信号
    
    逻辑: 单根15m K线涨跌幅 > 1% + ATR ≥ 0.5%
    
    Returns: list of candidate dicts (与quick_scan格式一致)
    """
    if spike_cooldowns is None:
        spike_cooldowns = {}
    
    tickers = get_qualified_symbols()
    funding = get_funding_rates()
    now = datetime.now(TZ_UTC8)
    candidates = []
    
    for t in tickers:
        sym = t["symbol"]
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
            except:
                pass
        
        try:
            # 获取30根15m K线
            klines = get_klines(sym, "15m", 30)
            if not klines or len(klines) < 15:
                continue
            
            # 最后一根已完成的15m K线（倒数第2根，最后一根可能在形成中）
            last_closed = klines[-2]
            o = float(last_closed[1])
            c = float(last_closed[4])
            h = float(last_closed[2])
            l = float(last_closed[3])
            
            # 15m K线涨跌幅
            chg_pct_15m = (c - o) / o if o > 0 else 0
            
            # 异动阈值
            if abs(chg_pct_15m) < SPIKE_THRESHOLD:
                continue
            
            # ATR过滤
            atr_pct = calc_atr_pct(klines, 14)
            if atr_pct < SPIKE_MIN_ATR:
                continue
            
            # 异动冷却检查
            direction = "long" if chg_pct_15m > 0 else "short"
            sc = spike_cooldowns.get(sym, {})
            last_spike_time = sc.get(direction)
            if last_spike_time:
                try:
                    lt = datetime.fromisoformat(last_spike_time)
                    if lt.tzinfo is None:
                        lt = lt.replace(tzinfo=TZ_UTC8)
                    if (now - lt).total_seconds() < SPIKE_COOLDOWN_SECS:
                        continue
                except:
                    pass
            
            # 信号强度
            abs_chg = abs(chg_pct_15m)
            strength = "S" if abs_chg >= 0.03 else "A" if abs_chg >= 0.02 else "B"
            
            # 动态止损: 基于ATR
            sl_pct = max(atr_pct * 1.5, 0.03)  # 最小3%
            tp_pct = sl_pct * 2.5
            rr = round(tp_pct / sl_pct, 2)
            
            price = float(t.get("lastPrice", 0))
            vol = float(t.get("quoteVolume", 0))
            fr = funding.get(sym, 0)
            chg_24h = float(t.get("priceChangePercent", 0))
            
            candidates.append({
                "symbol": sym,
                "type": "spike",
                "direction": direction,
                "strength": strength,
                "price": price,
                "fr": fr,
                "change": chg_24h,
                "vol": vol,
                "sl_pct": sl_pct,
                "tp_pct": tp_pct,
                "rr": rr,
                "reason": f"15m异动{chg_pct_15m*100:+.1f}% ATR={atr_pct*100:.1f}%",
                "chg_pct_15m": chg_pct_15m,
                "atr_pct": atr_pct,
            })
        except Exception:
            continue
    
    return candidates


if __name__ == "__main__":
    print(f"=== Spike信号扫描测试 [{now_str()}] ===")
    results = scan_spike_signals(set(), {})
    print(f"发现 {len(results)} 个spike信号:")
    for r in results[:15]:
        print(f"  {r['symbol']:20s} {r['direction']:5s} {r['strength']}"
              f" | {r['reason']:35s} | SL={r['sl_pct']*100:.1f}% TP={r['tp_pct']*100:.1f}% RR={r['rr']}")
