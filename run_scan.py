#!/usr/bin/env python3
"""单次信号扫描"""
import sys
sys.path.insert(0, '.')

from signals import scan_all_signals

print("开始信号扫描...")
signals = scan_all_signals()

print(f"\n发现 {len(signals)} 个信号:")
for s in signals[:20]:
    d = '多' if s['direction'] == 'long' else '空'
    print(f"  [{s['strength']}] {s['symbol']:15s} {d} @ {s['price']:>12} | {s['type']:25s} | {s['reason'][:70]}")

if not signals:
    print("  当前无信号，市场平静")
