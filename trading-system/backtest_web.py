#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from backtesting.core import (
    BinanceFuturesDataProvider,
    SampleDataProvider,
    UniversalBacktester,
    load_strategy,
    save_report,
)

try:
    from config import BINANCE_API_KEY, BINANCE_API_SECRET, BINANCE_TESTNET, INITIAL_BALANCE, TRADE_FAPI
except Exception:
    BINANCE_API_KEY = ""
    BINANCE_API_SECRET = ""
    BINANCE_TESTNET = True
    INITIAL_BALANCE = 1000.0
    TRADE_FAPI = "https://testnet.binancefuture.com"


BASE_DIR = Path(__file__).resolve().parent
REPORT_DIR = BASE_DIR / "data" / "backtest_reports"
DEFAULT_STRATEGY = BASE_DIR / "configs" / "backtest_v11j.json"
TRADES_FILE = BASE_DIR / "data" / "trades.json"
MONITOR_TTL_MINUTES = 7 * 24 * 60
TZ_UTC8 = timezone(timedelta(hours=8))
MONITOR_API_TIMEOUT = 4
RUNTIME_API_KEY = ""
RUNTIME_API_SECRET = ""
RUNTIME_TESTNET: bool | None = None
RUNTIME_ACCOUNT_NAME = ""
RUNTIME_ACCOUNTS: dict[str, dict[str, Any]] = {}
RUNTIME_ACTIVE_ACCOUNT_ID = ""


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


MONITOR_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>AITrade Live Monitor</title>
  <style>
    :root {
      color-scheme: dark;
      --bg:#10111b; --panel:#141625; --panel2:#171a2b; --ink:#e9edf8;
      --muted:#747b95; --line:#272b40; --blue:#4aa3ff; --green:#22c55e;
      --red:#ef4444; --amber:#eab308; --track:#242940;
    }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--ink); font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; letter-spacing:0; }
    header { padding:18px 22px 12px; border-bottom:1px solid var(--line); background:#111321; }
    h1 { margin:0; font-size:15px; color:var(--blue); font-weight:700; }
    .sub { margin-top:5px; color:var(--muted); font-size:12px; }
    main { padding:20px 22px 28px; min-width:1180px; }
    .topbar { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:14px; }
    .chips { display:flex; gap:8px; flex-wrap:wrap; }
    .chip { border:1px solid var(--line); background:var(--panel); color:var(--muted); border-radius:6px; padding:7px 9px; font-size:12px; }
    .chip strong { color:var(--ink); font-weight:700; }
    .settings { display:grid; grid-template-columns: 1fr 1.15fr 1.15fr 150px 110px; gap:10px; margin-bottom:14px; padding:12px; border:1px solid var(--line); background:var(--panel); }
    .session { display:none; align-items:center; justify-content:space-between; gap:12px; margin-bottom:14px; padding:12px; border:1px solid var(--line); background:var(--panel); }
    .session[data-active="true"] { display:flex; }
    .login-state { display:grid; gap:4px; }
    .login-state strong { color:var(--green); }
    .account-actions { display:flex; align-items:end; gap:8px; }
    .account-actions select { min-width:180px; }
    .strategy { margin-bottom:14px; padding:12px; border:1px solid var(--line); background:var(--panel); }
    .strategy-head { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:10px; }
    .strategy-title { color:var(--blue); font-weight:700; }
    .strategy-grid { display:grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap:8px; }
    .strategy-item { background:#101322; border:1px solid var(--line); border-radius:6px; padding:9px; min-height:58px; }
    .strategy-item span { display:block; color:var(--muted); font-size:11px; margin-bottom:5px; }
    .strategy-item strong { color:var(--ink); font-size:13px; }
    .profiles { margin-top:12px; border:1px solid var(--line); overflow:auto; }
    .profiles table { min-width:1120px; }
    .profiles .active-row td { background:#152036; }
    .allocation-note { margin-top:10px; color:var(--muted); font-size:11px; }
    .logline { margin-top:10px; color:var(--muted); font-size:11px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    label { display:block; color:var(--muted); font-size:11px; margin-bottom:6px; }
    input, select { width:100%; height:34px; border:1px solid var(--line); background:#0f1220; color:var(--ink); border-radius:6px; padding:0 9px; font:inherit; }
    input::placeholder { color:#555d75; }
    button { border:1px solid var(--line); background:var(--panel2); color:var(--ink); border-radius:6px; padding:8px 12px; cursor:pointer; font:inherit; }
    button:hover { border-color:#3a4162; }
    .table-wrap { border:1px solid var(--line); background:var(--panel); overflow:auto; }
    table { width:100%; border-collapse:collapse; font-size:12px; }
    th, td { padding:14px 16px; border-bottom:1px solid rgba(39,43,64,.72); text-align:left; vertical-align:middle; white-space:nowrap; }
    th { color:#8a91ab; font-size:12px; letter-spacing:.12em; font-weight:700; background:#131523; }
    tr:hover td { background:#171a2b; }
    .symbol { color:#f6f8ff; font-size:14px; font-weight:700; }
    .short { color:var(--red); } .long { color:var(--green); }
    .blue { color:var(--blue); } .muted { color:var(--muted); }
    .green { color:var(--green); } .red { color:var(--red); } .amber { color:var(--amber); }
    .stack { display:grid; gap:4px; }
    .mini { color:var(--muted); font-size:11px; }
    .bar { width:78px; height:4px; background:var(--track); border-radius:99px; overflow:hidden; display:inline-block; vertical-align:middle; margin-right:7px; }
    .fill { display:block; height:100%; background:var(--blue); border-radius:99px; }
    .empty { padding:42px; color:var(--muted); text-align:center; border:1px dashed var(--line); background:var(--panel); }
    .status-ok { color:var(--green); font-weight:700; }
    .status-warn { color:var(--amber); font-weight:700; }
    @media (max-width: 900px) { main { padding:12px; min-width:980px; } th, td { padding:12px 10px; } }
  </style>
</head>
<body>
  <header>
    <h1>V11j Live 持仓</h1>
    <div class="sub" id="headerSub">仓位、价格、浮盈来自 Binance；结构和锁利来自 Shadow</div>
  </header>
  <main>
    <form class="settings" id="apiForm">
      <div>
        <label>账户名</label>
        <input name="accountName" autocomplete="off" placeholder="例如 Binance 主账户">
      </div>
      <div>
        <label>BINANCE API KEY</label>
        <input name="apiKey" autocomplete="off" placeholder="输入 API Key">
      </div>
      <div>
        <label>BINANCE API SECRET</label>
        <input name="apiSecret" type="password" autocomplete="off" placeholder="输入 API Secret">
      </div>
      <div>
        <label>账户</label>
        <select name="mode">
          <option value="testnet">模拟盘 Testnet</option>
          <option value="live">实盘 Live</option>
        </select>
      </div>
      <div>
        <label>&nbsp;</label>
        <button id="saveApiBtn" type="submit">登录</button>
      </div>
    </form>
    <div class="session" id="sessionPanel">
      <div class="login-state">
        <strong id="loginTitle">已登录</strong>
        <span class="mini" id="loginDetail"></span>
      </div>
      <div class="account-actions">
        <div>
          <label>切换账户</label>
          <select id="accountSelect"></select>
        </div>
        <button id="switchAccountBtn" type="button">切换</button>
        <button id="addAccountBtn" type="button">添加账户</button>
        <button id="logoutBtn" type="button">退出</button>
      </div>
    </div>
    <div class="topbar">
      <div class="chips" id="summary"></div>
      <button id="refreshBtn" type="button">刷新</button>
    </div>
    <section class="strategy" id="strategyPanel"></section>
    <h3 style="margin:20px 0 10px;color:var(--blue);">当前持仓</h3>
    <div id="content" class="table-wrap"></div>
    <section class="strategy" id="profilesPanel"></section>
    <h3 style="margin:20px 0 10px;color:var(--blue);">账户收益曲线</h3>
    <div class="table-wrap" style="padding:14px;">
      <svg id="equityChart" viewBox="0 0 1000 200" preserveAspectRatio="none" style="width:100%;height:200px;background:var(--panel);"></svg>
      <div id="equityInfo" class="mini muted" style="margin-top:8px;text-align:center;"></div>
    </div>
    <h3 style="margin:30px 0 10px;color:var(--blue);">每日变化</h3>
    <div class="table-wrap" style="padding:14px;">
      <svg id="dailyChangeChart" viewBox="0 0 1000 150" preserveAspectRatio="none" style="width:100%;height:150px;background:var(--panel);"></svg>
      <div id="dailyChangeInfo" class="mini muted" style="margin-top:8px;text-align:center;"></div>
    </div>
    <h3 style="margin:30px 0 10px;color:var(--blue);">每日盈亏</h3>
    <div id="dailyPnlContent" class="table-wrap"></div>
    <h3 style="margin:30px 0 10px;color:var(--blue);">历史仓位</h3>
    <div id="historyContent" class="table-wrap"></div>
  </main>
<script>
const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "-").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (n, d=2) => n === null || n === undefined || Number.isNaN(Number(n)) ? "-" : Number(n).toLocaleString(undefined, { maximumFractionDigits:d });
const cls = (n) => Number(n) >= 0 ? "green" : "red";

async function saveApi(ev) {
  ev.preventDefault();
  const btn = $("saveApiBtn");
  const payload = Object.fromEntries(new FormData(ev.target).entries());
  btn.disabled = true;
  try {
    const res = await fetch("/api/monitor/credentials", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "保存失败");
    ev.target.apiKey.value = "";
    ev.target.apiSecret.value = "";
    await load();
  } catch (err) {
    $("content").className = "empty";
    $("content").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

async function switchAccount() {
  const accountId = $("accountSelect").value;
  if (!accountId) return;
  const btn = $("switchAccountBtn");
  btn.disabled = true;
  try {
    const res = await fetch("/api/monitor/switch-account", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({ accountId })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "切换失败");
    await load();
  } catch (err) {
    $("content").className = "empty";
    $("content").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

async function logout() {
  const btn = $("logoutBtn");
  btn.disabled = true;
  try {
    const res = await fetch("/api/monitor/logout", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "退出失败");
    await load();
  } catch (err) {
    $("content").className = "empty";
    $("content").textContent = err.message;
  } finally {
    btn.disabled = false;
  }
}

function showAddAccount() {
  $("apiForm").style.display = "grid";
  $("apiForm").accountName.focus();
}

function renderLoginState(s) {
  const loggedIn = Boolean(s.logged_in);
  const accounts = s.accounts || [];
  $("apiForm").style.display = loggedIn ? "none" : "grid";
  $("sessionPanel").dataset.active = (loggedIn || accounts.length) ? "true" : "false";
  $("accountSelect").innerHTML = accounts.map(a => `<option value="${esc(a.id)}"${a.active ? " selected" : ""}>${esc(a.name)} · ${esc(a.mode === "live" ? "Live" : "Testnet")}</option>`).join("");
  if (loggedIn) {
    $("loginTitle").textContent = `已登录：${s.account_name || "Binance Futures"}`;
    $("loginDetail").textContent = `${s.mode === "live" ? "实盘 Live" : "模拟盘 Testnet"} · 已保存 ${accounts.length} 个本地账户 · 只读持仓监控`;
  } else if (accounts.length) {
    $("loginTitle").textContent = "未登录";
    $("loginDetail").textContent = `已保存 ${accounts.length} 个本地账户，可直接切换登录`;
  }
}

function renderStrategy(data) {
  const a = (data.strategy || {}).allocation || {};
  const allocationItems = [
    ["账户权益", `${fmt(a.balance_usdt, 2)} USDT`],
    ["当前持仓", `${a.open_positions ?? "-"} 个`],
    ["保证金占用", `${fmt(a.current_margin_usd, 2)} USDT`],
    ["保证金占比", `${fmt(a.margin_ratio_pct, 2)}%`],
    ["名义仓位", `${fmt(a.current_notional_usd, 2)} USDT`],
    ["实际杠杆", `${fmt(a.effective_leverage, 2)}x`],
  ];
  $("strategyPanel").innerHTML = `
    <div class="strategy-head">
      <div class="strategy-title">仓位分配</div>
      <div class="muted">仅显示 Binance API 真实账户和持仓</div>
    </div>
    <div class="strategy-grid">${allocationItems.map(([k,v]) => `<div class="strategy-item"><span>${esc(k)}</span><strong>${esc(v)}</strong></div>`).join("")}</div>
    <div class="allocation-note">${esc(a.note || "当前策略状态/Profile 已先隐藏，避免展示不准的信息。")}</div>
  `;
}

function renderProfiles(data) {
  $("profilesPanel").style.display = "none";
  $("profilesPanel").innerHTML = "";
}

function renderSummary(data) {
  const s = data.summary || {};
  renderLoginState(s);

  // 计算区间变化
  const equityCurve = data.equity_curve || [];
  let balanceChange = 0;
  let balanceChangeClass = "muted";
  let snapshotDays = 0;

  if (equityCurve.length >= 2) {
    const firstBalance = Number(equityCurve[0].balance_usd) || 0;
    const lastBalance = Number(equityCurve[equityCurve.length - 1].balance_usd) || 0;
    balanceChange = lastBalance - firstBalance;
    balanceChangeClass = balanceChange >= 0 ? "green" : "red";
    snapshotDays = equityCurve.length;
  }

  // 当前余额
  const currentBalance = s.initial_balance || (equityCurve.length > 0 ? equityCurve[equityCurve.length - 1].balance_usd : 0);

  // 更新header副标题
  const today = data.generated_at ? data.generated_at.split(" ")[0] : "--";
  $("headerSub").textContent = `截至 ${today} 00:00:00 · ${snapshotDays} 天前 · Binance income`;

  const items = [
    ["当前余额", `${fmt(currentBalance, 2)} USDT`, ""],
    ["区间变化", `${balanceChange >= 0 ? "+" : ""}${fmt(balanceChange, 2)} USDT`, balanceChangeClass],
    ["快照天数", `${snapshotDays} 天`, ""],
    ["持仓", s.open_positions ?? 0, ""],
    ["浮盈", `${fmt(s.floating_pnl_usd)} USDT`, cls(s.floating_pnl_usd)],
    ["历史盈亏", `${fmt(s.history_total_pnl_usd)} USDT`, cls(s.history_total_pnl_usd)],
  ];
  $("summary").innerHTML = items.map(([k, v, c]) => `<span class="chip">${esc(k)} <strong class="${c}">${esc(v)}</strong></span>`).join("");
}

function row(t) {
  const directionClass = t.direction === "long" ? "long" : "short";
  const directionText = t.direction === "long" ? "做多" : "做空";
  const pnlClass = cls(t.floating_pnl_usd);
  const statusClass = t.status === "open" ? "status-ok" : "status-warn";
  return `<tr>
    <td><span class="symbol">${esc(t.symbol)}</span></td>
    <td><span class="${directionClass}">${directionText}</span></td>
    <td><div class="stack"><span class="blue">${esc(t.entry_structure)}</span><span class="mini">${esc(t.signal_reason)}</span></div></td>
    <td><div class="stack"><span>${esc(t.latest_structure)}</span><span class="mini">${esc(t.strategy_profile)}</span></div></td>
    <td><div class="stack"><span>${fmt(t.position_usd, 2)}U</span><span class="mini">${fmt(t.notional_usd, 2)} · ${esc(t.leverage)}x · ${esc(t.source)}</span></div></td>
    <td><div class="stack"><span>${fmt(t.entry_price, 6)}</span><span class="mini">${fmt(t.current_price, 6)}</span></div></td>
    <td><div class="stack"><span class="${pnlClass}">${fmt(t.floating_pnl_usd, 2)} USDT</span><span class="mini">shadow ${fmt(t.floating_pnl_pct, 2)}%</span></div></td>
    <td><div class="stack"><span class="${esc(t.lock_class)}">${esc(t.lock_text)}</span><span class="mini">MFE ${fmt(t.mfe_usd, 2)} USDT</span></div></td>
    <td><div class="stack"><span class="${esc(t.protection_class || "muted")}">${esc(t.protection_text || "-")}</span><span class="mini">${esc(t.protection_detail || "-")}</span></div></td>
    <td><div class="stack"><span>${esc(t.ttl_text)}</span><span class="mini">${esc(t.held_text)}</span></div></td>
    <td><div class="stack"><span>${esc(t.opened_day)}</span><span class="mini">${esc(t.opened_time)}</span></div></td>
    <td><div class="stack"><span class="${statusClass}">${esc(t.status_text)}</span><span class="mini">${esc(t.updated_at)}</span></div></td>
  </tr>`;
}

function historyRow(t) {
  const directionClass = t.direction === "long" ? "long" : "short";
  const directionText = t.direction === "long" ? "做多" : "做空";
  const pnlClass = cls(t.realized_pnl_usd);
  const commission = t.commission_usd || 0;
  // 手续费总是支出，显示为负数红色
  const commissionClass = "red";
  const commissionDisplay = commission > 0 ? -commission : commission;
  const funding = t.funding_fee_usd || 0;
  const fundingClass = cls(funding);
  // 计算持仓时长显示
  let durationText = "-";
  if (t.duration_minutes) {
    const dm = t.duration_minutes;
    durationText = dm < 60 ? `${dm}m` : `${Math.floor(dm/60)}h ${dm%60}m`;
  }
  // 杠杆显示
  const lev = t.leverage ? t.leverage + "x" : "-";
  const entryNotional = t.entry_notional_usd ?? t.notional_usd;
  const marginText = t.margin_usd === null || t.margin_usd === undefined ? "保证金 -" : `保证金 ${fmt(t.margin_usd, 2)}U`;
  const qtyText = t.qty === null || t.qty === undefined ? "数量 -" : `数量 ${fmt(t.qty, 4)}`;
  return `<tr>
    <td><span class="symbol">${esc(t.symbol)}</span></td>
    <td><span class="${directionClass}">${directionText}</span></td>
    <td><div class="stack"><span class="blue">${esc(t.entry_structure)}</span><span class="mini">${esc(t.signal_reason)}</span></div></td>
    <td><div class="stack"><span>${fmt(entryNotional, 2)}U 名义</span><span class="mini">${esc(marginText)} · ${esc(lev)} · ${esc(qtyText)}</span></div></td>
    <td><div class="stack"><span>${fmt(t.entry_price, 6)}</span><span class="mini">${fmt(t.exit_price, 6)}</span></div></td>
    <td><div class="stack"><span class="${pnlClass}">${fmt(t.realized_pnl_usd, 2)} USDT</span><span class="mini">${fmt(t.realized_pnl_pct, 2)}%</span></div></td>
    <td><span class="${commissionClass}">${fmt(commissionDisplay, 4)} U</span></td>
    <td><span class="${fundingClass}">${fmt(funding, 4)} U</span></td>
    <td><div class="stack"><span>${esc(t.opened_day)}</span><span class="mini">${esc(t.opened_time)}</span></div></td>
    <td><div class="stack"><span>${esc(t.closed_day)}</span><span class="mini">${esc(t.closed_time)}</span></div></td>
    <td>${esc(durationText)}</td>
  </tr>`;
}

function render(data) {
  renderSummary(data);
  renderStrategy(data);
  renderProfiles(data);
  const rows = data.positions || [];
  const historyRows = data.history_positions || [];

  // 渲染当前持仓
  if (!rows.length) {
    $("content").className = "empty";
    const err = (data.summary || {}).api_error;
    $("content").textContent = err ? `Binance API 未返回持仓：${err}` : "Binance API 当前没有持仓。";
  } else {
    $("content").className = "table-wrap";
    $("content").innerHTML = `<table>
      <thead><tr>
        <th>SYMBOL</th><th>方向</th><th>入场结构</th><th>最新结构</th><th>仓位</th><th>入场 / 当前</th>
        <th>实盘浮盈</th><th>锁利</th><th>保护单</th><th>TTL</th><th>开仓时间</th><th>状态</th>
      </tr></thead>
      <tbody>${rows.map(row).join("")}</tbody>
    </table>`;
  }

  // 渲染历史仓位
  if (!historyRows.length) {
    $("historyContent").className = "empty";
    $("historyContent").textContent = "暂无历史仓位记录。";
  } else {
    $("historyContent").className = "table-wrap";
    $("historyContent").innerHTML = `<table>
      <thead><tr>
        <th>SYMBOL</th><th>方向</th><th>入场结构</th><th>名义仓位</th><th>入场 / 平仓</th>
        <th>已实现盈亏</th><th>手续费</th><th>资金费率</th><th>开仓时间</th><th>平仓时间</th><th>持仓时长</th>
      </tr></thead>
      <tbody>${historyRows.map(historyRow).join("")}</tbody>
    </table>`;
  }

  // 渲染收益曲线
  renderEquityChart(data.equity_curve || []);

  // 渲染每日变化柱状图
  renderDailyChangeChart(data.daily_pnl || []);

  // 渲染每日盈亏
  renderDailyPnl(data.daily_pnl || []);
}

function renderDailyPnl(dailyData) {
  const container = $("dailyPnlContent");
  container.innerHTML = "";

  if (!dailyData || dailyData.length === 0) {
    container.className = "empty";
    container.textContent = "需要登录 API 查看每日盈亏";
    return;
  }

  // 计算汇总
  let totalTrade = 0, totalFunding = 0, totalCommission = 0, totalNet = 0;
  for (const d of dailyData) {
    totalTrade += d.trade_pnl || 0;
    totalFunding += d.funding_fee || 0;
    totalCommission += d.commission || 0;
    totalNet += d.net_pnl || 0;
  }

  const rows = dailyData.map(d => {
    const netClass = cls(d.net_pnl || 0);
    const changeClass = cls(d.daily_change || 0);
    // 账户余额
    const balance = d.account_balance || 0;
    // 当日变化
    const change = d.daily_change || 0;
    // 毛盈亏
    const grossPnl = (d.trade_pnl || 0) + (d.funding_fee || 0);
    // 净盈亏
    const netPnl = d.net_pnl || 0;
    // 手续费是支出，显示为负数
    const comm = -(d.commission || 0);

    // 解析交易笔数和胜率
    const tradesStr = d.trades || "-";
    const tradesParts = tradesStr.split(" / ");
    const tradeCount = tradesParts[0] || "0";
    const winRate = tradesParts[1] || "0%";

    return `<tr>
      <td>${esc(d.date)}</td>
      <td>${fmt(balance, 2)} USDT</td>
      <td class="${changeClass}">${change >= 0 ? "+" : ""}${fmt(change, 2)} USDT</td>
      <td class="${cls(grossPnl)}">${fmt(grossPnl, 2)} USDT</td>
      <td class="red">${fmt(comm, 2)} USDT</td>
      <td class="${cls(d.funding_fee || 0)}">${fmt(d.funding_fee, 2)} USDT</td>
      <td class="${netClass}">${fmt(netPnl, 2)} USDT</td>
      <td>${tradeCount}</td>
      <td>${winRate}</td>
    </tr>`;
  }).join("");

  container.className = "table-wrap";
  container.innerHTML = `<table>
    <thead><tr>
      <th>日期</th>
      <th>账户余额</th>
      <th>当日变化</th>
      <th>毛盈亏</th>
      <th>手续费</th>
      <th>资金费</th>
      <th>净盈亏</th>
      <th>交易笔数</th>
      <th>胜率</th>
    </tr></thead>
    <tbody>${rows}</tbody>
    <tfoot><tr style="font-weight:bold;background:var(--panel);">
      <td>汇总 (${dailyData.length}天)</td>
      <td>-</td>
      <td class="${cls(totalNet)}">${totalNet >= 0 ? "+" : ""}${fmt(totalNet, 2)} USDT</td>
      <td class="${cls(totalTrade + totalFunding)}">${fmt(totalTrade + totalFunding, 2)} USDT</td>
      <td class="red">${fmt(-totalCommission, 2)} USDT</td>
      <td class="${cls(totalFunding)}">${fmt(totalFunding, 2)} USDT</td>
      <td class="${cls(totalNet)}">${fmt(totalNet, 2)} USDT</td>
      <td>-</td>
      <td>-</td>
    </tr></tfoot>
  </table>`;
}

function renderEquityChart(curve) {
  const svg = $("equityChart");
  const info = $("equityInfo");
  svg.innerHTML = "";

  if (!curve || curve.length < 2) {
    info.textContent = "需要登录 API 查看收益曲线";
    return;
  }

  const values = curve.map(p => Number(p.balance_usd));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(1e-9, max - min);

  // 计算每个点的位置
  const points = curve.map((p, i) => {
    const x = (i / (curve.length - 1)) * 980 + 10;
    const y = 180 - ((Number(p.balance_usd) - min) / span) * 160;
    return { x, y, date: p.date, balance: p.balance_usd, index: i };
  });

  // 绘制曲线
  const pathD = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(" ");
  svg.insertAdjacentHTML("beforeend", `<path d="${pathD}" fill="none" stroke="#4aa3ff" stroke-width="2" vector-effect="non-scaling-stroke"/>`);

  // 绘制交互点和日期标签
  points.forEach((p, i) => {
    // 只在特定间隔显示日期标签
    const labelInterval = Math.max(1, Math.floor(curve.length / 6));
    if (i % labelInterval === 0 || i === curve.length - 1) {
      // 显示日期标签
      const dateLabel = p.date.substring(5).replace("-", "/"); // 显示 MM/DD
      svg.insertAdjacentHTML("beforeend", `<text x="${p.x}" y="195" fill="#747b95" font-size="10" text-anchor="middle">${dateLabel}</text>`);
    }

    // 绘制可交互的点
    const dotId = `equity-dot-${i}`;
    svg.insertAdjacentHTML("beforeend", `<circle id="${dotId}" cx="${p.x}" cy="${p.y}" r="5" fill="#4aa3ff" opacity="0" style="cursor:pointer"/>`);

    // 添加悬停效果
    const dot = document.getElementById(dotId);
    if (dot) {
      dot.addEventListener("mouseenter", () => {
        dot.setAttribute("opacity", "1");
        info.textContent = `${p.date} | 余额: ${fmt(p.balance, 2)} USDT`;
      });
      dot.addEventListener("mouseleave", () => {
        dot.setAttribute("opacity", "0");
      });
    }
  });

  // 绘制最高点和最低点
  const maxIdx = values.indexOf(max);
  const minIdx = values.indexOf(min);
  const maxPoint = points[maxIdx];
  const minPoint = points[minIdx];

  svg.insertAdjacentHTML("beforeend", `<circle cx="${maxPoint.x}" cy="${maxPoint.y}" r="4" fill="#22c55e"/>`);
  svg.insertAdjacentHTML("beforeend", `<circle cx="${minPoint.x}" cy="${minPoint.y}" r="4" fill="#ef4444"/>`);

  // 计算总收益
  const firstBalance = values[0];
  const lastBalance = values[values.length - 1];
  const totalPnl = lastBalance - firstBalance;
  const totalPnlPct = firstBalance > 0 ? (totalPnl / firstBalance * 100) : 0;

  info.textContent = `${curve[0].date} ~ ${curve[curve.length-1].date} | 起始 ${fmt(firstBalance, 2)} U → 当前 ${fmt(lastBalance, 2)} U | 收益 ${fmt(totalPnl, 2)} U (${fmt(totalPnlPct, 2)}%)`;
}

function renderDailyChangeChart(dailyData) {
  const svg = $("dailyChangeChart");
  const info = $("dailyChangeInfo");
  svg.innerHTML = "";

  if (!dailyData || dailyData.length === 0) {
    info.textContent = "需要登录 API 查看每日变化";
    return;
  }

  // 按日期正序排列
  const sortedData = [...dailyData].reverse();

  // 获取每日变化值
  const changes = sortedData.map(d => d.daily_change || 0);
  const maxChange = Math.max(...changes.map(Math.abs), 1);
  const zeroLine = 75; // SVG高度150，零线在中间

  // 绘制零线
  svg.insertAdjacentHTML("beforeend", `<line x1="0" y1="${zeroLine}" x2="1000" y2="${zeroLine}" stroke="#272b40" stroke-width="1"/>`);

  // 绘制柱状图
  sortedData.forEach((d, i) => {
    const change = d.daily_change || 0;
    const barHeight = Math.min(70, Math.abs(change) / maxChange * 70);
    const x = (i / (sortedData.length - 1 || 1)) * 980 + 10;
    const y = change >= 0 ? zeroLine - barHeight : zeroLine;
    const color = change >= 0 ? "#22c55e" : "#ef4444";

    svg.insertAdjacentHTML("beforeend", `<rect x="${x - 3}" y="${y}" width="6" height="${barHeight}" fill="${color}" rx="1"/>`);
  });

  // 计算汇总信息
  const totalChange = changes.reduce((a, b) => a + b, 0);
  const positiveDays = changes.filter(c => c > 0).length;
  const negativeDays = changes.filter(c => c < 0).length;

  info.textContent = `总变化 ${fmt(totalChange, 2)} U | 上涨 ${positiveDays} 天 | 下跌 ${negativeDays} 天 | 平均 ${fmt(totalChange / sortedData.length, 2)} U/天`;
}

async function load() {
  $("refreshBtn").disabled = true;
  try {
    const res = await fetch("/api/monitor");
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || "monitor failed");
    render(data);
  } catch (err) {
    $("content").className = "empty";
    $("content").textContent = err.message;
  } finally {
    $("refreshBtn").disabled = false;
  }
}

$("refreshBtn").addEventListener("click", load);
$("apiForm").addEventListener("submit", saveApi);
$("switchAccountBtn").addEventListener("click", switchAccount);
$("addAccountBtn").addEventListener("click", showAddAccount);
$("logoutBtn").addEventListener("click", logout);
load();
setInterval(load, 10000);
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
        if parsed.path == "/monitor":
            self._send(MONITOR_HTML.encode(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/monitor":
            self._json(build_monitor_payload(parse_qs(parsed.query)))
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
        if parsed.path == "/api/monitor/credentials":
            self.save_monitor_credentials()
            return
        if parsed.path == "/api/monitor/switch-account":
            self.switch_monitor_account()
            return
        if parsed.path == "/api/monitor/logout":
            self.clear_monitor_credentials()
            return
        if parsed.path != "/api/run":
            self._json({"error": "not found"}, 404)
            return

        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        payload = json.loads(body or "{}")
        self.run_backtest_payload(payload)

    def save_monitor_credentials(self) -> None:
        global RUNTIME_ACCOUNT_NAME, RUNTIME_ACTIVE_ACCOUNT_ID, RUNTIME_API_KEY, RUNTIME_API_SECRET, RUNTIME_TESTNET
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(body or "{}")
            account_name = str(payload.get("accountName") or "").strip()
            api_key = str(payload.get("apiKey") or "").strip()
            api_secret = str(payload.get("apiSecret") or "").strip()
            mode = str(payload.get("mode") or "testnet").strip().lower()
            if not api_key or not api_secret:
                self._json({"error": "API Key 和 API Secret 都必须填写"}, 400)
                return
            if mode not in {"testnet", "live"}:
                self._json({"error": "账户只能选择 testnet 或 live"}, 400)
                return
            RUNTIME_API_KEY = api_key
            RUNTIME_API_SECRET = api_secret
            RUNTIME_TESTNET = mode != "live"
            RUNTIME_ACCOUNT_NAME = account_name or "Binance Futures"
            account_id = hashlib.sha256(f"{RUNTIME_ACCOUNT_NAME}|{mode}|{api_key}".encode()).hexdigest()[:16]
            RUNTIME_ACCOUNTS[account_id] = {
                "id": account_id,
                "name": RUNTIME_ACCOUNT_NAME,
                "api_key": api_key,
                "api_secret": api_secret,
                "testnet": RUNTIME_TESTNET,
            }
            RUNTIME_ACTIVE_ACCOUNT_ID = account_id
            self._json({
                "ok": True,
                "account_id": account_id,
                "account_name": RUNTIME_ACCOUNT_NAME,
                "mode": "testnet" if RUNTIME_TESTNET else "live",
                "message": "API 已接入当前本地服务进程",
            })
        except json.JSONDecodeError:
            self._json({"error": "请求体不是合法 JSON"}, 400)

    def switch_monitor_account(self) -> None:
        global RUNTIME_ACCOUNT_NAME, RUNTIME_ACTIVE_ACCOUNT_ID, RUNTIME_API_KEY, RUNTIME_API_SECRET, RUNTIME_TESTNET
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(body or "{}")
            account_id = str(payload.get("accountId") or "").strip()
            account = RUNTIME_ACCOUNTS.get(account_id)
            if not account:
                self._json({"error": "账户不存在或服务已重启，需要重新添加"}, 404)
                return
            RUNTIME_ACTIVE_ACCOUNT_ID = account_id
            RUNTIME_ACCOUNT_NAME = account["name"]
            RUNTIME_API_KEY = account["api_key"]
            RUNTIME_API_SECRET = account["api_secret"]
            RUNTIME_TESTNET = bool(account["testnet"])
            self._json({
                "ok": True,
                "account_id": account_id,
                "account_name": RUNTIME_ACCOUNT_NAME,
                "mode": "testnet" if RUNTIME_TESTNET else "live",
            })
        except json.JSONDecodeError:
            self._json({"error": "请求体不是合法 JSON"}, 400)

    def clear_monitor_credentials(self) -> None:
        global RUNTIME_ACCOUNT_NAME, RUNTIME_ACTIVE_ACCOUNT_ID, RUNTIME_API_KEY, RUNTIME_API_SECRET, RUNTIME_TESTNET
        RUNTIME_ACCOUNT_NAME = ""
        RUNTIME_ACTIVE_ACCOUNT_ID = ""
        RUNTIME_API_KEY = ""
        RUNTIME_API_SECRET = ""
        RUNTIME_TESTNET = None
        self._json({"ok": True, "message": "已退出当前账户；已保存账户仍可从下拉框切换"})

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


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ_UTC8)
        return dt.astimezone(TZ_UTC8)
    except ValueError:
        return None


def load_trades() -> list[dict[str, Any]]:
    if not TRADES_FILE.exists():
        return []
    try:
        payload = json.loads(TRADES_FILE.read_text(encoding="utf-8"))
        trades = payload.get("trades", [])
        return trades if isinstance(trades, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def current_price_for(trade: dict[str, Any]) -> float | None:
    price = trade.get("current_price") or trade.get("mark_price") or trade.get("entry_price")
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def floating_pnl(trade: dict[str, Any], current_price: float | None) -> tuple[float | None, float | None]:
    try:
        entry = float(trade.get("entry_price"))
        leverage = float(trade.get("leverage") or 1)
        position_usd = float(trade.get("position_usd") or 0)
    except (TypeError, ValueError):
        return None, None
    if not current_price or not entry:
        return None, None
    raw = (current_price - entry) / entry
    if trade.get("direction") == "short":
        raw = -raw
    pnl_pct = raw * 100 * leverage
    return round(pnl_pct / 100 * position_usd, 4), round(pnl_pct, 4)


def max_favorable_excursion(trade: dict[str, Any], current_price: float | None) -> float | None:
    try:
        entry = float(trade.get("entry_price"))
        leverage = float(trade.get("leverage") or 1)
        position_usd = float(trade.get("position_usd") or 0)
    except (TypeError, ValueError):
        return None
    candidates: list[float] = []
    if current_price:
        candidates.append(current_price)
    for key in ("trail_high", "trail_low"):
        value = trade.get(key)
        if value:
            try:
                candidates.append(float(value))
            except (TypeError, ValueError):
                pass
    if not candidates or not entry:
        return None
    if trade.get("direction") == "short":
        best_raw = max((entry - p) / entry for p in candidates)
    else:
        best_raw = max((p - entry) / entry for p in candidates)
    return round(best_raw * leverage * position_usd, 4)


def monitor_settings() -> tuple[str, str, bool, str]:
    api_key = RUNTIME_API_KEY or BINANCE_API_KEY
    api_secret = RUNTIME_API_SECRET or BINANCE_API_SECRET
    testnet = BINANCE_TESTNET if RUNTIME_TESTNET is None else RUNTIME_TESTNET
    if RUNTIME_TESTNET is None:
        trade_fapi = TRADE_FAPI
    else:
        trade_fapi = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
    return api_key, api_secret, testnet, trade_fapi.rstrip("/")


def monitor_session() -> dict[str, Any]:
    api_key, api_secret, testnet, _trade_fapi = monitor_settings()
    accounts = [
        {
            "id": account_id,
            "name": account["name"],
            "mode": "testnet" if account["testnet"] else "live",
            "active": account_id == RUNTIME_ACTIVE_ACCOUNT_ID,
        }
        for account_id, account in RUNTIME_ACCOUNTS.items()
    ]
    return {
        "logged_in": bool(api_key and api_secret),
        "account_name": RUNTIME_ACCOUNT_NAME or ("Env Binance API" if api_key and api_secret else ""),
        "mode": "testnet" if testnet else "live",
        "active_account_id": RUNTIME_ACTIVE_ACCOUNT_ID,
        "accounts": accounts,
    }


def process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def read_text_tail(path: Path, max_lines: int = 5) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
    except OSError:
        return []


def detect_loop_profile() -> str | None:
    log_path = BASE_DIR / "data" / "g60b_testnet_loop.log"
    pid_path = BASE_DIR / "data" / "g60b_testnet_loop.pid"
    if pid_path.exists() or log_path.exists():
        return "G60B"
    return None


def build_strategy_status() -> dict[str, Any]:
    try:
        import config as cfg
        profiles = getattr(cfg, "STRATEGY_PROFILES", {})
        profile = detect_loop_profile() or getattr(cfg, "STRATEGY_PROFILE", "M40")
        profile_config = profiles.get(profile, {})
        default_profile = profiles.get(getattr(cfg, "STRATEGY_PROFILE", "M40"), {})
        pid_path = BASE_DIR / "data" / "g60b_testnet_loop.pid"
        log_path = BASE_DIR / "data" / "g60b_testnet_loop.log"
        pid = None
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                pid = None
        lines = [line.strip() for line in read_text_tail(log_path, 8) if line.strip()]
        profile_rows = []
        for name, row in profiles.items():
            profile_rows.append({
                "name": name,
                "active": name == profile,
                "desc": row.get("desc"),
                "max_loss_per_trade": row.get("MAX_LOSS_PER_TRADE"),
                "consec_loss_mult": row.get("V11I_CONSEC_LOSS_MULT", getattr(cfg, "V11I_CONSEC_LOSS_MULT", None)),
                "max_sl_pct": row.get("V11I_MAX_SL_PCT", getattr(cfg, "V11I_MAX_SL_PCT", None)),
                "max_atr_pct": row.get("V11I_MAX_ATR_PCT", getattr(cfg, "V11I_MAX_ATR_PCT", None)),
                "signal_quality_min": row.get("V8_SIGNAL_QUALITY_MIN", getattr(cfg, "V8_SIGNAL_QUALITY_MIN", None)),
                "mtf_agree_min": row.get("MTF_AGREE_MIN", getattr(cfg, "MTF_AGREE_MIN", None)),
                "extra_blacklist_count": len(row.get("EXTRA_BLACKLIST", [])),
            })
        return {
            "profile": profile,
            "desc": profile_config.get("desc") or default_profile.get("desc"),
            "pid": pid,
            "running": process_alive(pid),
            "last_log": lines[-1] if lines else "",
            "mode": "testnet" if getattr(cfg, "BINANCE_TESTNET", True) else "live",
            "live_trading_enabled": bool(getattr(cfg, "LIVE_TRADING_ENABLED", False)),
            "max_open_positions": getattr(cfg, "MAX_OPEN_POSITIONS", None),
            "position_pct": getattr(cfg, "POSITION_PCT", None),
            "leverage": getattr(cfg, "LEVERAGE", None),
            "scan_interval": getattr(cfg, "SCAN_INTERVAL", None),
            "monitor_interval": getattr(cfg, "MONITOR_INTERVAL", None),
            "max_loss_per_trade": profile_config.get("MAX_LOSS_PER_TRADE", getattr(cfg, "MAX_LOSS_PER_TRADE", None)),
            "max_sl_pct": profile_config.get("V11I_MAX_SL_PCT", getattr(cfg, "V11I_MAX_SL_PCT", None)),
            "signal_quality_min": profile_config.get("V8_SIGNAL_QUALITY_MIN", getattr(cfg, "V8_SIGNAL_QUALITY_MIN", None)),
            "mtf_agree_min": profile_config.get("MTF_AGREE_MIN", getattr(cfg, "MTF_AGREE_MIN", None)),
            "blacklist_count": len(getattr(cfg, "ACTIVE_STATIC_BLACKLIST", [])),
            "profiles": profile_rows,
        }
    except Exception as exc:
        return {"profile": "-", "desc": f"读取策略配置失败: {exc}", "running": False}


def build_allocation_status(
    positions: list[dict[str, Any]],
    balance_usdt: float | None,
    strategy: dict[str, Any],
) -> dict[str, Any]:
    position_pct = strategy.get("position_pct")
    max_open = strategy.get("max_open_positions")
    leverage = strategy.get("leverage")
    current_margin = round(sum(float(p.get("position_usd") or 0) for p in positions), 4)
    current_notional = round(sum(float(p.get("notional_usd") or 0) for p in positions), 4)
    open_positions = len(positions)
    margin_ratio = round(current_margin / float(balance_usdt) * 100, 4) if balance_usdt else None
    notional_ratio = round(current_notional / float(balance_usdt) * 100, 4) if balance_usdt else None
    effective_leverage = round(current_notional / current_margin, 4) if current_margin else None
    target_margin = None
    max_margin = None
    target_notional = None
    if balance_usdt is not None and position_pct is not None:
        target_margin = round(float(balance_usdt) * float(position_pct) / 100, 4)
        target_notional = round(target_margin * float(leverage or 1), 4)
        if max_open is not None:
            max_margin = round(target_margin * int(max_open), 4)
    return {
        "balance_usdt": balance_usdt,
        "position_pct": position_pct,
        "leverage": leverage,
        "max_open_positions": max_open,
        "open_positions": open_positions,
        "margin_ratio_pct": margin_ratio,
        "notional_ratio_pct": notional_ratio,
        "effective_leverage": effective_leverage,
        "target_margin_usd": target_margin,
        "target_notional_usd": target_notional,
        "max_margin_usd": max_margin,
        "current_margin_usd": current_margin,
        "current_notional_usd": current_notional,
        "remaining_slots": max(0, int(max_open) - open_positions) if max_open is not None else None,
        "note": "当前策略状态/Profile 已先隐藏；此处只按 Binance API 的真实余额和真实持仓计算。",
    }


def signed_monitor_get(endpoint: str, params: dict[str, Any] | None = None) -> tuple[Any | None, str | None]:
    api_key, api_secret, _testnet, trade_fapi = monitor_settings()
    if not api_key or not api_secret:
        return None, "BINANCE_API_KEY / BINANCE_API_SECRET 未配置"
    payload = dict(params or {})
    payload["timestamp"] = int(time.time() * 1000)
    payload["recvWindow"] = 5000
    query = urlencode(payload)
    signature = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    payload["signature"] = signature
    try:
        resp = requests.get(
            f"{trade_fapi}{endpoint}",
            params=payload,
            headers={"X-MBX-APIKEY": api_key},
            timeout=MONITOR_API_TIMEOUT,
        )
    except requests.RequestException as exc:
        return None, f"Binance API 请求失败: {exc}"
    if resp.status_code != 200:
        return None, f"Binance API {resp.status_code}: {resp.text[:240]}"
    try:
        return resp.json(), None
    except ValueError:
        return None, "Binance API 返回非 JSON"


def fetch_binance_positions() -> tuple[list[dict[str, Any]], str | None]:
    data, error = signed_monitor_get("/fapi/v2/positionRisk")
    if error:
        return [], error
    if not isinstance(data, list):
        return [], "Binance API positionRisk 返回格式异常"
    positions = []
    for item in data:
        try:
            if float(item.get("positionAmt", 0)) != 0:
                positions.append(item)
        except (TypeError, ValueError):
            continue
    return positions, None


def fetch_all_leverages() -> tuple[dict[str, int], str | None]:
    """获取所有币种的杠杆信息（包括持仓量为0的）"""
    data, error = signed_monitor_get("/fapi/v2/positionRisk")
    if error:
        return {}, error
    if not isinstance(data, list):
        return {}, "Binance API positionRisk 返回格式异常"
    leverage_map = {}
    for item in data:
        symbol = item.get("symbol", "")
        try:
            leverage = int(float(item.get("leverage", 1)))
        except (TypeError, ValueError):
            continue
        if symbol:
            leverage_map[symbol] = max(1, leverage)
    return leverage_map, None


def fetch_binance_balance() -> tuple[float | None, str | None]:
    data, error = signed_monitor_get("/fapi/v2/balance")
    if error:
        return None, error
    if not isinstance(data, list):
        return None, "Binance API balance 返回格式异常"
    for item in data:
        if item.get("asset") == "USDT":
            try:
                return float(item.get("balance", 0)), None
            except (TypeError, ValueError):
                return None, "USDT balance 格式异常"
    return None, "未找到 USDT balance"


def format_minutes(minutes: int | float | None) -> str:
    if minutes is None:
        return "-"
    total = max(0, int(minutes))
    days, rem = divmod(total, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}天{hours}h" if hours else f"{days}天"
    if hours:
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    return f"{mins}m"


def history_position_values(close_qty: float, entry_price: float, exit_price: float, leverage: int | float | None) -> dict[str, Any]:
    entry_notional = close_qty * entry_price
    exit_notional = close_qty * exit_price
    margin_usd = None
    leverage_display = None
    if leverage and leverage > 0:
        margin_usd = entry_notional / float(leverage)
        leverage_display = int(leverage) if float(leverage).is_integer() else round(float(leverage), 1)
    return {
        "position_usd": round(margin_usd if margin_usd is not None else entry_notional, 2),
        "margin_usd": round(margin_usd, 2) if margin_usd is not None else None,
        "notional_usd": round(entry_notional, 2),
        "entry_notional_usd": round(entry_notional, 2),
        "exit_notional_usd": round(exit_notional, 2),
        "leverage": leverage_display,
        "pnl_base_usd": margin_usd if margin_usd is not None else entry_notional,
    }


def fetch_binance_open_orders() -> tuple[list[dict[str, Any]], str | None]:
    data, error = signed_monitor_get("/fapi/v1/openOrders")
    if error:
        return [], error
    if not isinstance(data, list):
        return [], "Binance API openOrders 返回格式异常"
    return data, None


def fetch_income_history(now: datetime, days: int = 30) -> tuple[list[dict[str, Any]], str | None]:
    start_date = now.date() - timedelta(days=days - 1)
    start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=TZ_UTC8)
    end_dt = datetime.combine(now.date(), datetime.max.time(), tzinfo=TZ_UTC8)
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    rows: list[dict[str, Any]] = []

    for page in range(1, 21):
        data, error = signed_monitor_get(
            "/fapi/v1/income",
            {
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": "1000",
                "page": page,
            },
        )
        if error:
            return [], error
        if not isinstance(data, list):
            return [], "Binance API income 返回格式异常"
        rows.extend(data)
        if len(data) < 1000:
            break
    return rows, None


def protection_orders_by_symbol(orders: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for order in orders:
        symbol = str(order.get("symbol") or "").upper()
        order_type = str(order.get("type") or "").upper()
        if not symbol or order_type not in {"STOP", "STOP_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_MARKET"}:
            continue
        raw_stop = order.get("stopPrice") or order.get("price") or 0
        try:
            trigger = float(raw_stop)
        except (TypeError, ValueError):
            trigger = 0.0
        kind = "sl" if order_type in {"STOP", "STOP_MARKET"} else "tp"
        grouped.setdefault(symbol, []).append({
            "kind": kind,
            "side": str(order.get("side") or "").upper(),
            "type": order_type,
            "trigger": trigger,
        })
    return grouped


def protection_summary(
    orders: list[dict[str, Any]] | None,
    amt: float,
    local_trade: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    close_side = "SELL" if amt > 0 else "BUY"
    usable = [o for o in (orders or []) if not o.get("side") or o.get("side") == close_side]
    sl_orders = [o for o in usable if o.get("kind") == "sl"]
    tp_orders = [o for o in usable if o.get("kind") == "tp"]

    stop_loss = local_trade.get("stop_loss")
    take_profit = local_trade.get("take_profit")
    if sl_orders:
        stop_loss = sl_orders[0].get("trigger") or stop_loss
    if tp_orders:
        take_profit = tp_orders[0].get("trigger") or take_profit

    has_sl = stop_loss not in (None, "", 0, 0.0)
    has_tp = take_profit not in (None, "", 0, 0.0)
    if has_sl and has_tp:
        text = "SL/TP 已挂"
        css = "green"
    elif has_sl:
        text = "仅 SL"
        css = "amber"
    elif has_tp:
        text = "仅 TP"
        css = "amber"
    elif error:
        text = "读取失败"
        css = "amber"
    else:
        text = "未挂保护"
        css = "red"

    detail_parts = []
    if has_sl:
        detail_parts.append(f"SL {round(float(stop_loss), 6)}")
    if has_tp:
        detail_parts.append(f"TP {round(float(take_profit), 6)}")
    detail = " · ".join(detail_parts) if detail_parts else ("openOrders 读取失败" if error else "无 SL/TP 挂单")
    return {
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "protection_text": text,
        "protection_detail": detail,
        "protection_class": css,
    }


def fetch_binance_user_trades(now: datetime) -> tuple[list[dict[str, Any]], str | None]:
    """从 Binance API 获取用户交易历史"""
    # 获取所有币种的历史交易
    data, error = signed_monitor_get("/fapi/v1/userTrades", {"limit": "1000"})
    if error:
        return [], error
    if not isinstance(data, list):
        return [], "Binance API userTrades 返回格式异常"

    # 获取所有币种的杠杆信息（包括持仓量为0的）
    leverage_map, _ = fetch_all_leverages()

    # 获取资金费率记录 - 获取所有历史
    income_data, income_error = signed_monitor_get("/fapi/v2/income", {"limit": "1500"})
    # 按币种汇总所有资金费率（不限制时间）
    funding_by_symbol: dict[str, float] = {}
    if not income_error and isinstance(income_data, list):
        for item in income_data:
            if item.get("incomeType") == "FUNDING_FEE":
                symbol = item.get("symbol", "")
                if symbol:
                    amount = float(item.get("amount", 0))
                    funding_by_symbol[symbol] = funding_by_symbol.get(symbol, 0) + amount

    # 按币种分组交易
    symbol_trades: dict[str, list[dict]] = {}
    for trade in data:
        symbol = trade.get("symbol", "")
        if symbol not in symbol_trades:
            symbol_trades[symbol] = []
        symbol_trades[symbol].append(trade)

    # 先计算每个币种的总交易量（用于分配资金费率）
    symbol_total_qty: dict[str, float] = {}
    for trade in data:
        symbol = trade.get("symbol", "")
        qty = float(trade.get("qty", 0))
        symbol_total_qty[symbol] = symbol_total_qty.get(symbol, 0) + qty

    history_positions = []

    for symbol, trades in symbol_trades.items():
        # 按时间排序
        trades = sorted(trades, key=lambda t: int(t.get("time", 0)))

        # 使用队列来跟踪开仓：[price, qty, commission, time]
        long_entries = []  # 多头开仓队列
        short_entries = []  # 空头开仓队列

        for trade in trades:
            side = trade.get("side")  # BUY 或 SELL
            qty = float(trade.get("qty", 0))
            price = float(trade.get("price", 0))
            trade_id = trade.get("id", trade.get("orderId", ""))
            time_ms = int(trade.get("time", 0))
            commission = float(trade.get("commission", 0))

            trade_time = datetime.fromtimestamp(time_ms / 1000, TZ_UTC8)

            # 获取该币种的杠杆
            symbol_leverage = leverage_map.get(symbol, None)
            # 如果没有杠杆信息，不计算保证金价值，只使用名义价值

            if side == "BUY":
                # 买入：可能是开多仓 或 平空仓
                # 先平空仓
                remaining_qty = qty
                while remaining_qty > 0 and short_entries:
                    entry_price, entry_qty, entry_comm, entry_time = short_entries[0]
                    close_qty = min(remaining_qty, entry_qty)

                    # 空头盈亏 = (开仓价 - 平仓价) * 数量
                    gross_pnl = (entry_price - price) * close_qty
                    trade_comm = entry_comm * (close_qty / entry_qty) + commission * (close_qty / qty)

                    # 资金费率：从币种总资金费率中按交易量比例分配
                    symbol_funding = funding_by_symbol.get(symbol, 0)
                    total_qty = symbol_total_qty.get(symbol, 1)
                    funding_fee = symbol_funding * (close_qty / total_qty) if total_qty > 0 else 0

                    net_pnl = gross_pnl - trade_comm + funding_fee

                    values = history_position_values(close_qty, entry_price, price, symbol_leverage)
                    pnl_base = values["pnl_base_usd"]

                    history_positions.append({
                        "id": str(trade_id),
                        "symbol": symbol,
                        "direction": "short",
                        "entry_structure": "Binance",
                        "signal_reason": "交易所记录",
                        "strategy_profile": "testnet" if monitor_settings()[2] else "live",
                        "position_usd": values["position_usd"],
                        "margin_usd": values["margin_usd"],
                        "notional_usd": values["notional_usd"],
                        "entry_notional_usd": values["entry_notional_usd"],
                        "exit_notional_usd": values["exit_notional_usd"],
                        "qty": round(close_qty, 4),
                        "leverage": values["leverage"],
                        "entry_price": entry_price,
                        "exit_price": price,
                        "realized_pnl_usd": round(net_pnl, 4),
                        "realized_pnl_pct": round((net_pnl / pnl_base) * 100, 4) if pnl_base and pnl_base > 0 else 0,
                        "commission_usd": round(trade_comm, 4),
                        "funding_fee_usd": round(funding_fee, 4),
                        "opened_day": entry_time.strftime("%m/%d"),
                        "opened_time": entry_time.strftime("%H:%M"),
                        "closed_day": trade_time.strftime("%m/%d"),
                        "closed_time": trade_time.strftime("%H:%M"),
                        "duration_minutes": int((trade_time - entry_time).total_seconds() // 60),
                        "status": "closed",
                        "status_text": "已平仓",
                        "updated_at": trade_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "binance-testnet" if monitor_settings()[2] else "binance-live",
                    })

                    # 更新队列中的数量
                    short_entries[0][1] -= close_qty
                    if short_entries[0][1] <= 0.0001:
                        short_entries.pop(0)
                    remaining_qty -= close_qty

                # 剩余作为开多仓
                if remaining_qty > 0:
                    long_entries.append([price, remaining_qty, commission, trade_time])

            elif side == "SELL":
                # 卖出：可能是开空仓 或 平多仓
                # 先平多仓
                remaining_qty = abs(qty)
                while remaining_qty > 0 and long_entries:
                    entry_price, entry_qty, entry_comm, entry_time = long_entries[0]
                    close_qty = min(remaining_qty, entry_qty)

                    # 多头盈亏 = (平仓价 - 开仓价) * 数量
                    gross_pnl = (price - entry_price) * close_qty
                    trade_comm = entry_comm * (close_qty / entry_qty) + commission * (close_qty / abs(qty))

                    # 资金费率：从币种总资金费率中按交易量比例分配
                    symbol_funding = funding_by_symbol.get(symbol, 0)
                    total_qty = symbol_total_qty.get(symbol, 1)
                    funding_fee = symbol_funding * (close_qty / total_qty) if total_qty > 0 else 0

                    net_pnl = gross_pnl - trade_comm + funding_fee

                    values = history_position_values(close_qty, entry_price, price, symbol_leverage)
                    pnl_base = values["pnl_base_usd"]

                    history_positions.append({
                        "id": str(trade_id),
                        "symbol": symbol,
                        "direction": "long",
                        "entry_structure": "Binance",
                        "signal_reason": "交易所记录",
                        "strategy_profile": "testnet" if monitor_settings()[2] else "live",
                        "position_usd": values["position_usd"],
                        "margin_usd": values["margin_usd"],
                        "notional_usd": values["notional_usd"],
                        "entry_notional_usd": values["entry_notional_usd"],
                        "exit_notional_usd": values["exit_notional_usd"],
                        "qty": round(close_qty, 4),
                        "leverage": values["leverage"],
                        "entry_price": entry_price,
                        "exit_price": price,
                        "realized_pnl_usd": round(net_pnl, 4),
                        "realized_pnl_pct": round((net_pnl / pnl_base) * 100, 4) if pnl_base and pnl_base > 0 else 0,
                        "commission_usd": round(trade_comm, 4),
                        "funding_fee_usd": round(funding_fee, 4),
                        "opened_day": entry_time.strftime("%m/%d"),
                        "opened_time": entry_time.strftime("%H:%M"),
                        "closed_day": trade_time.strftime("%m/%d"),
                        "closed_time": trade_time.strftime("%H:%M"),
                        "duration_minutes": int((trade_time - entry_time).total_seconds() // 60),
                        "status": "closed",
                        "status_text": "已平仓",
                        "updated_at": trade_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "binance-testnet" if monitor_settings()[2] else "binance-live",
                    })

                    # 更新队列中的数量
                    long_entries[0][1] -= close_qty
                    if long_entries[0][1] <= 0.0001:
                        long_entries.pop(0)
                    remaining_qty -= close_qty

                # 剩余作为开空仓
                if remaining_qty > 0:
                    short_entries.append([price, remaining_qty, commission, trade_time])

    # 按平仓时间倒序排列
    history_positions.sort(key=lambda p: p.get("updated_at", ""), reverse=True)

    return history_positions[:100], None


def fetch_daily_balance_history(now: datetime) -> tuple[list[dict[str, Any]], str | None]:
    """获取每日资产历史 - 使用和每日盈亏相同的计算逻辑"""
    from datetime import timedelta

    # 获取当前余额
    api_balance, _ = fetch_binance_balance()
    if api_balance is None:
        api_balance = 10000

    # 获取所有类型的资金流水记录
    all_income = []
    data, error = fetch_income_history(now, days=30)
    if error:
        print(f"[DEBUG] Balance: Failed to fetch income: {error}")
    else:
        if isinstance(data, list):
            target_types = {"TRADE", "REALIZED_PNL", "FUNDING_FEE", "COMMISSION"}
            for item in data:
                income_type = item.get("incomeType", "")
                if income_type in target_types:
                    all_income.append(item)

    # 按日期汇总每日变化
    daily_change: dict[str, float] = {}
    for item in all_income:
        time_ms = int(item.get("time", 0))
        trade_date = datetime.fromtimestamp(time_ms / 1000, TZ_UTC8).strftime("%Y-%m-%d")
        amount = float(item.get("income", 0) or item.get("amount", 0))
        daily_change[trade_date] = daily_change.get(trade_date, 0) + amount

    # 从当前余额往回推算每日余额
    balance_at_date: dict[str, float] = {}
    running_balance = api_balance

    # 从最新日期往回推算（按日期倒序）
    sorted_dates = sorted(daily_change.keys(), reverse=True)
    for date in sorted_dates:
        balance_at_date[date] = running_balance
        running_balance -= daily_change[date]

    # 最老数据的余额（用于填补之前的空白）
    oldest_balance = running_balance if sorted_dates else api_balance

    # 生成30天的数据
    equity_curve = []
    latest_date = now.date()

    for i in range(30):  # 从最老的日期开始
        check_date = latest_date - timedelta(days=29 - i)
        date_str = check_date.strftime("%Y-%m-%d")

        if date_str in balance_at_date:
            equity_curve.append({
                "date": date_str,
                "balance_usd": round(balance_at_date[date_str], 2)
            })
        elif equity_curve:
            # 没有数据的日子，使用前一天的余额
            account_balance = equity_curve[-1]["balance_usd"]
            equity_curve.append({
                "date": date_str,
                "balance_usd": round(account_balance, 2)
            })
        else:
            # 最开始的日期，使用最老余额
            equity_curve.append({
                "date": date_str,
                "balance_usd": round(oldest_balance, 2)
            })

    print(f"[DEBUG] Balance: Returning {len(equity_curve)} records")
    return equity_curve, None


def fetch_daily_pnl(now: datetime) -> tuple[list[dict[str, Any]], str | None]:
    """获取每日盈亏数据"""
    # 获取当前余额
    api_balance, _ = fetch_binance_balance()
    if api_balance is None:
        api_balance = 10000

    # 获取所有类型的资金流水记录
    all_income = []
    data, error = fetch_income_history(now, days=30)
    if error:
        print(f"[DEBUG] Failed to fetch income: {error}")
        return [], error
    if not isinstance(data, list):
        print(f"[DEBUG] Income API unexpected response: {type(data)}")
        return [], "API返回格式异常"

    # 过滤出我们需要的类型
    target_types = {"TRADE", "REALIZED_PNL", "FUNDING_FEE", "COMMISSION"}
    for item in data:
        income_type = item.get("incomeType", "")
        if income_type in target_types:
            all_income.append(item)

    print(f"[DEBUG] Total income: {len(data)}, filtered: {len(all_income)}")

    # 按日期和类型汇总
    daily_data: dict[str, dict[str, Any]] = {}
    for item in all_income:
        time_ms = int(item.get("time", 0))
        trade_date = datetime.fromtimestamp(time_ms / 1000, TZ_UTC8).strftime("%Y-%m-%d")
        income_type = item.get("incomeType", "")
        amount = float(item.get("income", 0) or item.get("amount", 0))

        if trade_date not in daily_data:
            daily_data[trade_date] = {
                "trade": 0, "funding": 0, "commission": 0,
                "trades_count": 0, "win_count": 0
            }

        if income_type in ["TRADE", "REALIZED_PNL"]:
            daily_data[trade_date]["trade"] += amount
            daily_data[trade_date]["trades_count"] += 1
            if amount > 0:
                daily_data[trade_date]["win_count"] += 1
        elif income_type == "FUNDING_FEE":
            daily_data[trade_date]["funding"] += amount
        elif income_type == "COMMISSION":
            daily_data[trade_date]["commission"] += amount

    # 计算每日余额（从当前余额往回推）
    balance_at_date: dict[str, float] = {}
    running_balance = api_balance

    # 从最新日期往回推算
    sorted_dates_with_data = sorted(daily_data.keys(), reverse=True)
    for date in sorted_dates_with_data:
        balance_at_date[date] = running_balance
        d = daily_data[date]
        total_change = d["trade"] + d["funding"] + d["commission"]
        running_balance -= total_change

    # 最老数据的余额（用于填补之前的空白）
    if sorted_dates_with_data:
        oldest_balance = balance_at_date[sorted_dates_with_data[-1]]
    else:
        oldest_balance = api_balance

    # 生成30天的数据
    from datetime import timedelta
    result = []
    latest_date = now.date()

    for i in range(30):  # 从最老的日期开始
        check_date = latest_date - timedelta(days=29 - i)
        date_str = check_date.strftime("%Y-%m-%d")

        if date_str in daily_data:
            d = daily_data[date_str]
            total = d["trade"] + d["funding"] + d["commission"]
            trades_count = d.get("trades_count", 0)
            win_count = d.get("win_count", 0)
            win_rate = round((win_count / trades_count * 100) if trades_count > 0 else 0, 0)
            account_balance = balance_at_date.get(date_str, api_balance)

            result.append({
                "date": date_str,
                "account_balance": round(account_balance, 2),
                "daily_change": round(total, 2),
                "trade_pnl": round(d["trade"], 4),
                "funding_fee": round(d["funding"], 4),
                "commission": round(d["commission"], 4),
                "net_pnl": round(total, 4),
                "trades": f"{trades_count} / {win_rate}%",
            })
        else:
            # 没有数据的日子
            if result:
                account_balance = result[-1]["account_balance"]
            else:
                account_balance = oldest_balance

            result.append({
                "date": date_str,
                "account_balance": round(account_balance, 2),
                "daily_change": 0,
                "trade_pnl": 0,
                "funding_fee": 0,
                "commission": 0,
                "net_pnl": 0,
                "trades": "0 / 0%",
            })

    print(f"[DEBUG] Returning {len(result)} daily records")
    # 反转为倒序（最新日期在最上面）
    result.reverse()
    return result, None


def local_trade_by_symbol(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows = [t for t in trades if t.get("status") == "open" and t.get("symbol")]
    return {str(t["symbol"]).upper(): t for t in rows}


def binance_position_row(
    position: dict[str, Any],
    local_trade: dict[str, Any] | None,
    now: datetime,
    protection_orders: list[dict[str, Any]] | None = None,
    protection_error: str | None = None,
) -> dict[str, Any]:
    local_trade = local_trade or {}
    symbol = str(position.get("symbol", "-")).upper()
    amt = float(position.get("positionAmt", 0) or 0)
    entry_price = float(position.get("entryPrice", 0) or 0)
    mark_price = float(position.get("markPrice", 0) or 0)
    leverage = float(position.get("leverage", local_trade.get("leverage") or 1) or 1)
    notional = abs(float(position.get("notional", 0) or 0))
    if notional == 0 and mark_price:
        notional = abs(amt * mark_price)
    position_usd = notional / leverage if leverage else None
    pnl_usd = float(position.get("unRealizedProfit", 0) or 0)
    pnl_pct = round(pnl_usd / position_usd * 100, 4) if position_usd else None

    update_ms = int(position.get("updateTime", 0) or 0)
    opened = parse_time(local_trade.get("entry_time"))
    if not opened and update_ms:
        opened = datetime.fromtimestamp(update_ms / 1000, TZ_UTC8)
    elapsed_minutes = int((now - opened).total_seconds() // 60) if opened else 0
    remaining_minutes = max(0, MONITOR_TTL_MINUTES - elapsed_minutes) if opened else None
    ttl_pct = round(min(100, max(0, elapsed_minutes / MONITOR_TTL_MINUTES * 100)), 2) if opened else 0
    lock_text = "观察中" if pnl_usd > 0 else "未触发"
    lock_class = "green" if pnl_usd > 0 else "muted"
    if local_trade.get("trail_high") or local_trade.get("trail_low"):
        lock_text = "追踪中"
        lock_class = "amber"
    protection = protection_summary(protection_orders, amt, local_trade, protection_error)

    return {
        "id": local_trade.get("id"),
        "symbol": symbol,
        "direction": "long" if amt > 0 else "short",
        "entry_structure": local_trade.get("signal_strength") or local_trade.get("signal_type") or "Binance",
        "latest_structure": (local_trade.get("tech_snapshot") or {}).get("ema_trend") or "mark",
        "signal_reason": local_trade.get("signal_reason") or f"positionAmt {amt:g}",
        "strategy_profile": local_trade.get("strategy_profile") or ("testnet" if monitor_settings()[2] else "live"),
        "position_usd": round(position_usd, 4) if position_usd is not None else None,
        "notional_usd": round(notional, 4),
        "leverage": int(leverage) if leverage.is_integer() else leverage,
        "entry_price": entry_price,
        "current_price": mark_price,
        "floating_pnl_usd": round(pnl_usd, 4),
        "floating_pnl_pct": pnl_pct,
        "mfe_usd": max_favorable_excursion(local_trade, mark_price) if local_trade else round(max(pnl_usd, 0), 4),
        "lock_text": lock_text,
        "lock_class": lock_class,
        "stop_loss": protection["stop_loss"],
        "take_profit": protection["take_profit"],
        "protection_text": protection["protection_text"],
        "protection_detail": protection["protection_detail"],
        "protection_class": protection["protection_class"],
        "ttl_text": f"剩余 {format_minutes(remaining_minutes)}" if opened and remaining_minutes else ("已到期" if opened else "-"),
        "held_text": f"已持仓约 {format_minutes(elapsed_minutes)}" if opened else "开仓时间未知",
        "ttl_pct": ttl_pct,
        "opened_day": opened.strftime("%m/%d") if opened else "-",
        "opened_time": opened.strftime("%H:%M") if opened else "-",
        "status": "open",
        "status_text": "持仓中",
        "updated_at": now.strftime("%H:%M:%S"),
        "source": "binance-testnet" if monitor_settings()[2] else "binance-live",
    }


def monitor_row(trade: dict[str, Any], now: datetime) -> dict[str, Any]:
    current_price = current_price_for(trade)
    pnl_usd, pnl_pct = floating_pnl(trade, current_price)
    mfe_usd = max_favorable_excursion(trade, current_price)
    opened = parse_time(trade.get("entry_time"))
    elapsed_minutes = int((now - opened).total_seconds() // 60) if opened else 0
    remaining_minutes = max(0, MONITOR_TTL_MINUTES - elapsed_minutes) if opened else None
    ttl_pct = round(min(100, max(0, elapsed_minutes / MONITOR_TTL_MINUTES * 100)), 2) if opened else 0
    lock_text = "未触发"
    lock_class = "muted"
    if pnl_usd is not None and pnl_usd > 0:
        lock_text = "观察中"
        lock_class = "green"
    if trade.get("trail_high") or trade.get("trail_low"):
        lock_text = "追踪中"
        lock_class = "amber"

    signal_reason = trade.get("signal_reason") or trade.get("signal_type") or "-"
    entry_structure = trade.get("signal_strength") or trade.get("signal_type") or "-"
    latest_structure = (trade.get("tech_snapshot") or {}).get("ema_trend") or trade.get("signal_type") or "-"

    return {
        "id": trade.get("id"),
        "symbol": trade.get("symbol", "-"),
        "direction": trade.get("direction", "-"),
        "entry_structure": entry_structure,
        "latest_structure": latest_structure,
        "signal_reason": signal_reason,
        "strategy_profile": trade.get("strategy_profile") or "-",
        "position_usd": trade.get("position_usd"),
        "notional_usd": trade.get("notional_usd"),
        "leverage": trade.get("leverage") or 1,
        "entry_price": trade.get("entry_price"),
        "current_price": current_price,
        "floating_pnl_usd": pnl_usd,
        "floating_pnl_pct": pnl_pct,
        "mfe_usd": mfe_usd,
        "lock_text": lock_text,
        "lock_class": lock_class,
        "stop_loss": trade.get("stop_loss"),
        "take_profit": trade.get("take_profit"),
        "protection_text": "SL/TP 已挂" if trade.get("stop_loss") and trade.get("take_profit") else ("仅 SL" if trade.get("stop_loss") else ("仅 TP" if trade.get("take_profit") else "未挂保护")),
        "protection_detail": " · ".join([part for part in [f"SL {trade.get('stop_loss')}" if trade.get("stop_loss") else "", f"TP {trade.get('take_profit')}" if trade.get("take_profit") else ""] if part]) or "无 SL/TP 挂单",
        "protection_class": "green" if trade.get("stop_loss") and trade.get("take_profit") else ("amber" if trade.get("stop_loss") or trade.get("take_profit") else "red"),
        "ttl_text": f"剩余 {format_minutes(remaining_minutes)}" if opened and remaining_minutes else ("已到期" if opened else "-"),
        "held_text": f"已持仓约 {format_minutes(elapsed_minutes)}" if opened else "开仓时间未知",
        "ttl_pct": ttl_pct,
        "opened_day": opened.strftime("%m/%d") if opened else "-",
        "opened_time": opened.strftime("%H:%M") if opened else "-",
        "status": trade.get("status", "-"),
        "status_text": "持仓中" if trade.get("status") == "open" else trade.get("status", "-"),
        "updated_at": now.strftime("%H:%M:%S"),
        "source": "local",
    }


def closed_history_row(trade: dict[str, Any], now: datetime) -> dict[str, Any]:
    """处理已平仓的历史仓位数据"""
    opened = parse_time(trade.get("entry_time"))
    closed = parse_time(trade.get("exit_time"))
    direction = trade.get("direction", "-")

    # 计算持仓时长
    duration_minutes = 0
    if opened and closed:
        duration_minutes = int((closed - opened).total_seconds() // 60)
    elif opened:
        duration_minutes = int((now - opened).total_seconds() // 60)

    # 计算已实现盈亏
    try:
        entry_price = float(trade.get("entry_price") or 0)
        exit_price = float(trade.get("exit_price") or 0)
        position_usd = float(trade.get("position_usd") or 0)
        realized_pnl = float(trade.get("pnl_usd") or trade.get("realized_pnl_usd") or 0)

        # 如果没有直接的pnl_usd，尝试计算
        if not trade.get("pnl_usd") and not trade.get("realized_pnl_usd") and entry_price and exit_price:
            leverage = float(trade.get("leverage") or 1)
            price_change_pct = (exit_price - entry_price) / entry_price * 100
            if direction == "short":
                price_change_pct = -price_change_pct
            realized_pnl = round(price_change_pct / 100 * leverage * position_usd, 4)
    except (TypeError, ValueError):
        realized_pnl = 0

    pnl_pct = round(realized_pnl / position_usd * 100, 4) if position_usd else 0

    signal_reason = trade.get("signal_reason") or trade.get("signal_type") or "-"
    entry_structure = trade.get("signal_strength") or trade.get("signal_type") or "-"

    return {
        "id": trade.get("id"),
        "symbol": trade.get("symbol", "-"),
        "direction": direction,
        "entry_structure": entry_structure,
        "signal_reason": signal_reason,
        "strategy_profile": trade.get("strategy_profile") or "-",
        "position_usd": position_usd,
        "notional_usd": trade.get("notional_usd"),
        "leverage": trade.get("leverage") or 1,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "realized_pnl_usd": realized_pnl,
        "realized_pnl_pct": pnl_pct,
        "stop_loss": trade.get("stop_loss"),
        "take_profit": trade.get("take_profit"),
        "exit_reason": trade.get("exit_reason") or trade.get("close_reason") or "-",
        "opened_day": opened.strftime("%m/%d") if opened else "-",
        "opened_time": opened.strftime("%H:%M") if opened else "-",
        "closed_day": closed.strftime("%m/%d") if closed else "-",
        "closed_time": closed.strftime("%H:%M") if closed else "-",
        "duration_minutes": duration_minutes,
        "duration_text": f"{duration_minutes}m" if duration_minutes < 60 else f"{duration_minutes // 60}h {duration_minutes % 60}m",
        "status": "closed",
        "status_text": trade.get("exit_reason") or "已平仓",
        "updated_at": closed.strftime("%Y-%m-%d %H:%M:%S") if closed else now.strftime("%Y-%m-%d %H:%M:%S"),
        "source": trade.get("source") or "local",
    }


def build_monitor_payload(query: dict[str, list[str]]) -> dict[str, Any]:
    now = datetime.now(TZ_UTC8)
    _api_key, _api_secret, testnet, _trade_fapi = monitor_settings()
    api_positions, api_error = fetch_binance_positions()
    open_orders, orders_error = fetch_binance_open_orders() if api_error is None else ([], api_error)
    protection_map = protection_orders_by_symbol(open_orders)
    positions = [
        binance_position_row(
            p,
            None,
            now,
            protection_map.get(str(p.get("symbol", "")).upper(), []),
            orders_error,
        )
        for p in api_positions
    ] if api_error is None else []
    source = "binance-testnet" if testnet else "binance-live"
    floating_total = sum(float(p.get("floating_pnl_usd") or 0) for p in positions)
    api_balance, balance_error = fetch_binance_balance() if not api_error else (None, api_error)
    strategy = build_strategy_status()
    strategy["allocation"] = build_allocation_status(positions, api_balance, strategy)

    # 从交易所 API 加载历史交易记录
    history_positions, history_error = fetch_binance_user_trades(now)
    history_total_pnl = sum(float(p.get("realized_pnl_usd") or 0) for p in history_positions)

    # 获取每日资产历史
    equity_curve, equity_error = fetch_daily_balance_history(now)

    # 获取每日盈亏数据
    daily_pnl, pnl_error = fetch_daily_pnl(now)

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "strategy": strategy,
        "positions": positions,
        "history_positions": history_positions,
        "equity_curve": equity_curve,
        "daily_pnl": daily_pnl,
        "summary": {
            "open_positions": len(positions),
            "history_positions": len(history_positions),
            "history_total_pnl_usd": round(history_total_pnl, 4),
            "floating_pnl_usd": round(floating_total, 4),
            "initial_balance": api_balance,
            "source": source,
            "api_status": "error" if api_error else ("ok" if api_positions else "empty"),
            "api_error": api_error,
            "balance_error": balance_error,
            "history_error": history_error,
            "equity_error": equity_error,
            "orders_error": orders_error,
            **monitor_session(),
        },
    }


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
