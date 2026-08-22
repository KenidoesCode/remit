"""The lifecycle of an authority, as a machine rather than as a convention.

REMIT already had one real state machine -- `remit/exec/payments.py` -- with a
transition table, an `IllegalTransition` exception and a persisted history. The
authority itself had none. Its lifecycle lived as free strings assigned to a
dataclass field at eight different points in `journey.run`:

    "NONE" "BLOCKED" "AWAITING_HUMAN" "APPROVAL_REJECTED" "DECLINED_BY_HUMAN"
    "CREATED" "UNKNOWN" "FAILED"

Those are the terminal return values of one function call. Nothing rejected a
move between them because there were no moves -- each was written once, at the
end. That worked exactly as long as a journey stayed a single synchronous
function, and it left the system with no way to answer "what state is this
authority in right now, and what is it allowed to do next".

WHAT THIS IS NOT
----------------
It is not a rename of the payment machine. Payment state is what the *gateway*
says happened to the money. Authority state is what the *human* permitted and
how far that permission has been consumed. They move independently -- an
authority can be REVOKED while its payment is still SUCCESS, and that is not a
contradiction, it is the ordinary case after somebody presses stop.

It is also deliberately not decorative. `advance()` is called on the real path
and its history is persisted, because a state machine the payment path ignores
is documentation with a type annotation.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

# ── the states ──────────────────────────────────────────────────────────────
#
#   DRAFT           the sentence arrived, nothing is understood yet
#   INTERPRETED     it compiled into a structured intent
#   GROUNDED        every requested thing found a real product in this catalog
#   AUTHORIZED      the policy engine said the agent may proceed alone
#   PENDING_STEP_UP the policy engine stopped and asked a person
#   APPROVED        a person redeemed a token bound to this exact basket
#   EXECUTING       an order exists at the gateway
#   EXECUTED        the gateway confirmed the money moved
#   SETTLED         the funds reconciled
#
# and the ends:
#
#   REJECTED   a hard clause refused it
#   EXPIRED    the envelope's TTL passed before it was used
#   REVOKED    a person cancelled the authority
#   CANCELLED  the agent or the human abandoned it
#   FAILED     the gateway refused or errored

LEGAL: dict[str, set[str]] = {
    "DRAFT":           {"INTERPRETED", "REJECTED", "CANCELLED", "EXPIRED"},
    # An intent that compiles but grounds to nothing is not a rejection by
    # policy -- it is a catalog that cannot answer. It ends CANCELLED, and the
    # human is told what the shop does stock.
    "INTERPRETED":     {"GROUNDED", "REJECTED", "CANCELLED", "EXPIRED",
                        "REVOKED"},
    "GROUNDED":        {"AUTHORIZED", "PENDING_STEP_UP", "REJECTED",
                        "EXPIRED", "REVOKED", "CANCELLED"},
    "AUTHORIZED":      {"EXECUTING", "EXPIRED", "REVOKED", "CANCELLED",
                        "REJECTED"},
    "PENDING_STEP_UP": {"APPROVED", "CANCELLED", "EXPIRED", "REVOKED",
                        "REJECTED"},
    "APPROVED":        {"EXECUTING", "EXPIRED", "REVOKED", "FAILED"},
    # Revocation is deliberately still legal from EXECUTING. An order exists at
    # the gateway and the money has not moved; stopping there is exactly what a
    # person pressing the kill switch means. It is NOT legal from EXECUTED --
    # that is a refund, a different authority, and pretending otherwise would
    # let this machine claim it had unwound settled money.
    "EXECUTING":       {"EXECUTED", "FAILED", "REVOKED"},
    "EXECUTED":        {"SETTLED", "FAILED"},
    "SETTLED":         set(),
    "REJECTED":        set(),
    "EXPIRED":         set(),
    "REVOKED":         set(),
    "CANCELLED":       set(),
    "FAILED":          set(),
}

TERMINAL = {s for s, nxt in LEGAL.items() if not nxt}
STATES = frozenset(LEGAL)

SCHEMA = """
CREATE TABLE IF NOT EXISTS authority_state (
  intent_id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  user_id TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS authority_transitions (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  intent_id TEXT NOT NULL,
  from_state TEXT,
  to_state TEXT NOT NULL,
  correlation_id TEXT,
  cause TEXT NOT NULL,
  ts TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_auth_trans ON authority_transitions(intent_id, seq);
"""


class IllegalTransition(Exception):
    """Raised rather than logged.

    A state machine that warns and continues is a state machine that does not
    constrain anything. The one place this is caught is the journey, which
    converts it into a refusal -- because an authority whose lifecycle the
    system has lost track of must not be the one that moves money.
    """

    def __init__(self, intent_id: str, frm: str | None, to: str):
        self.intent_id, self.frm, self.to = intent_id, frm, to
        super().__init__(f"{intent_id}: {frm} -> {to} is not a legal move")


def legal(frm: str | None, to: str) -> bool:
    if to not in STATES:
        return False
    if frm is None:
        return to == "DRAFT"
    return to in LEGAL.get(frm, set())


class AuthorityMachine:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.executescript(SCHEMA)

    def state(self, intent_id: str) -> str | None:
        # Cursor closed explicitly: `.fetchone()` on a shared connection leaves
        # the implicit cursor holding rows, and the next statement from another
        # thread fails with "another row available". Safe under the API lock,
        # not safe for a machine that has to be correct without one.
        cur = self.db.execute(
            "SELECT state FROM authority_state WHERE intent_id=?", (intent_id,))
        try:
            row = cur.fetchone()
        finally:
            cur.close()
        return row["state"] if row else None

    def open(self, *, intent_id: str, user_id: str, now: datetime,
             correlation_id: str | None = None) -> str:
        """Start a lifecycle at DRAFT. Idempotent: a resent sentence rejoins
        the authority it already opened rather than resetting it, which is the
        same reasoning that keeps the idempotency key off the intent id."""
        existing = self.state(intent_id)
        if existing is not None:
            return existing
        self.db.execute(
            "INSERT INTO authority_state (intent_id, state, user_id, updated_at)"
            " VALUES (?,?,?,?)", (intent_id, "DRAFT", user_id, now.isoformat()))
        self._record(intent_id, None, "DRAFT", correlation_id, "opened", now)
        return "DRAFT"

    def advance(self, *, intent_id: str, to: str, now: datetime, cause: str,
                correlation_id: str | None = None) -> str:
        """Move, or refuse to.

        Same-state is a no-op rather than an error: two webhooks reporting the
        same outcome are one outcome, and the payment machine settled on the
        same rule for the same reason.
        """
        frm = self.state(intent_id)
        if frm == to:
            return to
        if not legal(frm, to):
            raise IllegalTransition(intent_id, frm, to)
        # Predicated on the state we read, not on having read it.
        #
        # Read-then-write lost an update the first time this was contended:
        # two threads both saw AUTHORIZED, both moved to REVOKED, and the
        # history recorded the same transition twice. Nothing about that is
        # visible in a sequential test, and "same state is a no-op" does not
        # help -- both threads passed that check before either wrote.
        #
        # The WHERE clause makes the database the serialisation point, the way
        # the approval token's `WHERE used_at IS NULL` and the payment's UNIQUE
        # idempotency key already do. Exactly one UPDATE matches a row.
        cur = self.db.execute(
            "UPDATE authority_state SET state=?, updated_at=?"
            " WHERE intent_id=? AND state=?",
            (to, now.isoformat(), intent_id, frm))
        if cur.rowcount != 1:
            # Somebody moved first. Their move is the one that happened; ours
            # is re-judged against where the authority actually is now.
            actual = self.state(intent_id)
            if actual == to:
                return to
            raise IllegalTransition(intent_id, actual, to)
        self._record(intent_id, frm, to, correlation_id, cause, now)
        return to

    def history(self, intent_id: str) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT from_state, to_state, correlation_id, cause, ts"
            " FROM authority_transitions WHERE intent_id=? ORDER BY seq",
            (intent_id,))]

    def _record(self, intent_id, frm, to, cid, cause, now) -> None:
        self.db.execute(
            "INSERT INTO authority_transitions (intent_id, from_state,"
            " to_state, correlation_id, cause, ts) VALUES (?,?,?,?,?,?)",
            (intent_id, frm, to, cid, cause, now.isoformat()))
