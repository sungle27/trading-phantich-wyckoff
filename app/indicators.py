from __future__ import annotations

from collections import deque

__all__ = [
    "EMA",
    "ATR",
    "RSI",
    "wick_ratio",
    "momentum",
]


class EMA:
    def __init__(self, period: int):
        self.period = period
        self.mult = 2.0 / (period + 1.0)
        self.value: float | None = None

    def update(self, price: float) -> float | None:
        if self.value is None:
            self.value = price
        else:
            self.value = (price - self.value) * self.mult + self.value
        return self.value


class ATR:
    """
    Wilder ATR
    """
    def __init__(self, period: int):
        self.period = period
        self.value: float | None = None
        self.prev_close: float | None = None
        self._warm = 0
        self._sum_tr = 0.0

    def update(self, high: float, low: float, close: float) -> float | None:
        if self.prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - self.prev_close), abs(low - self.prev_close))

        self.prev_close = close

        if self._warm < self.period:
            self._sum_tr += tr
            self._warm += 1
            if self._warm == self.period:
                self.value = self._sum_tr / self.period
            return self.value

        # Wilder smoothing
        self.value = (self.value * (self.period - 1) + tr) / self.period  # type: ignore[operator]
        return self.value



class RSI:
    """
    Wilder RSI
    - period: thường dùng 14
    - Trả về None cho đến khi đủ dữ liệu warm-up
    """
    def __init__(self, period: int = 14):
        self.period = period
        self.prev_close: float | None = None
        self.avg_gain: float | None = None
        self.avg_loss: float | None = None
        self._warm = 0
        self._gains: list[float] = []
        self._losses: list[float] = []

    def update(self, close: float) -> float | None:
        if self.prev_close is None:
            self.prev_close = close
            return None

        change = close - self.prev_close
        self.prev_close = close

        gain = max(change, 0.0)
        loss = max(-change, 0.0)

        if self._warm < self.period:
            self._gains.append(gain)
            self._losses.append(loss)
            self._warm += 1
            if self._warm == self.period:
                self.avg_gain = sum(self._gains) / self.period
                self.avg_loss = sum(self._losses) / self.period
            return None

        # Wilder smoothing
        self.avg_gain = (self.avg_gain * (self.period - 1) + gain) / self.period
        self.avg_loss = (self.avg_loss * (self.period - 1) + loss) / self.period

        if self.avg_loss == 0:
            return 100.0

        rs = self.avg_gain / self.avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

def wick_ratio(c: dict) -> float:
    """
    Total wick / range
    """
    o = float(c["open"])
    h = float(c["high"])
    l = float(c["low"])
    cl = float(c["close"])

    rng = max(h - l, 1e-12)
    body_top = max(o, cl)
    body_bot = min(o, cl)

    upper = max(0.0, h - body_top)
    lower = max(0.0, body_bot - l)
    return (upper + lower) / rng


def momentum(c: dict) -> float:
    """
    |close-open| / open
    """
    o = float(c["open"])
    cl = float(c["close"])
    if o == 0:
        return 0.0
    return abs(cl - o) / o