#!/usr/bin/env python3
"""
快速数据采集 — 多线程并发版
用 ThreadPoolExecutor 并发拉取，大幅加速采集
"""
import sys
import json
import time
import sqlite3
import requests
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

FAPI = "https://fapi.binance.com"
TZ_UTC8 = timezone(timedelta(hours=8))
DATA_DIR = Path.home() / ".hermes" / "trading" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "backtest_db.sqlite"

END_TIME = datetime(2026, 5, 8, 23, 59, tzinfo=TZ_UTC8)
START_TIME = END_TIME - timedelta(days=1000)
START_TS = int(START_TIME.timestamp() * 1000)
END_TS = int(END_TIME.timestamp() * 1000)
WARMUP_TS = int((START_TIME - timedelta(days=30)).timestamp() * 1000)
MIN_TS = int(datetime(2023, 1, 1, tzinfo=TZ_UTC8).timestamp() * 1000)

# 线程安全的计数器和锁
lock = threading.Lock()
progress = {}

def api_get(endpoint, params=None, retries=3):
    url = FAPI + endpoint
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                time.sleep(wait)
            else:
                return None
        except Exception as e:
            if attempt == retries - 1:
                return None
            time.sleep(1)
    return None

def setup_database():
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    
    tables = {
        'klines': """CREATE TABLE IF NOT EXISTS klines (
            symbol TEXT, interval TEXT, open_time INTEGER,
            open REAL, high REAL, low REAL, close REAL,
            volume REAL, close_time INTEGER, quote_volume REAL,
            trades INTEGER, taker_buy_volume REAL, taker_buy_quote_volume REAL,
            PRIMARY KEY (symbol, interval, open_time))""",
        'funding_rates': """CREATE TABLE IF NOT EXISTS funding_rates (
            symbol TEXT, funding_time INTEGER, funding_rate REAL,
            mark_price REAL, PRIMARY KEY (symbol, funding_time))""",
        'open_interest': """CREATE TABLE IF NOT EXISTS open_interest (
            symbol TEXT, timestamp INTEGER, sum_open_interest REAL,
            sum_open_interest_value REAL, PRIMARY KEY (symbol, timestamp))""",
        'mark_klines': """CREATE TABLE IF NOT EXISTS mark_klines (
            symbol TEXT, interval TEXT, open_time INTEGER,
            open REAL, high REAL, low REAL, close REAL,
            PRIMARY KEY (symbol, interval, open_time))""",
        'ticker_daily': """CREATE TABLE IF NOT EXISTS ticker_daily (
            symbol TEXT, date TEXT, volume REAL, quote_volume REAL,
            price_change_pct REAL, high REAL, low REAL, trades INTEGER,
            PRIMARY KEY (symbol, date))""",
        'ls_ratio_top': """CREATE TABLE IF NOT EXISTS ls_ratio_top (
            symbol TEXT, timestamp INTEGER, long_short_ratio REAL,
            long_account REAL, short_account REAL,
            PRIMARY KEY (symbol, timestamp))""",
        'ls_ratio_position': """CREATE TABLE IF NOT EXISTS ls_ratio_position (
            symbol TEXT, timestamp INTEGER, long_short_ratio REAL,
            long_account REAL, short_account REAL,
            PRIMARY KEY (symbol, timestamp))""",
        'ls_ratio_global': """CREATE TABLE IF NOT EXISTS ls_ratio_global (
            symbol TEXT, timestamp INTEGER, long_short_ratio REAL,
            long_account REAL, short_account REAL,
            PRIMARY KEY (symbol, timestamp))""",
        'taker_volume_ratio': """CREATE TABLE IF NOT EXISTS taker_volume_ratio (
            symbol TEXT, timestamp INTEGER, buy_sell_ratio REAL,
            buy_vol REAL, sell_vol REAL, PRIMARY KEY (symbol, timestamp))""",
        'symbols': """CREATE TABLE IF NOT EXISTS symbols (
            symbol TEXT PRIMARY KEY, base_asset TEXT, quote_asset TEXT,
            status TEXT, launch_date TEXT, contract_type TEXT)""",
        'progress': """CREATE TABLE IF NOT EXISTS progress (
            key TEXT PRIMARY KEY, value TEXT)""",
    }
    
    for sql in tables.values():
        c.execute(sql)
    conn.commit()
    return conn

def get_active_symbols(conn):
    print("\n🔍 获取活跃合约列表...")
    info = api_get("/fapi/v1/exchangeInfo")
    if not info:
        print("  ❌ 无法获取交易所信息")
        return []
    
    symbols = []
    for s in info.get('symbols', []):
        if (s['quoteAsset'] == 'USDT' and
            s['contractType'] == 'PERPETUAL' and
            s['status'] == 'TRADING'):
            symbols.append(s['symbol'])
            c = conn.cursor()
            c.execute("""INSERT OR REPLACE INTO symbols
                (symbol, base_asset, quote_asset, status, launch_date, contract_type)
                VALUES (?, ?, 'USDT', ?, ?, ?)""",
                (s['symbol'], s['baseAsset'], s['status'],
                 str(s.get('onboardDate', '')), s['contractType']))
    conn.commit()
    print(f"  ✅ {len(symbols)} 个活跃USDT永续合约")
    return symbols

# === 每个线程用独立连接 ===
_thread_local = threading.local()

def get_conn():
    if not hasattr(_thread_local, 'conn'):
        _thread_local.conn = sqlite3.connect(str(DB_PATH))
    return _thread_local.conn

def fetch_klines(symbol, interval="1h"):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT MAX(open_time) FROM klines WHERE symbol=? AND interval=?", (symbol, interval))
    row = c.fetchone()
    if row[0]:
        start = row[0] + 1
        if start >= END_TS - 86400000:
            return symbol, 0
    else:
        start = max(WARMUP_TS, MIN_TS)
    
    total = 0
    while start < END_TS:
        data = api_get("/fapi/v1/klines", {
            'symbol': symbol, 'interval': interval,
            'startTime': start, 'endTime': END_TS, 'limit': 1500
        })
        if not data:
            break
        rows = [(symbol, interval, k[0], float(k[1]), float(k[2]), float(k[3]),
                 float(k[4]), float(k[5]), k[6], float(k[7]),
                 int(k[8]), float(k[9]), float(k[10])) for k in data]
        with lock:
            c.executemany("INSERT OR REPLACE INTO klines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()
        total += len(rows)
        start = data[-1][0] + 1
        if len(data) < 1500:
            break
    return symbol, total

def fetch_funding_rates_one(symbol):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT MAX(funding_time) FROM funding_rates WHERE symbol=?", (symbol,))
    row = c.fetchone()
    if row[0]:
        start = row[0] + 1
        if start >= END_TS - 86400000:
            return symbol, 0
    else:
        start = START_TS
    
    total = 0
    while start < END_TS:
        data = api_get("/fapi/v1/fundingRate", {
            'symbol': symbol, 'startTime': start, 'endTime': END_TS, 'limit': 1000
        })
        if not data:
            break
        rows = [(symbol, f['fundingTime'], float(f['fundingRate']),
                 float(f.get('markPrice', 0))) for f in data]
        with lock:
            c.executemany("INSERT OR REPLACE INTO funding_rates VALUES (?,?,?,?)", rows)
            conn.commit()
        total += len(rows)
        start = data[-1]['fundingTime'] + 1
        if len(data) < 1000:
            break
    return symbol, total

def fetch_oi_one(symbol):
    conn = get_conn()
    c = conn.cursor()
    total = 0
    earliest = None
    while True:
        params = {'symbol': symbol, 'period': '1h', 'limit': 200}
        if earliest:
            params['endTime'] = earliest - 1
        data = api_get("/futures/data/openInterestHist", params)
        if not data or not isinstance(data, list):
            break
        rows = [(symbol, d['timestamp'], float(d['sumOpenInterest']),
                 float(d.get('sumOpenInterestValue', 0))) for d in data]
        with lock:
            c.executemany("INSERT OR REPLACE INTO open_interest VALUES (?,?,?,?)", rows)
            conn.commit()
        total += len(rows)
        earliest = data[0]['timestamp']
        if len(data) < 200:
            break
        time.sleep(0.05)
    return symbol, total

def fetch_ls_one(symbol, table, endpoint):
    conn = get_conn()
    c = conn.cursor()
    total = 0
    earliest = None
    while True:
        params = {'symbol': symbol, 'period': '1h', 'limit': 200}
        if earliest:
            params['endTime'] = earliest - 1
        data = api_get(endpoint, params)
        if not data or not isinstance(data, list):
            break
        rows = [(symbol, d['timestamp'], float(d['longShortRatio']),
                 float(d['longAccount']), float(d['shortAccount'])) for d in data]
        with lock:
            c.executemany(f"INSERT OR REPLACE INTO {table} VALUES (?,?,?,?,?)", rows)
            conn.commit()
        total += len(rows)
        earliest = data[0]['timestamp']
        if len(data) < 200:
            break
        time.sleep(0.05)
    return symbol, total

def fetch_taker_one(symbol):
    conn = get_conn()
    c = conn.cursor()
    total = 0
    earliest = None
    while True:
        params = {'symbol': symbol, 'period': '1h', 'limit': 200}
        if earliest:
            params['endTime'] = earliest - 1
        data = api_get("/futures/data/takerlongshortRatio", params)
        if not data or not isinstance(data, list):
            break
        rows = [(symbol, d['timestamp'], float(d['buySellRatio']),
                 float(d.get('buyVol', 0)), float(d.get('sellVol', 0))) for d in data]
        with lock:
            c.executemany("INSERT OR REPLACE INTO taker_volume_ratio VALUES (?,?,?,?,?)", rows)
            conn.commit()
        total += len(rows)
        earliest = data[0]['timestamp']
        if len(data) < 200:
            break
        time.sleep(0.05)
    return symbol, total

def fetch_mark_klines_one(symbol):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT MAX(open_time) FROM mark_klines WHERE symbol=? AND interval='1h'", (symbol,))
    row = c.fetchone()
    if row[0]:
        start = row[0] + 1
        if start >= END_TS - 86400000:
            return symbol, 0
    else:
        start = max(WARMUP_TS, MIN_TS)
    
    total = 0
    while start < END_TS:
        data = api_get("/fapi/v1/markPriceKlines", {
            'symbol': symbol, 'interval': '1h',
            'startTime': start, 'endTime': END_TS, 'limit': 1500
        })
        if not data:
            break
        rows = [(symbol, '1h', k[0], float(k[1]), float(k[2]),
                 float(k[3]), float(k[4])) for k in data]
        with lock:
            c.executemany("INSERT OR REPLACE INTO mark_klines VALUES (?,?,?,?,?,?,?)", rows)
            conn.commit()
        total += len(rows)
        start = data[-1][0] + 1
        if len(data) < 1500:
            break
    return symbol, total

def run_parallel(symbols, label, fn, max_workers=8, unit="条"):
    total = len(symbols)
    done = 0
    total_rows = 0
    print(f"\n🚀 {label} ({total}币种, {max_workers}线程)...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fn, s): s for s in symbols}
        for future in as_completed(futures):
            sym, n = future.result()
            done += 1
            total_rows += n
            pct = done / total * 100
            bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
            print(f"\r  [{bar}] {pct:5.1f}% ({done}/{total}) +{total_rows:,}{unit}  last: {sym} +{n}{unit}    ", end="", flush=True)
    print(f"\n  ✅ {label}完成: {total_rows:,}{unit}")

def main():
    print("=" * 60)
    print("⚡ 币安合约全量数据采集 (多线程加速版)")
    print(f"  时间范围: {START_TIME.strftime('%Y-%m-%d')} ~ {END_TIME.strftime('%Y-%m-%d')}")
    print(f"  数据存储: {DB_PATH}")
    print("=" * 60)
    
    conn = setup_database()
    symbols = get_active_symbols(conn)
    
    if not symbols:
        print("❌ 没有获取到币种列表")
        conn.close()
        return
    
    # BTC优先
    if 'BTCUSDT' in symbols:
        symbols.remove('BTCUSDT')
        symbols.insert(0, 'BTCUSDT')
    
    # === 并发采集 ===
    # K线类(重IO): 8线程
    run_parallel(symbols, "📈 1h K线", fetch_klines, max_workers=8, unit="根")
    run_parallel(symbols, "📈 4h K线", lambda s: fetch_klines(s, "4h"), max_workers=8, unit="根")
    run_parallel(symbols, "💹 标记价格K线", fetch_mark_klines_one, max_workers=8, unit="根")
    
    # 费率(中等IO): 6线程
    run_parallel(symbols, "💰 资金费率", fetch_funding_rates_one, max_workers=6, unit="条")
    
    # /futures/data 端点(限流较严): 4线程
    run_parallel(symbols, "📊 持仓量OI", fetch_oi_one, max_workers=4, unit="条")
    run_parallel(symbols, "🐋 大户多空比",
                 lambda s: fetch_ls_one(s, "ls_ratio_top", "/futures/data/topLongShortAccountRatio"),
                 max_workers=4, unit="条")
    run_parallel(symbols, "🦈 大户持仓比",
                 lambda s: fetch_ls_one(s, "ls_ratio_position", "/futures/data/topLongShortPositionRatio"),
                 max_workers=4, unit="条")
    run_parallel(symbols, "🌍 全局多空比",
                 lambda s: fetch_ls_one(s, "ls_ratio_global", "/futures/data/globalLongShortAccountRatio"),
                 max_workers=4, unit="条")
    run_parallel(symbols, "🔄 Taker买卖比", fetch_taker_one, max_workers=4, unit="条")
    
    # Ticker快照(单次API)
    print("\n📊 获取24h Ticker快照...")
    data = api_get("/fapi/v1/ticker/24hr")
    if data:
        c = conn.cursor()
        today = datetime.now(TZ_UTC8).strftime('%Y-%m-%d')
        count = 0
        for t in data:
            if t['symbol'] in symbols:
                c.execute("""INSERT OR REPLACE INTO ticker_daily
                    (symbol, date, volume, quote_volume, price_change_pct, high, low, trades)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (t['symbol'], today, float(t['volume']), float(t['quoteVolume']),
                     float(t['priceChangePercent']), float(t['highPrice']),
                     float(t['lowPrice']), int(t['count'])))
                count += 1
        conn.commit()
        print(f"  ✅ {count} 个币种Ticker快照已保存")
    
    # 统计
    print("\n" + "=" * 60)
    print("📊 数据库统计")
    print("=" * 60)
    c = conn.cursor()
    db_size = DB_PATH.stat().st_size / (1024 * 1024)
    print(f"  数据库大小: {db_size:.1f} MB")
    
    for interval in ['1h', '4h']:
        c.execute(f"SELECT COUNT(DISTINCT symbol), COUNT(*) FROM klines WHERE interval=?", (interval,))
        syms, rows = c.fetchone()
        print(f"  K线 {interval}: {syms}币种, {rows:,}根")
    
    for table, label in [('funding_rates', '资金费率'), ('open_interest', '持仓量OI'),
                         ('mark_klines', '标记价格K线'), ('ls_ratio_top', '大户多空比'),
                         ('ls_ratio_position', '大户持仓比'), ('ls_ratio_global', '全局多空比'),
                         ('taker_volume_ratio', 'Taker买卖比')]:
        c.execute(f"SELECT COUNT(DISTINCT symbol), COUNT(*) FROM {table}")
        syms, rows = c.fetchone()
        print(f"  {label}: {syms}币种, {rows:,}条")
    
    print("=" * 60)
    
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO progress VALUES (?, ?)",
              ("last_collect", datetime.now(TZ_UTC8).isoformat()))
    conn.commit()
    
    print("\n✅ 全量数据采集完成！")
    conn.close()

if __name__ == "__main__":
    main()
