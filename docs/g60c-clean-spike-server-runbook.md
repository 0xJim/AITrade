# G60C Clean Spike 服务器模拟盘运行方案

更新时间: 2026-05-18

## 结论

G60C 是当前推荐放到服务器继续跑的模拟盘方案。目标不是追求最高回测收益，而是先解决 05-15 到 05-18 模拟盘暴露的问题:

- Spike 假信号太多
- 空单尾部亏损偏大
- STOP_MARKET 保护单失败时不能允许裸奔
- 固定黑名单不合理，改为按表现进入小黑屋，到期自动释放

## 运行入口

服务器上使用这个入口:

```bash
python3 trading-system/run_g60c_loop.py
```

后台运行示例:

```bash
nohup python3 trading-system/run_g60c_loop.py > trading-system/data/g60c_loop.log 2>&1 &
```

停止:

```bash
pkill -f run_g60c_loop.py
```

## 必要环境变量

在 `trading-system/.env.binance` 或 shell 环境里配置:

```bash
BINANCE_API_KEY=你的模拟盘key
BINANCE_API_SECRET=你的模拟盘secret
BINANCE_TESTNET=true
```

G60C 入口会强制设置 `BINANCE_TESTNET=true`。实盘仍受三重硬锁保护，不能误触发。

## G60C 核心参数

| 参数 | 值 | 说明 |
|---|---:|---|
| `STRATEGY_PROFILE` | `G60C` | Clean Spike 小仓灰度档 |
| `INITIAL_BALANCE` | `1000` | 模拟盘基准本金 |
| `MAX_OPEN_POSITIONS` | `3` | 最大同时持仓 |
| `POSITION_PCT` | `4` | 目标单笔仓位上限参考 |
| `V8_POSITION_PCT_MAX` | `4` | Kelly 仓位硬上限 |
| `MAX_LOSS_PER_TRADE` | `12U` | profile 内置单笔估算亏损上限 |
| `CLOSED_15M_ANOMALY_THRESHOLD_PCT` | `1.5` | 15m 收线真实异动阈值 |
| `CLOSED_15M_ANOMALY_VOLUME_RATIO_MIN` | `1.8` | 量能放大过滤 |
| `CLOSED_15M_ANOMALY_BODY_RATIO_MIN` | `0.55` | 实体占比过滤 |
| `CLOSED_15M_ANOMALY_CLOSE_POSITION_MIN` | `0.65` | 收盘位置过滤 |
| `BLACKLIST_QUARANTINE_HOURS` | `72` | 小黑屋默认隔离时间 |

## 小黑屋规则

G60C 不使用 RIVER/TON/ARC 这种硬编码临时黑名单。币种进入小黑屋必须由交易表现触发:

- 近 30 天交易笔数达到阈值，累计亏损超过阈值且胜率偏低
- 或单笔亏损超过阈值
- 到 `quarantined_until` 后自动释放

G60C 服务器入口默认更严格:

```bash
BLACKLIST_MIN_TRADES=2
BLACKLIST_MAX_LOSS_USD=18
BLACKLIST_MAX_WIN_RATE=0.45
BLACKLIST_SINGLE_LOSS_USD=10
BLACKLIST_QUARANTINE_HOURS=72
```

## 回测结果

严格回测口径:

- 15m 已收线信号
- 下一根 15m open 入场
- 手续费和滑点计入
- 多空双向
- 1000U fixed basis

| 区间 | 版本 | 信号 | 交易 | 胜率 | PnL | PF | 最大回撤 |
|---|---|---:|---:|---:|---:|---:|---:|
| 365天 | 旧规则 | 4572 | 1783 | 46.38% | +898.67U | 1.32 | 26.10% |
| 365天 | G60C Clean Spike | 1832 | 1039 | 47.93% | +1052.34U | 1.59 | 17.86% |
| 60天 | 旧规则 | 930 | 353 | 47.88% | +442.62U | 1.72 | 7.35% |
| 60天 | G60C Clean Spike | 361 | 223 | 54.26% | +537.02U | 2.47 | 6.10% |
| 14天 | 旧规则 | 259 | 100 | 51.00% | +309.35U | 2.47 | 5.81% |
| 14天 | G60C Clean Spike | 129 | 69 | 63.77% | +425.47U | 5.47 | 3.64% |

注意: 这组 strict 回测窗口截至 2026-05-14 10:00 BJT，没有覆盖 05-15 到 05-18 的模拟盘实盘表现。因此服务器跑 G60C 后仍需要至少 3 天灰度观察。

## 3 天验收标准

继续跑的条件:

- 胜率高于 45%
- PF 高于 1.1
- 最大单亏尽量压在 12U 附近
- 不再出现无保护单裸奔
- 3 天累计亏损不超过 -1.5%

暂停并复盘的条件:

- STOP_MARKET 仍持续失败
- 连续 3 笔亏损后仍频繁开新仓
- 单日亏损超过 -1.5%
- 动态小黑屋没有生效

## 服务器检查命令

```bash
python3 -m py_compile trading-system/config.py trading-system/cron_scan.py trading-system/binance_api.py trading-system/run_g60c_loop.py
tail -f trading-system/data/g60c_loop.log
tail -f trading-system/data/scanner.log
cat trading-system/data/dynamic_blacklist.json
```
