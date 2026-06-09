# S24 Watch Pool 发现流程

目标：让 S24 的标的池变成“活的”，先发现新点火币，进入 Watch Pool 观察，再由人工每周决定是否加入 Active Pool。

## 当前原则

- Active Trader 仍然只扫 Active Pool，保持 60s 入场节奏。
- Watch Pool 扫描器必须独立运行，不和实盘交易循环共用进程。
- Watch 扫描只给建议，不自动下单、不自动 promote。
- 人工每周审阅一次报告，再改 `s24_pool_state.json`。

## 服务器首次全量扫描

```bash
cd ~/AITrade/strategies/S24-ignition
python3 screen_symbols.py --fetch-new --resume --delay 0.5 --min-volume 20000000
```

说明：

- `--fetch-new`：允许访问 Binance，拉取 exchangeInfo、24h ticker 和 K 线。
- `--resume`：中断后续跑，已算过的 symbol 不重复计算。
- 默认使用 exchangeInfo 自动发现所有 USDT 永续，不再依赖静态 CANDIDATE_POOL。
- 默认不会把候选写进 Active Pool，只输出 `data/s24_symbol_candidates.json`。

## 无网络 / 本地 dry-run

```bash
python3 screen_symbols.py --static-pool --max-symbols 20 --output /tmp/s24_candidates_test.json
```

## 输出字段

`data/s24_symbol_candidates.json` 里重点看：

- `universe_size`：本次发现的可扫描合约数。
- `passed_candidates`：通过筛选的完整排序。
- `watch_recommended`：建议加入 Watch Pool 的新候选列表。
- `watch_pool_patch`：可复制到 `s24_pool_state.json -> watch_pool` 的字典。
- `rejected`：计算过但未通过筛选的 symbol。

## 每周人工流程

1. 运行全量/增量筛选脚本。
2. 查看 `watch_recommended`。
3. 把认可的新币复制到 `s24_pool_state.json` 的 `watch_pool`。
4. 用 `s24_pool_scanner.py` 每周复盘 Active/Watch 表现。
5. 满足毕业条件后再手动放入 Active Pool。

## 建议毕业/降级规则

Watch → Active（同时满足）：

- 观察期 >= 45 天
- hour-only 信号 >= 15 笔
- WR >= 48%
- PF >= 1.25
- 假突破率 <= 40%

Active → Quarantine（任一触发）：

- 近 30 天 PF < 0.8
- 近 30 天亏损 > 5% 余额
- 近 30 天信号数 < 5

## 自动池管理器

候选生成后，用 manager 自动维护 Watch/Probe/LowRisk/Active/Quarantine：

```bash
python3 s24_auto_pool_manager.py --dry-run
python3 s24_auto_pool_manager.py --apply
```

交易器如需启用动态池，必须显式加：

```bash
--use-pool-state
```

不加该参数时，交易器继续使用原静态 COMMON_SYMBOLS，不会被自动池影响。


## 运行顺序要求

Quarantine 复查依赖 `data/s24_symbol_candidates.json` 里的最新 `hr_spikes`。

因此建议顺序固定为：

```bash
# 1. 先刷新候选/活跃度数据
python3 screen_symbols.py --fetch-new --resume --delay 0.5 --min-volume 20000000

# 2. 再执行自动池管理
python3 s24_auto_pool_manager.py --apply --balance <当前账户余额>
```

如果没有先跑 `screen_symbols.py`，Quarantine 里的币不会误恢复，只会因缺少活跃度数据而延后复查。
