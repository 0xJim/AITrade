#!/bin/bash
# 全策略1000天回测
set -e

PY="/home/ubuntu/.hermes/hermes-agent/venv/bin/python3"
BASE="/home/ubuntu/.hermes/trading"
LOG="$BASE/data/run_1000d.log"
RESULT_DIR="$BASE/data"

echo "🚀 全策略1000天回测 开始 $(date)" | tee -a $LOG
echo "======================================" | tee -a $LOG

# 策略列表: 脚本 策略名
STRATEGIES=(
    "backtest_v10.py:v10"
    "backtest.py:v10c"
    "backtest_v7plus.py:v7plus"
    "backtest_v7tuned.py:v7tuned"
    "backtest_v8.py:v8"
    "backtest_v12.py:v12"
    "backtest_v13.py:v13"
    "backtest_v14.py:v14"
    "backtest_v15.py:v15"
    "backtest_v16.py:v16"
    "backtest_v17.py:v17"
    "backtest_v18.py:v18"
)

TOTAL=${#STRATEGIES[@]}
DONE=0

for entry in "${STRATEGIES[@]}"; do
    IFS=':' read -r script name <<< "$entry"
    DONE=$((DONE + 1))
    
    echo "" | tee -a $LOG
    echo "[$DONE/$TOTAL] $(date '+%H:%M:%S') 运行 $name ($script)" | tee -a $LOG
    START=$(date +%s)
    
    cd $BASE
    $PY $script >> $LOG 2>&1
    
    RC=$?
    END=$(date +%s)
    ELAPSED=$((END - START))
    
    if [ $RC -eq 0 ]; then
        echo "  ✅ $name 完成 (${ELAPSED}s)" | tee -a $LOG
    else
        echo "  ❌ $name 失败 (${ELAPSED}s, rc=$RC)" | tee -a $LOG
    fi
done

# v11系列依赖v10数据，最后跑
echo "" | tee -a $LOG
echo "[$(($DONE+1))/$(($TOTAL+4))] $(date '+%H:%M:%S') 运行 v11 (依赖v10数据)" | tee -a $LOG
cd $BASE && $PY backtest_v11.py >> $LOG 2>&1 && echo "  ✅ v11 完成" | tee -a $LOG || echo "  ❌ v11 失败" | tee -a $LOG

echo "[$(($DONE+2))/$(($TOTAL+4))] $(date '+%H:%M:%S') 运行 v11g" | tee -a $LOG
cd $BASE && $PY backtest_v11g.py >> $LOG 2>&1 && echo "  ✅ v11g 完成" | tee -a $LOG || echo "  ❌ v11g 失败" | tee -a $LOG

echo "[$(($DONE+3))/$(($TOTAL+4))] $(date '+%H:%M:%S') 运行 v11h" | tee -a $LOG
cd $BASE && $PY backtest_v11h.py >> $LOG 2>&1 && echo "  ✅ v11h 完成" | tee -a $LOG || echo "  ❌ v11h 失败" | tee -a $LOG

echo "[$(($DONE+4))/$(($TOTAL+4))] $(date '+%H:%M:%S') 运行 v11i" | tee -a $LOG
cd $BASE && $PY backtest_v11i.py >> $LOG 2>&1 && echo "  ✅ v11i 完成" | tee -a $LOG || echo "  ❌ v11i 失败" | tee -a $LOG

echo "" | tee -a $LOG
echo "🏁 全部完成 $(date)" | tee -a $LOG
