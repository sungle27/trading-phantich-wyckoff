import time
import hmac
import hashlib
import os
from urllib.parse import urlencode

import aiohttp

class BinanceAccountClient:

    def __init__(self):

        # đọc trực tiếp từ environment (Fly secrets)
        self.key = os.getenv("BINANCE_API_KEY")
        self.secret = os.getenv("BINANCE_API_SECRET")

        self.base = "https://fapi.binance.com"

        if not self.key or not self.secret:
            raise RuntimeError("BINANCE_API_KEY / BINANCE_API_SECRET not set")

        print("BINANCE CLIENT INIT OK")
        print("API KEY PREFIX:", self.key[:6])

    def _sign(self, params: dict):

        qs = urlencode(params)

        sig = hmac.new(
            self.secret.encode(),
            qs.encode(),
            hashlib.sha256
        ).hexdigest()

        return qs + "&signature=" + sig

    async def get_account(self):

        params = {
            "timestamp": int(time.time() * 1000),
            "recvWindow": 5000
        }

        qs = self._sign(params)

        url = f"{self.base}/fapi/v2/account?{qs}"

        headers = {
            "X-MBX-APIKEY": self.key
        }

        print("REQUEST URL:", url)

        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers) as r:

                print("HTTP STATUS:", r.status)

                text = await r.text()
                print("RAW RESPONSE:", text)

                try:
                    data = await r.json()
                except Exception as e:
                    print("JSON PARSE ERROR:", e)
                    return None

                print("PARSED RESPONSE:", data)

                return data

    async def get_nav(self):

        acc = await self.get_account()

        if not acc:
            print("ACCOUNT RESPONSE EMPTY")
            return 0

        print("ACCOUNT KEYS:", acc.keys())

        if 'totalWalletBalance' not in acc:
            print("totalWalletBalance NOT FOUND")
            print("FULL ACCOUNT RESPONSE:", acc)
            return 0

        nav = float(acc['totalWalletBalance'])

        print("NAV FETCHED:", nav)

        return nav
