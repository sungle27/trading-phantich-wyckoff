from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.config import CFG
from app.telegram import send_telegram

NAV        = 1000.0   # USDT mỗi lệnh
BALANCE    = 10000.0  # Tổng tài sản


@dataclass
class Position:
    symbol:     str
    direction:  str   # LONG / SHORT
    entry:      float
    sl:         float
    tp:         float
    sl_pct:     float
    tp_pct:     float
    signal_type: str
    regime:     str
    open_time:  float = field(default_factory=time.time)

    def pnl(self, close_price: float) -> float:
        if self.direction == "LONG":
            return NAV * (close_price - self.entry) / self.entry
        else:
            return NAV * (self.entry - close_price) / self.entry

    def is_sl_hit(self, high: float, low: float) -> bool:
        if self.direction == "LONG":
            return low <= self.sl
        else:
            return high >= self.sl

    def is_tp_hit(self, high: float, low: float) -> bool:
        if self.direction == "LONG":
            return high >= self.tp
        else:
            return low <= self.tp


class SimulationEngine:
    def __init__(self):
        self.positions:   Dict[str, Position] = {}  # sym → Position
        self.closed:      List[dict] = []
        self.total_pnl:   float = 0.0
        self.wins:        int   = 0
        self.losses:      int   = 0

    def open_position(self, sig: dict, entry: float, sl: float, tp: float,
                      sl_pct: float, tp_pct: float) -> bool:
        sym = sig["symbol"]
        if sym in self.positions:
            return False  # đã có vị thế

        self.positions[sym] = Position(
            symbol      = sym,
            direction   = sig["direction"],
            entry       = entry,
            sl          = sl,
            tp          = tp,
            sl_pct      = sl_pct,
            tp_pct      = tp_pct,
            signal_type = sig.get("signal_type", ""),
            regime      = sig.get("market_regime", ""),
        )
        return True

    async def update(self, sym: str, high: float, low: float, close: float) -> None:
        if sym not in self.positions:
            return

        pos = self.positions[sym]
        result = None
        close_price = None

        if pos.is_tp_hit(high, low):
            result      = "WIN"
            close_price = pos.tp
        elif pos.is_sl_hit(high, low):
            result      = "LOSS"
            close_price = pos.sl

        if result:
            pnl      = pos.pnl(close_price)
            duration = (time.time() - pos.open_time) / 3600

            self.total_pnl += pnl
            if result == "WIN":
                self.wins += 1
            else:
                self.losses += 1

            total = self.wins + self.losses
            winrate = self.wins / total * 100 if total > 0 else 0

            self.closed.append({
                "symbol":    sym,
                "direction": pos.direction,
                "signal":    pos.signal_type,
                "regime":    pos.regime,
                "entry":     pos.entry,
                "close":     close_price,
                "pnl":       pnl,
                "result":    result,
                "duration":  duration,
            })

            del self.positions[sym]

            balance = BALANCE + self.total_pnl
            emoji = "✅" if result == "WIN" else "❌"
            await send_telegram(
                f"{emoji} {result} [{pos.signal_type}] {pos.direction} {sym}\n"
                f"Entry: {pos.entry:.6f} → Close: {close_price:.6f}\n"
                f"PnL: {'+' if pnl > 0 else ''}{pnl:.2f} USDT\n"
                f"Duration: {duration:.1f}h | Regime: {pos.regime}\n"
                f"W/L: {self.wins}/{self.losses} | WR: {winrate:.1f}%\n"
                f"Balance: {balance:.2f}$ | PnL: {self.total_pnl:+.2f}$"
            )

    def summary(self) -> str:
        total    = self.wins + self.losses
        winrate  = self.wins / total * 100 if total > 0 else 0
        open_pos = len(self.positions)
        balance  = BALANCE + self.total_pnl
        return (
            f"📊 SIM SUMMARY\n"
            f"Balance: {balance:.2f}$ / {BALANCE:.0f}$\n"
            f"Open: {open_pos} | Closed: {total}\n"
            f"W/L: {self.wins}/{self.losses} | WR: {winrate:.1f}%\n"
            f"Total PnL: {self.total_pnl:+.2f} USDT"
        )