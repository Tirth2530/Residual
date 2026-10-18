"""Fail loudly if a test leaves a Runtime / webcam thread running (the old suite could hang at exit)."""
import threading

import pytest


@pytest.fixture(autouse=True)
def no_leaked_threads():
    before = {t.ident for t in threading.enumerate()}
    yield
    leaked = [t for t in threading.enumerate()
              if t.ident not in before and t.is_alive() and t.name != "MainThread"
              and not t.name.startswith(("AnyIO", "asyncio", "ThreadPoolExecutor", "Dummy"))]
    for t in leaked:
        t.join(timeout=3.0)
    still = [t.name for t in leaked if t.is_alive()]
    assert not still, f"threads still running after the test: {still}"
