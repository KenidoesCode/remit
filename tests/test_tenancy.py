"""Merchant A cannot see, spend, revoke or audit Merchant B.

The readiness scorecard listed tenancy as **FAIL**, and it was: one `user_id`
column, one implicit tenant, and no notion of what kind of actor was asking.
Fine for a single-merchant demo, and the first thing that breaks the day two
merchants share an instance.

Two separate properties are tested here and they fail differently:

  · **isolation** -- a row belonging to tenant B is invisible to tenant A. The
    failure mode is a leak, and it is silent.
  · **capability** -- an AGENT may spend and may NOT approve. The failure mode
    is privilege escalation, and it is also silent, because an agent that can
    answer the step-up it triggered has not been stopped by anything.

The second is the one worth staring at. A step-up whose approval the agent can
supply is not a control, it is a formality with a round trip.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import remit.api as api_mod
from remit.api import api
from remit.assembly import build
from remit.domain.risk import Exposure
from remit.exec.razorpay import FakeGateway
from remit.tenancy import (ADMIN, AGENT, DEFAULT_TENANT, HUMAN, MERCHANT,
                           SYSTEM, CrossTenant, Directory, Forbidden,
                           Principal, stamp, unstamp)

NOW = datetime(2026, 8, 22, 19, 0, tzinfo=timezone.utc)
BUYS = "buy running shoes under 5000"


@pytest.fixture
def app():
    return build(now=NOW, gateway=FakeGateway())


@pytest.fixture
def dirn(app):
    return Directory(app.db)


@pytest.fixture
def http(tmp_path, monkeypatch):
    monkeypatch.setenv("REMIT_DB", str(tmp_path / "tnt.sqlite"))
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_forthetests")
    api_mod.STATE.pop("app", None)
    with TestClient(api) as boot:
        boot.get("/health")
        yield
    api_mod.STATE.pop("app", None)


def run(app, user, tenant, utterance=BUYS, **kw):
    return app.journey.run(utterance=utterance, user_id=user, now=NOW,
                           exposure=Exposure(), tenant_id=tenant, **kw)


# ─────────────────────────────────────────────────── capability, not identity

def test_an_agent_may_spend_and_may_not_approve(dirn):
    """The one that matters most. An agent that can approve its own step-up
    has not been stopped by anything -- the step-up becomes a formality with a
    round trip in it."""
    agent = dirn.register(principal_id="usr_a", tenant_id="tnt_a",
                          role=AGENT, now=NOW)
    assert agent.may_spend is True
    assert agent.may_approve is False
    assert agent.may_revoke is False
    with pytest.raises(Forbidden):
        agent.require("approve")


def test_only_a_human_may_approve(dirn):
    for role, may in ((HUMAN, True), (AGENT, False), (MERCHANT, False),
                      (ADMIN, False), (SYSTEM, False)):
        p = dirn.register(principal_id=f"usr_{role}", tenant_id="tnt_a",
                          role=role, now=NOW)
        assert p.may_approve is may, role


def test_a_merchant_may_never_spend_a_customers_authority(dirn):
    """The merchant is partially trusted -- for its own catalog and prices, and
    for nothing about what a customer authorised."""
    m = dirn.register(principal_id="usr_m", tenant_id="tnt_a",
                      role=MERCHANT, now=NOW)
    assert m.may_spend is False
    assert m.may_approve is False
    with pytest.raises(Forbidden):
        m.require("spend")


def test_an_admin_may_read_and_may_not_spend(dirn):
    a = dirn.register(principal_id="usr_ad", tenant_id="tnt_a",
                      role=ADMIN, now=NOW)
    assert a.may_read_audit is True
    assert a.may_revoke is True          # an operator can stop things
    assert a.may_spend is False          # and can never start one


def test_an_unknown_role_is_refused(dirn):
    with pytest.raises(ValueError):
        dirn.register(principal_id="usr_x", tenant_id="tnt_a",
                      role="superuser", now=NOW)


# ─────────────────────────────────────────────────────────── isolation

def test_a_principal_belongs_to_exactly_one_tenant(dirn):
    p = dirn.register(principal_id="usr_1", tenant_id="tnt_a", role=HUMAN,
                      now=NOW)
    assert p.owns("tnt_a")
    assert not p.owns("tnt_b")
    with pytest.raises(CrossTenant):
        p.require_tenant("tnt_b")


def test_every_row_on_the_money_path_carries_a_tenant(app):
    run(app, "usr_a", "tnt_a", human_confirms=True)
    for table in ("intents", "payments", "decisions"):
        rows = [dict(r) for r in app.db.execute(
            f"SELECT tenant_id FROM {table}")]
        assert rows, table
        assert all(r["tenant_id"] == "tnt_a" for r in rows), (table, rows)


def test_two_tenants_do_not_share_a_payment_namespace(app):
    """The same person-shaped id, the same sentence, two tenants. Two
    purchases, because they are two different worlds -- and if the idempotency
    namespace ignored the tenant, the second buyer would silently 'replay' the
    first buyer's payment and receive nothing."""
    a = run(app, "usr_same", "tnt_a", human_confirms=True)
    b = run(app, "usr_same", "tnt_b", human_confirms=True)
    tenants = {r["tenant_id"] for r in app.db.execute(
        "SELECT tenant_id FROM payments")}
    assert tenants == {"tnt_a", "tnt_b"}
    assert a.payment_id != b.payment_id or a.replayed is False


def test_a_query_scoped_to_one_tenant_cannot_see_the_other(app):
    run(app, "usr_a", "tnt_a", human_confirms=True)
    run(app, "usr_b", "tnt_b", human_confirms=True)
    for t in ("tnt_a", "tnt_b"):
        rows = [dict(r) for r in app.db.execute(
            "SELECT user_id FROM payments WHERE tenant_id=?", (t,))]
        assert len(rows) == 1, (t, rows)
    assert app.db.execute(
        "SELECT COUNT(*) c FROM payments WHERE tenant_id='tnt_c'"
    ).fetchone()["c"] == 0


# ────────────────────────────────────────────────── the tenant is not a field

def test_the_tenant_travels_signed_and_cannot_be_forged():
    """A tenant a caller can set is a tenant a caller can set to somebody
    else's -- FAILURES #32, one level up."""
    good = stamp("usr_1", "tnt_a", "human", "secret")
    back = unstamp(good, "secret")
    assert back and back.tenant_id == "tnt_a" and back.role == "human"

    assert unstamp("usr_1|tnt_b|human|" + good.split("|")[-1], "secret") is None
    assert unstamp(good, "another-secret") is None
    assert unstamp("usr_1|tnt_a|admin|deadbeef", "secret") is None
    assert unstamp(None, "secret") is None
    # a role that is not a role, correctly signed, is still refused
    forged = stamp("usr_1", "tnt_a", "superuser", "secret")
    assert unstamp(forged, "secret") is None
    # so is a tenant id that is not shaped like one
    assert unstamp(stamp("usr_1", "../../etc", "human", "secret"),
                   "secret") is None


def test_no_request_model_has_a_tenant_field():
    """Structural, like the identity fix. A rejected field is a field somebody
    finds a second spelling for; a field that does not exist is not."""
    import remit.api as m
    for name in ("ShopRequest", "CompareRequest", "ReplayRequest",
                 "RevokeRequest", "LimitRequest"):
        model = getattr(m, name, None)
        if model is None:
            continue
        for field in model.model_fields:
            assert "tenant" not in field.lower(), (name, field)
            assert "role" not in field.lower(), (name, field)


# ──────────────────────────────────────────────────────── over HTTP

def test_a_session_gets_a_principal_with_a_tenant_and_a_role(http):
    with TestClient(api) as c:
        c.post("/api/shop", json={"utterance": BUYS})
        rows = [dict(r) for r in api_mod.get_app().db.execute(
            "SELECT principal_id, tenant_id, role FROM principals")]
        assert len(rows) == 1, rows
        assert rows[0]["tenant_id"] == DEFAULT_TENANT
        assert rows[0]["role"] == HUMAN


def test_two_browsers_are_two_principals_in_one_tenant(http):
    """Single-tenant deployment: different people, same world. The isolation
    that matters here is per-principal, and it already holds."""
    with TestClient(api) as alice, TestClient(api) as bob:
        a = alice.post("/api/shop", json={"utterance": BUYS}).json()
        assert alice.get(f"/api/checkout/{a['correlation_id']}").status_code == 200
        assert bob.get(f"/api/checkout/{a['correlation_id']}").status_code == 404


def test_the_checkout_query_is_scoped_by_tenant_as_well_as_principal(http):
    """Belt and braces, and the braces are the one that survives a bug in the
    belt: if two tenants ever shared a principal id, the tenant filter is what
    stops the leak."""
    import inspect
    src = inspect.getsource(api_mod.checkout)
    assert "p.user_id=?" in src
    assert "p.tenant_id=?" in src


def test_a_cross_tenant_read_raises_rather_than_returning_nothing(dirn):
    """A cross-tenant read that quietly returns nothing looks exactly like a
    query with no results, and that is how a leak stays invisible until it
    is not."""
    p = Principal("usr_1", "tnt_a", HUMAN)
    with pytest.raises(CrossTenant) as e:
        p.require_tenant("tnt_b")
    assert "tnt_a" in str(e.value) and "tnt_b" in str(e.value)
