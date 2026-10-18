"""Voice calls, as the step after a message in an emergency.

Photon places calls over SIP through a voice provider (ElevenLabs, Retell, Twilio, LiveKit), configured in
the Photon dashboard rather than through one REST call. So Residual Relay talks to whatever starts that call
through one small webhook, VOICE_CALL_WEBHOOK, which receives {to, name, text, idempotency_key} and
returns JSON with at least {"status": "...", "call_id": "..."}.

  * no webhook configured, live Photon: no calls. Messages only, and the health check says so.
  * simulated Photon: a SimulatedCaller, which places nothing and labels every call SIMULATED.

A call is never a substitute for the reply: only a reply ("on my way") acknowledges.
"""

from __future__ import annotations

import json
import time
import urllib.request


class CallError(Exception):
    pass


class WebhookCaller:
    simulated = False

    def __init__(self, url: str, secret: str | None = None, timeout: float = 15.0):
        self.url, self.secret, self.timeout = url, secret, timeout
        self.placed: dict[str, dict] = {}

    async def place_call(self, contact: str, phone: str, text: str, idempotency_key: str) -> dict:
        if idempotency_key in self.placed:
            return {**self.placed[idempotency_key], "replayed": True}
        import asyncio
        body = json.dumps({"to": phone, "name": contact, "text": text, "idempotency_key": idempotency_key}).encode()
        headers = {"Content-Type": "application/json", "Idempotency-Key": idempotency_key}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"

        def go():
            req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        t0 = time.monotonic()
        try:
            d = await asyncio.get_running_loop().run_in_executor(None, go)
        except Exception as e:
            raise CallError(f"call webhook failed: {e}") from e
        res = {"call_id": d.get("call_id") or d.get("id"), "status": d.get("status", "started"), "contact": contact,
               "ms": int((time.monotonic() - t0) * 1000), "simulated": False}
        self.placed[idempotency_key] = res
        return res

    def health(self) -> dict:
        return {"calls": "webhook"}


class SimulatedCaller:
    simulated = True

    def __init__(self):
        self.placed: list[dict] = []
        self._keys: dict[str, dict] = {}

    async def place_call(self, contact: str, phone: str, text: str, idempotency_key: str) -> dict:
        if idempotency_key in self._keys:
            return {**self._keys[idempotency_key], "replayed": True}
        res = {"call_id": f"sim-call-{len(self.placed) + 1}", "status": "started", "contact": contact, "ms": 0, "simulated": True}
        self.placed.append({"contact": contact, "text": text, "key": idempotency_key})
        self._keys[idempotency_key] = res
        return res

    def health(self) -> dict:
        return {"calls": "simulated"}
