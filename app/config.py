from __future__ import annotations

import os
from dataclasses import dataclass

# Auto-load env file nếu python-dotenv có sẵn.
# Ưu tiên: _env → .env → bỏ qua (fly.io / container đã inject qua os.environ)
try:
    import os as _os
    from dotenv import load_dotenv  # type: ignore

    for _env_file in ("_env", ".env"):
        if _os.path.exists(_env_file):
            load_dotenv(_env_file, override=True)
            break
except Exception:
    pass


def _get(key: str, default: str | None = None) -> str | None:
    v = os.getenv(key)
    if v is None:
        return default
    v = v.strip()
    return v if v != "" else default


def _req(key: str) -> str:
    v = _get(key)
    if v is None:
        raise RuntimeError(f"[CONFIG] Missing env var: {key}")
    return v


def _i(key: str, default: int) -> int:
    v = _get(key, None)
    return int(v) if v is not None else int(default)


def _f(key: str, default: float) -> float:
    v = _get(key, None)
    return float(v) if v is not None else float(default)


@dataclass(frozen=True)
class Config:
    # Required core
    BINANCE_FUTURES_WS: str = _req("BINANCE_FUTURES_WS")
    TELEGRAM_BOT_TOKEN: str = _req("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID:   str = _req("TELEGRAM_CHAT_ID")

    # Binance REST API (dùng cho wallet sync)
    BINANCE_API_KEY:    str = _get("BINANCE_API_KEY",    "")
    BINANCE_API_SECRET: str = _get("BINANCE_API_SECRET", "")

    # Wallet sync
    WALLET_SYNC_ENABLED:      int = _i("WALLET_SYNC_ENABLED",       1)
    WALLET_SYNC_INTERVAL_SEC: int = _i("WALLET_SYNC_INTERVAL_SEC",  300)  # default 5 phút

    # Execution Service (app đặt lệnh thực)
    EXECUTION_URL:   str = _get("EXECUTION_URL",   "")   # vd: http://localhost:8000
    EXECUTION_TOKEN: str = _get("EXECUTION_TOKEN", "")   # Bearer token, để trống nếu không cần

    # Runtime
    DEBUG_ENABLED:  int = _i("DEBUG_ENABLED",  0)
    HEARTBEAT_SEC:  int = _i("HEARTBEAT_SEC",  300)

    # EMA
    EMA_FAST: int = _i("EMA_FAST", 9)
    EMA_SLOW: int = _i("EMA_SLOW", 26)

    # EMA GAP
    REGIME_EMA_GAP_EARLY: float = _f("REGIME_EMA_GAP_EARLY", 0.0010)
    REGIME_EMA_GAP_MAIN:  float = _f("REGIME_EMA_GAP",       0.0055)

    # Volume
    VOLUME_SMA_LEN:     int   = _i("VOLUME_SMA_LEN",     12)
    VOLUME_RATIO_EARLY: float = _f("VOLUME_RATIO_EARLY", 2.0)
    VOLUME_RATIO_MAIN:  float = _f("VOLUME_RATIO_MAIN",  2.6)
    ENABLE_VOLUME:      int   = _i("ENABLE_VOLUME",       1)

    # Spread
    ENABLE_SPREAD:          int   = _i("ENABLE_SPREAD",          1)
    SPREAD_MAX:             float = _f("SPREAD_MAX",             0.004)
    ENABLE_SPREAD_ADVANCED: int   = _i("ENABLE_SPREAD_ADVANCED", 1)
    SPREAD_MAX_EARLY:       float = _f("SPREAD_MAX_EARLY",       0.006)
    SPREAD_MAX_MAIN:        float = _f("SPREAD_MAX_MAIN",        0.003)

    # Cooldown
    COOLDOWN_SEC_EARLY: int = _i("COOLDOWN_SEC_EARLY", 180)
    COOLDOWN_SEC_MAIN:  int = _i("COOLDOWN_SEC_MAIN",  1200)

    # Filters
    ENABLE_ANTI_TRAP:         int   = _i("ENABLE_ANTI_TRAP",           1)
    ENABLE_FOLLOW_THROUGH:    int   = _i("ENABLE_FOLLOW_THROUGH",       1)
    FOLLOW_THROUGH_BARS_EARLY: int  = _i("FOLLOW_THROUGH_BARS_EARLY",  2)
    FOLLOW_THROUGH_BARS_MAIN:  int  = _i("FOLLOW_THROUGH_BARS_MAIN",   4)

    ENABLE_WICK_FILTER:    int   = _i("ENABLE_WICK_FILTER",    1)
    WICK_MAX_RATIO_EARLY:  float = _f("WICK_MAX_RATIO_EARLY",  0.45)
    WICK_MAX_RATIO_MAIN:   float = _f("WICK_MAX_RATIO_MAIN",   0.30)

    ENABLE_MOMENTUM:    int   = _i("ENABLE_MOMENTUM",    1)
    MOMENTUM_MIN_EARLY: float = _f("MOMENTUM_MIN_EARLY", 0.003)
    MOMENTUM_MIN_MAIN:  float = _f("MOMENTUM_MIN_MAIN",  0.007)

    # NAV cố định để tính khối lượng lệnh
    FIXED_NAV: float = _f("FIXED_NAV", 200.0)  # USDT

    # Bollinger Bands filter
    ENABLE_BB_FILTER: int   = _i("ENABLE_BB_FILTER", 1)
    BB_PERIOD:        int   = _i("BB_PERIOD",        20)
    BB_STD:           float = _f("BB_STD",            2.0)
    BB_THRESHOLD:     float = _f("BB_THRESHOLD",      0.85)  # 0-1, vị trí trong band

    # HTF trend filter (Higher Timeframe)
    ENABLE_HTF_FILTER: int = _i("ENABLE_HTF_FILTER", 1)
    HTF_EMA_FAST:      int = _i("HTF_EMA_FAST",      9)
    HTF_EMA_SLOW:      int = _i("HTF_EMA_SLOW",      26)

    # Overextended filter
    ENABLE_OVEREXTENDED_FILTER: int   = _i("ENABLE_OVEREXTENDED_FILTER", 1)
    OVEREXTENDED_LOOKBACK:      int   = _i("OVEREXTENDED_LOOKBACK",      5)
    OVEREXTENDED_DROP_MAX:      float = _f("OVEREXTENDED_DROP_MAX",      0.03)
    OVEREXTENDED_PUMP_MAX:      float = _f("OVEREXTENDED_PUMP_MAX",      0.03)

    # SL / TP động theo regime
    SL_PCT:          float = _f("SL_PCT",          0.028)  # NORMAL
    TP_PCT:          float = _f("TP_PCT",          0.020)  # NORMAL
    SL_PCT_TREND:    float = _f("SL_PCT_TREND",    0.030)  # TREND
    TP_PCT_TREND:    float = _f("TP_PCT_TREND",    0.035)  # TREND
    SL_PCT_RANGE:    float = _f("SL_PCT_RANGE",    0.028)  # RANGE
    TP_PCT_RANGE:    float = _f("TP_PCT_RANGE",    0.015)  # RANGE
    SL_PCT_PANIC:    float = _f("SL_PCT_PANIC",    0.040)  # PANIC
    TP_PCT_PANIC:    float = _f("TP_PCT_PANIC",    0.020)  # PANIC
    SL_PCT_RECOVERY: float = _f("SL_PCT_RECOVERY", 0.020)  # RECOVERY
    TP_PCT_RECOVERY: float = _f("TP_PCT_RECOVERY", 0.015)  # RECOVERY

    # RSI filter
    RSI_PERIOD:      int   = _i("RSI_PERIOD",      14)
    RSI_OVERBOUGHT:  float = _f("RSI_OVERBOUGHT",  65.0)  # LONG chỉ pass nếu RSI < 65
    RSI_OVERSOLD:    float = _f("RSI_OVERSOLD",    35.0)  # SHORT chỉ pass nếu RSI > 35

    # ATR compression
    ENABLE_ATR_COMPRESSION: int   = _i("ENABLE_ATR_COMPRESSION", 1)
    ATR_SHORT:               int   = _i("ATR_SHORT",               5)
    ATR_LONG:                int   = _i("ATR_LONG",               20)
    ATR_COMPRESSION_RATIO:   float = _f("ATR_COMPRESSION_RATIO",  0.70)

    # Score thresholds
    # FIX #2: Default thực tế hơn — early=5, main=8, high_conf=12, panic=11
    # (cũ: early=6, main=10, high_conf=14, panic=13)
    SCORE_MIN_EARLY:      int = _i("SCORE_MIN_EARLY",      5)   # FIX: 6 → 5
    SCORE_MIN_MAIN:       int = _i("SCORE_MIN_MAIN",       8)   # FIX: 10 → 8
    SCORE_HIGH_CONF:      int = _i("SCORE_HIGH_CONF",      12)  # FIX: 14 → 12
    SCORE_MIN_MAIN_PANIC: int = _i("SCORE_MIN_MAIN_PANIC", 11)  # FIX: 13 → 11

    # Regime config
    ENABLE_MARKET_REGIME: int = _i("ENABLE_MARKET_REGIME", 1)
    REGIME_PROXY_1: str = _get("REGIME_PROXY_1", "BTCUSDT")  # chỉ dùng BTC

    PANIC_DROP_1H:      float = _f("PANIC_DROP_1H",      -0.04)
    PANIC_RISE_1H:      float = _f("PANIC_RISE_1H",       0.02)
    RECOVERY_BARS_1H:   int   = _i("RECOVERY_BARS_1H",    2)

    TREND_EMA_GAP_4H:    float = _f("TREND_EMA_GAP_4H",    0.0025)
    TREND_GAP_MIN_1H:    float = _f("TREND_GAP_MIN_1H",    0.0020)
    RANGE_ATR_RATIO_MAX: float = _f("RANGE_ATR_RATIO_MAX", 0.70)

    REGIME_MIN_HOLD_SEC:       int = _i("REGIME_MIN_HOLD_SEC",        1800)
    REGIME_ALERT_COOLDOWN_SEC: int = _i("REGIME_ALERT_COOLDOWN_SEC",   900)
    REGIME_NOTIFY:             int = _i("REGIME_NOTIFY",                 1)

    # Portfolio / risk
    NAV_USD:             float = _f("NAV_USD",             10000.0)
    MAX_POSITIONS:       int   = _i("MAX_POSITIONS",           8)
    MAX_TOTAL_RISK_PCT:  float = _f("MAX_TOTAL_RISK_PCT",      3.0)
    MAX_CORRELATION:     float = _f("MAX_CORRELATION",         0.85)

    # Simulation
    SIM_ENABLED:   int   = _i("SIM_ENABLED",    1)
    SIM_START_NAV: float = _f("SIM_START_NAV",  10000.0)
    SIM_RR:        float = _f("SIM_RR",          2.0)

    NAV_REPORT_SEC: int = _i("NAV_REPORT_SEC", 3600)

    # Risk per trade logic
    RISK_EARLY: float = _f("RISK_EARLY", 0.25)
    RISK_MAIN:  float = _f("RISK_MAIN",  0.50)
    RISK_MAX:   float = _f("RISK_MAX",   1.0)

    SL_ATR_MULT: float = _f("SL_ATR_MULT", 1.5)
    TP_RR:       float = _f("TP_RR",       2.0)

    # Liquidity / vol sizing
    MIN_LIQUIDITY_USD: float = _f("MIN_LIQUIDITY_USD", 5_000_000.0)
    TARGET_VOL_PCT:    float = _f("TARGET_VOL_PCT",    0.015)

    # Entry mode
    ENTRY_MODE:          str   = _get("ENTRY_MODE", "adaptive")
    ENTRY_PULLBACK_PCT:  float = _f("ENTRY_PULLBACK_PCT",  0.003)
    ENTRY_BREAKOUT_PCT:  float = _f("ENTRY_BREAKOUT_PCT",  0.0015)

    # Slippage
    SLIPPAGE_PCT: float = _f("SLIPPAGE_PCT", 0.0002)

    # Drawdown
    DD_SOFT_PCT:          float = _f("DD_SOFT_PCT",          0.06)
    DD_HARD_PCT:          float = _f("DD_HARD_PCT",          0.10)
    DD_KILL_PCT:          float = _f("DD_KILL_PCT",          0.18)
    DD_HARD_COOLDOWN_SEC: int   = _i("DD_HARD_COOLDOWN_SEC", 6 * 60 * 60)


CFG = Config()