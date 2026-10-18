"""Who is on the team and how it starts.

Every agent gets its own identity from the one AGENT_SEED you already keep (derive_seed), so there is nothing new to
store and every address is the same on every restart. A member's own seed in the environment (MAYA_AGENT_SEED) wins.
Run together they share one process and talk through uAgents; each also has its own Agentverse mailbox, so each can be
found and talked to through ASI:One on its own."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from policy import Policy

from .circle import CaregiverBrain
from .runtime import Circle, LocalMember


def derive_seed(root: str, role: str, env=None) -> str:
    env = os.environ if env is None else env
    own = (env.get(f"{role.upper().replace(' ', '_')}_AGENT_SEED") or "").strip()
    if len(own) >= 20:
        return own
    return hashlib.sha256(f"{root}|residual-team|{role}".encode()).hexdigest()


def member_config(policy: Policy, raw: dict) -> dict[str, dict]:
    """One entry per caregiver on the care plan: how far they usually are, whether they can lift. Only people the care plan
    lists can be members."""
    given = (raw or {}).get("circle", {}) or {}
    names = [c.name for c in policy.contacts if "caregiver" in c.roles and c.name in policy.caregiver_order]
    return {n: {"base_eta_min": int(given.get(n, {}).get("base_eta_min", 15)), "can_lift": bool(given.get(n, {}).get("can_lift", True))}
            for n in names}


def brains_for(cfg: dict[str, dict], state_dir: Path) -> dict[str, CaregiverBrain]:
    return {n: CaregiverBrain(n, base_eta_min=c["base_eta_min"], can_lift=c["can_lift"], state_path=Path(state_dir) / f"circle_{n.lower()}.json")
            for n, c in cfg.items()}


def embedded_circle(brains: dict[str, CaregiverBrain], timeout_s: float = 3.0) -> Circle:
    """The same logic running inside the Relay, for a Relay started without the team (labelled 'local' on screen)."""
    return Circle({n: LocalMember(b) for n, b in brains.items()}, timeout_s=timeout_s)


def approver_picker(orch):
    """For Commerce: of the care profile's approvers, whoever the circle says is best placed right now. (Commerce only accepts a name
    from its own list, so this can only ever choose among people the care team already named.)"""
    import uuid

    async def pick(candidates: list[str]):
        circle = orch.circle
        if circle is None:
            return None
        plan = await circle.plan({"call_id": "approval-" + uuid.uuid4().hex[:8], "person": orch.policy.person, "need": "general", "urgency": "normal"},
                                 list(candidates), explicit=False)
        return plan["order"][0] if plan["mode"] == "circle" and plan["order"] else None
    return pick


def agent_names(role: str, relay_name: str = "Residual Relay") -> str:
    """The name each agent has on Agentverse (the same wherever it runs)."""
    return {"concierge": "Residual Concierge", "hands": "Residual Hands", "relay": relay_name}.get(role, f"{role} · Residual Care Circle")


def mailbox_roles(policy: Policy, root_seed: str, first_port: int = 8011, relay_name: str = "Residual Relay", include_relay: bool = False,
                  env=None) -> list[dict]:
    """The agents that need an Agentverse mailbox, each on its own port. The Agentverse Inspector can't talk to a Bureau (several agents in
    one process), so each is run alone, once, with the SAME identity it has in the team: the mailbox belongs to the address, not the process."""
    roles = (["relay"] if include_relay else []) + [c.name for c in policy.contacts if "caregiver" in c.roles and c.name in policy.caregiver_order] + ["concierge", "hands"]
    return [{"role": r, "name": agent_names(r, relay_name), "seed": root_seed if r == "relay" else derive_seed(root_seed, r, env), "port": first_port + i}
            for i, r in enumerate(roles)]


def inspector_link(port: int, address: str) -> str:
    return f"https://agentverse.ai/inspect/?uri=http%3A//127.0.0.1%3A{port}&address={address}"
