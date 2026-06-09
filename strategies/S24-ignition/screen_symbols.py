#!/usr/bin/env python3
"""
S24-Ignition 候选池筛选脚本

分段验证方法：
  In-sample:  2025-05-14 ~ 2025-11-14  (筛选候选币)
  Out-of-sample: 2025-11-15 ~ 2026-05-14  (验证，由主回测脚本完成)

用法：
  python3 screen_symbols.py                         # 默认参数首次运行
  python3 screen_symbols.py --resume                # 断点续跑
  python3 screen_symbols.py --days 90 --resume      # 用近90天 in-sample 数据

硬过滤条件（默认值）：
  quoteVolume >= 20M USDT
  spike_count >= 8（hour-only 过滤后）
  mfe5_pct >= 15%
  fake_break_pct <= 40%（放宽，假突破扣分而非一票否决）
"""
from __future__ import annotations
import argparse, hashlib, json, time as _time, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT      = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "final_true_one_year_backtests" / "api_cache"
OUT_DIR   = ROOT / "strategies" / "S24-ignition" / "data"
TZ_UTC8   = timezone(timedelta(hours=8))
MS_15M    = 15 * 60 * 1000
MS_1H     = 60 * 60 * 1000

# 与 backtest_ignition.py 对齐的全年区间
FULL_END   = datetime(2026, 5, 14, 10, 0, 0, tzinfo=TZ_UTC8)
FULL_START = FULL_END - timedelta(days=365)   # 2025-05-14 10:00 UTC+8
OOS_START  = datetime(2025, 11, 14, 10, 0, 0, tzinfo=TZ_UTC8)  # OOS 起点

SPIKE_THRESHOLD = 0.012
ATR_PERIOD      = 14
RSI_PERIOD      = 14
EMA_FAST        = 9
EMA_SLOW        = 21

STRUCTURAL_EXCLUDE = {"BUSDT", "BILLUSDT", "BNBUSDT", "LINKUSDT", "SAGAUSDT"}
EXCLUDE_BASE = {
    "BTC", "ETH", "BNB",
    "USDC", "BUSD", "TUSD", "USDD", "DAI", "FDUSD", "USDP", "PYUSD",
}
EXCLUDE_PATTERNS = [
    "UP", "DOWN", "BULL", "BEAR",
    "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "USDTUSDT",
    "1000000", "NEIRO1000",
]

CANDIDATE_POOL = [
    # 当前 19 币池
    "XAGUSDT","XAUUSDT","LABUSDT","SUIUSDT","XRPUSDT","CRCLUSDT",
    "SNDKUSDT","TONUSDT","GTCUSDT","1000PEPEUSDT","SKYAIUSDT","VVVUSDT",
    "MUUSDT","ADAUSDT","INTCUSDT","LDOUSDT","AVAXUSDT","PAXGUSDT","AAVEUSDT",
    # L1/L2
    "SOLUSDT","ETHUSDT","BNBUSDT","DOTUSDT","NEARUSDT","APTUSDT","ARBUSDT",
    "OPUSDT","INJUSDT","TAOUSDT","ICPUSDT","ALGOUSDT","XLMUSDT","TRXUSDT",
    "LTCUSDT","BCHUSDT","ETCUSDT","FILUSDT","XMRUSDT","ZECUSDT","DASHUSDT",
    # DeFi / AI
    "UNIUSDT","FETUSDT","RENDERUSDT","WLDUSDT","ENAUSDT","ONDOUSDT",
    "HBARUSDT","LINKUSDT","WIFUSDT","HYPEUSDT","VIRTUALUSDT",
    # Meme / 高Beta
    "DOGEUSDT","1000SHIBUSDT","FARTCOINUSDT","TRUMPUSDT","PENGUUSDT",
    "WLFIUSDT","PORTALUSDT",
    # 其他高流动性
    "JTOUSDT","QQQUSDT","NVDAUSDT","SOXLUSDT",
]




def load_pool_sets():
    """读取当前 pool_state，避免把 Active/Quarantine 误报为新增 Watch。"""
    path = OUT_DIR.parent / "s24_pool_state.json"
    if not path.exists():
        return set(), set()
    try:
        data = json.loads(path.read_text())
        active = set((data.get("active_pool") or {}).keys())
        quarantine = set((data.get("quarantine_pool") or {}).keys())
        return active, quarantine
    except Exception:
        return set(), set()

# ── 缓存 API ─────────────────────────────────────────────────────────────────

def cache_key(endpoint, params):
    return hashlib.sha1(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()


def cached_get(endpoint, params=None, allow_fetch=True):
    params = params or {}
    key  = cache_key(endpoint, params)
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    if not allow_fetch:
        return None
    url = "https://fapi.binance.com" + endpoint
    if params:
        url += "?" + urlencode(params)
    with urlopen(url, timeout=20) as r:
        data = json.loads(r.read())
    path.write_text(json.dumps(data, ensure_ascii=False))
    return data


def has_cached_klines(symbol, interval, start_ms, end_ms):
    key = cache_key("/fapi/v1/klines", {
        "symbol": symbol, "interval": interval,
        "startTime": start_ms, "endTime": end_ms, "limit": 1500,
    })
    return (CACHE_DIR / f"{key}.json").exists()


def fetch_klines(symbol, interval, start_ms, end_ms, delay=0.25, allow_fetch=True):
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
        if allow_fetch:
            _time.sleep(delay)
    return all_raw


def fetch_exchange_info(allow_fetch=True):
    return cached_get("/fapi/v1/exchangeInfo", {}, allow_fetch=allow_fetch) or {}


def fetch_24h_tickers(allow_fetch=True):
    raw = cached_get("/fapi/v1/ticker/24hr", {}, allow_fetch=allow_fetch) or []
    return {x.get("symbol"): x for x in raw if x.get("symbol")}


def load_universe(args):
    """自动构建 Binance USDT 永续候选 universe；失败时可回退静态池。"""
    if args.static_pool:
        return list(CANDIDATE_POOL), {"source": "static", "universe_size": len(CANDIDATE_POOL), "filtered_out": {}}

    info = fetch_exchange_info(allow_fetch=args.fetch_new)
    tickers = fetch_24h_tickers(allow_fetch=args.fetch_new)
    if not info or "symbols" not in info or not tickers:
        print("[universe] exchangeInfo/ticker 不可用，回退 CANDIDATE_POOL")
        return list(CANDIDATE_POOL), {"source": "static_fallback", "universe_size": len(CANDIDATE_POOL), "filtered_out": {}}

    filtered_out = {"not_perp_usdt": 0, "low_volume": 0, "excluded": 0}
    out = []
    for sym_info in info.get("symbols", []):
        sym = sym_info.get("symbol", "")
        base = sym_info.get("baseAsset", "")
        if not sym:
            continue
        if sym_info.get("contractType") != "PERPETUAL" or sym_info.get("status") != "TRADING" or sym_info.get("quoteAsset") != "USDT":
            filtered_out["not_perp_usdt"] += 1
            continue
        if sym in STRUCTURAL_EXCLUDE or base in EXCLUDE_BASE or any(p in sym for p in EXCLUDE_PATTERNS):
            filtered_out["excluded"] += 1
            continue
        vol = float(tickers.get(sym, {}).get("quoteVolume", 0) or 0)
        if vol < args.min_volume:
            filtered_out["low_volume"] += 1
            continue
        out.append((sym, vol))
    out.sort(key=lambda x: -x[1])
    symbols = [s for s, _ in out]
    return symbols, {
        "source": "binance_exchangeInfo",
        "universe_size": len(symbols),
        "min_volume": args.min_volume,
        "filtered_out": filtered_out,
        "top_volume": [{"symbol": s, "quoteVolume": v} for s, v in out[:20]],
    }


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
        h,l,pc = float(candles[i][2]),float(candles[i][3]),float(candles[i-1][4])
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


# ── 单币分析 ─────────────────────────────────────────────────────────────────

def analyze_symbol(symbol, k15, k1h):
    if len(k15) < 50 or len(k1h) < 30:
        return None

    closes_1h = [float(r[4]) for r in k1h]
    e9  = ema_series(closes_1h, EMA_FAST)
    e21 = ema_series(closes_1h, EMA_SLOW)
    ema_bull = {}
    for j, (f, s) in enumerate(zip(e9, e21)):
        if f is not None and s is not None:
            ema_bull[int(k1h[j][0])] = f > s

    n = len(k15)
    closes_15 = [float(r[4]) for r in k15]
    warmup = ATR_PERIOD + RSI_PERIOD + 10

    raw_spikes = []
    hr_spikes  = []

    for i in range(warmup, n - 17):
        c  = k15[i]
        o, cl = float(c[1]), float(c[4])
        if o <= 0: continue
        chg = (cl - o) / o
        if chg < SPIKE_THRESHOLD: continue

        future = k15[i+1:i+17]
        if len(future) < 16: continue

        sig_close = cl
        highs = [(float(f[2]) - sig_close)/sig_close for f in future]
        lows  = [(float(f[3]) - sig_close)/sig_close for f in future]
        ret4h = (float(future[15][4]) - sig_close)/sig_close
        mfe4h = max(highs)

        atr_val = compute_atr(k15[max(0,i-ATR_PERIOD-5):i])
        q = signal_quality(chg, atr_val, c, k15[max(0,i-20):i])

        raw_spikes.append((chg, q, mfe4h, ret4h))

        ts = int(c[0])
        if (ts % MS_1H) != 0: continue
        prev_1h = (ts // MS_1H) * MS_1H - MS_1H
        if not ema_bull.get(prev_1h): continue
        rsi_val = compute_rsi(closes_15[max(0,i-RSI_PERIOD-5):i+1])
        if rsi_val < 50: continue
        if q < 70: continue

        hr_spikes.append((chg, q, mfe4h, ret4h))

    if not raw_spikes:
        return None

    def sm(lst): return sum(lst)/len(lst) if lst else 0.0

    nr, nh = len(raw_spikes), len(hr_spikes)
    mfes_r = [s[2] for s in raw_spikes]
    rets_r = [s[3] for s in raw_spikes]
    mfes_h = [s[2] for s in hr_spikes]
    rets_h = [s[3] for s in hr_spikes]
    quals  = [s[1] for s in (hr_spikes or raw_spikes)]

    return {
        "symbol":       symbol,
        "raw_spikes":   nr,
        "hr_spikes":    nh,
        "avg_mfe_raw":  sm(mfes_r),
        "avg_ret_raw":  sm(rets_r),
        "avg_mfe_hr":   sm(mfes_h),
        "avg_ret_hr":   sm(rets_h),
        "hr_wr":        sum(1 for r in rets_h if r > 0)/nh if nh else 0,
        "avg_quality":  sm(quals),
        "q90_pct":      sum(1 for q in quals if q >= 90)/max(len(quals),1),
        "mfe5_pct_raw": sum(1 for m in mfes_r if m >= 0.05)/nr,
        "mfe5_pct_hr":  sum(1 for m in mfes_h if m >= 0.05)/nh if nh else 0,
        "fbk_pct":      sum(1 for r in rets_r if r < -0.015)/nr,
    }


# ── 综合评分（假突破扣分，不一票否决）────────────────────────────────────────

def compute_score(m):
    s = 0.0
    s += min(40.0, m["avg_mfe_hr"] * 100 * 4)           # MFE潜力 (0-40)
    s += m["hr_wr"] * 20                                  # 胜率 (0-20)
    s += m["avg_quality"] / 100 * 15                      # 质量分 (0-15)
    s += m["mfe5_pct_hr"] * 15                            # MFE5%比例 (0-15)
    s -= max(0.0, m["fbk_pct"] - 0.25) * 30              # 假突破超25%才扣分 (0-~5)
    return round(s, 2)


# ── 主流程 ────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(description="S24-Ignition 候选池筛选")
    p.add_argument("--days",           type=int,   default=184,
                   help="in-sample 回看天数（从 OOS 起点往前，默认 184 天=全半年）")
    p.add_argument("--min-volume",     type=float, default=20e6,
                   help="24h 成交额下限 USDT（默认 2000万）")
    p.add_argument("--max-fake-rate",  type=float, default=0.40,
                   help="假突破率硬上限（默认 0.40）")
    p.add_argument("--min-mfe5",       type=float, default=0.15,
                   help="MFE≥5% 比例下限（默认 0.15）")
    p.add_argument("--min-hr-spikes",  type=int,   default=8,
                   help="hour-only 过滤后 spike 最小数（默认 8）")
    p.add_argument("--top",            type=int,   default=50,
                   help="输出 Top N 候选（默认 50）")
    p.add_argument("--resume",         action="store_true",
                   help="断点续跑：加载已有结果，跳过已计算的 symbol")
    p.add_argument("--fetch-new",      action="store_true",
                   help="允许请求 Binance API；默认只用缓存/静态回退")
    p.add_argument("--static-pool",    action="store_true",
                   help="强制只扫描内置 CANDIDATE_POOL，不拉全量 universe")
    p.add_argument("--max-symbols",    type=int, default=0,
                   help="最多处理前 N 个 universe symbols，用于测试/分批")
    p.add_argument("--output",         default=str(OUT_DIR / "s24_symbol_candidates.json"),
                   help="输出文件路径")
    p.add_argument("--delay",          type=float, default=0.3,
                   help="每次 API 请求间隔秒数（默认 0.3）")
    return p.parse_args()


def main():
    args = parse_args()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # in-sample 窗口
    is_end   = OOS_START
    is_start = is_end - timedelta(days=args.days)
    # 使用全年数据做 K 线缓存对齐（截取 in-sample 区间在 Python 层完成）
    full_start_ms = int(FULL_START.timestamp() * 1000)
    full_end_ms   = int(FULL_END.timestamp()   * 1000)
    is_start_ms   = int(is_start.timestamp()   * 1000)
    is_end_ms     = int(is_end.timestamp()     * 1000)

    print(f"S24 候选池筛选  in-sample: {is_start.date()} ~ {is_end.date()}  ({args.days}d)")
    print(f"参数: fake_rate≤{args.max_fake_rate:.0%}  mfe5≥{args.min_mfe5:.0%}"
          f"  hr_spikes≥{args.min_hr_spikes}  top{args.top}  delay={args.delay}s  fetch_new={args.fetch_new}")

    # 断点续跑：加载已有结果
    done_results: list[dict] = []
    done_symbols: set[str]   = set()
    if args.resume and out_path.exists():
        try:
            prev = json.loads(out_path.read_text())
            done_results = prev.get("all_computed", [])
            done_symbols = {m["symbol"] for m in done_results}
            print(f"\n[resume] 已加载 {len(done_results)} 个已计算结果，跳过这些 symbol\n")
        except Exception as e:
            print(f"[resume] 加载失败: {e}，从头开始")

    universe, universe_meta = load_universe(args)
    if args.max_symbols and args.max_symbols > 0:
        universe = universe[:args.max_symbols]
    candidates = [s for s in universe if s not in done_symbols]
    print(f"universe: {universe_meta.get('source')} size={len(universe)}  待处理: {len(candidates)} 个（已跳过 {len(done_symbols)} 个）\n")

    results = list(done_results)

    for si, sym in enumerate(candidates, 1):
        print(f"  [{si:>3}/{len(candidates)}] {sym:<20}", end="", flush=True)
        try:
            if not args.fetch_new and not has_cached_klines(sym, "15m", full_start_ms, full_end_ms):
                print("  skip: 无缓存K线（加 --fetch-new 拉取）")
                continue
            k15_full = fetch_klines(sym, "15m", full_start_ms, full_end_ms, delay=args.delay, allow_fetch=args.fetch_new)
            if not k15_full:
                print("  skip: 无K线数据")
                continue
            if int(k15_full[0][0]) > is_start_ms + 30 * 24 * MS_1H:
                print(f"  skip: 上市太晚 ({datetime.fromtimestamp(int(k15_full[0][0])/1000, tz=TZ_UTC8).date()})")
                continue

            k15 = [c for c in k15_full if is_start_ms <= int(c[0]) <= is_end_ms]
            if len(k15) < 50:
                print(f"  skip: in-sample K线不足 ({len(k15)}根)")
                continue

            k1h_full = fetch_klines(sym, "1h", full_start_ms - MS_1H*50, full_end_ms, delay=args.delay, allow_fetch=args.fetch_new)
            k1h = [c for c in k1h_full if (is_start_ms - MS_1H*50) <= int(c[0]) <= is_end_ms]

            m = analyze_symbol(sym, k15, k1h)
            if m:
                m["score"] = compute_score(m)
                results.append(m)
                # 每完成一个 symbol 立即落盘
                _save(results, args, out_path, is_start, is_end, universe_meta)
                print(f"  ✓  spikes={m['hr_spikes']}  mfe={m['avg_mfe_hr']*100:.1f}%"
                      f"  fbk={m['fbk_pct']*100:.0f}%  score={m['score']:.1f}")
            else:
                print("  skip: 数据不足以计算指标")

        except KeyboardInterrupt:
            print("\n\n中断，进度已保存。用 --resume 继续。")
            _save(results, args, out_path, is_start, is_end, universe_meta)
            return
        except Exception as e:
            print(f"  error: {e}")
            _save(results, args, out_path, is_start, is_end, universe_meta)

    # 最终输出
    print(f"\n\n计算完成: {len(results)} 个币种")
    _save(results, args, out_path, is_start, is_end, universe_meta)
    _print_table(results, args)


def _save(results, args, out_path, is_start, is_end, universe_meta):
    """落盘当前所有结果"""
    passed = _filter(results, args)
    passed_sorted = sorted(passed, key=lambda x: -x["score"])
    current_pool, quarantine_pool = load_pool_sets()
    out = {
        "generated_at": datetime.now(TZ_UTC8).isoformat(),
        "universe": universe_meta,
        "in_sample": {"start": is_start.isoformat(), "end": is_end.isoformat()},
        "filter_params": {
            "max_fake_rate": args.max_fake_rate,
            "min_mfe5": args.min_mfe5,
            "min_hr_spikes": args.min_hr_spikes,
        },
        "universe_size": universe_meta.get("universe_size"),
        "total_computed": len(results),
        "total_passed": len(passed_sorted),
        "all_computed": results,
        "passed_candidates": passed_sorted,
        "passed_ranked": passed_sorted,  # backward compatible alias
        "top30": [m["symbol"] for m in passed_sorted[:30]],
        "top50": [m["symbol"] for m in passed_sorted[:50]],
        "new_vs_current_top30": [m["symbol"] for m in passed_sorted[:30]
                                  if m["symbol"] not in current_pool and m["symbol"] not in quarantine_pool],
        "watch_recommended": [
            {
                "symbol": m["symbol"],
                "added": datetime.now(TZ_UTC8).date().isoformat(),
                "source": "screen_symbols",
                "score": m["score"],
                "hr_spikes": m["hr_spikes"],
                "hr_wr": m["hr_wr"],
                "pf_proxy": None,
                "avg_mfe_hr": m["avg_mfe_hr"],
                "fbk_pct": m["fbk_pct"],
                "status": "watch",
            }
            for m in passed_sorted if m["symbol"] not in current_pool and m["symbol"] not in quarantine_pool
        ],
        "watch_pool_patch": {
            m["symbol"]: {
                "added": datetime.now(TZ_UTC8).date().isoformat(),
                "source": "screen_symbols",
                "score": m["score"],
                "hr_spikes": m["hr_spikes"],
                "hr_wr": round(m["hr_wr"], 4),
                "avg_mfe_hr": round(m["avg_mfe_hr"], 4),
                "fbk_pct": round(m["fbk_pct"], 4),
                "note": "auto-discovered candidate; manual review required before Active",
            }
            for m in passed_sorted if m["symbol"] not in current_pool and m["symbol"] not in quarantine_pool
        },
        "rejected": [m for m in results if m not in passed_sorted],
    }
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))


def _filter(results, args):
    return [m for m in results if
            m["hr_spikes"]  >= args.min_hr_spikes   and
            m["mfe5_pct_raw"] >= args.min_mfe5      and
            m["fbk_pct"]    <= args.max_fake_rate]


def _print_table(results, args):
    passed = sorted(_filter(results, args), key=lambda x: -x["score"])
    current_pool, quarantine_pool = load_pool_sets()

    print(f"\n初筛通过: {len(passed)} 个\n")
    print("%-4s %-18s %6s %6s %7s %7s %6s %5s %6s %5s %6s %s" %
          ('#','Symbol','RawSpk','HrSpk','MFEavg','Retavg','WR','Q90%','MFE5%','FBK%','Score',''))
    print('-'*105)
    for i, m in enumerate(passed[:args.top], 1):
        tag = "[当前]" if m["symbol"] in current_pool else ("[隔离]" if m["symbol"] in quarantine_pool else "[新  ]")
        print("%-4d %-18s %6d %6d %6.1f%% %6.2f%% %5.1f%% %4.0f%% %5.0f%% %4.0f%% %6.1f  %s" % (
            i, m["symbol"], m["raw_spikes"], m["hr_spikes"],
            m["avg_mfe_hr"]*100, m["avg_ret_hr"]*100,
            m["hr_wr"]*100, m["q90_pct"]*100,
            m["mfe5_pct_hr"]*100, m["fbk_pct"]*100,
            m["score"], tag))

    new_top30 = [m["symbol"] for m in passed[:30] if m["symbol"] not in current_pool and m["symbol"] not in quarantine_pool]
    print(f"\nTop30 新增候选 ({len(new_top30)} 个): {new_top30}")


if __name__ == "__main__":
    main()
