"""One-time helper: connect each agent in the team to an Agentverse mailbox.

The Agentverse Inspector can't talk to several agents in one process (it says "agent bureaus are not supported"), so this starts each
agent ALONE on its own port, with the same identity it has in the team. For each one: open its link, click Connect, choose Mailbox.
The mailbox belongs to the agent's address, so afterwards the normal team (`python residual_agent.py`) just uses it.

    python connect_mailboxes.py            # Maya, Raj, the Concierge and the Hands (the Relay's mailbox is usually already connected)
    python connect_mailboxes.py --all      # ...and the Relay too
Stop the team first (Ctrl-C): an address must not run twice at once. Press Ctrl-C here when every link says "Connected".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def one(role: str, name: str, seed: str, port: int, mailbox: bool) -> None:  # pragma: no cover - runs an agent
    from uagents import Agent
    agent = Agent(name=name, seed=seed, port=port, mailbox=mailbox, publish_agent_details=False)
    print(f"READY {role} {agent.address} {port}", flush=True)
    agent.run()


def main() -> None:  # pragma: no cover - interactive
    import residual_agent as ra
    from policy import Policy
    from security import require_seed
    from swarm.team import inspector_link, mailbox_roles
    if "--one" in sys.argv:
        a = json.loads(sys.argv[sys.argv.index("--one") + 1])
        return one(a["role"], a["name"], a["seed"], a["port"], a["mailbox"])
    ra._env()
    root = require_seed(os.environ)
    if root is None:
        sys.exit("AGENT_SEED is not set (see .env.example).")
    policy = Policy.load(os.environ.get("CONTACTS_FILE", HERE / "contacts.json"))
    mailbox = "--no-mailbox" not in sys.argv
    roles = mailbox_roles(policy, root, relay_name=os.environ.get("AGENT_NAME", "Residual Relay"), include_relay="--all" in sys.argv)
    procs = []
    try:
        for r in roles:
            procs.append(subprocess.Popen([sys.executable, __file__, "--one", json.dumps({**r, "mailbox": mailbox})], stdout=subprocess.PIPE, text=True,
                                          stderr=subprocess.DEVNULL, bufsize=1))
        print("\nOpen each link, click Connect, then choose Mailbox. Ctrl-C here when all are connected.\n")
        for r, p in zip(roles, procs):
            for line in p.stdout:                                  # the agent prints its address once it exists
                if line.startswith("READY"):
                    _, _, addr, port = line.split()
                    print(f"{r['name']}\\n  {inspector_link(int(port), addr)}\\n".replace("\\n", "\n"), flush=True)
                    break
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":  # pragma: no cover
    main()
