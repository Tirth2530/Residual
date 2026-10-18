"""Check a Photon 2.0 setup before the demo.

    python tools/photon_doctor.py               # print the API's paths and inbound event shapes
    python tools/photon_doctor.py --whoami      # READ-ONLY: your shared line and the users in your project (numbers masked)
    python tools/photon_doctor.py --events      # READ-ONLY: the webhook API versions and event types to subscribe to
    python tools/photon_doctor.py --create-webhook https://<your-tunnel>   # register the webhook; the secret goes straight into .env
    python tools/photon_doctor.py --webhooks    # READ-ONLY: list your webhook destinations
    python tools/photon_doctor.py --delete-webhook ID
    python tools/photon_doctor.py --send NAME   # send one real test message to a contact (needs .env)

Photon 2.0 is in beta; field names can move. This prints what the live API says today, so the
inbound parser in photon.py can be checked against it. --whoami and --events only ever GET: they
change nothing in your project, and they never print your API key.
"""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

API = "https://api.photon.codes/v1"


def mask(handle) -> str:
    """+14155550123 -> +*******0123 (enough to recognise your own number, not to share it)."""
    s = str(handle or "")
    if "@" in s:                                              # an iMessage email: keep the first letter and the domain
        local, _, domain = s.partition("@")
        return (local[:1] + "***@" + domain) if local else s
    keep = 4
    return re.sub(r"\d", "*", s[:-keep]) + s[-keep:] if len(s) > keep else s


async def specs():
    import aiohttp
    async with aiohttp.ClientSession() as s:
        async with s.get("https://api.photon.codes/openapi.json") as r:
            spec = await r.json(content_type=None)
        print("== REST paths (users / spaces / messages / webhooks) ==")
        for path, ops in sorted(spec.get("paths", {}).items()):
            if any(k in path for k in ("users", "spaces", "messages", "webhook", "lines")):
                print(f"  {', '.join(m.upper() for m in ops)}  {path}")
        async with s.get("https://api.photon.codes/asyncapi.yaml") as r:
            text = await r.text()
        print("\n== Event names mentioning 'message' (asyncapi) ==")
        for line in text.splitlines():
            if "message" in line.lower() and ("type" in line.lower() or "name" in line.lower() or "const" in line.lower()):
                print("  " + line.strip()[:140])


def webhook_url(u: str) -> str:
    """The URL Photon will call: https only, no credentials or fragment, ending at the Relay's webhook path."""
    from urllib.parse import urlparse
    u = (u or "").strip()
    p = urlparse(u)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise ValueError("the webhook URL must be an https:// address with no username or password (your tunnel's URL)")
    if "#" in u:
        raise ValueError("the webhook URL can't contain a #fragment")
    u = u.rstrip("/")
    return u if u.endswith("/photon/webhook") else u + "/photon/webhook"


def webhook_body(url: str, api_version: str) -> dict:
    """Subscribe to replies ONLY. The other message events describe our own messages (a failed send carries our own text)."""
    return {"name": "Residual Relay replies", "description": "A caregiver's reply to a Residual Relay message", "url": url,
            "apiVersion": api_version, "enabledEvents": ["message.received"], "status": "enabled"}


def upsert_env(path, key: str, value: str) -> None:
    """Set KEY=value in an env file, keeping every other line. Owner-only permissions; the value is never printed."""
    path = Path(path)
    lines = path.read_text().splitlines() if path.exists() else []
    out, done = [], False
    for ln in lines:
        if not done and re.match(rf"\s*{re.escape(key)}\s*=", ln):
            out.append(f"{key}={value}")
            done = True
        else:
            out.append(ln)
    if not done:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n")
    os.chmod(path, 0o600)


def _credentials():
    from residual_agent import _env
    _env()
    pid, key = os.environ.get("PHOTON_PROJECT_ID", "").strip(), os.environ.get("PHOTON_API_KEY", "").strip()
    if not (pid and key):
        print("Set PHOTON_PROJECT_ID and PHOTON_API_KEY in agent/.env first (the key is never printed).")
        return None
    return pid, key


def rows_of(data) -> list:
    """The list inside a Photon list response, whatever its key is (items, assignments, apiVersions...). Only dict rows are
    kept for field access; anything else is returned as is so the caller can still print it."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("items", "assignments", "apiVersions", "eventTypes", "events", "data", "results"):
            if isinstance(data.get(k), list):
                return data[k]
        for v in data.values():
            if isinstance(v, list):
                return v
    return []


def field(row, *names):
    return next((row[n] for n in names if isinstance(row, dict) and row.get(n) is not None), None)


async def _get(session, key, url, params=None):
    async with session.get(url, params=params, headers={"Authorization": f"Bearer {key}"}) as r:
        try:
            data = await r.json(content_type=None)
        except Exception:
            data = {"raw": (await r.text())[:300]}
        return r.status, data


def _problem(status, data) -> str:
    d = data if isinstance(data, dict) else {}
    return f"HTTP {status}: {d.get('title') or d.get('detail') or d.get('message') or json.dumps(d)[:200]}"


async def whoami():
    """Which shared line was assigned to you, and which user in the project is the agent on that line?"""
    creds = _credentials()
    if not creds:
        return
    import aiohttp
    pid, key = creds
    base = f"{API}/projects/{pid}"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        st, a = await _get(s, key, base + "/platforms/imessage/assignments")
        print("== Shared iMessage line assignments ==")
        if st >= 400:
            print("  " + _problem(st, a))
        else:
            rows = rows_of(a)
            for x in rows:
                print(f"  your handle {mask(field(x, 'handle'))}  ->  assigned line {mask(field(x, 'assignedPhoneNumber'))}"
                      f"{'  (RELEASED)' if field(x, 'releasedAt') else ''}   assignment {field(x, 'assignmentId', 'id')}")
            if not rows:
                print("  none found: assign a shared iMessage line to your phone in the Photon dashboard first.")
                print("  (response keys: " + ", ".join(a.keys() if isinstance(a, dict) else [type(a).__name__]) + ")")
        st, u = await _get(s, key, base + "/users", {"platform": "imessage", "pageSize": 100})
        print("\n== iMessage users in your project ==")
        if st >= 400:
            print("  " + _problem(st, u))
            if st == 403:
                print("  Your API key can't list users: that needs the project:read permission. Create a key with project:read, project:write")
                print("  and platforms:read (Photon dashboard > API keys), put it in agent/.env as PHOTON_API_KEY, and revoke the old one.")
            return
        rows = rows_of(u)
        for x in rows:
            det = field(x, "details") or {}
            print(f"  id {field(x, 'id')}   handle {mask(field(x, 'handle'))}   kind={field(x, 'kind')}   line={det.get('type') if isinstance(det, dict) else det}")
        if not rows:
            print("  none yet: text your assigned number 'hi' from your phone, then run this again.")
        print("\nYou do NOT need to set PHOTON_AGENT_USER_ID: the Relay finds each contact's agent from their assigned line.")
        print("Each person must have texted THEIR assigned line once after being assigned (a shared line only replies to someone who wrote first).")


async def events():
    """Which webhook events can a destination subscribe to?"""
    creds = _credentials()
    if not creds:
        return
    import aiohttp
    pid, key = creds
    base = f"{API}/projects/{pid}/webhooks"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        st, v = await _get(s, key, base + "/api-versions")
        print("== Webhook API versions ==")
        print("  " + (_problem(st, v) if st >= 400 else json.dumps(v)[:400]))
        ver = None
        for x in rows_of(v):
            ver = field(x, "version", "apiVersion", "id") if isinstance(x, dict) else x
        st, e = await _get(s, key, base + "/event-types", {"apiVersion": ver} if ver else None)
        print(f"\n== Event types (apiVersion {ver}) ==")
        if st >= 400:
            print("  " + _problem(st, e))
            return
        rows = rows_of(e)
        for x in rows:
            name = field(x, "type", "name", "id") if isinstance(x, dict) else x
            aud = field(x, "audience") if isinstance(x, dict) else ""
            print(f"  {'>>' if 'message' in str(name).lower() else '  '} {name}  {aud or ''}")
        if not rows:
            print("  " + json.dumps(e)[:600])


async def create_webhook(url):
    """Register the webhook and save its signing secret into agent/.env (shown by Photon only once)."""
    creds = _credentials()
    if not creds:
        return
    import uuid
    import aiohttp
    pid, key = creds
    try:
        hook = webhook_url(url)
    except ValueError as e:
        print(e)
        return
    base = f"{API}/projects/{pid}/webhooks"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
        st, v = await _get(s, key, base + "/api-versions")
        ver = "2026-07-01"
        for x in rows_of(v):
            cand = field(x, "apiVersion", "version", "id") if isinstance(x, dict) else x
            if isinstance(cand, str) and re.match(r"\d{4}-\d\d-\d\d$", cand):
                ver = cand
        async with s.post(base + "/destinations", json=webhook_body(hook, ver),
                          headers={"Authorization": f"Bearer {key}", "Idempotency-Key": str(uuid.uuid4())}) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:
                data = {}
            if r.status >= 400:
                print(_problem(r.status, data))
                if r.status == 403:
                    print("Your API key needs the project:write permission.")
                return
    secret = (data or {}).get("signingSecret")
    dest = (data or {}).get("destination") or {}
    if not secret:
        print("Photon created the destination but returned no signing secret. Delete it and try again:", field(dest, "destinationId", "id"))
        return
    env = Path(__file__).resolve().parent.parent / ".env"
    upsert_env(env, "PHOTON_WEBHOOK_SECRET", secret)
    print(f"Webhook created: {field(dest, 'destinationId', 'id')}  ->  {hook}  (api {ver}, events: message.received)")
    print(f"The signing secret was saved to {env} as PHOTON_WEBHOOK_SECRET. It was not printed and Photon won't show it again.")


async def list_webhooks():
    creds = _credentials()
    if not creds:
        return
    import aiohttp
    pid, key = creds
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        st, d = await _get(s, key, f"{API}/projects/{pid}/webhooks/destinations")
        print("  " + _problem(st, d) if st >= 400 else "")
        for x in rows_of(d):
            print(f"  {field(x, 'destinationId', 'id')}  {field(x, 'status')}  {field(x, 'url')}  events={field(x, 'enabledEvents')}")
        if st < 400 and not rows_of(d):
            print("  no webhook destinations")


async def delete_webhook(dest_id):
    creds = _credentials()
    if not creds:
        return
    import uuid
    import aiohttp
    pid, key = creds
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.delete(f"{API}/projects/{pid}/webhooks/destinations/{dest_id}",
                            headers={"Authorization": f"Bearer {key}", "Idempotency-Key": str(uuid.uuid4())}) as r:
            print("deleted" if r.status < 300 else _problem(r.status, {}))


async def send(name):
    from residual_agent import build_photon, _env
    from policy import Policy
    _env()
    here = Path(__file__).resolve().parent.parent
    photon = build_photon(here / "state")
    pol = Policy.load(here / os.environ.get("CONTACTS_FILE", "contacts.json"))
    c = pol.resolve(name)
    res = await photon.send_message(c.name, c.phone, "Residual test message. Reply 'on my way' to test replies.",
                                    f"doctor:{c.name}:{os.getpid()}")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--send")
    ap.add_argument("--whoami", action="store_true")
    ap.add_argument("--events", action="store_true")
    ap.add_argument("--create-webhook", metavar="URL")
    ap.add_argument("--webhooks", action="store_true")
    ap.add_argument("--delete-webhook", metavar="ID")
    a = ap.parse_args()
    asyncio.run(send(a.send) if a.send else whoami() if a.whoami else events() if a.events else create_webhook(a.create_webhook)
                if a.create_webhook else list_webhooks() if a.webhooks else delete_webhook(a.delete_webhook) if a.delete_webhook else specs())
