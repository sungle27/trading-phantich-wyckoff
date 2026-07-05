from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from app.config import CFG

REGIMES = ("ACCUMULATION", "MARKUP", "DISTRIBUTION", "MARKDOWN", "UNKNOWN")


def _get(name: str, default):
    return getattr(CFG, name, default)


@dataclass
class RegimeResult:
    regime: str
    panic: bool
    risk_mult: float
    reason: str
    range_high: float
    range_low: float


class MarketRegimeEngine:
    """
    Wyckoff Regime Engine per-symbol:
    - Trading Range từ 30 nến 4h (5 ngày)
    - Phase detection: Accumulation/Distribution/Markup/Markdown
    - Focus: Distribution → SHORT setup
    """

    def __init__(self):
        self.regime:     str   = "UNKNOWN"
        self.panic:      bool  = False
        self.range_high: float = 0.0
        self.range_low:  float = 0.0
        self.last_reason: str  = "init"

    def update(
        self,
        candles_1h:  List[dict],  # 1h candles cho entry
        candles_4h:  List[dict],  # 4h candles cho Trading Range
    ) -> RegimeResult:

        RANGE_PERIOD   = int(_get("WYCKOFF_RANGE_PERIOD", 30))     # nến 4h
        BREAKOUT_PCT   = float(_get("WYCKOFF_BREAKOUT_PCT", 0.003)) # 0.3%
        PRIOR_TREND_N  = int(_get("WYCKOFF_PRIOR_TREND_N", 10))    # nến 4h trước range

        def _make_result():
            return RegimeResult(
                self.regime, self.panic, 1.0,
                self.last_reason, self.range_high, self.range_low
            )

        # Chưa đủ dữ liệu
        if len(candles_4h) < RANGE_PERIOD + PRIOR_TREND_N:
            self.regime = "UNKNOWN"
            self.panic  = False
            self.last_reason = "insufficient 4h data"
            return _make_result()

        # ── Trading Range từ 4h ──
        range_candles   = candles_4h[-(RANGE_PERIOD + 1):-1]
        self.range_high = max(c["high"]  for c in range_candles)
        self.range_low  = min(c["low"]   for c in range_candles)

        last      = candles_4h[-1]
        cur_close = last["close"]

        # ── Prior trend trước range ──
        pre_range       = candles_4h[-(RANGE_PERIOD + PRIOR_TREND_N + 1):-(RANGE_PERIOD + 1)]
        prior_first     = pre_range[0]["close"]  if pre_range else cur_close
        prior_last      = pre_range[-1]["close"] if pre_range else cur_close
        prior_trend_pct = (prior_last - prior_first) / prior_first if prior_first > 0 else 0

        # ── Volume trend trong range ──
        vols = [c.get("volume", 0) for c in range_candles]
        if len(vols) >= 10:
            vol_first_half = sum(vols[:len(vols)//2]) / (len(vols)//2)
            vol_last_half  = sum(vols[len(vols)//2:]) / (len(vols) - len(vols)//2)
            vol_trend = (vol_last_half - vol_first_half) / vol_first_half if vol_first_half > 0 else 0
        else:
            vol_trend = 0

        # ── Xác định Phase ──

        # MARKUP: giá breakout lên trên range
        if cur_close > self.range_high * (1 + BREAKOUT_PCT):
            self.regime = "MARKUP"
            self.panic  = False
            self.last_reason = f"markup: close={cur_close:.4f} > high={self.range_high:.4f}"
            return _make_result()

        # MARKDOWN: giá breakdown xuống dưới range
        if cur_close < self.range_low * (1 - BREAKOUT_PCT):
            self.regime = "MARKDOWN"
            self.panic  = cur_close < self.range_low * (1 - BREAKOUT_PCT * 4)
            self.last_reason = f"markdown: close={cur_close:.4f} < low={self.range_low:.4f}"
            return _make_result()

        # Trong range — phân biệt Accumulation vs Distribution
        if prior_trend_pct > 0.01:
            # Uptrend trước range + volume tăng → DISTRIBUTION
            self.regime = "DISTRIBUTION"
            self.last_reason = f"distribution: prior={prior_trend_pct:.3f} vol_trend={vol_trend:.3f}"
        elif prior_trend_pct < -0.01:
            # Downtrend trước range → ACCUMULATION
            self.regime = "ACCUMULATION"
            self.last_reason = f"accumulation: prior={prior_trend_pct:.3f}"
        else:
            self.regime = "UNKNOWN"
            self.last_reason = f"unknown: prior={prior_trend_pct:.3f}"

        self.panic = False
        return _make_result()