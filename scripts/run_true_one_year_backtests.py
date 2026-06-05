#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import math
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
TZ_UTC8 = timezone(timedelta(hours=8))
DEFAULT_END = datetime(2026, 5, 14, 10, 0, 0, tzinfo=TZ_UTC8)


def _arg_value(name: str) -> str | None:
    if name not in sys.argv:
        return None
    idx = sys.argv.index(name)
    if idx + 1 >= len(sys.argv):
        raise SystemExit(f"{name} requires a value")
    return sys.argv[idx + 1]


def parse_dt(value: str, *, end_of_day: bool = False) -> datetime:
    text = value.strip()
    if "T" not in text and len(text) == 10:
        suffix = "23:59:59" if end_of_day else "00:00:00"
        text = f"{text}T{suffix}"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_UTC8)
    return dt


def parse_window() -> tuple[datetime, datetime, str]:
    start_arg = _arg_value("--start")
    end_arg = _arg_value("--end")
    if start_arg or end_arg:
        if not (start_arg and end_arg):
            raise SystemExit("--start and --end must be provided together")
        start = parse_dt(start_arg)
        end = parse_dt(end_arg, end_of_day=True)
        label = f"custom_{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
        return start, end, label

    if "--days" in sys.argv:
        days = int(_arg_value("--days") or "365")
    else:
        days = 365
    start = DEFAULT_END - timedelta(days=days)
    label = "one_year" if days == 365 else f"{days}d"
    return start, DEFAULT_END, label


START, END, RUN_LABEL = parse_window()
BACKTEST_DAYS = max(1, (END - START).days)
OUT_DIR = ROOT / "data" / f"final_true_{RUN_LABEL}_backtests"
LOG_DIR = OUT_DIR / "logs"
RAW_CACHE = OUT_DIR / "api_cache"
RESULT_PATH = OUT_DIR / f"final_true_{RUN_LABEL}_all_strategies.json"
INITIAL_BALANCE = 1000.0
FEE_RATE = 0.0004
SLIPPAGE_RATE = 0.0005

COMMON_SYMBOLS = [
    "XAGUSDT",
    "XAUUSDT",
    "LABUSDT",
    "SUIUSDT",
    "XRPUSDT",
    "BUSDT",
    "CRCLUSDT",
    "BILLUSDT",
    "BNBUSDT",
    "SNDKUSDT",
    "TONUSDT",
    "GTCUSDT",
    "1000PEPEUSDT",
    "SKYAIUSDT",
    "VVVUSDT",
    "SAGAUSDT",
    "MUUSDT",
    "ADAUSDT",
    "INTCUSDT",
    "LDOUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "PAXGUSDT",
    "AAVEUSDT",
]

REAL_SCRIPT_STRATEGIES = [
    ("S03", "v7plus", "精准狙击宽松版", ROOT / "strategies/S03-v7plus/backtest_v7plus.py"),
    ("S04", "v7tuned", "精准狙击保守版", ROOT / "strategies/S04-v7tuned/backtest_v7tuned.py"),
    ("S05", "v8", "六维评分版", ROOT / "strategies/S05-v8/backtest_v8.py"),
    ("S07", "v10", "三大升级版", ROOT / "strategies/S07-v10/backtest_v10.py"),
    ("S08", "v10c", "数据驱动版", ROOT / "strategies/S08-v10c/backtest.py"),
    ("S12", "v11new", "深度优化版", ROOT / "strategies/S12-v11new/backtest_v11.py"),
    ("S13", "v12", "RSI+仓位版", ROOT / "strategies/S13-v12/backtest_v12.py"),
    ("S16", "v13", "蓄势突破版", ROOT / "strategies/S16-v13/backtest_v13.py"),
    ("S17", "v14", "数据驱动3项版", ROOT / "strategies/S17-v14/backtest_v14.py"),
    ("S18", "v15", "做空收紧版", ROOT / "strategies/S18-v15/backtest_v15.py"),
    ("S19", "v16", "广撒网回归版", ROOT / "strategies/S19-v16/backtest_v16.py"),
    ("S20", "v17", "双策略合并版", ROOT / "strategies/S20-v17/backtest_v17.py"),
    ("S21", "v18", "v6微调版", ROOT / "strategies/S21-v18/backtest_v18.py"),
]

DERIVED_UNSUPPORTED = [
    {"num": "S01", "version": "v5", "name": "基础版", "status": "no_runnable_script"},
    {"num": "S02", "version": "v6", "name": "极端费率版", "status": "no_runnable_script"},
    {"num": "S06", "version": "v9", "name": "过渡版", "status": "no_runnable_script"},
]


def ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CachedBinanceApi:
    def __init__(self) -> None:
        RAW_CACHE.mkdir(parents=True, exist_ok=True)
        self.base_url = "https://fapi.binance.com"
        self.symbol_info = [
            {"symbol": s, "quoteVolume": str(10_000_000_000 - i * 10_000_000), "lastPrice": "1"}
            for i, s in enumerate(COMMON_SYMBOLS)
        ]

    def api_get(self, endpoint: str, params: dict[str, Any] | None = None):
        params = params or {}
        if endpoint == "/fapi/v1/ticker/24hr":
            return self.symbol_info
        key = hashlib.sha1(json.dumps([endpoint, params], sort_keys=True).encode()).hexdigest()
        path = RAW_CACHE / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text())

        url = self.base_url + endpoint
        last_error: Exception | None = None
        for attempt in range(6):
            try:
                query = urlencode(params or {})
                full_url = f"{url}?{query}" if query else url
                with urlopen(full_url, timeout=25) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                path.write_text(json.dumps(data, ensure_ascii=False))
                return data
            except Exception as exc:
                last_error = exc
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"API failed {endpoint} {params}: {last_error}")


class CachedResponse:
    def __init__(self, data: Any) -> None:
        self.status_code = 200
        self._data = data

    def json(self) -> Any:
        return self._data

    def raise_for_status(self) -> None:
        return None


def rewrite_realtime_source(source: str) -> str:
    source = re.sub(r"^INITIAL_BALANCE\s*=.*$", f"INITIAL_BALANCE = {INITIAL_BALANCE!r}", source, flags=re.MULTILINE)
    source = re.sub(
        r"^END_TIME\s*=\s*datetime\.now\(TZ_UTC8\).*$",
        f"END_TIME = datetime.fromisoformat({END.isoformat()!r})",
        source,
        flags=re.MULTILINE,
    )
    source = re.sub(
        r"^START_TIME\s*=\s*END_TIME\s*-\s*timedelta\(days=.*$",
        f"START_TIME = datetime.fromisoformat({START.isoformat()!r})",
        source,
        flags=re.MULTILINE,
    )
    source = re.sub(r"^import requests\s*$", "", source, flags=re.MULTILINE)
    source = source.replace("signal_quality / V8_SIGNAL_QUALITY_MIN", "signal_quality / max(V8_SIGNAL_QUALITY_MIN, 1)")
    source = source.replace("time.sleep(", "fast_sleep(")
    return source


def execute_realtime_script(path: Path, api: CachedBinanceApi) -> None:
    original_argv = sys.argv[:]

    def cached_get(url: str, params: dict[str, Any] | None = None, timeout: Any = None, **_kwargs: Any) -> CachedResponse:
        endpoint = url.replace(api.base_url, "")
        return CachedResponse(api.api_get(endpoint, params or {}))

    class RequestsStub:
        get = staticmethod(cached_get)

    ns: dict[str, Any] = {
        "__name__": "__main__",
        "__file__": str(path),
        "fast_sleep": lambda *_args, **_kwargs: None,
        "requests": RequestsStub,
    }
    try:
        sys.argv = [str(path)]
        source = rewrite_realtime_source(path.read_text())
        exec(compile(source, str(path), "exec"), ns)
    finally:
        sys.argv = original_argv


def latest_result_file(strategy_dir: Path, started_at: float) -> Path:
    data_dir = strategy_dir / "data"
    candidates = list(data_dir.glob("*result.json"))
    fresh = [p for p in candidates if p.stat().st_mtime >= started_at - 1]
    if fresh:
        return max(fresh, key=lambda p: p.stat().st_mtime)
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    raise FileNotFoundError(f"no result json under {data_dir}")


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_UTC8)
    return dt


def trade_notional(trade: dict[str, Any]) -> float:
    raw = trade.get("notional_usd")
    if raw is not None:
        try:
            value = float(raw)
            if math.isfinite(value) and value > 0:
                return value
        except (TypeError, ValueError):
            pass
    pos = trade.get("position_usd") or trade.get("margin_usd") or 0
    lev = trade.get("leverage") or 3
    try:
        return max(0.0, float(pos) * float(lev))
    except (TypeError, ValueError):
        return 0.0


def net_trade(trade: dict[str, Any]) -> dict[str, Any]:
    out = {**trade}
    gross = float(out.get("pnl_usd") or 0)
    cost = trade_notional(out) * 2 * (FEE_RATE + SLIPPAGE_RATE)
    out["gross_pnl_usd"] = round(gross, 6)
    out["roundtrip_cost_usd"] = round(cost, 6)
    out["pnl_usd"] = round(gross - cost, 6)
    return out


def net_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [net_trade(t) for t in trades]


def summarize_trades(
    num: str,
    version: str,
    name: str,
    trades: list[dict[str, Any]],
    status: str,
    note: str,
) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda t: parse_time(t.get("entry_time")) or datetime.min.replace(tzinfo=TZ_UTC8))
    balance = INITIAL_BALANCE
    peak = balance
    max_dd = 0.0
    wins = 0
    gross_profit = 0.0
    gross_loss = 0.0
    monthly: dict[str, float] = {}
    for trade in ordered:
        pnl = float(trade.get("pnl_usd") or 0)
        balance += pnl
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak * 100)
        if pnl > 0:
            wins += 1
            gross_profit += pnl
        else:
            gross_loss += abs(pnl)
        ts = parse_time(trade.get("entry_time"))
        if ts:
            monthly[ts.strftime("%Y-%m")] = monthly.get(ts.strftime("%Y-%m"), 0.0) + pnl

    total = len(ordered)
    pnl_total = balance - INITIAL_BALANCE
    profit_months = sum(1 for value in monthly.values() if value > 0)
    row = {
        "num": num,
        "version": version,
        "name": name,
        "status": status,
        "trades": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": round(wins / total * 100, 2) if total else 0,
        "pnl_1000u": round(pnl_total, 2),
        "roi_1000u": round(pnl_total / INITIAL_BALANCE * 100, 2),
        "final_balance_1000u": round(balance, 2),
        "max_drawdown": round(max_dd, 2),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "profit_months": profit_months,
        "total_months": len(monthly),
        "monthly_win_rate": round(profit_months / len(monthly) * 100, 2) if monthly else 0,
        "roi_dd_ratio": round((pnl_total / INITIAL_BALANCE * 100) / max_dd, 4) if max_dd else None,
        "note": note,
    }
    return row


def row_from_module_result(num: str, version: str, name: str, result: dict[str, Any], note: str) -> dict[str, Any]:
    pnl = float(result.get("total_pnl", result.get("pnl", 0)) or 0)
    dd = float(result.get("max_drawdown", result.get("dd", 0)) or 0)
    total = int(result.get("total_trades", result.get("total", 0)) or 0)
    wins = int(result.get("wins", 0) or 0)
    return {
        "num": num,
        "version": version,
        "name": name,
        "status": "ok",
        "trades": total,
        "wins": wins,
        "losses": int(result.get("losses", total - wins) or 0),
        "win_rate": float(result.get("win_rate", 0) or 0),
        "pnl_1000u": round(pnl, 2),
        "roi_1000u": round(float(result.get("return_pct", result.get("ret", result.get("roi", pnl / INITIAL_BALANCE * 100))) or 0), 2),
        "final_balance_1000u": float(result.get("final_balance", result.get("balance", INITIAL_BALANCE + pnl)) or INITIAL_BALANCE + pnl),
        "max_drawdown": dd,
        "profit_factor": result.get("profit_factor", result.get("pf")),
        "profit_months": int(result.get("profit_months", 0) or 0),
        "total_months": int(result.get("total_months", 0) or 0),
        "monthly_win_rate": round(int(result.get("profit_months", 0) or 0) / int(result.get("total_months", 1) or 1) * 100, 2),
        "roi_dd_ratio": round((pnl / INITIAL_BALANCE * 100) / dd, 4) if dd else None,
        "note": note,
    }


def run_realtime_strategy(api: CachedBinanceApi, num: str, version: str, name: str, path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    started_at = time.time()
    log_path = LOG_DIR / f"{num}_{version}.log"
    with log_path.open("w", encoding="utf-8") as log:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            execute_realtime_script(path, api)
    result_file = latest_result_file(path.parent, started_at)
    result = json.loads(result_file.read_text())
    raw_trades = result.get("trades", [])
    row = summarize_trades(
        num,
        version,
        name,
        net_trades(raw_trades),
        "ok",
        f"regenerated from common klines; source={result_file.relative_to(ROOT)}",
    )
    return row, raw_trades


def derive_from_v10(v10_net: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    mod9 = load_module(ROOT / "strategies/S09-v11g/backtest_v11g.py", "true_s09")
    mod9.INITIAL_BALANCE = INITIAL_BALANCE
    base9 = mod9.apply_v11_base_filter(v10_net)
    filtered9 = mod9.apply_v11g_filters(base9)
    rows.append(summarize_trades("S09", "v11g", "仓位调整版", mod9.simulate(filtered9)["trades"], "ok", "derived from regenerated S07 v10 net trades"))

    mod10 = load_module(ROOT / "strategies/S10-v11h/backtest_v11h.py", "true_s10")
    mod10.INITIAL_BALANCE = INITIAL_BALANCE
    base10 = mod10.apply_v11_base_filter(v10_net)
    filtered10 = mod10.apply_v11h_filters(base10)
    rows.append(summarize_trades("S10", "v11h", "精准优化版", mod10.simulate(filtered10)["trades"], "ok", "derived from regenerated S07 v10 net trades"))

    mod11 = load_module(ROOT / "strategies/S11-v11i/backtest_v11i.py", "true_s11")
    mod11.INITIAL_BALANCE = INITIAL_BALANCE
    base11 = mod11.apply_v11_base_filter(v10_net)
    filtered11 = mod11.apply_hard_filters(base11)
    rows.append(row_from_module_result("S11", "v11i", "精简优化版", mod11.simulate(filtered11), "derived from regenerated S07 v10 net trades"))

    mod14 = load_module(ROOT / "strategies/S14-v12j/backtest_v12j.py", "true_s14")
    mod14.INITIAL_BALANCE = INITIAL_BALANCE
    base14 = mod14.apply_base_filter(v10_net)
    best14_name = ""
    best14: dict[str, Any] | None = None
    best14_score = -10**9
    for ver, cfg in mod14.VERSIONS.items():
        filtered = mod14.apply_hard_filters(base14, cfg["hard_filters"]) if cfg.get("hard_filters") else base14
        result = mod14.simulate(filtered, cfg)
        score = result["return_pct"] / result["max_drawdown"] if result.get("max_drawdown") else -10**9
        if score > best14_score:
            best14_name = ver
            best14 = result
            best14_score = score
    if best14:
        rows.append(row_from_module_result("S14", f"v12j-{best14_name}", f"V8硬过滤版({best14_name})", best14, "selected best S14 internal version by ROI/DD on regenerated S07 net trades"))

    mod15 = load_module(ROOT / "strategies/S15-v12j_v2/backtest_v12j_v2.py", "true_s15")
    mod15.INITIAL_BALANCE = INITIAL_BALANCE
    base15 = [trade for trade in v10_net if trade["symbol"] not in mod15.BLACKLIST and mod15.get_v8(trade) >= 4]
    best15_name = ""
    best15: dict[str, Any] | None = None
    best15_score = -10**9
    for ver, cfg in mod15.VERSIONS.items():
        result = mod15.simulate(base15, cfg)
        score = result["ret"] / result["dd"] if result.get("dd") else -10**9
        if score > best15_score:
            best15_name = ver
            best15 = result
            best15_score = score
    if best15:
        rows.append(row_from_module_result("S15", f"v12j_v2-{best15_name}", f"V8分档版({best15_name})", best15, "selected best S15 internal version by ROI/DD on regenerated S07 net trades"))

    mod22 = load_module(ROOT / "strategies/S22-v11j/backtest_all_optimizations.py", "true_s22")
    mod22.INITIAL_BALANCE = INITIAL_BALANCE
    base22 = mod22.apply_v11_base_filter(v10_net)

    def get_atr(trade: dict[str, Any]) -> float | None:
        tech = trade.get("tech_snapshot", {})
        return tech.get("atr_pct") if isinstance(tech, dict) else None

    def apply_profile_filters(trades: list[dict[str, Any]], cfg: dict[str, Any]) -> list[dict[str, Any]]:
        excluded = set(cfg.get("exclude_symbols", set()))
        out = []
        for trade in trades:
            if trade["symbol"] in excluded:
                continue
            if cfg.get("min_quality") is not None and (trade.get("v8_quality") or 0) < cfg["min_quality"]:
                continue
            if cfg.get("min_mtf_agree") is not None and (trade.get("mtf_agree") or 0) < cfg["min_mtf_agree"]:
                continue
            atr = get_atr(trade)
            if cfg.get("max_atr_pct") is not None and atr is not None and atr * 100 > cfg["max_atr_pct"]:
                continue
            out.append(trade)
        return out

    for key, (num, version, name, cfg) in {
        "M40": ("S22", "v11j-M40", "单笔风险上限$40", {"max_sl_pct": 10.0, "consec_loss_mult": 0.7, "max_loss_per_trade": 40}),
        "D60": ("S22D", "v11j-D60", "单笔风险上限$60", {"max_sl_pct": 10.0, "consec_loss_mult": 0.7, "max_loss_per_trade": 60}),
        "G60": ("S22G", "v11j-G60", "连亏×0.5 + 上限$60", {"max_sl_pct": 10.0, "consec_loss_mult": 0.5, "max_loss_per_trade": 60}),
        "G60B": ("S22B", "v11j-G60B", "低DD均衡档", {"max_sl_pct": 7.0, "consec_loss_mult": 0.5, "max_loss_per_trade": 60, "exclude_symbols": {"ADAUSDT", "LDOUSDT", "SKYAIUSDT", "SNDKUSDT", "SUIUSDT", "TONUSDT", "VVVUSDT", "XRPUSDT"}, "min_quality": 85, "min_mtf_agree": 4, "max_atr_pct": 4.5}),
        "G60S": ("S22S", "v11j-G60S", "低回撤严格档", {"max_sl_pct": 6.0, "consec_loss_mult": 0.6, "max_loss_per_trade": 60, "exclude_symbols": {"ADAUSDT", "LDOUSDT", "SKYAIUSDT", "SNDKUSDT", "SUIUSDT", "TONUSDT", "VVVUSDT", "XRPUSDT"}, "min_quality": 85, "min_mtf_agree": 7, "max_atr_pct": 4.0}),
        "G60O6": ("S22O6", "v11j-G60O6", "G60优化档(排除6个一年拖累币)", {"max_sl_pct": 10.0, "consec_loss_mult": 0.5, "max_loss_per_trade": 60, "exclude_symbols": {"ADAUSDT", "LDOUSDT", "SKYAIUSDT", "SUIUSDT", "TONUSDT", "XRPUSDT"}}),
        "G60P": ("S22P", "v11j-G60P", "收益增强档", {"max_sl_pct": 8.0, "consec_loss_mult": 0.5, "max_loss_per_trade": 60, "exclude_symbols": {"ADAUSDT", "LDOUSDT", "SUIUSDT", "TONUSDT", "XRPUSDT"}, "min_quality": 85}),
        "L7": ("S22L", "v11j-L7", "只做SL≤7%", {"max_sl_pct": 7.0, "consec_loss_mult": 0.7, "max_loss_per_trade": None}),
    }.items():
        trades = apply_profile_filters(base22, cfg)
        result = mod22.simulate(trades, cfg)
        rows.append(row_from_module_result(num, version, name, result, f"S22 profile {key} from regenerated S07 net trades"))

    return rows


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    api = CachedBinanceApi()

    rows: list[dict[str, Any]] = []
    raw_results: dict[str, Any] = {}
    v10_raw: list[dict[str, Any]] | None = None

    for num, version, name, path in REAL_SCRIPT_STRATEGIES:
        print(f"RUN {num} {version} {name}", flush=True)
        try:
            row, raw_trades = run_realtime_strategy(api, num, version, name, path)
            rows.append(row)
            raw_results[num] = row
            if num == "S07":
                v10_raw = raw_trades
            print(f"OK  {num}: trades={row['trades']} pnl={row['pnl_1000u']} dd={row['max_drawdown']}", flush=True)
        except Exception as exc:
            err = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            (LOG_DIR / f"{num}_{version}_error.log").write_text(traceback.format_exc(), encoding="utf-8")
            rows.append({
                "num": num,
                "version": version,
                "name": name,
                "status": "failed",
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0,
                "pnl_1000u": 0,
                "roi_1000u": 0,
                "final_balance_1000u": INITIAL_BALANCE,
                "max_drawdown": 0,
                "profit_factor": None,
                "profit_months": 0,
                "total_months": 0,
                "monthly_win_rate": 0,
                "roi_dd_ratio": None,
                "note": err,
            })
            print(f"FAIL {num}: {err}", flush=True)

    if v10_raw:
        print("DERIVE S09/S10/S11/S14/S15/S22 from regenerated S07 v10", flush=True)
        rows.extend(derive_from_v10(net_trades(v10_raw)))
    else:
        print("SKIP derived strategies: S07 v10 did not complete", flush=True)

    for item in DERIVED_UNSUPPORTED:
        rows.append({
            **item,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "pnl_1000u": 0,
            "roi_1000u": 0,
            "final_balance_1000u": INITIAL_BALANCE,
            "max_drawdown": 0,
            "profit_factor": None,
            "profit_months": 0,
            "total_months": 0,
            "monthly_win_rate": 0,
            "roi_dd_ratio": None,
            "note": "no runnable signal-generation script exists in repository, excluded from final true rerun ranking",
        })

    ok_rows = [r for r in rows if r.get("status") == "ok"]
    ranking = sorted(ok_rows, key=lambda r: (r["roi_dd_ratio"] if r["roi_dd_ratio"] is not None else -10**9), reverse=True)
    payload = {
        "run_at": datetime.now(TZ_UTC8).isoformat(),
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "basis": {
            "initial_balance": INITIAL_BALANCE,
            "fee_rate": FEE_RATE,
            "slippage_rate": SLIPPAGE_RATE,
            "symbols": COMMON_SYMBOLS,
            "method": "signal-generation scripts rerun against the same symbol pool and time window; v11g/v11h/v11i/v12j/v12j_v2/v11j profiles derived from regenerated S07 v10 trades",
        },
        "ranking_by_roi_dd": ranking,
        "all_rows": rows,
        "logs": str(LOG_DIR.relative_to(ROOT)),
    }
    RESULT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("rank|num|version|name|status|trades|wr|pnl|roi|dd|pf|roi_dd|note")
    for idx, row in enumerate(ranking, 1):
        print("|".join(str(x) for x in [
            idx,
            row["num"],
            row["version"],
            row["name"],
            row["status"],
            row["trades"],
            row["win_rate"],
            row["pnl_1000u"],
            row["roi_1000u"],
            row["max_drawdown"],
            row["profit_factor"],
            row["roi_dd_ratio"],
            row["note"],
        ]))
    print(f"Saved: {RESULT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
