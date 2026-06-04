# G60B 回测口径冲突解决说明

## 结论

两边结果出入不是因为 BTC filter 代码本身，而是因为 **信号池和派生流程不同**。

- 官方 G60B 口径：`S07-v10 backtest_v10_result.json` 的原始逐笔交易 -> 扣手续费/滑点 -> `apply_v11_base_filter()` -> G60B prefilters -> `S22-v11j simulate()`。
- 另一个默认 v10 口径：重新跑 `backtest_v10.py` 或用不同过滤顺序得到的较低质量信号池，再套近似 G60B/BTC filter。

只要 baseline 不能复现官方 G60B 的 `363 trades / +601.27U / DD 3.31%`，就不能拿来判断官方 G60B 是否应该切换 SF。

## 官方口径的可复现检查点

运行：

```bash
python3 scripts/analyze_g60b_sf_filter.py
```

必须看到：

```text
raw_trades: 879
v11_base_after_net: 793
g60b_prefiltered_before_sim_hard_filters: 411
g60b_after_sim_hard_filters: 363
reproduction_warning: null
```

如果不是这些数，说明数据源或流程已经不是官方 G60B 口径。

## 为什么会出现 271 笔 vs 363 笔

`271 trades / +19.76U` 那套结果来自另一条路径：默认 v10 / 低质量信号池 / 近似过滤。它能说明 BTC filter 对低质量 spike long 有帮助，但不能直接推导官方高质量 G60B 的上线结论。

官方口径的逐笔数据并不在 `g60b_single_backtest_latest.json` 里，而是从 `strategies/S07-v10/data/backtest_v10_result.json` 派生出来。`g60b_single_backtest_latest.json` 只是最终指标摘要。

## band 语义

当前逻辑：

```python
if btc_ema_fast < btc_ema_slow * (1 - band):
    reject
```

所以：

- `band=0.005`：BTC EMA9 低于 EMA21 0.5% 才拒绝。
- `band=0.0075`：BTC EMA9 低于 EMA21 0.75% 才拒绝。

因此 `0.0075` 比 `0.005` **更宽松，过滤更少**，不是更严格。

## 官方口径 sweep 结果

运行：

```bash
python3 scripts/sweep_g60b_sf_band.py
```

关键结果：

| band | PnL | DD | PF | ROI/DD | removed | removed PnL |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| G60B baseline | 601.27 | 3.31% | 1.72 | 18.14 | 0 | 0 |
| 0.005 | 583.33 | 3.53% | 1.81 | 16.52 | 46 | +1.79 |
| 0.0075 | 606.68 | 2.94% | 1.80 | 20.66 | 28 | -8.73 |

官方高质量口径下，`0.0075` 只是一个小幅历史最优候选，不是强证据。

## 最终处理原则

1. 主策略仍以 G60B 为基准。
2. 不用默认 v10 口径决定 G60B/SF 上线。
3. 如果测试 SF，只测试 `band=0.0075`，并且必须 shadow 记录被过滤交易的虚拟结果。
4. 两周后根据真实 shadow PnL 决定是否切换，而不是继续争论历史近似口径。
