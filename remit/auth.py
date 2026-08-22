"""Who is asking.

Until this file existed, `user_id` arrived in the request body and nothing
verified it. Exposure, velocity, the idempotency namespace and — worst —
approval ownership were all keyed on that string, so anyone who knew another
principal's id inherited their limits and could redeem against them. REMIT's
own attack lab demonstrated it, on the public page, in red:

    BROKE  [payment] Spend as somebody else
           an unauthenticated caller spent 497600 paise against
           usr_victim_alice's identity and limits.

Every other control in this system is downstream of identity, so that one gap
made every guarantee conditional on nobody trying.

WHAT THIS IS, STATED PRECISELY
------------------------------
This authenticates a SESSION, not a person. The server mints an opaque
principal id, signs it, and puts it in an httpOnly cookie; from then on the
server derives identity from its own signature and never from anything the
caller can type. That is enough to make the boundary real: a caller cannot
choose whose limits to spend, cannot redeem somebody else's approval, and
cannot read another principal's order.

It is NOT a login. There is no password, no email, no account recovery, and no
claim that the human behind the session is who they say they are. A real
deployment binds this principal to an authenticated user from the merchant's
own identity provider — that is one function, `principal_from_upstream()`, and
it is deliberately not written here because writing it without an IdP behind it
would be theatre. The gap is in FINAL_AUDIT.md rather than papered over.

WHY A SIGNED COOKIE AND NOT A JWT
---------------------------------
Nothing here needs claims, expiry negotiation, or third-party verification. The
only question is "did this server issue this id", and an HMAC answers it in one
line with no library and no algorithm-confusion class of bug.
"""
from __future__ import annotations

import hmac
import os
import secrets
from hashlib import sha256

COOKIE = "remit_session"
# Long enough that a session survives a demo, short enough that a shared laptop
# does not hand the next person a spending identity.
MAX_AGE_SECONDS = 12 * 60 * 60


def session_secret(live: bool) -> str:
    """The key that decides whether a session id is one we issued.

    Live demands a real secret and refuses to start without one. A default here
    would be a published key: anyone who read this repository could mint a
    principal id and spend against it, which is the exact bug this file exists
    to close. Offline it is per-process random, so a test signs with the same
    object it verifies against and nothing that leaves the process is forgeable.
    """
    s = os.environ.get("REMIT_SESSION_SECRET", "").strip()
    if s:
        return s
    if live:
        raise RuntimeError(
            "REMIT_SESSION_SECRET is not set. REMIT will not derive spending "
            "identity from a key it cannot verify. Set it, or unset REMIT_LIVE.")
    return "dev-" + secrets.token_hex(16)


def mint(secret: str) -> str:
    """A fresh principal and its signature, as one cookie value."""
    pid = "usr_" + secrets.token_urlsafe(15)
    return f"{pid}.{_sig(pid, secret)}"


def _sig(pid: str, secret: str) -> str:
    return hmac.new(secret.encode(), pid.encode(), sha256).hexdigest()[:32]


def verify(cookie_value: str | None, secret: str) -> str | None:
    """The principal id, or None. Never raises, never guesses.

    Constant-time comparison because a signature check that leaks timing is a
    signature check that can be walked one byte at a time.
    """
    if not cookie_value or "." not in cookie_value:
        return None
    pid, _, sig = cookie_value.rpartition(".")
    if not pid.startswith("usr_") or len(pid) > 64:
        return None
    return pid if hmac.compare_digest(sig, _sig(pid, secret)) else None
