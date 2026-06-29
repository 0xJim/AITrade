# S24-Ignition 运行状态报告
> 生成时间: 2026-06-29 09:25 (北京时间)
> 策略: S24-Ignition (capped300+dynamic)
> 运行环境: Binance Futures Testnet

---

## 一、当前运行状态

| 项目 | 状态 |
|------|------|
| s24_trader.py 进程 | ✅ 运行中 (PID 2121018, 启动于 Jun28) |
| cron扫描 | ✅ 每2分钟 (2026-06-29 恢复) |
| 每日报告 | ✅ 早8点/晚8点推微信 |
| 周报复盘 | ✅ 每周日20点推微信 |
| 当前余额 | $4,975.29 (初始5000U) |
| 当前持仓 | 0 |
| 冷却期币种 | SKYAI, TON, LAB, SUI, XRP, VVV, ADA |
| 动态冷却 | SUIUSDT (直到 2026-06-21) |

---

## 二、交易汇总 (15笔已平仓)

| 指标 | 数值 |
|------|------|
| 总交易笔数 | 15 |
| 胜率 | 40% (6胜9负) |
| 总PnL | -24.71U |
| 平均盈利 | +46.2U/笔 |
| 平均亏损 | -121.2U/笔 |
| 盈亏比 | 0.38 |

### 按平仓原因分类

| 平仓原因 | 笔数 | PnL |
|----------|------|-----|
| take_profit | 3 | +260.2U |
| stop_loss | 5 | -174.3U |
| max_hold(超时) | 7 | -899.4U |

### 逐笔明细

| # | 币种 | 平仓原因 | PnL |
|---|------|----------|-----|
| 01 | SKYAIUSDT | take_profit | +15.6U ✅ |
| 02 | SKYAIUSDT | take盈 |
| 03 | TONUSDT | max_hold | -5.7U ❌ |
| 04 | LABUSDT | take_profit | +135.8U ✅ |
| 策略5 | SKYAIUSDT | stop_loss | -56.8U ❌ |
| 06 | SUIUSDT | max_hold | **-900.3U** ❌🔥 |
| 07 | TONUSDT | max_hold | +13.8U ✅ |
| 08 | LABUSDT | stop_loss | -27.9U ❌ |
| 09 | LABUSDT | state.json |
| 10 | LABUSDT | stop_loss | -27.7U ❌ |
| 11 | XRPUSDT | max_hold | -5.1U ❌ |
| 12 | TONUSDT | max_hold | -5.3U ❌ |
| 15 | VVVUSDT | stop_loss | -98.9U ❌ |
| 14 | TONUSDT | max_hold | +3.1U ✅ |
| 15 | ADAUSDT | max_hold | +0.2U ✅ |

---

## 三、关键问题

### 1. 🔥 SUIUSDT -900.33U (单笔最大亏损)
- 仓位: qty=1214.7, entry=0.7409, 名义价值=900U
- 设了止损 entry=0.7409, SL=0.718673 (3%止损)
- 但 max_hold 平仓时亏了900U = 100%名义价值
- **可能原因**: 价格暴跌/流动性枯竭/止损未生效/testnet极端滑点

### 2. max_hold 是最大亏损来源
- 7笔超时平仓共亏899.4U
- 说明策略的持仓时间限制在强制平仓时往往处于亏损状态
- 建议: 考虑增加持仓时间上限或优化max_hold平仓逻辑

### 3. 自6月9日后无新交易
- 6月9日至今(6/29)策略空转20天
- 原因: cron扫描因与M40共用账户杀仓问题暂停
- 2026-06-29已恢复S24独立运行(M40已停)

### 4. 威胁模式与API问题
- M40 共用testnet账户时存在互相杀仓风险(已解决)
- 币安API偶尔出现IP ban (请求频率过高)
- 微信通知存在rate limit问题

### 5. 历史异常
- FILUSDT orphan仓位检测到并市价平仓
- hermes send weixin timeout (30秒超时)

---

## 四、策略架构

### 文件结构
```
/home/ubuntu/AITrade/strategies/S24-ignition/
├── s24_trader.py          # 主交易逻辑 (34968 bytes, 2026-06-09)
├── s24_auto_pool_manager.py # 币种池自动管理
├── s24_pool_scanner.py    # 币种筛选
├── screen_symbols.py      # 信号检测
├── notifier.py            # 通知推送
├── config.py              # 配置
├── binance_api.py         # 币安API封装
├── testnet/               # 实际运行环境
│   ├── data_S24/
│   │   ├── s24_trades.jsonl     # 交易记录
│     │   ├── s24_decisions.jsonl # 决策日志
│   │   └── s24_state.json       # 当前状态
│   └── .env.binance       # API密钥(不提交git)
├── data/                  # 回测数据
│   ├── ignition_365d_*.json     # 各种回测结果
│   ┡── s24_paper_trades.jsonl   # 模拟交易
├── logs/
│   ├── s24_testnet.log    # 运行日志
│   ┡── s24_paper.log      # 模拟日志
└── deploy/                # 部署脚本
```

### 进程信息
```
PID 2121018 (s24_trader.py)
启动时间: Jun28
运行参数: --loop --interval 60 --max-entry-lag-sec 300 --use-pool-state
stdout重定向: /home/ubuntu/AITrade/strategies/S24-ignition/logs/s24_testnet.log
```

### Cron Jobs (当前活跃)
```
40e2174db482 | S24-Ignition 交易扫描 (capped300+dynamic) | every 2m | → weixin
27e06e66b852 | S24 每日交易报告(早8点)                    | 0 8 * * * | → weixin
9d7df4172961 | S24 毡日交易报告(晚8点)                    | 0 20 * * * | → weixin
80d30b23077b | S24 周报复盘                                | 0 20 * * 0 | → weixin
```

### 已停用(M40相关已删除)
```
M40 扫描 / 早报 / 晚报 / 周报 — 已于 2026-06-29 删除
```

---

## 五、S24-Ignition 策略参数

### 仓位管理
- 单笔保证金: 300U (固定)
- 杠杆: 3x
- 单笔名义价值: 900U
- 最大同时持仓: 3笔 (占5000U余额18%)

### 信号与入场
- 信号质量评分: capped300+dynamic pool
- 入场延迟上限: 300秒 (--max-entry-lag-sec)
- 扫描间隔: 60秒 (--interval 60)

### 平仓逻辑
- take_profit: 价格触及TP线
- stop_loss: 价格触及SL线
- max_hold: 达到最大持仓时间强制平仓

### 冷却机制
- 平仓后进入冷却期 (常规冷却)
- 重大亏损后进入动态冷却 (如SUI)
- 冷却期内不再入场该币种

---

*报告自动生成 by Hermes Agent*
