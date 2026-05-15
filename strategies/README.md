# 策略版本管理规范

## 命名规则
- 主版本: v10, v11, v12 ... (重大变更)
- 字母后缀: v11a, v11b, v11c ... (同一主版本的微调)
- 每个版本独立保存，**永不覆盖**

## 目录结构
```
~/.hermes/trading/strategies/
├── v10_strategy.md          # 策略文档
├── v10c_strategy.md
├── v11_strategy.md
├── v11a_strategy.md         # v11的保守型变体
├── v11b_strategy.md         # v11的激进型变体
└── ...

~/.hermes/trading/data/
├── version_registry.json    # 版本注册表
├── backtest_v10_result.json # 回测数据
├── backtest_v11a_result.json
└── ...
```

## 策略文档模板
每个 strategy.md 必须包含:
1. 名称、日期、状态 (baseline/candidate/active/archived/superseded)
2. 回测结果 (交易数/胜率/PnL/回撤/月胜率)
3. 过滤规则 (基于哪个版本，追加了什么)
4. 与父版本对比表
5. 月度明细
6. 关键发现
7. 风险点

## 注册表字段
```json
{
  "name": "简短描述",
  "type": "backtest",
  "file": "strategies/v11a_strategy.md",
  "data": "data/backtest_v11a_result.json",
  "result": "124笔/75%/+$1,883/回撤3.3%",
  "status": "candidate",
  "date": "2026-05-08",
  "parent": "v11"
}
```

## 状态流转
- candidate → active (选中实盘)
- candidate → archived (放弃)
- active → superseded (被新版本替代)
- baseline (永不变)

## 新建策略流程
1. 确定 parent 版本
2. 命名 (parent + 下一个字母)
3. 创建 strategies/vXXx_strategy.md
4. 运行回测，保存 data/
5. 更新 version_registry.json
6. 通知用户对比结果
