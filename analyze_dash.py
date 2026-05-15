#!/usr/bin/env python3
import json
with open('data/backtest_v6_result.json') as f:
    data = json.load(f)
trades = data.get('trades', data)
dash = sorted([t for t in trades if t.get('symbol') == 'DASHUSDT'], key=lambda x: x.get('entry_time',''))

for t in dash:
    d = t.get('direction','?')
    entry = t.get('entry_time','')[:16]
    pnl = t.get('pnl_usd',0)
    sl = t.get('signal_sl_pct',0)
    atr = t.get('tech_snapshot',{}).get('atr_pct',0)*100
    rsi = t.get('tech_snapshot',{}).get('rsi',0)
    trend = t.get('tech_snapshot',{}).get('ema_trend','?')
    strength = t.get('signal_strength','?')
    env = t.get('env_score',0)
    exit_r = t.get('exit_reason','?')
    m = 'Y' if pnl > 0 else 'N'
    print(f'{m} {entry} {d:5s} SL={sl:5.1f}% ATR={atr:4.1f}% RSI={rsi:5.0f} trend={trend:8s} str={strength} env={env} PnL={pnl:+6.0f}U exit={exit_r}')

losses = [t for t in dash if t.get('pnl_usd',0) < 0]
wins = [t for t in dash if t.get('pnl_usd',0) > 0]
print()
print('wins:', len(wins), 'losses:', len(losses))

losses_atr = [round(t.get('tech_snapshot',{}).get('atr_pct',0)*100,1) for t in losses]
wins_atr = [round(t.get('tech_snapshot',{}).get('atr_pct',0)*100,1) for t in wins]
losses_rsi = [int(t.get('tech_snapshot',{}).get('rsi',0)) for t in losses]
wins_rsi = [int(t.get('tech_snapshot',{}).get('rsi',0)) for t in wins]
losses_trend = [t.get('tech_snapshot',{}).get('ema_trend','?') for t in losses]
wins_trend = [t.get('tech_snapshot',{}).get('ema_trend','?') for t in wins]
losses_sl = [round(t.get('signal_sl_pct',0),1) for t in losses]
wins_sl = [round(t.get('signal_sl_pct',0),1) for t in wins]

print('loss_atr:', losses_atr)
print('win_atr:', wins_atr)
print('loss_rsi:', losses_rsi)
print('win_rsi:', wins_rsi)
print('loss_trend:', losses_trend)
print('win_trend:', wins_trend)
print('loss_sl:', losses_sl)
print('win_sl:', wins_sl)

# Now check: ALL same-symbol losses across board
print('\n=== 全币种亏损根因 ===')
all_losses = [t for t in trades if t.get('pnl_usd',0) < 0]
# Group by trend
from collections import Counter
trend_counter = Counter(t.get('tech_snapshot',{}).get('ema_trend','?') for t in all_losses)
print('亏损趋势分布:', dict(trend_counter))

# Group by RSI range
rsi_buckets = Counter()
for t in all_losses:
    rsi = t.get('tech_snapshot',{}).get('rsi',0)
    if rsi < 30: rsi_buckets['RSI<30'] += 1
    elif rsi < 45: rsi_buckets['RSI30-45'] += 1
    elif rsi < 55: rsi_buckets['RSI45-55'] += 1
    elif rsi < 65: rsi_buckets['RSI55-65'] += 1
    else: rsi_buckets['RSI>65'] += 1
print('亏损RSI分布:', dict(rsi_buckets))

# What about trend=down + long = bad?
bad_trend = [t for t in all_losses if t.get('direction') == 'long' and t.get('tech_snapshot',{}).get('ema_trend') == 'down']
print(f'\nlong+trend=down亏损: {len(bad_trend)}笔, PnL={sum(t.get("pnl_usd",0) for t in bad_trend):+.0f}U')

neutral_long = [t for t in all_losses if t.get('direction') == 'long' and t.get('tech_snapshot',{}).get('ema_trend') == 'neutral']
print(f'long+trend=neutral亏损: {len(neutral_long)}笔, PnL={sum(t.get("pnl_usd",0) for t in neutral_long):+.0f}U')

up_long = [t for t in all_losses if t.get('direction') == 'long' and t.get('tech_snapshot',{}).get('ema_trend') == 'up']
print(f'long+trend=up亏损: {len(up_long)}笔, PnL={sum(t.get("pnl_usd",0) for t in up_long):+.0f}U')

# Check wins by trend
all_wins = [t for t in trades if t.get('pnl_usd',0) > 0]
for trend_name in ['up', 'neutral', 'down']:
    wt = [t for t in all_wins if t.get('direction')=='long' and t.get('tech_snapshot',{}).get('ema_trend')==trend_name]
    print(f'long+trend={trend_name}盈利: {len(wt)}笔, PnL={sum(t.get("pnl_usd",0) for t in wt):+.0f}U')
