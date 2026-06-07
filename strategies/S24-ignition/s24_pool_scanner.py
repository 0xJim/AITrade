#!/usr/bin/env python3
"""
S24 Dynamic Pool Scanner

每 7 天运行一次，实现三层 symbol 池的生命周期管理：
  Active Pool  → 当前交易池（受 dynamic 风控约束）
  Watch Pool   → 观察池（记录 shadow 信号，不下单）
  Quarantine   → 隔离池（曾经表现差，低频观察）

核心功能：
  1. 扫描全量 Binance USDT 永续，发现新点火候选
  2. 检测 Active Pool 中的衰老信号
  3. 对 Watch Pool 统计 shadow 表现
  4. 输出 promote / demote 建议（人工确认后执行）

用法：
  python3 s24_pool_scanner.py                  # 使用缓存数据，不调 API
  python3 s24_pool_scanner.py --fetch-new      # 尝试拉取新币数据（可能触发限流）
  python3 s24_pool_scanner.py --days 30        # 分析窗口（默认30天）

输出文件：
  s24_pool_state.json             - 三层池初始状态/人工维护状态
  data/s24_scan_report_YYYYMMDD.json - 本次扫描报告（运行输出，不提交）
"""
from __future__ import annotations
import argparse, hashlib, json, time as _time, sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT      = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "final_true_one_year_backtests" / "api_cache"
OUT_DIR   = ROOT / "strategies" / "S24-ignition" / "data"
STATE_FILE = ROOT / "strategies" / "S24-ignition" / "s24_pool_state.json"
TZ_UTC8   = timezone(timedelta(hours=8))
MS_15M    = 15 * 60 * 1000
MS_1H     = 60 * 60 * 1000

SPIKE_THRESHOLD = 0.012
ATR_PERIOD = 14
RSI_PERIOD = 14
EMA_FAST   = 9
EMA_SLOW   = 21

# 毕业/降级阈值（第一版）
GRADUATE_MIN_DAYS    = 45
GRADUATE_MIN_SIGNALS = 15
GRADUATE_MIN_WR      = 0.48
GRADUATE_MIN_PF      = 1.25
GRADUATE_MAX_FBK     = 0.40

DEMOTE_MAX_PF        = 0.80   # 近30天 PF 低于此 → 降级
DEMOTE_MAX_LOSS_PCT  = 0.05   # 近30天亏损超过余额5% → 降级
DEMOTE_MIN_SIGNALS   = 5      # 近30天信号数不足 → 自动退出

# 新币发现：spike 加速比阈值（近30天/前30天）
ACCEL_THRESHOLD = 2.0   # 最近30天 spike 是前30天的2倍 → 候选


# ── 缓存 API ─────────────────────────────────────────────────────────────────

def cached_get(endpoint, params, allow_fetch=False):
    key  = hashlib.sha1(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    if not allow_fetch:
        return []
    url = "https://fapi.binance.com" + endpoint + "?" + urlencode(params)
    with urlopen(url, timeout=20) as r:
        data = json.loads(r.read())
    path.write_text(json.dumps(data))
    return data

def fetch_klines(symbol, interval, start_ms, end_ms, delay=0.3, allow_fetch=False):
    all_raw = []
    cur = start_ms
    while cur < end_ms:
        raw = cached_get("/fapi/v1/klines", {
            "symbol": symbol, "interval": interval,
            "startTime": cur, "endTime": end_ms, "limit": 1500,
        }, allow_fetch=allow_fetch)
        if not raw: break
        all_raw.extend(raw)
        if len(raw) < 1500: break
        cur = int(raw[-1][0]) + 1
        _time.sleep(delay)
    return all_raw


# ── 指标计算 ─────────────────────────────────────────────────────────────────

def ema_series(vals, period):
    if len(vals) < period: return [None]*len(vals)
    k = 2/(period+1)
    out = [None]*(period-1)
    v = sum(vals[:period])/period
    out.append(v)
    for x in vals[period:]:
        v = x*k + v*(1-k)
        out.append(v)
    return out

def compute_rsi(closes, period=RSI_PERIOD):
    if len(closes) < period+1: return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i]-closes[i-1]
        gains.append(max(d,0)); losses.append(max(-d,0))
    ag = sum(gains[-period:])/period
    al = sum(losses[-period:])/period
    return 100.0 if al==0 else 100-100/(1+ag/al)

def compute_atr(candles, period=ATR_PERIOD):
    if len(candles) < period+1: return 0.0
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i][2]); l = float(candles[i][3]); pc = float(candles[i-1][4])
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs[-period:])/period

def signal_quality(chg_pct, atr_val, c, history):
    score = min(35.0, (chg_pct - SPIKE_THRESHOLD)/SPIKE_THRESHOLD*20 + 20)
    if len(history) >= 10:
        avg_vol = sum(float(h[7]) for h in history[-10:])/10
        if avg_vol > 0:
            score += min(30.0, float(c[7])/avg_vol*10)
    if atr_val > 0 and float(c[4]) > 0:
        ap = atr_val/float(c[4])
        if 0.005 <= ap <= 0.04: score += 20.0
        elif ap < 0.005: score += 5.0
        else: score += max(0.0, 20.0-(ap-0.04)*200)
    body = abs(float(c[4])-float(c[1]))
    rng  = float(c[2])-float(c[3])
    if rng > 0: score += min(15.0, body/rng*20)
    return round(min(100.0, max(0.0, score)), 1)


def compute_metrics(k15, k1h, start_ms, end_ms):
    """计算指定时间窗口内的点火指标（hour_only + EMA + RSI + quality）"""
    if len(k15) < 40 or len(k1h) < 25:
        return None

    closes_1h = [float(r[4]) for r in k1h]
    e9  = ema_series(closes_1h, EMA_FAST)
    e21 = ema_series(closes_1h, EMA_SLOW)
    ema_bull = {}
    for j, (f, s) in enumerate(zip(e9, e21)):
        if f is not None and s is not None:
            ema_bull[int(k1h[j][0])] = f > s

    window = [c for c in k15 if start_ms <= int(c[0]) <= end_ms]
    if len(window) < 20:
        return None

    n = len(k15)
    closes_15 = [float(r[4]) for r in k15]
    warmup = ATR_PERIOD + RSI_PERIOD + 10

    # 找 window 在完整 k15 中的起始索引（用于计算 ATR/RSI）
    w_start_ts = int(window[0][0])
    idx_start  = next((i for i, c in enumerate(k15) if int(c[0]) == w_start_ts), warmup)
    idx_start  = max(idx_start, warmup)

    raw_spikes = 0
    hr_signals = []

    for i in range(idx_start, min(n - 17, len(k15))):
        c = k15[i]
        ts = int(c[0])
        if ts > end_ms: break
        if ts < start_ms: continue
        o, cl = float(c[1]), float(c[4])
        if o <= 0: continue
        chg = (cl - o) / o
        if chg < SPIKE_THRESHOLD: continue
        raw_spikes += 1

        if (ts % MS_1H) != 0: continue
        prev_1h = (ts // MS_1H) * MS_1H - MS_1H
        if not ema_bull.get(prev_1h): continue
        rsi_val = compute_rsi(closes_15[max(0,i-RSI_PERIOD-5):i+1])
        if rsi_val < 50: continue
        atr_val = compute_atr(k15[max(0,i-ATR_PERIOD-5):i])
        q = signal_quality(chg, atr_val, c, k15[max(0,i-20):i])
        if q < 70: continue

        future = k15[i+1:i+17]
        if len(future) < 16: continue
        sig_close = cl
        mfe4h = max((float(f[2])-sig_close)/sig_close for f in future)
        ret4h = (float(future[15][4])-sig_close)/sig_close
        fbk   = ret4h < -0.015

        hr_signals.append({"q": q, "mfe": mfe4h, "ret": ret4h, "fbk": fbk})

    if not hr_signals:
        return {"raw_spikes": raw_spikes, "hr_signals": 0,
                "wr": 0, "pf": 0, "avg_mfe": 0, "fbk_rate": 0}

    wins   = [s for s in hr_signals if s["ret"] > 0]
    losses = [s for s in hr_signals if s["ret"] <= 0]
    gp = sum(s["ret"] for s in wins)
    gl = abs(sum(s["ret"] for s in losses))
    pf = gp/gl if gl > 0 else 99.0

    return {
        "raw_spikes": raw_spikes,
        "hr_signals": len(hr_signals),
        "wr":         len(wins)/len(hr_signals),
        "pf":         round(pf, 3),
        "avg_mfe":    sum(s["mfe"] for s in hr_signals)/len(hr_signals),
        "fbk_rate":   sum(1 for s in hr_signals if s["fbk"])/len(hr_signals),
    }


# ── 主扫描逻辑 ───────────────────────────────────────────────────────────────

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"active_pool": {}, "watch_pool": {}, "quarantine_pool": {}}

def save_state(state):
    state["last_scan"] = datetime.now(TZ_UTC8).isoformat()
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


def scan_symbol(symbol, k15_full, k1h_full, now_ms, days):
    """分析最近 days 天和前 days 天的 spike 指标"""
    recent_end   = now_ms
    recent_start = now_ms - days * 24 * MS_1H
    prior_end    = recent_start
    prior_start  = prior_end - days * 24 * MS_1H

    recent = compute_metrics(k15_full, k1h_full, recent_start, recent_end)
    prior  = compute_metrics(k15_full, k1h_full, prior_start,  prior_end)
    return recent, prior


def main():
    p = argparse.ArgumentParser(description="S24 Dynamic Pool Scanner")
    p.add_argument("--days",       type=int, default=30,
                   help="分析窗口天数（默认30天，前后各一窗口）")
    p.add_argument("--fetch-new",  action="store_true",
                   help="尝试从 Binance API 拉取非缓存新币（可能触发限流）")
    p.add_argument("--dry-run",    action="store_true",
                   help="只输出报告，不修改 pool_state.json")
    args = p.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    now    = datetime.now(TZ_UTC8)
    now_ms = int(now.timestamp() * 1000)
    # 使用与原回测一致的全年缓存参数
    FULL_END   = datetime(2026, 5, 14, 10, 0, 0, tzinfo=TZ_UTC8)
    FULL_START = FULL_END - timedelta(days=365)
    full_start_ms = int(FULL_START.timestamp() * 1000)
    full_end_ms   = int(FULL_END.timestamp()   * 1000)
    # 对于实时扫描，用 now 代替 FULL_END
    scan_end_ms = min(now_ms, full_end_ms)

    print(f"S24 Pool Scanner  {now.strftime('%Y-%m-%d %H:%M')}  window={args.days}d")
    print()

    state = load_state()
    active_syms = set(state["active_pool"].keys())
    watch_syms  = set(state["watch_pool"].keys())
    quar_syms   = set(state["quarantine_pool"].keys())
    all_known   = active_syms | watch_syms | quar_syms

    # 候选扫描池：所有已知 + 新增候选
    scan_pool = sorted(all_known)

    # 加载 K 线（复用缓存）
    print("加载 K 线（使用缓存，API 仅在 --fetch-new 时触发）...")
    data_by_sym = {}
    for si, sym in enumerate(scan_pool, 1):
        print(f"  [{si:>2}/{len(scan_pool)}] {sym}", end="\r")
        try:
            delay = 0.3 if args.fetch_new else 0
            k15 = fetch_klines(sym, "15m", full_start_ms, scan_end_ms, delay=delay, allow_fetch=args.fetch_new)
            k1h = fetch_klines(sym, "1h",  full_start_ms - MS_1H*50, scan_end_ms, delay=delay, allow_fetch=args.fetch_new)
            if k15 and k1h:
                data_by_sym[sym] = (k15, k1h)
        except Exception as e:
            if args.fetch_new:
                print(f"  {sym}: 拉取失败 ({e})")

    print(f"\n  有数据: {len(data_by_sym)}/{len(scan_pool)} 个\n")

    # ── 分析各池 ──────────────────────────────────────────────────────────────

    promote_candidates = []  # watch → active
    demote_candidates  = []  # active → quarantine
    aging_warnings     = []  # active 衰老预警

    report = {
        "scan_date": now.isoformat(),
        "active_metrics": {},
        "watch_metrics":  {},
        "new_candidates": [],
        "promote":        [],
        "demote":         [],
        "aging_warnings": [],
    }

    # 1. 检查 Active Pool
    print("── Active Pool 衰老检测 ──")
    for sym in sorted(active_syms):
        if sym not in data_by_sym:
            print(f"  {sym:<18} [无缓存数据]")
            continue
        k15, k1h = data_by_sym[sym]
        recent, prior = scan_symbol(sym, k15, k1h, scan_end_ms, args.days)
        if not recent:
            print(f"  {sym:<18} [数据不足]")
            continue

        issues = []
        if recent["hr_signals"] < DEMOTE_MIN_SIGNALS:
            issues.append(f"信号不足({recent['hr_signals']}笔<{DEMOTE_MIN_SIGNALS})")
        if recent["pf"] < DEMOTE_MAX_PF and recent["hr_signals"] >= 5:
            issues.append(f"PF={recent['pf']:.2f}<{DEMOTE_MAX_PF}")

        accel = (recent["raw_spikes"] / prior["raw_spikes"]
                 if prior and prior["raw_spikes"] > 0 else 1.0)

        status = "⚠️ " if issues else "✓ "
        print(f"  {status}{sym:<16} signals={recent['hr_signals']:>2}  "
              f"WR={recent['wr']:.0%}  PF={recent['pf']:.2f}  "
              f"MFE={recent['avg_mfe']*100:.1f}%  accel={accel:.1f}x  "
              + (f"[{', '.join(issues)}]" if issues else ""))

        m = {**recent, "prior": prior, "accel": round(accel, 2), "issues": issues}
        report["active_metrics"][sym] = m

        if issues:
            if (recent["hr_signals"] < DEMOTE_MIN_SIGNALS or
                    (recent["pf"] < DEMOTE_MAX_PF and recent["hr_signals"] >= 5)):
                demote_candidates.append({"symbol": sym, "reasons": issues})
                aging_warnings.append(sym)

    # 2. 检查 Watch Pool
    print("\n── Watch Pool 进展 ──")
    for sym in sorted(watch_syms):
        if sym not in data_by_sym:
            print(f"  {sym:<18} [无缓存数据]")
            continue
        k15, k1h = data_by_sym[sym]
        recent, _ = scan_symbol(sym, k15, k1h, scan_end_ms, args.days * 3)  # 用更长窗口看watch
        if not recent:
            continue

        # 检查毕业条件
        wp = state["watch_pool"].get(sym, {})
        added_date = wp.get("added_to_watch")
        days_watched = 0
        if added_date:
            try:
                dt_added = datetime.fromisoformat(added_date)
                days_watched = (now - dt_added).days
            except:
                pass

        grad_ok = (
            days_watched >= GRADUATE_MIN_DAYS and
            recent["hr_signals"] >= GRADUATE_MIN_SIGNALS and
            recent["wr"] >= GRADUATE_MIN_WR and
            recent["pf"] >= GRADUATE_MIN_PF and
            recent["fbk_rate"] <= GRADUATE_MAX_FBK
        )

        status = "🎓" if grad_ok else "👀"
        print(f"  {status} {sym:<16} watched={days_watched}d  signals={recent['hr_signals']:>2}  "
              f"WR={recent['wr']:.0%}  PF={recent['pf']:.2f}  MFE={recent['avg_mfe']*100:.1f}%  "
              + ("[毕业候选!]" if grad_ok else ""))

        report["watch_metrics"][sym] = {**recent, "days_watched": days_watched, "graduate_ready": grad_ok}
        if grad_ok:
            promote_candidates.append({"symbol": sym, "metrics": recent, "days_watched": days_watched})

    # 3. 新币发现（spike 加速）
    print("\n── 新点火候选（spike 加速检测）──")
    for sym in sorted(data_by_sym.keys()):
        if sym in all_known: continue  # 已知的跳过
        k15, k1h = data_by_sym[sym]
        recent, prior = scan_symbol(sym, k15, k1h, scan_end_ms, args.days)
        if not recent or not prior: continue
        if prior["raw_spikes"] == 0: continue

        accel = recent["raw_spikes"] / max(prior["raw_spikes"], 1)
        if accel >= ACCEL_THRESHOLD and recent["hr_signals"] >= 3:
            candidate = {
                "symbol": sym, "accel": round(accel, 1),
                **{f"recent_{k}": v for k,v in recent.items()},
                **{f"prior_{k}": v for k,v in prior.items()},
            }
            report["new_candidates"].append(candidate)
            print(f"  🔥 {sym:<18} accel={accel:.1f}x  recent_signals={recent['hr_signals']}  "
                  f"WR={recent['wr']:.0%}  MFE={recent['avg_mfe']*100:.1f}%")

    if not report["new_candidates"]:
        print("  (无新候选，仅扫描了已知币种的缓存数据)")

    # ── 输出建议 ──────────────────────────────────────────────────────────────

    print("\n" + "="*60)
    print("建议操作（需人工确认）")
    print("="*60)

    report["demote"]  = demote_candidates
    report["promote"] = promote_candidates

    if demote_candidates:
        print("\n⬇️  建议降级到 Quarantine Pool：")
        for d in demote_candidates:
            print(f"  {d['symbol']:<18}  原因: {', '.join(d['reasons'])}")
    else:
        print("\n✓ Active Pool 无降级建议")

    if promote_candidates:
        print("\n⬆️  建议晋升到 Active Pool：")
        for p2 in promote_candidates:
            m = p2["metrics"]
            print(f"  {p2['symbol']:<18}  观察{p2['days_watched']}天  "
                  f"WR={m['wr']:.0%}  PF={m['pf']:.2f}  MFE={m['avg_mfe']*100:.1f}%")
    else:
        print("\n✓ Watch Pool 无毕业候选")

    if report["new_candidates"]:
        print(f"\n🔥 发现 {len(report['new_candidates'])} 个新点火候选 → 建议加入 Watch Pool")
        for c in report["new_candidates"]:
            print(f"  {c['symbol']}")

    # 保存报告
    date_str = now.strftime("%Y%m%d")
    report_path = OUT_DIR / f"s24_scan_report_{date_str}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n报告已保存: {report_path}")

    if not args.dry_run and report["new_candidates"]:
        confirm = input("\n是否自动将新候选加入 Watch Pool？(y/N) ").strip().lower()
        if confirm == 'y':
            for c in report["new_candidates"]:
                sym = c["symbol"]
                state["watch_pool"][sym] = {
                    "added_to_watch": now.isoformat(),
                    "discovery_accel": c["accel"],
                    "discovery_signals": c["recent_hr_signals"],
                }
            save_state(state)
            print(f"已更新 {STATE_FILE.name}")
        else:
            print("未修改 pool_state，请手动更新。")
    elif args.dry_run:
        print("\n[dry-run 模式，不修改 pool_state.json]")


if __name__ == "__main__":
    main()
