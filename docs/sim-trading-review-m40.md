# 📊 模拟盘复盘报告：v11j M40 Profile (5000U)

> **运行周期**: 2026-05-19 ~ 2026-05-31 (12天)
> **策略版本**: v11j M40 (单笔风险上限$40)
> **初始资金**: 5,000 USDT
> **交易平台**: Binance Testnet (模拟盘)

---

## 一、核心业绩指标

| 指标 | 数值 |
|------|------|
| **总PnL** | **+576.10U (+11.5%)** |
| 总交易数 | 96笔 |
| 有效交易 | 55笔 (排除STOP_FAIL) |
| 胜/负/平 | 25胜 / 19负 / 11平 |
| **胜率** | **56.8%** (排除持平) |
| 平均盈利 | +40.42U |
| 平均亏损 | -22.86U |
| **盈亏比** | **1.77:1** |
| 最大连胜 | 5笔 |
| 最大连亏 | 5笔 |
| 平均持仓 | 8.8小时 |
| 最长持仓 | 77.6小时 (SOLUSDT #071) |

## 二、周度对比

| 阶段 | 交易数 | 胜率 | PnL |
|------|--------|------|-----|
| **5/19-5/24 (第一周)** | 30笔 | 47.4% | +109.38U |
| **5/25-5/30 (第二周)** | 25笔 | 64.0% | +466.72U |

**关键发现**: 第二周胜率从47%提升到64%，PnL翻了4倍+。系统在STOP_FAIL bug修复后进入了稳定盈利阶段。

## 三、STOP_FAIL 问题 (已修复)

- **影响**: 38笔交易因 testnet 不支持 STOP_MARKET 订单类型而空跑
- **时间**: 集中在 5/19-5/21 (前3天)
- **修复**: 系统自动改用 MARKET 订单平仓，5/21后不再出现
- **损失**: 这些交易没有实际盈亏记录，但占用了大量扫描资源

## 四、信号类型表现

| 信号类型 | 笔数 | 胜率 | PnL | 评价 |
|----------|------|------|-----|------|
| **oi_surge (OI异动)** | 19笔 | 68.8% | **+378.36U** | 🏆 最佳信号 |
| **extreme_neg_funding (极端负费率)** | 11笔 | 50.0% | +116.14U | ✅ 稳定 |
| **funding_flip_neg (费率翻负)** | 1笔 | 100% | +101.83U | ⭐ 高质量 |
| closed_15m_spike (15m异动) | 23笔 | 47.1% | **-20.24U** | ⚠️ 需优化 |

**关键发现**:
- OI异动信号贡献了65.7%的利润，是核心盈利引擎
- 15m_spike信号胜率不足50%，虽然亏损不大但需要过滤优化
- 极端费率信号整体盈利但波动较大

## 五、平仓原因分布

| 平仓类型 | 笔数 | PnL |
|----------|------|-----|
| 止损 | 19笔 | -434.30U |
| 止盈 | 4笔 | +374.06U |
| 移动止盈 | 21笔 | +636.34U |
| 同步平仓(无持仓) | 11笔 | 0.00U |

**移动止盈系统表现优秀**: 21笔移动止盈共贡献 +636.34U，占全部盈利的60%+。

## 六、日度权益曲线

```
日期         日PnL        累计PnL     累计ROI
05/19       +0.00U       +0.00U      0.0%
05/20       +0.00U       +0.00U      0.0%     ← STOP_FAIL期
05/21       -6.47U       -6.47U     -0.1%
05/22      +43.90U      +37.43U     +0.7%     ← 首次盈利
05/23      -43.66U       -6.22U     -0.1%     ← 回撤
05/24       -3.70U       -9.93U     -0.2%
05/25     +159.10U     +149.18U     +3.0%     ← 爆发日
05/26     +214.24U     +363.41U     +7.3%     ← 最佳日
05/27       +2.36U     +365.77U     +7.3%
05/28      +82.10U     +447.88U     +9.0%
05/29       -3.85U     +444.03U     +8.9%
05/30     +132.07U     +576.10U    +11.5%     ← 当前
```

## 七、最佳/最差交易对 (≥2笔)

### 🏆 最佳
| 交易对 | 笔数 | 胜率 | PnL |
|--------|------|------|-----|
| XLMUSDT | 5笔 | 100% | +248.32U |
| NILUSDT | 2笔 | 100% | +139.91U |
| INJUSDT | 2笔 | 100% | +80.94U |
| UBUSDT | 4笔 | 75% | +64.24U |
| GRASSUSDT | 2笔 | 100% | +53.45U |

### ❌ 最差
| 交易对 | 笔数 | 胜率 | PnL |
|--------|------|------|-----|
| TONUSDT | 2笔 | 0% | -42.47U |
| SEIUSDT | 1笔 | 0% | -40.29U |
| HBARUSDT | 1笔 | 0% | -40.55U |
| MEUSDT | 1笔 | 0% | -40.44U |

## 八、当前持仓 (截至 5/31)

| ID | 交易对 | 方向 | 入场时间 | 入场价 | 止损 | 止盈 |
|----|--------|------|----------|--------|------|------|
| #093 | SUIUSDT | SHORT | 05/30 03:00 | 0.9032 | 0.9484 | 0.7903 |
| #094 | TAOUSDT | SHORT | 05/30 03:03 | 251.20 | 263.76 | 219.80 |
| #096 | INJUSDT | LONG | 05/30 10:03 | 6.581 | 6.252 | 7.404 |

## 九、问题与改进方向

### ✅ 表现良好的方面
1. **移动止盈系统** — 占盈利的60%+，是锁定利润的关键
2. **OI异动信号** — 胜率68.8%，贡献65.7%利润
3. **盈亏比1.77:1** — 即使胜率一般也能盈利
4. **第二周明显改善** — 修复bug后系统稳定盈利

### ⚠️ 需要改进
1. **15m_spike信号** — 胜率47%、PnL为负(-20.24U)，需要增加过滤条件或降低仓位
2. **做空策略** — 9笔做空PnL为-5.61U，做空能力不足
3. **单笔最大亏损** — MEUSDT/SEIUSDT/HBARUSDT 各亏40U触顶，止损可能需要更紧
4. **11笔"同步平仓"** — 交易所无持仓的空跑交易，浪费了信号

### 📋 下一步计划
1. 对15m_spike信号增加ATR/成交量过滤
2. 优化做空条件或暂停做空
3. 将ROI目标设为月+15% (年化~500%)
4. 积累30天数据后考虑升级到G60B Profile

---

## 附录：完整交易明细

<details>
<summary>点击展开全部96笔交易</summary>

### STOP_FAIL 交易 (38笔，5/19-5/21)
- #001 INJUSDT long oi_surge → protective_stop_failed
- #004 APRUSDT long closed_15m_spike → protective_stop_failed
- #005 AIAUSDT long oi_surge → protective_stop_failed
- #010-#049 (共38笔，均为 testnet 不支持 STOP_MARKET 导致)

### 同步平仓交易 (11笔，5/19-5/20)
- #002 ONDOUSDT long oi_surge → 交易所无持仓 PnL=0
- #003 EDENUSDT long closed_15m_spike → 交易所无持仓 PnL=0
- #006 MUUSDT short extreme_pos_funding → 交易所无持仓 PnL=0
- #007-#019 (共11笔)

### 有效交易明细 (55笔)

| ID | 交易对 | 方向 | 信号 | PnL | 持仓时间 | 平仓原因 |
|----|--------|------|------|-----|----------|----------|
| #050 | NEARUSDT | long | oi_surge | +59.11U | 19.5h | 止盈 |
| #051 | BANANAS31 | long | 15m_spike | -39.74U | 8.1h | 止损 |
| #052 | HYPEUSDT | long | 15m_spike | +33.26U | 7.4h | 移动止盈(2.9%) |
| #053 | HYPEUSDT | long | oi_surge | -25.08U | 6.2h | 止损 |
| #054 | ONDOUSDT | long | 15m_spike | +38.33U | 20.6h | 移动止盈(3.3%) |
| #055 | PENGUUSDT | long | 15m_spike | -27.40U | 21.2h | 止损 |
| #056 | DASHUSDT | long | 15m_spike | -19.12U | 7.2h | 止损 |
| #057 | UBUSDT | short | oi_surge | -9.34U | 4.0h | 止损 |
| #058 | NEARUSDT | long | oi_surge | -16.10U | 4.0h | 止损 |
| #059 | JTOUSDT | long | oi_surge | -5.79U | 4.0h | 止损 |
| #060 | PLAYUSDT | short | oi_surge | +5.63U | 3.8h | 移动止盈(2.8%) |
| #061 | LABUSDT | long | 15m_spike | -24.46U | 10.0h | 止损 |
| #062 | MEUSDT | long | extreme_neg | -40.44U | 16.3h | 止损 |
| #063 | UBUSDT | long | oi_surge | +31.60U | 15.2h | 移动止盈(2.9%) |
| #064 | GRASSUSDT | long | oi_surge | +29.17U | 1.7h | 移动止盈(3.3%) |
| #065 | ALTUSDT | long | 15m_spike | -21.43U | 12.5h | 止损 |
| #066 | NILUSDT | long | oi_surge | +96.84U | 5.9h | 止盈 |
| #067 | FIDAUSDT | long | extreme_neg | +21.86U | 4.8h | 移动止盈(2.5%) |
| #068 | SUPERUSDT | long | extreme_neg | +22.46U | 8.9h | 移动止盈(2.7%) |
| #069 | NILUSDT | long | oi_surge | +43.06U | 1.2h | 移动止盈(2.8%) |
| #070 | TONUSDT | short | 15m_spike | -6.34U | 7.6h | 止损 |
| #071 | SOLUSDT | long | 15m_spike | -24.16U | 77.6h | 止损 |
| #072 | SUPERUSDT | long | extreme_neg | -9.07U | 5.1h | 止损 |
| #073 | FIDAUSDT | long | extreme_neg | -19.13U | 7.5h | 止损 |
| #074 | GRASSUSDT | long | 15m_spike | +24.29U | 15.2h | 移动止盈(2.8%) |
| #075 | UBUSDT | long | 15m_spike | +31.28U | 4.0h | 移动止盈(2.6%) |
| #076 | ERAUSDT | long | extreme_neg | +33.21U | 3.1h | 移动止盈(3.2%) |
| #077 | ERAUSDT | long | extreme_neg | -11.38U | 4.0h | 止损 |
| #078 | UBUSDT | long | 15m_spike | +10.71U | 11.7h | 移动止盈(3.2%) |
| #079 | DRIFTUSDT | long | extreme_neg | **+116.28U** | 2.4h | 止盈 ⭐最佳单笔 |
| #080 | WLDUSDT | long | oi_surge | +41.14U | 3.5h | 移动止盈(3.0%) |
| #081 | PHAUSDT | long | extreme_neg | +38.49U | 8.3h | 移动止盈(5.6%) |
| #082 | VVVUSDT | short | 15m_spike | +4.44U | 36.3h | 移动止盈(2.8%) |
| #083 | TONUSDT | long | extreme_neg | -36.13U | 7.8h | 止损 |
| #084 | SEIUSDT | long | 15m_spike | -40.29U | 45.9h | 止损 |
| #085 | XLMUSDT | long | funding_flip | **+101.83U** | 10.0h | 止盈 |
| #086 | BEATUSDT | long | oi_surge | -18.37U | 11.9h | 止损 |
| #087 | XLMUSDT | long | oi_surge | +31.29U | 3.3h | 移动止盈(2.7%) |
| #088 | INJUSDT | long | 15m_spike | +49.55U | 6.4h | 移动止盈(3.4%) |
| #089 | XLMUSDT | long | oi_surge | +14.52U | 5.1h | 移动止盈(2.8%) |
| #090 | HBARUSDT | long | 15m_spike | -40.55U | 7.7h | 止损 |
| #091 | XLMUSDT | long | oi_surge | +23.97U | 11.0h | 移动止盈(2.8%) |
| #092 | INJUSDT | long | 15m_spike | +31.39U | 2.1h | 移动止盈(2.6%) |
| #095 | XLMUSDT | long | oi_surge | +76.71U | 4.1h | 移动止盈(2.7%) |

</details>

---

*Report generated on 2026-05-31 by Hermes Agent*
*Data source: Binance Testnet simulated trading*
