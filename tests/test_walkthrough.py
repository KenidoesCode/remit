"""The five presses the page promises, asserted over HTTP.

`web/app.js` renders a walk-through whose whole claim is that each step is a
real request and that each step commits to an outcome before it fires. A demo
that makes a claim the suite does not check is a demo that quietly rots: the
engine changes, the page still draws five green ticks, and the first person to
notice is a judge.

So this file is the same five steps, in the same order, against the same
endpoint, asserting the same outcomes. If the walk-through on the page can go
green while this file is red, one of the two is lying.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import remit.api as api_mod
from remit.api import api

ASK = "buy whisky under 2000"          # must match WALK_ASK in web/app.js


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("REMIT_DB", str(tmp_path / "walk.sqlite"))
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_forthetests")
    api_mod.STATE.pop("app", None)
    with TestClient(api) as boot:
        boot.get("/health")
        yield
    api_mod.STATE.pop("app", None)


def shop(c, **kw):
    return c.post("/api/shop",
                  json={"utterance": ASK, "accept_offers": "in_envelope", **kw}).json()


def test_the_walkthrough_holds_end_to_end(env):
    """One test, because the steps are one sequence and a step that passes in
    isolation proves nothing about the state the previous step left behind."""
    with TestClient(api) as browser, TestClient(api) as somebody_else:

        # 1 — the ask that an agent must not finish alone
        one = shop(browser)
        assert one["payment_state"] == "AWAITING_HUMAN", one["payment_state"]
        token = (one.get("approval") or {}).get("token")
        assert token, "no approval token was issued to redeem"
        for field in ("intent_hash", "cart_hash", "amount_paise", "expires_at"):
            assert one["approval"].get(field), f"the token does not bind {field}"

        # 2 — approve, and an order exists
        two = shop(browser, human_confirms=True, approval_token=token)
        assert two["payment_state"] == "CREATED", two
        assert two["order_id"]

        # 3 — the same approval, a second time
        three = shop(browser, human_confirms=True, approval_token=token)
        assert three["payment_state"] == "APPROVAL_REJECTED", three
        assert "already_used" in three["note"], three["note"]

        # 4 — a fresh approval, and a basket that changed after the yes
        fresh = (shop(browser).get("approval") or {}).get("token")
        assert fresh and fresh != token
        four = shop(browser, human_confirms=True, approval_token=fresh,
                    inject={"qty": 2})
        assert four["payment_state"] == "APPROVAL_REJECTED", four
        assert "cart_changed" in four["note"], four["note"]

        # 5 — somebody else, holding a token that is still unused
        five = shop(somebody_else, human_confirms=True, approval_token=fresh)
        assert five["payment_state"] == "APPROVAL_REJECTED", five
        assert "wrong_actor" in five["note"], five["note"]


def test_the_tamper_lever_leaves_no_catalog_change_behind(env):
    """Step 4 has to be re-runnable by the next visitor.

    `inject {"price": …}` would have done the job too, and would have moved a
    real catalog row every time a reviewer pressed it — the demo would inflate
    its own prices. `inject {"qty": …}` mutates the in-flight cart only."""
    with TestClient(api) as c:
        before = shop(c)["selected"]["price_paise"]
        tok = (shop(c).get("approval") or {}).get("token")
        shop(c, human_confirms=True, approval_token=tok, inject={"qty": 2})
        assert shop(c)["selected"]["price_paise"] == before


def test_a_rejected_token_is_not_a_spent_token(env):
    """Step 5 depends on step 4 having refused BEFORE consuming the token.
    If a cart_changed rejection also burned the token, step 5 would report
    already_used and the page would draw a red tick for the right reason with
    the wrong explanation."""
    with TestClient(api) as c:
        tok = (shop(c).get("approval") or {}).get("token")
        bad = shop(c, human_confirms=True, approval_token=tok, inject={"qty": 2})
        assert "cart_changed" in bad["note"]
        good = shop(c, human_confirms=True, approval_token=tok)
        assert good["payment_state"] == "CREATED", good


def test_the_page_and_the_test_ask_for_the_same_thing(env):
    """The one thing that can silently desynchronise these two files."""
    import pathlib
    js = pathlib.Path(__file__).resolve().parents[1] / "web" / "app.js"
    src = js.read_text(encoding="utf-8")
    assert f'const WALK_ASK = "{ASK}"' in src, (
        "web/app.js walks a different sentence than this test asserts")
