"""Browser Use Cloud (https://docs.browser-use.com), from Python. The same v2 endpoints app/lib/browser-use.js uses:

    POST  /sessions                 -> {id, liveUrl}          a browser that stays open for a whole errand
    POST  /tasks                    -> {id, sessionId}        one instruction, run in that session
    GET   /tasks/{id}               -> {status, output}       created | started | finished | stopped | failed
    PATCH /tasks/{id}  {action:stop}
    PATCH /sessions/{id} {action:stop}

The key comes from BROWSER_USE_API_KEY (agent/.env, git-ignored). It travels only in a header."""

from __future__ import annotations

import asyncio
import json
import time

BASE = "https://api.browser-use.com/api/v2"


class BrowserUseError(RuntimeError):
    pass


class BrowserUseCloud:
    simulated = False

    def __init__(self, api_key: str, *, base: str = BASE, request=None, session=None, sleep=asyncio.sleep, clock=time.monotonic, poll_s: float = 1.5):
        if not api_key:
            raise BrowserUseError("BROWSER_USE_API_KEY is not set")
        self.key, self.base = api_key, base.rstrip("/")
        self._request = request or self._req
        self._session = session
        self.sleep, self.clock, self.poll_s = sleep, clock, poll_s

    async def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        if self._session is None:
            import aiohttp
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        headers = {"X-Browser-Use-API-Key": self.key, "Content-Type": "application/json"}
        async with self._session.request(method, self.base + path, headers=headers, data=json.dumps(body) if body is not None else None) as r:
            text = await r.text()
            try:
                data = json.loads(text) if text else {}
            except ValueError:
                data = {"raw": text[:200]}
            if r.status >= 400:
                detail = data.get("detail") or data.get("message") if isinstance(data, dict) else ""
                raise BrowserUseError(f"Browser Use {method} {path}: {r.status} {str(detail)[:160]}")
            return data

    async def open(self, url: str) -> dict:
        s = await self._request("POST", "/sessions", {"startUrl": url})
        live = s.get("liveUrl")
        if not live:
            try:
                live = (await self._request("GET", f"/sessions/{s['id']}")).get("liveUrl")
            except Exception:
                live = None
        return {"session_id": s["id"], "live_url": live}

    async def run(self, session: str, task: str, *, phase: str = "prepare", action: str | None = None, schema: dict | None = None,
                  max_steps: int = 25, timeout: float = 120) -> dict:
        body = {"task": task, "maxSteps": max_steps, "flashMode": True, "sessionId": session}
        if schema:
            body["structuredOutput"] = json.dumps(schema)
        created = await self._request("POST", "/tasks", body)
        tid = created["id"]
        t0 = self.clock()
        while True:
            if self.clock() - t0 > timeout:
                try:
                    await self._request("PATCH", f"/tasks/{tid}", {"action": "stop"})
                except Exception:
                    pass
                raise BrowserUseError(f"Browser Use took longer than {int(timeout)} s")
            await self.sleep(self.poll_s)
            t = await self._request("GET", f"/tasks/{tid}")
            st = t.get("status")
            if st == "finished":
                out = t.get("output")
                try:
                    return out if isinstance(out, dict) else json.loads(out)
                except (TypeError, ValueError):
                    raise BrowserUseError("Browser Use finished but its answer wasn't in the expected form")
            if st in ("failed", "stopped"):
                raise BrowserUseError(f"Browser Use task {st}" + (f": {str(t.get('output'))[:160]}" if t.get("output") else ""))

    async def close(self, session: str) -> None:
        try:
            await self._request("PATCH", f"/sessions/{session}", {"action": "stop"})
        except Exception:
            pass
