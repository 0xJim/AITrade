# G60C Clean Spike 文件清单与运行计划

更新时间: 2026-05-19

## 一句话结论

G60C Clean Spike 是当前推荐继续上服务器跑的模拟盘方案。服务器只需要拉取 `main` 分支后启动:

```bash
python3 trading-system/run_g60c_loop.py
```

它会强制使用 testnet，并覆盖为 G60C 参数，不依赖手工设置 `STRATEGY_PROFILE`。

## 必带运行文件

这些文件是服务器跑 G60C 必须有的:

| 文件 | 作用 |
|---|---|
| `trading-system/run_g60c_loop.py` | G60C 服务器启动入口，循环调用 `cron_scan.py` |
| `trading-system/cron_scan.py` | 主扫描、候选池、深度过滤、下单、止损、动态小黑屋 |
| `trading-system/config.py` | G60C profile、风控参数、Binance endpoint、安全锁 |
| `trading-system/binance_api.py` | Binance 行情、账户、下单、STOP_MARKET 保护单 |
| `trading-system/notifier.py` | 开仓/平仓/告警通知 |
| `trading-system/review_db.py` | 交易复盘数据库同步 |

运行时会自动生成或读取:

| 路径 | 作用 |
|---|---|
| `trading-system/.env.binance` | Binance 模拟盘 API key/secret，本地私密文件，不提交 Git |
| `trading-system/data/trades.json` | 本地交易记录 |
| `trading-system/data/scanner_state.json` | 扫描状态和冷却状态 |
| `trading-system/data/dynamic_blacklist.json` | 动态小黑屋 |
| `trading-system/data/g60c_loop.log` | G60C 循环日志 |
| `trading-system/data/scanner.log` | 扫描和交易日志 |

## G60C 关键代码位置

| 位置 | 内容 |
|---|---|
| `run_g60c_loop.py` | 设置 `STRATEGY_PROFILE=G60C`、`BINANCE_TESTNET=true`、15m Clean Spike 参数和小黑屋参数 |
| `config.py` | `STRATEGY_PROFILES["G60C"]`: 单笔估算亏损上限 12U、仓位上限 4%、空单更保守 |
| `cron_scan.py:update_dynamic_blacklist` | 按亏损表现进入小黑屋，到期释放 |
| `cron_scan.py:build_closed_15m_spike_candidate` | 15m 收线异动 + 量能 + 实体 + 收盘位置过滤 |
| `cron_scan.py:v8_kelly_position` | 使用 `V8_POSITION_PCT_MAX` 限制 Kelly 仓位 |
| `cron_scan.py:handle_protective_stop_failure` | STOP_MARKET 保护单失败后立即尝试平仓，避免裸奔 |
| `binance_api.py:place_stop_loss_order` | reduceOnly STOP_MARKET，使用 `MARK_PRICE` |

## 验证与回测文件

这些文件不是服务器持续运行的必要文件，但用于验证策略质量:

| 文件 | 作用 |
|---|---|
| `strategies/S22-spike-v13/backtest_spike_v13_strict.py` | 严格 Spike 回测脚本，支持 Clean Spike 参数和 `--allow-shorts` |
| `tests/test_protective_stop_orders.py` | 单测: 保护止损、小黑屋、Clean Spike 过滤、G60C profile |
| `docs/g60c-clean-spike-server-runbook.md` | 服务器运行方案、参数、回测结果、验收标准 |
| `docs/g60c-clean-spike-file-index.md` | 本文件，文件索引和交接清单 |

本地未提交的大型回测 JSON 和缓存不需要上传服务器:

| 路径 | 说明 |
|---|---|
| `data/strict_spike_v13_cache/` | Binance K线缓存，服务器运行不需要 |
| `strategies/S22-spike-v13/data/*.json` | 逐笔回测结果，runbook 已摘录关键指标 |

## 策略参数

G60C 默认参数由 `run_g60c_loop.py` 注入:

```bash
STRATEGY_PROFILE=G60C
BINANCE_TESTNET=true
INITIAL_BALANCE=1000
MAX_OPEN_POSITIONS=3
POSITION_PCT=4
V8_POSITION_PCT_MAX=4
CLOSED_15M_ANOMALY_ENABLED=true
CLOSED_15M_ANOMALY_THRESHOLD_PCT=1.5
CLOSED_15M_ANOMALY_VOLUME_RATIO_MIN=1.8
CLOSED_15M_ANOMALY_BODY_RATIO_MIN=0.55
CLOSED_15M_ANOMALY_CLOSE_POSITION_MIN=0.65
BLACKLIST_MIN_TRADES=2
BLACKLIST_MAX_LOSS_USD=18
BLACKLIST_MAX_WIN_RATE=0.45
BLACKLIST_SINGLE_LOSS_USD=10
BLACKLIST_QUARANTINE_HOURS=72
BLACKLIST_SHORT_V8_SCORE_THRESHOLD=4
BLACKLIST_SHORT_POSITION_FACTOR=0.35
```

G60C profile 内置:

```text
MAX_LOSS_PER_TRADE=12U
V11I_MAX_SL_PCT=6.5%
V11I_MAX_ATR_PCT=4.0%
V8_SIGNAL_QUALITY_MIN=85
MTF_AGREE_MIN=4
V11I_SHORT_V8_THRESHOLD=4
V11I_SHORT_V8_MULT=0.35
```

## 服务器启动计划

1. 拉取代码:

```bash
cd ~/AITrade || cd /root/AITrade || cd /opt/AITrade
git pull origin main
```

2. 确认模拟盘 API:

```bash
cat > trading-system/.env.binance <<'EOF'
BINANCE_API_KEY=你的模拟盘key
BINANCE_API_SECRET=你的模拟盘secret
BINANCE_TESTNET=true
EOF
chmod 600 trading-system/.env.binance
```

3. 编译检查:

```bash
python3 -m py_compile trading-system/config.py trading-system/cron_scan.py trading-system/binance_api.py trading-system/run_g60c_loop.py
```

4. 停掉旧循环:

```bash
pkill -f run_g60b_loop.py || true
pkill -f run_g60c_loop.py || true
pkill -f cron_scan.py || true
```

5. 启动 G60C:

```bash
mkdir -p trading-system/data
nohup python3 trading-system/run_g60c_loop.py > trading-system/data/g60c_loop.log 2>&1 &
```

6. 确认运行:

```bash
ps aux | grep -E 'run_g60c|cron_scan' | grep -v grep
tail -80 trading-system/data/g60c_loop.log
tail -80 trading-system/data/scanner.log
```

## 3 天观察计划

继续跑的条件:

- 胜率高于 45%
- PF 高于 1.1
- 最大单亏尽量压在 12U 附近
- 不再出现无保护单裸奔
- 3 天累计亏损不超过 -1.5%
- 动态小黑屋有正常写入和释放

暂停复盘的条件:

- STOP_MARKET 仍持续失败
- 连续 3 笔亏损后仍频繁开新仓
- 单日亏损超过 -1.5%
- 最大单亏明显超过预期
- `dynamic_blacklist.json` 没有按亏损表现隔离币种

## 回测摘要

严格回测口径: 1000U fixed basis、3x、多空双向、15m 已收线信号、下一根 15m open 入场、手续费和滑点计入。

| 区间 | 版本 | 信号 | 交易 | 胜率 | PnL | PF | 最大回撤 |
|---|---|---:|---:|---:|---:|---:|---:|
| 365天 | 旧规则 | 4572 | 1783 | 46.38% | +898.67U | 1.32 | 26.10% |
| 365天 | G60C Clean Spike | 1832 | 1039 | 47.93% | +1052.34U | 1.59 | 17.86% |
| 60天 | 旧规则 | 930 | 353 | 47.88% | +442.62U | 1.72 | 7.35% |
| 60天 | G60C Clean Spike | 361 | 223 | 54.26% | +537.02U | 2.47 | 6.10% |
| 14天 | 旧规则 | 259 | 100 | 51.00% | +309.35U | 2.47 | 5.81% |
| 14天 | G60C Clean Spike | 129 | 69 | 63.77% | +425.47U | 5.47 | 3.64% |

注意: 回测窗口截至 2026-05-14 10:00 BJT，没有覆盖 05-15 到 05-18 的模拟盘亏损窗口，所以服务器仍需 3 天灰度验证。

