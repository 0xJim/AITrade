#!/usr/bin/env python3
"""数据完整性检查"""
import sqlite3
from datetime import datetime, timezone, timedelta

DB = "/home/ubuntu/.hermes/trading/data/backtest_db.sqlite"
TZ_UTC8 = timezone(timedelta(hours=8))
conn = sqlite3.connect(DB)
c = conn.cursor()

print("=" * 60)
print("🔍 数据完整性全面检查")
print("=" * 60)

# 1. K线覆盖
print("\n=== 1. K线覆盖 ===")
for iv in ['1h', '4h', '15m']:
    c.execute(f"SELECT COUNT(DISTINCT symbol), COUNT(*) FROM klines WHERE interval=?", (iv,))
    syms, rows = c.fetchone()
    c.execute(f"SELECT MIN(open_time), MAX(open_time) FROM klines WHERE interval=?", (iv,))
    mn, mx = c.fetchone()
    if mn:
        days = (mx - mn) / 86400000
        print(f"  {iv}: {syms}币种, {rows:,}根, {days:.0f}天")
    else:
        print(f"  {iv}: 无数据")

# 1b. 数据量不足的币种
print("\n  --- 1h K线数据不足(<5000根)的币种 ---")
c.execute("""SELECT symbol, COUNT(*) as cnt FROM klines WHERE interval='1h' 
    GROUP BY symbol HAVING COUNT(*) < 5000 ORDER BY cnt""")
low = c.fetchall()
if low:
    for sym, cnt in low[:20]:
        print(f"    {sym}: {cnt}根")
    if len(low) > 20:
        print(f"    ... 共{len(low)}个")
else:
    print("    无 (全部>=5000根)")

# 2. 资金费率
print("\n=== 2. 资金费率 ===")
c.execute("SELECT COUNT(DISTINCT symbol), COUNT(*), MIN(funding_time), MAX(funding_time) FROM funding_rates")
syms, rows, mn, mx = c.fetchone()
if mn:
    days = (mx - mn) / 86400000
    print(f"  {syms}币种, {rows:,}条, {days:.0f}天")
c.execute("""SELECT symbol, COUNT(*) as cnt FROM funding_rates GROUP BY symbol ORDER BY cnt LIMIT 5""")
print(f"  最少记录: {c.fetchall()}")

# 3. 标记价格K线
print("\n=== 3. 标记价格K线 ===")
c.execute("SELECT COUNT(DISTINCT symbol), COUNT(*) FROM mark_klines")
syms, rows = c.fetchone()
print(f"  {syms}币种, {rows:,}根")
c.execute("""SELECT COUNT(DISTINCT k.symbol) FROM klines k 
    WHERE k.interval='1h' AND k.symbol NOT IN 
    (SELECT DISTINCT symbol FROM mark_klines)""")
missing = c.fetchone()[0]
print(f"  缺失: {missing}个币种")

# 4. /futures/data 端点
print("\n=== 4. /futures/data 端点 (只有最近30天) ===")
for table, label in [('open_interest','OI'), ('ls_ratio_top','大户多空比'),
                     ('ls_ratio_position','大户持仓比'), ('ls_ratio_global','全局多空比'),
                     ('taker_volume_ratio','Taker买卖比')]:
    c.execute(f"SELECT COUNT(DISTINCT symbol), COUNT(*), MIN(timestamp), MAX(timestamp) FROM {table}")
    syms, rows, mn, mx = c.fetchone()
    days = (mx - mn) / 86400000 if mn else 0
    print(f"  {label}: {syms}币种, {rows:,}条, {days:.0f}天")

# 5. BTC基准
print("\n=== 5. BTC基准数据 ===")
for iv in ['1h', '4h']:
    c.execute("SELECT COUNT(*) FROM klines WHERE symbol='BTCUSDT' AND interval=?", (iv,))
    print(f"  BTC {iv}: {c.fetchone()[0]:,}根")
c.execute("SELECT COUNT(*) FROM funding_rates WHERE symbol='BTCUSDT'")
print(f"  BTC 费率: {c.fetchone()[0]:,}条")
c.execute("SELECT COUNT(*) FROM mark_klines WHERE symbol='BTCUSDT'")
print(f"  BTC 标记K线: {c.fetchone()[0]:,}根")

# 6. v11需要但缺失的关键数据
print("\n=== 6. v11关键缺失检查 ===")
# 信号A需要15m K线
c.execute("SELECT COUNT(DISTINCT symbol) FROM klines WHERE interval='15m'")
has_15m = c.fetchone()[0]
print(f"  15m K线: {has_15m}币种 {'✅' if has_15m > 0 else '❌ 缺失! v11信号A需要'}")

# 4h K线 for MTF
c.execute("SELECT COUNT(DISTINCT symbol) FROM klines WHERE interval='4h'")
has_4h = c.fetchone()[0]
print(f"  4h K线(MTF): {has_4h}币种 {'✅' if has_4h > 0 else '❌ 缺失!'}")

# OI历史只有30天 — 回测需要更长期的怎么办
c.execute("SELECT MIN(timestamp), MAX(timestamp) FROM open_interest")
mn, mx = c.fetchone()
oi_days = (mx - mn) / 86400000 if mn else 0
print(f"  OI历史: {oi_days:.0f}天 (⚠️ /futures/data只给30天，无法回测更早)")

# 7. 数据库总体
print("\n=== 7. 数据库总览 ===")
import os
db_size = os.path.getsize(DB) / (1024*1024)
print(f"  文件大小: {db_size:.0f} MB")
c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [r[0] for r in c.fetchall()]
print(f"  表: {tables}")

conn.close()
