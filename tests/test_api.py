"""The HTTP surface, including the interactions the experience depends on.

The property line and the with/without comparison are product features, so
they get tested like product features rather than trusted because they
looked right in a browser once.
"""
import json

import pytest
from fastapi.testclient import TestClient

from remit import api as api_mod


@pytest.fixture
def client():
    api_mod.STATE.clear()
    return TestClient(api_mod.api)


def test_health_reports_the_facts_a_reviewer_wants_first(client):
    d = client.get("/health").json()
    assert d["status"] == "ok"
    assert d["products"] > 100
    assert d["ledger_intact"] is True
    assert d["gateway"] == "FakeGateway"


def test_shop_returns_a_full_decision(client):
    d = client.post("/api/shop",
                    json={"utterance": "buy running shoes under 5000"}).json()
    assert d["intent"]["category"] == "running shoes"
    assert d["authorization"]["verdict"] in ("AUTO", "STEP_UP", "DENY")
    assert len(d["authorization"]["clauses"]) >= 15
    assert d["drift"] is not None and d["risk"] is not None


# ---------- the property line ----------

def test_replay_re_decides_the_same_basket_under_a_new_authority(client):
    d = client.post("/api/shop",
                    json={"utterance": "buy running shoes under 5000"}).json()
    cid, total = d["correlation_id"], d["totals"]["total_paise"]

    generous = client.post("/api/replay", json={
        "correlation_id": cid, "ceiling_paise": total * 2}).json()
    assert generous["authorization"]["verdict"] == "AUTO"

    mean = client.post("/api/replay", json={
        "correlation_id": cid, "ceiling_paise": total // 2}).json()
    assert mean["authorization"]["verdict"] != "AUTO"
    assert "CEIL-001" in mean["authorization"]["failed"]
    # the basket did not move; only the permission did
    assert mean["total_paise"] == total


def test_replay_is_pure_and_fast(client):
    """The claim the whole frontier rests on: authorize() does no I/O."""
    d = client.post("/api/shop",
                    json={"utterance": "buy running shoes under 5000"}).json()
    cid = d["correlation_id"]
    r = client.post("/api/replay",
                    json={"correlation_id": cid, "ceiling_paise": 400000}).json()
    assert r["engine_us"] < 20000, "the pure path should be sub-20ms, not a request"
    # and it wrote nothing
    before = client.get("/api/control").json()["verdicts"]
    client.post("/api/replay", json={"correlation_id": cid, "ceiling_paise": 100000})
    assert client.get("/api/control").json()["verdicts"] == before


def test_replay_of_an_unknown_journey_404s_rather_than_guessing(client):
    r = client.post("/api/replay",
                    json={"correlation_id": "cor_nope", "ceiling_paise": 1000})
    assert r.status_code == 404


# ---------- with / without ----------

def test_compare_shows_the_boundary_doing_something(client):
    c = client.post("/api/compare", json={
        "utterance": "buy a yoga mat under 2500"}).json()
    assert c["without"]["total_paise"] > c["with"]["total_paise"], \
        "an unbounded agent should spend more"
    assert c["with"]["unauthorized_paise"] == 0
    assert c["without"]["unauthorized_paise"] > 0, \
        "with the integrity layer off, money should move past the ceiling"


def test_compare_is_the_same_code_path_not_a_different_build(client):
    c = client.post("/api/compare", json={
        "utterance": "buy running shoes under 5000"}).json()
    # both arms produce a real verdict from the same authorize()
    for arm in ("with", "without"):
        assert c[arm]["verdict"] in ("AUTO", "STEP_UP", "DENY")


# ---------- act V data ----------

def test_failures_are_parsed_from_the_markdown_not_retyped(client):
    f = client.get("/api/failures").json()
    assert f["count"] >= 8
    e = f["entries"][0]
    assert e["when"].startswith("2026")
    assert e["title"]
    assert any(k.startswith("what i saw") for k in e["fields"])


def test_builder_reports_real_build_facts(client):
    b = client.get("/api/builder").json()
    assert b["handle"] == "techuilaguy"
    assert b["this_build"]["products"] > 100
    assert b["this_build"]["failures_logged"] >= 8
    assert b["this_build"]["clauses"] == 17


def test_results_404_with_a_hint_rather_than_a_lie(client):
    r = client.get("/api/results/nope")
    assert r.status_code == 404


# ---------- the levers in Act IV are real ----------

@pytest.mark.parametrize("inject,expect_clause", [
    ({"revoked": True}, "AUTH-003"),
    ({"expire": True}, "AUTH-002"),
    ({"delist": True}, "STOCK-001"),
])
def test_break_levers_are_caught_by_a_named_clause(client, inject, expect_clause):
    d = client.post("/api/shop", json={
        "utterance": "buy running shoes under 5000",
        "human_confirms": True, "inject": inject}).json()
    assert expect_clause in d["authorization"]["failed"]
    assert d["payment_state"] != "CREATED"


def test_forged_webhook_is_refused_over_http(client):
    d = client.post("/api/shop", json={
        "utterance": "buy running shoes under 5000", "human_confirms": True}).json()
    assert d["payment_id"]
    body = json.dumps({"id": "evt_forged", "event": "payment.captured",
                       "payload": {"payment_id": d["payment_id"]}})
    r = client.post("/api/webhook", content=body,
                    headers={"content-type": "application/json",
                             "x-razorpay-signature": "deadbeef"}).json()
    assert r["accepted"] is False
    state = [p for p in client.get("/api/control").json()["payments"]
             if p["payment_id"] == d["payment_id"]][0]["state"]
    assert state == "CREATED", "a forged signature must never move state"


def test_the_static_experience_is_served(client):
    for path, needle in [("/", "REMIT"), ("/app.js", "property line"),
                         ("/style.css", "--signal"),
                         ("/vendor/gsap.min.js", "gsap")]:
        r = client.get(path)
        assert r.status_code == 200
        assert needle.lower() in r.text.lower()
