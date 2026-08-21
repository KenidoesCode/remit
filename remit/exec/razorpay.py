"""Razorpay adapter. The ONLY file that knows Razorpay exists.

Everything above this layer talks to `PaymentGateway`. Swapping the rail
means writing one more class here, not touching the domain.

Test mode only, enforced. A live key in a repo that deliberately injects
retries and duplicate webhooks is how a student ends up explaining real
debits to a stranger.

Capability notes, stated rather than faked:
  - Orders / Payments / Refunds / Payment Links are fully exercisable on
    `rzp_test_` keys with no KYC.
  - Razorpay documents an idempotency key header for RazorpayX payouts only.
    Core Orders/Payments dedupe via the Order `receipt` field (max 40 chars),
    which is what we use.
  - UPI Reserve Pay / SBMD is NOT available in test mode. We do not simulate
    a mandate and call it real; the authority model here is REMIT's own
    intent envelope.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Protocol

import httpx

BASE = "https://api.razorpay.com/v1"


def verify_payment_signature(*, order_id: str, payment_id: str,
                             signature: str, key_secret: str) -> bool:
    """Razorpay Checkout returns razorpay_signature = HMAC-SHA256 of
    "<order_id>|<payment_id>" keyed with the API secret.

    This is the only thing standing between "the browser said it paid" and the
    payment store believing it. Verified server-side, constant-time, and a
    failure may never advance state -- exactly the rule the webhook path uses.
    """
    if not (order_id and payment_id and signature and key_secret):
        return False
    expected = hmac.new(key_secret.encode(),
                        f"{order_id}|{payment_id}".encode(),
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class PaymentGateway(Protocol):
    def create_order(self, *, amount_paise: int, receipt: str, notes: dict) -> dict: ...
    def fetch_order(self, order_id: str) -> dict: ...
    def lookup_by_receipt(self, receipt: str) -> dict | None: ...


class RazorpayTestClient:
    def __init__(self, key_id: str | None = None, key_secret: str | None = None,
                 timeout: float = 10.0):
        self.key_id = key_id or os.environ.get("RAZORPAY_KEY_ID", "")
        self.key_secret = key_secret or os.environ.get("RAZORPAY_KEY_SECRET", "")
        if not self.key_id.startswith("rzp_test_"):
            raise ValueError(
                "REMIT refuses non-test keys. Expected a key starting 'rzp_test_'; "
                "got %r" % self.key_id[:12])
        self._c = httpx.Client(auth=(self.key_id, self.key_secret),
                               timeout=timeout, base_url=BASE)

    def create_order(self, *, amount_paise: int, receipt: str, notes: dict) -> dict:
        r = self._c.post("/orders", json={"amount": amount_paise, "currency": "INR",
                                          "receipt": receipt, "notes": notes})
        r.raise_for_status()
        return r.json()

    def fetch_order(self, order_id: str) -> dict:
        r = self._c.get(f"/orders/{order_id}")
        r.raise_for_status()
        return r.json()

    def lookup_by_receipt(self, receipt: str) -> dict | None:
        """Reconciliation path: ask the gateway what actually happened.
        Razorpay has no receipt-filter endpoint, so we page recent orders."""
        r = self._c.get("/orders", params={"count": 100})
        r.raise_for_status()
        for o in r.json().get("items", []):
            if o.get("receipt") == receipt:
                return o
        return None


class FakeGateway:
    """Offline double and fault injector. Same interface, no network."""

    def __init__(self, fail_on: set[str] | None = None,
                 timeout_on: set[str] | None = None,
                 latency_ms: float = 0.0):
        self.orders: dict[str, dict] = {}
        self.by_receipt: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_on = fail_on or set()
        self.timeout_on = timeout_on or set()
        self.latency_ms = latency_ms

    def _mk(self, amount_paise: int, receipt: str, notes: dict) -> dict:
        oid = "order_" + receipt[:14]
        self.orders[oid] = {"id": oid, "amount": amount_paise, "receipt": receipt,
                            "status": "created", "notes": notes}
        self.by_receipt[receipt] = oid
        return self.orders[oid]

    def create_order(self, *, amount_paise: int, receipt: str, notes: dict) -> dict:
        self.calls.append(("create_order", receipt))
        if receipt in self.timeout_on:
            self._mk(amount_paise, receipt, notes)   # it DID get created
            raise TimeoutError("network died after the order was created")
        if receipt in self.fail_on:
            raise RuntimeError("gateway rejected the order")
        return self._mk(amount_paise, receipt, notes)

    def fetch_order(self, order_id: str) -> dict:
        self.calls.append(("fetch_order", order_id))
        return self.orders[order_id]

    def lookup_by_receipt(self, receipt: str) -> dict | None:
        self.calls.append(("lookup_by_receipt", receipt))
        oid = self.by_receipt.get(receipt)
        return self.orders.get(oid) if oid else None

    def mark_paid(self, receipt: str) -> None:
        oid = self.by_receipt.get(receipt)
        if oid:
            self.orders[oid]["status"] = "paid"
