"""A limit is not an authority — as a computation, not a slide.

This is the project's central conceptual claim, so it must be the thing least
allowed to be staged. One mandate is held fixed and the AGENT's action varies;
every alternative is re-decided against the ORIGINAL envelope by the same drift
engine and the same 22 clauses.

The first version got this wrong in a way worth recording. Each row was run as
its own utterance, so each row got its own mandate — and "buy 4 running shoes
under 50000" came back AUTO. Correct, and beside the point: nobody asked for
running shoes. The human said laptop. A demonstration that quietly changes the
question is a demonstration that proves nothing.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import remit.api as api_mod
from remit.api import api


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REMIT_DB", str(tmp_path / "lim.sqlite"))
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_forthetests")
    api_mod.STATE.pop("app", None)
    with TestClient(api) as boot:
        boot.get("/health")
        yield
    api_mod.STATE.pop("app", None)


def ask(c, utterance=None):
    return c.post("/api/limit-vs-authority",
                  json={"utterance": utterance} if utterance else {}).json()


def test_the_gap_between_a_limit_and_an_authority_is_real(env):
    with TestClient(api) as c:
        d = ask(c)
        s = d["summary"]
        assert s["of"] >= 3
        assert s["a_limit_would_allow"] > s["remit_allows_alone"], (
            "if a limit and REMIT allow the same set, this page has no argument")


def test_the_thing_that_was_asked_for_is_allowed(env):
    """The other half, and the one that keeps this from being a system that
    simply refuses everything. Row one is a REAL journey — ranking included —
    not a synthetic cart."""
    with TestClient(api) as c:
        first = ask(c)["rows"][0]
        assert first["why"].startswith("the thing that was actually asked")
        assert first["remit"] == "AUTO", first
        assert first["drift"] == 0.0, first


def test_every_alternative_is_under_the_number_and_still_stopped(env):
    with TestClient(api) as c:
        d = ask(c)
        alts = d["rows"][1:]
        assert alts, "no alternatives were generated"
        for row in alts:
            assert row["a_limit_allows"] is True, (
                f"{row['product']} is not under the ceiling, so it does not "
                f"make the point")
            assert row["remit"] != "AUTO", row
            assert row["failed"], row
            assert row["drift"] > 0, row


def test_the_mandate_is_held_fixed(env):
    """Every row is the same human sentence with a different thing in the
    cart. If the rows carried their own utterances they would carry their own
    mandates, and the comparison would be meaningless."""
    with TestClient(api) as c:
        d = ask(c, "buy running shoes under 5000")
        assert d["mandate"]["utterance"] == "buy running shoes under 5000"
        assert d["mandate"]["ceiling_paise"] == 500000
        assert "held fixed" in d["mandate"]["note"]
        assert all("utterance" not in row for row in d["rows"])


def test_the_laptop_case_is_the_model_being_wrong(env):
    """This shop sells a laptop STAND and no laptop. The agent's best answer to
    'buy a laptop' is therefore the wrong product, and REMIT stops it — which
    is the demonstration, not a bug in the catalog."""
    with TestClient(api) as c:
        d = ask(c, "buy a laptop under 50000")
        first = d["rows"][0]
        assert "stand" in first["product"].lower()
        assert first["remit"] != "AUTO"
        assert "MATCH-001" in first["failed"], first["failed"]
        assert d["summary"]["remit_allows_alone"] == 0


def test_nothing_here_touches_the_live_instance(env):
    with TestClient(api) as c:
        before = api_mod.get_app().db.execute(
            "SELECT COUNT(*) c FROM payments").fetchone()["c"]
        shelf = c.get("/api/catalog?q=running shoes").json()["catalog_version"]
        for _ in range(3):
            assert ask(c)["sandboxed"] is True
        after = api_mod.get_app().db.execute(
            "SELECT COUNT(*) c FROM payments").fetchone()["c"]
        assert after == before
        assert c.get("/api/catalog?q=running shoes").json()["catalog_version"] == shelf


def test_an_ungroundable_mandate_is_refused_not_faked(env):
    with TestClient(api) as c:
        r = c.post("/api/limit-vs-authority",
                   json={"utterance": "buy a helicopter"})
        assert r.status_code == 422
        assert r.json()["error"] == "not_grounded"
