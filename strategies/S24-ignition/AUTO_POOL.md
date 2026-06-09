# S24 自动动态池 v1

目标：自动发现新点火币，并通过 Watch → Probe → LowRisk → Active 分层放大仓位，避免人工维护死列表。

## Pool 层级

| Tier | 是否交易 | margin_cap | symbol_daily_cap | 作用 |
|---|---:|---:|---:|---|
| Watch | 否 | 0U | 0 | 影子观察 / Probe 等候室 |
| Probe | 是 | 30U | 1 | 新热点极小仓试探 |
| LowRisk | 是 | 100U | 1 | 小仓验证 |
| Active | 是 | 300U | 2 | 正常交易 |
| Quarantine | 否 | 0U | 0 | 冷却隔离 |

Active 额外规则：当天该 symbol 第一笔亏损后，当天不再开第二笔。

## 自动升降级规则

### Candidate → Watch

来自 `screen_symbols.py` 的候选满足任一：

- `S24 score >= 45`
- `S24 score >= 35` 且 `external_score >= 15`（预留 Binance Skills 加分）

### Watch → Probe

- Probe 槽未满（默认最多 3 个）
- `final_score >= 45`
- `hr_spikes >= 8`

如果 Probe 满了，继续留在 Watch。

### Probe → LowRisk

- `probe_trades >= 5`
- `PF >= 1.25`
- `WR >= 45%`

Probe 连续 2 笔亏损不进 Quarantine，只回 Watch 冷却 3 天。

### LowRisk → Active

- LowRisk 运行至少 5 天
- 真实/模拟交易至少 5 笔
- `PF >= 1.20`
- `DD <= 5%`
- Active 未满

### Active / LowRisk → Quarantine

任一触发：

- 近 30 天 PF < 0.8
- 近 30 天亏损 > balance * 5%
- 最近 8 笔亏 >= 5 笔
- 当日亏损 > balance * 3%

### Quarantine → Watch

- 冷却满 14 天
- 且重新有活跃 spike（最近指标 `hr_spikes >= 2`）

不满足则 review_after 延后 7 天。

## 文件

- `screen_symbols.py`：每天服务器全量发现候选。
- `s24_auto_pool_manager.py`：每 15 分钟维护 Pool 状态。
- `s24_pool_state.json`：唯一 Pool 状态源。
- `s24_paper_trader.py --use-pool-state`：按 Pool tier 交易。
- `testnet/s24_trader.py --use-pool-state`：按 Pool tier 交易。

## 推荐运行

### 1. 每天全量候选发现（服务器跑）

```bash
cd ~/AITrade/strategies/S24-ignition
python3 screen_symbols.py --fetch-new --resume --delay 0.5 --min-volume 20000000
```

### 2. 每 15 分钟自动维护 Pool

先 dry-run：

```bash
python3 s24_auto_pool_manager.py --dry-run
```

确认后正式写状态：

```bash
python3 s24_auto_pool_manager.py --apply
```

### 3. 交易器读取 Pool 状态

Paper：

```bash
python3 s24_paper_trader.py --loop --interval 60 --use-pool-state
```

Testnet：

```bash
cd testnet
python3 s24_trader.py --loop --interval 60 --use-pool-state
```

不加 `--use-pool-state` 时，交易器保持旧逻辑，不受自动池影响。


## 余额参数注意

` s24_auto_pool_manager.py` 的降级阈值会用账户余额计算：

- 近30天亏损 > balance * 5%
- 单日亏损 > balance * 3%

所以正式运行时请传入当前账户真实余额，例如：

```bash
python3 s24_auto_pool_manager.py --apply --balance 5000
```

如果不传，默认按 5000U 计算。
