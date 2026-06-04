# M40 OI Core 验证结论 (2026-06-04)

## 数据限制

Binance `openInterestHist` 官方只提供最近约 1 个月数据，因此无法用公开 API 重跑 2025-06~2026-06 的真实 OI 历史。本文使用当前可取得的最新 500 根 1h OI 数据（约 20.8 天）做 OI-only 快速验收。

- OI 数据: `/futures/data/openInterestHist`, period=1h, limit=500
- K线: Binance Futures 1h Kline
- 窗口: 约 2026-05-14 ~ 2026-06-04
- 信号: OI 1h 增幅 >= 5%, OI notional >= 2M
- 方向: 24h price change > 0 做多，否则做空
- 退出: ATR stop / 2.5R TP / 24h max hold
- 仓位: 50U margin, 3x leverage, fee+slippage included

## 核心结果

| 版本 | 笔数 | 胜率 | PnL | PF | 结论 |
|---|---:|---:|---:|---:|---|
| OI all | 123 | 35.8% | -32.84U | 0.95 | 不合格 |
| OI long only | 71 | 36.6% | +61.42U | 1.17 | 勉强正 |
| OI short only | 52 | 34.6% | -94.26U | 0.69 | 禁用 |
| OI long + blacklist | 42 | 47.6% | +166.65U | 1.92 | 合格 |
| OI long + blacklist + OI>=7.5% | 15 | 46.7% | +100.06U | 2.92 | 样本少但最好 |

Blacklist used in validation: `SKYAIUSDT`, `BUSDT`, `VVVUSDT`, `INTCUSDT`, `TONUSDT`.

## 分币种观察

主要正贡献：

- `LABUSDT`: +75.15U
- `BILLUSDT`: +46.15U
- `GTCUSDT`: +18.48U
- `SAGAUSDT`: +18.54U
- `MUUSDT`: +17.63U

主要负贡献：

- `SKYAIUSDT`: -109.97U
- `VVVUSDT`: -33.59U
- `BUSDT`: -27.63U
- `INTCUSDT`: -24.27U
- `TONUSDT`: -17.44U

## 结论

原始 `oi_surge` 不能无脑保留；真正有效的是：

```text
OI 做多 + 坏币过滤 + 禁做空
```

建议的生产候选不是 `M40`，也不是 `OI all`，而是：

```text
M40_OI_CORE_LONG
```

规则建议：

1. 只保留 `oi_surge long`。
2. 禁用 `oi_surge short`。
3. 禁用 `closed_15m_spike long`。
4. 黑名单至少包括：`SKYAIUSDT`, `BUSDT`, `VVVUSDT`, `INTCUSDT`, `TONUSDT`。
5. OI surge 门槛可从 5% 提到 7.5% 做 testnet 对照。
6. 继续记录 shadow，至少 50 笔后再决定是否扩大仓位。

一句话：

> M40 不是好策略；OI all 也不是好策略；当前值得保留的是 `OI long filtered`。
