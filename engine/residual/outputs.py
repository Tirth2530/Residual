"""Layer 6 outputs: speech, caregiver alerts, and keystrokes into other apps.

Configure with environment variables (see .env.example):
    ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID   natural speech (else the browser speaks)
    NTFY_TOPIC                                caregiver alert to a phone via ntfy.sh
    ALERT_WEBHOOK_URL                         or any webhook (JSON POST)
    KEYSTROKES=switch|type|off                drive existing communication software
    SWITCH_KEY=space                          key pressed per detected gesture (switch mode)
"""

from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger("residual.outputs")

ELEVEN_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice}"
DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # ElevenLabs premade voice; override with ELEVENLABS_VOICE_ID


class Speech:
    """Fetches and caches ElevenLabs audio. Returns None when not configured,
    in which case the browser's built-in speech synthesis is used."""

    def __init__(self) -> None:
        self.key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
        self.voice = os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_VOICE).strip()
        self._cache: dict[str, bytes] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.key)

    def audio(self, text: str) -> bytes | None:
        if not self.enabled:
            return None
        if text in self._cache:
            return self._cache[text]
        import httpx

        try:
            r = httpx.post(
                ELEVEN_URL.format(voice=self.voice),
                headers={"xi-api-key": self.key, "accept": "audio/mpeg"},
                json={"text": text, "model_id": "eleven_turbo_v2_5"},
                timeout=10.0,
            )
            r.raise_for_status()
        except Exception as e:  # network errors must never break the demo
            log.warning("ElevenLabs failed (%s); browser speech will be used", e)
            return None
        self._cache[text] = r.content
        return r.content

    def prewarm(self, phrases: list[str]) -> None:
        """Fetch every board phrase up front so playback is instant and survives Wi-Fi loss."""
        if self.enabled:
            threading.Thread(target=lambda: [self.audio(p) for p in phrases], daemon=True).start()


class Alerts:
    def __init__(self) -> None:
        self.topic = os.environ.get("NTFY_TOPIC", "").strip()
        self.webhook = os.environ.get("ALERT_WEBHOOK_URL", "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.topic or self.webhook)

    def send(self, text: str) -> None:
        if not self.enabled:
            log.info("alert (not configured): %s", text)
            return
        threading.Thread(target=self._send, args=(text,), daemon=True).start()

    def _send(self, text: str) -> None:
        import httpx

        try:
            if self.topic:
                httpx.post(f"https://ntfy.sh/{self.topic}", content=f"Residual: {text}".encode(),
                           headers={"Title": "Residual alert", "Priority": "urgent",
                                    "Tags": "rotating_light"}, timeout=8.0)
            if self.webhook:
                httpx.post(self.webhook, json={"source": "residual", "text": text}, timeout=8.0)
        except Exception as e:
            log.warning("alert failed: %s", e)


class Keystrokes:
    """Lets Residual drive other software.

    switch: every detected gesture presses SWITCH_KEY, so Residual acts as an
            adaptive switch for any app with switch scanning.
    type:   types the selected phrase.

    On macOS, grant Accessibility permission to your terminal/Python first,
    or key events are silently dropped.
    """

    def __init__(self) -> None:
        self.mode = os.environ.get("KEYSTROKES", "off").strip().lower()
        self.key_name = os.environ.get("SWITCH_KEY", "space").strip()
        self._kb = None
        if self.mode in ("switch", "type"):
            try:
                from pynput.keyboard import Controller, Key

                self._kb = Controller()
                self._key = getattr(Key, self.key_name, self.key_name)
            except Exception as e:
                log.warning("keystrokes disabled (%s). pip install pynput", e)
                self.mode = "off"

    def on_fire(self) -> None:
        if self.mode == "switch" and self._kb:
            self._kb.press(self._key)
            self._kb.release(self._key)

    def on_select(self, text: str) -> None:
        if self.mode == "type" and self._kb:
            self._kb.type(text)
