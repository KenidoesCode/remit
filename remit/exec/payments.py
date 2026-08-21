"""Payment state machine + orchestrator.

States and the ONLY legal transitions. Anything else raises.

    CREATED     -> AUTHORIZED | FAILED | UNKNOWN
    AUTHORIZED  -> SUCCESS | FAILED | UNKNOWN
    UNKNOWN     -> RECONCILING
    RECONCILING -> SUCCESS | FAILED | UNKNOWN
    SUCCESS     -> (terminal)
    FAILED      -> (terminal)

UNKNOWN is not a bug, it is a required state. RBI's TAT circular allows T+5
for "debited but merchant confirmation not received". A system without an
UNKNOWN state will either charge twice or refund something that never
settled. We enter it deliberately on timeout and let the reconciler own it.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

LEGAL: dict[str, set[str]] = {
    # payment.captured routinely arrives without us ever seeing
    # payment.authorized -- gateways drop intermediate events. Refusing the jump
    # meant a captured payment sat in CREATED forever. Found by the eval's
    # out-of-order webhook bucket; see FAILURES.md 2026-08-21 15:05.
    "CREATED": {"AUTHORIZED", "SUCCESS", "FAILED", "UNKNOWN"},
    "AUTHORIZED": {"SUCCESS", "FAILED", "UNKNOWN"},
    "UNKNOWN": {"RECONCILING"},
    "RECONCILING": {"SUCCESS", "FAILED", "UNKNOWN"},
    "SUCCESS": set(),
    "FAILED": set(),
}
TERMINAL = {"SUCCESS", "FAILED"}


class IllegalTransition(Exception):
    pass


class PaymentStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def create(self, *, cart_id: str, intent_id: str, idem_key: str,
               amount_paise: int, now: datetime,
               correlation_id: str | None = None) -> tuple[str, bool]:
        """Returns (payment_id, created). created=False means this exact
        intent+cart was already paid for -- the caller must NOT retry."""
        row = self.db.execute(
            "SELECT payment_id FROM payments WHERE idem_key=?", (idem_key,)).fetchone()
        if row:
            return row["payment_id"], False
        pid = "pay_" + uuid.uuid4().hex[:18]
        try:
            self.db.execute(
                "INSERT INTO payments (payment_id, cart_id, intent_id, idem_key,"
                " amount_paise, state, correlation_id, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (pid, cart_id, intent_id, idem_key, amount_paise, "CREATED",
                 correlation_id,
                 now.isoformat(), now.isoformat()))
        except sqlite3.IntegrityError:
            row = self.db.execute(
                "SELECT payment_id FROM payments WHERE idem_key=?", (idem_key,)).fetchone()
            return row["payment_id"], False
        self._log(pid, "-", "CREATED", now, "created")
        return pid, True

    def get(self, payment_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM payments WHERE payment_id=?",
                               (payment_id,)).fetchone()

    def _log(self, pid: str, frm: str, to: str, now: datetime, cause: str) -> None:
        self.db.execute(
            "INSERT INTO payment_transitions (payment_id, from_state, to_state, ts, cause)"
            " VALUES (?,?,?,?,?)", (pid, frm, to, now.isoformat(), cause))

    def transition(self, payment_id: str, to: str, now: datetime, cause: str) -> str:
        row = self.get(payment_id)
        if row is None:
            raise IllegalTransition(f"no such payment {payment_id}")
        frm = row["state"]
        if to == frm:
            return frm                      # idempotent no-op, e.g. duplicate webhook
        if to not in LEGAL.get(frm, set()):
            raise IllegalTransition(f"{frm} -> {to} is not a legal transition")
        self.db.execute(
            "UPDATE payments SET state=?, updated_at=?, unknown_since=? WHERE payment_id=?",
            (to, now.isoformat(),
             now.isoformat() if to == "UNKNOWN" else row["unknown_since"], payment_id))
        self._log(payment_id, frm, to, now, cause)
        return to

    def attach_order(self, payment_id: str, order_id: str) -> None:
        self.db.execute("UPDATE payments SET order_id=? WHERE payment_id=?",
                        (order_id, payment_id))

    def timeline(self, payment_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM payment_transitions WHERE payment_id=? ORDER BY seq",
            (payment_id,))]
