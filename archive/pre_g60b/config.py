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
# ⚠️ 极端费率信号已被回测证伪（v10b: 负费率29%WR亏$178, v10c: 正费率仅1笔亏$6.34）
# 5连亏全部来自极端正费率做空。关闭这些信号。
ENABLE_EXTREME_NEG_FUNDING = False   # 回测证明亏钱，已关闭
ENABLE_EXTREME_POS_FUNDING = False   # 回测几乎无数据，模拟盘5连亏来源，已关闭
ENABLE_CRASH_BOUNCE = True           # 暴跌反弹保留
ENABLE_PUMP_SHORT = True             # 暴涨回落保留
ENABLE_OI_SURGE = True               # OI异动保留
ENABLE_FUNDING_FLIP = True           # 费率翻转保留

# === Spike专项过滤 (v13优化, 基于26笔历史订单分析) ===
# 数据结论: 做多+EMA上升 PnL=+254U(40%WR), 做空 -9U(0%WR)
#           质量≥70: +230U(58%WR), 65-70: -111U(11%WR)
#           RSI>70: +188U(60%WR), RSI<50: -39U(0%WR)
#           持仓<6h: +208U, >6h: -1093U
SPIKE_LONG_ONLY = True              # Spike只做多（做空0胜率）
SPIKE_MIN_SIGNAL_QUALITY = 70       # 信号质量门槛65→70（65-70是死亡区间）
SPIKE_MIN_RSI = 50                  # 做多RSI最低50（<50全部亏损）
SPIKE_REQUIRE_EMA_UP = True         # 做多要求EMA上升趋势
SPIKE_MAX_HOLD_HOURS = 8            # 持仓时间上限（>6h表现急剧下降）
SPIKE_MIN_QUALITY_BOOST_MULT = 1.3  # 质量≥80时加仓×1.3（高质量信号更可靠）

# 阈值（当信号开启时使用）
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
V11I_MAX_SL_PCT = 10.0          # 默认值，会被STRATEGY_PROFILE覆盖
V11I_MAX_ATR_PCT = 5.0          # ATR>5%跳过 (回测: 高ATR波动太大)
V11I_FILTER_V8_RSI = True       # V8≥6.5+RSI<55做多跳过 (v11i教训: RSI≥55做多限制太严误杀+$816)

# ── 策略6: 连续亏损冷却 ──
# 来源: v11i回测 — 连续亏损后降低仓位避免情绪化加仓
V11I_CONSEC_LOSS_THRESHOLD = 2  # 连续亏损≥2笔后启动冷却
V11I_CONSEC_LOSS_MULT = 0.7     # 默认值，会被STRATEGY_PROFILE覆盖

# ═══════════════════════════════════════════════════════════════════
# v11j Profile 系统 — 可切换策略参数集
# ═══════════════════════════════════════════════════════════════════
# M40 = 保守安全挡(testnet/小资金/冷启动)
# G60 = 下一阶段主测方案(收益弹性+风控平衡)
# D60 = 对照组(验证连亏减仓0.5的贡献)
# L7  = 研究基准(SL过滤因子，无单笔硬帽)
#
# 缩仓逻辑: 开仓前 est_risk = position_usd × signal_sl_pct × leverage × mult
#            若 est_risk > MAX_LOSS_PER_TRADE，按比例缩小mult
#            不用 raw_pnl < 0 决定是否缩仓(已修正偷看未来bug)
# ═══════════════════════════════════════════════════════════════════

STRATEGY_PROFILES = {
    "M40": {
        "description": "保守安全挡 — testnet/小资金/冷启动",
        "MAX_LOSS_PER_TRADE": 40.0,
        "V11I_CONSEC_LOSS_MULT": 0.7,
        "V11I_MAX_SL_PCT": 10.0,
    },
    "D60": {
        "description": "对照组 — 验证连亏减仓0.5的贡献",
        "MAX_LOSS_PER_TRADE": 60.0,
        "V11I_CONSEC_LOSS_MULT": 0.7,
        "V11I_MAX_SL_PCT": 10.0,
    },
    "G60": {
        "description": "下一阶段主测 — 收益弹性+风控平衡",
        "MAX_LOSS_PER_TRADE": 60.0,
        "V11I_CONSEC_LOSS_MULT": 0.5,
        "V11I_MAX_SL_PCT": 10.0,
    },
    "L7": {
        "description": "研究基准 — SL过滤因子(无单笔硬帽)",
        "MAX_LOSS_PER_TRADE": None,
        "V11I_CONSEC_LOSS_MULT": 0.7,
        "V11I_MAX_SL_PCT": 7.0,
    },
}

# 默认profile暂保持M40，不直接切线上默认
# 下一轮testnet主测G60
STRATEGY_PROFILE = "M40"

# ── 应用 Profile 覆盖 ──
_profile = STRATEGY_PROFILES.get(STRATEGY_PROFILE)
if _profile is None:
    raise ValueError(f"Unknown STRATEGY_PROFILE: {STRATEGY_PROFILE!r}. "
                     f"Available: {list(STRATEGY_PROFILES.keys())}")

MAX_LOSS_PER_TRADE = _profile["MAX_LOSS_PER_TRADE"]
V11I_CONSEC_LOSS_MULT = _profile["V11I_CONSEC_LOSS_MULT"]
V11I_MAX_SL_PCT = _profile["V11I_MAX_SL_PCT"]

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
