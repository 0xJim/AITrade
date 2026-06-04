#!/usr/bin/env python3
"""Reproducible G60B vs G60B_SF BTC-trend filter analysis.

This script intentionally reuses the same building blocks as
`scripts/run_true_one_year_backtests.py` and `strategies/S22-v11j/backtest_all_optimizations.py`:

1. load regenerated S07 v10 trades
2. net PnL with fee + slippage
3. apply v11 base filter
4. apply official G60B profile pre-filters
5. optionally remove spike longs by BTC 4h EMA regime
6. run the official v11j simulator, preserving consecutive-loss and risk-cap effects

Outputs:
- JSON with baseline/filtered metrics and trade lists
- Markdown report with monthly comparison
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRADING_SYSTEM = ROOT / "trading-system"
sys.path.insert(0, str(TRADING_SYSTEM))

from backtesting.core import BinanceFuturesDataProvider, TZ_UTC8, ema  # noqa: E402

DEFAULT_SOURCE = ROOT / "strategies/S07-v10/data/backtest_v10_result.json"
DEFAULT_OUT_JSON = ROOT / "data/analysis/g60b_sf_filter_analysis.json"
DEFAULT_OUT_MD = ROOT / "docs/g60b-sf-filter-analysis-2026-06-04.md"
START = datetime.fromisoformat("2025-05-14T10:00:00+08:00")
END = datetime.fromisoformat("2026-05-14T10:00:00+08:00")
FEE_RATE = 0.0004
SLIPPAGE_RATE = 0.0005

G60B_PROFILE = {
    "max_sl_pct": 7.0,
    "consec_loss_mult": 0.5,
    "max_loss_per_trade": 60,
    "exclude_symbols": {"ADAUSDT", "LDOUSDT", "SKYAIUSDT", "SNDKUSDT", "SUIUSDT", "TONUSDT", "VVVUSDT", "XRPUSDT"},
    "min_quality": 85,
    "min_mtf_agree": 4,
    "max_atr_pct": 4.5,
}


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def parse_time(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_UTC8)
    return dt


def trade_notional(trade: dict[str, Any]) -> float:
    raw = trade.get("notional_usd")
    if raw is not None:
        try:
            value = float(raw)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    return float(trade.get("position_usd") or 0) * float(trade.get("leverage") or 3)


def net_trade(trade: dict[str, Any]) -> dict[str, Any]:
    out = {**trade}
    gross = float(out.get("pnl_usd") or 0)
    cost = trade_notional(out) * 2 * (FEE_RATE + SLIPPAGE_RATE)
    out["gross_pnl_usd"] = round(gross, 6)
    out["roundtrip_cost_usd"] = round(cost, 6)
    out["pnl_usd"] = round(gross - cost, 6)
    return out


def in_window(trade: dict[str, Any]) -> bool:
    ts = parse_time(trade["entry_time"])
    return START <= ts <= END


def get_atr_pct(trade: dict[str, Any]) -> float | None:
    tech = trade.get("tech_snapshot", {})
    if not isinstance(tech, dict):
        return None
    value = tech.get("atr_pct")
    return float(value) if value is not None else None


def apply_g60b_prefilters(trades: list[dict[str, Any]], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    excluded = set(cfg.get("exclude_symbols", set()))
    out: list[dict[str, Any]] = []
    for trade in trades:
        if trade["symbol"] in excluded:
            continue
        if cfg.get("min_quality") is not None and (trade.get("v8_quality") or 0) < cfg["min_quality"]:
            continue
        if cfg.get("min_mtf_agree") is not None and (trade.get("mtf_agree") or 0) < cfg["min_mtf_agree"]:
            continue
        atr = get_atr_pct(trade)
        if cfg.get("max_atr_pct") is not None and atr is not None and atr * 100 > cfg["max_atr_pct"]:
            continue
        out.append(trade)
    return out


def btc_regime_series(band: float) -> list[dict[str, Any]]:
    provider = BinanceFuturesDataProvider()
    start_ms = int((START - timedelta(days=10)).timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    candles = provider.klines("BTCUSDT", "4h", start_ms, end_ms)
    if len(candles) < 21:
        raise RuntimeError(f"Not enough BTCUSDT 4h candles: {len(candles)}")
    closes: list[float] = []
    rows: list[dict[str, Any]] = []
    for candle in candles:
        closes.append(candle.close)
        if len(closes) < 21:
            regime = "unknown"
            fast = slow = None
        else:
            fast = ema(closes, 9)
            slow = ema(closes, 21)
            if fast is None or slow is None:
                regime = "unknown"
            elif fast > slow * (1 + band):
                regime = "up"
            elif fast < slow * (1 - band):
                regime = "down"
            else:
                regime = "neutral"
        rows.append({"time": candle.time, "regime": regime, "ema_fast": fast, "ema_slow": slow})
    return rows


def regime_at(series: list[dict[str, Any]], dt: datetime) -> str:
    ts = int(dt.timestamp() * 1000)
    current = "unknown"
    for row in series:
        if row["time"] <= ts:
            current = row["regime"]
        else:
            break
    return current


def filter_by_regime(trades: list[dict[str, Any]], series: list[dict[str, Any]], mode: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for trade in trades:
        regime = regime_at(series, parse_time(trade["entry_time"]))
        t = {**trade, "btc_regime": regime}
        is_spike_long = t.get("direction") == "long" and str(t.get("signal_type", "")).lower() in {"spike", "closed_15m_spike"}
        remove = False
        if is_spike_long:
            if mode == "reject_down" and regime == "down":
                remove = True
            elif mode == "allow_up_only" and regime != "up":
                remove = True
        if remove:
            removed.append(t)
        else:
            kept.append(t)
    return kept, removed


def slim_trade(trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": trade.get("id"),
        "symbol": trade.get("symbol"),
        "direction": trade.get("direction"),
        "entry_time": trade.get("entry_time"),
        "exit_time": trade.get("exit_time"),
        "signal_type": trade.get("signal_type"),
        "btc_regime": trade.get("btc_regime"),
        "pnl_usd": round(float(trade.get("pnl_usd") or 0), 6),
        "gross_pnl_usd": trade.get("gross_pnl_usd"),
        "roundtrip_cost_usd": trade.get("roundtrip_cost_usd"),
        "v8_quality": trade.get("v8_quality"),
        "v8_score": trade.get("v8_score"),
        "mtf_agree": trade.get("mtf_agree"),
        "signal_sl_pct": trade.get("signal_sl_pct"),
        "atr_pct": get_atr_pct(trade),
        "position_usd": trade.get("position_usd"),
        "leverage": trade.get("leverage"),
    }


def summarize_removed(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0, "wins": 0, "win_rate": 0, "pnl_usd": 0, "profit_factor": None}
    pnl = [float(t.get("pnl_usd") or 0) for t in trades]
    wins = sum(1 for x in pnl if x > 0)
    gp = sum(x for x in pnl if x > 0)
    gl = -sum(x for x in pnl if x < 0)
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": len(trades) - wins,
        "win_rate": round(wins / len(trades) * 100, 2),
        "pnl_usd": round(sum(pnl), 2),
        "avg_pnl_usd": round(sum(pnl) / len(trades), 4),
        "profit_factor": round(gp / gl, 4) if gl else None,
        "by_symbol": Counter(t["symbol"] for t in trades).most_common(),
        "by_month": monthly_pnl(trades),
        "by_regime": dict(Counter(t.get("btc_regime", "unknown") for t in trades)),
    }


def monthly_pnl(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    months: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        months.setdefault(parse_time(trade["entry_time"]).strftime("%Y-%m"), []).append(trade)
    out: dict[str, dict[str, Any]] = {}
    for month, rows in sorted(months.items()):
        pnl = sum(float(t.get("pnl_usd") or 0) for t in rows)
        wins = sum(1 for t in rows if float(t.get("pnl_usd") or 0) > 0)
        out[month] = {"trades": len(rows), "wins": wins, "win_rate": round(wins / len(rows) * 100, 2), "pnl_usd": round(pnl, 2)}
    return out


def monthly_delta(base: dict[str, Any], variant: dict[str, Any]) -> dict[str, dict[str, Any]]:
    months = sorted(set(base.get("monthly_pnl", {})) | set(variant.get("monthly_pnl", {})))
    return {
        m: {
            "g60b": round(float(base.get("monthly_pnl", {}).get(m, 0)), 2),
            "variant": round(float(variant.get("monthly_pnl", {}).get(m, 0)), 2),
            "delta": round(float(variant.get("monthly_pnl", {}).get(m, 0)) - float(base.get("monthly_pnl", {}).get(m, 0)), 2),
        }
        for m in months
    }


def md_table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
    return "\n".join(lines)


def result_row(name: str, result: dict[str, Any]) -> list[Any]:
    return [
        name,
        result.get("total_trades"),
        f"{result.get('win_rate')}%",
        result.get("total_pnl"),
        f"{result.get('max_drawdown')}%",
        result.get("profit_factor"),
        result.get("roi_dd_ratio"),
        result.get("max_single_loss"),
    ]


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    rows = [result_row("G60B", payload["results"]["G60B"])]
    rows.append(result_row("G60B_SF_REJECT_DOWN", payload["results"]["G60B_SF_REJECT_DOWN"]["result"]))
    rows.append(result_row("G60B_SF_ALLOW_UP_ONLY", payload["results"]["G60B_SF_ALLOW_UP_ONLY"]["result"]))

    lines = [
        "# G60B_SF BTC Filter 可复现分析",
        "",
        f"- 生成时间: {datetime.now(TZ_UTC8).isoformat(timespec='seconds')}",
        f"- 窗口: {START.isoformat()} / {END.isoformat()}",
        f"- BTC regime: 4h EMA9/EMA21, band={payload['btc_band']}",
        "- 口径: S07 v10 regenerated trades -> net fees/slippage -> v11 base filter -> G60B prefilters -> BTC regime filter -> official v11j simulate()",
        "",
        "## 指标对比",
        "",
        md_table(["版本", "交易数", "胜率", "PnL", "DD", "PF", "ROI/DD", "最大单亏"], rows),
        "",
        "## 被过滤交易表现",
        "",
        md_table(
            ["过滤模式", "过滤笔数", "胜率", "PnL", "PF", "平均PnL"],
            [
                ["reject_down", payload["results"]["G60B_SF_REJECT_DOWN"]["removed_summary"].get("trades"), payload["results"]["G60B_SF_REJECT_DOWN"]["removed_summary"].get("win_rate"), payload["results"]["G60B_SF_REJECT_DOWN"]["removed_summary"].get("pnl_usd"), payload["results"]["G60B_SF_REJECT_DOWN"]["removed_summary"].get("profit_factor"), payload["results"]["G60B_SF_REJECT_DOWN"]["removed_summary"].get("avg_pnl_usd")],
                ["allow_up_only", payload["results"]["G60B_SF_ALLOW_UP_ONLY"]["removed_summary"].get("trades"), payload["results"]["G60B_SF_ALLOW_UP_ONLY"]["removed_summary"].get("win_rate"), payload["results"]["G60B_SF_ALLOW_UP_ONLY"]["removed_summary"].get("pnl_usd"), payload["results"]["G60B_SF_ALLOW_UP_ONLY"]["removed_summary"].get("profit_factor"), payload["results"]["G60B_SF_ALLOW_UP_ONLY"]["removed_summary"].get("avg_pnl_usd")],
            ],
        ),
        "",
        "## 月度对比：G60B vs reject_down",
        "",
    ]
    md_rows = []
    for month, row in payload["results"]["G60B_SF_REJECT_DOWN"]["monthly_delta"].items():
        md_rows.append([month, row["g60b"], row["variant"], row["delta"]])
    lines.append(md_table(["月份", "G60B", "G60B_SF_REJECT_DOWN", "Delta"], md_rows))
    lines.extend([
        "",
        "## 结论口径",
        "",
        "本文件只回答一个问题：在官方 G60B 派生口径下，BTC 4h EMA 过滤会删除哪些交易，删除后指标如何变化。",
        "如果 baseline 交易数无法复现官方 363 笔，脚本会在 JSON 的 `reproduction_warning` 中标记，不能用于上线结论。",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze G60B_SF BTC EMA filter with reproducible official-ish G60B pipeline.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE))
    parser.add_argument("--band", type=float, default=0.005)
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    args = parser.parse_args()

    source = Path(args.source)
    raw = json.loads(source.read_text()).get("trades", [])
    in_window_raw = [t for t in raw if in_window(t)]
    net = [net_trade(t) for t in in_window_raw]

    mod22 = load_module(ROOT / "strategies/S22-v11j/backtest_all_optimizations.py", "g60b_sf_mod22")
    mod22.INITIAL_BALANCE = 1000.0

    base22 = mod22.apply_v11_base_filter(net)
    g60b_prefiltered = apply_g60b_prefilters(base22, G60B_PROFILE)
    btc_series = btc_regime_series(args.band)

    g60b_result = mod22.simulate(g60b_prefiltered, G60B_PROFILE)
    if not g60b_result:
        raise RuntimeError("G60B simulation returned no result")

    results: dict[str, Any] = {"G60B": g60b_result}
    for key, mode in [("G60B_SF_REJECT_DOWN", "reject_down"), ("G60B_SF_ALLOW_UP_ONLY", "allow_up_only")]:
        kept, removed = filter_by_regime(g60b_prefiltered, btc_series, mode)
        result = mod22.simulate(kept, G60B_PROFILE)
        if not result:
            raise RuntimeError(f"{key} simulation returned no result")
        results[key] = {
            "mode": mode,
            "result": result,
            "removed_summary": summarize_removed(removed),
            "kept_prefilter_count": len(kept),
            "removed_trades": [slim_trade(t) for t in removed],
            "monthly_delta": monthly_delta(g60b_result, result),
        }

    reproduction_warning = None
    if g60b_result.get("total_trades") != 363:
        reproduction_warning = (
            f"Baseline reproduced {g60b_result.get('total_trades')} trades, not official 363. "
            "This source file or local branch may differ from the official final_true_one_year run."
        )

    payload = {
        "generated_at": datetime.now(TZ_UTC8).isoformat(),
        "source": str(source.relative_to(ROOT) if source.is_relative_to(ROOT) else source),
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "btc_band": args.band,
        "pipeline_counts": {
            "raw_trades": len(raw),
            "window_raw_trades": len(in_window_raw),
            "v11_base_after_net": len(base22),
            "g60b_prefiltered_before_sim_hard_filters": len(g60b_prefiltered),
            "g60b_after_sim_hard_filters": g60b_result.get("total_trades"),
        },
        "official_reference": {
            "strategy": "S22B v11j-G60B",
            "trades": 363,
            "win_rate": 63.6,
            "pnl": 601.27,
            "max_drawdown": 3.31,
            "profit_factor": 1.72,
        },
        "reproduction_warning": reproduction_warning,
        "results": results,
    }

    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown(Path(args.out_md), payload)

    print(json.dumps({
        "source": payload["source"],
        "pipeline_counts": payload["pipeline_counts"],
        "reproduction_warning": reproduction_warning,
        "G60B": {k: g60b_result[k] for k in ["total_trades", "win_rate", "total_pnl", "max_drawdown", "profit_factor", "roi_dd_ratio"]},
        "G60B_SF_REJECT_DOWN": {k: results["G60B_SF_REJECT_DOWN"]["result"][k] for k in ["total_trades", "win_rate", "total_pnl", "max_drawdown", "profit_factor", "roi_dd_ratio"]},
        "removed_reject_down": results["G60B_SF_REJECT_DOWN"]["removed_summary"],
        "out_json": str(out_json),
        "out_md": args.out_md,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
