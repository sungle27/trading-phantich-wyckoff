from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import aiohttp

from app.utils import backoff_s
from app.telegram import send_telegram


class ExecutionClient:
    """
    Push events to Execution Service

    Features:
    - async queue (non blocking)
    - retry with backoff
    - extensive debug logging
    - telegram debug alerts
    - never crash trading engine
    """

    def __init__(self, base_url: str, token: Optional[str] = None, timeout_sec: int = 8):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_sec = int(timeout_sec)

        self._q: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=1000)

    # ============================================================
    # ENABLE CHECK
    # ============================================================

    def enabled(self) -> bool:
        return bool(self.base_url)

    # ============================================================
    # EMIT EVENT
    # ============================================================

    def emit(self, payload: Dict[str, Any]) -> None:
        """
        Non-blocking enqueue
        """

        if not self.enabled():
            print("EXECUTION DISABLED (no URL)")
            return

        try:
            self._q.put_nowait(payload)

            print("EXEC EMIT OK:", payload.get("schema"), payload.get("symbol"))

        except asyncio.QueueFull:
            print("EXEC QUEUE FULL — dropping event")

    # ============================================================
    # WORKER LOOP
    # ============================================================

    async def worker(self) -> None:
        """
        background worker sending events
        """

        await send_telegram(
            f"🛰 EXECUTION CLIENT STARTED\n"
            f"url={self.base_url}\n"
            f"timeout={self.timeout_sec}s"
        )

        while True:

            payload = await self._q.get()

            try:

                print("EXEC WORKER SEND:", payload.get("schema"))

                await self._post(payload)

            except Exception as e:

                print("EXEC WORKER ERROR:", e)

                try:
                    await send_telegram(
                        f"⚠ EXECUTION WORKER ERROR\n{str(e)}"
                    )
                except Exception:
                    pass

            finally:
                self._q.task_done()

    # ============================================================
    # POST EVENT
    # ============================================================

    async def _post(self, payload: Dict[str, Any]) -> None:

        url = f"{self.base_url}/events"

        headers = {
            "Content-Type": "application/json"
        }

        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        body = json.dumps(payload, ensure_ascii=False)

        # --- debug payload
        try:
            await send_telegram(
                f"📤 SENDING SIGNAL\n"
                f"url={url}\n"
                f"symbol={payload.get('symbol')}\n"
                f"schema={payload.get('schema')}"
            )
        except Exception:
            pass

        for attempt in range(1, 6):

            try:

                timeout = aiohttp.ClientTimeout(total=self.timeout_sec)

                async with aiohttp.ClientSession(timeout=timeout) as s:

                    async with s.post(url, headers=headers, data=body) as resp:

                        text = await resp.text()

                        print("EXEC HTTP STATUS:", resp.status)

                        if 200 <= resp.status < 300:

                            print("EXEC SUCCESS")

                            try:
                                await send_telegram(
                                    f"✅ SIGNAL Main SENT v1\n"
                                    f"{payload.get('symbol')} {payload.get('direction')}\n"
                                    f"status={resp.status}"
                                )
                            except Exception:
                                pass

                            return

                        if 400 <= resp.status < 500:

                            print("EXEC CLIENT ERROR:", text)

                            try:
                                await send_telegram(
                                    f"❌ EXECUTION CLIENT ERROR\n"
                                    f"status={resp.status}\n"
                                    f"{text}"
                                )
                            except Exception:
                                pass

                            return

                        print("EXEC SERVER ERROR:", text)

            except Exception as e:

                print("EXEC POST ERROR:", e)

                try:
                    await send_telegram(
                        f"⚠ EXECUTION RETRY\n"
                        f"attempt={attempt}\n"
                        f"{str(e)}"
                    )
                except Exception:
                    pass

                await asyncio.sleep(backoff_s(attempt))
