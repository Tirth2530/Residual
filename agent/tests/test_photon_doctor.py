"""The doctor's read-only helpers must cope with each shape Photon's list endpoints use."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location("photon_doctor", HERE / "tools" / "photon_doctor.py")
pd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pd)


def test_rows_come_out_of_every_response_shape():
    a = {"assignmentId": "a1", "handle": "+14155550123", "assignedPhoneNumber": "+14155550999"}
    assert pd.rows_of({"assignments": [a], "nextPageToken": ""}) == [a]               # shared line assignments
    assert pd.rows_of({"items": [a]}) == [a]                                           # users, spaces, messages
    assert pd.rows_of({"apiVersions": ["2026-01-01"], "packageVersion": "1"}) == ["2026-01-01"]
    assert pd.rows_of([a]) == [a]
    assert pd.rows_of({"unknownKey": [a]}) == [a]                                      # any list will do
    assert pd.rows_of({"error": "x"}) == [] and pd.rows_of(None) == [] and pd.rows_of("text") == []


def test_field_reads_dicts_only_and_never_raises_on_a_string_row():
    assert pd.field({"a": 1}, "x", "a") == 1
    assert pd.field("just a string", "a") is None                                      # the crash that started this
    assert pd.field({"a": None}, "a") is None


def test_handles_are_masked_phone_and_email():
    assert pd.mask("+14155550123") == "+*******0123"
    assert pd.mask("someone@icloud.com") == "s***@icloud.com"
    assert pd.mask(None) == ""
    assert "4155550123" not in pd.mask("+14155550123")


def test_the_webhook_url_is_https_and_ends_at_the_webhook_path():
    assert pd.webhook_url("https://abc.trycloudflare.com") == "https://abc.trycloudflare.com/photon/webhook"
    assert pd.webhook_url("https://abc.trycloudflare.com/") == "https://abc.trycloudflare.com/photon/webhook"
    assert pd.webhook_url("https://abc.example.com/photon/webhook") == "https://abc.example.com/photon/webhook"
    for bad in ("http://abc.example.com", "abc.example.com", "https://user:pw@abc.example.com", "https://abc.example.com/#x", ""):
        try:
            pd.webhook_url(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should have been refused")


def test_the_destination_subscribes_to_replies_only():
    b = pd.webhook_body("https://abc.example.com/photon/webhook", "2026-07-01")
    assert b["enabledEvents"] == ["message.received"]            # never the events about our own messages
    assert b["url"].startswith("https://") and b["apiVersion"] == "2026-07-01" and b["status"] == "enabled" and b["name"]


def test_the_secret_goes_into_env_without_touching_the_rest(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AGENT_SEED=keep-me\n# a comment\nPHOTON_WEBHOOK_SECRET=\nPHOTON_MODE=simulated\n")
    pd.upsert_env(env, "PHOTON_WEBHOOK_SECRET", "whsec_abc")
    t = env.read_text()
    assert "PHOTON_WEBHOOK_SECRET=whsec_abc" in t and "AGENT_SEED=keep-me" in t and "# a comment" in t and "PHOTON_MODE=simulated" in t
    assert t.count("PHOTON_WEBHOOK_SECRET") == 1
    pd.upsert_env(env, "NEW_KEY", "v")                            # appended when missing
    assert env.read_text().rstrip().endswith("NEW_KEY=v")
    assert env.stat().st_mode & 0o077 == 0                        # owner-only
    fresh = tmp_path / "other.env"
    pd.upsert_env(fresh, "A", "1")
    assert fresh.read_text() == "A=1\n"


def test_a_destination_id_is_read_from_destinationId():
    assert pd.field({"destinationId": "pho_whd_1", "url": "https://x"}, "destinationId", "id") == "pho_whd_1"
    assert pd.field({"id": "legacy"}, "destinationId", "id") == "legacy"
