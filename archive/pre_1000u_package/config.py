"""
交易系统配置 v11j — 策略 Profile 系统
基于v11i + 单笔风险上限风控层

策略定位:
- M40 = 保守风控挡 (默认，适合 testnet 冷启动)
- G60 = 下一阶段 testnet 主测方案 (收益/风控平衡)
- G60B = 低DD均衡档 (主推验证档)
- G60S = 低回撤严格档 (DD优先验证档)
- G60O6 = G60 + 一年真实回测拖累币种过滤 (优化验证档)
- G60P = 收益增强档 (更高收益、更高回撤)
- L7  = 研究基准 (SL 过滤因子，不直接裸上)
- D60 = 对照组 (判断连亏减仓是否有效)

下一步: testnet 跑 G60 至少 7 天，验证执行质量后再考虑实盘。
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


def get_config(name: str, default: str = "") -> str:
    """Return runtime config with shell env taking priority over .env.binance."""
    return os.environ.get(name, BINANCE_CONFIG.get(name, default))


BINANCE_API_KEY = get_config("BINANCE_API_KEY")
BINANCE_API_SECRET = get_config("BINANCE_API_SECRET")
BINANCE_TESTNET = get_config("BINANCE_TESTNET", "true").lower() == "true"

# Binance endpoints are intentionally split:
# - public market data should match backtests, so it defaults to production futures data
# - signed order/account traffic follows BINANCE_TESTNET for execution safety
DATA_FAPI = get_config("BINANCE_DATA_FAPI", "https://fapi.binance.com").rstrip("/")
TRADE_FAPI = get_config(
    "BINANCE_TRADE_FAPI",
    "https://testnet.binancefuture.com" if BINANCE_TESTNET else "https://fapi.binance.com",
).rstrip("/")

# Backward-compatible alias for old code. New code should use DATA_FAPI/TRADE_FAPI.
FAPI = TRADE_FAPI

# ═══════════════════════════════════════════════════════════════════
# 实盘硬锁 — 安全第一
# ═══════════════════════════════════════════════════════════════════
# 实盘交易必须同时满足两个条件:
# 1. ENABLE_LIVE_TRADING=true
# 2. LIVE_CONFIRM=I_UNDERSTAND_MAINNET_RISK
# 默认全部禁用，防止误操作导致真实资金损失
ENABLE_LIVE_TRADING = get_config("ENABLE_LIVE_TRADING", "false").lower() == "true"

LIVE_CONFIRM = get_config("LIVE_CONFIRM")

# 检查实盘是否启用（仅用于日志，不改变交易行为）
LIVE_TRADING_ENABLED = (not BINANCE_TESTNET) and ENABLE_LIVE_TRADING and (LIVE_CONFIRM == "I_UNDERSTAND_MAINNET_RISK")

# === 交易参数 ===
INITIAL_BALANCE = float(get_config("INITIAL_BALANCE", "5000.0"))  # 初始资金 (USDT)
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

# === 时间 ===
GRACE_PERIOD_HOURS = 4         # 入场后4h宽限期，不扫止损
# 当真实下单并挂交易所止损时，本地不应用宽限期（优先安全）
EXCHANGE_STOP_IMMEDIATE = True  # True=有交易所止损单则不使用宽限期，False=保留宽限期

# === 扫描间隔 ===
SCAN_INTERVAL = 60
MONITOR_INTERVAL = 30

# === 15m 收线异动候选池 ===
# 先用真实已收线 15m K 触发候选，再交给 V8/MTF/SL/ATR/Profile 做硬过滤。
CLOSED_15M_ANOMALY_ENABLED = get_config("CLOSED_15M_ANOMALY_ENABLED", "true").lower() == "true"
CLOSED_15M_ANOMALY_THRESHOLD_PCT = float(get_config("CLOSED_15M_ANOMALY_THRESHOLD_PCT", "1.0"))
CLOSED_15M_ANOMALY_STRONG_PCT = float(get_config("CLOSED_15M_ANOMALY_STRONG_PCT", "1.5"))
CLOSED_15M_ANOMALY_MAX_CHECK = int(get_config("CLOSED_15M_ANOMALY_MAX_CHECK", "80"))

# v13 Spike过滤 (基于26笔历史数据优化)
SPIKE_LONG_ONLY = get_config("SPIKE_LONG_ONLY", "true").lower() == "true"  # 只做多(做空全亏)
SPIKE_REQUIRE_EMA_UP = get_config("SPIKE_REQUIRE_EMA_UP", "true").lower() == "true"  # 1h EMA必须多头排列
SPIKE_MIN_RSI = float(get_config("SPIKE_MIN_RSI", "50"))  # RSI门槛
SPIKE_MIN_ATR = float(get_config("SPIKE_MIN_ATR", "0.005"))  # ATR最小阈值
SPIKE_COOLDOWN_SECS = int(get_config("SPIKE_COOLDOWN_SECS", "3600"))  # 异动冷却1小时

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
# v11i: 仓位调整系统 (基线参数，可被 Profile 覆盖)
# ═══════════════════════════════════════════════════════════════════

V11I_SHORT_V8_THRESHOLD = 5
V11I_SHORT_V8_MULT = 0.5

V11I_V8_LOW_THRESHOLD = 4.0
V11I_V8_LOW_MULT = 1.3
V11I_V8_HIGH_THRESHOLD = 6.5
V11I_V8_HIGH_MULT_LONG = 0.8
V11I_V8_HIGH_MULT_SHORT = 0.6

V11I_RSI_WEAK = 50
V11I_RSI_WEAK_MULT = 0.7
V11I_RSI_MID_LOW = 55
V11I_RSI_MID_HIGH = 60
V11I_RSI_MID_MULT = 0.4
V11I_RSI_STRONG_LOW = 65
V11I_RSI_STRONG_HIGH = 75
V11I_RSI_STRONG_MULT = 1.2
V11I_RSI_VERY_STRONG = 75
V11I_RSI_VERY_STRONG_MULT = 1.1

V11I_SL_MEDIUM_LOW = 4.0
V11I_SL_MEDIUM_HIGH = 6.0
V11I_SL_MEDIUM_MULT = 0.65
V11I_SL_WIDE_LOW = 8.0
V11I_SL_WIDE_HIGH = 10.0
V11I_SL_WIDE_MULT = 1.2

# 基线硬过滤 (可被 Profile 覆盖 V11I_MAX_SL_PCT)
V11I_MAX_ATR_PCT = 5.0
V11I_FILTER_V8_RSI = True

# 基线连亏冷却 (可被 Profile 覆盖 V11I_CONSEC_LOSS_MULT)
V11I_CONSEC_LOSS_THRESHOLD = 2
V11I_CONSEC_LOSS_MULT = 0.7

# ═══════════════════════════════════════════════════════════════════
# 策略 Profile 系统
# ═══════════════════════════════════════════════════════════════════

STRATEGY_PROFILES = {
    # M40: 保守风控挡 (默认)
    # 适合: testnet 冷启动、小资金、首次验证
    # 特点: 单笔风险上限 $40，回撤最小
    "M40": {
        "MAX_LOSS_PER_TRADE": 40.0,
        "V11I_CONSEC_LOSS_MULT": 0.7,
        "V11I_MAX_SL_PCT": 10.0,
        "desc": "保守风控挡",
    },

    # D60: 对照组
    # 用途: 判断 G60 的"连亏减仓 0.5"是否真实有效
    # 特点: 只放宽单笔上限到 $60，其他与 M40 相同
    "D60": {
        "MAX_LOSS_PER_TRADE": 60.0,
        "V11I_CONSEC_LOSS_MULT": 0.7,
        "V11I_MAX_SL_PCT": 10.0,
        "desc": "对照组(仅$60上限)",
    },

    # G60: 下一阶段 testnet 主测方案
    # 适合: 收益/风控平衡，验证后可考虑实盘
    # 特点: 单笔 $60 + 连亏减仓加强 0.5
    "G60": {
        "MAX_LOSS_PER_TRADE": 60.0,
        "V11I_CONSEC_LOSS_MULT": 0.5,
        "V11I_MAX_SL_PCT": 10.0,
        "desc": "收益风控平衡挡",
    },

    # G60B: 低DD均衡档，当前主推testnet验证档
    # 2025-05-14~2026-05-14复算: +601U / DD3.31% / PF1.72 / ROI-DD18.14
    "G60B": {
        "MAX_LOSS_PER_TRADE": 60.0,
        "V11I_CONSEC_LOSS_MULT": 0.5,
        "V11I_MAX_SL_PCT": 7.0,
        "V11I_MAX_ATR_PCT": 4.5,
        "V8_SIGNAL_QUALITY_MIN": 85,
        "MTF_AGREE_MIN": 4,
        "EXTRA_BLACKLIST": ["ADAUSDT", "LDOUSDT", "SKYAIUSDT", "SNDKUSDT", "SUIUSDT", "TONUSDT", "VVVUSDT", "XRPUSDT"],
        "desc": "低DD均衡档(主推验证)",
    },

    # G60S: 低回撤严格档，适合 DD 优先或小资金冷启动 A/B。
    # 2025-05-14~2026-05-14复算: +481U / DD2.20% / PF2.12 / ROI-DD21.83
    "G60S": {
        "MAX_LOSS_PER_TRADE": 60.0,
        "V11I_CONSEC_LOSS_MULT": 0.6,
        "V11I_MAX_SL_PCT": 6.0,
        "V11I_MAX_ATR_PCT": 4.0,
        "V8_SIGNAL_QUALITY_MIN": 85,
        "MTF_AGREE_MIN": 7,
        "EXTRA_BLACKLIST": ["ADAUSDT", "LDOUSDT", "SKYAIUSDT", "SNDKUSDT", "SUIUSDT", "TONUSDT", "VVVUSDT", "XRPUSDT"],
        "desc": "低回撤严格档(DD优先验证)",
    },

    # G60O6: 2025-05-14~2026-05-14真实一年复算优化档
    # 在G60基础上排除一年内持续拖累或显著放大回撤的6个币。
    # 适合: testnet A/B验证，不直接裸上实盘。
    "G60O6": {
        "MAX_LOSS_PER_TRADE": 60.0,
        "V11I_CONSEC_LOSS_MULT": 0.5,
        "V11I_MAX_SL_PCT": 10.0,
        "EXTRA_BLACKLIST": ["ADAUSDT", "LDOUSDT", "SKYAIUSDT", "SUIUSDT", "TONUSDT", "XRPUSDT"],
        "desc": "G60优化档(排除6个一年拖累币)",
    },

    # G60P: 收益增强档，允许更高回撤；仅用于testnet对照。
    # 2025-05-14~2026-05-14复算: +748U / DD5.40% / PF1.55 / ROI-DD13.84
    "G60P": {
        "MAX_LOSS_PER_TRADE": 60.0,
        "V11I_CONSEC_LOSS_MULT": 0.5,
        "V11I_MAX_SL_PCT": 8.0,
        "V8_SIGNAL_QUALITY_MIN": 85,
        "EXTRA_BLACKLIST": ["ADAUSDT", "LDOUSDT", "SUIUSDT", "TONUSDT", "XRPUSDT"],
        "desc": "收益增强档(testnet对照)",
    },

    # L7: 研究基准
    # 用途: 拆出"SL 过滤因子"效果，不直接裸上实盘
    # 特点: 收紧 SL 上限到 7%，无单笔硬风险帽
    "L7": {
        "MAX_LOSS_PER_TRADE": None,  # 无单笔硬风险帽
        "V11I_CONSEC_LOSS_MULT": 0.7,
        "V11I_MAX_SL_PCT": 7.0,
        "desc": "研究基准(SL≤7%)",
    },
}

# 当前激活的 Profile (通过环境变量覆盖)
STRATEGY_PROFILE = os.environ.get("STRATEGY_PROFILE", "M40")

# 应用 Profile 配置
if STRATEGY_PROFILE in STRATEGY_PROFILES:
    profile_config = STRATEGY_PROFILES[STRATEGY_PROFILE]
    if "MAX_LOSS_PER_TRADE" in profile_config:
        MAX_LOSS_PER_TRADE = profile_config["MAX_LOSS_PER_TRADE"]
    else:
        MAX_LOSS_PER_TRADE = None
    V11I_CONSEC_LOSS_MULT = profile_config.get("V11I_CONSEC_LOSS_MULT", V11I_CONSEC_LOSS_MULT)
    V11I_MAX_SL_PCT = profile_config.get("V11I_MAX_SL_PCT", 10.0)
    V11I_MAX_ATR_PCT = profile_config.get("V11I_MAX_ATR_PCT", V11I_MAX_ATR_PCT)
    V8_SIGNAL_QUALITY_MIN = profile_config.get("V8_SIGNAL_QUALITY_MIN", V8_SIGNAL_QUALITY_MIN)
    MTF_AGREE_MIN = profile_config.get("MTF_AGREE_MIN", MTF_AGREE_MIN)
    PROFILE_EXTRA_BLACKLIST = profile_config.get("EXTRA_BLACKLIST", [])
else:
    # 默认使用 M40
    MAX_LOSS_PER_TRADE = 40.0
    V11I_CONSEC_LOSS_MULT = 0.7
    V11I_MAX_SL_PCT = 10.0
    PROFILE_EXTRA_BLACKLIST = []

ACTIVE_STATIC_BLACKLIST = sorted(set(STATIC_BLACKLIST) | set(PROFILE_EXTRA_BLACKLIST))

# ═══════════════════════════════════════════════════════════════════
# 兼容旧代码
# ═══════════════════════════════════════════════════════════════════
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
