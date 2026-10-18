"""Real uAgents in one Bureau, no network needed: the Relay's agent calls two members' agents and gets their bids back, a
family member's text reaches their own agent, and the outcome is told to the right one. This is the part that has to work
for the Agentverse demo to mean anything. It runs in its own process (see swarm_scenario.py)."""

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def test_real_agents_bid_hear_and_record():
    p = subprocess.run([sys.executable, str(HERE / "swarm_scenario.py"), "18765"], capture_output=True, text=True, timeout=90)
    line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert line, f"no result.\nSTDOUT:\n{p.stdout[-1500:]}\nSTDERR:\n{p.stderr[-1500:]}"
    r = json.loads(line[len("RESULT "):])
    assert "error" not in r, r
    assert r["plan"]["mode"] == "circle" and r["plan"]["order"] == ["Raj", "Maya"]
    assert r["bids"]["Maya"]["can_help"] is False and "meeting" in r["bids"]["Maya"]["reason"]
    assert r["bids"]["Raj"]["eta_min"] == 6
    assert set(r["plan"]["transport"].values()) == {"agentverse"}
    assert r["ack"] and "Drive safe" in r["ack"] and r["maya_mode"] == "driving"
    assert r["raj_load"] > 0
    assert {s["member"] for s in r["snap"]} == {"Maya", "Raj"}


def test_a_request_through_chat_still_gets_the_circles_bids():
    """Asked through the chat protocol (how ASI:One asks), the Relay's handler waits for bids that arrive as messages to the same
    agent. It must not hold up its own message queue: live, every request used to time out and fall back to the fixed order."""
    p = subprocess.run([sys.executable, str(HERE / "swarm_chat_scenario.py"), "18766"], capture_output=True, text=True, timeout=90)
    line = next((l for l in p.stdout.splitlines() if l.startswith("RESULT ")), None)
    assert line, f"no result.\nSTDOUT:\n{p.stdout[-1500:]}\nSTDERR:\n{p.stderr[-1500:]}"
    r = json.loads(line[len("RESULT "):])
    assert "error" not in r, r
    assert r["swarm"] != "fallback" and r["bids"] == ["Maya", "Raj"], r         # both agents answered in time
