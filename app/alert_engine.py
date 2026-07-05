from __future__ import annotations

import time
from typing import Dict

from app.config import CFG

_last_alert_time: Dict[str, float] = {}


def _get(name: str, default):
    return getattr(CFG, name, default)


def _count_block(reason: str) -> None:
    try:
        from app import main as _main
        _main._block_counts[reason] = _main._block_counts.get(reason, 0) + 1
    except Exception:
        pass


def check_signal(
    symbol: str,
    candles: list[dict],      # 1h candles
    volumes: list[float],     # 1h volumes
    spread: float,
    mode: str = "main",
    market_regime: str = "UNKNOWN",
    market_panic: bool = False,
    range_high: float = 0.0,
    range_low: float = 0.0,
    candles_1h: list[dict] | None = None,
    candles_2h: list[dict] | None = None,
):
    now = time.time()

    # ── Cooldown ──
    cooldown = float(_get("COOLDOWN_SEC_MAIN", 900))
    if now - _last_alert_time.get(symbol, 0.0) < cooldown:
        return None

    # ── Chưa đủ dữ liệu ──
    if len(candles) < 5:
        _count_block("insufficient_1h")
        return None

    if range_high <= range_low or range_high == 0:
        _count_block("no_range")
        return None

    if int(getattr(CFG, "DEBUG_ENABLED", 0)):
        print(f"[wyckoff] {symbol} regime={market_regime} range={range_low:.4f}-{range_high:.4f} last_high={candles[-1]['high']:.4f} last_close={candles[-1]['close']:.4f}")

    # ── Panic block ──
    if market_panic:
        _count_block("panic_block")
        return None

    last      = candles[-1]
    cur_high  = last["high"]
    cur_low   = last["low"]
    cur_close = last["close"]
    prev_close = candles[-2]["close"] if len(candles) >= 2 else cur_close

    # Volume SMA
    vol_confirm = float(_get("WYCKOFF_VOL_CONFIRM", 1.5))
    vol_sma_period = int(_get("WYCKOFF_VOL_SMA", 10))
    if len(volumes) >= vol_sma_period:
        vol_sma = sum(volumes[-vol_sma_period:]) / vol_sma_period
        cur_vol = volumes[-1]
        vol_ratio = cur_vol / vol_sma if vol_sma > 0 else 1
    else:
        vol_ratio = 1

    upthrust_pct = float(_get("WYCKOFF_SPRING_PCT", 0.003))  # 0.3%
    direction    = None
    signal_type  = None

    # ── UPTHRUST → SHORT (focus chính) ──
    # Nến 1h phá lên trên đỉnh range rồi đóng bên trong
    if int(getattr(CFG, "DEBUG_ENABLED", 0)):
        print(f"[wyckoff] {symbol} cur_high={cur_high:.4f} cur_low={cur_low:.4f} cur_close={cur_close:.4f} vol_ratio={vol_ratio:.2f}")

    if (cur_high > range_high * (1 + upthrust_pct) and
            cur_close < range_high and          # đóng bên trong range
            cur_close < prev_close and          # đang giảm
            vol_ratio >= vol_confirm):          # volume confirm
        direction   = "SHORT"
        signal_type = "UPTHRUST"

    # ── SPRING → LONG (secondary) ──
    # Nến 1h phá xuống dưới đáy range rồi đóng bên trong
    elif (cur_low < range_low * (1 - upthrust_pct) and
            cur_close > range_low and           # đóng bên trong range
            cur_close > prev_close and          # đang tăng
            vol_ratio >= vol_confirm and        # volume confirm
            market_regime == "ACCUMULATION"):   # chỉ LONG khi đang Accumulation
        direction   = "LONG"
        signal_type = "SPRING"

    else:
        _count_block("no_wyckoff_signal")
        return None

    # ── Regime gate ──
    if market_regime == "MARKUP" and direction == "SHORT":
        _count_block("markup_block_short")
        return None

    if market_regime == "MARKDOWN" and direction == "LONG":
        _count_block("markdown_block_long")
        return None

    # ── Candle gap filter ──
    candle_max = float(_get("CANDLE_MAX_MOVE", 0.03))
    if prev_close > 0 and abs(cur_close - prev_close) / prev_close > candle_max:
        _count_block("candle_gap")
        return None

    _last_alert_time[symbol] = now

    return {
        "symbol":        symbol,
        "direction":     direction,
        "signal_type":   signal_type,
        "score":         0,
        "high_conf":     vol_ratio >= vol_confirm * 1.5,
        "market_regime": market_regime,
        "market_panic":  bool(market_panic),
        "range_high":    range_high,
        "range_low":     range_low,
        "vol_ratio":     round(vol_ratio, 2),
    }