"""Live server smoke test: autopilot source, real-time loop, websocket, recording,
and an exact replay of the recording through a fresh engine."""

import json
import time

from fastapi.testclient import TestClient

from residual import server as srv
from residual.replay import replay


def test_live_session_records_and_replays_exactly(tmp_path, monkeypatch):
    monkeypatch.setattr(srv, "SESSIONS", tmp_path)
    rt = srv.Runtime("autopilot")
    client = TestClient(srv.create_app(rt))
    try:
        with client.websocket_connect("/ws") as ws:
            got_state = False
            for _ in range(20):
                msg = json.loads(ws.receive_text())
                if msg["type"] == "state":
                    s = msg["state"]
                    assert s["control"] == ["hand"]
                    assert s["runtime"]["source"] == "autopilot"
                    got_state = True
                    break
            assert got_state
            ws.send_text(json.dumps({"type": "record", "value": True}))
            ws.send_text(json.dumps({"type": "degrade", "channel": "hand", "level": 0.3}))
            time.sleep(15)
            seq_live = [e for e in rt.events if e["kind"] == "select"]
            ws.send_text(json.dumps({"type": "record", "value": False}))
            time.sleep(0.2)
    finally:
        rt.stop()
    files = list(tmp_path.glob("session_*.jsonl"))
    assert len(files) == 1
    events = replay(str(files[0]), verbose=False)
    replayed = [e for e in events if e.kind == "select"]
    assert len(replayed) >= 2  # the simulated person kept selecting
    # every selection made while recording is reproduced, in order, at the same time
    rec_start = json.loads(files[0].read_text().splitlines()[0])["t_start"]
    live = [(round(e["t"], 2), e["data"]["index"]) for e in seq_live if e["t"] >= rec_start]
    rep = [(round(e.t, 2), e.data["index"]) for e in replayed]
    assert rep[: len(live)] == live[: len(rep)]


def test_state_is_valid_json_for_the_browser():
    rt = srv.Runtime("autopilot")
    try:
        time.sleep(0.5)
        text = json.dumps(rt.state(), default=srv._json_default, allow_nan=False)
        assert "Infinity" not in text
    finally:
        rt.stop()


def _wait_step(rt, name, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if rt.cal is None or rt.cal.name == name:
            return
        time.sleep(0.05)
    raise AssertionError(f"never reached {name}")


def _pulse(rt, ch, n, hold=0.6, gap=0.9):
    for _ in range(n):
        rt.keyboard.down[ch] = True
        time.sleep(hold)
        rt.keyboard.down[ch] = False
        time.sleep(gap)


def test_calibration_is_success_driven_and_honest():
    """Keyboard source: hand and blink are done 3 times, eyebrow is never raised and skipped.
    The result must say so, the eyebrow must not be usable, and Select must still work."""
    rt = srv.Runtime("keyboard")
    try:
        _wait_step(rt, "hand")
        _pulse(rt, "hand", 3)
        _wait_step(rt, "brow")
        time.sleep(1.0)
        rt.command({"type": "calibration_skip"})
        _wait_step(rt, "blink")
        _pulse(rt, "blink", 3, hold=0.6)
        end = time.monotonic() + 10
        while rt.cal is not None and time.monotonic() < end:
            time.sleep(0.05)
        assert rt.cal is None
        st = rt.state()
        res = st["runtime"]["calibration_result"]
        assert res["results"]["hand"]["status"] == "VALID", res
        assert res["results"]["blink"]["status"] == "VALID", res
        assert res["results"]["brow"]["status"] == "FAILED" and res["results"]["brow"]["skipped"]
        assert res["all_ok"] is False
        assert rt.engine.eligible["brow"] is False
        roles = st["runtime"]["roles"]
        assert "brow" not in (roles["next"], roles["back"]) and "brow" not in roles["select"]
        assert "brow" not in roles["eligible_aux"]
        # holding H now selects something on the board
        sel_before = sum(e["kind"] == "select" for e in rt.events)
        _pulse(rt, "hand", 6, hold=0.6, gap=1.0)
        assert sum(e["kind"] == "select" for e in rt.events) > sel_before
    finally:
        rt.stop()


def test_retry_one_movement_keeps_the_others():
    rt = srv.Runtime("keyboard")
    try:
        _wait_step(rt, "hand")
        _pulse(rt, "hand", 3)
        _wait_step(rt, "brow")
        rt.command({"type": "calibration_skip"})
        _wait_step(rt, "blink")
        _pulse(rt, "blink", 3)
        while rt.cal is not None:
            time.sleep(0.05)
        rt.command({"type": "calibrate", "channel": "brow"})
        _wait_step(rt, "brow")
        _pulse(rt, "brow", 3)
        end = time.monotonic() + 10
        while rt.cal is not None and time.monotonic() < end:
            time.sleep(0.05)
        res = rt.state()["runtime"]["calibration_result"]
        assert {c: r["status"] for c, r in res["results"].items()} == {"hand": "VALID", "brow": "VALID", "blink": "VALID"}
        assert res["all_ok"] is True
        assert rt.engine.eligible == {"hand": True, "brow": True, "blink": True}
    finally:
        rt.stop()


def test_health_reports_camera_problems():
    """The supervisor must not call the engine healthy just because it answers."""
    rt = srv.Runtime("keyboard")
    try:
        rt.source_name = "webcam"          # pretend: webcam selected but it never opened
        rt.webcam = None
        rt.camera_error = "no device"
        client = TestClient(srv.create_app(rt))
        h = client.get("/api/health").json()
        assert h["ok"] is False and h["why"] == "camera_not_open"
    finally:
        rt.source_name = "keyboard"
        rt.stop()
