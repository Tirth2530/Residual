"""The Python Browser Use Cloud client, against a fake of the HTTP API (the same endpoints app/lib/browser-use.js uses)."""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from tools.browser_use import BrowserUseCloud, BrowserUseError  # noqa: E402


class Fake:
    def __init__(self, polls):
        self.calls = []
        self.polls = list(polls)

    async def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "POST" and path == "/sessions":
            return {"id": "sess1", "liveUrl": "https://live.browser-use.com/abc"}
        if method == "POST" and path == "/tasks":
            return {"id": "task1", "sessionId": "sess1"}
        if method == "GET" and path == "/tasks/task1":
            return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
        if method == "GET" and path == "/sessions/sess1":
            return {"id": "sess1", "liveUrl": "https://live.browser-use.com/abc"}
        return {}


async def nap(_s):
    return None


def client(polls, **kw):
    f = Fake(polls)
    return BrowserUseCloud("key", request=f, sleep=nap, **kw), f


@pytest.mark.asyncio
async def test_open_makes_a_session_and_returns_its_live_view():
    c, f = client([])
    s = await c.open("https://pharmacy.example.com/")
    assert s == {"session_id": "sess1", "live_url": "https://live.browser-use.com/abc"}
    assert f.calls[0] == ("POST", "/sessions", {"startUrl": "https://pharmacy.example.com/"})


@pytest.mark.asyncio
async def test_a_task_runs_in_the_session_and_its_structured_output_is_parsed():
    c, f = client([{"status": "started"}, {"status": "finished", "output": json.dumps({"status": "ready_to_commit", "final_action": "Submit refill"})}])
    out = await c.run("sess1", "do it", schema={"type": "object"}, max_steps=12, timeout=30)
    assert out == {"status": "ready_to_commit", "final_action": "Submit refill"}
    m, p, body = next(x for x in f.calls if x[1] == "/tasks")
    assert body["sessionId"] == "sess1" and body["maxSteps"] == 12 and body["task"] == "do it"
    assert isinstance(body["structuredOutput"], str) and json.loads(body["structuredOutput"]) == {"type": "object"}


@pytest.mark.asyncio
async def test_a_failed_task_raises_with_the_reason():
    c, _ = client([{"status": "failed", "output": "site blocked the browser"}])
    with pytest.raises(BrowserUseError) as e:
        await c.run("sess1", "x", timeout=30)
    assert "site blocked" in str(e.value)


@pytest.mark.asyncio
async def test_a_task_that_takes_too_long_is_stopped():
    t = {"now": 0.0}

    async def clocked_sleep(s):
        t["now"] += s
    f = Fake([{"status": "started"}])
    c = BrowserUseCloud("key", request=f, sleep=clocked_sleep, clock=lambda: t["now"], poll_s=5)
    with pytest.raises(BrowserUseError) as e:
        await c.run("sess1", "x", timeout=20)
    assert "longer than 20" in str(e.value)
    assert ("PATCH", "/tasks/task1", {"action": "stop"}) in f.calls


@pytest.mark.asyncio
async def test_output_that_isnt_json_is_an_error_not_a_guess():
    c, _ = client([{"status": "finished", "output": "I clicked the button!"}])
    with pytest.raises(BrowserUseError):
        await c.run("sess1", "x", timeout=30)


@pytest.mark.asyncio
async def test_close_stops_the_session():
    c, f = client([])
    await c.close("sess1")
    assert ("PATCH", "/sessions/sess1", {"action": "stop"}) in f.calls


@pytest.mark.asyncio
async def test_the_key_goes_in_a_header_and_never_in_a_url_or_body():
    seen = {}

    class Resp:
        status = 200

        async def text(self):
            return "{}"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class Sess:
        def request(self, method, url, headers=None, data=None, **kw):
            seen.update(method=method, url=url, headers=headers, data=data)
            return Resp()

        async def close(self):
            pass
    c = BrowserUseCloud("sekret-key", session=Sess())
    await c._req("POST", "/sessions", {"startUrl": "https://x.test"})
    assert seen["headers"]["X-Browser-Use-API-Key"] == "sekret-key"
    assert "sekret-key" not in seen["url"] and "sekret-key" not in (seen["data"] or "")
    assert seen["url"].startswith("https://api.browser-use.com/api/v2/")
