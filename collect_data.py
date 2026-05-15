#!/usr/bin/env python3
"""
数据采集脚本 — 1000天全量回测数据库
采集币安合约所有活跃币种的:
1. K线 (1h + 4h 多时间框架)
2. 资金费率历史
3. 持仓量(OI)历史  
4. 24h Ticker (成交量、涨跌幅等)
5. BTC基准数据
存储到SQLite + JSON双格式
"""
import sys
import json
import time
import sqlite3
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict

FAPI = "https://fapi.binance.com"
TZ_UTC8 = timezone(timedelta(hours=8))
DATA_DIR = Path.home() / ".hermes" / "trading" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "backtest_db.sqlite"

# === 时间范围: 1000天 ===
END_TIME = datetime(2026, 5, 8, 23, 59, tzinfo=TZ_UTC8)
START_TIME = END_TIME - timedelta(days=1000)
START_TS = int(START_TIME.timestamp() * 1000)
END_TS = int(END_TIME.timestamp() * 1000)

# 预热期: 多拉30天数据用于计算指标
WARMUP_TS = int((START_TIME - timedelta(days=30)).timestamp() * 1000)

def api_get(endpoint, params=None, retries=3):
    url = FAPI + endpoint
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"    ⚠️ 限流, 等待{wait}s...")
                time.sleep(wait)
            else:
                return None
        except Exception as e:
            if attempt == retries - 1:
                return None
            time.sleep(1)
    return None

def setup_database():
    """创建SQLite表结构"""
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    
    # K线表 (1h和4h共用, interval字段区分)
    c.execute("""CREATE TABLE IF NOT EXISTS klines (
        symbol TEXT, interval TEXT, open_time INTEGER, 
        open REAL, high REAL, low REAL, close REAL,
        volume REAL, close_time INTEGER, quote_volume REAL,
        trades INTEGER, taker_buy_volume REAL, taker_buy_quote_volume REAL,
        PRIMARY KEY (symbol, interval, open_time)
    )""")
    
    # 资金费率
    c.execute("""CREATE TABLE IF NOT EXISTS funding_rates (
        symbol TEXT, funding_time INTEGER, funding_rate REAL,
        mark_price REAL,
        PRIMARY KEY (symbol, funding_time)
    )""")
    
    # 持仓量(Open Interest) 5分钟粒度
    c.execute("""CREATE TABLE IF NOT EXISTS open_interest (
        symbol TEXT, timestamp INTEGER, sum_open_interest REAL,
        sum_open_interest_value REAL,
        PRIMARY KEY (symbol, timestamp)
    )""")
    
    # 标记价格K线 (用于计算未实现盈亏/强平价)
    c.execute("""CREATE TABLE IF NOT EXISTS mark_klines (
        symbol TEXT, interval TEXT, open_time INTEGER,
        open REAL, high REAL, low REAL, close REAL,
        PRIMARY KEY (symbol, interval, open_time)
    )""")
    
    # OI历史(按日)
    c.execute("""CREATE TABLE IF NOT EXISTS oi_daily (
        symbol TEXT, date TEXT, sum_open_interest REAL, 
        sum_open_interest_value REAL,
        PRIMARY KEY (symbol, date)
    )""")
    
    # Ticker快照(24h数据)
    c.execute("""CREATE TABLE IF NOT EXISTS ticker_daily (
        symbol TEXT, date TEXT, volume REAL, quote_volume REAL,
        price_change_pct REAL, high REAL, low REAL,
        trades INTEGER,
        PRIMARY KEY (symbol, date)
    )""")
    
    # 大户多空比(topLongShortAccountRatio)
    c.execute("""CREATE TABLE IF NOT EXISTS ls_ratio_top (
        symbol TEXT, timestamp INTEGER, long_short_ratio REAL,
        long_account REAL, short_account REAL,
        PRIMARY KEY (symbol, timestamp)
    )""")
    
    # 大户持仓比(topLongShortPositionRatio)
    c.execute("""CREATE TABLE IF NOT EXISTS ls_ratio_position (
        symbol TEXT, timestamp INTEGER, long_short_ratio REAL,
        long_account REAL, short_account REAL,
        PRIMARY KEY (symbol, timestamp)
    )""")
    
    # 全局多空比(globalLongShortAccountRatio)
    c.execute("""CREATE TABLE IF NOT EXISTS ls_ratio_global (
        symbol TEXT, timestamp INTEGER, long_short_ratio REAL,
        long_account REAL, short_account REAL,
        PRIMARY KEY (symbol, timestamp)
    )""")
    
    # Taker买卖比
    c.execute("""CREATE TABLE IF NOT EXISTS taker_volume_ratio (
        symbol TEXT, timestamp INTEGER, buy_sell_ratio REAL,
        buy_vol REAL, sell_vol REAL,
        PRIMARY KEY (symbol, timestamp)
    )""")
    
    # 币种元信息
    c.execute("""CREATE TABLE IF NOT EXISTS symbols (
        symbol TEXT PRIMARY KEY, base_asset TEXT, quote_asset TEXT,
        status TEXT, launch_date TEXT, contract_type TEXT
    )""")
    
    # 采集进度
    c.execute("""CREATE TABLE IF NOT EXISTS progress (
        key TEXT PRIMARY KEY, value TEXT
    )""")
    
    conn.commit()
    return conn

def get_active_symbols(conn):
    """获取所有活跃USDT永续合约"""
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
            symbols.append({
                'symbol': s['symbol'],
                'base': s['baseAsset'],
                'status': s['status'],
                'contractType': s['contractType'],
                'launchDate': s.get('onboardDate', '')
            })
    
    # 存入数据库
    c = conn.cursor()
    for s in symbols:
        c.execute("""INSERT OR REPLACE INTO symbols 
            (symbol, base_asset, quote_asset, status, launch_date, contract_type)
            VALUES (?, ?, 'USDT', ?, ?, ?)""",
            (s['symbol'], s['base'], s['status'], 
             str(s['launchDate']) if s['launchDate'] else '', s['contractType']))
    conn.commit()
    
    print(f"  ✅ {len(symbols)} 个活跃USDT永续合约")
    return [s['symbol'] for s in symbols]

def fetch_klines(conn, symbol, interval="1h"):
    """拉取K线数据，带断点续传"""
    c = conn.cursor()
    
    # 检查上次采集到哪里
    c.execute("""SELECT MAX(open_time) FROM klines 
        WHERE symbol=? AND interval=?""", (symbol, interval))
    row = c.fetchone()
    
    if row[0]:
        start = row[0] + 1  # 从上次结束+1开始
        # 如果距离结束时间<1天就不拉了
        if start >= END_TS - 86400000:
            return 0
    else:
        start = WARMUP_TS
    
    # 确保不早于2023-01-01(币安数据可靠性边界)
    MIN_TS = int(datetime(2023, 1, 1, tzinfo=TZ_UTC8).timestamp() * 1000)
    start = max(start, MIN_TS)
    
    total_inserted = 0
    batch_size = 1500  # 币安单次最大1500根
    
    while start < END_TS:
        params = {
            'symbol': symbol,
            'interval': interval,
            'startTime': start,
            'endTime': END_TS,
            'limit': batch_size
        }
        data = api_get("/fapi/v1/klines", params)
        if not data:
            break
        
        rows = []
        for k in data:
            rows.append((
                symbol, interval, k[0],
                float(k[1]), float(k[2]), float(k[3]), float(k[4]),
                float(k[5]), k[6], float(k[7]),
                int(k[8]), float(k[9]), float(k[10])
            ))
        
        c.executemany("""INSERT OR REPLACE INTO klines 
            (symbol, interval, open_time, open, high, low, close,
             volume, close_time, quote_volume, trades, 
             taker_buy_volume, taker_buy_quote_volume)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
        conn.commit()
        
        total_inserted += len(rows)
        start = data[-1][0] + 1  # 下一批从最后一根+1开始
        
        if len(data) < batch_size:
            break
    
    return total_inserted

def fetch_funding_rates(conn, symbol):
    """拉取资金费率历史"""
    c = conn.cursor()
    
    c.execute("""SELECT MAX(funding_time) FROM funding_rates WHERE symbol=?""", (symbol,))
    row = c.fetchone()
    
    if row[0]:
        start = row[0] + 1
        if start >= END_TS - 86400000:
            return 0
    else:
        start = START_TS
    
    total_inserted = 0
    batch_size = 1000
    
    while start < END_TS:
        params = {
            'symbol': symbol,
            'startTime': start,
            'endTime': END_TS,
            'limit': batch_size
        }
        data = api_get("/fapi/v1/fundingRate", params)
        if not data:
            break
        
        rows = []
        for f in data:
            mp = f.get('markPrice', '0')
            rows.append((
                symbol, f['fundingTime'], float(f['fundingRate']),
                float(mp) if mp else 0.0
            ))
        
        c.executemany("""INSERT OR REPLACE INTO funding_rates 
            (symbol, funding_time, funding_rate, mark_price)
            VALUES (?,?,?,?)""", rows)
        conn.commit()
        
        total_inserted += len(rows)
        start = data[-1]['fundingTime'] + 1
        
        if len(data) < batch_size:
            break
    
    return total_inserted

def fetch_open_interest_history(conn, symbol):
    """拉取持仓量历史(1h粒度) — /futures/data 端点只支持最近30天"""
    c = conn.cursor()
    
    c.execute("""SELECT MAX(timestamp) FROM open_interest WHERE symbol=?""", (symbol,))
    row = c.fetchone()
    
    # 只拉最新数据（API限制约30天）
    total_inserted = 0
    limit = 200
    earliest = None  # None表示第一页不带endTime
    
    while True:
        params = {
            'symbol': symbol,
            'period': '1h',
            'limit': limit
        }
        if earliest:
            params['endTime'] = earliest - 1
        
        data = api_get("/futures/data/openInterestHist", params)
        if not data or not isinstance(data, list):
            break
        
        rows = []
        for oi in data:
            rows.append((
                symbol, oi['timestamp'], 
                float(oi['sumOpenInterest']),
                float(oi.get('sumOpenInterestValue', 0))
            ))
        
        c.executemany("""INSERT OR REPLACE INTO open_interest 
            (symbol, timestamp, sum_open_interest, sum_open_interest_value) 
            VALUES (?,?,?,?)""", rows)
        conn.commit()
        
        total_inserted += len(rows)
        earliest = data[0]['timestamp']
        
        if len(data) < limit:
            break
        
        time.sleep(0.1)
    
    return total_inserted

def fetch_ls_ratio(conn, symbol, table, endpoint):
    """拉取多空比数据 — /futures/data 端点只支持最近30天，用endTime翻页"""
    c = conn.cursor()
    
    total_inserted = 0
    limit = 200
    earliest = None
    
    while True:
        params = {
            'symbol': symbol,
            'period': '1h',
            'limit': limit
        }
        if earliest:
            params['endTime'] = earliest - 1
        
        data = api_get(endpoint, params)
        if not data or not isinstance(data, list):
            break
        
        rows = []
        for d in data:
            rows.append((
                symbol, d['timestamp'],
                float(d['longShortRatio']),
                float(d['longAccount']),
                float(d['shortAccount'])
            ))
        
        c.executemany(f"""INSERT OR REPLACE INTO {table} 
            (symbol, timestamp, long_short_ratio, long_account, short_account)
            VALUES (?,?,?,?,?)""", rows)
        conn.commit()
        
        total_inserted += len(rows)
        earliest = data[0]['timestamp']
        
        if len(data) < limit:
            break
        
        time.sleep(0.1)
    
    return total_inserted

def fetch_taker_volume(conn, symbol):
    """拉取Taker买卖比 — /futures/data 端点只支持最近30天"""
    c = conn.cursor()
    
    total_inserted = 0
    limit = 200
    earliest = None
    
    while True:
        params = {
            'symbol': symbol,
            'period': '1h',
            'limit': limit
        }
        if earliest:
            params['endTime'] = earliest - 1
        
        data = api_get("/futures/data/takerlongshortRatio", params)
        if not data or not isinstance(data, list):
            break
        
        rows = []
        for d in data:
            rows.append((
                symbol, d['timestamp'],
                float(d['buySellRatio']),
                float(d.get('buyVol', 0)),
                float(d.get('sellVol', 0))
            ))
        
        c.executemany("""INSERT OR REPLACE INTO taker_volume_ratio 
            (symbol, timestamp, buy_sell_ratio, buy_vol, sell_vol)
            VALUES (?,?,?,?,?)""", rows)
        conn.commit()
        
        total_inserted += len(rows)
        earliest = data[0]['timestamp']
        
        if len(data) < limit:
            break
        
        time.sleep(0.1)
    
    return total_inserted

def fetch_mark_klines(conn, symbol, interval="1h"):
    """拉取标记价格K线"""
    c = conn.cursor()
    
    c.execute("""SELECT MAX(open_time) FROM mark_klines 
        WHERE symbol=? AND interval=?""", (symbol, interval))
    row = c.fetchone()
    
    if row[0]:
        start = row[0] + 1
        if start >= END_TS - 86400000:
            return 0
    else:
        start = WARMUP_TS
    
    MIN_TS = int(datetime(2023, 1, 1, tzinfo=TZ_UTC8).timestamp() * 1000)
    start = max(start, MIN_TS)
    
    total_inserted = 0
    batch_size = 1500
    
    while start < END_TS:
        params = {
            'symbol': symbol,
            'interval': interval,
            'startTime': start,
            'endTime': END_TS,
            'limit': batch_size
        }
        data = api_get("/fapi/v1/markPriceKlines", params)
        if not data:
            break
        
        rows = []
        for k in data:
            rows.append((
                symbol, interval, k[0],
                float(k[1]), float(k[2]), float(k[3]), float(k[4])
            ))
        
        c.executemany("""INSERT OR REPLACE INTO mark_klines 
            (symbol, interval, open_time, open, high, low, close)
            VALUES (?,?,?,?,?,?,?)""", rows)
        conn.commit()
        
        total_inserted += len(rows)
        start = data[-1][0] + 1
        
        if len(data) < batch_size:
            break
    
    return total_inserted

def fetch_ticker_24h(conn, symbols):
    """拉取当前所有币种24h ticker"""
    print("\n📊 获取24h Ticker快照...")
    data = api_get("/fapi/v1/ticker/24hr")
    if not data:
        print("  ❌ 获取失败")
        return
    
    c = conn.cursor()
    today = datetime.now(TZ_UTC8).strftime('%Y-%m-%d')
    
    count = 0
    for t in data:
        if t['symbol'] in symbols:
            c.execute("""INSERT OR REPLACE INTO ticker_daily 
                (symbol, date, volume, quote_volume, price_change_pct,
                 high, low, trades)
                VALUES (?,?,?,?,?,?,?,?)""", (
                t['symbol'], today,
                float(t['volume']), float(t['quoteVolume']),
                float(t['priceChangePercent']),
                float(t['highPrice']), float(t['lowPrice']),
                int(t['count'])
            ))
            count += 1
    conn.commit()
    print(f"  ✅ {count} 个币种Ticker快照已保存")

def fetch_long_short_ratio(conn, symbol):
    """拉取多空比历史(大户vs散户)"""
    c = conn.cursor()
    
    c.execute("""SELECT MAX(timestamp) FROM open_interest WHERE symbol=? AND interval IS NULL""", (symbol,))
    # 用单独表存
    # 这里先跳过，后续可加
    return 0

def print_stats(conn):
    """打印数据库统计"""
    c = conn.cursor()
    
    print("\n" + "=" * 60)
    print("📊 数据库统计")
    print("=" * 60)
    
    # 总大小
    db_size = DB_PATH.stat().st_size / (1024 * 1024)
    print(f"  数据库大小: {db_size:.1f} MB")
    print(f"  路径: {DB_PATH}")
    
    # K线
    for interval in ['1h', '4h']:
        c.execute(f"SELECT COUNT(DISTINCT symbol), COUNT(*) FROM klines WHERE interval=?", (interval,))
        symbols, rows = c.fetchone()
        print(f"  K线 {interval}: {symbols}币种, {rows:,}根")
    
    # 标记价格K线
    c.execute("SELECT COUNT(DISTINCT symbol), COUNT(*) FROM mark_klines")
    syms, rows = c.fetchone()
    print(f"  标记价格K线: {syms}币种, {rows:,}根")
    
    # 时间范围
    c.execute("SELECT MIN(open_time), MAX(open_time) FROM klines WHERE interval='1h'")
    mn, mx = c.fetchone()
    if mn and mx:
        print(f"  K线时间范围: {datetime.fromtimestamp(mn/1000, TZ_UTC8).strftime('%Y-%m-%d')} ~ {datetime.fromtimestamp(mx/1000, TZ_UTC8).strftime('%Y-%m-%d')}")
    
    # 费率
    c.execute("SELECT COUNT(DISTINCT symbol), COUNT(*) FROM funding_rates")
    symbols, rows = c.fetchone()
    print(f"  资金费率: {symbols}币种, {rows:,}条")
    
    # OI
    c.execute("SELECT COUNT(DISTINCT symbol), COUNT(*) FROM open_interest")
    symbols, rows = c.fetchone()
    print(f"  持仓量OI: {symbols}币种, {rows:,}条")
    
    # 多空比
    for table, label in [('ls_ratio_top', '大户多空比'), ('ls_ratio_position', '大户持仓比'), ('ls_ratio_global', '全局多空比')]:
        c.execute(f"SELECT COUNT(DISTINCT symbol), COUNT(*) FROM {table}")
        syms, rows = c.fetchone()
        print(f"  {label}: {syms}币种, {rows:,}条")
    
    # Taker买卖比
    c.execute("SELECT COUNT(DISTINCT symbol), COUNT(*) FROM taker_volume_ratio")
    syms, rows = c.fetchone()
    print(f"  Taker买卖比: {syms}币种, {rows:,}条")
    
    # 币种
    c.execute("SELECT COUNT(*) FROM symbols")
    print(f"  币种总数: {c.fetchone()[0]}")
    
    print("=" * 60)

def run_batch(conn, symbols, label, fetch_fn, unit="条"):
    """通用的批量采集+进度条"""
    total = len(symbols)
    print(f"\n🔄 {label} ({total}币种)...")
    for i, sym in enumerate(symbols):
        n = fetch_fn(conn, sym) if not isinstance(fetch_fn, tuple) else fetch_fn[0](conn, sym, *fetch_fn[1:])
        pct = (i + 1) / total * 100
        bar = "█" * int(pct // 5) + "░" * (20 - int(pct // 5))
        print(f"\r  [{bar}] {pct:5.1f}% ({i+1}/{total}) {sym}: +{n}{unit}", end="", flush=True)
    print()

def main():
    print("=" * 60)
    print("📦 币安合约全量数据采集")
    print(f"  时间范围: {START_TIME.strftime('%Y-%m-%d')} ~ {END_TIME.strftime('%Y-%m-%d')}")
    print(f"  数据存储: {DB_PATH}")
    print("=" * 60)
    
    conn = setup_database()
    symbols = get_active_symbols(conn)
    
    if not symbols:
        print("❌ 没有获取到币种列表")
        conn.close()
        return
    
    # BTC优先(基准数据)
    if 'BTCUSDT' in symbols:
        symbols.remove('BTCUSDT')
        symbols.insert(0, 'BTCUSDT')
    
    total = len(symbols)
    
    # === 第一轮: 1h K线 ===
    run_batch(conn, symbols, "📈 采集 1h K线", lambda c, s: fetch_klines(c, s, "1h"), "根")
    
    # === 第二轮: 4h K线 ===
    run_batch(conn, symbols, "📈 采集 4h K线", lambda c, s: fetch_klines(c, s, "4h"), "根")
    
    # === 第三轮: 标记价格K线(1h) ===
    run_batch(conn, symbols, "💹 采集标记价格K线 1h", lambda c, s: fetch_mark_klines(c, s, "1h"), "根")
    
    # === 第四轮: 资金费率 ===
    run_batch(conn, symbols, "💰 采集资金费率", fetch_funding_rates, "条")
    
    # === 第五轮: 持仓量OI ===
    run_batch(conn, symbols, "📊 采集持仓量OI", fetch_open_interest_history, "条")
    
    # === 第六轮: 大户多空比 ===
    run_batch(conn, symbols, "🐋 采集大户多空比", 
              lambda c, s: fetch_ls_ratio(c, s, "ls_ratio_top", "/futures/data/topLongShortAccountRatio"), "条")
    
    # === 第七轮: 大户持仓比 ===
    run_batch(conn, symbols, "🦈 采集大户持仓比", 
              lambda c, s: fetch_ls_ratio(c, s, "ls_ratio_position", "/futures/data/topLongShortPositionRatio"), "条")
    
    # === 第八轮: 全局多空比 ===
    run_batch(conn, symbols, "🌍 采集全局多空比", 
              lambda c, s: fetch_ls_ratio(c, s, "ls_ratio_global", "/futures/data/globalLongShortAccountRatio"), "条")
    
    # === 第九轮: Taker买卖比 ===
    run_batch(conn, symbols, "🔄 采集Taker买卖比", fetch_taker_volume, "条")
    
    # === Ticker快照 ===
    fetch_ticker_24h(conn, symbols)
    
    # === 统计 ===
    print_stats(conn)
    
    # 保存采集完成标记
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO progress VALUES (?, ?)", 
              ("last_collect", datetime.now(TZ_UTC8).isoformat()))
    conn.commit()
    
    print("\n✅ 全量数据采集完成！后续回测直接从本地SQLite读取，无需再拉API")
    conn.close()

if __name__ == "__main__":
    main()
