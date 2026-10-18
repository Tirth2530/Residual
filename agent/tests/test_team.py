"""Starting the team: who the members are, where their identities come from, and that a Relay without the team still works."""

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from policy import Contact, Policy  # noqa: E402
from swarm.team import brains_for, derive_seed, embedded_circle, member_config  # noqa: E402


def pol():
    return Policy(person="Dev", contacts=[Contact("Maya", "+1", "daughter", roles=["caregiver", "emergency"]),
                                          Contact("Raj", "+2", "son", roles=["caregiver"]),
                                          Contact("Dr Lee", "+3", "doctor", roles=["clinician"])],
                  emergency_order=["Maya", "Raj"], caregiver_order=["Maya", "Raj"])


def test_each_agent_gets_its_own_stable_identity_from_the_one_seed():
    root = "a" * 40
    a, b = derive_seed(root, "Maya"), derive_seed(root, "Raj")
    assert a != b and a != root and len(a) >= 40
    assert derive_seed(root, "Maya") == a                         # same address on every restart
    assert derive_seed("b" * 40, "Maya") != a                     # a different root, different agents


def test_a_members_own_seed_in_the_environment_wins():
    mine = "my-own-maya-seed-" + "z" * 12
    assert derive_seed("a" * 40, "Maya", env={"MAYA_AGENT_SEED": mine}) == mine


def test_members_are_the_caregivers_on_the_plan(tmp_path):
    cfg = member_config(pol(), {"circle": {"Maya": {"base_eta_min": 9, "can_lift": False}}})
    assert set(cfg) == {"Maya", "Raj"}                            # the doctor isn't a caregiver; nobody outside the plan
    assert cfg["Maya"]["base_eta_min"] == 9 and cfg["Maya"]["can_lift"] is False
    assert cfg["Raj"]["base_eta_min"] == 15 and cfg["Raj"]["can_lift"] is True


def test_a_member_named_in_the_config_but_not_on_the_plan_is_ignored():
    cfg = member_config(pol(), {"circle": {"Mallory": {"base_eta_min": 1}}})
    assert "Mallory" not in cfg


def test_brains_keep_their_state_between_runs(tmp_path):
    cfg = member_config(pol(), {})
    b1 = brains_for(cfg, tmp_path)
    b1["Maya"].hear("in a meeting till 3")
    b2 = brains_for(cfg, tmp_path)
    assert b2["Maya"].presence.mode == "busy"


@pytest.mark.asyncio
async def test_without_the_team_running_the_circle_still_answers(tmp_path):
    c = embedded_circle(brains_for(member_config(pol(), {}), tmp_path))
    plan = await c.plan({"call_id": "x", "need": "thirsty", "urgency": "normal"}, ["Maya", "Raj"], explicit=False)
    assert plan["mode"] == "circle" and set(plan["transport"].values()) == {"local"}


def test_the_team_has_one_agent_per_member_and_the_relay_uses_them(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setenv("RESIDUAL_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("AGENT_SEED", "s" * 40)
    monkeypatch.setenv("RESIDUAL_MAILBOX", "0")
    monkeypatch.setenv("RESIDUAL_PUBLISH_MANIFEST", "0")
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PHOTON_MODE", "simulated")
    monkeypatch.setenv("CONTACTS_FILE", str(HERE / "contacts.example.json"))
    monkeypatch.setenv("CARE_PROFILE", str(HERE / "care_profile.example.json"))
    import residual_agent as ra
    loop = asyncio.new_event_loop()
    relay = ra.build_relay()
    assert [type(m).__name__ for m in relay.orch.circle.members.values()] == ["LocalMember", "LocalMember"]
    ra.build_team(relay, loop)
    assert relay.team is True
    assert [type(m).__name__ for m in relay.orch.circle.members.values()] == ["RemoteMember", "RemoteMember"]
    addrs = list(relay.team_addresses.values())
    assert len(addrs) == 4 and len(set(addrs)) == 4                # the Relay, Maya, Raj and the Concierge: all different
    assert "Residual Hands" not in relay.team_addresses            # browser errands go straight to Browser Use, not through ASI:One
    ra2 = ra.build_relay()
    ra.build_team(ra2, loop)
    assert list(ra2.team_addresses.values()) == addrs              # and the same on the next start
    monkeypatch.setenv("HANDS_ON_ASI", "1")                        # opt back in to the Hands being a findable agent
    ra3 = ra.build_relay()
    ra.build_team(ra3, loop)
    assert len(ra3.team_addresses) == 5 and "Residual Hands" in ra3.team_addresses
    loop.close()


@pytest.mark.asyncio
async def test_the_approver_asked_is_the_one_who_is_free(tmp_path):
    from swarm.team import approver_picker

    class O:
        pass
    o = O()
    o.policy = pol()
    o.circle = embedded_circle(brains_for(member_config(pol(), {}), tmp_path))
    o.circle.members["Maya"].brain.hear("in a meeting till 3")
    o.circle.members["Raj"].brain.hear("home, 5 min away")
    pick = approver_picker(o)
    assert await pick(["Maya", "Raj"]) == "Raj"
    o.circle = None
    assert await pick(["Maya", "Raj"]) is None


def test_the_mailbox_helper_plans_one_agent_per_port_with_the_same_identities(tmp_path):
    from swarm.team import agent_names, inspector_link, mailbox_roles
    root = "r" * 40
    roles = mailbox_roles(pol(), root, first_port=8011, relay_name="Residual Relay", include_relay=False)
    assert [r["role"] for r in roles] == ["Maya", "Raj", "concierge", "hands"]
    assert [r["port"] for r in roles] == [8011, 8012, 8013, 8014]
    assert roles[0]["seed"] == derive_seed(root, "Maya") and roles[2]["seed"] == derive_seed(root, "concierge")
    assert [r["name"] for r in roles] == [agent_names(n) for n in ("Maya", "Raj", "concierge", "hands")]
    assert mailbox_roles(pol(), root, include_relay=True, relay_name="Residual Relay")[0]["role"] == "relay"
    link = inspector_link(8011, "agent1qabc")
    assert link == "https://agentverse.ai/inspect/?uri=http%3A//127.0.0.1%3A8011&address=agent1qabc"
    assert "agents/" not in link
