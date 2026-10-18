"""Small, pure helpers that decide who may reach what. No network, no state beyond what is passed in,
so each rule is tested on its own (tests/test_security.py).

  * the local API (Residual's screen -> Relay) needs a token that only this computer's files hold, and a
    local Host header, so a web page in the person's browser cannot use it
  * the Photon webhook is a separate, tiny server; live mode will not start without its signing secret
  * approval codes come from `secrets`, six digits, and a run of wrong guesses locks the guesser out
  * the agent's identity (AGENT_SEED) must be set on purpose
"""

from __future__ import annotations

import hmac
import os
import secrets
import time
from pathlib import Path
from typing import Callable, Mapping

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MIN_SEED_CHARS = 20


class ConfigError(RuntimeError):
    """A setting that would leave the agent exposed. Raised at startup, with the fix in the message."""


# ------------------------------------------------------------------ approval codes
def new_code(n: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(n))


# ------------------------------------------------------------------ the local API token
def ensure_token(state_dir: str | Path, env: Mapping[str, str] | None = None) -> str:
    """The shared secret between Residual's screen and the Relay. RESIDUAL_LOCAL_TOKEN wins; otherwise a random
    token is made once and kept in state/local_token, readable by the owner only. The app reads the same file."""
    env = os.environ if env is None else env
    given = (env.get("RESIDUAL_LOCAL_TOKEN") or "").strip()
    if given:
        return given
    p = Path(state_dir) / "local_token"
    if p.exists():
        t = p.read_text().strip()
        if len(t) >= 32:
            return t
    p.parent.mkdir(parents=True, exist_ok=True)
    t = secrets.token_urlsafe(32)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t)
    os.chmod(p, 0o600)
    return t


def token_ok(given: str | None, want: str | None) -> bool:
    if not given or not want:
        return False
    return hmac.compare_digest(given.encode(), want.encode())


def host_ok(host_header: str | None, port: int | None = None) -> bool:
    """Only the names for this computer, on our port. A hostile site that resolves to 127.0.0.1 still sends its own
    name in Host (DNS rebinding), and is turned away here."""
    h = (host_header or "").strip().lower()
    if not h:
        return False
    if h.startswith("["):                                    # [::1]:8100
        name, _, rest = h[1:].partition("]")
        p = rest[1:] if rest.startswith(":") else ""
    elif h.count(":") == 1:
        name, _, p = h.partition(":")
    else:
        name, p = h, ""
    return name in LOOPBACK and (p == "" or port is None or p == str(port))


def require_loopback(host: str) -> None:
    if host not in LOOPBACK:
        raise ConfigError(f"HTTP_HOST={host!r}: the local API controls real messages and must stay on 127.0.0.1. "
                          "Only the Photon webhook (its own port) may be reached from outside, through a tunnel.")


# ------------------------------------------------------------------ the webhook
def check_webhook_config(live: bool, secret: str) -> None:
    if live and not (secret or "").strip():
        raise ConfigError("Photon is LIVE but PHOTON_WEBHOOK_SECRET is empty: anyone who finds the webhook URL could post a forged "
                          "reply (for example a fake 'YES' approval). Set the secret from the webhook you register in the Photon "
                          "dashboard, or run with PHOTON_MODE=simulated.")


# ------------------------------------------------------------------ identity
def require_seed(env: Mapping[str, str] | None = None) -> str | None:
    """The seed IS the agent's address. Without one, every restart would be a different agent, and a public listing
    would point at nothing. ALLOW_EPHEMERAL_AGENT=1 is for local experiments only."""
    env = os.environ if env is None else env
    seed = (env.get("AGENT_SEED") or "").strip()
    if len(seed) >= MIN_SEED_CHARS:
        return seed
    if (env.get("ALLOW_EPHEMERAL_AGENT") or "").strip() == "1":
        return None
    raise ConfigError(f"AGENT_SEED is missing or too short (need at least {MIN_SEED_CHARS} characters). Make one and keep it:\n"
                      "  python -c \"import secrets; print(secrets.token_hex(32))\"\n"
                      "Put it in agent/.env as AGENT_SEED=... (never commit it). Set ALLOW_EPHEMERAL_AGENT=1 only for a local test.")


# ------------------------------------------------------------------ limits
class RateLimiter:
    """At most `max_events` per `window_s` for each key."""

    def __init__(self, max_events: int, window_s: float, now: Callable[[], float] = time.time):
        self.max, self.window, self.now = max_events, window_s, now
        self.hits: dict[str, list[float]] = {}

    def _prune(self, key: str) -> list[float]:
        t = self.now()
        h = [x for x in self.hits.get(key, []) if t - x < self.window]
        self.hits[key] = h
        return h

    def allow(self, key: str) -> bool:
        h = self._prune(key)
        if len(h) >= self.max:
            return False
        h.append(self.now())
        return True

    def retry_after(self, key: str) -> float:
        h = self._prune(key)
        return max(0.0, self.window - (self.now() - h[0])) if len(h) >= self.max else 0.0


class AttemptLock:
    """`max_fail` wrong guesses within `window_s` lock the key out for `lock_s`, even for a right guess."""

    def __init__(self, max_fail: int = 5, window_s: float = 600, lock_s: float = 900, now: Callable[[], float] = time.time):
        self.max, self.window, self.lock_s, self.now = max_fail, window_s, lock_s, now
        self.fails: dict[str, list[float]] = {}
        self.until: dict[str, float] = {}

    def locked(self, key: str) -> bool:
        return self.now() < self.until.get(key, 0.0)

    def fail(self, key: str) -> bool:
        """Record a wrong guess. True when this one tripped the lock."""
        t = self.now()
        h = [x for x in self.fails.get(key, []) if t - x < self.window] + [t]
        self.fails[key] = h
        if len(h) >= self.max:
            self.until[key] = t + self.lock_s
            self.fails[key] = []
            return True
        return False

    def ok(self, key: str) -> None:
        self.fails.pop(key, None)
