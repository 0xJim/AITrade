# S24-Ignition v4 最终优化版

日期: 2026-06-05

## 修复内容

本版针对 S24 审查发现的问题做了四项修复/增强：

1. `signal_quality()` 改为使用当前 `--threshold`，避免 threshold sweep 失真。
2. ATR 计算排除信号当根 spike K 线，只用信号前历史波动。
3. `--tp-ratio` 参数真正参与信号止盈计算。
4. 新增仓位口径：
   - `percent`: 按余额百分比复利仓位。
   - `fixed`: 固定 70U / 100U / 150U 保证金。
   - `capped`: 按余额百分比，但单笔保证金封顶。

## 最终推荐

生产/testnet 推荐使用 **capped150**，不是纯复利版。

```bash
python3 strategies/S24-ignition/backtest_ignition.py \
  --days 365 \
  --hour-only \
  --sym-cap 5 \
  --exclude BUSDT,BILLUSDT,BNBUSDT,LINKUSDT,SAGAUSDT \
  --margin-mode capped \
  --margin-cap 150 \
  --label v4_houronly_ex5_cap5_capped150
```

## 365 天回测对比

区间: 2025-05-14 ~ 2026-05-14  
初始资金: 1000U  
手续费: 0.04%  
滑点: 0.05%  
杠杆: 3x

| 版本 | 交易 | WR | ROI | DD | PF | ROI/DD | 盈利月 | 最大单笔盈利 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| percent 复利 | 633 | 48.0% | 251.8% | 11.98% | 1.48 | 21.01 | 11/13 | 262.6U |
| fixed 固定保证金 | 633 | 48.0% | 132.4% | 8.07% | 1.60 | 16.40 | 12/13 | 100.4U |
| capped200 | 633 | 48.0% | 190.9% | 10.28% | 1.51 | 18.57 | 12/13 | 133.9U |
| **capped150 推荐** | **633** | **48.0%** | **160.5%** | **8.97%** | **1.54** | **17.89** | **12/13** | **100.8U** |
| capped120 | 633 | 48.0% | 133.2% | 8.12% | 1.54 | 16.41 | 12/13 | 80.6U |

## 为什么选 capped150

纯复利版 ROI 最高，但后期仓位膨胀明显，收益数字容易被末期大仓放大。

fixed 固定保证金最干净，但过于保守，无法模拟实盘中小幅放大仓位的情况。

**capped150 是折中最优：**

- ROI 仍有 160.5%。
- DD 控制在 8.97%。
- PF 1.54。
- 12/13 盈利月。
- 最大单笔盈利约 100U，不再依赖 200U+ 单笔大单。

## 最终参数

```text
threshold       = 1.2%
quality         >= 70
RSI             >= 50
hour_only       = true
max_hold        = 4h
tp/sl           = 2.5
leverage        = 3
sym_weekly_cap  = 5
margin_mode     = capped
margin_cap      = 150U
exclude         = BUSDT,BILLUSDT,BNBUSDT,LINKUSDT,SAGAUSDT
```

## 仍需注意

S24 仍然是进攻策略，不是防守主策略。它依赖高动量币种，且固定排除列表存在一定历史拟合风险。

建议定位：

- G60S: 主防守策略。
- S24 v4 capped150: 收益增强策略，小仓/testnet 先跑。

## Testnet 验收线

跑 2 周后若满足以下条件，再考虑放大：

- PF >= 1.35
- DD <= 10%
- 最大单笔盈利不超过总 PnL 的 25%
- 单 symbol PnL 占比不超过 35%
- 实际信号频率接近回测，不出现连续空窗或过密交易
