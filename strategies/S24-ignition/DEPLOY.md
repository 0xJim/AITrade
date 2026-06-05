# S24-Ignition 模拟盘部署手册

> 最后更新: 2026-06-06 00:45 CST

---

## 1. 架构总览

```
┌─────────────────────────────────────────────────────┐
│                    服务器 (VPS)                       │
│                                                      │
│  ┌──────────────────────┐  ┌──────────────────────┐  │
│  │  Paper Trading 版     │  │  Testnet 下单版       │  │
│  │  s24_paper_trader.py  │  │  s24_trader.py        │  │
│  │  PID: 2586396         │  │  PID: 2587794         │  │
│  │  每2分钟循环           │  │  每2分钟循环           │  │
│  └──────┬───────────────┘  └──────┬───────────────┘  │
│         │                         │                   │
│         │  数据源(正式网)          │  数据源(正式网)    │
│         │  fapi.binance.com       │  fapi.binance.com │
│         │                         │  下单(测试网)      │
│         │                         │  testnet.binance   │
│         │                         │  future.com        │
│         │                         │                   │
│         ▼                         ▼                   │
│  ┌─────────────┐         ┌──────────────┐            │
│  │ data/       │         │ data_S24/    │            │
│  │  s24_paper_ │         │  s24_state   │            │
│  │  state.json │         │  .json       │            │
│  │  s24_paper_ │         │  s24_trades  │            │
│  │  trades     │         │  .jsonl      │            │
│  │  .jsonl     │         │  s24_        │            │
│  │  s24_paper_ │         │  decisions   │            │
│  │  decisions  │         │  .jsonl      │            │
│  │  .jsonl     │         └──────────────┘            │
│  └─────────────┘                  │                  │
│                                   │                  │
│                          挂单失败/开仓/平仓            │
│                                   │                  │
│                                   ▼                  │
│                          ┌──────────────┐            │
│                          │ notifier.py   │            │
│                          │ hermes send   │            │
│                          │ → 微信+TG     │            │
│                          └──────────────┘            │
└─────────────────────────────────────────────────────┘
```

---

## 2. 两个版本对比

| | Paper Trading | Testnet 下单版 |
|---|---|---|
| **脚本** | `s24_paper_trader.py` (445行) | `s24_trader.py` (581行) |
| **路径** | `~/AITrade/strategies/S24-ignition/` | `~/.hermes/trading/run_S24/` |
| **数据源** | 正式网 `fapi.binance.com` | 正式网拉K线 + testnet下单 |
| **下单** | 纯模拟，不下真单 | testnet真实MARKET单 |
| **止损止盈** | 软件模拟检查 | testnet挂单（目前-4120失败，软件兜底） |
| **状态文件** | `data/s24_paper_state.json` | `data_S24/s24_state.json` |
| **交易记录** | `data/s24_paper_trades.jsonl` | `data_S24/s24_trades.jsonl` |
| **决策日志** | `data/s24_paper_decisions.jsonl` | `data_S24/s24_decisions.jsonl` |
| **通知** | 无 | 微信+Telegram（hermes send） |

---

## 3. 启动方式

### 当前方式：nohup后台进程

```bash
# Paper Trading 版
cd ~/AITrade
nohup /home/ubuntu/.hermes/hermes-agent/venv/bin/python3 \
  strategies/S24-ignition/s24_paper_trader.py \
  --loop --interval 120 \
  >> /tmp/s24_paper.log 2>&1 &

# Testnet 下单版
cd ~/.hermes/trading/run_S24
nohup /home/ubuntu/.hermes/hermes-agent/venv/bin/python3 \
  s24_trader.py \
  --loop --interval 120 \
  >> /tmp/s24_testnet.log 2>&1 &
```

### ⚠️ 问题：nohup不稳定

服务器重启后进程会丢失。之前尝试用systemd timer（用户自行配置的），但当前实际跑的是nohup。
需要迁移到systemd service或supervisor。

---

## 4. 文件清单

### Paper Trading 版 (~/AITrade/strategies/S24-ignition/)

```
strategies/S24-ignition/
├── s24_paper_trader.py    # 主脚本（仓库原版+优化）
├── backtest_ignition.py   # 回测脚本
├── data/                  # 运行时数据
│   ├── s24_paper_state.json
│   ├── s24_paper_trades.jsonl
│   └── s24_paper_decisions.jsonl
└── README.md
```

### Testnet 下单版 (~/.hermes/trading/run_S24/)

```
run_S24/
├── s24_trader.py          # 主脚本（基于paper版改造）
├── binance_api.py         # 币安API层（数据+交易）
├── config.py              # 配置（路径、API密钥、参数）
├── notifier.py            # 通知模块（hermes send → 微信+TG）
├── .env.binance           # API密钥（权限600）
└── data_S24/              # 运行时数据
    ├── s24_state.json
    ├── s24_trades.jsonl
    └── s24_decisions.jsonl
```

---

## 5. 配置详情

### 交易参数

```python
INITIAL_BALANCE = 1000.0    # 初始本金 1000 USDT
LEVERAGE = 3.0              # 杠杆 3x
FEE_RATE = 0.0004           # 手续费率 0.04%
SLIPPAGE_RATE = 0.0005      # 滑点 0.05%
MAX_POSITIONS = 3           # 最大同时持仓 3
COOLDOWN_HOURS = 4          # 同币种冷却 4 小时

SPIKE_THRESHOLD = 0.012     # 涨幅阈值 1.2%
MIN_RSI = 50.0              # 最低RSI 50
MIN_QUALITY = 70.0          # 最低质量分 70
ATR_SL_MULT = 1.5           # ATR止损倍数
MIN_SL_PCT = 0.030          # 最小止损 3%
MAX_SL_PCT = 0.090          # 最大止损 9%
TP_SL_RATIO = 2.5           # 止盈/止损比 2.5:1
MAX_HOLD_HOURS = 4.0        # 最长持仓 4 小时
GRACE_HOURS = 0.5           # 止损宽限期 0.5 小时

SYM_WEEKLY_CAP = 5          # 每币每周最多 5 次
MARGIN_CAP = 300.0          # 单仓最大保证金 300U

POSITION_PCT_LOW = 0.07     # 质量<80: 7% 余额
POSITION_PCT_MID = 0.10     # 质量80-89: 10%
POSITION_PCT_HIGH = 0.15    # 质量≥90: 15%
```

### 仓位计算

```
保证金 = min(余额 × 仓位比例, MARGIN_CAP, 余额)
名义价值 = 保证金 × 杠杆(3x)
数量 = 名义价值 / 入场价
```

### 币种列表

```python
COMMON_SYMBOLS = [
    "XAGUSDT","XAUUSDT","LABUSDT","SUIUSDT","XRPUSDT","BUSDT","CRCLUSDT",
    "BILLUSDT","BNBUSDT","SNDKUSDT","TONUSDT","GTCUSDT","1000PEPEUSDT",
    "SKYAIUSDT","VVVUSDT","SAGAUSDT","MUUSDT","ADAUSDT","INTCUSDT","LDOUSDT",
    "AVAXUSDT","LINKUSDT","PAXGUSDT","AAVEUSDT",
]  # 24个

DEFAULT_EXCLUDE = {"BUSDT", "BILLUSDT", "BNBUSDT", "LINKUSDT", "SAGAUSDT"}  # 排除5个

# 实际交易: 19个币种
```

---

## 6. 信号触发流程

```
每2分钟执行一次 run_once()

1. 加载状态 (s24_state.json / s24_paper_state.json)
2. 获取活跃币种 (24 - 5排除 = 19)
3. 遍历每个持仓 → 检查是否触发止损/止盈/超时 → 平仓
4. 遍历每个币种 → 检查冷却期/动态黑名单 → 扫描信号

scan_symbol() 信号扫描:
  ┌─ 获取15m K线 (96根, ~24小时)
  ├─ hour_only? 只看 XX:00 的15m蜡烛
  ├─ 涨幅 ≥ 1.2% ?
  ├─ 获取1h K线 (48根), EMA9 > EMA21 (多头) ?
  ├─ RSI ≥ 50 ?
  ├─ ATR止损计算 (3%~9%)
  ├─ 质量分 ≥ 70 ?
  ├─ 入场窗口 ≤ 90秒 ? (整点信号 → 下一个15m开盘)
  └─ 获取入场蜡烛的开盘价 → 返回候选

open_position() / open_position_live():
  ┌─ 计算保证金和数量
  ├─ [Testnet版] 设置杠杆, 发送MARKET多单
  ├─ [Testnet版] 挂STOP_MARKET止损 → 失败则软件兜底
  ├─ [Testnet版] 挂TAKE_PROFIT_MARKET止盈 → 失败则软件兜底
  ├─ [Testnet版] 失败时推送微信通知
  └─ 保存到状态文件
```

---

## 7. 通知机制

### 通知方式

```python
# notifier.py → 调用 hermes send CLI
def _hermes_send(text: str, target: str) -> bool:
    subprocess.run(
        ["hermes", "send", "-t", target, text],
        timeout=30, capture_output=True
    )
```

### 通知触发点

| 事件 | Paper版 | Testnet版 |
|---|---|---|
| 开仓成功 | ❌ 不通知 | ✅ 微信+TG |
| 平仓（止损/止盈/超时） | ❌ 不通知 | ✅ 微信+TG |
| 止损挂单失败 | N/A | ✅ 微信+TG |
| 止盈挂单失败 | N/A | ✅ 微信+TG |
| 开仓失败 | N/A | ✅ 微信+TG |
| 扫描异常 | ❌ 只写日志 | ✅ 写日志 |

### 通知内容示例

**开仓通知:**
```
📈 S24 开仓 SKYAIUSDT
时间: 2026-06-05 22:16
入场: 0.1234 | 质量: 77.1 | RSI: 74.51
涨幅: +1.68% | 仓位: 70U (3x)
止损: 0.1197 (3.0%) | 止盈: 0.1326 (7.5%)
```

**挂单失败通知:**
```
⚠️ SKYAIUSDT 开仓成功但挂单部分失败
止损: ❌ 软件兜底
止盈: ❌ 软件兜底
请关注！
```

**平仓通知:**
```
📉 S24 平仓 SKYAIUSDT
原因: take_profit
入场: 0.1234 → 出场: 0.1326
PnL: +6.78U (+5.5%) | 余额: 1006.78U
持仓时间: 2.3h
```

---

## 8. API优化（防封禁）

### 改动

| 项目 | 原值 | 新值 |
|---|---|---|
| 请求间延迟 | 0ms | 200ms |
| 15m K线 limit | 160 (权重2) | 96 (权重1) |
| 1h K线 limit | 80 (权重2) | 48 (权重1) |
| 扫描间隔 | 60秒 | 120秒 |

### 效果

```
之前: 19币 × 2周期 = 38请求/2秒 = 1140 req/min瞬时 → 被封
现在: 19币 × 2周期 = 38请求/8秒 + 120秒间隔 = ~19 req/min → 安全
```

### 数据充分性

- 96根15m = 24小时数据 → 足够EMA21+RSI14+ATR14
- 48根1h = 2天数据 → 足够EMA21计算

---

## 9. 已知问题

### 🔴 严重

| # | 问题 | 影响 | 状态 |
|---|---|---|---|
| 1 | testnet不支持STOP_MARKET(-4120) | 止损止盈只能靠软件每2分钟检查，极端行情可能滞后 | ⚠️ 软件兜底 |
| 2 | entry_price=0 | testnet MARKET单返回avgPrice="0.00" | ✅ 已修(fallback) |
| 3 | nohup进程不稳定 | 服务器重启后进程丢失 | ❌ 未修 |

### 🟡 一般

| # | 问题 | 影响 | 状态 |
|---|---|---|---|
| 4 | Telegram chat_id配置错误 | 配的是"hermes gateway start"而非数字ID，TG通知发不出 | ❌ 未修 |
| 5 | Paper版无通知 | 开仓/平仓静默，只能看日志 | 设计如此 |
| 6 | 数据源和下单源不一致 | 正式网价格 vs testnet价格有~0.3%偏差 | 已知 |

### 🟢 已修复

| # | 问题 | 修复方案 |
|---|---|---|
| 7 | testnet IP反复被封 | 200ms延迟 + limit减小 + 2分钟间隔 |
| 8 | 5分钟间隔错过信号 | 改为2分钟 |
| 9 | 挂单失败不通知 | 加微信推送 |
| 10 | M40遗留仓位无保护 | 已手动平仓 |

---

## 10. 监控方式

```bash
# 查看进程
ps aux | grep "s24.*trader" | grep python | grep -v grep

# Paper版日志
tail -f /tmp/s24_paper.log

# Testnet版日志
tail -f /tmp/s24_testnet.log

# Paper版状态
cat ~/AITrade/strategies/S24-ignition/data/s24_paper_state.json | python3 -m json.tool

# Testnet版状态
cat ~/.hermes/trading/run_S24/data_S24/s24_state.json | python3 -m json.tool

# Paper版决策日志
cat ~/AITrade/strategies/S24-ignition/data/s24_paper_decisions.jsonl

# Testnet版决策日志
cat ~/.hermes/trading/run_S24/data_S24/s24_decisions.jsonl

# 手动单次运行 (Paper)
cd ~/AITrade && python3 strategies/S24-ignition/s24_paper_trader.py --once

# 手动单次运行 (Testnet)
cd ~/.hermes/trading/run_S24 && python3 s24_trader.py --once
```

---

## 11. M40 遗留仓位处理

2026-06-05 21:36 已手动平仓：

| 币种 | 方向 | PnL | 状态 |
|---|---|---|---|
| FILUSDT | 空 | +38.3U | ✅ 已平 |
| BCHUSDT | 空 | +6.2U | ✅ 已平 |

M40 cron job (479aa1ee305b) 已暂停。

---

## 12. 待办

- [ ] 改用systemd service管理进程（替代nohup）
- [ ] 修Telegram chat_id配置
- [ ] 研究Algo API解决STOP_MARKET -4120问题
- [ ] 跑够数据后对比Paper vs Testnet执行差异
