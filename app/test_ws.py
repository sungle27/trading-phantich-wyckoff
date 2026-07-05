"""
Test WebSocket Binance Futures - stream.binance.com:9443
Dùng shared ClientSession như code test đã thành công
Chạy: py test_ws.py
"""
import asyncio
import aiohttp
import json

async def main():
    url = "wss://stream.binance.com:9443/stream?streams=btcusdt@aggTrade/ethusdt@aggTrade"
    print(f"Connecting: {url}")

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url, heartbeat=30) as ws:
            print("Connected ✅ — waiting for data...")
            count = 0
            async for msg in ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data).get("data", {})
                sym   = data.get("s")
                price = data.get("p")
                qty   = data.get("q")
                if not sym:
                    continue
                print(f"{sym} price={price} qty={qty}")
                count += 1
                if count >= 5:
                    print("✅ Success!")
                    break

asyncio.run(main())