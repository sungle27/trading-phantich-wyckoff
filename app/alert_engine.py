from __future__ import annotations

import time
from typing import Dict, List, Optional

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


def _slope(candles: List[dict], period: int) -> float:
    """Tính slope % của N nến 15m gần nhất dựa trên close."""
    if len(candles) < period:
        return 0.0
    closes = [c["close"] for c in candles[-period:]]
    if closes[0] <= 0:
        return 0.0
    return (closes[-1] - closes[0]) / closes[0]


def check_signal(
    symbol: str,
    candles: list[dict],      # M15 candles
    volumes: list[float],
    spread: float,
    mode: str = "main",
    market_regime: str = "NORMAL",
    market_panic: bool = False,
    candles_1h: list[dict] | None = None,
    candles_2h: list[dict] | None = None,
):
    now = time.time()

    # ── Cooldown ──
    cooldown = float(_get("COOLDOWN_SEC_MAIN", 900))
    if now - _last_alert_time.get(symbol, 0.0) < cooldown:
        return None

    # ── Chưa đủ data ──
    if len(candles) < 10:
        _count_block("insufficient_15m")
        return None

    if market_regime == "UNKNOWN":
        _count_block("regime_unknown")
        return None

    # ── Tính slope 3 nến 15m ──
    PERIOD         = int(_get("SIGNAL_CANDLE_PERIOD", 3))
    SPIKE_THRESH   = float(_get("SIGNAL_SPIKE_THRESHOLD", 0.015))   # 1.5% đột biến
    NORMAL_THRESH  = float(_get("SIGNAL_NORMAL_THRESHOLD", 0.005))  # 0.5% bình thường

    slope = _slope(candles, PERIOD)

    direction = None

    if market_regime == "NORMAL":
        if slope > SPIKE_THRESH:
            # Tăng đột biến → SHORT (reverse)
            direction = "SHORT"
            signal_type = "REVERSE_SHORT"
        elif 0 < slope < NORMAL_THRESH:
            # Tăng nhẹ → LONG
            direction = "LONG"
            signal_type = "NORMAL_LONG"
        elif slope <= 0:
            # Giảm → SHORT
            direction = "SHORT"
            signal_type = "NORMAL_SHORT"
        else:
            # 0.5% <= slope < 1.5% → không vào
            _count_block("neutral_zone")
            return None

    elif market_regime == "TREND_UP":
        # Chỉ LONG theo trend
        if slope > 0:
            direction   = "LONG"
            signal_type = "TREND_LONG"
        else:
            _count_block("trend_up_no_long")
            return None

    elif market_regime == "TREND_DOWN":
        # Chỉ SHORT theo trend
        if slope < 0:
            direction   = "SHORT"
            signal_type = "TREND_SHORT"
        else:
            _count_block("trend_down_no_short")
            return None

    else:
        _count_block("regime_unknown")
        return None

    if not direction:
        return None

    _last_alert_time[symbol] = now

    return {
        "symbol":        symbol,
        "direction":     direction,
        "signal_type":   signal_type,
        "score":         0,
        "high_conf":     False,
        "market_regime": market_regime,
        "market_panic":  bool(market_panic),
        "slope":         round(slope * 100, 3),
    }