"""Webhook intake.

Assumptions we refuse to make, because they are false in production:
  - that a webhook arrives exactly once   -> dedupe on event id
  - that webhooks arrive in order         -> the state machine rejects
                                             illegal transitions instead of
                                             regressing state
  - that a webhook is authentic           -> HMAC-SHA256 signature, and an
                                             invalid signature may NEVER
                                             change payment state

Razorpay signs webhooks with HMAC-SHA256 over the raw body using the webhook
secret; we verify with a constant-time compare.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from datetime import datetime

from .payments import IllegalTransition, PaymentStore

EVENT_TO_STATE = {
    "payment.authorized": "AUTHORIZED",
    "payment.captured": "SUCCESS",
    "payment.failed": "FAILED",
    "order.paid": "SUCCESS",
}


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(body: bytes, signature: str, secret: str) -> bool:
    return hmac.compare_digest(sign(body, secret), signature or "")


class WebhookProcessor:
    def __init__(self, db: sqlite3.Connection, payments: PaymentStore, secret: str):
        self.db, self.payments, self.secret = db, payments, secret

    def handle(self, *, body: bytes, signature: str, now: datetime) -> dict:
        ok = verify(body, signature, self.secret)
        try:
            evt = json.loads(body.decode())
        except Exception:
            return {"accepted": False, "why": "malformed body"}

        event_id = evt.get("id") or ""
        kind = evt.get("event") or ""
        pid = (evt.get("payload", {}) or {}).get("payment_id")

        existing = self.db.execute(
            "SELECT applied FROM webhook_events WHERE event_id=?", (event_id,)).fetchone()
        if existing:
            return {"accepted": False, "why": "duplicate", "event_id": event_id}

        note, applied = "", 0
        if not ok:
            note = "INVALID SIGNATURE - state not touched"
        else:
            target = EVENT_TO_STATE.get(kind)
            if target is None:
                note = f"unhandled event {kind}"
            elif not pid:
                note = "no payment_id in payload"
            else:
                try:
                    cur = self.payments.transition(pid, target, now, f"webhook {kind}")
                    applied = 1
                    note = f"state now {cur}"
                except IllegalTransition as e:
                    # Out-of-order or late event. Record it, never regress.
                    note = f"out-of-order: {e}"

        self.db.execute(
            "INSERT INTO webhook_events (event_id, payment_id, kind, ts, received_at,"
            " body, signature_ok, applied, note) VALUES (?,?,?,?,?,?,?,?,?)",
            (event_id, pid, kind, evt.get("created_at", now.isoformat()),
             now.isoformat(), body.decode(), int(ok), applied, note))
        return {"accepted": bool(ok), "applied": bool(applied), "note": note,
                "event_id": event_id}
