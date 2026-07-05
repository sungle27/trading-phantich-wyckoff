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

# Global simulator
sim = SimulationEngine()


# ============================================================
# Globals
# ============================================================
_block_counts: dict = {}
_signal_check_count = 0
_signal_ok_count    = 0

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
    """Fetch lịch sử candles từ Binance REST API."""
    url = f"{REST_BASE}/fapi/v1/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    try:
        async with sess.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            candles = []
            for k in data:
                candles.append({
                    "open":   float(k[1]),
                    "high":   float(k[2]),
                    "low":    float(k[3]),
                    "close":  float(k[4]),
                    "volume": float(k[5]),
                })
            return candles
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
        self.r1h         = TimeframeResampler(60 * 60)       # 1h candles
        self.r4h         = TimeframeResampler(4 * 60 * 60)   # 4h Trading Range
        self.candles_1h: List[dict]  = []
        self.volumes_1h: List[float] = []
        self.candles_4h: List[dict]  = []
        self.regime:     str         = "UNKNOWN"
        self.panic:      bool        = False
        self.range_high: float       = 0.0
        self.range_low:  float       = 0.0
        self.mre        = MarketRegimeEngine()

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

    # Warmup check
    warmup = int(getattr(CFG, "WARMUP_CANDLES", 6))
    if len(st.candles_4h) < warmup:
        return

    sig = check_signal(
        sym,
        st.candles_1h,
        st.volumes_1h,
        st.spread(),
        mode="main",
        market_regime=st.regime,
        market_panic=st.panic,
        range_high=st.range_high,
        range_low=st.range_low,
    )
    _signal_check_count += 1
    if int(getattr(CFG, "DEBUG_ENABLED", 0)):
        print(f"[check] {sym} regime={market_regime} 1h={len(st.candles_1h)} 4h={len(st.candles_4h)} range={st.range_low:.4f}-{st.range_high:.4f}")
    if not sig:
        return
    _signal_ok_count += 1
    if int(getattr(CFG, "DEBUG_ENABLED", 0)):
        print(f"[signal] {sym} {sig['signal_type']} {sig['direction']} vol_ratio={sig.get('vol_ratio',0):.2f}")

    direction    = str(sig["direction"]).upper()
    signal_type  = sig.get("signal_type", "")
    entry        = st.mid() or st.candles_1h[-1]["close"] if st.candles_1h else 0

    _regime    = st.regime.upper()
    signal_type = sig.get("signal_type", "")

    if signal_type == "UPTHRUST":        # SHORT setup
        sl_pct = float(getattr(CFG, "SL_PCT_UPTHRUST", 0.015))   # 1.5%
        tp_pct = float(getattr(CFG, "TP_PCT_UPTHRUST", 0.030))   # 3.0%
    elif signal_type == "SPRING":        # LONG setup
        sl_pct = float(getattr(CFG, "SL_PCT_SPRING", 0.015))     # 1.5%
        tp_pct = float(getattr(CFG, "TP_PCT_SPRING", 0.030))     # 3.0%
    elif _regime == "MARKDOWN":          # SHORT trong markdown
        sl_pct = float(getattr(CFG, "SL_PCT_MARKDOWN", 0.020))   # 2.0%
        tp_pct = float(getattr(CFG, "TP_PCT_MARKDOWN", 0.040))   # 4.0%
    else:
        sl_pct = float(getattr(CFG, "SL_PCT", 0.015))
        tp_pct = float(getattr(CFG, "TP_PCT", 0.030))

    if direction == "LONG":
        sl = entry * (1 - sl_pct)
        tp = entry * (1 + tp_pct)
    else:
        sl = entry * (1 + sl_pct)
        tp = entry * (1 - tp_pct)

    rr = round(tp_pct / sl_pct, 2)

    if int(getattr(CFG, "PAPER_TRADE", 1)):
        # Paper trade — simulate
        sim.open_position(sig, entry, sl, tp, sl_pct, tp_pct)
    else:
        # Live trade — gửi sang execution service
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
        f"RR: {rr} | Regime: {st.regime}\n"
        f"Range: {st.range_low:.6f} - {st.range_high:.6f}\n"
        f"Vol ratio: {sig.get('vol_ratio', 0):.2f}x"
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
                f"btc_1h={len(btc.candles_1h) if btc else 0} "
                f"btc_4h={len(btc.candles_4h) if btc else 0} "
                f"blocks={dict(_block_counts)}"
            )
        _block_counts.clear()

        if tick % 12 == 0 and btc and btc.candles_1h:
            last_c  = btc.candles_1h[-1]
            btc_mid = btc.mid() or last_c["close"]
            try:
                await asyncio.wait_for(send_telegram(
                    f"📊 BTC HOURLY\n"
                    f"Price: {btc_mid:.2f} | Regime: {btc.regime}\n"
                    f"Candles 1h: {len(btc.candles_1h)} | 4h: {len(btc.candles_4h)}\n"
                    f"Range: {btc.range_low:.2f} - {btc.range_high:.2f}\n"
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
            f"✅ BOT RUNNING [Wyckoff v1]\n"
            f"symbols={len(syms)} | 1h signal | 4h range\n"
            f"Focus: UPTHRUST SHORT\n"
            f"Execution: {getattr(CFG,'EXECUTION_URL','N/A')}"
        ), timeout=10)
    except Exception:
        pass

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
                                # ── 4h candle (Trading Range) ──
                                c4, d4 = st.r4h.update(st.cur_sec, mid, qty)
                                if d4 and c4:
                                    st.candles_4h.append({
                                        "open": c4.open, "high": c4.high,
                                        "low": c4.low, "close": c4.close,
                                        "volume": c4.volume
                                    })
                                    st.candles_4h = st.candles_4h[-200:]

                                # ── 1h candle (Signal) ──
                                c1, d1 = st.r1h.update(st.cur_sec, mid, qty)
                                if d1 and c1:
                                    st.candles_1h.append({
                                        "open": c1.open, "high": c1.high,
                                        "low": c1.low, "close": c1.close,
                                        "volume": c1.volume
                                    })
                                    st.volumes_1h.append(c1.volume)
                                    st.candles_1h = st.candles_1h[-300:]
                                    st.volumes_1h = st.volumes_1h[-300:]

                                    # Update regime mỗi nến 1h
                                    prev_regime = st.regime
                                    rr = st.mre.update(st.candles_1h, st.candles_4h)
                                    st.regime     = rr.regime
                                    st.panic      = rr.panic
                                    st.range_high = rr.range_high
                                    st.range_low  = rr.range_low

                                    if int(getattr(CFG, "DEBUG_ENABLED", 0)):
                                        print(f"[regime] {sym} {prev_regime}->{st.regime} range={st.range_low:.4f}-{st.range_high:.4f} reason={rr.reason}")

                                    # Notify regime change
                                    if st.regime != prev_regime and int(getattr(CFG, "REGIME_NOTIFY", 1)):
                                        asyncio.get_event_loop().create_task(send_telegram(
                                            f"🌐 REGIME {sym}: {prev_regime} → {st.regime}\n"
                                            f"Range: {st.range_low:.6f} - {st.range_high:.6f}\n"
                                            f"{rr.reason}"
                                        ))

                                    # Update simulator positions
                                    if int(getattr(CFG, "PAPER_TRADE", 1)):
                                        await sim.update(sym, c1.high, c1.low, c1.close)

                                    # Check signal mỗi nến 1h
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

    # ── Fetch lịch sử candles khi start ──
    print(f"[init] Fetching historical candles for {len(syms)} symbols...")
    async with aiohttp.ClientSession() as sess:
        tasks_4h = [fetch_klines(sess, s, "4h", 80) for s in syms]
        tasks_1h = [fetch_klines(sess, s, "1h", 80) for s in syms]

        results_4h = await asyncio.gather(*tasks_4h, return_exceptions=True)
        results_1h = await asyncio.gather(*tasks_1h, return_exceptions=True)

        for i, sym in enumerate(syms):
            if isinstance(results_4h[i], list) and results_4h[i]:
                states[sym].candles_4h = results_4h[i]
            if isinstance(results_1h[i], list) and results_1h[i]:
                states[sym].candles_1h = results_1h[i]
                states[sym].volumes_1h = [c["volume"] for c in results_1h[i]]

            # Init regime từ historical data
            if states[sym].candles_4h and states[sym].candles_1h:
                rr = states[sym].mre.update(states[sym].candles_1h, states[sym].candles_4h)
                states[sym].regime     = rr.regime
                states[sym].panic      = rr.panic
                states[sym].range_high = rr.range_high
                states[sym].range_low  = rr.range_low

    # Debug: log kết quả fetch
    if int(getattr(CFG, "DEBUG_ENABLED", 0)):
        for sym in list(states.keys()):  # log tất cả symbols
            st = states[sym]
            if st.regime != "UNKNOWN":  # chỉ in regime khác UNKNOWN
                print(f"[init] {sym} 4h={len(st.candles_4h)} 1h={len(st.candles_1h)} regime={st.regime} range={st.range_low:.4f}-{st.range_high:.4f}")
        # Tổng kết
        regime_counts = {}
        for st in states.values():
            regime_counts[st.regime] = regime_counts.get(st.regime, 0) + 1
        print(f"[init] Regime summary: {regime_counts}")
    print(f"[init] Historical data loaded. Starting WebSocket...")

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