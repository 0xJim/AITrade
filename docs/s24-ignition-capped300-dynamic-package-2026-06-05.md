# S24-Ignition capped300 + dynamic 最终策略包

日期: 2026-06-05

## 最终选择

最终 testnet 版本：**S24-Ignition v4 capped300 + dynamic**

定位：进攻型收益增强策略，不替代 G60S 主防守。

## 核心参数

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
margin_cap      = 300U
dynamic         = true
exclude         = BUSDT,BILLUSDT,BNBUSDT,LINKUSDT,SAGAUSDT
```

## 365 天回测结果

区间：2025-05-14 ~ 2026-05-14

| 指标 | 数值 |
|---|---:|
| 交易数 | 475 |
| 胜率 | 50.3% |
| PnL | +2041.35U |
| ROI | 204.1% |
| 最大回撤 | 6.10% |
| PF | 1.6881 |
| ROI/DD | 33.44 |
| 盈利月 | 13/13 |

月度 PnL：

```text
2025-05   +33.6U
2025-06   +69.2U
2025-07  +112.9U
2025-08  +149.8U
2025-09    +6.4U
2025-10  +510.2U
2025-11  +198.7U
2025-12  +414.6U
2026-01   +22.6U
2026-02  +222.1U
2026-03   +40.1U
2026-04   +52.0U
2026-05  +321.3U
```

动态黑名单触发：

```text
Rule1 亏损率: 31 次
Rule2 低 PF: 53 次
Rule3 7天亏损: 0 次
Rule4 日熔断: 3 次
```

Top symbols：

```text
SKYAIUSDT       +586.5U
LABUSDT         +411.7U
VVVUSDT         +328.3U
TONUSDT         +238.5U
LDOUSDT         +143.4U
1000PEPEUSDT    +129.8U
INTCUSDT        +101.9U
AAVEUSDT         +86.1U
```

## 为什么选 capped300 + dynamic

capped150 更稳，但收益偏保守；capped300 在不进入纯复利风险区的情况下，拿到了最好的 ROI/DD。

| cap | ROI | DD | PF | ROI/DD | 盈利月 |
|---:|---:|---:|---:|---:|---:|
| 150 | 139.0% | 5.14% | 1.72 | 27.07 | 13/13 |
| 200 | 170.4% | 5.71% | 1.72 | 29.82 | 13/13 |
| **300** | **204.1%** | **6.10%** | **1.69** | **33.44** | **13/13** |
| 350 | 223.7% | 6.95% | 1.71 | 32.19 | 12/13 |
| 500+ / percent | 207.3% | 10.20% | 1.69 | 20.32 | 12/13 |

结论：**cap300 是风险收益最优点；超过 300 后 DD 放大，ROI/DD 下降。**

## 回测命令

```bash
python3 strategies/S24-ignition/backtest_ignition.py \
  --days 365 \
  --hour-only \
  --sym-cap 5 \
  --exclude BUSDT,BILLUSDT,BNBUSDT,LINKUSDT,SAGAUSDT \
  --margin-mode capped \
  --margin-cap 300 \
  --dynamic \
  --label v4_final_capped300_dynamic
```

## 模拟盘命令

单次扫描：

```bash
python3 strategies/S24-ignition/s24_paper_trader.py --once
```

常驻运行：

```bash
python3 strategies/S24-ignition/s24_paper_trader.py --loop --interval 60
```

或者使用启动脚本：

```bash
bash strategies/S24-ignition/run_s24_paper_capped300_dynamic.sh
```

## 模拟盘输出

```text
strategies/S24-ignition/data/s24_paper_state.json
strategies/S24-ignition/data/s24_paper_trades.jsonl
strategies/S24-ignition/data/s24_paper_decisions.jsonl
```

## Testnet 验收标准

建议至少跑 30 天，交易数 >= 45 笔。

通过条件：

```text
PF >= 1.30
去掉最大单笔后 PF >= 1.20
DD <= 10%
单 symbol PnL 占比 <= 35%
最大单笔盈利 <= 总 PnL 的 25%
不出现连续 5 笔止损
```

## 注意

`s24_paper_trader.py` 是 paper trading，不会真实下单，不需要 API key。

## 2026-06-05 打包前修复

根据代码审查，paper trader 又补齐三点：

1. 动态黑名单补齐 Rule 3：近 7 天单币亏损超过余额 3% → 冷却 14 天。
2. 交易日志新增 `exit_time_ms`，用于 Rule 3 的 7 天窗口判断。
3. 入场时序修正：如果错过下一根 15m 开盘窗口超过 90 秒，拒绝入场并写入 `decisions`，不再使用历史开盘价伪造入场。

README 已替换旧 v3 推荐数字，当前推荐以 capped300 + dynamic 为准。
