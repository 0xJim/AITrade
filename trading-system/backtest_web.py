#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backtesting.core import (
    BinanceFuturesDataProvider,
    SampleDataProvider,
    UniversalBacktester,
    load_strategy,
    save_report,
)


BASE_DIR = Path(__file__).resolve().parent
REPORT_DIR = BASE_DIR / "data" / "backtest_reports"
DEFAULT_STRATEGY = BASE_DIR / "configs" / "backtest_v11j.json"


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AITrade Backtest</title>
  <style>
    :root { color-scheme: light; --bg:#f6f7f9; --panel:#fff; --ink:#151923; --muted:#687386; --line:#d9dee8; --green:#12805c; --red:#bf3f3f; --blue:#1f6feb; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background:var(--bg); color:var(--ink); }
    header { padding:20px 28px 12px; border-bottom:1px solid var(--line); background:var(--panel); }
    h1 { margin:0; font-size:24px; letter-spacing:0; }
    main { padding:20px 28px 32px; max-width:1440px; margin:0 auto; }
    .toolbar { display:grid; grid-template-columns: 150px 120px minmax(220px,1fr) minmax(260px,1fr) 120px; gap:12px; align-items:end; margin-bottom:16px; }
    label { display:block; font-size:12px; color:var(--muted); margin-bottom:6px; }
    input, select, button { width:100%; height:38px; border:1px solid var(--line); border-radius:6px; background:#fff; padding:0 10px; font-size:14px; }
    button { background:var(--blue); color:#fff; border-color:var(--blue); font-weight:600; cursor:pointer; }
    button:disabled { opacity:.65; cursor:wait; }
    .grid { display:grid; grid-template-columns: repeat(4, minmax(150px,1fr)); gap:12px; margin:14px 0; }
    .metric, .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; }
    .metric { padding:14px; min-height:84px; }
    .metric .k { color:var(--muted); font-size:12px; }
    .metric .v { font-size:24px; font-weight:700; margin-top:8px; overflow-wrap:anywhere; }
    .positive { color:var(--green); }
    .negative { color:var(--red); }
    .panel { margin-top:14px; padding:14px; }
    .panel h2 { margin:0 0 12px; font-size:16px; }
    #chart { width:100%; height:300px; display:block; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th, td { padding:9px 8px; border-top:1px solid var(--line); text-align:left; white-space:nowrap; }
    th { color:var(--muted); font-weight:600; }
    .scroll { overflow:auto; max-height:460px; }
    .status { color:var(--muted); min-height:20px; font-size:13px; }
    @media (max-width: 900px) {
      header, main { padding-left:14px; padding-right:14px; }
      .toolbar { grid-template-columns: 1fr 1fr; }
      .grid { grid-template-columns: 1fr 1fr; }
    }
  </style>
</head>
<body>
  <header><h1>AITrade Backtest</h1></header>
  <main>
    <form class="toolbar" id="runForm">
      <div>
        <label>数据源</label>
        <select name="source"><option value="sample">sample</option><option value="binance">binance</option></select>
      </div>
      <div>
        <label>天数</label>
        <input name="days" type="number" min="1" max="1200" value="90">
      </div>
      <div>
        <label>币种</label>
        <input name="symbols" placeholder="BTCUSDT,ETHUSDT；留空=按成交额池">
      </div>
      <div>
        <label>策略 JSON</label>
        <input name="strategy" value="configs/backtest_v11j.json">
      </div>
      <div><button id="runBtn">运行</button></div>
    </form>
    <div class="status" id="status"></div>

    <section class="grid" id="metrics"></section>

    <section class="panel">
      <h2>权益曲线</h2>
      <svg id="chart" viewBox="0 0 1000 300" preserveAspectRatio="none"></svg>
    </section>

    <section class="panel">
      <h2>分币种</h2>
      <div class="scroll"><table id="symbolTable"></table></div>
    </section>

    <section class="panel">
      <h2>交易明细</h2>
      <div class="scroll"><table id="tradeTable"></table></div>
    </section>
  </main>
<script>
const $ = (id) => document.getElementById(id);

function fmt(n, digits=2) {
  if (n === null || n === undefined) return "-";
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: digits });
}

function cls(n) { return Number(n) >= 0 ? "positive" : "negative"; }

function renderMetrics(s) {
  const items = [
    ["策略", `${s.strategy} ${s.version || ""}`],
    ["PnL", `${fmt(s.pnl_usd, 2)} U`, cls(s.pnl_usd)],
    ["ROI", `${fmt(s.roi_pct, 2)}%`, cls(s.roi_pct)],
    ["最大回撤", `${fmt(s.max_drawdown_pct, 2)}%`, Number(s.max_drawdown_pct) <= 15 ? "positive" : "negative"],
    ["交易数", fmt(s.trades, 0)],
    ["胜率", `${fmt(s.win_rate_pct, 2)}%`],
    ["PF", fmt(s.profit_factor, 3)],
    ["最终权益", `${fmt(s.final_balance, 2)} U`, cls(s.final_balance - s.initial_balance)]
  ];
  $("metrics").innerHTML = items.map(([k,v,c=""]) => `<div class="metric"><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`).join("");
}

function renderChart(points) {
  const svg = $("chart");
  svg.innerHTML = "";
  if (!points || points.length < 2) return;
  const values = points.map(p => Number(p.equity));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(1e-9, max - min);
  const path = points.map((p, i) => {
    const x = (i / (points.length - 1)) * 980 + 10;
    const y = 280 - ((Number(p.equity) - min) / span) * 250;
    return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  svg.insertAdjacentHTML("beforeend", `<path d="${path}" fill="none" stroke="#1f6feb" stroke-width="3" vector-effect="non-scaling-stroke"/>`);
  svg.insertAdjacentHTML("beforeend", `<text x="12" y="24" fill="#687386" font-size="13">${fmt(max,2)} U</text><text x="12" y="292" fill="#687386" font-size="13">${fmt(min,2)} U</text>`);
}

function table(el, headers, rows) {
  el.innerHTML = `<thead><tr>${headers.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody>`;
}

function renderTables(report) {
  const bySymbol = Object.entries(report.by_symbol || {}).map(([symbol, r]) =>
    `<tr><td>${symbol}</td><td>${r.trades}</td><td>${fmt(r.win_rate_pct)}%</td><td class="${cls(r.pnl_usd)}">${fmt(r.pnl_usd,2)}</td></tr>`);
  table($("symbolTable"), ["币种","交易","胜率","PnL"], bySymbol);

  const trades = (report.trades || []).slice().reverse().slice(0, 300).map(t =>
    `<tr><td>${t.id}</td><td>${t.symbol}</td><td>${t.direction}</td><td>${t.signal_type}</td><td>${t.entry_time}</td><td>${t.exit_time}</td><td>${fmt(t.entry_price,6)}</td><td>${fmt(t.exit_price,6)}</td><td>${t.exit_reason}</td><td class="${cls(t.pnl_usd)}">${fmt(t.pnl_usd,2)}</td></tr>`);
  table($("tradeTable"), ["#","币种","方向","信号","开仓","平仓","开仓价","平仓价","原因","PnL"], trades);
}

async function runBacktest(ev) {
  ev.preventDefault();
  const btn = $("runBtn");
  const data = new FormData(ev.target);
  const payload = Object.fromEntries(data.entries());
  btn.disabled = true;
  $("status").textContent = "运行中...";
  try {
    const res = await fetch("/api/run", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });
    const resp = await res.json();
    if (!res.ok) throw new Error(resp.error || "run failed");
    render(resp);
    $("status").textContent = `完成，报告已保存：${resp.report_path || "latest.json"}`;
  } catch (err) {
    $("status").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

function render(report) {
  renderMetrics(report.summary || {});
  renderChart(report.equity_curve || []);
  renderTables(report);
}

async function loadLatest() {
  try {
    const res = await fetch("/api/report/latest");
    if (res.ok) render(await res.json());
  } catch (_) {}
}

$("runForm").addEventListener("submit", runBacktest);
loadLatest();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, body: bytes, content_type: str = "application/json", status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, status: int = 200) -> None:
        self._send(json.dumps(payload, ensure_ascii=False).encode(), status=status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(HTML.encode(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/run":
            self._json({"error": "use POST /api/run"}, 405)
            return
        if parsed.path == "/api/report/latest":
            self.latest_report()
            return
        if parsed.path == "/api/reports":
            self.list_reports()
            return
        self._json({"error": "not found"}, 404)

    def run_backtest(self, query: dict[str, list[str]]) -> None:
        try:
            source = one(query, "source", "sample")
            # 限制天数范围
            days = max(1, min(int(one(query, "days", "90")), 1200))
            symbols_text = one(query, "symbols", "")
            strategy_text = one(query, "strategy", str(DEFAULT_STRATEGY))
            strategy_path = resolve_strategy_path(strategy_text)
            strategy = load_strategy(strategy_path)
            provider = SampleDataProvider() if source == "sample" else BinanceFuturesDataProvider()
            symbols = [s.strip().upper() for s in symbols_text.split(",") if s.strip()] or None
            # 限制币种数量
            if symbols:
                symbols = symbols[:50]
            report = UniversalBacktester(strategy, provider).run(symbols=symbols, days=days)
            path = save_report(report, REPORT_DIR)
            report["report_path"] = str(path)
            self._json(report)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def latest_report(self) -> None:
        path = REPORT_DIR / "latest.json"
        if not path.exists():
            self._json({"error": "no report yet"}, 404)
            return
        self._send(path.read_bytes())

    def list_reports(self) -> None:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        reports = sorted(REPORT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        self._json({"reports": [str(p) for p in reports if p.name != "latest.json"]})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/run":
            self._json({"error": "not found"}, 404)
            return

        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        payload = json.loads(body or "{}")
        self.run_backtest_payload(payload)

    def run_backtest_payload(self, payload: dict[str, Any]) -> None:
        try:
            source = payload.get("source", "sample")
            days = max(1, min(int(payload.get("days", 90)), 1200))
            symbols_text = payload.get("symbols", "")
            strategy_text = payload.get("strategy", str(DEFAULT_STRATEGY))
            strategy_path = resolve_strategy_path(strategy_text)
            strategy = load_strategy(strategy_path)
            provider = SampleDataProvider() if source == "sample" else BinanceFuturesDataProvider()
            symbols = [s.strip().upper() for s in symbols_text.split(",") if s.strip()] or None
            if symbols:
                symbols = symbols[:50]
            report = UniversalBacktester(strategy, provider).run(symbols=symbols, days=days)
            path = save_report(report, REPORT_DIR)
            report["report_path"] = str(path)
            self._json(report)
        except Exception as exc:
            message = str(exc)
            self._json({"error": message[:1000]}, 500)


def one(query: dict[str, list[str]], key: str, default: str) -> str:
    values = query.get(key)
    return values[0] if values else default


def resolve_strategy_path(value: str) -> Path:
    raw = Path(value).expanduser()
    path = raw if raw.is_absolute() else (BASE_DIR / raw)
    path = path.resolve()

    configs_dir = (BASE_DIR / "configs").resolve()
    try:
        path.relative_to(configs_dir)
    except ValueError:
        raise ValueError("策略文件必须在 trading-system/configs 目录下")

    if path.suffix != ".json":
        raise ValueError("策略文件必须是 JSON 格式")
    if not path.exists():
        raise ValueError("策略文件不存在")

    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the AITrade backtest web dashboard.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Backtest dashboard: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
