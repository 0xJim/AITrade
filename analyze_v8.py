#!/usr/bin/env python3
"""v8回测深度分析：为什么赚钱太少？"""
import json

with open('data/backtest_v8_result.json') as f:
    data = json.load(f)

trades = data.get('trades', data)

print("=" * 60)
print("🔍 v8 赚钱太少的根因分析")
print("=" * 60)

# 1. 每笔交易详情
print("\n📋 每笔交易详细分析:")
wins = []
losses = []
for t in sorted(trades, key=lambda x: x.get('entry_time', '')):
    pnl = t.get('pnl_usd', 0)
    symbol = t.get('symbol', '?').replace('USDT','')
    side = t.get('direction', '?')
    sl_pct = t.get('signal_sl_pct', 0)
    tp_pct = t.get('signal_tp_pct', 0)
    rr = t.get('signal_rr', 0)
    atr = t.get('tech_snapshot', {}).get('atr_pct', 0) * 100
    pos_usd = t.get('position_usd', 0)
    env = t.get('env_score', 0)
    exit_reason = t.get('exit_reason', '?')
    actual_pct = pnl / pos_usd * 100 if pos_usd > 0 else 0
    
    tag = "✅" if pnl > 0 else "❌"
    print(f"  {tag} {t['id']} {symbol:6s} {side:4s} | PnL {pnl:+7.1f}U | 实际{actual_pct:+5.1f}% | SL={sl_pct:.1f}% TP={tp_pct:.1f}% RR={rr:.1f} | ATR={atr:.1f}% | env={env} | ${pos_usd:.0f} | {exit_reason}")
    if pnl > 0:
        wins.append(t)
    else:
        losses.append(t)

# 2. 盈亏对比
print(f"\n📊 盈亏对比:")
avg_win = sum(t.get('pnl_usd', 0) for t in wins) / len(wins) if wins else 0
avg_loss = sum(t.get('pnl_usd', 0) for t in losses) / len(losses) if losses else 0
total_win = sum(t.get('pnl_usd', 0) for t in wins)
total_loss = sum(t.get('pnl_usd', 0) for t in losses)
print(f"  盈利笔: {len(wins)}笔, 总+{total_win:.1f}U, 均+{avg_win:.1f}U")
print(f"  亏损笔: {len(losses)}笔, 总{total_loss:.1f}U, 均{avg_loss:.1f}U")
print(f"  盈亏比: {abs(avg_win/avg_loss):.2f}" if avg_loss != 0 else "  盈亏比: N/A")

# 3. 止损单 - SL/ATR分析
print(f"\n🔬 止损单 SL/ATR分析:")
for t in losses:
    pnl = t.get('pnl_usd', 0)
    sl_pct = t.get('signal_sl_pct', 0)
    atr = t.get('tech_snapshot', {}).get('atr_pct', 0) * 100
    pos_usd = t.get('position_usd', 0)
    symbol = t.get('symbol', '?').replace('USDT','')
    sl_atr = sl_pct / atr if atr > 0 else 0
    print(f"  ❌ {t['id']} {symbol:6s}: SL={sl_pct:.1f}% ATR={atr:.1f}% SL/ATR={sl_atr:.1f}x | 亏{abs(pnl):.1f}U | 仓位${pos_usd:.0f}")

# 4. 盈利单 - 实际赚了多少 vs 本来可以赚多少
print(f"\n🔬 盈利单 - 止盈是否太早:")
for t in wins:
    pnl = t.get('pnl_usd', 0)
    tp_pct = t.get('signal_tp_pct', 0)
    actual_pct = pnl / t.get('position_usd', 1) * 100
    tp_potential = t.get('position_usd', 0) * tp_pct / 100
    symbol = t.get('symbol', '?').replace('USDT','')
    exit_reason = t.get('exit_reason', '?')
    print(f"  ✅ {t['id']} {symbol:6s}: 赚{pnl:+.1f}U (实际{actual_pct:+.1f}%) | TP目标={tp_pct:.1f}%(${tp_potential:.0f}) | {exit_reason}")

# 5. 关键问题
print(f"\n{'='*60}")
print("🎯 赚钱少的三大根因:")
print(f"{'='*60}")

# 仓位分析
positions = [t.get('position_usd', 0) for t in trades]
avg_pos = sum(positions) / len(positions) if positions else 0
max_risk = avg_pos * abs(avg_loss) / 100 if avg_pos > 0 else 0
print(f"\n  问题1: 仓位太小")
print(f"    平均仓位 ${avg_pos:.0f}, 平均亏${abs(avg_loss):.0f}")
print(f"    3x杠杆下实际敞口 ${avg_pos*3:.0f}")

print(f"\n  问题2: 盈亏比{abs(avg_win/avg_loss):.2f} < 1.0 (目标≥1.5)")
print(f"    均赢${avg_win:.0f} vs 均亏${abs(avg_loss):.0f}")
print(f"    需要把均赢提到${abs(avg_loss)*1.5:.0f}以上")

print(f"\n  问题3: 11笔/6月太少")
print(f"    21个信号只用了11个（过滤率52%）")
print(f"    可能有些被误杀")

# 潜力分析
print(f"\n{'='*60}")
print("💡 如果优化后的潜力:")
print(f"{'='*60}")
# Scenario: 提高仓位50%, RR=1.5, 胜率不变
new_pos = avg_pos * 1.5
new_avg_win = abs(avg_loss) * 1.5
new_total = len(wins) * new_avg_win + len(losses) * avg_loss
print(f"  方案A: 仓位+50% + RR=1.5 → 预估PnL = {new_total:+.0f}U/6月")
print(f"  方案B: 仓位+50% + RR=2.0 + 15笔/月 → 预估PnL = {15*0.6*abs(avg_loss)*2.0*1.5 - 15*0.4*abs(avg_loss)*1.5:+.0f}U/6月")

# v7对比
print(f"\n  v7实际: -$176 (太激进)")
print(f"  v8实际: +$39 (太保守)")
print(f"  目标: +$200~500/6月 (4~10%回报)")
