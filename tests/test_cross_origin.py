"""Cross-origin access is opt-in, exact, and does not weaken identity.

REMIT can be served two ways: by this service, or as static files from a CDN
talking to it across origins. The second one is where cookies get quietly
broken -- a SameSite=Lax cookie is simply not sent on a cross-site XHR, so
every call mints a fresh principal and exposure, revocation and audit scoping
stop working without anything failing. That is FAILURES #51 one layer out, and
these tests exist so it cannot happen again silently.
"""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

VERCEL = "https://remit.vercel.app"


def _app(monkeypatch, origins: str | None):
    """Reload the API with a given REMIT_ALLOWED_ORIGINS."""
    import remit.api as api_mod

    if origins is None:
        monkeypatch.delenv("REMIT_ALLOWED_ORIGINS", raising=False)
    else:
        monkeypatch.setenv("REMIT_ALLOWED_ORIGINS", origins)
    return importlib.reload(api_mod)


@pytest.fixture(autouse=True)
def _restore():
    """Every test here reloads the module, so put it back afterwards."""
    yield
    import os

    import remit.api as api_mod
    os.environ.pop("REMIT_ALLOWED_ORIGINS", None)
    importlib.reload(api_mod)


def test_cross_origin_is_off_by_default(monkeypatch):
    """No env var, no CORS. The safest configuration needs no code."""
    mod = _app(monkeypatch, None)
    assert mod.CROSS_ORIGIN is False
    c = TestClient(mod.api)
    r = c.options("/v1/intents", headers={
        "Origin": VERCEL,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.headers.get("access-control-allow-origin") is None


def test_a_named_origin_is_allowed_with_credentials(monkeypatch):
    mod = _app(monkeypatch, VERCEL)
    c = TestClient(mod.api)
    r = c.options("/v1/intents", headers={
        "Origin": VERCEL,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.headers.get("access-control-allow-origin") == VERCEL
    # Without credentials the cookie does not travel and identity breaks.
    assert r.headers.get("access-control-allow-credentials") == "true"


def test_an_unnamed_origin_is_refused(monkeypatch):
    """The list is exact. This is the whole security property."""
    mod = _app(monkeypatch, VERCEL)
    c = TestClient(mod.api)
    for origin in ("https://evil.example",
                   "https://remit.vercel.app.evil.example",
                   "http://remit.vercel.app",          # scheme matters
                   "https://REMIT.vercel.app"):        # and so does case
        r = c.options("/v1/intents", headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        })
        assert r.headers.get("access-control-allow-origin") != origin, origin


def test_there_is_never_a_wildcard(monkeypatch):
    """A wildcard with credentials is refused by browsers anyway, but the
    server should not be the one proposing it."""
    mod = _app(monkeypatch, VERCEL)
    c = TestClient(mod.api)
    r = c.options("/v1/intents", headers={
        "Origin": VERCEL,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert r.headers.get("access-control-allow-origin") != "*"


def test_the_cookie_becomes_cross_site_capable_and_secure(monkeypatch):
    """SameSite=None is only honoured on a Secure cookie.

    Getting one without the other is the failure that looks like it works: the
    browser drops the cookie and the client silently becomes a new person on
    every request.
    """
    mod = _app(monkeypatch, VERCEL)
    c = TestClient(mod.api)
    r = c.post("/v1/intents", json={"utterance": "buy a yoga mat under 2000"})
    setc = r.headers.get("set-cookie", "")
    assert "samesite=none" in setc.lower(), setc
    assert "secure" in setc.lower(), setc


def test_same_origin_keeps_lax(monkeypatch):
    """Nothing about the default deployment changes."""
    mod = _app(monkeypatch, None)
    c = TestClient(mod.api)
    r = c.post("/v1/intents", json={"utterance": "buy a yoga mat under 2000"})
    setc = r.headers.get("set-cookie", "")
    assert "samesite=lax" in setc.lower(), setc


def test_identity_still_cannot_be_chosen_across_origins(monkeypatch):
    """Opening a door is not the same as removing the lock behind it."""
    mod = _app(monkeypatch, VERCEL)
    c = TestClient(mod.api)
    r = c.post("/v1/intents",
               json={"utterance": "buy a yoga mat under 2000",
                     "user_id": "usr_someone_else"},
               headers={"Origin": VERCEL,
                        "Authorization": "Bearer usr_iwouldliketobethis.00000000"})
    got = r.json()["intent"]["actor_id"]
    assert got != "usr_someone_else"
    assert got != "usr_iwouldliketobethis"
    assert got.startswith("usr_")
