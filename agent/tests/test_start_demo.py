"""The one-command demo start: tunnel, webhook, agent, app. The parts that can be wrong without anyone noticing are tested:
reading the tunnel address, reading the webhook id, and never deleting a webhook this tool didn't create."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from tools import start_demo as sd  # noqa: E402

LOG = """2026-10-18T14:44:49Z INF Requesting new quick Tunnel on trycloudflare.com...
2026-10-18T14:44:52Z INF |  https://midi-fairy-desktop-attempted.trycloudflare.com                                    |
2026-10-18T14:44:52Z INF Version 2026.10.1"""


def test_the_tunnel_address_is_read_from_cloudflared_output():
    assert sd.parse_tunnel_url(LOG) == "https://midi-fairy-desktop-attempted.trycloudflare.com"
    assert sd.parse_tunnel_url("Requesting new quick Tunnel on trycloudflare.com...") is None     # the bare domain isn't an address
    assert sd.parse_tunnel_url("") is None


def test_the_webhook_id_is_read_from_the_doctors_answer():
    out = "Webhook created: pho_whd_01m57qm9a1ecs875hbz7n118f6  ->  https://x.trycloudflare.com/photon/webhook  (api 2026-07-01)\nThe signing secret was saved"
    assert sd.parse_webhook_id(out) == "pho_whd_01m57qm9a1ecs875hbz7n118f6"
    assert sd.parse_webhook_id("something else") is None


def test_only_the_webhook_this_tool_made_is_replaced(tmp_path):
    f = tmp_path / "webhook_id"
    assert sd.recorded_webhook(f) is None                    # nothing recorded: delete nothing (it could be a teammate's)
    sd.record_webhook(f, "pho_whd_abc123")
    assert sd.recorded_webhook(f) == "pho_whd_abc123"
    f.write_text("not an id")
    assert sd.recorded_webhook(f) is None                    # anything that isn't a webhook id is ignored


def test_the_plan_lists_every_step_in_the_order_that_keeps_the_secret_fresh():
    steps = [s for s, _ in sd.plan(webhook=True, app=True)]
    assert steps == ["stop leftovers", "start the tunnel", "wait for the tunnel to resolve", "register the Photon webhook",
                     "start the agent", "check the public path", "start the app"]                # the secret is saved before the agent reads it
    assert "register the Photon webhook" not in [s for s, _ in sd.plan(webhook=False, app=True)]
    assert "start the app" not in [s for s, _ in sd.plan(webhook=True, app=False)]


def test_the_public_address_is_read_from_a_public_resolvers_answer():
    # Photon resolves names with public DNS. This Mac's own resolver can cache "doesn't exist" for a new name for minutes.
    assert sd.first_ip("104.16.231.132\n104.16.230.132\n") == "104.16.231.132"
    assert sd.first_ip("") is None
    assert sd.first_ip("tunnel.example.com.\n") is None                 # a CNAME line is not an address
    assert sd.first_ip(";; connection timed out\n") is None
