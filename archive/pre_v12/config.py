"""
交易系统配置 v5 — v11g优化版
基于v11g回测数据(234笔/68.4%胜率/+$2987/DD3.8%/PF2.22/月7/7):
1. V8反转: V8≤4加仓×1.3(主力盈利+1996U), V8≥6.5减仓×0.6(亏损-325U)
2. RSI区间仓位: RSI65-75做多×1.2(最强+1004U), RSI<50×0.7(弱势)
3. SL区间仓位: SL 4-6%×0.65(最大亏损源-498U), SL 8-10%×1.2(最赚+323U)
4. 过滤: SL>10%跳过, ATR>5%跳过, V8≥6.5+RSI<55做多跳过
5. 连续亏损冷却: 连续2+亏损后下一笔×0.7
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
POSITION_PCT = 15              # 每笔仓位占比% (Kelly动态调整)
LEVERAGE = 5                   # 杠杆
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

# ═══ v11i: 仓位调整系统 (基于v11g修复3处bug) ═══
# 回测1000U/1年: 525笔/63.8%/+$913(+91%)/DD6.1%/月10/13盈利
# vs v11g 5000U/6月: 234笔/68.4%/+$2881/DD4.6%/PF2.08/月6/7

# 做空额外惩罚
V11I_SHORT_V8_THRESHOLD = 5     # 做空V8≥此值减半
V11I_SHORT_V8_MULT = 0.5        # 做空减半倍率

# V8反转: 低V8加仓, 高V8减仓
V11I_V8_LOW_THRESHOLD = 4.0     # V8≤此值加仓
V11I_V8_LOW_MULT = 1.3          # 加仓倍率(多空相同)
V11I_V8_HIGH_THRESHOLD = 6.5    # V8≥此值减仓
V11I_V8_HIGH_MULT_LONG = 0.8    # 做多减仓倍率
V11I_V8_HIGH_MULT_SHORT = 0.6   # 做空减仓倍率

# RSI区间仓位 (仅做多)
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

# SL%区间仓位 (v11i修复: SL≤4%不减仓)
V11I_SL_MEDIUM_LOW = 4.0        # SL 4-6%大幅减仓(最大亏损源)
V11I_SL_MEDIUM_HIGH = 6.0
V11I_SL_MEDIUM_MULT = 0.65
V11I_SL_WIDE_LOW = 8.0          # SL 8-10%加仓(最赚区间)
V11I_SL_WIDE_HIGH = 10.0
V11I_SL_WIDE_MULT = 1.2

# 过滤规则
V11I_MAX_SL_PCT = 10.0          # SL>10%跳过
V11I_MAX_ATR_PCT = 5.0          # ATR>5%跳过
V11I_FILTER_V8_RSI = True       # V8≥6.5+RSI<55做多跳过

# 连续亏损冷却
V11I_CONSEC_LOSS_THRESHOLD = 2  # 连续亏损≥2笔后启动冷却
V11I_CONSEC_LOSS_MULT = 0.7     # 冷却期仓位倍率

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
