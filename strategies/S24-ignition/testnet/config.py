"""
S24-Ignition 配置 — capped300 + dynamic 模拟盘版
"""
import os
from pathlib import Path

# === 路径 ===
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data_S24"
DATA_DIR.mkdir(exist_ok=True)

TRADES_FILE = DATA_DIR / "s24_trades.jsonl"
DECISIONS_FILE = DATA_DIR / "s24_decisions.jsonl"
STATE_FILE = DATA_DIR / "s24_state.json"

# === 币安API ===
def load_binance_env():
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
    return os.environ.get(name, BINANCE_CONFIG.get(name, default))

BINANCE_API_KEY = get_config("BINANCE_API_KEY")
BINANCE_API_SECRET = get_config("BINANCE_API_SECRET")
BINANCE_TESTNET = get_config("BINANCE_TESTNET", "true").lower() == "true"

DATA_FAPI = get_config("BINANCE_DATA_FAPI", "https://fapi.binance.com").rstrip("/")
TRADE_FAPI = get_config(
    "BINANCE_TRADE_FAPI",
    "https://testnet.binancefuture.com" if BINANCE_TESTNET else "https://fapi.binance.com",
).rstrip("/")
FAPI = TRADE_FAPI

# === S24 策略参数 ===
INITIAL_BALANCE = 1000.0
LEVERAGE = 3
MAX_POSITIONS = 3
COOLDOWN_HOURS = 4

SPIKE_THRESHOLD = 0.012   # 15m涨幅≥1.2%
MIN_RSI = 50.0
MIN_QUALITY = 70.0
ATR_PERIOD = 14
RSI_PERIOD = 14
EMA_FAST = 9
EMA_SLOW = 21
ATR_SL_MULT = 1.5
MIN_SL_PCT = 0.030
MAX_SL_PCT = 0.090
TP_SL_RATIO = 2.5
MAX_HOLD_HOURS = 4.0
GRACE_HOURS = 0.5
SYM_WEEKLY_CAP = 5
SYM_DAILY_CAP = 2
MARGIN_CAP = 300.0

POSITION_PCT_LOW = 0.07   # quality 70-79
POSITION_PCT_MID = 0.10   # quality 80-89
POSITION_PCT_HIGH = 0.15  # quality 90+

# 动态黑名单
DYN_WIN = 10
DYN_LOSS_N = 8
DYN_PF_MIN = 0.8
DYN_SYM_LOSS_PCT = 0.03
DYN_COOL1_DAYS = 7
DYN_COOL2_DAYS = 14
DYN_DAILY_PCT = 0.03

# 交易对
COMMON_SYMBOLS = [
    "XAGUSDT","XAUUSDT","LABUSDT","SUIUSDT","XRPUSDT","CRCLUSDT",
    "SNDKUSDT","TONUSDT","GTCUSDT","1000PEPEUSDT",
    "SKYAIUSDT","VVVUSDT","MUUSDT","ADAUSDT","INTCUSDT","LDOUSDT",
    "AVAXUSDT","PAXGUSDT","AAVEUSDT",
]
DEFAULT_EXCLUDE = {"BUSDT", "BILLUSDT", "BNBUSDT", "LINKUSDT", "SAGAUSDT"}

# 扫描间隔
SCAN_INTERVAL = 60
MAX_ENTRY_LAG_SEC = 120
MIN_VOLUME_M = 50  # 最小24h成交额百万U
