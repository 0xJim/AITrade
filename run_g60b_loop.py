#!/usr/bin/env python3
"""
G60B Loop Wrapper — 每分钟调用 cron_scan.main()
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))

from datetime import datetime, timezone, timedelta
TZ = timezone(timedelta(hours=8))

def now_str():
    return datetime.now(TZ).strftime("%m-%d %H:%M:%S")

def main():
    interval = int(os.environ.get("G60B_LOOP_INTERVAL", "60"))
    print(f"[{now_str()}] G60B Loop 启动, 间隔={interval}s")
    
    while True:
        try:
            from cron_scan import main as scan_main
            scan_main()
        except Exception as e:
            print(f"[{now_str()}] ❌ 扫描异常: {e}")
        
        print(f"[{now_str()}] 等待{interval}s...")
        time.sleep(interval)

if __name__ == "__main__":
    main()
