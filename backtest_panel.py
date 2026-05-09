#!/usr/bin/env python3
"""
交易系统参数面板 + 回测器
用法: python3 backtest_panel.py
浏览器打开 http://localhost:8787
"""
import yaml, json, os, sys, time, threading, re
from pathlib import Path
from flask import Flask, render_template_string, request, jsonify, Response

BASE = Path(__file__).parent
PARAMS_FILE = BASE / "params.yaml"
RESULT_FILE = BASE / "data" / "backtest_v8_result.json"

app = Flask(__name__)

# 全局回测状态
bt_status = {"running": False, "log": "", "done": False, "result": None}

# ============================================================
# YAML 加载/保存
# ============================================================
def load_params():
    with open(PARAMS_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def save_params(data):
    with open(PARAMS_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

def flatten(d, prefix=""):
    """将嵌套dict扁平化为 {路径: 值}"""
    out = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(flatten(v, key))
        elif isinstance(v, list):
            out[key] = v
        else:
            out[key] = v
    return out

# ============================================================
# 回测执行
# ============================================================
def run_backtest_thread():
    """在子线程中运行回测"""
    global bt_status
    bt_status = {"running": True, "log": "", "done": False, "result": None}
    
    import subprocess
    proc = subprocess.Popen(
        [sys.executable, str(BASE / "backtest_v8.py"), "--from-params"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=str(BASE), text=True, bufsize=1
    )
    
    for line in proc.stdout:
        bt_status["log"] += line
        # 保留最近2000行
        lines = bt_status["log"].split("\n")
        if len(lines) > 2000:
            bt_status["log"] = "\n".join(lines[-2000:])
    
    proc.wait()
    bt_status["running"] = False
    bt_status["done"] = True
    
    # 读取结果
    if RESULT_FILE.exists():
        with open(RESULT_FILE, "r") as f:
            bt_status["result"] = json.load(f)

# ============================================================
# HTML 模板
# ============================================================
HTML_TEMPLATE = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>交易系统参数面板</title>
<style>
:root {
  --bg: #0d1117; --card: #161b22; --border: #30363d;
  --text: #e6edf3; --muted: #8b949e; --accent: #58a6ff;
  --green: #3fb950; --red: #f85149; --yellow: #d29922;
  --blue: #58a6ff;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { 
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg); color: var(--text); padding: 20px;
  max-width: 1400px; margin: 0 auto;
}
h1 { text-align: center; margin-bottom: 8px; font-size: 24px; }
.subtitle { text-align: center; color: var(--muted); margin-bottom: 24px; font-size: 14px; }

/* Tabs */
.tabs { display: flex; gap: 4px; margin-bottom: 20px; flex-wrap: wrap; }
.tab {
  padding: 8px 16px; background: var(--card); border: 1px solid var(--border);
  border-radius: 8px 8px 0 0; cursor: pointer; color: var(--muted);
  font-size: 13px; transition: all 0.2s;
}
.tab.active { background: var(--accent); color: white; border-color: var(--accent); }
.tab:hover { color: var(--text); }

/* Panel */
.panel { display: none; }
.panel.active { display: block; }
.card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: 12px; padding: 20px; margin-bottom: 16px;
}
.card h3 { color: var(--accent); margin-bottom: 16px; font-size: 16px; }

/* Grid */
.param-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
.param-item {
  display: flex; flex-direction: column; gap: 4px;
}
.param-item label {
  font-size: 12px; color: var(--muted); display: flex; justify-content: space-between;
}
.param-item label .val { color: var(--accent); font-weight: 600; }
.param-item input[type="number"],
.param-item input[type="text"] {
  background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
  padding: 8px 12px; color: var(--text); font-size: 14px; width: 100%;
  transition: border-color 0.2s;
}
.param-item input:focus { outline: none; border-color: var(--accent); }
.param-item .hint { font-size: 11px; color: var(--muted); }

/* Toggle */
.toggle-wrap { display: flex; align-items: center; gap: 10px; }
.toggle {
  width: 44px; height: 24px; background: var(--border); border-radius: 12px;
  position: relative; cursor: pointer; transition: background 0.2s;
}
.toggle.on { background: var(--green); }
.toggle::after {
  content: ''; width: 20px; height: 20px; background: white;
  border-radius: 50%; position: absolute; top: 2px; left: 2px;
  transition: transform 0.2s;
}
.toggle.on::after { transform: translateX(20px); }

/* Buttons */
.btn-row { display: flex; gap: 12px; margin: 20px 0; flex-wrap: wrap; }
.btn {
  padding: 10px 24px; border-radius: 8px; border: none;
  font-size: 14px; font-weight: 600; cursor: pointer; transition: all 0.2s;
}
.btn-primary { background: var(--accent); color: white; }
.btn-primary:hover { background: #79c0ff; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-success { background: var(--green); color: white; }
.btn-danger { background: var(--red); color: white; }
.btn-secondary { background: var(--card); color: var(--text); border: 1px solid var(--border); }

/* Result */
.result-box {
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  padding: 16px; font-family: 'Courier New', monospace; font-size: 13px;
  white-space: pre-wrap; max-height: 600px; overflow-y: auto;
  line-height: 1.6;
}
.result-stats {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 12px; margin-bottom: 16px;
}
.stat-card {
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  padding: 12px; text-align: center;
}
.stat-card .val { font-size: 24px; font-weight: 700; }
.stat-card .label { font-size: 12px; color: var(--muted); margin-top: 4px; }
.green { color: var(--green); }
.red { color: var(--red); }

/* Spinner */
.spinner {
  display: inline-block; width: 16px; height: 16px;
  border: 2px solid var(--border); border-top-color: var(--accent);
  border-radius: 50%; animation: spin 0.8s linear infinite;
  margin-right: 8px;
}
@keyframes spin { to { transform: rotate(360deg); } }

/* Log */
#log-output { color: var(--muted); font-size: 12px; }

/* Responsive */
@media (max-width: 768px) {
  .param-grid { grid-template-columns: 1fr; }
  .result-stats { grid-template-columns: repeat(2, 1fr); }
}

/* Slider */
input[type="range"] {
  -webkit-appearance: none; width: 100%; height: 6px;
  background: var(--border); border-radius: 3px; outline: none;
}
input[type="range"]::-webkit-slider-thumb {
  -webkit-appearance: none; width: 18px; height: 18px;
  background: var(--accent); border-radius: 50%; cursor: pointer;
}

/* Trade list table */
.trade-table { width: 100%; border-collapse: collapse; font-size: 12px; }
.trade-table th { 
  text-align: left; padding: 8px; border-bottom: 1px solid var(--border);
  color: var(--muted); font-weight: 600;
}
.trade-table td { padding: 6px 8px; border-bottom: 1px solid var(--border); }
.trade-table tr:hover { background: rgba(88,166,255,0.05); }

/* Comparison */
.compare-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 16px;
}
</style>
</head>
<body>

<h1>⚡ 交易系统参数面板</h1>
<p class="subtitle">调参数 → 保存 → 回测 → 看结果 | 参数越少干预越少，结果越好</p>

<!-- Tabs -->
<div class="tabs" id="tabs">
    <div class="tab active" data-panel="basics">📐 基础 & 信号</div>
  <div class="tab" data-panel="sltp">🎯 止损止盈</div>
  <div class="tab" data-panel="filter">🚪 入场 & 仓位</div>
  <div class="tab" data-panel="weights">⚖️ 六维权重</div>
  <div class="tab" data-panel="quality">📊 信号质量</div>
  <div class="tab" data-panel="mtf">🕐 异动 & 多框架</div>
  <div class="tab" data-panel="backtest">🧪 回测结果</div>
</div>

<!-- Panel 1: Basics -->
<div class="panel active" id="panel-basics">
  <div class="card">
    <h3>基础设置</h3>
    <div class="param-grid" id="grid-basics"></div>
  </div>
  <div class="card">
    <h3>信号触发阈值</h3>
    <div class="param-grid" id="grid-signal"></div>
  </div>
</div>

<!-- Panel 2: SL/TP -->
<div class="panel" id="panel-sltp">
  <div class="card">
    <h3>止损止盈</h3>
    <div class="param-grid" id="grid-sltp"></div>
  </div>
  <div class="card">
    <h3>移动止盈 / 分批止盈</h3>
    <div class="param-grid" id="grid-trail"></div>
  </div>
</div>

<!-- Panel 3: Filter -->
<div class="panel" id="panel-filter">
  <div class="card">
    <h3>入场过滤</h3>
    <div class="param-grid" id="grid-filter"></div>
  </div>
  <div class="card">
    <h3>Kelly 仓位管理</h3>
    <div class="param-grid" id="grid-kelly"></div>
  </div>
</div>

<!-- Panel 4: Weights -->
<div class="panel" id="panel-weights">
  <div class="card">
    <h3>六维评分权重（总和 = 1.0）</h3>
    <div id="weights-viz" style="margin-bottom:16px;"></div>
    <div class="param-grid" id="grid-weights"></div>
    <p style="color:var(--muted);font-size:12px;margin-top:12px;">
      当前总和: <span id="weight-sum" style="color:var(--accent);font-weight:600;">-</span>
    </p>
  </div>
  <div class="card">
    <h3>六维评分内部阈值</h3>
    <div class="param-grid" id="grid-dimscore"></div>
  </div>
</div>

<!-- Panel 5: Signal Quality -->
<div class="panel" id="panel-quality">
  <div class="card">
    <h3>信号质量评分（0-100）</h3>
    <div class="param-grid" id="grid-quality"></div>
  </div>
  <div class="card">
    <h3>宏观 FGI 映射</h3>
    <div class="param-grid" id="grid-fgi"></div>
  </div>
</div>

<!-- Panel 6: MTF -->
<div class="panel" id="panel-mtf">
  <div class="card">
    <h3>15分钟异动扫描</h3>
    <div class="param-grid" id="grid-spike"></div>
  </div>
  <div class="card">
    <h3>多时间框架分析</h3>
    <div class="param-grid" id="grid-mtf"></div>
    <p style="color:var(--muted);font-size:12px;margin-top:12px;">
      框架列表: 30m, 1h, 4h, 6h, 8h, 12h, 1d, 3d, 1w, 1M<br>
      大框架(1d及以上)额外加权重，小框架额外加权重
    </p>
  </div>
</div>

<!-- Panel 7: Backtest -->
<div class="panel" id="panel-backtest">
  <div class="btn-row">
    <button class="btn btn-primary" id="btn-run" onclick="runBacktest()">
      🚀 运行回测
    </button>
    <button class="btn btn-secondary" onclick="loadResult()">📂 加载上次结果</button>
    <button class="btn btn-secondary" onclick="exportParams()">📋 导出参数</button>
    <button class="btn btn-danger" onclick="resetParams()">🔄 恢复默认</button>
    <span id="run-status" style="line-height:40px;color:var(--muted);"></span>
  </div>
  
  <div id="result-area" style="display:none;">
    <div class="card">
      <h3>📊 回测结果</h3>
      <div class="result-stats" id="result-stats"></div>
    </div>
    <div class="card">
      <h3>📋 交易明细</h3>
      <div style="overflow-x:auto;">
        <table class="trade-table" id="trade-table"></table>
      </div>
    </div>
    <div class="card">
      <h3>📝 运行日志</h3>
      <div class="result-box" id="log-output"></div>
    </div>
  </div>
</div>

<!-- Save/Load -->
<div style="position:fixed;bottom:20px;right:20px;display:flex;gap:8px;">
  <button class="btn btn-success" onclick="saveAll()" id="btn-save">💾 保存参数</button>
</div>

<div id="toast" style="position:fixed;top:20px;right:20px;background:var(--green);color:white;
  padding:12px 24px;border-radius:8px;display:none;font-weight:600;z-index:999;"></div>

<script>
let params = {};
let paramInputs = {};

// Category mapping for panels
const CATEGORIES = {
  basics: [
    "基础.初始资金", "基础.杠杆倍数", "基础.最大持仓数", "基础.冷却时间_小时", "基础.每笔风险比例",
  ],
  signal: [
    "信号触发.极端负费率阈值", "信号触发.极端正费率阈值", "信号触发.负费率连续期数",
    "信号触发.费率窗口期数", "信号触发.负费率S级阈值", "信号触发.负费率A级阈值",
    "信号触发.正费率S级阈值", "信号触发.正费率A级阈值",
    "信号触发.超级逼空阈值", "信号触发.超级崩盘阈值",
    "信号触发.暴跌做多阈值", "信号触发.暴跌A级阈值",
    "信号触发.暴涨做空阈值", "信号触发.暴涨回落最低", "信号触发.暴涨A级阈值",
  ],
  sltp: [
    "止损止盈.ATR止损乘数", "止损止盈.最小止损百分比", "止损止盈.默认止损", "止损止盈.默认止盈",
    "止损止盈.止盈止损比", "止损止盈.ATR低波动阈值", "止损止盈.ATR高波动减",
  ],
  trail: [
    "止盈策略.趋势跟随开关", "止盈策略.趋势跟随激活",
    "止盈策略.分批止盈开关", "止盈策略.分批止盈比例", "止盈策略.分批后保本",
    "止盈策略.移动止盈开关", "止盈策略.移动止盈激活", "止盈策略.移动止盈回撤",
    "止盈策略.做多EMA偏离", "止盈策略.做空EMA偏离",
  ],
  filter: [
    "入场过滤.信号质量最低分", "入场过滤.RR比最低", "入场过滤.趋势过滤", "入场过滤.EMA偏离系数",
  ],
  kelly: [
    "Kelly仓位.Kelly保守系数", "Kelly仓位.默认胜率",
    "Kelly仓位.仓位下限", "Kelly仓位.仓位上限",
    "Kelly仓位.质量因子下限", "Kelly仓位.质量因子上限", "Kelly仓位.宏观因子下限",
  ],
  weights: [
    "六维权重.OI趋势", "六维权重.资金费率", "六维权重.量价因子",
    "六维权重.宏观环境", "六维权重.清算数据", "六维权重.聪明钱",
  ],
  dimscore: [
    "六维评分.OI变化有效阈值", "六维评分.OI同向得分", "六维评分.OI背离得分",
    "六维评分.做多负费率强阈值", "六维评分.做多负费率弱阈值",
    "六维评分.做多正费率惩罚强", "六维评分.做多正费率惩罚弱",
    "六维评分.做空正费率强阈值", "六维评分.做空正费率弱阈值",
    "六维评分.做空负费率惩罚强", "六维评分.做空负费率惩罚弱",
    "六维评分.顺趋势加分", "六维评分.逆趋势减分",
    "六维评分.做多RSI超卖", "六维评分.做多RSI偏低", "六维评分.做多RSI偏高",
    "六维评分.做空RSI超买", "六维评分.做空RSI偏高", "六维评分.做空RSI偏低",
    "六维评分.量价缩放因子", "六维评分.量价评分上限",
    "六维评分.BTC微涨做多加分", "六维评分.BTC大跌做多减分",
    "六维评分.FGI恐惧做多加分", "六维评分.FGI贪婪做多减分", "六维评分.宏观评分上限",
    "六维评分.费率极端清算阈值", "六维评分.清算减分", "六维评分.清算评分上限",
    "六维评分.做多负费率聪明钱阈值", "六维评分.做多负费率聪明钱加分",
    "六维评分.做空正费率聪明钱阈值", "六维评分.做空正费率聪明钱加分",
    "六维评分.聪明钱评分上限",
  ],
  quality: [
    "信号质量.量价满分", "信号质量.形态满分", "信号质量.订单流满分", "信号质量.宏观满分",
    "信号质量.订单流基础分", "信号质量.宏观基础分", "信号质量.形态基础分",
    "信号质量.跨框架确认加分", "信号质量.跨框架同向加分",
    "信号质量.做多顺势加分", "信号质量.做多中性加分", "信号质量.做多逆势减分",
    "信号质量.做多RSI超卖加分", "信号质量.做多RSI偏低加分", "信号质量.做多RSI偏高减分",
    "信号质量.做空顺势加分", "信号质量.做空中性加分", "信号质量.做空逆势减分",
    "信号质量.做空RSI超买加分", "信号质量.做空RSI偏高加分", "信号质量.做空RSI偏低减分",
    "信号质量.高成交量阈值", "信号质量.高成交量加分", "信号质量.中成交量阈值",
    "信号质量.中成交量加分", "信号质量.低成交量阈值", "信号质量.低成交量减分",
  ],
  fgi: [
    "FGI映射.BTC极度恐惧偏离", "FGI映射.BTC恐惧偏离", "FGI映射.BTC偏恐惧偏离",
    "FGI映射.BTC中性偏离", "FGI映射.BTC偏贪婪偏离", "FGI映射.BTC贪婪偏离", "FGI映射.BTC极度贪婪",
  ],
  spike: [
    "异动扫描.异动阈值", "异动扫描.扫描周期", "异动扫描.最小ATR过滤", "异动扫描.冷却期_同向",
  ],
  mtf: [
    "多时间框架.一致性权重", "多时间框架.大框架权重", "多时间框架.小框架权重", "多时间框架.趋势一致最低",
  ],
};

function getVal(path) {
  const keys = path.split(".");
  let v = params;
  for (const k of keys) v = v[k];
  return v;
}

function setVal(path, val) {
  const keys = path.split(".");
  let v = params;
  for (let i = 0; i < keys.length - 1; i++) v = v[keys[i]];
  const lastKey = keys[keys.length - 1];
  if (typeof v[lastKey] === "boolean") v[lastKey] = val === true || val === "true";
  else if (typeof v[lastKey] === "number") v[lastKey] = parseFloat(val) || 0;
  else v[lastKey] = val;
}

function buildInput(path) {
  const val = getVal(path);
  const name = path.split(".").pop();
  const id = path.replace(/\./g, "_");
  
  if (typeof val === "boolean") {
    return `<div class="param-item">
      <label>${name} <span class="val">${val ? "✅ 开" : "❌ 关"}</span></label>
      <div class="toggle-wrap">
        <div class="toggle ${val ? 'on' : ''}" id="tog_${id}" onclick="toggleParam('${path}')"></div>
      </div>
    </div>`;
  }
  
  const step = (typeof val === "number" && Math.abs(val) < 1) ? "0.01" : 
               (typeof val === "number" && Math.abs(val) < 100) ? "0.1" : "1";
  
  return `<div class="param-item">
    <label>${name} <span class="val" id="lbl_${id}">${val}</span></label>
    <input type="number" step="${step}" value="${val}" id="inp_${id}"
      oninput="updateParam('${path}', this.value, '${id}')">
  </div>`;
}

function renderGrid(gridId, paths) {
  const el = document.getElementById(gridId);
  if (!el) return;
  el.innerHTML = paths.map(p => buildInput(p)).join("");
}

function toggleParam(path) {
  const val = !getVal(path);
  setVal(path, val);
  const id = path.replace(/\./g, "_");
  const tog = document.getElementById("tog_" + id);
  const lbl = document.querySelector(`#tog_${id}`).closest(".param-item").querySelector(".val");
  tog.classList.toggle("on", val);
  lbl.textContent = val ? "✅ 开" : "❌ 关";
}

function updateParam(path, val, id) {
  setVal(path, val);
  const lbl = document.getElementById("lbl_" + id);
  if (lbl) lbl.textContent = val;
  // Update weight sum if weights changed
  if (path.startsWith("六维权重.")) updateWeightViz();
}

function updateWeightViz() {
  const w = params["六维权重"];
  const sum = Object.values(w).reduce((a, b) => a + b, 0);
  document.getElementById("weight-sum").textContent = sum.toFixed(2);
  document.getElementById("weight-sum").style.color = 
    Math.abs(sum - 1.0) < 0.01 ? "var(--green)" : "var(--red)";
  
  // Bar chart
  const labels = { "OI趋势": "OI", "资金费率": "费率", "量价因子": "量价", 
                   "宏观环境": "宏观", "清算数据": "清算", "聪明钱": "聪明钱" };
  const colors = ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#bc8cff", "#79c0ff"];
  let html = '<div style="display:flex;gap:4px;height:24px;border-radius:4px;overflow:hidden;">';
  let i = 0;
  for (const [k, v] of Object.entries(w)) {
    const pct = (v / sum * 100).toFixed(1);
    html += `<div style="flex:${v};background:${colors[i]};display:flex;align-items:center;
      justify-content:center;font-size:11px;font-weight:600;min-width:40px;" 
      title="${k}: ${v} (${pct}%)">${labels[k]||k}</div>`;
    i++;
  }
  html += '</div>';
  document.getElementById("weights-viz").innerHTML = html;
}

function renderAll() {
  renderGrid("grid-basics", CATEGORIES.basics);
  renderGrid("grid-signal", CATEGORIES.signal);
  renderGrid("grid-sltp", CATEGORIES.sltp);
  renderGrid("grid-trail", CATEGORIES.trail);
  renderGrid("grid-filter", CATEGORIES.filter);
  renderGrid("grid-kelly", CATEGORIES.kelly);
  renderGrid("grid-weights", CATEGORIES.weights);
  renderGrid("grid-dimscore", CATEGORIES.dimscore);
  renderGrid("grid-quality", CATEGORIES.quality);
  renderGrid("grid-fgi", CATEGORIES.fgi);
  updateWeightViz();
}

// Tabs
document.querySelectorAll(".tab").forEach(tab => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach(p => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("panel-" + tab.dataset.panel).classList.add("active");
  };
});

// Save
async function saveAll() {
  const btn = document.getElementById("btn-save");
  btn.textContent = "⏳ 保存中...";
  const res = await fetch("/api/params", {
    method: "POST", headers: {"Content-Type": "application/json"},
    body: JSON.stringify(params)
  });
  if (res.ok) showToast("✅ 参数已保存到 params.yaml");
  else showToast("❌ 保存失败");
  btn.textContent = "💾 保存参数";
}

// Run backtest
let pollTimer = null;
async function runBacktest() {
  await saveAll();
  const btn = document.getElementById("btn-run");
  btn.disabled = true;
  btn.textContent = "⏳ 运行中...";
  document.getElementById("run-status").innerHTML = '<span class="spinner"></span>回测运行中...';
  document.getElementById("result-area").style.display = "block";
  document.getElementById("log-output").textContent = "⏳ 回测启动中...\n";
  
  await fetch("/api/run", { method: "POST" });
  
  pollTimer = setInterval(async () => {
    const res = await fetch("/api/status");
    const data = await res.json();
    
    if (data.log) {
      document.getElementById("log-output").textContent = data.log;
      document.getElementById("log-output").scrollTop = 999999;
    }
    
    if (!data.running) {
      clearInterval(pollTimer);
      btn.disabled = false;
      btn.textContent = "🚀 运行回测";
      document.getElementById("run-status").textContent = "✅ 回测完成";
      if (data.result) renderResult(data.result);
    }
  }, 2000);
}

function renderResult(r) {
  const s = r.summary || {};
  const pnl = s.total_pnl || 0;
  const pnlClass = pnl >= 0 ? "green" : "red";
  
  document.getElementById("result-stats").innerHTML = `
    <div class="stat-card"><div class="val ${pnlClass}">${pnl>=0?'+':''}${(pnl).toFixed(0)}U</div><div class="label">总盈亏</div></div>
    <div class="stat-card"><div class="val">${s.total_trades||0}</div><div class="label">总交易</div></div>
    <div class="stat-card"><div class="val ${pnlClass}">${((s.win_rate||0)*100).toFixed(0)}%</div><div class="label">胜率</div></div>
    <div class="stat-card"><div class="val">${(s.avg_win||0).toFixed(1)}U</div><div class="label">平均盈利</div></div>
    <div class="stat-card"><div class="val">${(s.avg_loss||0).toFixed(1)}U</div><div class="label">平均亏损</div></div>
    <div class="stat-card"><div class="val">${(s.profit_factor||0).toFixed(2)}</div><div class="label">盈亏比</div></div>
    <div class="stat-card"><div class="val ${pnlClass}">${(s.max_drawdown||0).toFixed(1)}%</div><div class="label">最大回撤</div></div>
    <div class="stat-card"><div class="val">${s.signals_found||0}→${s.signals_traded||0}</div><div class="label">信号→交易</div></div>
  `;
  
  // Trade table
  const trades = r.trades || [];
  let html = `<thead><tr><th>#</th><th>时间</th><th>币种</th><th>方向</th><th>等级</th>
    <th>仓位</th><th>SL</th><th>评分</th><th>质量</th><th>盈亏</th><th>原因</th></tr></thead><tbody>`;
  trades.forEach((t, i) => {
    const cls = (t.pnl||0) >= 0 ? "green" : "red";
    html += `<tr>
      <td>${i+1}</td><td>${t.date||'-'}</td><td>${t.symbol||'-'}</td>
      <td>${t.direction||'-'}</td><td>${t.grade||'-'}</td>
      <td>${(t.position||0).toFixed(0)}U</td><td>${((t.sl_pct||0)*100).toFixed(1)}%</td>
      <td>${t.score||'-'}</td><td>${t.quality||'-'}</td>
      <td class="${cls}">${(t.pnl||0)>=0?'+':''}${(t.pnl||0).toFixed(1)}U</td>
      <td>${t.close_reason||'-'}</td>
    </tr>`;
  });
  html += "</tbody>";
  document.getElementById("trade-table").innerHTML = html;
}

async function loadResult() {
  const res = await fetch("/api/result");
  const data = await res.json();
  if (data.error) { showToast("❌ 没有历史回测结果"); return; }
  document.getElementById("result-area").style.display = "block";
  renderResult(data);
  showToast("📂 已加载上次结果");
}

function exportParams() {
  const blob = new Blob([JSON.stringify(params, null, 2)], {type: "application/json"});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "params_" + new Date().toISOString().slice(0,10) + ".json";
  a.click();
  showToast("📋 参数已导出");
}

function resetParams() {
  if (!confirm("确认恢复默认参数？")) return;
  fetch("/api/reset", {method: "POST"}).then(() => {
    loadAndRender();
    showToast("🔄 已恢复默认参数");
  });
}

function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.style.display = "block";
  t.style.background = msg.includes("❌") ? "var(--red)" : "var(--green)";
  setTimeout(() => t.style.display = "none", 2000);
}

async function loadAndRender() {
  const res = await fetch("/api/params");
  params = await res.json();
  renderAll();
}

// Init
loadAndRender();
</script>
</body>
</html>
"""

# ============================================================
# Flask Routes
# ============================================================
@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/params", methods=["GET"])
def get_params():
    return jsonify(load_params())

@app.route("/api/params", methods=["POST"])
def set_params():
    data = request.json
    save_params(data)
    return jsonify({"ok": True})

@app.route("/api/run", methods=["POST"])
def start_backtest():
    if bt_status["running"]:
        return jsonify({"error": "回测已在运行中"}), 400
    t = threading.Thread(target=run_backtest_thread, daemon=True)
    t.start()
    return jsonify({"ok": True})

@app.route("/api/status")
def get_status():
    return jsonify(bt_status)

@app.route("/api/result")
def get_result():
    if RESULT_FILE.exists():
        with open(RESULT_FILE, "r") as f:
            return jsonify(json.load(f))
    return jsonify({"error": "没有回测结果"})

@app.route("/api/reset", methods=["POST"])
def reset_params():
    """恢复默认参数"""
    defaults = {
        "基础": {"初始资金": 5000.0, "杠杆倍数": 3, "最大持仓数": 2, "冷却时间_小时": 24, "每笔风险比例": 0.01},
        "信号触发": {"极端负费率阈值": -0.08, "极端正费率阈值": 0.10, "负费率连续期数": 3,
            "费率窗口期数": 8, "负费率S级阈值": -0.20, "负费率A级阈值": -0.10,
            "正费率S级阈值": 0.25, "正费率A级阈值": 0.15, "超级逼空阈值": -0.30, "超级崩盘阈值": 0.30,
            "暴跌做多阈值": -0.25, "暴跌A级阈值": -0.35,
            "暴涨做空阈值": 0.50, "暴涨回落最低": 0.05, "暴涨A级阈值": 0.10},
        "止损止盈": {"ATR止损乘数": 1.5, "最小止损百分比": 0.03, "默认止损": 0.05,
            "默认止盈": 0.10, "止盈止损比": 2.5, "ATR低波动阈值": 0.02, "ATR高波动减": 0.5},
        "止盈策略": {"趋势跟随开关": True, "趋势跟随激活": 0.02,
            "分批止盈开关": True, "分批止盈比例": 0.50, "分批后保本": True,
            "移动止盈开关": True, "移动止盈激活": 0.04, "移动止盈回撤": 0.02,
            "做多EMA偏离": 0.995, "做空EMA偏离": 1.005},
        "入场过滤": {"信号质量最低分": 0, "RR比最低": 0, "趋势过滤": False, "EMA偏离系数": 0.001},
        "Kelly仓位": {"Kelly保守系数": 0.25, "默认胜率": 0.55,
            "仓位下限": 0.02, "仓位上限": 0.20, "质量因子下限": 0.5, "质量因子上限": 1.5, "宏观因子下限": 0.5},
        "六维权重": {"OI趋势": 0.20, "资金费率": 0.15, "量价因子": 0.25,
            "宏观环境": 0.15, "清算数据": 0.10, "聪明钱": 0.15},
        "六维评分": {"OI变化有效阈值": 5, "OI同向得分": 20, "OI背离得分": 8,
            "做多负费率强阈值": -0.05, "做多负费率弱阈值": -0.01,
            "做多正费率惩罚强": 0.10, "做多正费率惩罚弱": 0.05,
            "做空正费率强阈值": 0.10, "做空正费率弱阈值": 0.05,
            "做空负费率惩罚强": -0.10, "做空负费率惩罚弱": -0.05,
            "顺趋势加分": 3, "逆趋势减分": -2,
            "做多RSI超卖": 2, "做多RSI偏低": 1, "做多RSI偏高": -2,
            "做空RSI超买": 2, "做空RSI偏高": 1, "做空RSI偏低": -2,
            "量价缩放因子": 5, "量价评分上限": 25,
            "BTC微涨做多加分": 5, "BTC大跌做多减分": -5,
            "FGI恐惧做多加分": 5, "FGI贪婪做多减分": -3, "宏观评分上限": 15,
            "费率极端清算阈值": 0.15, "清算减分": -3, "清算评分上限": 10,
            "做多负费率聪明钱阈值": -0.05, "做多负费率聪明钱加分": 8,
            "做空正费率聪明钱阈值": 0.05, "做空正费率聪明钱加分": 8, "聪明钱评分上限": 15},
        "信号质量": {"量价满分": 40, "形态满分": 30, "订单流满分": 20, "宏观满分": 10,
            "订单流基础分": 10, "宏观基础分": 5, "形态基础分": 10,
            "跨框架确认加分": 10, "跨框架同向加分": 5,
            "做多顺势加分": 15, "做多中性加分": 5, "做多逆势减分": -5,
            "做多RSI超卖加分": 10, "做多RSI偏低加分": 8, "做多RSI偏高减分": -5,
            "做空顺势加分": 15, "做空中性加分": 5, "做空逆势减分": -5,
            "做空RSI超买加分": 10, "做空RSI偏高加分": 8, "做空RSI偏低减分": -5,
            "高成交量阈值": 200000000, "高成交量加分": 10,
            "中成交量阈值": 100000000, "中成交量加分": 5,
            "低成交量阈值": 50000000, "低成交量减分": -5},
        "FGI映射": {"BTC极度恐惧偏离": 0.85, "BTC恐惧偏离": 0.92,
            "BTC偏恐惧偏离": 0.97, "BTC中性偏离": 1.03,
            "BTC偏贪婪偏离": 1.10, "BTC贪婪偏离": 1.20, "BTC极度贪婪": 85},
        "FGI归一化": {"极度恐惧": [0, 25], "恐惧": [25, 40], "中性": [40, 50], "贪婪": [50, 60], "极度贪婪": [60, 75]},
        "技术指标": {"ATR周期": 14, "EMA快线": 9, "EMA慢线": 21, "RSI周期": 14},
        "币种": {"最低成交量": 50000000, "最低价格": 0.001, "最大币种数": 50,
            "排除列表": ["BTCUSDT","ETHUSDT","BNBUSDT","BUSDUSDT","USDCUSDT","TUSDUSDT","FDUSDUSDT","DAIUSDT"]},
        "回测": {"回测天数": 180, "API超时_秒": 15, "限流等待_秒": 2,
            "请求间隔_秒": 0.1, "批量间隔_秒": 0.3, "预热小时数": 50},
    }
    save_params(defaults)
    return jsonify({"ok": True})

# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    # Install deps
    try:
        import flask
    except ImportError:
        os.system(f"{sys.executable} -m pip install flask pyyaml -q")
    
    print("=" * 50)
    print("🌐 交易系统参数面板")
    print(f"📁 参数文件: {PARAMS_FILE}")
    print(f"📁 结果文件: {RESULT_FILE}")
    print(f"🔗 打开浏览器: http://localhost:8787")
    print("=" * 50)
    app.run(host="0.0.0.0", port=8787, debug=False)
