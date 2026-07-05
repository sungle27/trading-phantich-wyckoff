from __future__ import annotations

"""
wallet_sync.py
──────────────
Fetch NAV thực tế từ Binance USDT-M Futures wallet và sync vào
sim / pos_mgr / ddm.

Dùng REST API signed request (HMAC-SHA256).
Không cần thư viện binance — chỉ dùng aiohttp + hashlib.
"""

import hashlib
import hmac
import time
from typing import Optional
from urllib.parse import urlencode

import aiohttp

from app.config import CFG


# ─────────────────────────────────────────────
# Low-level: signed GET
# ─────────────────────────────────────────────
def _sign(params: dict, secret: str) -> str:
    query = urlencode(params)
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


async def _get_signed(endpoint: str, params: dict) -> Optional[dict]:
    """
    Gọi Binance Futures REST API với HMAC signature.
    Đọc API key trực tiếp từ os.getenv để tránh bị cache bởi frozen CFG dataclass.
    Trả về JSON dict hoặc None nếu lỗi.
    """
    import os
    api_key    = os.getenv("BINANCE_API_KEY",    "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()

    # DEBUG — log trạng thái key (không in giá trị thật)
    print(f"[wallet_sync:debug] BINANCE_API_KEY    → {'SET' if api_key    else '❌ MISSING'} (len={len(api_key)})")
    print(f"[wallet_sync:debug] BINANCE_API_SECRET → {'SET' if api_secret else '❌ MISSING'} (len={len(api_secret)})")

    if not api_key or not api_secret:
        print("[wallet_sync] ❌ Key thiếu — kiểm tra fly.io secrets hoặc _env")
        return None

    base_url = "https://fapi.binance.com"

    params["timestamp"] = int(time.time() * 1000)
    params["signature"] = _sign(params, api_secret)

    url = f"{base_url}{endpoint}?{urlencode(params)}"
    print(f"[wallet_sync:debug] → GET {base_url}{endpoint}")

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                url,
                headers={"X-MBX-APIKEY": api_key},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                status = r.status
                text   = await r.text()
                print(f"[wallet_sync:debug] ← HTTP {status} | {text[:400]}")
                if status != 200:
                    return None
                import json as _json
                return _json.loads(text)
    except Exception as e:
        print(f"[wallet_sync:debug] ❌ exception: {type(e).__name__}: {e}")
        return None


# ─────────────────────────────────────────────
# Public: fetch USDT-M wallet balance
# ─────────────────────────────────────────────
async def fetch_futures_usdt_balance() -> Optional[float]:
    """
    Trả về tổng walletBalance USDT từ Futures account.
    Dùng /fapi/v2/account để lấy assets[].walletBalance.

    walletBalance = số dư ví (bao gồm unrealized PnL chưa được settle)
    Nếu muốn availableBalance (tiền rảnh thật sự), đổi field bên dưới.
    """
    data = await _get_signed("/fapi/v2/account", {})
    if data is None:
        return None

    try:
        assets = data.get("assets", [])
        for asset in assets:
            if asset.get("asset") == "USDT":
                # walletBalance: tổng ví (kể cả margin đang dùng)
                # availableBalance: số còn rảnh
                # → dùng walletBalance để tính drawdown chính xác hơn
                return float(asset["walletBalance"])
    except Exception as e:
        print(f"[wallet_sync] parse error: {e}")

    return None


# ─────────────────────────────────────────────
# Public: sync NAV vào sim / pos_mgr / ddm
# ─────────────────────────────────────────────
async def sync_nav(sim, pos_mgr, ddm) -> Optional[float]:
    """
    Fetch balance từ Binance, cập nhật:
      - sim.nav
      - pos_mgr.nav_usd
      - ddm (qua ddm.update — tự track peak nếu nav tăng)

    Peak chỉ được reset thủ công (ddm.reset_peak) hoặc lúc startup.
    Trả về NAV mới nếu thành công, None nếu fetch lỗi.
    """
    balance = await fetch_futures_usdt_balance()

    if balance is None:
        print("[wallet_sync] fetch thất bại, giữ NAV cũ")
        return None

    if balance <= 0:
        print(f"[wallet_sync] balance={balance} <= 0, bỏ qua")
        return None

    old_nav = sim.nav
    sim.nav = balance
    pos_mgr.update_nav(balance)
    ddm.update(balance)   # DrawdownManager tự nâng peak nếu balance > peak hiện tại

    print(f"[wallet_sync] NAV synced: {old_nav:.2f} → {balance:.2f} USDT | peak={ddm.peak_nav:.2f}")
    return balance