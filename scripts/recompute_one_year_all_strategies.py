#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CUTOFF = datetime.fromisoformat("2025-05-12T00:00:00")
OUT_PATH = ROOT / "data" / "all_strategies_1y_recomputed.json"


STRATEGIES = [
    ("S01", "v5", "基础版", ROOT / "strategies/S01-v5/backtest_v5_result.json"),
    ("S02", "v6", "极端费率版", ROOT / "strategies/S02-v6/backtest_v6_result.json"),
    ("S03", "v7plus", "精准狙击宽松版", ROOT / "strategies/S03-v7plus/backtest_v7plus_result.json"),
    ("S04", "v7tuned", "精准狙击保守版", ROOT / "strategies/S04-v7tuned/backtest_v7tuned_result.json"),
    ("S05", "v8", "六维评分版", ROOT / "strategies/S05-v8/backtest_v8_result.json"),
    ("S06", "v9", "过渡版", ROOT / "strategies/S06-v9/backtest_v9_result.json"),
    ("S07", "v10", "三大升级版", ROOT / "strategies/S07-v10/backtest_v10_result.json"),
    ("S08", "v10c", "数据驱动版", ROOT / "strategies/S08-v10c/backtest_v10c_result.json"),
    ("S09", "v11g", "仓位调整版", ROOT / "strategies/S09-v11g/backtest_v11g_result.json"),
    ("S10", "v11h", "精准优化版", ROOT / "strategies/S10-v11h/backtest_v11h_result.json"),
    ("S11", "v11i", "精简优化版", ROOT / "strategies/S11-v11i/backtest_v11i_result.json"),
    ("S12", "v11new", "深度优化版", ROOT / "strategies/S12-v11new/backtest_v11_result.json"),
    ("S13", "v12", "RSI+仓位版", ROOT / "strategies/S13-v12/backtest_v12_result.json"),
    ("S16", "v13", "蓄势突破版", ROOT / "strategies/S16-v13/backtest_v13_result.json"),
    ("S17", "v14", "数据驱动3项版", ROOT / "strategies/S17-v14/backtest_v14_result.json"),
    ("S18", "v15", "做空收紧版", ROOT / "strategies/S18-v15/backtest_v15_result.json"),
    ("S19", "v16", "广撒网回归版", ROOT / "strategies/S19-v16/backtest_v16_result.json"),
    ("S20", "v17", "双策略合并版", ROOT / "strategies/S20-v17/backtest_v17_result.json"),
    ("S21", "v18", "v6微调版", ROOT / "strategies/S21-v18/backtest_v18_result.json"),
]


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None


def trade_time(trade: dict[str, Any]) -> datetime | None:
    return parse_time(trade.get("entry_time") or trade.get("open_time") or trade.get("time"))


def in_window(trade: dict[str, Any]) -> bool:
    ts = trade_time(trade)
    return ts is not None and ts >= CUTOFF


def pnl_of(trade: dict[str, Any]) -> float:
    value = trade.get("pnl_usd")
    if value is None:
        value = trade.get("pnl")
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def summarize_trades(
    num: str,
    version: str,
    name: str,
    trades: list[dict[str, Any]],
    initial_balance: float,
    note: str = "",
) -> dict[str, Any]:
    scale = 1000.0 / initial_balance if initial_balance else 1.0
    balance = 1000.0
    peak = balance
    max_dd = 0.0
    wins = losses = 0
    gross_profit = gross_loss = 0.0
    monthly: dict[str, float] = {}
    max_single_loss = 0.0

    ordered = sorted(trades, key=lambda t: trade_time(t) or datetime.min)
    for trade in ordered:
        pnl = pnl_of(trade) * scale
        if pnl > 0:
            wins += 1
            gross_profit += pnl
        elif pnl < 0:
            losses += 1
            gross_loss += abs(pnl)
            max_single_loss = min(max_single_loss, pnl)
        else:
            losses += 1
        balance += pnl
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak * 100)
        ts = trade_time(trade)
        if ts:
            monthly[ts.strftime("%Y-%m")] = monthly.get(ts.strftime("%Y-%m"), 0.0) + pnl

    total = len(ordered)
    pnl_total = balance - 1000.0
    profit_months = sum(1 for value in monthly.values() if value > 0)
    total_months = len(monthly)
    return {
        "num": num,
        "version": version,
        "name": name,
        "trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total * 100, 2) if total else 0,
        "pnl_1000u": round(pnl_total, 2),
        "roi_1000u": round(pnl_total / 1000 * 100, 2),
        "final_balance_1000u": round(balance, 2),
        "max_drawdown": round(max_dd, 2),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "profit_months": profit_months,
        "total_months": total_months,
        "monthly_win_rate": round(profit_months / total_months * 100, 2) if total_months else 0,
        "max_single_loss": round(max_single_loss, 2),
        "roi_dd_ratio": round((pnl_total / 1000 * 100) / max_dd, 4) if max_dd else None,
        "note": note,
    }


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def summarize_standard() -> list[dict[str, Any]]:
    rows = []
    for num, version, name, path in STRATEGIES:
        data = json.loads(path.read_text())
        trades = [trade for trade in data.get("trades", []) if in_window(trade)]
        initial = float(data.get("initial_balance") or 1000)
        rows.append(summarize_trades(num, version, name, trades, initial, "trade-log recompute"))
    return rows


def summarize_v12j(v10_trades: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
    one_year_raw = [trade for trade in v10_trades if in_window(trade)]

    mod14 = load_module(ROOT / "strategies/S14-v12j/backtest_v12j.py", "s14_v12j")
    base14 = mod14.apply_base_filter(one_year_raw)
    rows14 = {}
    for ver, cfg in mod14.VERSIONS.items():
        filtered = mod14.apply_hard_filters(base14, cfg["hard_filters"]) if cfg.get("hard_filters") else base14
        rows14[ver] = mod14.simulate(filtered, cfg)
    best14_name, best14 = max(
        rows14.items(),
        key=lambda item: (item[1]["total_pnl"] / item[1]["max_drawdown"]) if item[1].get("max_drawdown") else -10**9,
    )
    row14 = {
        "num": "S14",
        "version": "v12j",
        "name": f"V8硬过滤版({best14_name})",
        "trades": best14["total_trades"],
        "wins": best14["wins"],
        "losses": best14["total_trades"] - best14["wins"],
        "win_rate": best14["win_rate"],
        "pnl_1000u": best14["total_pnl"],
        "roi_1000u": best14["return_pct"],
        "final_balance_1000u": best14["final_balance"],
        "max_drawdown": best14["max_drawdown"],
        "profit_factor": best14["profit_factor"],
        "profit_months": best14["profit_months"],
        "total_months": best14["total_months"],
        "monthly_win_rate": round(best14["profit_months"] / best14["total_months"] * 100, 2) if best14["total_months"] else 0,
        "max_single_loss": None,
        "roi_dd_ratio": round(best14["return_pct"] / best14["max_drawdown"], 4) if best14["max_drawdown"] else None,
        "note": "recomputed from v10 one-year trades; selected best internal variant by ROI/DD",
    }

    mod15 = load_module(ROOT / "strategies/S15-v12j_v2/backtest_v12j_v2.py", "s15_v12j_v2")
    base15 = [trade for trade in one_year_raw if trade["symbol"] not in mod15.BLACKLIST and mod15.get_v8(trade) >= 4]
    rows15 = {ver: mod15.simulate(base15, cfg) for ver, cfg in mod15.VERSIONS.items()}
    best15_name, best15 = max(
        rows15.items(),
        key=lambda item: (item[1]["pnl"] / item[1]["dd"]) if item[1].get("dd") else -10**9,
    )
    row15 = {
        "num": "S15",
        "version": "v12j_v2",
        "name": f"V8分档版({best15_name})",
        "trades": best15["total"],
        "wins": best15["wins"],
        "losses": best15["total"] - best15["wins"],
        "win_rate": best15["win_rate"],
        "pnl_1000u": best15["pnl"],
        "roi_1000u": best15["ret"],
        "final_balance_1000u": best15["balance"],
        "max_drawdown": best15["dd"],
        "profit_factor": best15["pf"],
        "profit_months": best15["profit_months"],
        "total_months": best15["total_months"],
        "monthly_win_rate": round(best15["profit_months"] / best15["total_months"] * 100, 2) if best15["total_months"] else 0,
        "max_single_loss": None,
        "roi_dd_ratio": round(best15["ret"] / best15["dd"], 4) if best15["dd"] else None,
        "note": "recomputed from v10 one-year trades; selected best internal variant by ROI/DD",
    }
    return row14, row15


def summarize_s22_profiles(v10_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mod22 = load_module(ROOT / "strategies/S22-v11j/backtest_all_optimizations.py", "s22_profiles")
    one_year_raw = [trade for trade in v10_trades if in_window(trade)]
    one_year_base = mod22.apply_v11_base_filter(one_year_raw)
    profile_map = {
        "M40": ("S22", "v11j-M40", "单笔风险上限$40", {"max_sl_pct": 10.0, "consec_loss_mult": 0.7, "max_loss_per_trade": 40}),
        "D60": ("S22D", "v11j-D60", "单笔风险上限$60", {"max_sl_pct": 10.0, "consec_loss_mult": 0.7, "max_loss_per_trade": 60}),
        "G60": ("S22G", "v11j-G60", "连亏×0.5 + 上限$60", {"max_sl_pct": 10.0, "consec_loss_mult": 0.5, "max_loss_per_trade": 60}),
        "L7": ("S22L", "v11j-L7", "只做SL≤7%", {"max_sl_pct": 7.0, "consec_loss_mult": 0.7, "max_loss_per_trade": None}),
    }
    rows = []
    for key, (num, version, name, cfg) in profile_map.items():
        result = mod22.simulate(one_year_base, cfg)
        rows.append({
            "num": num,
            "version": version,
            "name": name,
            "trades": result["total_trades"],
            "wins": result["wins"],
            "losses": result["losses"],
            "win_rate": result["win_rate"],
            "pnl_1000u": result["total_pnl"],
            "roi_1000u": result["roi"],
            "final_balance_1000u": result["final_balance"],
            "max_drawdown": result["max_drawdown"],
            "profit_factor": result["profit_factor"],
            "profit_months": result["profit_months"],
            "total_months": result["total_months"],
            "monthly_win_rate": result["monthly_win_rate"],
            "max_single_loss": result["max_single_loss"],
            "roi_dd_ratio": result["roi_dd_ratio"],
            "note": f"S22 one-year profile recompute ({key})",
        })
    return rows


def main() -> int:
    rows = summarize_standard()
    v10_trades = json.loads((ROOT / "strategies/S07-v10/backtest_v10_result.json").read_text())["trades"]
    row14, row15 = summarize_v12j(v10_trades)
    rows.extend([row14, row15])
    s22_rows = summarize_s22_profiles(v10_trades)
    rows.extend(s22_rows)

    rows_sorted = sorted(rows, key=lambda row: (row["roi_dd_ratio"] if row["roi_dd_ratio"] is not None else -10**9), reverse=True)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps({
        "window": {
            "start": CUTOFF.isoformat(),
            "end": "latest trade timestamp in each source result",
            "basis": "recomputed from stored trade logs; S14/S15/S22 profiles re-simulated from v10 one-year trades",
        },
        "ranking_by_roi_dd": rows_sorted,
        "all_rows": rows,
    }, ensure_ascii=False, indent=2))

    print("rank|num|version|name|trades|wr|pnl|roi|dd|pf|roi_dd|note")
    for idx, row in enumerate(rows_sorted, 1):
        print("|".join(str(x) for x in [
            idx,
            row["num"],
            row["version"],
            row["name"],
            row["trades"],
            row["win_rate"],
            row["pnl_1000u"],
            row["roi_1000u"],
            row["max_drawdown"],
            row["profit_factor"],
            row["roi_dd_ratio"],
            row["note"],
        ]))
    print(f"\nSaved: {OUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
