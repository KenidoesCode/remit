"""Reconciliation: the owner of the UNKNOWN state.

Never guesses. Asks the gateway what actually happened, and only then moves
the payment. If the gateway cannot say, the payment stays UNKNOWN and lands
on the exception list -- which is reported, not hidden.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from .payments import PaymentStore


class Reconciler:
    def __init__(self, db: sqlite3.Connection, payments: PaymentStore, gateway):
        self.db, self.payments, self.gw = db, payments, gateway

    def pending(self, now: datetime, older_than_s: int = 0) -> list[sqlite3.Row]:
        cutoff = (now - timedelta(seconds=older_than_s)).isoformat()
        return list(self.db.execute(
            "SELECT * FROM payments WHERE state='UNKNOWN' AND unknown_since<=?"
            " ORDER BY unknown_since", (cutoff,)))

    def run(self, now: datetime) -> dict:
        resolved, still, exceptions = 0, 0, []
        for row in self.pending(now):
            pid = row["payment_id"]
            self.payments.transition(pid, "RECONCILING", now, "reconciler picked up")
            truth = None
            try:
                truth = self.gw.lookup_by_receipt(row["idem_key"][:40])
            except Exception as e:
                exceptions.append({"payment_id": pid, "why": f"gateway error: {e}"})
            if truth is None:
                self.payments.transition(pid, "UNKNOWN", now, "gateway has no record")
                still += 1
                exceptions.append({"payment_id": pid,
                                   "why": "no gateway record; stays UNKNOWN",
                                   "amount_paise": row["amount_paise"]})
                continue
            self.payments.attach_order(pid, truth["id"])
            state = "SUCCESS" if truth.get("status") in ("paid", "captured") else "FAILED"
            self.payments.transition(pid, state, now, "reconciled against gateway")
            resolved += 1
        total = resolved + still
        return {"scanned": total, "resolved": resolved, "unresolved": still,
                "match_rate": round(resolved / total, 4) if total else 1.0,
                "exceptions": exceptions}
