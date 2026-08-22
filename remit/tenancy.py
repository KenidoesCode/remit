"""Tenant, principal and role — the three things a shared deployment needs.

The readiness scorecard listed tenancy and production identity as FAIL, and
they were: one `user_id` column, one implicit tenant, and no notion of what
kind of actor was asking. That is fine for a single-merchant demo and it is the
first thing that breaks when two merchants share an instance.

WHAT A TENANT IS HERE
---------------------
A tenant owns catalog, authority, audit and money. Nothing crosses. The whole
model is one rule, applied everywhere rather than remembered in places:

    every row that belongs to somebody carries their tenant,
    and every read on the money path filters by it.

WHAT A PRINCIPAL IS
-------------------
An identity plus a ROLE, because "who" and "what they may do" are different
questions and conflating them is how privilege escalation gets built:

    HUMAN    grants authority. The only role that may approve or revoke.
    AGENT    spends it. May propose, may execute inside an envelope.
             May NOT approve its own step-up -- an agent that can answer the
             question it triggered has not been stopped by anything.
    MERCHANT sells. May read its own catalog and its own settlements.
             May never act on a customer's authority.
    ADMIN    operates. May read audit, may not spend.
    SYSTEM   the reconciler and the webhook handler. No interactive rights.

The separation matters most in one place: `AGENT` cannot approve. That is not a
policy setting, it is a role check, and it is the difference between a step-up
and a formality.

WHAT THIS IS NOT
----------------
Not an identity provider. The session still mints an opaque principal; the
tenant and role are attached to it. A production deployment resolves all three
from the merchant's own IdP, and that seam is `principal_from_upstream()` --
named, unwritten, and in the audit rather than faked here.
"""
from __future__ import annotations

import hmac
import os
import re
import secrets
import sqlite3
from dataclasses import dataclass
from hashlib import sha256

# Roles, most-privileged last in nothing -- these are not a ladder. A merchant
# is not "more" than an agent; they are different, and no role is a superset of
# another except ADMIN over reads.
HUMAN, AGENT, MERCHANT, ADMIN, SYSTEM = (
    "human", "agent", "merchant", "admin", "system")
ROLES = frozenset({HUMAN, AGENT, MERCHANT, ADMIN, SYSTEM})

# What each role may do. Absent from the set means no.
CAN_SPEND = frozenset({HUMAN, AGENT})
CAN_APPROVE = frozenset({HUMAN})            # deliberately NOT the agent
CAN_REVOKE = frozenset({HUMAN, ADMIN})
CAN_READ_AUDIT = frozenset({HUMAN, ADMIN, MERCHANT})
CAN_REFUND = frozenset({HUMAN, MERCHANT, ADMIN})

DEFAULT_TENANT = "tnt_default"
TENANT_RE = re.compile(r"^tnt_[A-Za-z0-9_-]{1,40}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
  tenant_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS principals (
  principal_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  role TEXT NOT NULL,
  label TEXT,
  agent_id TEXT,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_principal_tenant ON principals(tenant_id, role);
"""


class CrossTenant(Exception):
    """Raised, never logged-and-continued.

    A cross-tenant read that returns nothing looks identical to a query with no
    results, and "it returned nothing" is how a leak stays invisible until it
    does not.
    """


class Forbidden(Exception):
    """The principal exists, belongs here, and may not do this."""


@dataclass(frozen=True)
class Principal:
    """Who is asking, on behalf of whom, in what capacity."""
    principal_id: str
    tenant_id: str
    role: str
    agent_id: str | None = None
    label: str | None = None

    # -- capability questions, asked rather than inferred from the role string
    @property
    def may_spend(self) -> bool:
        return self.role in CAN_SPEND

    @property
    def may_approve(self) -> bool:
        return self.role in CAN_APPROVE

    @property
    def may_revoke(self) -> bool:
        return self.role in CAN_REVOKE

    @property
    def may_read_audit(self) -> bool:
        return self.role in CAN_READ_AUDIT

    @property
    def may_refund(self) -> bool:
        return self.role in CAN_REFUND

    def owns(self, tenant_id: str | None) -> bool:
        return tenant_id is None or tenant_id == self.tenant_id

    def require(self, capability: str) -> None:
        if not getattr(self, f"may_{capability}", False):
            raise Forbidden(
                f"a {self.role} principal may not {capability.replace('_', ' ')}")

    def require_tenant(self, tenant_id: str | None) -> None:
        if not self.owns(tenant_id):
            raise CrossTenant(
                f"{self.principal_id} is in {self.tenant_id}, that object is "
                f"in {tenant_id}")

    def dict(self) -> dict:
        return {"principal_id": self.principal_id, "tenant_id": self.tenant_id,
                "role": self.role, "agent_id": self.agent_id,
                "label": self.label}


class Directory:
    """Where principals live. Small on purpose."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.executescript(SCHEMA)

    def ensure_tenant(self, tenant_id: str, name: str, now) -> str:
        if not TENANT_RE.match(tenant_id):
            raise ValueError(f"not a tenant id: {tenant_id!r}")
        self.db.execute(
            "INSERT OR IGNORE INTO tenants (tenant_id, name, created_at)"
            " VALUES (?,?,?)", (tenant_id, name, now.isoformat()))
        return tenant_id

    def register(self, *, principal_id: str, tenant_id: str, role: str, now,
                 agent_id: str | None = None,
                 label: str | None = None) -> Principal:
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        self.ensure_tenant(tenant_id, tenant_id, now)
        self.db.execute(
            "INSERT OR REPLACE INTO principals (principal_id, tenant_id, role,"
            " label, agent_id, created_at) VALUES (?,?,?,?,?,?)",
            (principal_id, tenant_id, role, label, agent_id, now.isoformat()))
        return Principal(principal_id, tenant_id, role, agent_id, label)

    def get(self, principal_id: str) -> Principal | None:
        cur = self.db.execute(
            "SELECT * FROM principals WHERE principal_id=?", (principal_id,))
        try:
            row = cur.fetchone()
        finally:
            cur.close()
        if row is None:
            return None
        return Principal(row["principal_id"], row["tenant_id"], row["role"],
                         row["agent_id"], row["label"])

    def resolve(self, principal_id: str, now) -> Principal:
        """Known principals keep their tenant and role. An unknown one -- a
        fresh browser session -- becomes a HUMAN in the default tenant, which
        is what a single-merchant demo needs and what a production deployment
        replaces with `principal_from_upstream()`."""
        found = self.get(principal_id)
        if found is not None:
            return found
        return self.register(principal_id=principal_id,
                             tenant_id=DEFAULT_TENANT, role=HUMAN, now=now)


# ── tenant in the session cookie ────────────────────────────────────────────
#
# The tenant travels with the identity, signed by the same secret, because a
# tenant a caller can set is a tenant a caller can set to somebody else's --
# the exact shape of FAILURES #32.

def stamp(principal_id: str, tenant_id: str, role: str, secret: str) -> str:
    body = f"{principal_id}|{tenant_id}|{role}"
    sig = hmac.new(secret.encode(), body.encode(), sha256).hexdigest()[:32]
    return f"{body}|{sig}"


def unstamp(value: str | None, secret: str) -> Principal | None:
    if not value or value.count("|") != 3:
        return None
    pid, tid, role, sig = value.split("|")
    body = f"{pid}|{tid}|{role}"
    want = hmac.new(secret.encode(), body.encode(), sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, want):
        return None
    if role not in ROLES or not TENANT_RE.match(tid):
        return None
    return Principal(pid, tid, role)
