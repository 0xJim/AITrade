# S24-Ignition 部署手册

> 最后更新: 2026-06-05 UTC

---

## 1. 当前目录结构

```text
strategies/S24-ignition/
├── DEPLOY.md
├── .gitignore                         # 忽略运行时日志/状态/密钥
├── README.md
├── backtest_ignition.py               # 回测脚本
├── s24_paper_trader.py                # Paper 模拟盘脚本
├── data/                              # 回测结果 + Paper 运行状态
│   ├── ignition_*.json                # 可提交的回测结果
│   ├── s24_paper_state.json           # 运行时文件，不提交
│   ├── s24_paper_trades.jsonl         # 运行时文件，不提交
│   └── s24_paper_decisions.jsonl      # 运行时文件，不提交
├── logs/                              # stdout / 运行日志，不提交
│   ├── s24_paper.log
│   ├── s24_testnet.log
│   ├── s24_paper_decisions.jsonl
│   ├── s24_decisions.jsonl
│   ├── s24_paper_state.json
│   └── s24_state.json
├── deploy/
│   ├── s24-paper.service.example
│   └── s24-testnet.service.example
└── testnet/
    ├── s24_trader.py                  # Testnet 下单版
    ├── binance_api.py
    ├── config.py
    ├── notifier.py
    ├── .env.binance                   # 密钥文件，不提交
    └── data_S24/                      # Testnet 运行状态，不提交
```

---

## 2. Paper 与 Testnet 对比

| | Paper Trading | Testnet 下单版 |
|---|---|---|
| 脚本 | `s24_paper_trader.py` | `testnet/s24_trader.py` |
| 数据源 | 正式网 `fapi.binance.com` | 正式网拉K线 + testnet下单 |
| 下单 | 纯模拟，不下单 | Binance futures testnet MARKET单 |
| 状态文件 | `data/s24_paper_state.json` | `testnet/data_S24/s24_state.json` |
| 交易记录 | `data/s24_paper_trades.jsonl` | `testnet/data_S24/s24_trades.jsonl` |
| 决策日志 | `data/s24_paper_decisions.jsonl` | `testnet/data_S24/s24_decisions.jsonl` |
| stdout日志 | `logs/s24_paper.log` | `logs/s24_testnet.log` |
| 通知 | 无 | 微信 + Telegram/通知模块 |

---

## 3. 推荐启动方式：systemd user service

不要再用 `nohup` 作为长期运行方案；服务器重启或进程崩溃会丢失。

### Paper

```bash
mkdir -p ~/.config/systemd/user
cp strategies/S24-ignition/deploy/s24-paper.service.example ~/.config/systemd/user/s24-paper.service
systemctl --user daemon-reload
systemctl --user enable --now s24-paper.service
loginctl enable-linger "$USER"
```

### Testnet

```bash
mkdir -p ~/.config/systemd/user
cp strategies/S24-ignition/deploy/s24-testnet.service.example ~/.config/systemd/user/s24-testnet.service
systemctl --user daemon-reload
systemctl --user enable --now s24-testnet.service
loginctl enable-linger "$USER"
```

### 查看状态

```bash
systemctl --user status s24-paper.service
systemctl --user status s24-testnet.service
journalctl --user -u s24-paper.service -f
journalctl --user -u s24-testnet.service -f
```

---

## 4. 手动启动命令

### Paper 单次

```bash
cd ~/AITrade
python3 strategies/S24-ignition/s24_paper_trader.py --once
```

### Paper 循环

```bash
cd ~/AITrade
python3 strategies/S24-ignition/s24_paper_trader.py \
  --loop \
  --interval 60 \
  --max-entry-lag-sec 120
```

### Testnet 单次

```bash
cd ~/AITrade/strategies/S24-ignition/testnet
python3 s24_trader.py --once
```

### Testnet 循环

```bash
cd ~/AITrade/strategies/S24-ignition/testnet
python3 s24_trader.py \
  --loop \
  --interval 60 \
  --max-entry-lag-sec 120
```

---

## 5. 核心参数

```python
SPIKE_THRESHOLD = 0.012     # 15m涨幅 >= 1.2%
MIN_RSI = 50.0
MIN_QUALITY = 70.0
MAX_HOLD_HOURS = 4.0
TP_SL_RATIO = 2.5
LEVERAGE = 3
MAX_POSITIONS = 3
SYM_WEEKLY_CAP = 5
MARGIN_CAP = 300.0
MAX_ENTRY_LAG_SEC = 120
SCAN_INTERVAL = 60
```

最终策略：**capped300 + dynamic**。

---

## 6. 信号流程

```text
每 60 秒 run_once()
1. 加载状态
2. 检查已有持仓是否止损/止盈/4h超时
3. 刷新动态黑名单
4. 遍历 19 个可交易币种
5. 只看每小时第一根 15m K线（hour-only）
6. 15m 涨幅 >= 1.2%
7. 上一根已收盘 1h EMA9 > EMA21
8. RSI >= 50
9. quality >= 70
10. 下一根 15m 开盘后 120 秒内允许入场；超时拒绝并写 decisions
```

`interval=60` 是扫描频率；`max_entry_lag_sec=120` 是入场窗口容错。二者只影响执行，不改变策略信号逻辑。

---

## 7. 动态黑名单规则

| 规则 | 条件 | 动作 |
|---|---|---|
| Rule 1 | 近10笔亏损 >= 8笔 | 冷却 7 天 |
| Rule 2 | 近10笔 PF < 0.8 | 冷却 14 天 |
| Rule 3 | 近7天单币亏损 > 余额3% | 冷却 14 天 |
| Rule 4 | 当日总亏损 > 余额3% | 当天停止新开仓 |

Paper 与 Testnet 逻辑必须保持一致。

---

## 8. 运行时文件不要提交 Git

已新增 `strategies/S24-ignition/.gitignore`。

如果运行时文件已经被 Git 追踪，执行：

```bash
git rm --cached -r strategies/S24-ignition/logs
git rm --cached strategies/S24-ignition/data/s24_paper_state.json
git rm --cached strategies/S24-ignition/data/s24_paper_trades.jsonl
git rm --cached strategies/S24-ignition/data/s24_paper_decisions.jsonl
git rm --cached -r strategies/S24-ignition/testnet/data_S24 || true
git rm --cached strategies/S24-ignition/testnet/.env.binance || true
```

---

## 9. 已知风险与处理建议

### P0 已处理

- `.gitignore` 已忽略运行时文件。
- 运行时文件已从 Git index 移除。
- Paper 默认 `interval=60`、`max_entry_lag_sec=120`。
- Testnet 默认 `MAX_ENTRY_LAG_SEC=120`，`SCAN_INTERVAL=60`。
- 已提供 systemd service 示例，替代 nohup。

### P1 仍需继续增强

1. **Testnet STOP_MARKET -4120**  
   当前仍可能导致止损/止盈挂单失败，只能软件兜底。建议后续加入：
   - `unprotected=true`
   - 无保护仓位 15-30 秒快速检查
   - 连续挂保护单失败 N 次后市价平仓

2. **启动时同步交易所仓位**  
   建议启动时对比：
   - 本地 state
   - 交易所当前持仓
   - 交易所挂单

3. **通知链路**  
   Telegram `chat_id` 必须修正；启动时应发送 heartbeat 通知验证。

4. **Heartbeat**  
   建议每轮写入：
   - `last_seen`
   - `last_scan_symbols`
   - `open_positions`
   - `dynamic_cooldowns`
   - `unprotected_positions`

---

## 10. 监控命令

```bash
# systemd
systemctl --user status s24-paper.service
systemctl --user status s24-testnet.service

# stdout logs
tail -f strategies/S24-ignition/logs/s24_paper.log
tail -f strategies/S24-ignition/logs/s24_testnet.log

# state
cat strategies/S24-ignition/data/s24_paper_state.json | python3 -m json.tool
cat strategies/S24-ignition/testnet/data_S24/s24_state.json | python3 -m json.tool

# decisions
tail -f strategies/S24-ignition/data/s24_paper_decisions.jsonl
tail -f strategies/S24-ignition/testnet/data_S24/s24_decisions.jsonl
```

---

## 11. Testnet 验收标准

至少跑 30 天，交易数 >= 45 笔。

```text
PF >= 1.30
去掉最大单笔后 PF >= 1.20
DD <= 10%
单 symbol PnL 占比 <= 35%
最大单笔盈利 <= 总 PnL 的 25%
不出现连续 5 笔止损
```
