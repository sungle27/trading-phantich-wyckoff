from __future__ import annotations

import asyncio
import json
import time
from typing import Dict, List, Optional

import aiohttp

from app.config import CFG
from app.telegram import send_telegram
from app.resample import TimeframeResampler
from app.alert_engine import check_signal
from app.symbols import FALLBACK_SYMBOLS
from app.utils import backoff_s
from app.market_regime import MarketRegimeEngine
from app.execution_client import ExecutionClient
from app.simulator import SimulationEngine

# ============================================================
# Globals
# ============================================================
_block_counts: dict = {}
_signal_check_count = 0
_signal_ok_count    = 0
sim = SimulationEngine()

exec_client = ExecutionClient(
    base_url=str(getattr(CFG, "EXECUTION_URL", "")),
    token=str(getattr(CFG, "EXECUTION_TOKEN", "")) or None,
    timeout_sec=8,
)

REST_BASE = "https://fapi.binance.com"


# ============================================================
# FETCH HISTORICAL CANDLES
# ============================================================
async def fetch_klines(sess: aiohttp.ClientSession, symbol: str, interval: str, limit: int) -> List[dict]:
    url    = f"{REST_BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        async with sess.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            return [{
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
            } for k in data]
    except Exception:
        return []


# ============================================================
# SYMBOL STATE
# ============================================================
class SymbolState:
    def __init__(self):
        self.bid         = None
        self.ask         = None
        self.cur_sec     = None
        self.vol_bucket  = 0.0
        self.r15m        = TimeframeResampler(15 * 60)    # M15 signal
        self.r1h         = TimeframeResampler(60 * 60)    # H1 trend (EMA50/200)
        self.r2h         = TimeframeResampler(2 * 60 * 60)  # 2h regime
        self.candles_15m: List[dict]  = []
        self.volumes_15m: List[float] = []
        self.candles_1h:  List[dict]  = []
        self.candles_2h:  List[dict]  = []
        self.regime:      str         = "UNKNOWN"
        self.mre         = MarketRegimeEngine()

    def mid(self) -> Optional[float]:
        if self.bid is None or self.ask is None:
            return None
        return (float(self.bid) + float(self.ask)) / 2.0

    def spread(self) -> float:
        m = self.mid()
        if not m:
            return 0.0
        return (float(self.ask) - float(self.bid)) / float(m)


# ============================================================
# SIGNAL → EMIT
# ============================================================
async def _try_open_position(sym: str, st: SymbolState) -> None:
    global _signal_check_count, _signal_ok_count

    sig = check_signal(
        sym,
        st.candles_15m,
        st.volumes_15m,
        st.spread(),
        mode="main",
        market_regime=st.regime,
        market_panic=False,
        candles_1h=st.candles_1h,
    )
    _signal_check_count += 1
    if not sig:
        return
    _signal_ok_count += 1

    direction    = str(sig["direction"]).upper()
    signal_type  = sig.get("signal_type", "")
    entry        = st.mid() or st.candles_15m[-1]["close"]

    # SL/TP
    if int(getattr(CFG, "USE_FIXED_SLTP", 1)):
        # % cố định
        sl_pct = float(getattr(CFG, "SL_PCT", 0.005))  # 0.5%
        tp_pct = float(getattr(CFG, "TP_PCT", 0.010))  # 1.0%
        if direction == "LONG":
            sl = entry * (1 - sl_pct)
            tp = entry * (1 + tp_pct)
        else:
            sl = entry * (1 + sl_pct)
            tp = entry * (1 - tp_pct)
    else:
        # ATR-based
        retest_level = sig.get("retest_level", entry)
        atr          = sig.get("atr", entry * 0.005)
        tp_rr        = float(getattr(CFG, "TP_RR", 2.0))
        if direction == "LONG":
            sl     = retest_level - atr
            sl_pct = (entry - sl) / entry
            tp     = entry + (entry - sl) * tp_rr
            tp_pct = (tp - entry) / entry
        else:
            sl     = retest_level + atr
            sl_pct = (sl - entry) / entry
            tp     = entry - (sl - entry) * tp_rr
            tp_pct = (entry - tp) / entry

    rr = round(tp_rr, 2)

    if int(getattr(CFG, "PAPER_TRADE", 1)):
        sim.open_position(sig, entry, sl, tp, sl_pct, tp_pct)
    else:
        exec_client.emit({
            "schema":          "trade_signal.v1",
            "idempotency_key": f"{sym}_{int(time.time() * 1000)}",
            "symbol":          sym,
            "direction":       direction,
            "entry":           entry,
            "sl":              sl,
            "tp":              tp,
            "qty":             0.0,
            "risk_usd":        0.0,
            "rr":              rr,
            "mode":            "main",
            "regime":          st.regime,
            "ts":              int(time.time()),
            "ttl_sec":         20,
        })

    await send_telegram(
        f"🟢 {signal_type} {direction} {sym}\n"
        f"Entry: {entry:.6f}\n"
        f"SL: {sl:.6f} | TP: {tp:.6f}\n"
        f"RR: {rr} | RSI: {sig.get('rsi', 0):.1f}\n"
        f"Vol: {sig.get('vol_ratio', 0):.2f}x | Regime: {st.regime}\n"
        f"Struct: {sig.get('struct_low', 0):.6f} - {sig.get('struct_high', 0):.6f}"
    )


# ============================================================
# SUMMARY LOOP
# ============================================================
async def summary_loop(states: Dict[str, SymbolState]) -> None:
    await asyncio.sleep(300)
    tick = 0
    while True:
        tick += 1
        btc = states.get("BTCUSDT")

        if int(getattr(CFG, "DEBUG_ENABLED", 0)):
            print(
                f"[summary] btc_regime={btc.regime if btc else 'N/A'} "
                f"15m={len(btc.candles_15m) if btc else 0} "
                f"1h={len(btc.candles_1h) if btc else 0} "
                f"blocks={dict(_block_counts)}"
            )
        _block_counts.clear()

        if tick % 12 == 0 and btc and btc.candles_15m:
            last_c  = btc.candles_15m[-1]
            btc_mid = btc.mid() or last_c["close"]
            try:
                await asyncio.wait_for(send_telegram(
                    f"📊 BTC HOURLY\n"
                    f"Price: {btc_mid:.2f} | Regime: {btc.regime}\n"
                    f"Candles 15m: {len(btc.candles_15m)} | 1h: {len(btc.candles_1h)}\n"
                    f"Signals OK: {_signal_ok_count}\n"
                    f"{sim.summary()}"
                ), timeout=10)
            except Exception:
                pass

        await asyncio.sleep(300)


# ============================================================
# WS: BOOKTICKER
# ============================================================
async def ws_bookticker(states: Dict[str, SymbolState], url: str) -> None:
    while True:
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(url, heartbeat=30) as ws:
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        data = json.loads(msg.data).get("data", {})
                        sym  = data.get("s")
                        if sym in states:
                            states[sym].bid = float(data["b"])
                            states[sym].ask = float(data["a"])
        except Exception as e:
            if int(getattr(CFG, "DEBUG_ENABLED", 0)):
                print(f"[bookticker] error={e}")
            await asyncio.sleep(5)


# ============================================================
# WS: AGG TRADE
# ============================================================
async def ws_aggtrade_binance(states: Dict[str, SymbolState]) -> None:
    ws_base    = "wss://stream.binance.com:9443/stream"
    syms       = list(states.keys())
    chunk_size = 48
    chunks     = [syms[i:i+chunk_size] for i in range(0, len(syms), chunk_size)]

    try:
        await asyncio.wait_for(send_telegram(
            f"✅ BOT RUNNING [Fake Breakout Filter v1]\n"
            f"Symbols: {len(states)} | M15 signal | H1 trend\n"
            f"Mode: {'PAPER' if int(getattr(CFG, 'PAPER_TRADE', 1)) else 'LIVE'} | "
            f"Debug: {'ON' if int(getattr(CFG, 'DEBUG_ENABLED', 0)) else 'OFF'}\n"
            f"Balance: 10000$ | NAV: 1000$/lệnh\n"
            f"Session filter: {'ON' if int(getattr(CFG, 'SESSION_FILTER', 1)) else 'OFF'}"
        ), timeout=10)
    except Exception as e:
        print(f"[init] Telegram error: {e}")

    async def handle_chunk(chunk: list, idx: int, sess: aiohttp.ClientSession) -> None:
        url = ws_base + "?streams=" + "/".join(f"{s.lower()}@aggTrade" for s in chunk)
        while True:
            try:
                async with sess.ws_connect(url, heartbeat=30) as ws:
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        data  = json.loads(msg.data).get("data", {})
                        sym   = data.get("s")
                        if not sym or sym not in states:
                            continue

                        st    = states[sym]
                        sec   = data["T"] // 1000
                        qty   = float(data["q"])
                        price = float(data["p"])

                        if st.bid is None:
                            st.bid = price * 0.9995
                            st.ask = price * 1.0005
                        if st.cur_sec is None:
                            st.cur_sec = sec

                        while sec > st.cur_sec:
                            mid = st.mid() or price
                            if mid:
                                # ── H1 candle (EMA50/200 trend) ──
                                c1, d1 = st.r1h.update(st.cur_sec, mid, 0.0)
                                if d1 and c1:
                                    st.candles_1h.append({
                                        "open": c1.open, "high": c1.high,
                                        "low": c1.low, "close": c1.close
                                    })
                                    st.candles_1h = st.candles_1h[-300:]

                                # ── 2h candle (regime) ──
                                c2, d2 = st.r2h.update(st.cur_sec, mid, qty)
                                if d2 and c2:
                                    st.candles_2h.append({
                                        "open": c2.open, "high": c2.high,
                                        "low": c2.low, "close": c2.close,
                                        "volume": c2.volume
                                    })
                                    st.candles_2h = st.candles_2h[-200:]

                                    # Update regime mỗi nến 2h
                                    rr = st.mre.update(st.candles_2h)
                                    prev_regime = st.regime
                                    st.regime   = rr.regime

                                    if st.regime != prev_regime and int(getattr(CFG, "REGIME_NOTIFY", 1)):
                                        asyncio.get_event_loop().create_task(send_telegram(
                                            f"🌐 REGIME {sym}: {prev_regime} → {st.regime}\n{rr.reason}"
                                        ))

                                # ── M15 candle (signal) ──
                                c15, d15 = st.r15m.update(st.cur_sec, mid, qty)
                                if d15 and c15:
                                    st.candles_15m.append({
                                        "open": c15.open, "high": c15.high,
                                        "low": c15.low, "close": c15.close,
                                        "volume": c15.volume
                                    })
                                    st.volumes_15m.append(c15.volume)
                                    st.candles_15m = st.candles_15m[-400:]
                                    st.volumes_15m = st.volumes_15m[-400:]

                                    # Update simulator
                                    if int(getattr(CFG, "PAPER_TRADE", 1)):
                                        await sim.update(sym, c15.high, c15.low, c15.close)

                                    # Check signal
                                    await _try_open_position(sym, st)

                            st.vol_bucket = 0.0
                            st.cur_sec += 1

                        st.vol_bucket += qty

            except Exception as e:
                if int(getattr(CFG, "DEBUG_ENABLED", 0)):
                    print(f"[ws_chunk{idx}] error={e}")
                await asyncio.sleep(backoff_s(1))

    async with aiohttp.ClientSession() as sess:
        await asyncio.gather(*[handle_chunk(chunk, i, sess) for i, chunk in enumerate(chunks)])


# ============================================================
# MAIN
# ============================================================
async def main():
    states = {s: SymbolState() for s in FALLBACK_SYMBOLS}
    syms   = list(states.keys())

    # ── Fetch historical candles ──
    print(f"[init] Fetching historical candles for {len(syms)} symbols...")
    async with aiohttp.ClientSession() as sess:
        tasks_1h  = [fetch_klines(sess, s, "1h",  250) for s in syms]  # 250 nến H1 cho EMA200
        tasks_15m = [fetch_klines(sess, s, "15m",  80) for s in syms]

        results_1h  = await asyncio.gather(*tasks_1h,  return_exceptions=True)
        results_2h  = await asyncio.gather(*tasks_2h,  return_exceptions=True)
        results_15m = await asyncio.gather(*tasks_15m, return_exceptions=True)

        regime_counts = {}
        for i, sym in enumerate(syms):
            if isinstance(results_1h[i], list) and results_1h[i]:
                states[sym].candles_1h = results_1h[i]
            if isinstance(results_2h[i], list) and results_2h[i]:
                states[sym].candles_2h = results_2h[i]
                rr = states[sym].mre.update(states[sym].candles_2h)
                states[sym].regime = rr.regime
            if isinstance(results_15m[i], list) and results_15m[i]:
                states[sym].candles_15m = results_15m[i]
                states[sym].volumes_15m = [c["volume"] for c in results_15m[i]]

            regime_counts[states[sym].regime] = regime_counts.get(states[sym].regime, 0) + 1

    print(f"[init] Regime: {regime_counts}")
    print(f"[init] Done. Starting WebSocket...")

    try:
        await asyncio.wait_for(send_telegram(
            f"✅ BOT RUNNING [Fake Breakout Filter v1]\n"
            f"Symbols: {len(states)} | M15 signal | H1 trend\n"
            f"Mode: {'PAPER' if int(getattr(CFG, 'PAPER_TRADE', 1)) else 'LIVE'} | "
            f"Debug: {'ON' if int(getattr(CFG, 'DEBUG_ENABLED', 0)) else 'OFF'}\n"
            f"Balance: 10000$ | NAV: 1000$/lệnh\n"
            f"Regime: {regime_counts}"
        ), timeout=10)
    except Exception as e:
        print(f"[init] Telegram error: {e}")

    _book_base  = "wss://stream.binance.com:9443/stream"
    book_chunks = [syms[i:i+50] for i in range(0, len(syms), 50)]
    url_books   = [
        _book_base + "?streams=" + "/".join(f"{s.lower()}@bookTicker" for s in chunk)
        for chunk in book_chunks
    ]

    await asyncio.gather(
        *[ws_bookticker(states, url) for url in url_books],
        ws_aggtrade_binance(states),
        exec_client.worker(),
        summary_loop(states),
    )


if __name__ == "__main__":
    asyncio.run(main())