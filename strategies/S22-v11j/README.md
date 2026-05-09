# S22 - v11j Profile 策略系统

## 策略定位

v11j 不是"收益增强"策略，而是**风控底座 + Profile化参数切换系统**。

### 四个 Profile

| Profile | 定位 | MAX_LOSS | CONSEC_MULT | MAX_SL | 说明 |
|---------|------|----------|-------------|--------|------|
| **M40** | 保守安全挡 | $40 | ×0.7 | 10% | testnet冷启动/小资金，风控底座 |
| **G60** | 下一阶段主测 ★ | $60 | ×0.5 | 10% | 收益弹性+风控平衡，优先testnet验证 |
| **D60** | 对照组 | $60 | ×0.7 | 10% | 验证连亏×0.5是否真实贡献 |
| **L7** | 研究基准 | 无硬帽 | ×0.7 | 7% | SL过滤因子研究，不直接裸上 |

### 核心原则

1. **M40 保命，G60 主测，L7 拆因子，D60 做对照**
2. 先 testnet 验证执行质量，再谈实盘
3. 单笔风险用 `est_risk = position_usd × signal_sl_pct × leverage × mult` 估算，不看 pnl 正负
4. 开仓后必须挂交易所级 STOP_MARKET reduceOnly 止损单
5. 平仓后取消遗留挂单

## 配置切换

在 `trading-system/config.py` 中修改：

```python
STRATEGY_PROFILE = "M40"  # 可选: M40, G60, D60, L7
```

默认 M40（保守），下一轮 testnet 主测 G60。

## 文件说明

| 文件 | 说明 |
|------|------|
| `backtest_all_optimizations.py` | 四组 Profile 对比回测，含关键指标 |
| `backtest_v11j_compare.py` | 1年 vs 全期对比 |
| `data/backtest_v10_result.json` | v10 原始535笔交易数据 |

## 运行回测

```bash
# 四组 Profile 对比
python3 strategies/S22-v11j/backtest_all_optimizations.py

# 1年 vs 全期
python3 strategies/S22-v11j/backtest_v11j_compare.py
```

## Testnet 运行标准

先跑 G60，周期至少 7 天。每天检查：

1. 是否每笔开仓后成功挂 STOP_MARKET reduceOnly
2. 实际最大单笔亏损是否接近 $60，不能频繁滑到 $70+
3. 连亏后仓位是否真实下降
4. scan_decisions 是否记录拒绝原因
5. trades.json 里 signal_sl_pct_raw / signal_sl_pct_percent 是否正确
6. 真实成交价和回测假设偏差是否过大

## 执行路线

1. ~~保留 M40 当前安全底座~~ ✅
2. ~~做 profile 化，不要硬改单一参数~~ ✅
3. ~~本地重跑 M40 / D60 / G60 / L7~~ → 运行回测脚本
4. testnet 切到 G60 跑 7 天
5. 如果 G60 实盘滑点可控，再考虑小资金
6. 如果 G60 单笔风险经常超 $60，退回 M40 或做 $50 中间挡
7. 如果 L7 表现稳定，再研究 "L7 + $60 风险帽" 新组合
