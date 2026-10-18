"""One command for the demo: tunnel, Photon webhook, agent, app. Ctrl+C stops everything it started.

    python agent/tools/start_demo.py                 # the whole thing
    python agent/tools/start_demo.py --no-app        # everything except the Electron app
    python agent/tools/start_demo.py --no-webhook    # keep the webhook as it is (a tunnel you manage yourself)
    python agent/tools/start_demo.py --dry-run       # print the plan and change nothing

Why: a free Cloudflare tunnel gets a new address each time it starts, and Photon sends a caregiver's reply to the address
it was given. So every start needs: a new tunnel, then the webhook pointed at it (which makes a new signing secret that goes
straight into agent/.env), then the agent (which reads that secret). The order below is that order.

Only the webhook this tool made last time is deleted (its id is kept in agent/state/webhook_id). Nothing else in the
Photon project is touched. Logs: agent/state/run/{tunnel,agent,app}.log
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # agent/
ROOT = HERE.parent
STATE = HERE / "state"
RUN = STATE / "run"
WEBHOOK_FILE = STATE / "webhook_id"
LOCAL_PORT, HOOK_PORT = 8100, 8101

TUNNEL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
HOOK_ID_RE = re.compile(r"pho_whd_[A-Za-z0-9]+")


# ---- the small pure parts (tested)
def parse_tunnel_url(text: str) -> str | None:
    m = TUNNEL_RE.search(text or "")
    return m.group(0) if m else None


def parse_webhook_id(text: str) -> str | None:
    m = re.search(r"Webhook created:\s*(" + HOOK_ID_RE.pattern + ")", text or "")
    return m.group(1) if m else None


def recorded_webhook(path: Path = WEBHOOK_FILE) -> str | None:
    try:
        v = path.read_text().strip()
    except OSError:
        return None
    return v if HOOK_ID_RE.fullmatch(v) else None


def record_webhook(path: Path, hook_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(hook_id + "\n")


def plan(webhook: bool = True, app: bool = True) -> list[tuple[str, str]]:
    steps = [("stop leftovers", "anything still listening on 8100/8101 and an old tunnel"),
             ("start the tunnel", "cloudflared quick tunnel to the webhook port"),
             ("wait for the tunnel to resolve", "its address must exist in DNS before Photon can use it")]
    if webhook:
        steps.append(("register the Photon webhook", "delete the one this tool made last time, create one for the new address"))
    steps.append(("start the agent", "the Relay, Maya's, Raj's and the Concierge (reads the new secret)"))
    steps.append(("check the public path", "the tunnel must reach the agent"))
    if app:
        steps.append(("start the app", "npm start in app/"))
    return steps


# ---- doing it
def say(msg: str) -> None:
    print(msg, flush=True)


def listeners(port: int) -> list[int]:
    out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True).stdout
    return [int(x) for x in out.split()]


def stop_leftovers() -> None:
    for port in (LOCAL_PORT, HOOK_PORT):
        for pid in listeners(port):
            say(f"  stopping the process on port {port} (pid {pid})")
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    subprocess.run(["pkill", "-f", f"cloudflared tunnel --url http://127.0.0.1:{HOOK_PORT}"], capture_output=True)
    time.sleep(1.5)


def spawn(cmd: list[str], log: str, cwd: Path) -> subprocess.Popen:
    RUN.mkdir(parents=True, exist_ok=True)
    f = open(RUN / log, "w")
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}          # so the log shows lines as they happen, not when a buffer fills
    return subprocess.Popen(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, start_new_session=True, env=env)


def wait_for(cond, secs: float, what: str, every: float = 0.5):
    end = time.time() + secs
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(every)
    raise SystemExit(f"Timed out waiting for {what}. Look at agent/state/run/ for the logs.")


IPV4 = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def first_ip(dig_output: str) -> str | None:
    for line in (dig_output or "").splitlines():
        if IPV4.match(line.strip()):
            return line.strip()
    return None


def public_ip(host: str) -> str | None:
    """The address Photon will see, from a public resolver. (This Mac's resolver can cache "no such name" for a new tunnel.)"""
    for server in ("1.1.1.1", "8.8.8.8"):
        try:
            out = subprocess.run(["dig", "+short", "+time=3", "+tries=1", f"@{server}", host], capture_output=True, text=True, timeout=8).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        ip = first_ip(out)
        if ip:
            return ip
    return None


def resolves(host: str) -> bool:
    return public_ip(host) is not None


def http_status(url: str, method: str = "GET", resolve_via: str | None = None) -> int:
    """Status code of a request; `resolve_via` pins the address (so a stale local DNS answer can't hide a working tunnel)."""
    if resolve_via:
        host = url.split("//", 1)[1].split("/", 1)[0]
        r = subprocess.run(["curl", "-s", "-m", "10", "-o", "/dev/null", "-w", "%{http_code}", "-X", method, "--resolve", f"{host}:443:{resolve_via}", url],
                           capture_output=True, text=True)
        return int(r.stdout.strip() or 0) if r.stdout.strip().isdigit() else 0
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def doctor(*args: str) -> str:
    r = subprocess.run([sys.executable, str(HERE / "tools" / "photon_doctor.py"), *args], capture_output=True, text=True, cwd=ROOT)
    return (r.stdout or "") + (r.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-app", action="store_true")
    ap.add_argument("--no-webhook", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    steps = plan(webhook=not a.no_webhook, app=not a.no_app)
    if a.dry_run:
        for i, (s, why) in enumerate(steps, 1):
            say(f"{i}. {s}: {why}")
        return 0
    if not shutil.which("cloudflared"):
        raise SystemExit("cloudflared isn't installed (brew install cloudflared).")
    if not (ROOT / ".venv").exists():
        say("note: no .venv found next to agent/; using " + sys.executable)
    kids: list[subprocess.Popen] = []

    def stop_all(*_):
        say("\nStopping everything this started…")
        for p in kids:
            try:
                os.killpg(p.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        sys.exit(0)
    signal.signal(signal.SIGINT, stop_all)
    signal.signal(signal.SIGTERM, stop_all)

    say("1/7 stopping leftovers"); stop_leftovers()
    say("2/7 starting the tunnel")
    tunnel = spawn(["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{HOOK_PORT}"], "tunnel.log", ROOT); kids.append(tunnel)
    url = wait_for(lambda: parse_tunnel_url((RUN / "tunnel.log").read_text()), 45, "the tunnel's address")
    host = url.split("//", 1)[1]
    say(f"      {url}")
    say("3/7 waiting for the tunnel's address to appear in public DNS")
    wait_for(lambda: resolves(host), 120, "the tunnel's address to resolve", every=1.5)
    if not a.no_webhook:
        say("4/7 pointing Photon's webhook at it")
        old = recorded_webhook()
        if old:
            say(f"      replacing the one this tool made last time ({old})")
            doctor("--delete-webhook", old)
        out = doctor("--create-webhook", url)
        new = parse_webhook_id(out)
        if not new:
            raise SystemExit("Couldn't create the Photon webhook:\n" + re.sub(r"whsec_[A-Za-z0-9]+", "whsec_[hidden]", out)[-600:])
        record_webhook(WEBHOOK_FILE, new)
        say(f"      created {new} (the new signing secret is in agent/.env; it isn't shown)")
    say("5/7 starting the agent")
    agent = spawn([sys.executable, str(HERE / "residual_agent.py")], "agent.log", ROOT); kids.append(agent)
    wait_for(lambda: http_status(f"http://127.0.0.1:{LOCAL_PORT}/health") == 200, 90, "the agent to answer")
    wait_for(lambda: (RUN / "agent.log").read_text().count("registration status updated to active") >= 4, 90, "the four agents to register")
    say("      the agent is up and its four agents are active")
    say("6/7 checking the public path")
    ip = public_ip(host)
    wait_for(lambda: http_status(url + "/photon/webhook", resolve_via=ip) == 405, 90, "the tunnel to reach the agent")   # a GET is "method not allowed": it got there
    say("      Photon replies will reach this machine")
    if not a.no_app:
        say("7/7 starting the app")
        if not (ROOT / "app" / "node_modules").exists():
            raise SystemExit("Run `npm install` in app/ first.")
        kids.append(spawn(["npm", "start"], "app.log", ROOT / "app"))
    say("\nAll running. Logs: agent/state/run/{tunnel,agent,app}.log   (tail -f agent/state/run/agent.log to watch replies)")
    say("Press Ctrl+C here to stop everything.")
    while True:
        time.sleep(3)
        dead = [p for p in kids if p.poll() is not None]
        if dead and any(p in (tunnel, agent) for p in dead):
            say("The tunnel or the agent stopped. Look at agent/state/run/ for the logs."); stop_all()


if __name__ == "__main__":
    sys.exit(main())
