"""
交易系统配置 v11j — 回撤优化版
基于v11i + 15方案回测对比(1000U/1年/1274笔基线)
最优方案M(单笔亏损上限$40): PnL +$11462(+64%)/DD 10.6%(-75%)/月胜率97.1%/ROI/DD=108.5
"""
import os
from pathlib import Path

# === 路径 ===
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

TRADES_FILE = DATA_DIR / "trades.json"
SCANNER_STATE_FILE = DATA_DIR / "scanner_state.json"
SCANNER_LOG_FILE = DATA_DIR / "scanner.log"
OI_CACHE_FILE = DATA_DIR / "oi_cache.json"
BLACKLIST_FILE = DATA_DIR / "dynamic_blacklist.json"

# === 币安API ===
def load_binance_env():
    """加载币安API配置"""
    env_file = BASE_DIR / ".env.binance"
    config = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                config[k.strip()] = v.strip()
    return config

BINANCE_CONFIG = load_binance_env()
BINANCE_API_KEY=BINANCE_CONFIG.get("BINANCE_API_KEY", os.environ.get("BINANCE_API_KEY", ""))
BINANCE_API_SECRET=BINANCE_CONFIG.get("BINANCE_API_SECRET", os.environ.get("BINANCE_API_SECRET", ""))
BINANCE_TESTNET = BINANCE_CONFIG.get("BINANCE_TESTNET", "true").lower() == "true"

# 币安合约API base URL
FAPI = "https://testnet.binancefuture.com" if BINANCE_TESTNET else "https://fapi.binance.com"

# === 交易参数 (v4: 同步回测) ===
INITIAL_BALANCE = 5000.0       # 初始资金 (USDT)
MAX_OPEN_POSITIONS = 3         # 最大同时持仓
POSITION_PCT = 10              # 每笔仓位占比% (Kelly动态调整)
LEVERAGE = 3                   # 杠杆
COOLDOWN_HOURS = 4             # 同一币种冷却时间
MIN_VOLUME_M = 50              # 最小24h成交额百万U

# === 止损止盈 ===
DEFAULT_SL_PCT = 0.05          # 兜底止损5%
DEFAULT_TP_PCT = 0.10          # 兜底止盈10%

# === 移动止盈 ===
TRAILING_TP_ENABLED = True
TRAILING_TP_TRIGGER = 0.05     # 盈利5%后启动
TRAILING_TP_STEP = 0.025       # 回撤2.5%即平仓

# === 时间 (v4: 新增宽限期) ===
GRACE_PERIOD_HOURS = 4         # v4: 入场后4h宽限期，不扫止损
# v4: 不加MAX_HOLD — 数据显示>24h的交易盈亏不确定

# === 扫描间隔 ===
SCAN_INTERVAL = 60
MONITOR_INTERVAL = 30

# === OI/费率阈值 ===
EXTREME_NEG_FUNDING = -0.08
EXTREME_POS_FUNDING = 0.10
OI_SURGE_PCT = 5.0
MIN_OI_USD = 2_000_000

# === 趋势确认 ===
EMA_FAST = 9
EMA_SLOW = 21
RSI_PERIOD = 14
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
ATR_PERIOD = 14
ATR_SL_MULTIPLIER = 1.5

# === 环境检查评分 ===
MIN_ENV_SCORE = 4

# === v8 六维加权评分系统 ===
V8_ENABLED = True

V8_SIGNAL_WEIGHTS = {
    "oi_trend": 0.20,
    "funding_rate": 0.15,
    "price_volume": 0.25,
    "macro_environment": 0.15,
    "liquidation": 0.10,
    "smart_money": 0.15,
}

V8_SIGNAL_QUALITY_MIN = 55
V8_RR_MIN = 1.3

V8_KELLY_FRACTION = 0.25
V8_DEFAULT_WIN_RATE = 0.55

V8_PATTERN_WEIGHTS = {
    "A+": 1.0, "A": 0.85, "B+": 0.75, "B": 0.65, "C": 0.5,
}

# === v11: 静态黑名单 + 动态黑名单 ===
STATIC_BLACKLIST = [
    "ZECUSDT", "DUSDT", "NILUSDT", "SOLUSDT", "DOGEUSDT",
    "CLUSDT", "KSMUSDT", "HYPEUSDT", "TAOUSDT", "PUMPUSDT",
    "BZUSDT", "FILUSDT", "WLFIUSDT", "ONDOUSDT", "ENAUSDT",
]

# v11: v8_score最低门槛
V11_MIN_V8_SCORE = 4

# 动态黑名单
BLACKLIST_LOOKBACK_DAYS = 30
BLACKLIST_MIN_TRADES = 3
BLACKLIST_MAX_LOSS_USD = 50
BLACKLIST_MAX_WIN_RATE = 0.40
BLACKLIST_SHORT_V8_SCORE_THRESHOLD = 5
BLACKLIST_SHORT_POSITION_FACTOR = 0.5

# MTF一致性门槛
MTF_AGREE_MIN = 3

# ═══════════════════════════════════════════════════════════════════
# v11i: 仓位调整系统 (基于v11g修复3处bug)
# 回测基线(1000U/1年): 1274笔/62.7%胜率/+$6983/DD43.1%/PF1.42/月胜82.4%
# ═══════════════════════════════════════════════════════════════════

# ── 策略1: 做空额外惩罚 ──
# 来源: v11i回测 — 做空V8≥5时胜率偏低需减仓
V11I_SHORT_V8_THRESHOLD = 5     # 做空V8≥此值减半
V11I_SHORT_V8_MULT = 0.5        # 做空减仓倍率

# ── 策略2: V8反转仓位 ──
# 来源: v11i回测 — 低V8(<4)信号确定性高加仓，高V8(>6.5)噪音大减仓
V11I_V8_LOW_THRESHOLD = 4.0     # V8≤此值加仓
V11I_V8_LOW_MULT = 1.3          # 加仓倍率(多空相同)
V11I_V8_HIGH_THRESHOLD = 6.5    # V8≥此值减仓
V11I_V8_HIGH_MULT_LONG = 0.8    # 做多减仓倍率
V11I_V8_HIGH_MULT_SHORT = 0.6   # 做空减仓倍率

# ── 策略3: RSI区间仓位 (仅做多) ──
# 来源: v11i回测 — RSI反映做多动能，不同区间仓位应不同
# RSI<50: 弱势减仓 ×0.7
# RSI 55-60: 中性偏弱大幅减仓 ×0.4 (v11i新增，之前没这档)
# RSI 65-75: 强势加仓 ×1.2 (最强做多区间)
# RSI≥75: 超强但可能过热，微加仓 ×1.1
V11I_RSI_WEAK = 50              # RSI<此值减仓
V11I_RSI_WEAK_MULT = 0.7
V11I_RSI_MID_LOW = 55           # RSI 55-60大幅减仓
V11I_RSI_MID_HIGH = 60
V11I_RSI_MID_MULT = 0.4
V11I_RSI_STRONG_LOW = 65        # RSI在此区间加仓(最强)
V11I_RSI_STRONG_HIGH = 75
V11I_RSI_STRONG_MULT = 1.2
V11I_RSI_VERY_STRONG = 75       # RSI≥此值微加仓
V11I_RSI_VERY_STRONG_MULT = 1.1

# ── 策略4: SL%区间仓位 ──
# 来源: v7回测分析 — SL 4-6%是最大亏损源(33%胜率亏$208)，需大幅减仓
#        SL 8-10%是最赚区间(46%胜率)，可适当加仓
# v11i修复: SL≤4%不再减仓(之前误杀了低ATR高胜率信号)
V11I_SL_MEDIUM_LOW = 4.0        # SL 4-6%大幅减仓(最大亏损源)
V11I_SL_MEDIUM_HIGH = 6.0
V11I_SL_MEDIUM_MULT = 0.65
V11I_SL_WIDE_LOW = 8.0          # SL 8-10%加仓(最赚区间)
V11I_SL_WIDE_HIGH = 10.0
V11I_SL_WIDE_MULT = 1.2

# ── 策略5: 硬过滤规则 ──
# 来源: v11i回测 — 过滤极端参数避免重仓踩坑
V11I_MAX_SL_PCT = 10.0          # SL>10%跳过 (回测: SL>10%信号不稳定)
V11I_MAX_ATR_PCT = 5.0          # ATR>5%跳过 (回测: 高ATR波动太大)
V11I_FILTER_V8_RSI = True       # V8≥6.5+RSI<55做多跳过 (v11i教训: RSI≥55做多限制太严误杀+$816)

# ── 策略6: 连续亏损冷却 ──
# 来源: v11i回测 — 连续亏损后降低仓位避免情绪化加仓
V11I_CONSEC_LOSS_THRESHOLD = 2  # 连续亏损≥2笔后启动冷却
V11I_CONSEC_LOSS_MULT = 0.7     # 冷却期仓位倍率

# ═══════════════════════════════════════════════════════════════════
# v11j新增: 单笔亏损上限 — 开仓前缩仓(盈利同步缩小)
# ═══════════════════════════════════════════════════════════════════
# ★ 注意: 离线模拟(backtest_all_optimizations.py)之前用「亏损硬截断」
#   只截亏损不截盈利，严重高估收益。已修正为「开仓前缩仓」。
#   修正后方案M(仅上限$40): +$4,011/DD16.5%/ROI/DD=24.38(vs基线ROI/DD=16.2)
#
# 修正后的最优方案(按ROI/DD):
#   方案L(仅SL≤7%): +$5,103/DD14.7%/ROI/DD=34.81
#   方案G(连亏×0.5+上限$60): +$5,559/DD17.6%/ROI/DD=31.50
#   方案D(仅上限$60): +$5,520/DD18.6%/ROI/DD=29.71
# ═══════════════════════════════════════════════════════════════════
MAX_LOSS_PER_TRADE = 40.0       # 单笔最大亏损(USDT)，超限则缩小仓位
# 逻辑: 开仓时计算 max_loss = pos_usd × sl_pct × leverage
#        若 max_loss > MAX_LOSS_PER_TRADE，则缩小pos_usd使max_loss = MAX_LOSS_PER_TRADE

# 兼容: 旧V11G变量指向V11I
V11G_V8_LOW_THRESHOLD = V11I_V8_LOW_THRESHOLD
V11G_V8_LOW_MULT = V11I_V8_LOW_MULT
V11G_V8_HIGH_THRESHOLD = V11I_V8_HIGH_THRESHOLD
V11G_V8_HIGH_MULT = V11I_V8_HIGH_MULT_LONG
V11G_RSI_WEAK = V11I_RSI_WEAK
V11G_RSI_WEAK_MULT = V11I_RSI_WEAK_MULT
V11G_RSI_STRONG_LOW = V11I_RSI_STRONG_LOW
V11G_RSI_STRONG_HIGH = V11I_RSI_STRONG_HIGH
V11G_RSI_STRONG_MULT = V11I_RSI_STRONG_MULT
V11G_RSI_VERY_STRONG = V11I_RSI_VERY_STRONG
V11G_RSI_VERY_STRONG_MULT = V11I_RSI_VERY_STRONG_MULT
V11G_SL_MEDIUM_LOW = V11I_SL_MEDIUM_LOW
V11G_SL_MEDIUM_HIGH = V11I_SL_MEDIUM_HIGH
V11G_SL_MEDIUM_MULT = V11I_SL_MEDIUM_MULT
V11G_SL_WIDE_LOW = V11I_SL_WIDE_LOW
V11G_SL_WIDE_HIGH = V11I_SL_WIDE_HIGH
V11G_SL_WIDE_MULT = V11I_SL_WIDE_MULT
V11G_MAX_SL_PCT = V11I_MAX_SL_PCT
V11G_MAX_ATR_PCT = V11I_MAX_ATR_PCT
V11G_FILTER_V8_RSI = V11I_FILTER_V8_RSI
V11G_CONSEC_LOSS_THRESHOLD = V11I_CONSEC_LOSS_THRESHOLD
V11G_CONSEC_LOSS_MULT = V11I_CONSEC_LOSS_MULT
