"""Taking it back.

`intents.revoked_at` has been in the schema since the first migration. Nothing
ever wrote to it and nothing ever read it. `AUTH-003` — a hard DENY clause
whose entire purpose is to refuse a revoked mandate — took its input from
`inject.get("revoked")`, a boolean the caller passed in on the request. So the
honest description of revocation in this system was: *a demo lever*.

That matters more than a missing feature, because "can I revoke it?" is one of
the questions a person actually asks before handing an agent money, and REMIT
answered it on the page while not implementing it in the code.

WHAT REVOCATION MEANS HERE
--------------------------
Two scopes, because people mean two different things:

    intent      this mandate, by id. "Not that one."
    principal   everything this actor holds. "Stop."

The second is the one somebody reaches for at 2am. It is a kill switch, it
takes effect immediately, and it does not require them to know an id.

FORWARD ONLY
------------
Revocation stops what has not happened yet. It does not unwind a payment that
already moved — a refund is a different operation with a different authority,
and a control plane that quietly reverses settled money is a control plane
nobody can reason about. Revoking after execution is allowed, recorded, and
changes nothing about the completed transaction. The record says so.

WHY IT WINS
-----------
Revocation is checked twice on every journey: once by the policy engine, where
it is a hard clause, and once again immediately before the payment is created.
The second check exists because the interesting case is the one where the
revocation lands *between* those two moments. Today a single process-wide lock
makes that interleaving impossible; the re-check is what keeps the guarantee
true when that stops being the case. A control that is only correct because of
a lock it does not own is not a control.
"""
from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS revocations (
  revocation_id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,               -- 'intent' | 'principal'
  target TEXT NOT NULL,              -- intent_id, or the principal itself
  user_id TEXT NOT NULL,             -- whose authority this was
  revoked_by TEXT NOT NULL,          -- who pressed it
  reason TEXT,
  revoked_at TEXT NOT NULL,
  tenant_id TEXT NOT NULL DEFAULT 'tnt_default',
  state_at_revocation TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS idx_revocation_target
  ON revocations(scope, target, user_id);
CREATE INDEX IF NOT EXISTS idx_revocation_user ON revocations(user_id, revoked_at);
"""


def _one(db, sql: str, args: tuple):
    """Read one row and let go of the cursor.

    `db.execute(...).fetchone()` on a shared sqlite3 connection leaves the
    connection's implicit cursor holding the rows nobody asked for, and the
    next statement on that connection from another thread fails with
    "another row available". Every other store in this repository does exactly
    that and has never seen it, because every one of them runs under the API's
    process-wide lock. This one is also called directly -- by the kill switch,
    which is the operation least entitled to require a lock it does not own.
    """
    cur = db.execute(sql, args)
    try:
        return cur.fetchone()
    finally:
        cur.close()


@dataclass(frozen=True)
class Revocation:
    revocation_id: str
    scope: str
    target: str
    user_id: str
    revoked_by: str
    reason: str | None
    revoked_at: str
    state_at_revocation: str | None
    already: bool = False          # this call did not create it; it existed

    def dict(self) -> dict:
        return {"revocation_id": self.revocation_id, "scope": self.scope,
                "target": self.target, "user_id": self.user_id,
                "revoked_by": self.revoked_by, "reason": self.reason,
                "revoked_at": self.revoked_at,
                "state_at_revocation": self.state_at_revocation,
                "already_revoked": self.already}


class NotYours(Exception):
    """One person's authority is not another person's to cancel."""


class NoSuchIntent(Exception):
    """Refusing to record a revocation against something that never existed --
    otherwise the table becomes a place to write arbitrary strings, and an
    audit trail you can write anything into is not an audit trail."""


class RevocationStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.executescript(SCHEMA)

    # ---------------------------------------------------------------- write

    def revoke(self, *, user_id: str, now: datetime, scope: str = "principal",
               target: str | None = None, revoked_by: str | None = None,
               reason: str | None = None, state: str | None = None
               ) -> Revocation:
        """Cancel an authority. Idempotent, and it says which it was.

        Revoking twice is not an error — the person pressing it a second time
        wants the same outcome, and returning a failure would read as "it did
        not work". The record is the first one, with `already_revoked` set, so
        the audit trail keeps one revocation with one timestamp rather than a
        pile of them.
        """
        if scope not in ("intent", "principal"):
            raise ValueError(f"unknown revocation scope {scope!r}")
        target = target or user_id
        revoked_by = revoked_by or user_id

        if scope == "intent":
            row = _one(self.db, "SELECT user_id FROM intents WHERE intent_id=?",
                       (target,))
            if row is None:
                raise NoSuchIntent(target)
            if row["user_id"] != user_id:
                raise NotYours(target)
        elif target != user_id:
            raise NotYours(target)

        existing = _one(
            self.db,
            "SELECT * FROM revocations WHERE scope=? AND target=? AND user_id=?",
            (scope, target, user_id))
        if existing is not None:
            return Revocation(**{k: existing[k] for k in (
                "revocation_id", "scope", "target", "user_id", "revoked_by",
                "reason", "revoked_at", "state_at_revocation")}, already=True)

        rid = "rvk_" + secrets.token_urlsafe(15)
        try:
            self.db.execute(
                "INSERT INTO revocations (revocation_id, scope, target,"
                " user_id, revoked_by, reason, revoked_at,"
                " state_at_revocation) VALUES (?,?,?,?,?,?,?,?)",
                (rid, scope, target, user_id, revoked_by, reason,
                 now.isoformat(), state))
        except sqlite3.DatabaseError:
            # DatabaseError, not IntegrityError. Under thread contention
            # sqlite3 surfaces the same UNIQUE violation as the base class,
            # so catching the specific one worked sequentially and missed the
            # only case that mattered. The `if existing is None: raise` below
            # is what keeps the wider catch honest: anything that is not this
            # constraint still propagates.
            # Twelve tabs on one kill switch. The SELECT above and this INSERT
            # are not one operation, so the UNIQUE index is the serialisation
            # point rather than the read -- the same shape as the idempotency
            # key on payments. Exactly one INSERT wins; everybody else reads
            # the winner's row and gets the same answer they asked for, which
            # is that the authority is cancelled.
            existing = _one(
                self.db,
                "SELECT * FROM revocations WHERE scope=? AND target=?"
                " AND user_id=?", (scope, target, user_id))
            if existing is None:                      # not our constraint
                raise
            return Revocation(**{k: existing[k] for k in (
                "revocation_id", "scope", "target", "user_id", "revoked_by",
                "reason", "revoked_at", "state_at_revocation")}, already=True)
        if scope == "intent":
            # The column that was in the schema from the beginning and had
            # never been written to.
            self.db.execute(
                "UPDATE intents SET revoked_at=? WHERE intent_id=?",
                (now.isoformat(), target))
        else:
            self.db.execute(
                "UPDATE intents SET revoked_at=? WHERE user_id=? AND"
                " revoked_at IS NULL", (now.isoformat(), user_id))
        return Revocation(rid, scope, target, user_id, revoked_by, reason,
                          now.isoformat(), state)

    # ----------------------------------------------------------------- read

    def check(self, *, user_id: str, intent_id: str | None = None
              ) -> Revocation | None:
        """Is this authority cancelled? The principal scope wins over the
        intent scope, because "stop" is broader than "not that one"."""
        row = _one(
            self.db,
            "SELECT * FROM revocations WHERE user_id=? AND"
            " ((scope='principal' AND target=?) OR (scope='intent' AND target=?))"
            " ORDER BY CASE scope WHEN 'principal' THEN 0 ELSE 1 END,"
            " revoked_at LIMIT 1",
            (user_id, user_id, intent_id or ""))
        if row is None:
            return None
        return Revocation(**{k: row[k] for k in (
            "revocation_id", "scope", "target", "user_id", "revoked_by",
            "reason", "revoked_at", "state_at_revocation")})

    def is_revoked(self, *, user_id: str, intent_id: str | None = None) -> bool:
        return self.check(user_id=user_id, intent_id=intent_id) is not None

    def listing(self, *, user_id: str, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM revocations WHERE user_id=?"
            " ORDER BY revoked_at DESC LIMIT ?", (user_id, limit))]

    def restore(self, *, user_id: str, scope: str, target: str) -> bool:
        """Deliberately NOT exposed over HTTP.

        Un-revoking is a real operation an operator occasionally needs, and it
        is also the single most dangerous button in this file: an attacker who
        can reach it can undo the kill switch. It exists for tests and for a
        console, and if it is ever given an endpoint that endpoint needs a
        stronger authority than the one that pressed revoke.
        """
        cur = self.db.execute(
            "DELETE FROM revocations WHERE user_id=? AND scope=? AND target=?",
            (user_id, scope, target))
        if scope == "intent":
            self.db.execute("UPDATE intents SET revoked_at=NULL WHERE intent_id=?",
                            (target,))
        else:
            self.db.execute("UPDATE intents SET revoked_at=NULL WHERE user_id=?",
                            (user_id,))
        return cur.rowcount > 0
