from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.config import CFG

REGIMES = ("UPTREND", "DOWNTREND", "UNKNOWN")


def _get(name: str, default):
    return getattr(CFG, name, default)


@dataclass
class RegimeResult:
    regime: str
    panic:  bool
    reason: str


def _ema(closes: List[float], period: int) -> Optional[float]:
    if len(closes) < period:
        return None
    mult = 2.0 / (period + 1.0)
    val  = closes[0]
    for p in closes[1:]:
        val = (p - val) * mult + val
    return val


class MarketRegimeEngine:
    """
    Xu hướng H1 dùng EMA50 vs EMA200:
    - EMA50 > EMA200 → UPTREND  → chỉ BUY
    - EMA50 < EMA200 → DOWNTREND → chỉ SELL
    """

    def __init__(self):
        self.regime = "UNKNOWN"
        self.panic  = False
        self.last_reason = "init"

    def update(self, candles_1h: List[dict]) -> RegimeResult:
        EMA_FAST = int(_get("TREND_EMA_FAST", 50))
        EMA_SLOW = int(_get("TREND_EMA_SLOW", 200))

        if len(candles_1h) < EMA_SLOW:
            self.regime = "UNKNOWN"
            self.last_reason = f"insufficient data {len(candles_1h)}/{EMA_SLOW}"
            return RegimeResult(self.regime, False, self.last_reason)

        closes = [c["close"] for c in candles_1h]
        ema_fast = _ema(closes, EMA_FAST)
        ema_slow = _ema(closes, EMA_SLOW)

        if ema_fast is None or ema_slow is None:
            self.regime = "UNKNOWN"
            self.last_reason = "ema calc failed"
            return RegimeResult(self.regime, False, self.last_reason)

        if ema_fast > ema_slow:
            self.regime = "UPTREND"
            self.last_reason = f"ema{EMA_FAST}={ema_fast:.4f} > ema{EMA_SLOW}={ema_slow:.4f}"
        else:
            self.regime = "DOWNTREND"
            self.last_reason = f"ema{EMA_FAST}={ema_fast:.4f} < ema{EMA_SLOW}={ema_slow:.4f}"

        self.panic = False
        return RegimeResult(self.regime, False, self.last_reason)