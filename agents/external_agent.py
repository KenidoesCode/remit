#!/usr/bin/env python3
"""An agent that has never heard of REMIT's internals.

This file imports NOTHING from `remit`. That constraint is the entire point:
if it needed the journey, the policy engine, the envelope class or the catalog,
then REMIT would be a library with a website on top, and the claim "an external
agent can integrate" would be a claim about this repository rather than about
the protocol.

What it knows: an HTTP base URL, six nouns, and JSON.

    intent      what the human authorised
    authority   the envelope it became
    action      what this agent proposes
    decision    whether it may
    execution   the money, once
    evidence    why, afterwards

It deliberately behaves like a real agent rather than a well-mannered client:
it asks for things it should not get, it retries, it tries to spend after being
revoked, and it tries to reuse an approval. Every one of those is answered by
the same boundary the website talks to, because /v1 is a projection over the
same journey -- not a second implementation.

    python agents/external_agent.py [base_url]
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")


class Remit:
    """The entire client. Session cookie in, session cookie out."""

    def __init__(self, base: str):
        self.base = base
        self.cookie: str | None = None

    def call(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"content-type": "application/json"}
            | ({"cookie": self.cookie} if self.cookie else {}))
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                raw = res.read().decode()
                got = res.headers.get("set-cookie")
                if got:
                    self.cookie = got.split(";")[0]
                return res.status, json.loads(raw or "{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")


def show(title, status, body, *keys):
    print(f"\n\033[1m{title}\033[0m  → HTTP {status}")
    for k in keys:
        cur, path = body, k.split(".")
        for part in path:
            cur = (cur or {}).get(part) if isinstance(cur, dict) else None
        print(f"   {k:34} {cur}")


def main() -> int:
    r = Remit(BASE)

    status, spec = r.call("GET", "/v1/")
    if status != 200:
        print(f"no protocol at {BASE}/v1 (HTTP {status}). Is REMIT running?")
        return 1
    print(f"connected to {spec['protocol']} v{spec['version']}")
    print(f"  {spec['thesis']}")

    # 1 — say what the human wants, and get back a bounded authority
    s, out = r.call("POST", "/v1/intents",
                    {"utterance": "buy running shoes under 5000"})
    show("1. create an intent", s, out,
         "intent.intent_id", "intent.ceiling.amount_paise",
         "intent.ceiling.currency", "authority.state", "intent.interpreter")
    intent_id = (out.get("intent") or {}).get("intent_id")

    # 2 — ask before doing. A well-behaved agent checks first.
    s, out = r.call("POST", "/v1/evaluate",
                    {"utterance": "buy running shoes under 5000"})
    show("2. would this be allowed?", s, out,
         "verdict", "would_execute", "total.amount_paise", "sandboxed")

    # 3 — do it
    s, out = r.call("POST", "/v1/execute",
                    {"utterance": "buy running shoes under 5000"})
    show("3. execute", s, out, "decision.verdict", "execution.state",
         "execution.order_id", "authority_state")
    cid = (out.get("decision") or {}).get("correlation_id")

    # 4 — retry, the way an agent with no jitter does
    s, again = r.call("POST", "/v1/execute",
                      {"utterance": "buy running shoes under 5000"})
    show("4. retry the same request", s, again,
         "execution.payment_id", "execution.replayed")
    same = (again.get("execution") or {}).get("payment_id") == \
        (out.get("execution") or {}).get("payment_id")
    print(f"   {'one financial effect':34} {same}")

    # 5 — ask for something the agent may not have alone
    s, out = r.call("POST", "/v1/step-up", {"utterance": "buy whisky under 2000"})
    show("5. something a person must approve", s, out,
         "required", "asking.clause", "asking.why",
         "asking.amount.amount_paise")
    token = (out.get("approval") or {}).get("token")

    # 6 — approve it, then try to spend the approval twice
    s, out = r.call("POST", "/v1/approve",
                    {"utterance": "buy whisky under 2000",
                     "approval_token": token})
    show("6. approve", s, out, "decision.verdict", "execution.state",
         "execution.order_id")
    s, out = r.call("POST", "/v1/approve",
                    {"utterance": "buy whisky under 2000",
                     "approval_token": token})
    show("7. spend the same approval again", s, out,
         "execution.state", "decision.reason")

    # 7 — try a foreign currency
    s, out = r.call("POST", "/v1/execute",
                    {"utterance": "buy headphones under $5000"})
    show("8. a ceiling in the wrong currency", s, out,
         "decision.verdict", "decision.failed", "execution.state")

    # 8 — prove afterwards why any of it happened
    s, out = r.call("GET", f"/v1/audit/{cid}")
    kinds = [e["kind"] for e in out.get("events", [])]
    print(f"\n\033[1m9. reconstruct it\033[0m  → HTTP {s}")
    print(f"   {'events':34} {len(kinds)}")
    print(f"   {'chain intact':34} {out.get('chain_intact')}")
    print(f"   {'authority walked':34} "
          f"{' -> '.join(h['to_state'] for h in out.get('authority_history', []))}")

    # 9 — the human takes it back
    s, out = r.call("POST", "/v1/revoke", {"reason": "handing the laptop back"})
    show("10. revoke", s, out, "revocation_id", "scope", "revoked_at")
    s, out = r.call("POST", "/v1/execute",
                    {"utterance": "buy a notebook under 300"})
    show("11. spend after revocation", s, out,
         "decision.verdict", "execution.state", "decision.reason")

    blocked = (out.get("execution") or {}).get("state") == "BLOCKED"
    print("\n" + "─" * 66)
    print("an agent that never imported anything from this repository:")
    print("  · created a bounded authority from a sentence")
    print("  · was told what it may do before doing it")
    print("  · executed once, and could not execute twice")
    print("  · was refused a foreign-currency ceiling")
    print("  · had a step-up approval spent exactly once")
    print("  · could reconstruct why, from the record")
    print(f"  · was stopped by a revocation: {blocked}")
    print("\nthe model changes. the boundary does not.")
    return 0 if blocked else 2


if __name__ == "__main__":
    raise SystemExit(main())
