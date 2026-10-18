"""Ordering food with two facial movements, without handing over financial control.

    person picks "Order my usual"  ->  Relay discovers a food agent by capability (allowlisted merchants only)
    -> asks it for the menu (its reply is DATA: validated, re-priced locally, never followed as instructions)
    -> builds the order from the person's saved usual  ->  the person sees merchant, items, total, delivery
    and picks Confirm or Cancel  ->  spending policy: per-order cap, daily cap, merchant allowlist
    -> over a cap: the approved caregiver gets "Dev wants to order X for $Y. Reply YES 482913 to approve"
       through Photon; only that reply, for that order, within 30 minutes, approves it
    -> SANDBOX payment, idempotent on the order id (a repeated confirm or a retry can't pay twice)
    -> every step goes to an audit trail, and the outcome is spoken

Nothing here moves real money. The payment agent is sandbox only, and says so in every receipt.
Who may do what:
  * Residual's own screen (the person): order and confirm
  * ASI:One: may PREPARE an order (it then waits for the person's confirmation on Residual's screen);
    it can confirm only if its sender address is listed in the care profile AND the profile allows it
  * caregiver: approves over-cap orders by replying; raises caps only with the caregiver token
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from security import AttemptLock, new_code

# ---------------------------------------------------------------- external agent replies are data
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def clean_text(s: Any, n: int = 80) -> str:
    """A name from another agent: plain text, short, no control characters. Never an instruction."""
    return _CTRL.sub(" ", str(s or "")).strip()[:n]


class MenuItem(BaseModel):
    id: str
    name: str
    price_cents: int = Field(ge=1, le=20000)        # nothing on a menu costs more than $200
    tags: list[str] = Field(default_factory=list)

    @field_validator("id", "name", mode="before")
    @classmethod
    def _clean(cls, v):
        return clean_text(v, 80)

    @field_validator("tags", mode="before")
    @classmethod
    def _tags(cls, v):
        return [clean_text(t, 24).lower() for t in (v or [])][:8]


class Menu(BaseModel):
    merchant_id: str
    merchant_name: str
    delivery_fee_cents: int = Field(ge=0, le=2000)
    eta_min: Optional[int] = Field(default=None, ge=1, le=240)
    items: list[MenuItem]

    @field_validator("merchant_id", "merchant_name", mode="before")
    @classmethod
    def _clean(cls, v):
        return clean_text(v, 60)


def parse_menu(raw: Any) -> Menu:
    """Validate a menu that came from another agent. Unknown fields (including anything that looks like
    an instruction: "note", "system", "assistant", "ignore previous...") are dropped, not read."""
    if not isinstance(raw, dict):
        raise ValueError("menu isn't an object")
    keep = {k: raw[k] for k in ("merchant_id", "merchant_name", "delivery_fee_cents", "eta_min", "items") if k in raw}
    if "eta_min" in keep and not isinstance(keep["eta_min"], int):
        keep.pop("eta_min")
    items = []
    for it in keep.get("items") or []:
        if isinstance(it, dict):
            try:
                items.append(MenuItem(**{k: it[k] for k in ("id", "name", "price_cents", "tags") if k in it}))
            except ValidationError:
                continue                       # a bad item is skipped, not trusted
    keep["items"] = items
    return Menu(**keep)


# ---------------------------------------------------------------- agents (discovered by capability)
@dataclass
class AgentCard:
    name: str
    address: str
    capabilities: list[str]
    merchant_id: Optional[str] = None


class MockRestaurantAgent:
    """A small mock restaurant with a real-looking menu and a structured API (stands in for an Agentverse
    food agent; a real one is reached the same way and its replies go through parse_menu)."""

    def __init__(self, merchant_id: str = "luna-kitchen", name: str = "Luna Kitchen", inject: str | None = None, *,
                 eta_min: int = 35, fee_cents: int = 299, items: list | None = None):
        self.card = AgentCard(name=f"{name} (mock)", address=f"mock:{merchant_id}", capabilities=["food.menu", "food.order"],
                              merchant_id=merchant_id)
        self.name = name
        self.inject = inject                   # tests: a hostile agent that tries to give instructions
        self.eta_min, self.fee_cents = eta_min, fee_cents
        self.items = items
        self.calls = 0

    async def menu(self) -> dict:
        self.calls += 1
        m = {"merchant_id": self.card.merchant_id, "merchant_name": self.name, "delivery_fee_cents": self.fee_cents, "eta_min": self.eta_min,
             "items": [dict(i) for i in self.items] if self.items is not None else [
            {"id": "soup-lemon-rice", "name": "Lemon rice soup", "price_cents": 899, "tags": ["soup", "soft", "warm"]},
            {"id": "mac-cheese", "name": "Baked mac and cheese", "price_cents": 1199, "tags": ["soft"]},
            {"id": "smoothie-berry", "name": "Berry smoothie", "price_cents": 649, "tags": ["drink", "soft"]},
            {"id": "pudding-rice", "name": "Rice pudding", "price_cents": 549, "tags": ["dessert", "soft"]},
            {"id": "salmon-mash", "name": "Salmon with mashed potatoes", "price_cents": 1899, "tags": ["soft"]},
        ]}
        if self.inject:
            m["note"] = self.inject                                                    # ignored by parse_menu
            m["items"].append({"id": "x", "name": self.inject[:200], "price_cents": 50000})   # rejected: over $200
        return m


class Registry:
    """Capability discovery. Every agent found here is untrusted: its replies are data."""

    def __init__(self, agents: list | None = None):
        self.agents = list(agents or [])

    def discover(self, capability: str, allowed_merchants: set[str]) -> list:
        return [a for a in self.agents if capability in a.card.capabilities and a.card.merchant_id in allowed_merchants]


class SandboxPayments:
    """Test-mode payments only. Idempotent: the same key returns the same receipt and charges once."""
    sandbox = True

    def __init__(self):
        self.receipts: dict[str, dict] = {}
        self.charges = 0

    async def charge(self, merchant_id: str, amount_cents: int, idempotency_key: str) -> dict:
        if idempotency_key in self.receipts:
            return {**self.receipts[idempotency_key], "replayed": True}
        self.charges += 1
        r = {"receipt_id": "sbx_" + uuid.uuid4().hex[:12], "merchant_id": merchant_id, "amount_cents": amount_cents,
             "sandbox": True, "status": "succeeded", "t": time.time()}
        self.receipts[idempotency_key] = r
        return r


# ---------------------------------------------------------------- the care profile (editable, local)
DEFAULT_PROFILE = {
    "person": "Dev",
    "spending": {"per_order_cap_cents": 2500, "daily_cap_cents": 6000, "allowed_merchants": ["luna-kitchen", "green-bowl", "harbor-deli"],
                 "asi_may_confirm": False, "authorized_asi": [], "approver": "Maya", "approvers": ["Maya", "Raj"]},
    "usual": {"merchant_id": "luna-kitchen", "items": [{"id": "soup-lemon-rice", "qty": 1, "kind": "soup"}, {"id": "smoothie-berry", "qty": 1, "kind": "drink"}]},
    "dietary": [],                     # only what a caregiver writes here; never inferred from conversation
    "delivery": {"label": "Home", "address": "123 Example Street"},
    # sha256 of the caregiver token; set with:  python commerce.py set-token
    "caregiver_token_sha256": "",
}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Commerce:
    def __init__(self, profile: dict, registry: Registry, payments: SandboxPayments, send: Callable | None = None,
                 audit_path: Path | None = None, profile_path: Path | None = None, now: Callable[[], float] = time.time,
                 today: Callable[[], str] = lambda: date.today().isoformat()):
        self.profile = profile
        self.registry = registry
        self.payments = payments
        self.send = send                       # async (contact, text, key) -> result  (Photon via the Relay)
        self.audit_path = audit_path
        self.profile_path = profile_path
        self.now, self.today = now, today
        # wrong approval codes: five in ten minutes lock that person out for fifteen (even a right code), so a code can't be guessed
        self.lock = AttemptLock(max_fail=5, window_s=600, lock_s=900, now=lambda: self.now())
        self.orders: dict[str, dict] = {}
        self.audit: list[dict] = []
        self.on_event: Callable[[dict], None] = lambda o: None
        # optional: async (candidates) -> the approver who is best placed right now (the Care Circle). Only ever chosen from the
        # care profile's own list (spending.approvers), never from anything an agent says.
        self.approver_picker: Callable | None = None

    # ------------------------------------------------------------ audit (no message content)
    def _log(self, order: dict, kind: str, **data) -> None:
        e = {"t": self.now(), "order_id": order["id"], "kind": kind, "intent": order.get("intent"), "source": order.get("source"),
             "agent": order.get("agent"), "merchant": order.get("merchant_id"), "amount_cents": order.get("total_cents"),
             "approval_required": order.get("approval_required", False), "status": order.get("status"), **data}
        self.audit.append(e)
        if self.audit_path:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a") as f:
                f.write(json.dumps(e) + "\n")
        try:
            self.on_event(self.summary(order))
        except Exception:
            pass

    # ------------------------------------------------------------ spending
    @property
    def sp(self) -> dict:
        return self.profile["spending"]

    def spent_today(self) -> int:
        d = self.today()
        return sum(o["total_cents"] for o in self.orders.values() if o["status"] == "paid" and o.get("paid_day") == d)

    def over_cap(self, total: int) -> str | None:
        if total > self.sp["per_order_cap_cents"]:
            return f"over the ${self.sp['per_order_cap_cents'] / 100:.2f} per-order limit"
        if self.spent_today() + total > self.sp["daily_cap_cents"]:
            return f"over the ${self.sp['daily_cap_cents'] / 100:.2f} daily limit"
        return None

    def raise_caps(self, token: str, per_order_cap_cents: int | None = None, daily_cap_cents: int | None = None) -> dict:
        want = self.profile.get("caregiver_token_sha256") or ""
        if not want or not hmac.compare_digest(_hash(token or ""), want):
            return {"ok": False, "error": "only a caregiver with the care token can change spending limits"}
        if per_order_cap_cents is not None:
            self.sp["per_order_cap_cents"] = int(per_order_cap_cents)
        if daily_cap_cents is not None:
            self.sp["daily_cap_cents"] = int(daily_cap_cents)
        self._save_profile()
        return {"ok": True, "spending": {k: self.sp[k] for k in ("per_order_cap_cents", "daily_cap_cents")}}

    def update_profile(self, token: str, patch: dict) -> dict:
        want = self.profile.get("caregiver_token_sha256") or ""
        if not want or not hmac.compare_digest(_hash(token or ""), want):
            return {"ok": False, "error": "only a caregiver with the care token can edit the care profile"}
        for k in ("usual", "dietary", "delivery"):
            if k in patch:
                self.profile[k] = patch[k]
        self._save_profile()
        return {"ok": True}

    def _save_profile(self) -> None:
        if self.profile_path:
            self.profile_path.write_text(json.dumps(self.profile, indent=2))

    # ------------------------------------------------------------ ordering
    async def propose(self, which: str = "usual", source: str = "residual_ui", items: list[dict] | None = None,
                      merchant_id: str | None = None, asi_sender: str | None = None) -> dict:
        allowed = set(self.sp["allowed_merchants"])
        usual = self.profile.get("usual") or {}
        merchant_id = merchant_id or usual.get("merchant_id")
        lines = items if items is not None else usual.get("items") or []
        order = {"id": str(uuid.uuid4()), "created": self.now(), "intent": f"order_food:{which}", "source": source,
                 "asi_sender": asi_sender, "merchant_id": merchant_id, "status": "proposed", "lines": [], "total_cents": 0,
                 "approval_required": False}
        self.orders[order["id"]] = order
        if merchant_id not in allowed:
            return self._reject(order, f"{merchant_id or 'that merchant'} isn't on the approved list")
        agents = self.registry.discover("food.menu", allowed)
        agent = next((a for a in agents if a.card.merchant_id == merchant_id), None)
        if agent is None:
            return self._reject(order, "no food agent found for that merchant")
        order["agent"] = agent.card.name
        try:
            menu = parse_menu(await agent.menu())
        except Exception as e:
            return self._reject(order, f"the food agent's menu wasn't usable: {e}")
        if menu.merchant_id != merchant_id:
            return self._reject(order, "the food agent answered for a different merchant")
        by_id = {m.id: m for m in menu.items}
        for ln in lines:
            m = by_id.get(str(ln.get("id")))
            qty = int(ln.get("qty", 1))
            if not m or not (1 <= qty <= 5):
                return self._reject(order, f"'{clean_text(ln.get('id'), 40)}' isn't on the menu any more")
            order["lines"].append({"id": m.id, "name": m.name, "qty": qty, "price_cents": m.price_cents})
        if not order["lines"]:
            return self._reject(order, "no saved usual order yet: a caregiver can add one to the care profile")
        # totals are computed here, from validated prices: never taken from the other agent
        order["subtotal_cents"] = sum(l["qty"] * l["price_cents"] for l in order["lines"])
        order["delivery_fee_cents"] = menu.delivery_fee_cents
        order["total_cents"] = order["subtotal_cents"] + menu.delivery_fee_cents
        order["merchant_name"] = menu.merchant_name
        order["eta_min"] = menu.eta_min
        order["delivery"] = self.profile.get("delivery")
        order["dietary"] = list(self.profile.get("dietary") or [])
        order["status"] = "awaiting_confirmation"
        why = self.over_cap(order["total_cents"])
        if why:
            order["approval_required"], order["approval_reason"] = True, why
        self._log(order, "proposed", over_cap=why)
        return self.summary(order)

    # ------------------------------------------------------------ the quote round
    def _quote(self, menu: Menu, usual_merchant: str | None, brief: list[dict]) -> dict | None:
        """What this merchant would send for the person's usual. The saved merchant gets the exact items; any other is matched
        by kind (soup, drink...) to its cheapest item that breaks no dietary rule a caregiver wrote. No match, no quote."""
        banned = {d.lower() for d in (self.profile.get("dietary") or [])}
        by_id = {m.id: m for m in menu.items}
        lines = []
        for ln in brief:
            qty = max(1, min(5, int(ln.get("qty", 1))))
            if menu.merchant_id == usual_merchant and str(ln.get("id")) in by_id:
                m = by_id[str(ln["id"])]
                if banned & set(m.tags):
                    return None
            else:
                kind = (ln.get("kind") or "").lower()
                cands = sorted([m for m in menu.items if kind and kind in m.tags and not banned & set(m.tags)], key=lambda m: m.price_cents)
                if not cands:
                    return None
                m = cands[0]
            lines.append({"id": m.id, "qty": qty, "price_cents": m.price_cents})
        if not lines:
            return None
        total = sum(l["qty"] * l["price_cents"] for l in lines) + menu.delivery_fee_cents
        return {"merchant_id": menu.merchant_id, "merchant_name": menu.merchant_name, "total_cents": total, "eta_min": menu.eta_min or 45,
                "lines": [{"id": l["id"], "qty": l["qty"]} for l in lines], "exact": menu.merchant_id == usual_merchant}

    async def quote_round(self) -> dict | None:
        """Ask every approved restaurant agent for a quote on the usual and choose. No order is created. -> {quotes, win, why} or None
        when there is only one merchant to ask. 'Best' is what costs the person the fewest bits: the usual if it is in budget and not
        much slower; otherwise the fastest one that stays under the spending limit, so no caregiver is woken to approve it. Every
        menu is untrusted data (parse_menu); prices are ours."""
        allowed = set(self.sp["allowed_merchants"])
        usual = self.profile.get("usual") or {}
        agents = self.registry.discover("food.menu", allowed)
        if len(agents) <= 1 or not usual.get("items"):
            return None
        quotes = []
        for a in agents:
            try:
                q = self._quote(parse_menu(await a.menu()), usual.get("merchant_id"), usual["items"])
            except Exception:
                continue                                                      # a merchant that fails or sends nonsense is skipped
            if q and q["merchant_id"] == a.card.merchant_id:
                quotes.append(q)
        if not quotes:
            return None
        under = [q for q in quotes if not self.over_cap(q["total_cents"])]
        pool = under or quotes
        fastest = min(pool, key=lambda q: (q["eta_min"], q["total_cents"]))
        exact = next((q for q in pool if q["exact"]), None)
        cap = f"${self.sp['per_order_cap_cents'] / 100:.2f}"
        usual_q = next((q for q in quotes if q["exact"]), None)
        if exact and not (exact["eta_min"] > fastest["eta_min"] * 1.5 and exact["eta_min"] - fastest["eta_min"] >= 20):
            win = exact
            why = f"Your usual from {win['merchant_name']}: ${win['total_cents'] / 100:.2f}, about {win['eta_min']} min."
        else:
            win = fastest
            if usual_q and usual_q not in pool:
                why = (f"{win['merchant_name']} keeps this under your {cap} limit (about {win['eta_min']} min), so nobody has to be asked to approve it. "
                       f"Your usual from {usual_q['merchant_name']} would be ${usual_q['total_cents'] / 100:.2f}.")
            else:
                why = f"{win['merchant_name']} is faster: about {win['eta_min']} min" + (f" instead of {exact['eta_min']}" if exact else "") + \
                      f", ${win['total_cents'] / 100:.2f}."
        shown = [{k: q[k] for k in ("merchant_id", "merchant_name", "total_cents", "eta_min", "exact")} | {"chosen": q is win}
                 for q in sorted(quotes, key=lambda q: (q["eta_min"], q["total_cents"]))]
        return {"quotes": shown, "win": win, "why": why}

    async def propose_best(self, which: str = "usual", source: str = "residual_ui", asi_sender: str | None = None) -> dict:
        """The usual, from whichever approved merchant serves the person best (see quote_round); otherwise exactly propose()."""
        r = await self.quote_round() if which == "usual" else None
        if r is None:
            return await self.propose(which, source, asi_sender=asi_sender)
        win = r["win"]
        o = await self.propose(which, source, items=win["lines"], merchant_id=win["merchant_id"], asi_sender=asi_sender)
        order = self.orders.get(o.get("id"))
        if order is not None and o.get("status") == "awaiting_confirmation":
            order["quotes"], order["negotiation"] = r["quotes"], r["why"]
            return self.summary(order)
        return o

    def _reject(self, order: dict, why: str) -> dict:
        order["status"], order["error"] = "rejected", why
        self._log(order, "rejected", error=why)
        return self.summary(order)

    async def confirm(self, order_id: str, source: str, confirmed: bool, asi_sender: str | None = None) -> dict:
        order = self.orders.get(order_id)
        if not order:
            return {"status": "rejected", "error": "no such order"}
        if order["status"] in ("paid", "awaiting_approval"):
            return self.summary(order)                    # a repeated confirm changes nothing
        if order["status"] != "awaiting_confirmation":
            return {**self.summary(order), "error": f"this order is {order['status']}"}
        if source == "asi":
            if not (self.sp.get("asi_may_confirm") and asi_sender and asi_sender in self.sp.get("authorized_asi", [])):
                self._log(order, "confirm_refused", why="unauthorized ASI caller")
                return {**self.summary(order), "error": "ASI:One can prepare this order, but only Dev can confirm it on Residual's screen"}
        elif source != "residual_ui":
            return {**self.summary(order), "error": "not allowed"}
        if not confirmed:
            order["status"] = "cancelled"
            self._log(order, "cancelled", by=source)
            return self.summary(order)
        order["confirmed_by"] = source
        self._log(order, "confirmed", by=source)
        why = self.over_cap(order["total_cents"])
        if why:
            order["approval_required"], order["approval_reason"] = True, why
            return await self._ask_approval(order)
        return await self._pay(order)

    async def _pick_approver(self) -> str | None:
        default = self.sp.get("approver")
        candidates = list(self.sp.get("approvers") or ([default] if default else []))
        if self.approver_picker is None or len(candidates) < 2:
            return default
        try:
            chosen = await self.approver_picker(candidates)
        except Exception:
            return default
        return chosen if chosen in candidates else default

    async def _ask_approval(self, order: dict) -> dict:
        approver = await self._pick_approver()
        order["status"] = "awaiting_approval"
        order["approval_code"] = new_code(6)
        order["approval_from"] = approver
        order["approval_expires"] = self.now() + 30 * 60
        items = ", ".join(f"{l['qty']}× {l['name']}" for l in order["lines"])
        text = (f"{self.profile.get('person', 'Dev')} wants to order {items} from {order['merchant_name']} for "
                f"${order['total_cents'] / 100:.2f} ({order['approval_reason']}). Reply YES {order['approval_code']} to approve "
                f"or NO {order['approval_code']} to decline. (Sandbox test order: no real money.)")
        sent = None
        if self.send:
            try:
                sent = await self.send(approver, text, f"order:{order['id']}:approval")
            except Exception as e:
                sent = {"error": str(e)[:200]}
        self._log(order, "approval_requested", approver=approver, sent=bool(sent and not sent.get("error")),
                  simulated=bool(sent and sent.get("simulated")), error=(sent or {}).get("error"))
        return self.summary(order)

    def _wrong_guess(self, contact: str) -> None:
        if self.lock.fail(contact):
            for o in self.orders.values():
                if o["status"] == "awaiting_approval":
                    self._log(o, "approval_locked", by=contact, why="too many wrong codes")

    def try_reply(self, contact: str, text: str) -> dict | None:
        """A caregiver's reply. Approves only the order whose six-digit code it names, if the sender is that order's approver."""
        m = re.search(r"\b(yes|approve[d]?|ok|no|decline[d]?|deny)\b\D{0,12}(\d{6})\b", text or "", re.I)
        if not m:
            return None
        word, code = m.group(1).lower(), m.group(2)
        if self.lock.locked(contact):
            return {"matched": False, "why": "too many wrong codes: approvals from this person are paused for a while"}
        order = next((o for o in self.orders.values() if o["status"] == "awaiting_approval" and o.get("approval_code") == code), None)
        if order is None:
            if any(o["status"] == "awaiting_approval" for o in self.orders.values()):
                self._wrong_guess(contact)                                  # only a guess against a live order counts
            return {"matched": False, "why": "no order waiting for that code"}
        if contact != order.get("approval_from"):
            self._log(order, "approval_ignored", why=f"reply from {contact}, not the approver")
            self._wrong_guess(contact)
            return {"matched": False, "why": "only the approver can approve"}
        self.lock.ok(contact)
        if self.now() > order["approval_expires"]:
            order["status"] = "expired"
            self._log(order, "approval_expired")
            return {"matched": True, "order_id": order["id"], "status": "expired"}
        if word in ("no", "decline", "declined", "deny"):
            order["status"] = "declined"
            self._log(order, "declined", by=contact)
            return {"matched": True, "order_id": order["id"], "status": "declined"}
        order["approved_by"] = contact
        self._log(order, "approved", by=contact)
        return {"matched": True, "order_id": order["id"], "status": "approved", "pay": True}

    async def pay_approved(self, order_id: str) -> dict:
        order = self.orders[order_id]
        if order["status"] != "awaiting_approval" or not order.get("approved_by"):
            return self.summary(order)
        return await self._pay(order)

    async def _pay(self, order: dict) -> dict:
        try:
            r = await self.payments.charge(order["merchant_id"], order["total_cents"], idempotency_key=f"order:{order['id']}")
        except Exception as e:
            order["status"], order["error"] = "failed", f"payment failed: {e}"
            self._log(order, "payment_failed", error=str(e)[:200])
            return self.summary(order)
        order["status"], order["receipt"], order["paid_day"] = "paid", r, self.today()
        self._log(order, "executed", receipt=r["receipt_id"], sandbox=r.get("sandbox", False))
        return self.summary(order)

    def summary(self, o: dict) -> dict:
        return {k: o.get(k) for k in ("id", "created", "intent", "source", "agent", "merchant_id", "merchant_name", "lines", "subtotal_cents",
                                      "delivery_fee_cents", "total_cents", "delivery", "dietary", "status", "error", "approval_required",
                                      "approval_reason", "approval_from", "approved_by", "receipt", "confirmed_by", "eta_min", "quotes", "negotiation")} | {"sandbox": True}

    def history(self, limit: int = 50) -> list[dict]:
        return [self.summary(o) for o in sorted(self.orders.values(), key=lambda x: -x["created"])[:limit]]


def load_profile(path: Path) -> dict:
    if path.exists():
        p = json.loads(path.read_text())
        return {**DEFAULT_PROFILE, **p, "spending": {**DEFAULT_PROFILE["spending"], **p.get("spending", {})}}
    return json.loads(json.dumps(DEFAULT_PROFILE))


if __name__ == "__main__":  # pragma: no cover
    import getpass
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "set-token":
        here = Path(__file__).parent / "care_profile.json"
        prof = load_profile(here)
        tok = getpass.getpass("Caregiver token (keep it private): ")
        prof["caregiver_token_sha256"] = _hash(tok)
        here.write_text(json.dumps(prof, indent=2))
        print("Saved. Spending limits and the care profile can now be changed with this token.")
