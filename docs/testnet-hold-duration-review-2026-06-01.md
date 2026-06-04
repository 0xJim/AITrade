# Testnet 持仓时长与逐笔盈亏复盘

- 检查时间 UTC: 2026-06-01T02:01:29.397038+00:00
- 交易所钱包余额: None USDT
- 交易所未实现盈亏: None USDT
- 交易所可用余额: None USDT
- 交易所真实持仓数: 3
- 交易所真实挂单数: 0
- 交易所 REALIZED_PNL: +412.5516 USDT
- 交易所 COMMISSION: -9.5777 USDT
- 交易所 FUNDING_FEE: +4.5691 USDT
- 本地账本已平仓 PnL 合计: +576.0988 USDT

> 注意: 逐笔表的 PnL 来自服务器本地交易账本, 真实账户总收益以 Binance Testnet income 为准。

## 当前未平仓
| ID | Symbol | 方向 | Profile | 持仓时长 | 本地入场 | 交易所持仓/浮盈亏 |
|---|---|---:|---|---:|---:|---|
| 093 | SUIUSDT | short | M40 | 47.0h | 2026-05-30T03:00:44 | amt -163.6 / entry 0.9031 / uPnL 3.60804585 |
| 094 | TAOUSDT | short | M40 | 47.0h | 2026-05-30T03:03:55 | amt -0.643 / entry 250.72 / uPnL -0.34722000 |
| 096 | INJUSDT | long | M40 | 40.0h | 2026-05-30T10:03:11 | amt 75.9 / entry 6.589 / uPnL -9.02851448 |

## 持仓时长分桶
| 持仓时长 | 笔数 | 胜 | 负 | 胜率 | PnL合计 | 单笔均值 |
|---|---:|---:|---:|---:|---:|---:|
| <=4h | 57 | 8 | 0 | 14.0% | +331.17 | +5.81 |
| 4-12h | 26 | 12 | 14 | 46.2% | +240.89 | +9.26 |
| 12-24h | 7 | 4 | 3 | 57.1% | +64.06 | +9.15 |
| 1-2d | 2 | 1 | 1 | 50.0% | -35.85 | -17.92 |
| >2d | 1 | 0 | 1 | 0.0% | -24.16 | -24.16 |

## 逐笔明细
| ID | Symbol | 方向 | Profile | 状态 | 持仓时长 | PnL(U) | PnL% | 退出原因 |
|---|---|---:|---|---|---:|---:|---:|---|
| 001 | INJUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 002 | ONDOUSDT | long | M40 | closed | 0m | +0.00 | +0.00% | 同步平仓(交易所无持仓) |
| 003 | EDENUSDT | long | M40 | closed | 1m | +0.00 | +0.00% | 同步平仓(交易所无持仓) |
| 004 | APRUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 005 | AIAUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 006 | MUUSDT | short | M40 | closed | 0m | +0.00 | +0.00% | 同步平仓(交易所无持仓) |
| 007 | SNDKUSDT | short | G60C | closed | 0m | +0.00 | +0.00% | 同步平仓(交易所无持仓) |
| 008 | BSBUSDT | long | M40 | closed | 0m | +0.00 | +0.00% | 同步平仓(交易所无持仓) |
| 009 | EDENUSDT | long | M40 | closed | 1m | +0.00 | +0.00% | 同步平仓(交易所无持仓) |
| 010 | ONTUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 011 | HYPEUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 012 | MSTRUSDT | short | M40 | closed | 1m | +0.00 | +0.00% | 同步平仓(交易所无持仓) |
| 013 | SNDKUSDT | short | M40 | closed | 1m | +0.00 | +0.00% | 同步平仓(交易所无持仓) |
| 014 | MUUSDT | short | M40 | closed | 1m | +0.00 | +0.00% | 同步平仓(交易所无持仓) |
| 015 | BCHUSDT | short | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 016 | ENJUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 017 | INJUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 018 | TONUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 019 | CLUSDT | long | M40 | closed | 1m | +0.00 | +0.00% | 同步平仓(交易所无持仓) |
| 020 | VVVUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 021 | ENJUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 022 | ZECUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 023 | ENJUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 024 | SUIUSDT | short | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 025 | INTCUSDT | long | M40 | closed | 0m | +0.00 | +0.00% | 同步平仓(交易所无持仓) |
| 026 | ZECUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 027 | VVVUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 028 | ONDOUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 029 | INJUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 030 | NEARUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 031 | PLAYUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 032 | FIDAUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 033 | HOMEUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 034 | HYPEUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 035 | INJUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 036 | PLAYUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 037 | ZECUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 038 | VVVUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 039 | TONUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 040 | LABUSDT | short | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 041 | WLDUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 042 | ZECUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 043 | DASHUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 044 | SUIUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 045 | TONUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 046 | NEARUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 047 | ONDOUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 048 | JTOUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 049 | SKYAIUSDT | long | M40 | closed | 0m | +0.00 | - | protective_stop_failed |
| 050 | NEARUSDT | long | M40 | closed | 19.5h | +59.11 | +37.84% | 止盈 |
| 051 | BANANAS31USDT | long | M40 | closed | 8.1h | -39.74 | -15.24% | 止损 |
| 052 | HYPEUSDT | long | M40 | closed | 7.4h | +33.26 | +15.87% | 移动止盈(高点回撤2.9%) |
| 053 | HYPEUSDT | long | M40 | closed | 6.2h | -25.08 | -16.06% | 止损 |
| 054 | ONDOUSDT | long | M40 | closed | 20.6h | +38.33 | +23.25% | 移动止盈(高点回撤3.3%) |
| 055 | PENGUUSDT | long | M40 | closed | 21.1h | -27.40 | -15.32% | 止损 |
| 056 | DASHUSDT | long | M40 | closed | 7.3h | -19.12 | -15.28% | 止损 |
| 057 | UBUSDT | short | M40 | closed | 4.0h | -9.34 | -35.10% | 止损 |
| 058 | NEARUSDT | long | M40 | closed | 4.0h | -16.10 | -15.28% | 止损 |
| 059 | JTOUSDT | long | M40 | closed | 4.0h | -5.79 | -16.56% | 止损 |
| 060 | PLAYUSDT | short | M40 | closed | 3.8h | +5.63 | +26.61% | 移动止盈(低点反弹2.8%) |
| 061 | LABUSDT | long | M40 | closed | 10.0h | -24.46 | -15.59% | 止损 |
| 062 | MEUSDT | long | M40 | closed | 16.3h | -40.44 | -19.38% | 止损 |
| 063 | UBUSDT | long | M40 | closed | 15.2h | +31.60 | +17.13% | 移动止盈(高点回撤2.9%) |
| 064 | GRASSUSDT | long | M40 | closed | 1.7h | +29.17 | +21.30% | 移动止盈(高点回撤3.3%) |
| 065 | ALTUSDT | long | M40 | closed | 12.5h | -21.43 | -15.05% | 止损 |
| 066 | NILUSDT | long | M40 | closed | 5.9h | +96.84 | +50.45% | 止盈 |
| 067 | FIDAUSDT | long | M40 | closed | 4.8h | +21.86 | +26.14% | 移动止盈(高点回撤2.5%) |
| 068 | SUPERUSDT | long | M40 | closed | 8.8h | +22.46 | +22.88% | 移动止盈(高点回撤2.7%) |
| 069 | NILUSDT | long | M40 | closed | 1.2h | +43.06 | +24.19% | 移动止盈(高点回撤2.8%) |
| 070 | TONUSDT | short | M40 | closed | 7.5h | -6.34 | -15.44% | 止损 |
| 071 | SOLUSDT | long | M40 | closed | 3.2d | -24.16 | -15.59% | 止损 |
| 072 | SUPERUSDT | long | M40 | closed | 5.1h | -9.07 | -15.43% | 止损 |
| 073 | FIDAUSDT | long | M40 | closed | 7.5h | -19.13 | -22.72% | 止损 |
| 074 | GRASSUSDT | long | M40 | closed | 15.2h | +24.29 | +21.62% | 移动止盈(高点回撤2.8%) |
| 075 | UBUSDT | long | M40 | closed | 4.0h | +31.28 | +15.04% | 移动止盈(高点回撤2.6%) |
| 076 | ERAUSDT | long | M40 | closed | 3.1h | +33.21 | +17.29% | 移动止盈(高点回撤3.2%) |
| 077 | ERAUSDT | long | M40 | closed | 4.0h | -11.38 | -20.87% | 止损 |
| 078 | UBUSDT | long | M40 | closed | 11.7h | +10.71 | +16.10% | 移动止盈(高点回撤3.2%) |
| 079 | DRIFTUSDT | long | M40 | closed | 2.3h | +116.28 | +63.31% | 止盈 |
| 080 | WLDUSDT | long | M40 | closed | 3.5h | +41.14 | +26.20% | 移动止盈(高点回撤3.0%) |
| 081 | PHAUSDT | long | M40 | closed | 8.3h | +38.49 | +35.84% | 移动止盈(高点回撤5.6%) |
| 082 | VVVUSDT | short | M40 | closed | 36.3h | +4.44 | +17.42% | 移动止盈(低点反弹2.8%) |
| 083 | TONUSDT | long | M40 | closed | 7.8h | -36.13 | -15.30% | 止损 |
| 084 | SEIUSDT | long | M40 | closed | 45.9h | -40.29 | -15.13% | 止损 |
| 085 | XLMUSDT | long | M40 | closed | 10.0h | +101.83 | +42.70% | 止盈 |
| 086 | BEATUSDT | long | M40 | closed | 11.9h | -18.37 | -21.01% | 止损 |
| 087 | XLMUSDT | long | M40 | closed | 3.3h | +31.29 | +17.05% | 移动止盈(高点回撤2.7%) |
| 088 | INJUSDT | long | M40 | closed | 6.4h | +49.55 | +22.05% | 移动止盈(高点回撤3.4%) |
| 089 | XLMUSDT | long | M40 | closed | 5.1h | +14.52 | +15.68% | 移动止盈(高点回撤2.8%) |
| 090 | HBARUSDT | long | M40 | closed | 7.7h | -40.55 | -15.20% | 止损 |
| 091 | XLMUSDT | long | M40 | closed | 11.0h | +23.97 | +25.55% | 移动止盈(高点回撤2.8%) |
| 092 | INJUSDT | long | M40 | closed | 2.1h | +31.39 | +15.32% | 移动止盈(高点回撤2.6%) |
| 093 | SUIUSDT | short | M40 | open | 47.0h | - | - |  |
| 094 | TAOUSDT | short | M40 | open | 47.0h | - | - |  |
| 095 | XLMUSDT | long | M40 | closed | 4.1h | +76.71 | +37.86% | 移动止盈(高点回撤2.7%) |
| 096 | INJUSDT | long | M40 | open | 40.0h | - | - |  |
