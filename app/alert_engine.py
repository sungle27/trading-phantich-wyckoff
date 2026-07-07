from __future__ import annotations

import time
from typing import Dict, List, Optional

from app.config import CFG

_last_alert_time: Dict[str, float] = {}

# Per-symbol breakout tracking
# {sym: {"type": "BUY"/"SELL", "level": float, "candles_since": int}}
_breakout_state: Dict[str, dict] = {}


def _get(name: str, default):
    return getattr(CFG, name, default)


def _count_block(reason: str) -> None:
    try:
        from app import main as _main
        _main._block_counts[reason] = _main._block_counts.get(reason, 0) + 1
    except Exception:
        pass


def _ema(closes: List[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    mult = 2.0 / (period + 1.0)
    val  = closes[0]
    for p in closes[1:]:
        val = (p - val) * mult + val
    return val


def _is_bullish_engulfing(candles: List[dict]) -> bool:
    if len(candles) < 2:
        return False
    prev = candles[-2]
    cur  = candles[-1]
    return (prev["close"] < prev["open"] and          # nến trước đỏ
            cur["close"] > cur["open"] and            # nến hiện tại xanh
            cur["open"]  < prev["close"] and          # mở dưới close trước
            cur["close"] > prev["open"])              # đóng trên open trước


def _is_bearish_engulfing(candles: List[dict]) -> bool:
    if len(candles) < 2:
        return False
    prev = candles[-2]
    cur  = candles[-1]
    return (prev["close"] > prev["open"] and          # nến trước xanh
            cur["close"] < cur["open"] and            # nến hiện tại đỏ
            cur["open"]  > prev["close"] and          # mở trên close trước
            cur["close"] < prev["open"])              # đóng dưới open trước


def _is_bullish_pin_bar(candle: dict, atr: float) -> bool:
    body   = abs(candle["close"] - candle["open"])
    lower  = min(candle["open"], candle["close"]) - candle["low"]
    upper  = candle["high"] - max(candle["open"], candle["close"])
    return (lower >= 2 * body and      # lower wick >= 2x body
            upper <= 0.5 * body and    # upper wick nhỏ
            body > 0)


def _is_bearish_pin_bar(candle: dict, atr: float) -> bool:
    body   = abs(candle["close"] - candle["open"])
    upper  = candle["high"] - max(candle["open"], candle["close"])
    lower  = min(candle["open"], candle["close"]) - candle["low"]
    return (upper >= 2 * body and      # upper wick >= 2x body
            lower <= 0.5 * body and    # lower wick nhỏ
            body > 0)


def _is_morning_star(candles: List[dict]) -> bool:
    if len(candles) < 3:
        return False
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    return (c1["close"] < c1["open"] and              # nến 1 đỏ
            abs(c2["close"] - c2["open"]) < abs(c1["close"] - c1["open"]) * 0.3 and  # nến 2 nhỏ
            c3["close"] > c3["open"] and              # nến 3 xanh
            c3["close"] > (c1["open"] + c1["close"]) / 2)  # đóng > giữa nến 1


def _is_evening_star(candles: List[dict]) -> bool:
    if len(candles) < 3:
        return False
    c1, c2, c3 = candles[-3], candles[-2], candles[-1]
    return (c1["close"] > c1["open"] and              # nến 1 xanh
            abs(c2["close"] - c2["open"]) < abs(c1["close"] - c1["open"]) * 0.3 and  # nến 2 nhỏ
            c3["close"] < c3["open"] and              # nến 3 đỏ
            c3["close"] < (c1["open"] + c1["close"]) / 2)  # đóng < giữa nến 1


def _atr(candles: List[dict], period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(-period, 0):
        h  = candles[i]["high"]
        l  = candles[i]["low"]
        pc = candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs)


def _is_session(hour_utc: int) -> bool:
    """London: 08-16 UTC, NY: 13-21 UTC"""
    london = 8 <= hour_utc < 16
    ny     = 13 <= hour_utc < 21
    return london or ny


def check_signal(
    symbol: str,
    candles: list[dict],      # M15 candles
    volumes: list[float],     # M15 volumes
    spread: float,
    mode: str = "main",
    market_regime: str = "UNKNOWN",
    market_panic: bool = False,
    candles_1h: list[dict] | None = None,
    candles_2h: list[dict] | None = None,
):
    now = time.time()

    # ── Session filter ──
    if int(_get("SESSION_FILTER", 1)):
        hour_utc = time.gmtime(now).tm_hour
        if not _is_session(hour_utc):
            _count_block("outside_session")
            return None

    # ── Cooldown ──
    cooldown = float(_get("COOLDOWN_SEC_MAIN", 900))
    if now - _last_alert_time.get(symbol, 0.0) < cooldown:
        return None

    # ── Chưa đủ data ──
    if len(candles) < 30:
        _count_block("insufficient_15m")
        return None

    # ── Regime gate ──
    if market_regime == "UNKNOWN":
        _count_block("regime_unknown")
        return None

    # ── ATR ──
    atr = _atr(candles, 14)
    if atr <= 0:
        return None

    # ── RSI filter ──
    rsi_period = int(_get("RSI_PERIOD", 14))
    closes_list = [c["close"] for c in candles[-rsi_period-1:]]
    if len(closes_list) >= rsi_period + 1:
        gains  = [max(closes_list[i] - closes_list[i-1], 0) for i in range(1, len(closes_list))]
        losses = [max(closes_list[i-1] - closes_list[i], 0) for i in range(1, len(closes_list))]
        avg_g  = sum(gains)  / rsi_period
        avg_l  = sum(losses) / rsi_period
        rsi    = 100 - (100 / (1 + avg_g / avg_l)) if avg_l > 0 else 100
    else:
        rsi = 50

    # ── EMA20 M15 ──
    closes_15m = [c["close"] for c in candles]
    ema20 = _ema(closes_15m, 20)

    # ── Volume SMA20 ──
    vol_sma = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
    cur_vol  = volumes[-1] if volumes else 0
    vol_ratio = cur_vol / vol_sma if vol_sma > 0 else 0

    last      = candles[-1]
    cur_high  = last["high"]
    cur_low   = last["low"]
    cur_close = last["close"]

    # ── Structural high/low (đỉnh/đáy gần nhất) ──
    lookback    = int(_get("STRUCTURE_LOOKBACK", 20))
    recent      = candles[-(lookback+1):-1]
    struct_high = max(c["high"]  for c in recent) if recent else cur_high
    struct_low  = min(c["low"]   for c in recent) if recent else cur_low

    vol_threshold = float(_get("VOL_BREAKOUT_MIN", 1.5))
    retest_max    = int(_get("RETEST_MAX_CANDLES", 5))
    sym_state     = _breakout_state.get(symbol, {})

    direction    = None
    signal_type  = None
    retest_level = None

    # ── Step 1: Check breakout ──
    if not sym_state:
        # BUY breakout: giá phá đỉnh cấu trúc với volume
        if (cur_high > struct_high and
                vol_ratio >= vol_threshold and
                atr > 0 and (cur_high - cur_low) <= 2 * atr and  # không quá lớn
                market_regime == "UPTREND" and
                rsi < float(_get("RSI_OB", 80))):
            _breakout_state[symbol] = {
                "type":         "BUY",
                "level":        struct_high,
                "candles_since": 0,
            }
            _count_block("breakout_buy_detected")

        # SELL breakout: giá phá đáy cấu trúc với volume
        elif (cur_low < struct_low and
                vol_ratio >= vol_threshold and
                atr > 0 and (cur_high - cur_low) <= 2 * atr and
                market_regime == "DOWNTREND" and
                rsi > float(_get("RSI_OS", 20))):
            _breakout_state[symbol] = {
                "type":         "SELL",
                "level":        struct_low,
                "candles_since": 0,
            }
            _count_block("breakout_sell_detected")

        return None  # chờ retest

    # ── Step 2: Đang chờ retest ──
    sym_state["candles_since"] += 1

    # Hết thời gian retest
    if sym_state["candles_since"] > retest_max:
        del _breakout_state[symbol]
        _count_block("retest_timeout")
        return None

    bo_type  = sym_state["type"]
    bo_level = sym_state["level"]

    # ── Step 3: Check retest ──
    if bo_type == "BUY":
        # Giá quay về vùng breakout (bo_level)
        retest_ok = (cur_low <= bo_level * 1.005 and   # chạm vùng breakout
                     cur_close > bo_level * 0.995)      # đóng trên bo_level
        if not retest_ok:
            return None

        # ── Step 4: Nến xác nhận BUY ──
        confirm = (
            _is_bullish_engulfing(candles) or
            _is_bullish_pin_bar(last, atr) or
            _is_morning_star(candles)
        )
        if not confirm:
            _count_block("no_confirm_candle")
            return None

        # RSI không overbought
        if rsi > float(_get("RSI_OB", 80)):
            _count_block("rsi_overbought")
            del _breakout_state[symbol]
            return None

        direction    = "LONG"
        signal_type  = "BREAKOUT_BUY"
        retest_level = cur_low  # SL dưới đáy retest

    elif bo_type == "SELL":
        # Giá quay về vùng breakout
        retest_ok = (cur_high >= bo_level * 0.995 and
                     cur_close < bo_level * 1.005)
        if not retest_ok:
            return None

        # ── Step 4: Nến xác nhận SELL ──
        confirm = (
            _is_bearish_engulfing(candles) or
            _is_bearish_pin_bar(last, atr) or
            _is_evening_star(candles)
        )
        if not confirm:
            _count_block("no_confirm_candle")
            return None

        # RSI không oversold
        if rsi < float(_get("RSI_OS", 20)):
            _count_block("rsi_oversold")
            del _breakout_state[symbol]
            return None

        direction    = "SHORT"
        signal_type  = "BREAKOUT_SELL"
        retest_level = cur_high  # SL trên đỉnh retest

    if not direction:
        return None

    # ── Clear breakout state ──
    del _breakout_state[symbol]
    _last_alert_time[symbol] = now

    return {
        "symbol":        symbol,
        "direction":     direction,
        "signal_type":   signal_type,
        "score":         0,
        "high_conf":     vol_ratio >= vol_threshold * 1.5,
        "market_regime": market_regime,
        "market_panic":  bool(market_panic),
        "retest_level":  retest_level,
        "vol_ratio":     round(vol_ratio, 2),
        "rsi":           round(rsi, 1),
        "atr":           round(atr, 6),
        "struct_high":   struct_high,
        "struct_low":    struct_low,
    }