"""What a human actually agreed to, and how we prove it later.

A step-up that returns `true` from a checkbox is not an approval. It is a
boolean, and a boolean cannot answer the only question that matters in a
dispute: *approved WHAT?*

So an approval here is a token bound to five things, all of them hashed at the
moment the person was shown the basket:

    who        the user id it was issued to
    what       the intent, by semantic hash -- the request, not the request id
    which      the cart, by a signature over (product, qty, unit price)
    how much   the exact total that was on screen
    until when a short expiry, because consent goes stale

Change any of them and the token stops verifying. That is the whole design:

    CHANGED CART      -> different cart hash      -> rejected
    CHANGED PRICE     -> different cart hash      -> rejected
    CHANGED PRODUCT   -> different cart hash      -> rejected
    DIFFERENT AMOUNT  -> different amount         -> rejected
    REUSED            -> used_at is already set   -> rejected
    LATE              -> past expires_at          -> rejected

Single-use is enforced by an UPDATE with a `used_at IS NULL` predicate rather
than a read-then-write, so two browser tabs racing the same token cannot both
win: SQLite serialises the writes and exactly one of them changes a row.
"""
from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..models import canonical, sha

SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  intent_id TEXT NOT NULL,
  intent_hash TEXT NOT NULL,
  cart_hash TEXT NOT NULL,
  amount_paise INTEGER NOT NULL,
  currency TEXT NOT NULL,
  merchants TEXT NOT NULL,
  correlation_id TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT);
CREATE INDEX IF NOT EXISTS idx_approvals_user ON approvals(user_id, created_at);
"""


def cart_hash(cart) -> str:
    """A signature over what is actually being bought.

    Product, quantity and unit price -- not the cart id, which is fresh on
    every journey, and not the line ordering, which is an implementation
    detail. Two carts that would charge the same person the same money for the
    same things hash the same, and anything else does not.
    """
    return sha(canonical(sorted(
        [l.product_id, l.qty, l.unit_price_paise] for l in cart.lines)))


@dataclass(frozen=True)
class Approval:
    token: str
    amount_paise: int
    expires_at: str
    cart_hash: str
    intent_hash: str


@dataclass(frozen=True)
class Rejection:
    reason: str
    detail: str


class ApprovalStore:
    def __init__(self, db: sqlite3.Connection, ttl_minutes: int = 15):
        self.db = db
        self.ttl = ttl_minutes
        self.db.executescript(SCHEMA)

    def issue(self, *, user_id: str, env, cart, totals, now: datetime,
              correlation_id: str) -> Approval:
        token = "apr_" + secrets.token_urlsafe(24)
        ch = cart_hash(cart)
        exp = now + timedelta(minutes=self.ttl)
        self.db.execute(
            "INSERT INTO approvals (token, user_id, intent_id, intent_hash,"
            " cart_hash, amount_paise, currency, merchants, correlation_id,"
            " created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (token, user_id, env.intent_id, env.semantic_hash, ch,
             totals.total_paise, env.currency,
             ",".join(sorted({l.merchant_id for l in cart.lines})),
             correlation_id, now.isoformat(), exp.isoformat()))
        return Approval(token=token, amount_paise=totals.total_paise,
                        expires_at=exp.isoformat(), cart_hash=ch,
                        intent_hash=env.semantic_hash)

    def redeem(self, *, token: str, user_id: str, env, cart, totals,
               now: datetime) -> Rejection | None:
        """Consume the token, or explain precisely why it does not apply.

        Returns None on success. The order of the checks is the order a human
        would want them explained -- 'this is not your approval' before 'the
        price changed' -- and every rejection names what differs rather than
        saying the token is invalid.
        """
        row = self.db.execute("SELECT * FROM approvals WHERE token=?",
                              (token,)).fetchone()
        if row is None:
            return Rejection("unknown", "no such approval was ever issued")
        if row["user_id"] != user_id:
            return Rejection("wrong_actor",
                             "that approval belongs to a different person")
        if row["used_at"]:
            return Rejection("already_used",
                             f"approved once already at {row['used_at']}")
        if now.isoformat() > row["expires_at"]:
            return Rejection("expired",
                             f"consent given at {row['created_at']} has gone "
                             f"stale; the basket has to be shown again")
        if row["intent_hash"] != env.semantic_hash:
            return Rejection("different_request",
                             "this approval was for a different request")
        ch = cart_hash(cart)
        if row["cart_hash"] != ch:
            return Rejection("cart_changed",
                             "the basket is not the one that was approved -- a "
                             "price, a product or a quantity has moved since")
        if row["amount_paise"] != totals.total_paise:
            return Rejection(
                "amount_changed",
                f"approved {row['amount_paise']} paise, the cart now totals "
                f"{totals.total_paise}")
        # Single-use, enforced by the predicate rather than by having read the
        # row a moment ago. Two tabs racing this both reach here; one UPDATE
        # matches a row and one does not.
        cur = self.db.execute(
            "UPDATE approvals SET used_at=? WHERE token=? AND used_at IS NULL",
            (now.isoformat(), token))
        if cur.rowcount != 1:
            return Rejection("already_used", "another request redeemed it first")
        return None

    def get(self, token: str):
        return self.db.execute("SELECT * FROM approvals WHERE token=?",
                               (token,)).fetchone()
