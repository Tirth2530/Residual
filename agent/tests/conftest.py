"""Tests never read the developer's real agent/.env and never see real keys from the shell."""
import os
import re

import pytest

os.environ["RESIDUAL_ENV_FILE"] = "/nonexistent/residual-tests.env"          # before any module can load a real .env

_PRIVATE = re.compile(r"^(PHOTON_|AGENT_|ASI|VOICE_|HTTP_|WEBHOOK_|ALLOW_|DEMO_|RESIDUAL_LOCAL|STATE_DIR|CONTACTS_FILE|CARE_PROFILE|ELEVEN|BROWSER_USE)")


@pytest.fixture(autouse=True)
def _no_real_settings(monkeypatch):
    for k in list(os.environ):
        if _PRIVATE.match(k) and k != "RESIDUAL_ENV_FILE":
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RESIDUAL_ENV_FILE", "/nonexistent/residual-tests.env")
