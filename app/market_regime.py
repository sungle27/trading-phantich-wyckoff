from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.config import CFG

REGIMES = ("TREND_UP", "TREND_DOWN", "NORMAL", "UNKNOWN")


def _get(name: str, default):
    return getattr(CFG, name, default)


@dataclass
class RegimeResult:
    regime: str
    panic:  bool
    reason: str


class MarketRegimeEngine:
    """
    Regime dựa trên 4 nến 2h liên tiếp:
    - 4 nến tăng liên tục + ratio > 0.6 + price_change > 1.2% + volume spike → TREND_UP
    - 4 nến giảm liên tục + ratio < 0.4 + price_change < -1.2% + volume spike → TREND_DOWN
    - Còn lại → NORMAL
    """

    def __init__(self):
        self.regime      = "UNKNOWN"
        self.panic       = False
        self.last_reason = "init"

    def update(self, candles_2h: List[dict]) -> RegimeResult:
        PERIOD         = int(_get("REGIME_CANDLE_PERIOD", 4))
        PRICE_THRESH   = float(_get("REGIME_PRICE_THRESHOLD", 0.012))  # 1.2%
        RATIO_UP       = float(_get("REGIME_RATIO_UP", 0.6))
        RATIO_DOWN     = float(_get("REGIME_RATIO_DOWN", 0.4))
        VOL_SPIKE      = float(_get("REGIME_VOL_SPIKE", 1.3))          # 1.3x

        # Chưa đủ dữ liệu
        if len(candles_2h) < PERIOD * 2:
            self.regime = "UNKNOWN"
            self.last_reason = f"insufficient data {len(candles_2h)}/{PERIOD*2}"
            return RegimeResult(self.regime, False, self.last_reason)

        cur  = candles_2h[-PERIOD:]    # 4 nến hiện tại
        prev = candles_2h[-PERIOD*2:-PERIOD]  # 4 nến trước

        # ── Consecutive closes ──
        closes = [c["close"] for c in cur]
        up_consecutive   = all(closes[i] > closes[i-1] for i in range(1, PERIOD))
        down_consecutive = all(closes[i] < closes[i-1] for i in range(1, PERIOD))

        # ── Body position ratio ──
        ratios = []
        for c in cur:
            rng = c["high"] - c["low"]
            if rng > 0:
                ratios.append((c["close"] - c["low"]) / rng)
        ratio_avg = sum(ratios) / len(ratios) if ratios else 0.5

        # ── Price change ──
        price_change = (closes[-1] - closes[0]) / closes[0] if closes[0] > 0 else 0

        # ── Volume spike ──
        vol_cur  = sum(c.get("volume", 0) for c in cur)  / PERIOD
        vol_prev = sum(c.get("volume", 0) for c in prev) / PERIOD
        vol_ratio = vol_cur / vol_prev if vol_prev > 0 else 1

        # ── TREND_UP ──
        if (up_consecutive and
                ratio_avg > RATIO_UP and
                price_change > PRICE_THRESH and
                vol_ratio >= VOL_SPIKE):
            self.regime = "TREND_UP"
            self.last_reason = f"trend_up: change={price_change:.3f} ratio={ratio_avg:.2f} vol={vol_ratio:.2f}"

        # ── TREND_DOWN ──
        elif (down_consecutive and
                ratio_avg < RATIO_DOWN and
                price_change < -PRICE_THRESH and
                vol_ratio >= VOL_SPIKE):
            self.regime = "TREND_DOWN"
            self.last_reason = f"trend_down: change={price_change:.3f} ratio={ratio_avg:.2f} vol={vol_ratio:.2f}"

        # ── NORMAL ──
        else:
            self.regime = "NORMAL"
            self.last_reason = f"normal: change={price_change:.3f} ratio={ratio_avg:.2f} vol={vol_ratio:.2f}"

        self.panic = False
        return RegimeResult(self.regime, False, self.last_reason)