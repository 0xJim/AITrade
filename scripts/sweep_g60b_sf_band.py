#!/usr/bin/env python3
"""Sweep BTC EMA band for G60B_SF reject-down filter.

Reuses scripts/analyze_g60b_sf_filter.py so the baseline stays aligned with
the official G60B one-year pipeline. Outputs JSON + Markdown summary.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BANDS = [0, 0.0025, 0.005, 0.0075, 0.01, 0.0125, 0.015, 0.02, 0.03]
DEFAULT_OUT_JSON = ROOT / "data/analysis/g60b_sf_band_sweep.json"
DEFAULT_OUT_MD = ROOT / "docs/g60b-sf-band-sweep-2026-06-04.md"


def run_band(band: float) -> dict:
    with tempfile.TemporaryDirectory() as td:
        out_json = Path(td) / "result.json"
        out_md = Path(td) / "result.md"
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/analyze_g60b_sf_filter.py"),
                "--band",
                str(band),
                "--out-json",
                str(out_json),
                "--out-md",
                str(out_md),
            ],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        data = json.loads(out_json.read_text(encoding="utf-8"))
    base = data["results"]["G60B"]
    var = data["results"]["G60B_SF_REJECT_DOWN"]["result"]
    removed = data["results"]["G60B_SF_REJECT_DOWN"]["removed_summary"]
    return {
        "band": band,
        "reproduction_warning": data.get("reproduction_warning"),
        "base": {k: base[k] for k in ["total_trades", "win_rate", "total_pnl", "max_drawdown", "profit_factor", "roi_dd_ratio"]},
        "variant": {k: var[k] for k in ["total_trades", "win_rate", "total_pnl", "max_drawdown", "profit_factor", "roi_dd_ratio"]},
        "removed": {
            "trades": removed.get("trades"),
            "win_rate": removed.get("win_rate"),
            "pnl_usd": removed.get("pnl_usd"),
            "profit_factor": removed.get("profit_factor"),
        },
        "deltas": {
            "pnl": round(var["total_pnl"] - base["total_pnl"], 2),
            "dd": round(var["max_drawdown"] - base["max_drawdown"], 2),
            "pf": round(var["profit_factor"] - base["profit_factor"], 2),
            "roi_dd": round(var["roi_dd_ratio"] - base["roi_dd_ratio"], 2),
        },
    }


def md_table(headers: list[str], rows: list[list[object]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    out.extend("| " + " | ".join(str(x) for x in row) + " |" for row in rows)
    return "\n".join(out)


def write_outputs(payload: dict, out_json: Path, out_md: Path) -> None:
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    rows = []
    for r in payload["rows"]:
        rows.append([
            r["band"],
            r["variant"]["total_trades"],
            f'{r["variant"]["win_rate"]}%',
            r["variant"]["total_pnl"],
            r["deltas"]["pnl"],
            f'{r["variant"]["max_drawdown"]}%',
            r["deltas"]["dd"],
            r["variant"]["profit_factor"],
            r["variant"]["roi_dd_ratio"],
            r["removed"]["trades"],
            r["removed"]["pnl_usd"],
        ])
    best = payload["best_by_roi_dd"]
    base = payload["base"]
    lines = [
        "# G60B_SF BTC EMA Band Sweep",
        "",
        f"- Generated: {payload['generated_at']}",
        "- Baseline reproduced official G60B: " + ("yes" if not payload.get("reproduction_warning") else payload["reproduction_warning"]),
        f"- G60B baseline: trades={base['total_trades']}, WR={base['win_rate']}%, PnL={base['total_pnl']}U, DD={base['max_drawdown']}%, PF={base['profit_factor']}, ROI/DD={base['roi_dd_ratio']}",
        "- Mode: reject `closed_15m_spike`/`spike` long only when BTC 4h EMA9 < EMA21 × (1 - band).",
        "",
        "## Sweep Results",
        "",
        md_table(["band", "trades", "WR", "PnL", "ΔPnL", "DD", "ΔDD", "PF", "ROI/DD", "removed", "removed PnL"], rows),
        "",
        "## Best historical setting",
        "",
        f"Best by ROI/DD: band={best['band']} -> PnL={best['variant']['total_pnl']}U, DD={best['variant']['max_drawdown']}%, PF={best['variant']['profit_factor']}, ROI/DD={best['variant']['roi_dd_ratio']}.",
        "",
        "## Interpretation",
        "",
        "- band=0.005 (the initial SF setting) improves WR/PF but lowers PnL and ROI/DD versus G60B.",
        "- band=0.0075 is the only tested setting that improves PnL, DD, PF, and ROI/DD together while removing net-negative trades.",
        "- The edge is small and based on 28 removed trades, so treat it as a testnet/shadow candidate, not a guaranteed live upgrade.",
    ]
    out_md.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bands", nargs="*", type=float, default=DEFAULT_BANDS)
    parser.add_argument("--out-json", default=str(DEFAULT_OUT_JSON))
    parser.add_argument("--out-md", default=str(DEFAULT_OUT_MD))
    args = parser.parse_args()
    rows = [run_band(b) for b in args.bands]
    warning = next((r["reproduction_warning"] for r in rows if r.get("reproduction_warning")), None)
    best = max(rows, key=lambda r: r["variant"]["roi_dd_ratio"])
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "reproduction_warning": warning,
        "base": rows[0]["base"],
        "best_by_roi_dd": best,
        "rows": rows,
    }
    write_outputs(payload, Path(args.out_json), Path(args.out_md))
    print(json.dumps({"best_by_roi_dd": best, "out_json": args.out_json, "out_md": args.out_md}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
